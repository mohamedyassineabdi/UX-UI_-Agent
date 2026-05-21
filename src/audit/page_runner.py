from src.audit.element_detector import detect_clickables
from src.audit.interaction_classifier import classify_clickables, summarize_classification
from src.audit.page_visit_helpers import (
    collect_network_log,
    dismiss_cookie_banners,
    extract_basic_page_info,
    save_dom_snapshot,
    smart_scroll,
    scroll_to_url_fragment,
    wait_for_page_ready,
)
from src.audit.html_extractor import extract_html_blocks
from src.audit.checks.runtime_motion_detector import detect_runtime_motion
from src.audit.rendered_css_extractor import extract_rendered_ui
from src.audit.safe_interaction_tester import test_safe_clickables
from src.utils.file_utils import ensure_dir, join_path, write_json_file
from src.utils.url_utils import build_page_folder_name, build_website_folder_name


WEB_VITALS_INIT_SCRIPT = """
(() => {
  if (window.__uxAuditVitals) return;
  window.__uxAuditVitals = {
    lcp: null,
    cls: 0,
    clsEntries: [],
    inpCandidate: null,
    eventCandidates: []
  };

  const observe = (type, callback, options = {}) => {
    try {
      if (!("PerformanceObserver" in window)) return;
      const supported = PerformanceObserver.supportedEntryTypes || [];
      if (!supported.includes(type)) return;
      const observer = new PerformanceObserver((list) => callback(list.getEntries()));
      observer.observe({ type, buffered: true, ...options });
    } catch (_) {}
  };

  observe("largest-contentful-paint", (entries) => {
    const entry = entries[entries.length - 1];
    if (!entry) return;
    window.__uxAuditVitals.lcp = {
      valueMs: Math.round(entry.startTime || 0),
      size: entry.size || 0,
      element: entry.element ? (entry.element.tagName || "").toLowerCase() : "",
      url: entry.url || ""
    };
  });

  observe("layout-shift", (entries) => {
    for (const entry of entries) {
      if (entry.hadRecentInput) continue;
      window.__uxAuditVitals.cls += Number(entry.value || 0);
      window.__uxAuditVitals.clsEntries.push({
        value: Number((entry.value || 0).toFixed(4)),
        startTimeMs: Math.round(entry.startTime || 0)
      });
    }
    window.__uxAuditVitals.cls = Number(window.__uxAuditVitals.cls.toFixed(4));
  });

  observe("event", (entries) => {
    for (const entry of entries) {
      const duration = Number(entry.duration || 0);
      if (!duration) continue;
      const candidate = {
        name: entry.name || "",
        durationMs: Math.round(duration),
        startTimeMs: Math.round(entry.startTime || 0),
        interactionId: entry.interactionId || 0
      };
      window.__uxAuditVitals.eventCandidates.push(candidate);
      if (!window.__uxAuditVitals.inpCandidate || duration > window.__uxAuditVitals.inpCandidate.durationMs) {
        window.__uxAuditVitals.inpCandidate = candidate;
      }
    }
  }, { durationThreshold: 16 });
})();
"""


async def install_performance_observers(page):
    try:
        await page.add_init_script(WEB_VITALS_INIT_SCRIPT)
    except Exception:
        pass


async def collect_performance_snapshot(page):
    """Collect browser performance signals without requiring Lighthouse."""
    try:
        return await page.evaluate(
            """
            () => {
              const nav = performance.getEntriesByType("navigation")[0] || {};
              const vitals = window.__uxAuditVitals || {};
              const paints = Object.fromEntries(
                performance.getEntriesByType("paint").map((entry) => [entry.name, Math.round(entry.startTime)])
              );
              const resources = performance.getEntriesByType("resource") || [];
              const transferSize = resources.reduce((sum, entry) => sum + (entry.transferSize || 0), 0);
              const encodedBodySize = resources.reduce((sum, entry) => sum + (entry.encodedBodySize || 0), 0);
              const blockingResources = resources
                .filter((entry) => {
                  const type = entry.initiatorType || "";
                  const duration = Number(entry.duration || 0);
                  return ["script", "css", "link"].includes(type) && duration >= 250;
                })
                .slice(0, 8)
                .map((entry) => ({
                  name: entry.name,
                  initiatorType: entry.initiatorType || "",
                  durationMs: Math.round(entry.duration || 0),
                  transferSize: entry.transferSize || 0
                }));
              return {
                webVitals: {
                  lcpMs: vitals.lcp ? vitals.lcp.valueMs : null,
                  lcpElement: vitals.lcp ? vitals.lcp.element : "",
                  lcpUrl: vitals.lcp ? vitals.lcp.url : "",
                  cls: typeof vitals.cls === "number" ? Number(vitals.cls.toFixed(4)) : null,
                  clsEntries: Array.isArray(vitals.clsEntries) ? vitals.clsEntries.slice(0, 8) : [],
                  inpCandidateMs: vitals.inpCandidate ? vitals.inpCandidate.durationMs : null,
                  inpEventName: vitals.inpCandidate ? vitals.inpCandidate.name : "",
                  eventTimingCandidates: Array.isArray(vitals.eventCandidates) ? vitals.eventCandidates.slice(0, 8) : []
                },
                navigation: {
                  ttfbMs: Math.round((nav.responseStart || 0) - (nav.requestStart || 0)),
                  domContentLoadedMs: Math.round((nav.domContentLoadedEventEnd || 0) - (nav.startTime || 0)),
                  loadEventMs: Math.round((nav.loadEventEnd || 0) - (nav.startTime || 0)),
                  transferSize: nav.transferSize || 0,
                  encodedBodySize: nav.encodedBodySize || 0
                },
                paint: {
                  firstPaintMs: paints["first-paint"] || null,
                  firstContentfulPaintMs: paints["first-contentful-paint"] || null
                },
                resources: {
                  count: resources.length,
                  totalTransferSize: transferSize,
                  totalEncodedBodySize: encodedBodySize,
                  blockingCandidates: blockingResources
                },
                limitations: [
                  "This is a Playwright lab snapshot, not CrUX field data.",
                  "LCP and CLS are collected from browser PerformanceObserver when supported.",
                  "INP requires user input; the lab value is only available when event timing entries are produced during the audit."
                ]
              };
            }
            """
        )
    except Exception as error:
        return {"error": str(error)}


async def collect_keyboard_accessibility_snapshot(page, max_tabs: int = 80):
    """Probe basic keyboard reachability and visible focus behavior."""
    try:
        interactive = await page.evaluate(
            """
            () => {
              const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
              };
              const nameOf = (el) => clean(
                el.getAttribute('aria-label') ||
                el.innerText ||
                el.textContent ||
                el.getAttribute('title') ||
                el.getAttribute('name') ||
                el.getAttribute('id') ||
                el.getAttribute('href') ||
                el.tagName
              );
              return Array.from(document.querySelectorAll(
                'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [role="checkbox"], [role="radio"], [role="switch"], [tabindex]:not([tabindex="-1"])'
              ))
                .filter((el) => isVisible(el) && !el.disabled && el.getAttribute('aria-hidden') !== 'true')
                .slice(0, 160)
                .map((el, index) => ({
                  index,
                  tag: el.tagName.toLowerCase(),
                  role: clean(el.getAttribute('role')),
                  text: nameOf(el).slice(0, 120),
                  href: clean(el.getAttribute('href')).slice(0, 180),
                  tabIndex: el.tabIndex,
                  fingerprint: [el.tagName.toLowerCase(), clean(el.getAttribute('role')), nameOf(el), clean(el.getAttribute('href')), clean(el.id), index].join('|')
                }));
            }
            """
        )
        await page.keyboard.press("Home")
        focused = []
        seen = set()
        weak_focus = []
        for _ in range(max(1, min(max_tabs, 160))):
            await page.keyboard.press("Tab")
            item = await page.evaluate(
                """
                () => {
                  const el = document.activeElement;
                  if (!el || el === document.body || el === document.documentElement) return null;
                  const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const rect = el.getBoundingClientRect();
                  const style = window.getComputedStyle(el);
                  const outlineWidth = parseFloat(style.outlineWidth || '0') || 0;
                  const boxShadow = clean(style.boxShadow);
                  const hasVisibleFocus = outlineWidth >= 2 || (boxShadow && boxShadow !== 'none');
                  const name = clean(
                    el.getAttribute('aria-label') ||
                    el.innerText ||
                    el.textContent ||
                    el.getAttribute('title') ||
                    el.getAttribute('name') ||
                    el.getAttribute('id') ||
                    el.getAttribute('href') ||
                    el.tagName
                  );
                  return {
                    tag: el.tagName.toLowerCase(),
                    role: clean(el.getAttribute('role')),
                    text: name.slice(0, 120),
                    href: clean(el.getAttribute('href')).slice(0, 180),
                    tabIndex: el.tabIndex,
                    hasVisibleFocus,
                    outlineWidth,
                    boxShadow: boxShadow.slice(0, 120),
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
                    fingerprint: [el.tagName.toLowerCase(), clean(el.getAttribute('role')), name, clean(el.getAttribute('href')), clean(el.id)].join('|')
                  };
                }
                """
            )
            if not item:
                continue
            key = item.get("fingerprint")
            if key in seen:
                break
            seen.add(key)
            focused.append(item)
            if not item.get("hasVisibleFocus"):
                weak_focus.append(item)
        return {
            "tested": True,
            "interactiveCount": len(interactive or []),
            "focusedCount": len(focused),
            "coverage": round((len(focused) / len(interactive)) * 100.0, 1) if interactive else 100.0,
            "unfocusedSamples": [item for item in (interactive or []) if item.get("fingerprint") not in seen][:8],
            "weakFocusSamples": weak_focus[:8],
            "focusedSamples": focused[:12],
            "limitations": [
                "This is a tab-order probe in Chromium, not a full screen-reader audit.",
                "Keyboard activation of every focused control is not attempted during this probe.",
            ],
        }
    except Exception as error:
        return {"tested": False, "error": str(error)}


def config_for_page(config, page_info):
    if page_info.get("sourceType") != "section":
        return config

    scoped = dict(config)
    interaction = dict(config.get("interactionTesting", {}))
    current_limit = interaction.get("maxSafeInteractionsPerPage")
    if not isinstance(current_limit, int) or current_limit > 5:
        interaction["maxSafeInteractionsPerPage"] = 5
    scoped["interactionTesting"] = interaction
    return scoped


async def run_page_audit(*, context, page_info, page_index, config):
    config = config_for_page(config, page_info)
    page = await context.new_page()
    cleanup_config = config.get("outputCleanup", {})
    keep_debug_artifacts = cleanup_config.get("keepDebugArtifacts", False)

    site_url = page_info.get("siteUrl") or page_info["url"]
    website_folder_name = build_website_folder_name(site_url)
    folder_segments = page_info.get("folderSegments") or [
        build_page_folder_name(page_info["name"], f"page_{page_index + 1}")
    ]
    page_folder_path = join_path(
        config["paths"]["screenshotDir"],
        website_folder_name,
        *folder_segments,
    )

    result = {
        "index": page_index + 1,
        "name": page_info["name"],
        "originalUrl": page_info["url"],
        "siteUrl": site_url,
        "normalizedUrl": page_info.get("normalizedUrl"),
        "navigationPath": page_info.get("navigationPath") or [page_info["name"]],
        "folderSegments": folder_segments,
        "finalUrl": None,
        "status": "pending",
        "screenshotPath": None,
        "screenshotFolder": page_folder_path,
        "clickableSummary": {
            "totalDetected": 0,
            "safe": 0,
            "forbidden": 0,
            "unknown": 0,
        },
        "interactionSummary": {
            "safeCandidates": 0,
            "tested": 0,
            "skippedSafe": 0,
            "successful": 0,
            "failed": 0,
            "navigations": 0,
            "domChanges": 0,
            "popups": 0,
            "dialogs": 0,
            "noEffects": 0,
            "errors": 0,
            "notFound": 0,
            "interactionScreenshotsCreated": 0,
        },
        "clickables": [],
        "safeInteractionResults": [],
        "cookieActions": [],
        "scrollScreenshotPaths": [],
        "pageMetadata": None,
        "html": None,
        "renderedUi": None,
        "performance": None,
        "keyboardAccessibility": None,
        "runtimeMotion": None,
        "networkLogPath": None,
        "pageMetadataPath": None,
        "domSnapshotPath": None,
        "error": None,
    }

    try:
        ensure_dir(page_folder_path)
        page_screenshots_dir = join_path(page_folder_path, "page")
        artifacts_dir = join_path(page_folder_path, "artifacts")
        ensure_dir(page_screenshots_dir)
        if keep_debug_artifacts:
            ensure_dir(artifacts_dir)
        scroll_screenshots_dir = join_path(page_screenshots_dir, "scrolls")
        ensure_dir(scroll_screenshots_dir)

        network_log = []
        collect_network_log(page, network_log)
        await install_performance_observers(page)

        await page.goto(
            page_info["url"],
            wait_until=config["navigation"]["waitUntil"],
            timeout=config["navigation"]["timeoutMs"],
        )

        if config["navigation"]["postLoadDelayMs"] > 0:
            await page.wait_for_timeout(config["navigation"]["postLoadDelayMs"])

        result["finalUrl"] = page.url

        if config.get("pageCapture", {}).get("dismissCookieBanners"):
            result["cookieActions"] = await dismiss_cookie_banners(page)

        await wait_for_page_ready(page, config)
        await scroll_to_url_fragment(page, page_info["url"])

        if config.get("pageCapture", {}).get("captureScrollScreenshots"):
            result["scrollScreenshotPaths"] = await smart_scroll(
                page=page,
                screenshots_dir=scroll_screenshots_dir,
                page_label=page_info["name"],
                screenshot_type=config["screenshot"]["type"],
                max_rounds=config.get("pageCapture", {}).get("scrollMaxRounds", 4),
            )

        await wait_for_page_ready(page, config)
        await scroll_to_url_fragment(page, page_info["url"])

        result["pageMetadata"] = await extract_basic_page_info(page, page_info["url"])
        result["finalUrl"] = result["pageMetadata"]["finalUrl"] or result["finalUrl"]
        result["performance"] = await collect_performance_snapshot(page)
        result["keyboardAccessibility"] = await collect_keyboard_accessibility_snapshot(
            page,
            max_tabs=int(config.get("accessibilityTesting", {}).get("maxKeyboardTabs", 80)),
        )

        if keep_debug_artifacts and config.get("pageCapture", {}).get("saveDomSnapshot"):
            dom_snapshot_path = join_path(artifacts_dir, "dom_snapshot.html")
            await save_dom_snapshot(page, dom_snapshot_path)
            result["domSnapshotPath"] = dom_snapshot_path

        screenshot_path = join_path(page_screenshots_dir, f"main.{config['screenshot']['type']}")
        await page.screenshot(
            path=screenshot_path,
            full_page=config["screenshot"]["fullPage"],
            type=config["screenshot"]["type"],
        )
        result["screenshotPath"] = screenshot_path

        if keep_debug_artifacts:
            page_metadata_path = join_path(artifacts_dir, "page_metadata.json")
            result["pageMetadataPath"] = page_metadata_path
            write_json_file(
                page_metadata_path,
                {
                    **(result["pageMetadata"] or {}),
                    "cookieActions": result["cookieActions"],
                    "scrollScreenshotPaths": result["scrollScreenshotPaths"],
                    "pageScreenshotPath": result["screenshotPath"],
                    "performance": result["performance"],
                    "keyboardAccessibility": result["keyboardAccessibility"],
                },
            )

        result["html"] = await extract_html_blocks(
            page=page,
            page_info=page_info,
            basic_page_info=result["pageMetadata"],
            screenshot_path=result["screenshotPath"],
            scroll_screenshot_paths=result["scrollScreenshotPaths"],
        )
        page_meta = result["html"].get("pageMeta") if isinstance(result["html"], dict) else None
        page_meta_data = page_meta.get("data") if isinstance(page_meta, dict) else None
        if isinstance(page_meta_data, dict):
            page_meta_data["performance"] = result["performance"]

        if config.get("renderedUi", {}).get("enabled"):
            result["renderedUi"] = await extract_rendered_ui(page, config)
        if (config.get("presentationChecks") or {}).get("runtimeMotion", {}).get("enabled", False):
            result["runtimeMotion"] = await detect_runtime_motion(page, config)

        if keep_debug_artifacts and config.get("pageCapture", {}).get("saveNetworkLog"):
            network_log_path = join_path(artifacts_dir, "network_log.json")
            write_json_file(network_log_path, network_log)
            result["networkLogPath"] = network_log_path

        detected_clickables = await detect_clickables(page, config)
        classified_clickables = classify_clickables(detected_clickables, config)
        classification_summary = summarize_classification(classified_clickables)

        result["clickables"] = classified_clickables
        result["clickableSummary"] = {
            "totalDetected": len(classified_clickables),
            "safe": classification_summary["safe"],
            "forbidden": classification_summary["forbidden"],
            "unknown": classification_summary["unknown"],
        }

        interaction_test_output = await test_safe_clickables(
            context=context,
            page_info=page_info,
            classified_clickables=classified_clickables,
            config=config,
        )

        safe_interaction_results = interaction_test_output["safeInteractionResults"]
        result["safeInteractionResults"] = safe_interaction_results
        result["interactionSummary"] = {
            "safeCandidates": classification_summary["safe"],
            "tested": interaction_test_output["testedCount"],
            "skippedSafe": interaction_test_output["skippedSafeCount"],
            "successful": len([item for item in safe_interaction_results if item["success"]]),
            "failed": len([item for item in safe_interaction_results if not item["success"]]),
            "navigations": len([item for item in safe_interaction_results if item["outcomeType"] == "navigation"]),
            "domChanges": len([item for item in safe_interaction_results if item["outcomeType"] == "dom_change"]),
            "popups": len([item for item in safe_interaction_results if item["outcomeType"] == "popup"]),
            "dialogs": len([item for item in safe_interaction_results if item["outcomeType"] == "dialog"]),
            "noEffects": len([item for item in safe_interaction_results if item["outcomeType"] == "no_effect"]),
            "errors": len([item for item in safe_interaction_results if item["outcomeType"] == "error"]),
            "notFound": len([item for item in safe_interaction_results if item["outcomeType"] == "not_found"]),
            "interactionScreenshotsCreated": interaction_test_output["interactionScreenshotsCreated"],
        }

        result["status"] = "success"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = str(error)
        if keep_debug_artifacts:
            ensure_dir(artifacts_dir)
            write_json_file(
                join_path(artifacts_dir, "page_error.json"),
                {
                    "name": page_info["name"],
                    "url": page_info["url"],
                    "error": str(error),
                },
            )
    finally:
        await page.close()

    return result
