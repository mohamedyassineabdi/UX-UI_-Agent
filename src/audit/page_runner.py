from src.audit.element_detector import detect_clickables
from src.audit.interaction_classifier import classify_clickables, summarize_classification
from src.audit.safe_interaction_tester import test_safe_clickables
from src.utils.file_utils import ensure_dir, join_path
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
        "error": None,
    }

    try:
        ensure_dir(page_folder_path)

        await page.goto(
            page_info["url"],
            wait_until=config["navigation"]["waitUntil"],
            timeout=config["navigation"]["timeoutMs"],
        )

        if config["navigation"]["postLoadDelayMs"] > 0:
            await page.wait_for_timeout(config["navigation"]["postLoadDelayMs"])

        result["finalUrl"] = page.url

        screenshot_path = join_path(page_folder_path, f"page.{config['screenshot']['type']}")
        await page.screenshot(
            path=screenshot_path,
            full_page=config["screenshot"]["fullPage"],
            type=config["screenshot"]["type"],
        )
        result["screenshotPath"] = screenshot_path

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
    finally:
        await page.close()

    return result
