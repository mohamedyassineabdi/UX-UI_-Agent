from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .common import (
    AXIS_DEFINITIONS,
    AXIS_IMPACT,
    AXIS_KEYWORDS,
    AXIS_USER_IMPACT,
    clamp,
    clean_text,
    count_keyword_hits,
    dedupe_strings,
    mean,
    normalize_status,
    safe_float,
    safe_int,
    score_to_severity,
)
from .vision_client import run_gtm_vision_review


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
RESULTS_DIR = ROOT_DIR / "shared" / "output" / "results"
DEFAULT_WEBSITE_MENU = GENERATED_DIR / "website_menu.json"
DEFAULT_CLEANED = GENERATED_DIR / "html_cleaned.json"
DEFAULT_RENDERED = GENERATED_DIR / "rendered_ui_extraction.json"
DEFAULT_CHECKS = GENERATED_DIR / "sheet_checks.json"
DEFAULT_OUTPUT = GENERATED_DIR / "gtm_audit.json"

ACTION_WORDS = {"contact", "demander", "demo", "discover", "en savoir plus", "learn", "planifier", "request", "start", "talk", "try"}
AUDIENCE_WORDS = {"b2b", "brand", "brands", "e-commerce", "enterprise", "equipes", "fabricants", "grossistes", "merchant", "merchants", "operations", "professionnel", "retailer", "retailers", "supply chain", "teams", "wholesale"}
TRUST_WORDS = {"bpi", "case study", "certified", "client", "clients", "french tech", "gdpr", "partner", "partenaire", "partners", "privacy", "review", "reviews", "secure", "security", "soc 2", "testimonial", "testimonials", "trusted by", "vision"}
VISION_TRUST_PAGE_WORDS = {
    "about",
    "apropos",
    "a-propos",
    "equipe",
    "équipe",
    "founder",
    "founders",
    "leader",
    "leadership",
    "notre equipe",
    "qui sommes nous",
    "team",
    "trust",
    "vision",
}
PROOF_PATTERNS = [r"\b\d+\s*%\b", r"\b\d+\s*(minutes|min|jours|days|hours|heures)\b", r"\b-\d+\s*%\b"]
LOCALE_NOISE = {"deutsch", "english", "espanol", "español", "francais", "français", "français▼", "italiano", "nederlands", "portugues", "português"}
def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def to_path(raw: str, default: Path) -> Path:
    if not clean_text(raw):
        return default
    path = Path(raw)
    return path if path.is_absolute() else ROOT_DIR / path


def load_latest_results() -> Optional[Dict[str, Any]]:
    candidates = sorted(RESULTS_DIR.glob("audit-results_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return load_json(candidates[0])


def collect_numbers(node: Any, key: str) -> List[float]:
    values: List[float] = []
    if isinstance(node, dict):
        for nested_key, nested in node.items():
            if nested_key == key and isinstance(nested, (int, float)):
                values.append(float(nested))
            values.extend(collect_numbers(nested, key))
    elif isinstance(node, list):
        for nested in node:
            values.extend(collect_numbers(nested, key))
    return values


def nav_count(items: Iterable[Dict[str, Any]]) -> int:
    total = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        total += 1
        total += nav_count(item.get("children") or [])
    return total


def nav_labels(items: Iterable[Dict[str, Any]]) -> List[str]:
    labels: List[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        labels.extend(
            text
            for text in (
                clean_text(item.get("label")),
                clean_text(item.get("title")),
                clean_text(item.get("text")),
            )
            if text
        )
        labels.extend(nav_labels(item.get("children") or []))
    return dedupe_strings(labels, limit=24)


def page_meta(page: Dict[str, Any]) -> Dict[str, Any]:
    return ((page.get("pageMeta") or {}).get("data") or {})


def homepage_page(cleaned_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pages = cleaned_data.get("pages") or []
    for page in pages:
        if clean_text(page_meta(page).get("sourceType")).lower() == "homepage":
            return page
    return pages[0] if pages else None


def page_texts(page: Dict[str, Any], bucket: str) -> List[str]:
    data = ((page.get("textContent") or {}).get("data") or {})
    values = data.get(bucket) or []
    if bucket == "ctaTexts":
        return dedupe_strings(item.get("text") for item in values if isinstance(item, dict) and clean_text(item.get("text")))
    return dedupe_strings(item.get("text") for item in values if isinstance(item, dict))


def headings(page: Dict[str, Any]) -> List[str]:
    items = (((page.get("titlesAndHeadings") or {}).get("data") or {}).get("headings") or [])
    return dedupe_strings(item.get("text") for item in items if isinstance(item, dict))


def flatten_checks(checks_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for sheet_name, payload in (checks_data.get("sheets") or {}).items():
        for item in payload.get("results") or []:
            evidence = item.get("evidence")
            rows.append(
                {
                    "sheet": clean_text(item.get("sheet") or sheet_name),
                    "row": safe_int(item.get("row")),
                    "criterion": clean_text(item.get("criterion")),
                    "status": normalize_status(item.get("status")),
                    "confidence": clamp(safe_float(item.get("confidence"), 0.5), 0.0, 1.0),
                    "decision_basis": clean_text(item.get("decision_basis")).lower(),
                    "rationale": clean_text(item.get("rationale")),
                    "evidence": evidence if isinstance(evidence, list) else [clean_text(evidence)] if clean_text(evidence) else [],
                    "page_name": clean_text(item.get("page_name")),
                    "page_url": clean_text(item.get("page_url") or item.get("final_url")),
                    "screenshot_path": clean_text(item.get("screenshot_path")),
                    "evidence_bundle": item.get("evidence_bundle") if isinstance(item.get("evidence_bundle"), dict) else None,
                }
            )
    return rows


def sheet_score(summary: Dict[str, Any]) -> float:
    passed = safe_int(summary.get("TRUE"))
    failed = safe_int(summary.get("FALSE"))
    total = passed + failed
    return round((passed / total) * 100.0, 1) if total else 45.0


def _performance_score(performance: Dict[str, Any]) -> float:
    web_vitals = performance.get("webVitals") if isinstance(performance.get("webVitals"), dict) else {}
    navigation = performance.get("navigation") if isinstance(performance.get("navigation"), dict) else {}
    paint = performance.get("paint") if isinstance(performance.get("paint"), dict) else {}
    resources = performance.get("resources") if isinstance(performance.get("resources"), dict) else {}
    lcp = safe_float(web_vitals.get("lcpMs"), 0.0)
    inp = safe_float(web_vitals.get("inpCandidateMs"), 0.0)
    cls = safe_float(web_vitals.get("cls"), -1.0)
    fcp = safe_float(paint.get("firstContentfulPaintMs"), 0.0)
    load = safe_float(navigation.get("loadEventMs"), 0.0)
    ttfb = safe_float(navigation.get("ttfbMs"), 0.0)
    transfer = safe_float(resources.get("totalTransferSize"), 0.0)
    blocking_count = len(resources.get("blockingCandidates") or []) if isinstance(resources.get("blockingCandidates"), list) else 0

    scores = []
    if lcp > 0:
        scores.append(clamp(100.0 - max(0.0, lcp - 2500.0) / 25.0, 0.0, 100.0))
    if inp > 0:
        scores.append(clamp(100.0 - max(0.0, inp - 200.0) / 4.0, 0.0, 100.0))
    if cls >= 0:
        scores.append(clamp(100.0 - max(0.0, cls - 0.1) * 500.0, 0.0, 100.0))
    if fcp > 0:
        scores.append(clamp(100.0 - max(0.0, fcp - 1800.0) / 22.0, 0.0, 100.0))
    if load > 0:
        scores.append(clamp(100.0 - max(0.0, load - 3000.0) / 45.0, 0.0, 100.0))
    if ttfb > 0:
        scores.append(clamp(100.0 - max(0.0, ttfb - 800.0) / 18.0, 0.0, 100.0))
    if transfer > 0:
        scores.append(clamp(100.0 - max(0.0, transfer - 2_000_000.0) / 45_000.0, 0.0, 100.0))
    scores.append(clamp(100.0 - blocking_count * 12.0, 0.0, 100.0))
    return round(mean(scores, default=55.0), 1)


def page_performance_profiles(cleaned_pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for page in cleaned_pages:
        meta = page_meta(page)
        performance = meta.get("performance")
        if not isinstance(performance, dict) or performance.get("error"):
            continue
        web_vitals = performance.get("webVitals") if isinstance(performance.get("webVitals"), dict) else {}
        navigation = performance.get("navigation") if isinstance(performance.get("navigation"), dict) else {}
        paint = performance.get("paint") if isinstance(performance.get("paint"), dict) else {}
        resources = performance.get("resources") if isinstance(performance.get("resources"), dict) else {}
        blocking = resources.get("blockingCandidates") if isinstance(resources.get("blockingCandidates"), list) else []
        largest_blocking = max(blocking, key=lambda item: safe_float(item.get("transferSize"), 0.0), default={})
        profiles.append(
            {
                "pageName": clean_text(page.get("name")) or clean_text(meta.get("name")) or "Page",
                "pageUrl": clean_text(page.get("finalUrl") or page.get("url") or meta.get("finalUrl")),
                "screenshotPath": clean_text((meta.get("screenshotPaths") or {}).get("page")),
                "score": _performance_score(performance),
                "largestContentfulPaintMs": safe_float(web_vitals.get("lcpMs"), 0.0),
                "largestContentfulPaintUrl": clean_text(web_vitals.get("lcpUrl")),
                "interactionToNextPaintCandidateMs": safe_float(web_vitals.get("inpCandidateMs"), 0.0),
                "cumulativeLayoutShift": safe_float(web_vitals.get("cls"), 0.0),
                "lcpElement": clean_text(web_vitals.get("lcpElement")),
                "firstContentfulPaintMs": safe_float(paint.get("firstContentfulPaintMs"), 0.0),
                "loadEventMs": safe_float(navigation.get("loadEventMs"), 0.0),
                "ttfbMs": safe_float(navigation.get("ttfbMs"), 0.0),
                "totalTransferSize": safe_float(resources.get("totalTransferSize"), 0.0),
                "totalEncodedBodySize": safe_float(resources.get("totalEncodedBodySize"), 0.0),
                "resourceCount": safe_int(resources.get("count")),
                "blockingResourceCount": len(blocking),
                "blockingResources": blocking[:3],
                "largestBlockingResource": largest_blocking,
                "limitations": performance.get("limitations") if isinstance(performance.get("limitations"), list) else [],
            }
        )
    return sorted(profiles, key=lambda item: item["score"])


def count_matches(texts: Iterable[str], words: Iterable[str]) -> int:
    haystack = " ".join(clean_text(text) for text in texts).lower()
    return sum(1 for word in words if word.lower() in haystack)


def count_proof_points(texts: Iterable[str]) -> int:
    haystack = " ".join(clean_text(text) for text in texts)
    return sum(len(re.findall(pattern, haystack, flags=re.IGNORECASE)) for pattern in PROOF_PATTERNS)


WCAG_SUCCESS_CRITERIA = {
    "1.4.3": "Contrast (Minimum)",
    "1.4.11": "Non-text Contrast",
    "2.1.1": "Keyboard",
    "2.1.2": "No Keyboard Trap",
    "2.4.3": "Focus Order",
    "2.4.4": "Link Purpose (In Context)",
    "2.4.7": "Focus Visible",
    "2.5.8": "Target Size (Minimum)",
    "3.3.2": "Labels or Instructions",
    "4.1.2": "Name, Role, Value",
}


def screenshot_lookup_from_cleaned(cleaned_pages: List[Dict[str, Any]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for page in cleaned_pages:
        meta = page_meta(page)
        shot = clean_text((meta.get("screenshotPaths") or {}).get("page"))
        if not shot:
            continue
        for key in (page.get("name"), page.get("url"), page.get("finalUrl"), meta.get("url"), meta.get("finalUrl")):
            cleaned = clean_text(key).lower()
            if cleaned:
                lookup[cleaned] = shot
    return lookup


def _page_screenshot(page: Dict[str, Any], screenshot_lookup: Dict[str, str]) -> str:
    direct = clean_text(page.get("screenshotPath"))
    if direct:
        return direct
    for key in (page.get("name"), page.get("url"), page.get("finalUrl")):
        shot = screenshot_lookup.get(clean_text(key).lower())
        if shot:
            return shot
    return ""


def _wcag_title(sc: str, title: str) -> str:
    return f"WCAG 2.2 SC {sc} {WCAG_SUCCESS_CRITERIA.get(sc, '')}: {title}".strip()


def _wcag_finding(
    *,
    sc: str,
    title: str,
    page: Dict[str, Any],
    screenshot_path: str,
    severity: str,
    evidence: List[str],
    explanation: str,
    why_it_matters: str,
    recommendation: str,
    confidence: float = 0.82,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    page_name = clean_text(page.get("name")) or "Audited page"
    page_url = clean_text(page.get("finalUrl") or page.get("url"))
    criterion = _wcag_title(sc, title)
    return {
        "title": criterion,
        "pageName": page_name,
        "pageUrl": page_url,
        "sourceSheet": "WCAG 2.2 Runtime",
        "severity": severity,
        "confidence": confidence,
        "evidence": "; ".join(evidence)[:240],
        "visibleSignals": dedupe_strings(evidence, limit=4),
        "explanation": explanation,
        "whyItMatters": why_it_matters,
        "recommendation": recommendation,
        "screenshotPath": screenshot_path,
        "visualRegion": None,
        "evidenceBundle": {
            "source": "wcag_2_2_runtime",
            "criterion": criterion,
            "successCriterion": sc,
            "raw": raw or {},
        },
        "wcagCriterion": sc,
    }


def _risk_samples(signals: List[str], prefix: str, limit: int = 6) -> List[str]:
    return [clean_text(item) for item in signals if clean_text(item).lower().startswith(prefix.lower())][:limit]


def _interaction_label(item: Dict[str, Any]) -> str:
    return clean_text(item.get("clickableText") or item.get("clickableAriaLabel") or item.get("clickableTag") or "control")


def _is_meaningful_failed_interaction(item: Dict[str, Any], page: Dict[str, Any]) -> bool:
    if not item.get("tested"):
        return False
    outcome = clean_text(item.get("outcomeType"))
    if outcome not in {"error", "no_effect", "not_found"}:
        return False
    label = _interaction_label(item)
    label_lower = label.lower()
    page_name = clean_text(page.get("name")).lower()
    page_url = clean_text(page.get("finalUrl") or page.get("url")).lower()
    if not label_lower or label_lower in {"a", "button", "link"}:
        return False
    if "@" in label_lower or re.search(r"\+?\d[\d\s().-]{6,}", label_lower):
        return False
    if outcome == "no_effect":
        # Current-page nav links and utility/contact protocols often have no visible DOM change by design.
        if label_lower and (label_lower == page_name or label_lower in page_url):
            return False
        if label_lower in {"accueil", "home"} and ("index." in page_url or page_url.endswith("/")):
            return False
        if label_lower in {"accueil", "home", "contact"} and label_lower in page_name:
            return False
    return True


def _interaction_results_by_page(results_data: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    if not isinstance(results_data, dict):
        return grouped
    for page in results_data.get("pages") or []:
        if not isinstance(page, dict):
            continue
        keys = [clean_text(page.get("name")).lower(), clean_text(page.get("finalUrl")).lower(), clean_text(page.get("originalUrl")).lower()]
        for key in keys:
            if key:
                grouped[key] = page.get("safeInteractionResults") or []
    return grouped


def _keyboard_snapshot_for_page(page: Dict[str, Any], results_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = page.get("keyboardAccessibility")
    if isinstance(snapshot, dict):
        return snapshot
    rendered_meta = ((page.get("renderedUi") or {}).get("pageMeta") or {})
    if isinstance(rendered_meta.get("keyboardAccessibility"), dict):
        return rendered_meta["keyboardAccessibility"]
    if not isinstance(results_data, dict):
        return {}
    page_keys = {clean_text(page.get("name")).lower(), clean_text(page.get("finalUrl")).lower(), clean_text(page.get("url")).lower()}
    for result_page in results_data.get("pages") or []:
        if not isinstance(result_page, dict):
            continue
        result_keys = {
            clean_text(result_page.get("name")).lower(),
            clean_text(result_page.get("finalUrl")).lower(),
            clean_text(result_page.get("originalUrl")).lower(),
        }
        if page_keys & result_keys and isinstance(result_page.get("keyboardAccessibility"), dict):
            return result_page["keyboardAccessibility"]
    return {}


def _component_label(component: Dict[str, Any]) -> str:
    return clean_text(
        component.get("accessibleName")
        or component.get("ariaLabel")
        or component.get("label")
        or component.get("text")
        or component.get("href")
        or component.get("id")
        or component.get("xpathHint")
        or component.get("tag")
    )


def wcag_findings_from_runtime(
    rendered_data: Dict[str, Any],
    cleaned_pages: List[Dict[str, Any]],
    results_data: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    screenshot_lookup = screenshot_lookup_from_cleaned(cleaned_pages)
    interactions_by_page = _interaction_results_by_page(results_data)
    rendered_pages = rendered_data.get("pages") or []
    for page in rendered_pages:
        if not isinstance(page, dict):
            continue
        ui = page.get("renderedUi") or {}
        page_ref = {"name": page.get("name"), "url": page.get("url"), "finalUrl": page.get("finalUrl")}
        screenshot_path = _page_screenshot(page, screenshot_lookup)
        signals = ((ui.get("auditSignals") or {}).get("accessibilityRisks") or [])
        signals = [clean_text(item) for item in signals if clean_text(item)]
        page_name = clean_text(page_ref.get("name")) or "this page"

        low_contrast = _risk_samples(signals, "Possible low text contrast") + _risk_samples(signals, "Possible low large-text contrast")
        if low_contrast:
            samples = [re.sub(r"^Possible low (large-text |text )?contrast detected for\s*", "", item, flags=re.IGNORECASE).strip(" '\".") for item in low_contrast[:5]]
            severity = "high" if len(low_contrast) >= 12 or any("input#" in sample or "textarea#" in sample for sample in samples) else "medium"
            findings.append(
                _wcag_finding(
                    sc="1.4.3",
                    title="Text contrast is below the required readability threshold",
                    page=page_ref,
                    screenshot_path=screenshot_path,
                    severity=severity,
                    evidence=[f"{len(low_contrast)} low-contrast text sample(s)", *samples[:3]],
                    explanation=(
                        f"On {page_name}, the rendered UI extractor found text whose measured foreground/background contrast is below the WCAG text contrast target. "
                        f"Examples include {', '.join(samples[:3])}."
                    ),
                    why_it_matters=(
                        f"These specific low-contrast labels and content blocks make {page_name} harder to read for low-vision users and in bright or low-quality display conditions. "
                        "If the affected text is a form field, CTA, phone number, email, or article link, users can miss the action entirely."
                    ),
                    recommendation="Raise the foreground/background contrast for the cited text to at least 4.5:1 for normal text, or 3:1 for large text, then retest the affected components.",
                    raw={"samples": low_contrast[:12]},
                )
            )

        small_targets = _risk_samples(signals, "Small click target")
        if small_targets:
            samples = [re.sub(r"^Small click target detected for interactive element\s*", "", item, flags=re.IGNORECASE).strip(" '\".") for item in small_targets[:5]]
            findings.append(
                _wcag_finding(
                    sc="2.5.8",
                    title="Interactive targets are smaller than WCAG 2.2 target-size expectations",
                    page=page_ref,
                    screenshot_path=screenshot_path,
                    severity="medium",
                    evidence=[f"{len(small_targets)} small target(s)", *samples[:3]],
                    explanation=(
                        f"On {page_name}, several visible controls appear below the WCAG 2.2 target-size expectation. "
                        f"Examples include {', '.join(samples[:3])}."
                    ),
                    why_it_matters=(
                        f"Small targets on {page_name} increase mis-taps and make navigation harder for users with motor impairments, tremor, touch devices, or zoomed layouts. "
                        "This is especially risky when the target is a navigation item, contact link, or primary CTA."
                    ),
                    recommendation="Increase the clickable area or spacing around the cited controls so each target has at least a 24 by 24 CSS-pixel target area unless a WCAG exception clearly applies.",
                    raw={"samples": small_targets[:12]},
                )
            )

        components = ui.get("components") or {}
        unlabeled_controls: List[str] = []
        for bucket in ("buttons", "links", "navLinks", "inputs", "selects", "textareas"):
            for component in components.get(bucket) or []:
                if not isinstance(component, dict) or component.get("visible") is False:
                    continue
                if _component_label(component):
                    continue
                unlabeled_controls.append(clean_text(component.get("xpathHint") or component.get("tag") or bucket))
        if unlabeled_controls:
            findings.append(
                _wcag_finding(
                    sc="4.1.2",
                    title="Interactive controls are missing an accessible name",
                    page=page_ref,
                    screenshot_path=screenshot_path,
                    severity="high" if any("button" in item or "input" in item for item in unlabeled_controls) else "medium",
                    evidence=[f"{len(unlabeled_controls)} unlabeled control(s)", *unlabeled_controls[:3]],
                    explanation=(
                        f"On {page_name}, at least one visible interactive element lacks usable text, aria-label, or label association. "
                        f"Examples include {', '.join(unlabeled_controls[:3])}."
                    ),
                    why_it_matters=(
                        f"Screen-reader and voice-control users depend on the accessible name to know what each control on {page_name} does. "
                        "Without it, a link or button can appear as an unnamed control and become very difficult to operate confidently."
                    ),
                    recommendation="Give each cited control a clear visible label or accessible name via text, aria-label, aria-labelledby, or a correctly associated form label.",
                    raw={"samples": unlabeled_controls[:12]},
                )
            )

        interactions = interactions_by_page.get(clean_text(page_ref.get("name")).lower()) or interactions_by_page.get(clean_text(page_ref.get("finalUrl")).lower()) or []
        failed = [
            item for item in interactions
            if isinstance(item, dict)
            and _is_meaningful_failed_interaction(item, page_ref)
        ]
        if failed:
            samples = [f"{_interaction_label(item)}: {clean_text(item.get('reason') or item.get('outcomeType'))}" for item in failed[:4]]
            first = failed[0]
            findings.append(
                _wcag_finding(
                    sc="4.1.2",
                    title="Interactive controls could not be operated reliably",
                    page=page_ref,
                    screenshot_path=clean_text(first.get("screenshotPath")) or screenshot_path,
                    severity="high",
                    evidence=[f"{len(failed)} failed tested interaction(s)", *samples[:3]],
                    explanation=(
                        f"On {page_name}, Playwright attempted safe controls and found controls that produced no visible result, disappeared from the fresh page, or failed during activation. "
                        f"Examples: {'; '.join(samples[:3])}."
                    ),
                    why_it_matters=(
                        f"When a cited control on {page_name} cannot be activated reliably, keyboard users, switch users, and ordinary mouse/touch users can all get blocked before completing the journey. "
                        "This is materially different from a cosmetic issue because the intended action may be unreachable."
                    ),
                    recommendation="Fix the cited controls so activation works from a fresh page load, then retest with mouse and keyboard. If a control is hidden until a drawer opens, remove it from the tab/click path until it is actually visible.",
                    raw={"samples": failed[:8]},
                )
            )

        keyboard = _keyboard_snapshot_for_page(page, results_data)
        if keyboard.get("tested"):
            coverage = safe_float(keyboard.get("coverage"), 100.0)
            interactive_count = safe_int(keyboard.get("interactiveCount"))
            focused_count = safe_int(keyboard.get("focusedCount"))
            if interactive_count and focused_count == 0:
                findings.append(
                    _wcag_finding(
                        sc="2.1.1",
                        title="Keyboard navigation cannot reach interactive controls",
                        page=page_ref,
                        screenshot_path=screenshot_path,
                        severity="high",
                        evidence=[f"{interactive_count} interactive controls detected", "0 controls reached by Tab"],
                        explanation=f"On {page_name}, the keyboard probe found visible interactive controls but Tab navigation did not reach any of them.",
                        why_it_matters=f"Users who rely on keyboard, switch control, or assistive technology would be unable to operate the visible journey on {page_name}.",
                        recommendation="Ensure links, buttons, form fields, menus, and custom controls are naturally focusable or have correct tabindex, then verify the full page can be traversed by Tab and Shift+Tab.",
                        raw=keyboard,
                    )
                )
            elif interactive_count and coverage < 70:
                samples = [_component_label(item) for item in keyboard.get("unfocusedSamples") or [] if isinstance(item, dict)]
                findings.append(
                    _wcag_finding(
                        sc="2.4.3",
                        title="Keyboard focus order does not cover enough interactive controls",
                        page=page_ref,
                        screenshot_path=screenshot_path,
                        severity="medium",
                        evidence=[f"Keyboard coverage {coverage:.1f}%", f"{focused_count}/{interactive_count} controls reached", *samples[:2]],
                        explanation=f"On {page_name}, the keyboard probe reached {focused_count} of {interactive_count} visible interactive controls by Tab.",
                        why_it_matters=f"If users cannot tab to every relevant control on {page_name}, navigation and form completion become dependent on a mouse or touch input.",
                        recommendation="Audit the tab order, remove focusable hidden controls, and make every visible custom control reachable in a logical sequence.",
                        raw=keyboard,
                    )
                )
            weak_focus = keyboard.get("weakFocusSamples") if isinstance(keyboard.get("weakFocusSamples"), list) else []
            if weak_focus:
                samples = [_component_label(item) for item in weak_focus if isinstance(item, dict)]
                findings.append(
                    _wcag_finding(
                        sc="2.4.7",
                        title="Focused controls do not show a reliable visible focus indicator",
                        page=page_ref,
                        screenshot_path=screenshot_path,
                        severity="medium",
                        evidence=[f"{len(weak_focus)} weak focus indicator sample(s)", *samples[:3]],
                        explanation=f"On {page_name}, the keyboard probe reached controls whose computed focused state did not expose a strong outline or focus shadow.",
                        why_it_matters=f"Without a visible focus indicator on {page_name}, keyboard users can lose track of where they are before activating a link, menu item, or form field.",
                        recommendation="Add a clear, high-contrast :focus-visible style to links, buttons, form fields, menu triggers, and custom controls.",
                        raw=keyboard,
                    )
                )

    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda item: (rank.get(clean_text(item.get("severity")).lower(), 3), clean_text(item.get("pageName")), clean_text(item.get("title"))))
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in findings:
        key = f"{item.get('wcagCriterion')}|{clean_text(item.get('title')).lower()}|{clean_text(item.get('pageName')).lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_profile(website_menu: Dict[str, Any], cleaned_data: Dict[str, Any], rendered_data: Dict[str, Any], checks_data: Dict[str, Any], results_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned_pages = cleaned_data.get("pages") or []
    rendered_pages = rendered_data.get("pages") or []
    homepage = homepage_page(cleaned_data) or {}
    meta = page_meta(homepage)
    navigation_labels = nav_labels(website_menu.get("navigation") or [])
    page_titles = dedupe_strings(
        clean_text(page_meta(page).get("title")) or clean_text(page.get("name"))
        for page in cleaned_pages[:10]
    )
    home_headings = headings(homepage)[:5]
    home_paragraphs = page_texts(homepage, "paragraphs")[:6]
    home_ctas = [
        text
        for text in page_texts(homepage, "ctaTexts")
        if text.lower() not in LOCALE_NOISE
        and text.lower() not in {"menu", "search"}
        and any(word in text.lower() for word in ACTION_WORDS)
    ][:8]
    text_pool = dedupe_strings(home_headings + home_paragraphs + home_ctas + navigation_labels + page_titles, limit=40)
    summary = (results_data or {}).get("summary") or {}
    interactions_tested = safe_int(summary.get("testedInteractions"))
    interactions_ok = safe_int(summary.get("successfulInteractions"))
    interaction_success_rate = (interactions_ok / interactions_tested) * 100.0 if interactions_tested else 52.0
    average_interaction_settle_ms = safe_float(summary.get("averageInteractionSettleMs"), 0.0)
    p95_interaction_settle_ms = safe_float(summary.get("p95InteractionSettleMs"), 0.0)
    performance_profiles = page_performance_profiles(cleaned_pages)
    performance_score = mean([item["score"] for item in performance_profiles], default=55.0)
    wcag_findings = wcag_findings_from_runtime(rendered_data, cleaned_pages, results_data)
    host = urlparse(clean_text(website_menu.get("homepage"))).netloc or clean_text(website_menu.get("homepage"))
    if host.startswith("www."):
        host = host[4:]
    display_name = host.split(".")[0].replace("-", " ").replace("_", " ").title() if host else "Client site"
    return {
        "site": {
            "homepage": clean_text(website_menu.get("homepage")),
            "domain": host,
            "display_name": display_name,
            "language": clean_text(website_menu.get("language") or meta.get("language")),
        },
        "counts": {
            "pages": len(cleaned_pages),
            "topLevelNavigation": len(website_menu.get("navigation") or []),
            "navigationItems": nav_count(website_menu.get("navigation") or []),
        },
        "messaging": {
            "heroHeadings": home_headings,
            "heroParagraphs": home_paragraphs,
            "heroCtas": home_ctas,
            "navigationLabels": navigation_labels,
            "pageTitles": page_titles,
            "textPool": text_pool,
            "audienceSignals": count_matches(text_pool, AUDIENCE_WORDS),
            "trustSignals": count_matches(text_pool, TRUST_WORDS),
            "proofSignals": count_proof_points(text_pool),
        },
        "metrics": {
            "interactionSuccessRate": interaction_success_rate,
            "averageInteractionSettleMs": average_interaction_settle_ms,
            "p95InteractionSettleMs": p95_interaction_settle_ms,
            "performanceScore": performance_score,
            "coreWebVitals": performance_score,
            "pageSpeed": performance_score,
            "designHealth": mean([mean(collect_numbers(page.get("renderedUi") or {}, "overallDesignSystemHealth"), default=0.0) for page in rendered_pages], default=0.0),
            "componentConsistency": mean([mean(collect_numbers(page.get("renderedUi") or {}, "componentConsistency"), default=0.0) for page in rendered_pages], default=0.0),
            "navigationClarity": mean([mean(collect_numbers(page.get("renderedUi") or {}, "navigationClarity"), default=0.0) for page in rendered_pages], default=0.0),
            "contentHierarchy": mean([mean(collect_numbers(page.get("renderedUi") or {}, "contentHierarchy"), default=0.0) for page in rendered_pages], default=0.0),
            "ctaClarity": mean([mean(collect_numbers(page.get("renderedUi") or {}, "ctaClarity"), default=0.0) for page in rendered_pages], default=0.0),
            "formUsability": mean([mean(collect_numbers(page.get("renderedUi") or {}, "formUsability"), default=0.0) for page in rendered_pages], default=0.0),
            "accessibilityReadiness": mean([mean(collect_numbers(page.get("renderedUi") or {}, "accessibilityReadiness"), default=0.0) for page in rendered_pages], default=0.0),
            "interactionFeedback": mean([mean(collect_numbers(page.get("renderedUi") or {}, "interactionFeedback"), default=0.0) for page in rendered_pages], default=0.0),
            "conversionReadiness": mean([mean(collect_numbers(page.get("renderedUi") or {}, "conversionReadiness"), default=0.0) for page in rendered_pages], default=0.0),
        },
        "performance": performance_profiles,
        "wcagFindings": wcag_findings,
        "sheetScores": {sheet_name: sheet_score((payload or {}).get("summary") or {}) for sheet_name, payload in (checks_data.get("sheets") or {}).items()},
        "homepageScreenshot": clean_text((meta.get("screenshotPaths") or {}).get("page")),
    }


def select_focus_screenshots(cleaned_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = []
    for page in cleaned_data.get("pages") or []:
        meta = page_meta(page)
        shot = clean_text((meta.get("screenshotPaths") or {}).get("page"))
        if not shot:
            continue
        name = clean_text(page.get("name"))
        url = clean_text(page.get("finalUrl") or page.get("url"))
        score = 100 if clean_text(meta.get("sourceType")).lower() == "homepage" else 0
        if "contact" in name.lower() or "contact" in url.lower():
            score += 30
        if any(word in f"{name} {url}".lower() for word in ("pricing", "tarif", "demo", "trial", "contact", "about", "team", "customer", "client")):
            score += 28
        if "solution" in name.lower() or "commerce" in " ".join(clean_text(item) for item in meta.get("pageTypeClues") or []).lower():
            score += 20
        candidates.append((score, {"page_name": name, "page_url": url, "title": clean_text(meta.get("title")), "reason": "Representative page", "screenshot_path": shot}))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:3]]


def select_scanned_pages(cleaned_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for page in cleaned_data.get("pages") or []:
        meta = page_meta(page)
        shot = clean_text((meta.get("screenshotPaths") or {}).get("page"))
        if not shot:
            continue
        name = clean_text(page.get("name")) or "Page"
        url = clean_text(page.get("finalUrl") or page.get("url"))
        key = url or name or shot
        if key in seen:
            continue
        seen.add(key)
        pages.append(
            {
                "page_name": name,
                "page_url": url,
                "title": clean_text(meta.get("title")),
                "source_type": clean_text(meta.get("sourceType")),
                "screenshot_path": shot,
            }
        )
    return pages


def select_vision_screenshots(scanned_pages: List[Dict[str, Any]], focus_screenshots: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    candidates: List[tuple[int, int, Dict[str, Any]]] = []
    seen: set[str] = set()
    combined = list(scanned_pages or []) + list(focus_screenshots or [])
    for order, item in enumerate(combined):
        shot = clean_text(item.get("screenshot_path"))
        if not shot:
            continue
        key = clean_text(item.get("page_url")) or clean_text(item.get("page_name")) or shot
        if key in seen:
            continue
        seen.add(key)
        text = " ".join(
            [
                clean_text(item.get("page_name")),
                clean_text(item.get("page_url")),
                clean_text(item.get("title")),
                clean_text(item.get("source_type")),
                clean_text(item.get("reason")),
            ]
        ).lower()
        score = 0
        if order == 0 or "homepage" in text or "accueil" in text or "home" in text:
            score += 100
        if any(word in text for word in VISION_TRUST_PAGE_WORDS):
            score += 90
        if "contact" in text:
            score += 35
        if any(word in text for word in ("solution", "product", "produit", "fashion", "pro", "pricing", "tarif", "demo", "trial", "customer", "client", "case study")):
            score += 30
        candidates.append((score, -order, item))
    candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
    selected = []
    for index, (_, _, item) in enumerate(candidates[: max(1, limit)]):
        selected.append({**item, "screenshot_index": index})
    return selected


def row_weight(row: Dict[str, Any]) -> float:
    basis = row.get("decision_basis") or ""
    return 1.0 if basis == "direct" else 0.75 if basis == "proxy" else 0.55 if basis == "interactive_required" else 0.7


def axis_rows(flat_rows: List[Dict[str, Any]], axis: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = {sheet.lower() for sheet in axis.get("focus") or []}
    keywords = AXIS_KEYWORDS.get(axis["id"], [])
    out = []
    for row in flat_rows:
        texts = [row.get("criterion"), row.get("rationale")] + (row.get("evidence") or [])
        sheet_match = clean_text(row.get("sheet")).lower() in focus
        keyword_hits = count_keyword_hits(texts, keywords)
        criterion_hits = count_keyword_hits([row.get("criterion")], keywords)
        if sheet_match or criterion_hits >= 1 or keyword_hits >= 2:
            out.append({**row, "_axis_relevance": round((2.0 if sheet_match else 0.0) + min(keyword_hits, 4) * 0.35, 2)})
    return out


def axis_row_score(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    if not rows:
        return {"score": 55.0, "confidence": 0.25}
    total = 0.0
    points = 0.0
    confs = []
    for row in rows:
        weight = row_weight(row)
        confidence = clamp(safe_float(row.get("confidence"), 0.5), 0.15, 1.0)
        confs.append(confidence)
        status = normalize_status(row.get("status"))
        status_score = 1.0 if status == "TRUE" else 0.0 if status == "FALSE" else 0.5
        factor = 0.5 + 0.5 * confidence
        total += weight * factor
        points += weight * factor * status_score
    return {"score": round((points / total) * 100.0, 1) if total else 55.0, "confidence": round(clamp(mean(confs, default=0.35) * min(1.0, len(rows) / 7.0), 0.2, 0.95), 2)}


def metric_score(axis_id: str, profile: Dict[str, Any]) -> float:
    metrics = profile["metrics"]
    sheets = profile["sheetScores"]
    messaging = profile["messaging"]
    if axis_id == "task_execution":
        runtime_performance = mean(
            [
                metrics.get("performanceScore", 55.0),
                metrics.get("coreWebVitals", 55.0),
                metrics.get("pageSpeed", 55.0),
            ],
            default=55.0,
        )
        settle_score = 55.0
        p95_settle = safe_float(metrics.get("p95InteractionSettleMs"), 0.0)
        if p95_settle > 0:
            settle_score = clamp(100.0 - max(0.0, p95_settle - 1000.0) / 35.0, 0.0, 100.0)
        return mean([runtime_performance, settle_score, metrics.get("interactionSuccessRate", 52.0), metrics.get("formUsability", 55.0), metrics.get("interactionFeedback", 55.0), metrics.get("ctaClarity", 55.0)], default=55.0)
    if axis_id == "flow_architecture":
        nav_structure = 82.0 if profile["counts"]["topLevelNavigation"] >= 3 and profile["counts"]["navigationItems"] >= 5 else 52.0
        return mean([metrics.get("navigationClarity", 55.0), metrics.get("contentHierarchy", 55.0), nav_structure, sheets.get("Navigation", 55.0)], default=55.0)
    if axis_id == "trust_accessibility":
        trust_score = clamp(40.0 + messaging.get("trustSignals", 0) * 12.0, 0.0, 100.0)
        return mean([metrics.get("accessibilityReadiness", 55.0), sheets.get("Content", 55.0), sheets.get("Labeling", 55.0), sheets.get("Forms", 55.0), trust_score], default=55.0)
    if axis_id == "ui_consistency":
        return mean([metrics.get("designHealth", 55.0), metrics.get("componentConsistency", 55.0), sheets.get("Presentation", 55.0), sheets.get("Visual hierarchy", 55.0)], default=55.0)
    if axis_id == "content_microcopy":
        copy_score = clamp(35.0 + min(len(profile["messaging"]["heroCtas"]), 4) * 10.0, 0.0, 100.0)
        return mean([sheets.get("Content", 55.0), sheets.get("Labeling", 55.0), copy_score], default=55.0)
    return 55.0


def score_ceiling(axis_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    counts = profile.get("counts") or {}
    messaging = profile.get("messaging") or {}
    metrics = profile.get("metrics") or {}
    pages = safe_int(counts.get("pages"))
    nav_items = safe_int(counts.get("navigationItems"))
    top_nav = safe_int(counts.get("topLevelNavigation"))
    ctas = len(messaging.get("heroCtas") or [])
    text_items = len(messaging.get("textPool") or [])
    trust_signals = safe_int(messaging.get("trustSignals")) + safe_int(messaging.get("proofSignals"))
    interaction_rate = safe_float(metrics.get("interactionSuccessRate"), 52.0)

    ceiling = 100.0
    reasons: List[str] = []

    def cap(value: float, reason: str) -> None:
        nonlocal ceiling
        if value < ceiling:
            ceiling = value
        if reason not in reasons:
            reasons.append(reason)

    if pages <= 1 and nav_items == 0:
        if axis_id == "task_execution":
            cap(45.0, "Only one page and no detected navigation/CTA path, so task completion evidence is weak.")
        elif axis_id == "flow_architecture":
            cap(35.0, "Only one page and no detected navigation, so information architecture cannot score as mature.")
        elif axis_id == "trust_accessibility":
            cap(48.0, "Only one page and no supporting journey or proof context limits trust/accessibility confidence.")
        elif axis_id == "ui_consistency":
            cap(55.0, "Too few repeated patterns were available to justify a high consistency score.")
        elif axis_id == "content_microcopy":
            cap(42.0, "Only one page with little journey copy cannot show strong content depth.")

    if ctas == 0:
        if axis_id == "task_execution":
            cap(48.0, "No outcome-specific CTA was detected on the primary page.")
        elif axis_id == "content_microcopy":
            cap(52.0, "No clear primary CTA copy was detected.")

    if top_nav == 0 and axis_id == "flow_architecture":
        cap(45.0, "No top-level navigation was detected.")

    if text_items <= 5:
        if axis_id == "content_microcopy":
            cap(45.0, "Very little meaningful visible copy was available.")
        elif axis_id == "trust_accessibility":
            cap(52.0, "Very little visible content was available for trust and accessibility judgment.")

    if trust_signals == 0 and axis_id == "trust_accessibility":
        cap(55.0, "No visible trust or proof signals were detected.")

    if interaction_rate < 50 and axis_id == "task_execution":
        cap(58.0, "Tested interactions succeeded less than half the time.")

    return {"ceiling": ceiling, "reasons": reasons}


def vision_axis_score(payload: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    severity = clean_text(payload.get("severity")).lower()
    confidence = clamp(safe_float(payload.get("confidence"), 0.5), 0.1, 1.0)
    base = {"low": 80.0, "medium": 58.0, "high": 36.0}.get(severity)
    return None if base is None else clamp(base + (confidence - 0.5) * 12.0, 0.0, 100.0)


def _detail_sentence(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."


def _is_low_signal_evidence(text: str, row: Dict[str, Any]) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return True
    if re.match(r"^(siteScore|checkedPages)\s*:", cleaned, flags=re.IGNORECASE):
        return True
    page_values = {
        clean_text(row.get("page_name")).lower(),
        clean_text(row.get("page_url")).lower(),
    }
    if cleaned.lower() in {value for value in page_values if value}:
        return True
    return len(cleaned.split()) <= 2 and cleaned.replace(".", "").isdigit() is False


def _split_rationale_and_recommendation(text: Any) -> tuple[str, str]:
    cleaned = clean_text(text)
    if not cleaned:
        return "", ""
    parts = re.split(r"\bRecommendation:\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)
    rationale = clean_text(parts[0])
    recommendation = clean_text(parts[1]) if len(parts) > 1 else ""
    return rationale, recommendation


def _short_evidence(row: Dict[str, Any]) -> str:
    evidence_values = [clean_text(item) for item in (row.get("evidence") or []) if clean_text(item)]
    if evidence_values:
        first = evidence_values[0]
        if not _is_low_signal_evidence(first, row):
            return first[:240]
    rationale, _ = _split_rationale_and_recommendation(row.get("rationale"))
    return rationale[:240]


def _page_label(row: Dict[str, Any]) -> str:
    return clean_text(row.get("page_name")) or clean_text(row.get("page_url")) or "the audited journey"


def _evidence_after_prefix(row: Dict[str, Any], suffix: str) -> str:
    needle = f"{suffix}:"
    for item in row.get("evidence") or []:
        text = clean_text(item)
        if text.lower().startswith(needle.lower()):
            return clean_text(text[len(needle) :])
    return ""


def _is_responsive_desktop_mobile_row(row: Dict[str, Any]) -> bool:
    criterion = clean_text(row.get("criterion")).lower()
    return "responsive" in criterion and "desktop" in criterion and "phone" in criterion


def _responsive_finding_from_row(row: Dict[str, Any], axis: Dict[str, Any]) -> Dict[str, Any]:
    page_name = _evidence_after_prefix(row, "failingPages: name") or clean_text(row.get("page_name")) or "Home"
    page_url = _evidence_after_prefix(row, "failingPages: url") or clean_text(row.get("page_url"))
    desktop_width = _evidence_after_prefix(row, "failingPages: desktopViewport: width")
    desktop_height = _evidence_after_prefix(row, "failingPages: desktopViewport: height")
    mobile_width = _evidence_after_prefix(row, "failingPages: mobileViewport: width")
    mobile_height = _evidence_after_prefix(row, "failingPages: mobileViewport: height")
    mobile_path = _evidence_after_prefix(row, "failingPages: mobileScreenshotPath")
    if mobile_path:
        candidate = Path(mobile_path)
        absolute_candidate = candidate if candidate.is_absolute() else ROOT_DIR / candidate
        clean_candidate = absolute_candidate.with_name("mobile-clean.png")
        if clean_candidate.exists():
            mobile_path = str(clean_candidate.relative_to(ROOT_DIR))
    overflowing_text = _evidence_after_prefix(row, "failingPages: overflowingElements: text")
    overflow_px = _evidence_after_prefix(row, "failingPages: mobileOverflowPx")
    viewport_evidence = (
        f"Desktop viewport {desktop_width}x{desktop_height}; phone viewport {mobile_width}x{mobile_height}. "
        f"The phone render exposes desktop-layout content that does not adapt correctly"
        f"{f', including `{overflowing_text}`' if overflowing_text else ''}"
        f"{f' (measured overflow: {overflow_px}px).' if overflow_px else '.'}"
    )
    recommendation = (
        "Rebuild the responsive breakpoint for the affected templates: remove fixed-width rows, let content groups collapse "
        "to a readable single-column flow, make hero/media blocks fluid, and test at 390px, 430px, 768px, and desktop widths "
        "before publishing."
    )
    return {
        "title": "Website layout breaks on phone screens",
        "pageName": page_name,
        "pageUrl": page_url,
        "sourceSheet": row["sheet"],
        "severity": "high",
        "confidence": row["confidence"],
        "evidence": viewport_evidence[:240],
        "visibleSignals": dedupe_strings(
            [
                "Phone viewport render fails responsive adaptation",
                f"Desktop viewport: {desktop_width}x{desktop_height}" if desktop_width and desktop_height else "",
                f"Phone viewport: {mobile_width}x{mobile_height}" if mobile_width and mobile_height else "",
                overflowing_text,
            ],
            limit=4,
        ),
        "explanation": (
            f"On {page_name}, the audit found that the website does not adapt reliably from desktop to phone. "
            f"{viewport_evidence}"
        ),
        "whyItMatters": (
            "This matters because mobile visitors cannot evaluate offers, navigation, or promotions with confidence when "
            "the page keeps desktop layout assumptions on a phone. In a GTM context, this can directly reduce discovery, "
            "store/product engagement, and trust for first-time mobile users."
        ),
        "recommendation": recommendation,
        "screenshotPath": mobile_path or row["screenshot_path"],
        "visualRegion": {
            "x": 0,
            "y": 0,
            "width": 1,
            "height": 1,
            "coordinate_system": "normalized_0_1",
            "description": "Full phone viewport showing responsive layout failure",
        },
        "evidenceBundle": None,
        "responsiveFailure": True,
    }


def _legacy_site_specific_recommendation(row: Dict[str, Any], axis: Dict[str, Any]) -> str:
    return ""


def _specific_recommendation(row: Dict[str, Any], axis: Dict[str, Any]) -> str:
    criterion = clean_text(row.get("criterion")).lower()
    if "search is available on every page" in criterion:
        return (
            "Add a persistent site search entry in the header on desktop and mobile, keep it available on key service, "
            "portfolio, resource, and contact pages, and return typed-ahead suggestions for services, case studies, or articles "
            "so prospects can reach relevant content quickly."
        )
    if "calls to action" in criterion and "clearly labeled" in criterion:
        return (
            "Convert ambiguous icon-only or ghost controls into labeled actions with visible button states. Prioritize primary "
            "journey CTAs such as `View services`, `View work`, `Contact us`, `Start a project`, or equivalent labels that match "
            "the site's actual language and information architecture."
        )
    if "verbs are used for all actions" in criterion:
        return (
            "Rewrite action labels so they start with a verb and name the outcome, such as `View services`, `Explore work`, "
            "`Contact the team`, `Start a project`, or `Read the case study`. Use labels drawn from the audited site's actual "
            "navigation and content model."
        )
    if "frequently used features" in criterion:
        return (
            "Expose high-frequency visitor actions in predictable header and mobile-nav locations: services, work or case studies, "
            "contact, language switching, and key proof points. Keep these controls reachable after scroll."
        )
    if "control over interactive content" in criterion:
        return (
            "Add consistent escape and orientation controls around interactive modules: back/close controls, carousel controls, "
            "clear filter reset, and visible current-state labels for browsing interactive content."
        )
    if "page layouts are consistent" in criterion:
        return (
            "Standardize core page templates around the same header, content hierarchy, CTA, card/list, and footer structure, "
            "then document the exceptions that are intentionally page-specific."
        )
    return _detail_sentence(_split_rationale_and_recommendation(row.get("rationale"))[1]) or clean_text(axis.get("default_fix"))


def _polish_issue_text(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    cleaned = re.sub(
        r"(?<=[a-z0-9])\s+(?=(At least|The available|The visible|Some |Average |Actual |No |Frequently |Interactive |Calls |Control |Expose |Create |Front-loaded |Most |Primary ))",
        ". ",
        cleaned,
    )
    cleaned = cleaned.replace("=False", " was not found")
    cleaned = cleaned.replace("=True", " was found")
    return _detail_sentence(cleaned)


MAJOR_ISSUE_PATTERN = re.compile(
    r"\b("
    r"broken|breaks|blocked|blocking|cannot|can't|does not work|doesn't work|not working|"
    r"fails?|failure|error|404|dead link|wrong page|wrong destination|incorrect destination|"
    r"malfunction|unusable|unreachable|unresponsive|non[- ]?clickable|click failed|"
    r"submit failed|cannot submit|not submit|crash|clipped|overflow|horizontal scroll|"
    r"impossible|inaccessible|key action|primary action"
    r")\b",
    flags=re.IGNORECASE,
)

MINOR_ISSUE_PATTERN = re.compile(
    r"\b("
    r"visual styles?|style|spacing|alignment|similar types|consistent ways?|negative space|"
    r"microcopy|tone|label clarity|hierarchy|proof signal|pattern|template|polish"
    r")\b",
    flags=re.IGNORECASE,
)

LOW_IMPACT_POLISH_PATTERN = re.compile(
    r"\b("
    r"spacing|alignment|negative space|polish|tone|label clarity|word count|microcopy|proof signal"
    r")\b",
    flags=re.IGNORECASE,
)


def issue_severity_from_row(row: Dict[str, Any], axis: Dict[str, Any]) -> str:
    """Classify issue impact, not just detection confidence."""
    confidence = safe_float(row.get("confidence"), 0.0)
    criterion = clean_text(row.get("criterion"))
    sheet = clean_text(row.get("sheet"))
    rationale = clean_text(row.get("rationale"))
    evidence = " ".join(clean_text(item) for item in (row.get("evidence") or []) if clean_text(item))
    bundle = row.get("evidence_bundle")
    target_text = ""
    if isinstance(bundle, dict):
        target = bundle.get("target")
        if isinstance(target, dict):
            target_text = " ".join(
                clean_text(target.get(key))
                for key in ("component_text", "component_type", "issue_kind")
                if clean_text(target.get(key))
            )
    combined = " ".join([criterion, sheet, rationale, evidence, target_text])
    axis_id = clean_text(axis.get("id"))

    if confidence < 0.45:
        return "low"

    has_major_signal = bool(MAJOR_ISSUE_PATTERN.search(combined))
    if has_major_signal and confidence >= 0.6:
        return "high"

    if axis_id == "task_execution":
        interaction_sheet = "interaction" in sheet.lower()
        primary_action = re.search(r"\b(primary|cta|call to action|button|form|submit|search|checkout|contact)\b", combined, re.IGNORECASE)
        if interaction_sheet and primary_action and confidence >= 0.75:
            return "medium"

    if LOW_IMPACT_POLISH_PATTERN.search(combined):
        return "low"

    if MINOR_ISSUE_PATTERN.search(combined):
        return "medium" if confidence >= 0.72 else "low"

    if confidence >= 0.55:
        return "medium"
    return "low"


def normalize_ai_issue_severity(raw_severity: Any, confidence: float, *parts: Any) -> str:
    severity = clean_text(raw_severity).lower()
    if severity not in {"high", "medium", "low"}:
        severity = "medium"
    combined = " ".join(clean_text(part) for part in parts if clean_text(part))
    if confidence < 0.45:
        return "low"
    if severity == "high" and not MAJOR_ISSUE_PATTERN.search(combined):
        return "medium"
    if severity == "medium" and confidence < 0.55:
        return "low"
    return severity


def issue_specific_why_it_matters(item_or_row: Dict[str, Any], axis: Dict[str, Any], page_label: str, evidence: str = "") -> str:
    axis_id = clean_text(axis.get("id"))
    title = clean_text(item_or_row.get("title") or item_or_row.get("criterion"))
    haystack = " ".join([title, evidence, clean_text(item_or_row.get("rationale")), clean_text(item_or_row.get("sourceSheet"))]).lower()
    page = clean_text(page_label) or "this page"

    if "wcag 2.2 sc 1.4.3" in haystack or "contrast" in haystack:
        return (
            f"On {page}, this affects whether users can actually read the cited content and controls, not just whether the page looks polished. "
            "Low contrast is especially damaging for low-vision users, mobile users in glare, and anyone scanning contact details or CTAs quickly."
        )
    if "wcag 2.2 sc 2.5.8" in haystack or "target size" in haystack or "small target" in haystack:
        return (
            f"On {page}, small targets make navigation and form actions error-prone for touch, tremor, reduced dexterity, and zoomed layouts. "
            "A user may tap the wrong item or abandon the action because the control is physically hard to hit."
        )
    if "wcag 2.2 sc 2.1.1" in haystack or "keyboard" in haystack or "could not be operated" in haystack or "no visible result" in haystack:
        return (
            f"On {page}, this can block users who rely on keyboard, switch control, or assistive technology, and it also signals a real interaction defect for mouse and touch users. "
            "If the control cannot be activated reliably, the journey can stop at that point."
        )
    if "wcag 2.2 sc 2.4.7" in haystack or "focus indicator" in haystack:
        return (
            f"On {page}, keyboard users need to see which control currently has focus before pressing Enter or Space. "
            "Without that visual cue, users can activate the wrong link or lose their place in the page."
        )
    if "wcag 2.2 sc 2.4.3" in haystack or "focus order" in haystack:
        return (
            f"On {page}, an incomplete or illogical tab order makes the page depend on pointer input. "
            "Users should be able to move through navigation, forms, and CTAs in the same order the visual layout suggests."
        )
    if "wcag 2.2 sc 4.1.2" in haystack or "accessible name" in haystack or "name, role, value" in haystack:
        return (
            f"On {page}, unnamed controls are difficult to understand through screen readers and voice control. "
            "Users hear or target a generic element instead of the actual action, which makes confident navigation much harder."
        )
    if "lcp" in haystack or "largest contentful paint" in haystack:
        return (
            f"On {page}, the main visible content arrives later than users expect, so the first impression feels slower even if the page eventually becomes usable. "
            "The cited LCP element should be treated as the first optimization target."
        )
    if "inp" in haystack or "interaction response" in haystack:
        return (
            f"On {page}, delayed interaction response makes clicks and taps feel unreliable. "
            "Users can double-click, abandon, or lose confidence because the interface does not acknowledge input quickly enough."
        )
    if "load event" in haystack or "page load time" in haystack:
        return (
            f"On {page}, the page keeps loading far beyond the target window, which means users may see late assets, delayed scripts, or unstable readiness after the first screen appears. "
            "This affects perceived quality before users evaluate the content."
        )
    if "page weight" in haystack or "transferred resources" in haystack or "heavy image" in haystack or "largest slow asset" in haystack:
        return (
            f"On {page}, the cited resource weight creates unnecessary waiting and bandwidth cost, especially on mobile or slower connections. "
            "Heavy startup assets also make performance regressions more likely as the site grows."
        )
    if "cls" in haystack or "layout shift" in haystack:
        return (
            f"On {page}, layout movement can cause users to lose their reading position or activate the wrong control. "
            "The affected components should reserve stable space before late-loading content appears."
        )
    if axis_id == "task_execution":
        return (
            f"On {page}, this issue affects whether users can complete the intended action without hesitation or recovery work. "
            "The cited evidence should be fixed where it appears first, then retested on the same path."
        )
    if axis_id == "flow_architecture":
        return (
            f"On {page}, this issue affects how quickly users can understand where they are and where to go next. "
            "When the structure is unclear, users spend effort interpreting the site instead of evaluating the offer."
        )
    if axis_id == "ui_consistency":
        return (
            f"On {page}, this issue weakens recognition of repeated components and actions. "
            "Users have to re-interpret similar elements instead of relying on a stable pattern."
        )
    if axis_id == "content_microcopy":
        return (
            f"On {page}, this wording issue affects whether users understand the action, value, or next step at the moment they need it. "
            "Clear microcopy should reduce hesitation, not add interpretation work."
        )
    return (
        f"On {page}, the cited evidence creates a specific usability risk for this screen rather than a general design concern. "
        "Fixing it should make the affected action or content easier to understand and use."
    )


def _visual_region_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bundle = row.get("evidence_bundle")
    if not isinstance(bundle, dict):
        return None
    target = bundle.get("target")
    if not isinstance(target, dict):
        return None
    if _low_quality_evidence_target(target):
        return None
    rect = target.get("rect")
    if not isinstance(rect, dict):
        return None
    return {
        "x": safe_float(rect.get("x")),
        "y": safe_float(rect.get("y")),
        "width": safe_float(rect.get("width")),
        "height": safe_float(rect.get("height")),
        "coordinate_system": "pixels",
        "description": clean_text(target.get("component_text") or target.get("component_type") or target.get("target_kind")),
    }


def _low_quality_evidence_target(target: Dict[str, Any]) -> bool:
    text = clean_text(target.get("component_text")).strip()
    normalized = text.lower()
    component_type = clean_text(target.get("component_type")).lower()
    issue_kind = clean_text(target.get("issue_kind")).lower()
    if not text:
        return True
    if normalized in {"en", "fr", "ar", "de", "es", "it", "nl", "pt"}:
        return True
    if len(normalized) <= 2 and "button" in component_type:
        return True
    if issue_kind in {"presence", "proxy"} and normalized in {"menu", "close", "x"}:
        return True
    return False


def _clean_evidence_bundle(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bundle = row.get("evidence_bundle")
    if not isinstance(bundle, dict):
        return None
    target = bundle.get("target")
    if isinstance(target, dict) and _low_quality_evidence_target(target):
        cleaned = dict(bundle)
        cleaned["target"] = {}
        return cleaned
    return bundle


def _visible_signals_from_row(row: Dict[str, Any], evidence: str) -> List[str]:
    signals: List[str] = []
    if clean_text(row.get("criterion")):
        signals.append(clean_text(row["criterion"]))
    if evidence:
        signals.append(evidence)
    bundle = row.get("evidence_bundle")
    if isinstance(bundle, dict):
        target = bundle.get("target")
        if isinstance(target, dict):
            signals.extend(
                text
                for text in (
                    clean_text(target.get("component_text")),
                    clean_text(target.get("component_type")),
                    clean_text(target.get("issue_kind")),
                )
                if text
            )
    return dedupe_strings(signals, limit=4)


def finding_from_row(row: Dict[str, Any], axis: Dict[str, Any]) -> Dict[str, Any]:
    if _is_responsive_desktop_mobile_row(row):
        return _responsive_finding_from_row(row, axis)

    severity = issue_severity_from_row(row, axis)
    page_label = _page_label(row)
    evidence = _short_evidence(row)
    visible_signals = _visible_signals_from_row(row, evidence)
    rationale, _extracted_recommendation = _split_rationale_and_recommendation(row.get("rationale"))
    rationale_sentence = _polish_issue_text(rationale)
    evidence_sentence = _polish_issue_text(evidence) if evidence and evidence.lower() not in rationale.lower() else ""
    explanation = (
        f"On {page_label}, the audit found that '{row['criterion']}' is not fully met. "
        f"{rationale_sentence or 'The current interface does not give users enough support around this part of the experience.'}"
        f"{f' Observed signal: {evidence_sentence}' if evidence_sentence else ''}"
    ).strip()
    why_it_matters = issue_specific_why_it_matters(row, axis, page_label, evidence)
    recommendation = _specific_recommendation(row, axis) or (
        f"Fix this first on {page_label} by clarifying the interaction, tightening the label or feedback, "
        f"and making the intended next step more obvious."
    )
    return {
        "title": row["criterion"],
        "pageName": row["page_name"],
        "pageUrl": row["page_url"],
        "sourceSheet": row["sheet"],
        "severity": severity,
        "confidence": row["confidence"],
        "evidence": clean_text(evidence)[:240],
        "visibleSignals": visible_signals,
        "explanation": explanation,
        "whyItMatters": why_it_matters,
        "recommendation": recommendation,
        "screenshotPath": row["screenshot_path"],
        "visualRegion": _visual_region_from_row(row),
        "evidenceBundle": _clean_evidence_bundle(row),
    }


def performance_finding_from_profile(item: Dict[str, Any], axis: Dict[str, Any]) -> Dict[str, Any]:
    page_label = clean_text(item.get("pageName")) or "Page"
    score = safe_float(item.get("score"), 55.0)
    fcp = safe_float(item.get("firstContentfulPaintMs"), 0.0)
    lcp = safe_float(item.get("largestContentfulPaintMs"), 0.0)
    inp = safe_float(item.get("interactionToNextPaintCandidateMs"), 0.0)
    cls = safe_float(item.get("cumulativeLayoutShift"), 0.0)
    load = safe_float(item.get("loadEventMs"), 0.0)
    ttfb = safe_float(item.get("ttfbMs"), 0.0)
    transfer_mb = safe_float(item.get("totalTransferSize"), 0.0) / 1_000_000.0
    blocking_count = safe_int(item.get("blockingResourceCount"))
    signals = [
        f"LCP {int(round(lcp))} ms" if lcp else "",
        f"INP candidate {int(round(inp))} ms" if inp else "",
        f"CLS {cls:.3f}" if cls else "",
        f"FCP {int(round(fcp))} ms" if fcp else "",
        f"load event {int(round(load))} ms" if load else "",
        f"TTFB {int(round(ttfb))} ms" if ttfb else "",
        f"{transfer_mb:.1f} MB transferred" if transfer_mb else "",
        f"{blocking_count} slow script/CSS candidate(s)" if blocking_count else "",
    ]
    signals = dedupe_strings(signals, limit=5)
    if not signals:
        signals = ["Playwright lab performance snapshot available"]
    title = "Runtime performance snapshot needs optimization" if score < 65 else "Runtime performance is acceptable in the lab snapshot"
    severity = "high" if score < 45 else "medium" if score < 65 else "low"
    evidence = "; ".join(signals)
    return {
        "title": title,
        "pageName": page_label,
        "pageUrl": clean_text(item.get("pageUrl")),
        "sourceSheet": "Runtime Performance",
        "severity": severity,
        "confidence": 0.72,
        "evidence": evidence[:240],
        "visibleSignals": signals,
        "explanation": (
            f"On {page_label}, the audit captured a browser lab performance score of {int(round(score))}/100. "
            f"{evidence}."
        ),
        "whyItMatters": (
            f"This matters because {AXIS_USER_IMPACT[axis['id']]}. Slow first paint, heavy resources, or delayed load can make users abandon before the UX is evaluated."
        ),
        "recommendation": (
            "Run Lighthouse or Web Vitals for the page, then prioritize LCP candidates, render-blocking CSS/JS, image weight, caching, and server response time."
        ),
        "screenshotPath": clean_text(item.get("screenshotPath")),
        "visualRegion": None,
        "evidenceBundle": {
            "source": "playwright_performance_snapshot",
            "raw": item,
        },
    }


def _format_ms(value: float) -> str:
    return f"{int(round(value))} ms"


def _format_mb(value: float) -> str:
    return f"{value / 1_000_000.0:.1f} MB"


def _resource_name(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return "largest measured resource"
    try:
        path = urlparse(raw).path
        name = Path(path).name
        return name or raw
    except Exception:
        return raw


def _performance_kpi_finding(
    item: Dict[str, Any],
    axis: Dict[str, Any],
    *,
    title: str,
    severity: str,
    metric_label: str,
    observed: str,
    target: str,
    recommendation: str,
    extra_signal: str = "",
) -> Dict[str, Any]:
    page_label = clean_text(item.get("pageName")) or "Page"
    evidence_parts = [f"{metric_label}: {observed}", f"target: {target}", extra_signal]
    evidence = "; ".join(part for part in evidence_parts if clean_text(part))
    return {
        "title": title,
        "pageName": page_label,
        "pageUrl": clean_text(item.get("pageUrl")),
        "sourceSheet": "Runtime Performance KPI",
        "severity": severity,
        "confidence": 0.82,
        "evidence": evidence[:240],
        "visibleSignals": dedupe_strings(evidence_parts, limit=4),
        "explanation": (
            f"On {page_label}, the audit measured {metric_label.lower()} at {observed}, above the audit target of {target}. "
            "This is based on the Playwright browser performance snapshot captured during the crawl."
        ),
        "whyItMatters": issue_specific_why_it_matters(
            {"title": title, "sourceSheet": "Runtime Performance KPI", "rationale": f"{metric_label} {observed} target {target}"},
            axis,
            page_label,
            evidence,
        ),
        "recommendation": recommendation,
        "screenshotPath": clean_text(item.get("screenshotPath")),
        "visualRegion": None,
        "evidenceBundle": {
            "source": "playwright_performance_kpi",
            "raw": item,
            "metric": metric_label,
            "observed": observed,
            "threshold": target,
        },
    }


def performance_kpi_findings_from_profile(item: Dict[str, Any], axis: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    lcp = safe_float(item.get("largestContentfulPaintMs"), 0.0)
    inp = safe_float(item.get("interactionToNextPaintCandidateMs"), 0.0)
    cls = safe_float(item.get("cumulativeLayoutShift"), -1.0)
    fcp = safe_float(item.get("firstContentfulPaintMs"), 0.0)
    load = safe_float(item.get("loadEventMs"), 0.0)
    ttfb = safe_float(item.get("ttfbMs"), 0.0)
    transfer = safe_float(item.get("totalTransferSize"), 0.0)
    resource_count = safe_int(item.get("resourceCount"))
    largest_resource = item.get("largestBlockingResource") if isinstance(item.get("largestBlockingResource"), dict) else {}
    largest_resource_size = safe_float(largest_resource.get("transferSize"), 0.0) if isinstance(largest_resource, dict) else 0.0
    largest_resource_name = _resource_name(largest_resource.get("name")) if isinstance(largest_resource, dict) else ""

    if lcp > 2500:
        lcp_url = _resource_name(item.get("largestContentfulPaintUrl"))
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Largest Contentful Paint is slower than the Web Vitals target",
                severity="high" if lcp > 4000 else "medium",
                metric_label="LCP",
                observed=_format_ms(lcp),
                target="<= 2500 ms",
                extra_signal=f"LCP candidate: {lcp_url}" if lcp_url else "",
                recommendation=(
                    "Optimize the LCP element first: compress or resize the hero image, preload the critical asset, "
                    "reduce render-blocking CSS/JS, and keep the above-the-fold layout server-ready."
                ),
            )
        )
    if inp > 200:
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Interaction response is slower than the Web Vitals target",
                severity="high" if inp > 500 else "medium",
                metric_label="INP candidate",
                observed=_format_ms(inp),
                target="<= 200 ms",
                recommendation=(
                    "Reduce main-thread JavaScript work around interactive controls, split long tasks, defer non-critical scripts, "
                    "and retest the most important buttons/forms after optimization."
                ),
            )
        )
    if cls > 0.1:
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Layout shift is above the Web Vitals target",
                severity="high" if cls > 0.25 else "medium",
                metric_label="CLS",
                observed=f"{cls:.3f}",
                target="<= 0.100",
                recommendation=(
                    "Reserve dimensions for images, embeds, banners, and late-loading UI; avoid injecting content above existing content "
                    "after the first render."
                ),
            )
        )
    if ttfb > 800:
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Server response time is above the TTFB target",
                severity="high" if ttfb > 1800 else "medium",
                metric_label="TTFB",
                observed=_format_ms(ttfb),
                target="<= 800 ms",
                recommendation=(
                    "Improve server response with caching, CDN edge delivery, lighter backend work, and fewer blocking redirects before HTML is returned."
                ),
            )
        )
    if fcp > 1800:
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="First Contentful Paint is slower than the visual feedback target",
                severity="high" if fcp > 3000 else "medium",
                metric_label="FCP",
                observed=_format_ms(fcp),
                target="<= 1800 ms",
                recommendation=(
                    "Inline or preload critical CSS, defer non-critical JavaScript, and prioritize the first visible text or brand mark."
                ),
            )
        )
    if load > 4000:
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Page load time is above the audit target",
                severity="high" if load > 8000 else "medium",
                metric_label="Load event",
                observed=_format_ms(load),
                target="<= 4000 ms",
                recommendation=(
                    "Reduce non-critical resources loaded during startup, lazy-load below-the-fold media, defer third-party scripts, "
                    "and keep the initial route focused on the assets needed for the first screen."
                ),
            )
        )
    if transfer > 2_000_000:
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Page weight is heavy for a first visit",
                severity="high" if transfer > 5_000_000 else "medium",
                metric_label="Transferred resources",
                observed=_format_mb(transfer),
                target="<= 2.0 MB",
                extra_signal=f"{resource_count} resources requested" if resource_count else "",
                recommendation=(
                    "Compress and resize images, ship modern formats such as WebP/AVIF where possible, remove unused scripts/styles, "
                    "and lazy-load media that is not visible in the first viewport."
                ),
            )
        )
    if largest_resource_size > 250_000:
        initiator = clean_text(largest_resource.get("initiatorType")) if isinstance(largest_resource, dict) else ""
        is_image_like = bool(re.search(r"\.(png|jpe?g|webp|gif|avif)(\?|$)", clean_text(largest_resource.get("name")), flags=re.IGNORECASE)) if isinstance(largest_resource, dict) else False
        findings.append(
            _performance_kpi_finding(
                item,
                axis,
                title="Heavy image or blocking asset delays page readiness" if is_image_like else "Heavy blocking asset delays page readiness",
                severity="high" if largest_resource_size > 500_000 else "medium",
                metric_label="Largest slow asset",
                observed=f"{largest_resource_name} ({_format_mb(largest_resource_size)})",
                target="<= 250 KB for a critical startup asset",
                extra_signal=f"initiator: {initiator}" if initiator else "",
                recommendation=(
                    "Optimize this critical asset directly: resize it to displayed dimensions, compress it, use a modern format, "
                    "and avoid loading it as render-blocking CSS if it is not needed for the first viewport."
                ),
            )
        )
    return findings


def performance_kpi_findings(items: List[Dict[str, Any]], axis: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        for finding in performance_kpi_findings_from_profile(item, axis):
            key = f"{finding['title']}|{finding.get('pageUrl') or finding.get('pageName')}"
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda item: (rank.get(clean_text(item.get("severity")).lower(), 3), clean_text(item.get("pageName")), clean_text(item.get("title"))))
    return findings[:limit]


def _performance_evidence_labels(items: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    labels: List[str] = []
    for item in items[:limit]:
        page = clean_text(item.get("pageName")) or "Page"
        parts = []
        lcp = safe_float(item.get("largestContentfulPaintMs"), 0.0)
        inp = safe_float(item.get("interactionToNextPaintCandidateMs"), 0.0)
        cls = safe_float(item.get("cumulativeLayoutShift"), -1.0)
        fcp = safe_float(item.get("firstContentfulPaintMs"), 0.0)
        if lcp:
            parts.append(f"LCP {int(round(lcp))} ms")
        if inp:
            parts.append(f"INP candidate {int(round(inp))} ms")
        if cls >= 0:
            parts.append(f"CLS {cls:.3f}")
        if fcp:
            parts.append(f"FCP {int(round(fcp))} ms")
        if parts:
            labels.append(f"{page}: " + ", ".join(parts))
    return labels


def _interaction_kpi_labels(metrics: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    success = safe_float(metrics.get("interactionSuccessRate"), 0.0)
    avg_settle = safe_float(metrics.get("averageInteractionSettleMs"), 0.0)
    p95_settle = safe_float(metrics.get("p95InteractionSettleMs"), 0.0)
    if success:
        labels.append(f"Safe interaction success rate {success:.0f}%")
    if avg_settle:
        labels.append(f"Average tested interaction settle time {int(round(avg_settle))} ms")
    if p95_settle:
        labels.append(f"P95 tested interaction settle time {int(round(p95_settle))} ms")
    return labels


TRUST_WCAG_ROW_PATTERN = re.compile(
    r"\b("
    r"contrast|readable|legible|label|instruction|required|error|focus|keyboard|tab|aria|accessible|"
    r"button|link|clickable|target|checkbox|form|field|input|blinking|flashing|animation|motion|"
    r"horizontal scrolling|responsive|screen resolution"
    r")\b",
    flags=re.IGNORECASE,
)

TRUST_GENERIC_ROW_PATTERN = re.compile(
    r"\b(language is consistent|terms, language and tone|page layouts are consistent|visual styles are consistent|similar types of information|negative space)\b",
    flags=re.IGNORECASE,
)


def trust_accessibility_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = []
    for row in rows:
        text = " ".join([clean_text(row.get("criterion")), clean_text(row.get("rationale")), " ".join(row.get("evidence") or [])])
        if TRUST_GENERIC_ROW_PATTERN.search(text):
            continue
        if TRUST_WCAG_ROW_PATTERN.search(text):
            filtered.append(row)
    return filtered


def ai_discovered_findings(vision: Dict[str, Any], screenshots: List[Dict[str, Any]], axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = (vision or {}).get("result")
    if not isinstance(result, dict):
        return []

    axis_by_id = {axis["id"]: axis for axis in axes}
    raw_items = []
    for key in ("visual_trust_findings", "criteria_discoveries", "priority_issues"):
        items = result.get(key)
        if isinstance(items, list):
            raw_items.extend(item for item in items if isinstance(item, dict))

    findings: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_items:
        axis_id = clean_text(item.get("axis_id"))
        axis = axis_by_id.get(axis_id)
        if not axis:
            continue
        title = clean_text(item.get("criterion") or item.get("title"))
        if not title:
            continue
        key = (axis_id, title.lower())
        if key in seen:
            continue
        seen.add(key)

        screenshot = {}
        if item.get("screenshot_index") not in (None, ""):
            screenshot_index = safe_int(item.get("screenshot_index"))
            if 0 <= screenshot_index < len(screenshots):
                screenshot = screenshots[screenshot_index]
        if not screenshot:
            page_name = clean_text(item.get("page_name"))
            page_url = clean_text(item.get("page_url"))
            for candidate in screenshots:
                if page_url and page_url == clean_text(candidate.get("page_url")):
                    screenshot = candidate
                    break
                if page_name and page_name.lower() == clean_text(candidate.get("page_name")).lower():
                    screenshot = candidate
                    break

        page_name = clean_text(item.get("page_name")) or clean_text(screenshot.get("page_name")) or "AI-reviewed screen"
        page_url = clean_text(item.get("page_url")) or clean_text(screenshot.get("page_url"))
        visible_signals = dedupe_strings(item.get("visible_signals") or [], limit=4)
        evidence = _detail_sentence(clean_text(item.get("evidence") or item.get("reason") or " ".join(visible_signals))) or "The AI review identified this issue from the reviewed screenshots."
        explanation = f"On {page_name}, the AI review identified a GTM issue that is not necessarily covered by the workbook criteria: {evidence}"
        why_it_matters = _detail_sentence(clean_text(item.get("why_it_matters"))) or (
            f"This matters because {AXIS_USER_IMPACT[axis_id]}. In a GTM context, it can reduce clarity, trust, or sales readiness during a first review."
        )
        recommendation = _detail_sentence(clean_text(item.get("recommendation"))) or clean_text(axis.get("default_fix")) or "Review this screen manually and prioritize the change if it affects a primary commercial journey."
        visual_region = item.get("visual_region") if isinstance(item.get("visual_region"), dict) else item.get("visualRegion") if isinstance(item.get("visualRegion"), dict) else None
        confidence = clamp(safe_float(item.get("confidence"), 0.65), 0.0, 1.0)
        severity = normalize_ai_issue_severity(
            item.get("severity"),
            confidence,
            title,
            evidence,
            why_it_matters,
            recommendation,
            " ".join(visible_signals),
        )
        findings.append(
            {
                "title": title,
                "axisId": axis_id,
                "axisName": axis["short_name"],
                "pageName": page_name,
                "pageUrl": page_url,
                "sourceSheet": "AI Discovery",
                "severity": severity,
                "confidence": confidence,
                "evidence": evidence[:240],
                "visibleSignals": visible_signals,
                "explanation": explanation,
                "whyItMatters": why_it_matters,
                "recommendation": recommendation,
                "screenshotPath": clean_text(screenshot.get("screenshot_path")),
                "visualRegion": visual_region,
                "screenshotIndex": safe_int(item.get("screenshot_index"), -1),
                "evidenceBundle": None,
                "aiDiscovered": True,
                "outsideWorkbook": bool(item.get("outside_workbook")),
            }
        )
    return findings[:6]


def attach_ai_findings_to_axes(axes: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> None:
    if not findings:
        return
    axes_by_id = {axis["id"]: axis for axis in axes}
    for finding in findings:
        axis_id = clean_text(finding.get("axisId"))
        axis = axes_by_id.get(axis_id)
        if not axis:
            continue
        current = axis.get("painPoints") or []
        duplicate = any(clean_text(item.get("title")).lower() == clean_text(finding.get("title")).lower() for item in current)
        if duplicate:
            continue
        axis["painPoints"] = [finding, *current][:4]
        axis["evidence"] = dedupe_strings([finding.get("evidence")] + (axis.get("evidence") or []), limit=6)
        axis["signals"]["aiDiscoveredFindings"] = safe_int(axis["signals"].get("aiDiscoveredFindings")) + 1


def build_axis(axis: Dict[str, Any], flat_rows: List[Dict[str, Any]], profile: Dict[str, Any], vision_axes: Dict[str, Any]) -> Dict[str, Any]:
    rows = axis_rows(flat_rows, axis)
    rows_scored = axis_row_score(rows)
    heuristic = metric_score(axis["id"], profile)
    axis_review = (vision_axes or {}).get(axis["id"]) or {}
    vscore = vision_axis_score(axis_review)
    weighted = [(rows_scored["score"], 0.55), (heuristic, 0.35)] + ([(vscore, 0.10)] if vscore is not None else [])
    raw_score = round(sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted), 1)
    ceiling = score_ceiling(axis["id"], profile)
    score = round(min(raw_score, safe_float(ceiling.get("ceiling"), 100.0)), 1)
    failed = sorted(
        [row for row in rows if row["status"] == "FALSE"],
        key=lambda row: (
            0 if axis["id"] == "flow_architecture" and _is_responsive_desktop_mobile_row(row) else 2 if _is_responsive_desktop_mobile_row(row) else 1,
            -safe_float(row.get("_axis_relevance"), 0.0),
            -row["confidence"],
            row["sheet"],
            row["row"],
        ),
    )
    passed = sorted([row for row in rows if row["status"] == "TRUE"], key=lambda row: (-row["confidence"], row["sheet"], row["row"]))
    performance_profiles = profile.get("performance") if isinstance(profile.get("performance"), list) else []
    wcag_findings = profile.get("wcagFindings") if isinstance(profile.get("wcagFindings"), list) else []
    if axis["id"] == "trust_accessibility" and wcag_findings:
        high_count = sum(1 for item in wcag_findings if clean_text(item.get("severity")).lower() == "high")
        medium_count = sum(1 for item in wcag_findings if clean_text(item.get("severity")).lower() == "medium")
        wcag_penalty = min(35.0, high_count * 5.0 + medium_count * 2.5)
        score = round(max(0.0, score - wcag_penalty), 1)
    performance_findings: List[Dict[str, Any]] = []
    performance_strengths: List[Dict[str, Any]] = []
    if axis["id"] == "task_execution":
        slow_pages = [item for item in performance_profiles if safe_float(item.get("score"), 100.0) < 65.0]
        good_pages = [item for item in performance_profiles if safe_float(item.get("score"), 0.0) >= 75.0]
        performance_findings = performance_kpi_findings(performance_profiles, axis, limit=4)
        if not performance_findings:
            performance_findings = [performance_finding_from_profile(item, axis) for item in slow_pages[:2]]
        performance_strengths = [performance_finding_from_profile(item, axis) for item in good_pages[:1]]
    if axis["id"] == "trust_accessibility":
        failed = trust_accessibility_rows(failed)
        passed = trust_accessibility_rows(passed)
    pain_points = [*performance_findings, *[finding_from_row(row, axis) for row in failed[: max(0, 6 - len(performance_findings))]]]
    if axis["id"] == "trust_accessibility":
        pain_points = [*wcag_findings[:6], *pain_points[: max(0, 6 - min(6, len(wcag_findings)))]]
    strengths = [*performance_strengths, *[finding_from_row(row, axis) for row in passed[: max(0, 2 - len(performance_strengths))]]]
    vision_observation = clean_text(axis_review.get("observation"))
    missing_context = clean_text(axis_review.get("missing_context"))
    proof_points = dedupe_strings(axis_review.get("proof_points") or [], limit=4)
    performance_evidence = (
        _performance_evidence_labels(performance_profiles)
        + _interaction_kpi_labels(profile.get("metrics") or {})
        if axis["id"] == "task_execution"
        else []
    )
    wcag_count = len(wcag_findings) if axis["id"] == "trust_accessibility" else 0
    summary = f"{axis['short_name']} scores {int(round(score))}/100 in this GTM view. Structured evidence surfaced {len(failed) + len(performance_findings) + wcag_count} pain point(s) and {len(passed) + len(performance_strengths)} positive signal(s)."
    if vision_observation:
        summary += f" Vision review: {vision_observation}"
    if missing_context:
        summary += f" Missing context: {missing_context}"
    return {
        "id": axis["id"],
        "name": axis["short_name"],
        "shortName": axis["short_name"],
        "description": axis["description"],
        "coreQuestion": clean_text(axis.get("core_question")),
        "lookFor": list(axis.get("look_for") or []),
        "healthySignals": list(axis.get("healthy_signals") or []),
        "failureModes": list(axis.get("failure_modes") or []),
        "outOfScope": list(axis.get("out_of_scope") or []),
        "score": int(round(score)),
        "severity": score_to_severity(score),
        "confidence": round(clamp(mean([rows_scored["confidence"], 0.65 if heuristic > 0 else 0.25, safe_float(axis_review.get("confidence"), 0.0)], default=0.4), 0.25, 0.95), 2),
        "summary": summary,
        "businessImpact": AXIS_IMPACT[axis["id"]],
        "painPoints": pain_points,
        "strengths": strengths,
        "opportunities": dedupe_strings(([f"Resolve '{item['title']}' on the main commercial pages first." for item in pain_points[:2]] + [f"Raise this axis on homepage and primary conversion journeys before broader refinements."]), limit=3),
        "evidence": dedupe_strings([item["evidence"] for item in pain_points + strengths if clean_text(item.get("evidence"))] + performance_evidence + proof_points + profile["messaging"]["heroHeadings"][:2] + profile["messaging"]["heroCtas"][:2], limit=6),
        "signals": {
            "rowScore": round(rows_scored["score"], 1),
            "heuristicScore": round(heuristic, 1),
            "visionScore": round(vscore, 1) if vscore is not None else None,
            "rawScoreBeforeCaps": raw_score,
            "scoreCeiling": safe_float(ceiling.get("ceiling"), 100.0),
            "scoreCeilingReasons": ceiling.get("reasons") or [],
            "relevantChecks": len(rows),
            "performanceKpis": performance_profiles[:5] if axis["id"] == "task_execution" else [],
            "wcagFindings": wcag_findings[:8] if axis["id"] == "trust_accessibility" else [],
        },
        "visionObservation": vision_observation,
        "missingContext": missing_context,
        "proofPoints": proof_points,
    }


def _finding_signature(item: Dict[str, Any], *, include_page: bool = False) -> str:
    title = clean_text(item.get("title")).lower()
    page = clean_text(item.get("pageUrl") or item.get("pageName")).lower() if include_page else ""
    return "|".join(part for part in (title, page) if part)


def _evidence_signature(item: Dict[str, Any]) -> str:
    evidence = re.sub(r"\s+", " ", clean_text(item.get("evidence")).lower())
    return evidence[:220]


def diversify_axis_leads(axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep axis stories from reusing the same lead issue and screenshot when alternatives exist."""
    used_titles: set[str] = set()
    used_evidence: set[str] = set()

    diversified: List[Dict[str, Any]] = []
    for axis in axes:
        axis_copy = dict(axis)
        points = list(axis_copy.get("painPoints") or [])
        if not points:
            diversified.append(axis_copy)
            continue

        selected_index = 0
        for index, point in enumerate(points):
            title_sig = _finding_signature(point)
            evidence_sig = _evidence_signature(point)
            title_repeated = bool(title_sig and title_sig in used_titles)
            evidence_repeated = bool(evidence_sig and evidence_sig in used_evidence)
            if not title_repeated and not evidence_repeated:
                selected_index = index
                break

        if selected_index:
            lead = points.pop(selected_index)
            points.insert(0, lead)

        lead = points[0]
        title_sig = _finding_signature(lead)
        evidence_sig = _evidence_signature(lead)
        if title_sig:
            used_titles.add(title_sig)
        if evidence_sig:
            used_evidence.add(evidence_sig)

        axis_copy["painPoints"] = points[:4]
        axis_copy["opportunities"] = dedupe_strings(
            [f"Resolve '{item['title']}' on the main commercial pages first." for item in axis_copy["painPoints"][:2]]
            + ["Raise this axis on homepage and primary conversion journeys before broader refinements."],
            limit=3,
        )
        axis_copy["evidence"] = dedupe_strings(
            [item["evidence"] for item in (axis_copy.get("painPoints") or []) + (axis_copy.get("strengths") or []) if clean_text(item.get("evidence"))]
            + (axis_copy.get("proofPoints") or []),
            limit=6,
        )
        diversified.append(axis_copy)

    return diversified


def top_priorities(axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for axis in axes:
        for point in axis.get("painPoints") or []:
            items.append({**point, "axisId": axis["id"], "axisName": axis["shortName"], "axisScore": axis["score"]})
    rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(
        key=lambda item: (
            0 if item.get("responsiveFailure") else 1,
            rank.get(clean_text(item.get("severity")).lower(), 3),
            item.get("axisScore", 999),
            -safe_float(item.get("confidence"), 0.0),
        )
    )
    deduped = []
    seen = set()
    for item in items:
        key = (
            clean_text(item.get("title")).lower(),
            clean_text(item.get("pageUrl") or item.get("pageName")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 8:
            break
    return deduped


def build_recommendations(priorities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    recommendations = []
    seen = set()
    for item in priorities:
        title = clean_text(item.get("title"))
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        severity = clean_text(item.get("severity")).lower()
        priority = "Critical" if severity == "high" else "High" if severity == "medium" else "Medium"
        page_name = clean_text(item.get("pageName")) or "Core journey"
        axis_name = clean_text(item.get("axisName"))
        recommendations.append(
            {
                "priority": priority,
                "title": title,
                "description": _recommendation_description(item),
                "impact": _recommendation_impact(item, page_name),
                "axis": axis_name,
            }
        )
        if len(recommendations) >= 5:
            break
    return recommendations


def _recommendation_description(item: Dict[str, Any]) -> str:
    recommendation = clean_text(item.get("recommendation")) or "Address this issue on the most commercial flow first."
    evidence = clean_text(item.get("evidence"))
    page_name = clean_text(item.get("pageName")) or "the affected page"
    if item.get("responsiveFailure"):
        return (
            f"{recommendation} Start with {page_name}, then reuse the same breakpoint rules across category and promotion templates. "
            "Acceptance check: at 390px width there should be no clipped primary content, no desktop-width product rows, and navigation/actions should remain reachable without horizontal panning."
        )
    if evidence:
        return f"{recommendation} Use the captured evidence on {page_name} as the acceptance target: {evidence[:180]}"
    return recommendation


def _recommendation_impact(item: Dict[str, Any], page_name: str) -> str:
    axis_name = clean_text(item.get("axisName"))
    severity = clean_text(item.get("severity")).lower()
    if item.get("responsiveFailure"):
        return f"Mobile conversion risk on {page_name}: phone users see a broken layout before they can browse offers or navigate."
    severity_label = "major" if severity == "high" else "moderate" if severity == "medium" else "minor"
    return f"{severity_label.title()} {axis_name or 'UX'} risk on {page_name}; fix on this template before scaling to sibling pages."


def build_payload(website_menu: Dict[str, Any], cleaned_data: Dict[str, Any], rendered_data: Dict[str, Any], checks_data: Dict[str, Any], results_data: Optional[Dict[str, Any]], include_vision: bool) -> Dict[str, Any]:
    flat_rows = flatten_checks(checks_data)
    profile = build_profile(website_menu, cleaned_data, rendered_data, checks_data, results_data)
    focus_screenshots = select_focus_screenshots(cleaned_data)
    scanned_pages = select_scanned_pages(cleaned_data)
    vision_limit = max(1, safe_int(os.getenv("GTM_VISION_MAX_SCREENSHOTS"), 12))
    vision_screenshots = select_vision_screenshots(scanned_pages, focus_screenshots, limit=vision_limit)
    vision = {"enabled": False, "model": "", "used_images": 0, "error": "Vision review disabled for this run.", "result": None}
    if include_vision:
        vision = run_gtm_vision_review(
            site_context={
                "site": profile["site"],
                "hero_headings": profile["messaging"]["heroHeadings"][:3],
                "hero_ctas": profile["messaging"]["heroCtas"][:4],
                "navigation_labels": profile["messaging"]["navigationLabels"][:12],
                "page_titles": profile["messaging"]["pageTitles"][:10],
                "metrics": profile["metrics"],
                "sheet_scores": profile["sheetScores"],
                "visual_trust_review": {
                    "enabled": True,
                    "goal": "Detect GTM trust risks visible in screenshots, including AI-enhanced-looking team imagery, generic stock visuals, broken images, clipping, rendering artifacts, and credibility gaps.",
                    "preferred_axes": ["trust_accessibility", "ui_consistency", "content_microcopy"],
                },
            },
            screenshots=vision_screenshots,
        )
    vision_axes = ((vision.get("result") or {}).get("axes") or {}) if isinstance(vision, dict) else {}
    axes = diversify_axis_leads([build_axis(axis, flat_rows, profile, vision_axes) for axis in AXIS_DEFINITIONS])
    ai_findings = ai_discovered_findings(vision, vision_screenshots, AXIS_DEFINITIONS)
    attach_ai_findings_to_axes(axes, ai_findings)
    overall_score = int(round(mean([axis["score"] for axis in axes], default=0.0)))
    strongest = max(axes, key=lambda axis: axis["score"], default=None)
    weakest = min(axes, key=lambda axis: axis["score"], default=None)
    priorities = top_priorities(axes)
    position = clean_text(((vision.get("result") or {}).get("market_positioning") or ""))
    if not position and profile["messaging"]["heroHeadings"]:
        lead = profile["messaging"]["heroHeadings"][0]
        cta = clean_text((profile["messaging"]["heroCtas"] or [""])[0])
        position = f"Lead with '{lead}' and support it with a clearer commercial CTA like '{cta}'." if cta else f"Lead with '{lead}' as the commercial narrative anchor."
    summary = f"{profile['site']['display_name']} scores {overall_score}/100 on the first GTM-oriented UX/UI audit pass."
    if weakest:
        summary += f" The biggest commercial risk sits in {weakest['shortName'].lower()} ({weakest['score']}/100)."
    if strongest:
        summary += f" The strongest current signal is {strongest['shortName'].lower()} ({strongest['score']}/100)."
    context = {
        "siteType": "Website audit",
        "pagesAudited": profile["counts"]["pages"],
        "topLevelNavigation": profile["counts"]["topLevelNavigation"],
        "auditAxes": len(AXIS_DEFINITIONS),
        "approach": "Shared crawl and extraction pipeline, then a GTM synthesis that keeps only the highest-impact UX/UI pain points.",
    }
    methodology = [
        {
            "step": "Context",
            "description": "We isolate the homepage, core conversion pages, and the strongest commercial story signals before scoring.",
        },
        {
            "step": "Axis Review",
            "description": "The product is reviewed through the active GTM-oriented UX/UI axes with rule-based evidence from the detailed audit.",
        },
        {
            "step": "Prioritization",
            "description": "Only the highest-impact friction points are kept, then converted into sales-facing recommendations.",
        },
    ]
    return {
        "version": 1,
        "mode": "gtm",
        "generator": "src.gtm_audit.generate_gtm_audit",
        "site": profile["site"],
        "context": context,
        "methodology": methodology,
        "profile": profile,
        "focusScreenshots": focus_screenshots,
        "scannedPages": scanned_pages,
        "visionReview": vision,
        "aiDiscoveredFindings": ai_findings,
        "axes": axes,
        "executiveSummary": {"overallScore": overall_score, "strongestAxis": strongest, "weakestAxis": weakest, "summary": summary, "positioningHook": position, "topPriorities": priorities},
        "recommendations": build_recommendations(priorities),
        "artifacts": {
            "websiteMenu": str(website_menu.get("homepage") or ""),
            "cleanedPath": str(DEFAULT_CLEANED),
            "renderedPath": str(DEFAULT_RENDERED),
            "checksPath": str(DEFAULT_CHECKS),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the GTM-oriented UX/UI audit.")
    parser.add_argument("--website-menu", default=str(DEFAULT_WEBSITE_MENU))
    parser.add_argument("--cleaned", default=str(DEFAULT_CLEANED))
    parser.add_argument("--rendered", default=str(DEFAULT_RENDERED))
    parser.add_argument("--checks", default=str(DEFAULT_CHECKS))
    parser.add_argument("--results", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--skip-vision", action="store_true")
    args = parser.parse_args()

    website_menu_path = to_path(args.website_menu, DEFAULT_WEBSITE_MENU)
    cleaned_path = to_path(args.cleaned, DEFAULT_CLEANED)
    rendered_path = to_path(args.rendered, DEFAULT_RENDERED)
    checks_path = to_path(args.checks, DEFAULT_CHECKS)
    output_path = to_path(args.output, DEFAULT_OUTPUT)
    results_path = to_path(args.results, RESULTS_DIR) if clean_text(args.results) else None

    for required in (website_menu_path, cleaned_path, rendered_path, checks_path):
        if not required.exists():
            raise FileNotFoundError(f"Required input file not found: {required}")

    results_data = load_latest_results() if results_path is None else load_json(results_path)
    payload = build_payload(
        load_json(website_menu_path),
        load_json(cleaned_path),
        load_json(rendered_path),
        load_json(checks_path),
        results_data,
        include_vision=not args.skip_vision,
    )
    payload["artifacts"]["cleanedPath"] = str(cleaned_path)
    payload["artifacts"]["renderedPath"] = str(rendered_path)
    payload["artifacts"]["checksPath"] = str(checks_path)
    save_json(output_path, payload)
    print(f"GTM audit written to: {output_path}")


if __name__ == "__main__":
    main()
