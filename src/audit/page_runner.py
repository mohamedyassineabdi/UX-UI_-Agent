from src.audit.element_detector import detect_clickables
from src.audit.interaction_classifier import classify_clickables, summarize_classification
from src.audit.page_visit_helpers import (
    collect_network_log,
    dismiss_cookie_banners,
    extract_basic_page_info,
    save_dom_snapshot,
    smart_scroll,
)
from src.audit.safe_interaction_tester import test_safe_clickables
from src.utils.file_utils import ensure_dir, join_path, write_json_file
from src.utils.url_utils import build_page_folder_name, build_website_folder_name


async def run_page_audit(*, context, page_info, page_index, config):
    page = await context.new_page()

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
        "networkLogPath": None,
        "pageMetadataPath": None,
        "domSnapshotPath": None,
        "error": None,
    }

    try:
        ensure_dir(page_folder_path)
        scroll_screenshots_dir = join_path(page_folder_path, "scrolls")
        ensure_dir(scroll_screenshots_dir)

        network_log = []
        collect_network_log(page, network_log)

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

        if config.get("pageCapture", {}).get("captureScrollScreenshots"):
            result["scrollScreenshotPaths"] = await smart_scroll(
                page=page,
                screenshots_dir=scroll_screenshots_dir,
                page_label=page_info["name"],
                screenshot_type=config["screenshot"]["type"],
                max_rounds=config.get("pageCapture", {}).get("scrollMaxRounds", 4),
            )

        result["pageMetadata"] = await extract_basic_page_info(page, page_info["url"])
        result["finalUrl"] = result["pageMetadata"]["finalUrl"] or result["finalUrl"]

        if config.get("pageCapture", {}).get("saveDomSnapshot"):
            dom_snapshot_path = join_path(page_folder_path, "dom_snapshot.html")
            await save_dom_snapshot(page, dom_snapshot_path)
            result["domSnapshotPath"] = dom_snapshot_path

        screenshot_path = join_path(page_folder_path, f"page.{config['screenshot']['type']}")
        await page.screenshot(
            path=screenshot_path,
            full_page=config["screenshot"]["fullPage"],
            type=config["screenshot"]["type"],
        )
        result["screenshotPath"] = screenshot_path

        page_metadata_path = join_path(page_folder_path, "page_metadata.json")
        result["pageMetadataPath"] = page_metadata_path
        write_json_file(
            page_metadata_path,
            {
                **(result["pageMetadata"] or {}),
                "cookieActions": result["cookieActions"],
                "scrollScreenshotPaths": result["scrollScreenshotPaths"],
                "pageScreenshotPath": result["screenshotPath"],
            },
        )

        if config.get("pageCapture", {}).get("saveNetworkLog"):
            network_log_path = join_path(page_folder_path, "network_log.json")
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
        write_json_file(
            join_path(page_folder_path, "page_error.json"),
            {
                "name": page_info["name"],
                "url": page_info["url"],
                "error": str(error),
            },
        )
    finally:
        await page.close()

    return result
