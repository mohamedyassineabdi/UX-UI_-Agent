from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime
from typing import Any, Dict, List

from playwright.async_api import async_playwright

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.audit.page_runner import run_page_audit
from src.audit.person_a_postprocess import clean_person_a_output
from src.config.audit_config import AUDIT_CONFIG
from src.utils.file_utils import (
    build_timestamp_for_file_name,
    ensure_output_dirs,
    join_path,
    read_json_file,
    write_json_file,
)
from src.utils.url_utils import build_page_folder_name, deduplicate_pages


def clean_label(value: Any) -> str:
    return str(value or "").strip()


def normalize_flat_page(page: Dict[str, Any], index: int = 0) -> Dict[str, str]:
    if not isinstance(page, dict):
        raise ValueError(f"Item at index {index} is not a valid object.")

    url = page.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError(f'Item at index {index} is missing a valid "url".')

    name = page.get("name")
    normalized_name = name.strip() if isinstance(name, str) and name.strip() else f"Page_{index + 1}"

    raw_navigation_path = page.get("navigationPath")
    navigation_path = (
        [clean_label(segment) for segment in raw_navigation_path if clean_label(segment)]
        if isinstance(raw_navigation_path, list)
        else [normalized_name]
    )

    raw_folder_segments = page.get("folderSegments")
    folder_segments = (
        [clean_label(segment) for segment in raw_folder_segments if clean_label(segment)]
        if isinstance(raw_folder_segments, list)
        else [build_page_folder_name(normalized_name, f"page_{index + 1}")]
    )

    return {
        "name": normalized_name,
        "url": url.strip(),
        "siteUrl": clean_label(page.get("siteUrl")) or url.strip(),
        "navigationPath": navigation_path or [normalized_name],
        "folderSegments": folder_segments or [build_page_folder_name(normalized_name, f"page_{index + 1}")],
        "sourceType": clean_label(page.get("sourceType")) or "page",
    }


def get_navigation_roots(raw_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    navigation = raw_input.get("navigation")
    if isinstance(navigation, list):
        return [item for item in navigation if isinstance(item, dict)]

    navbars = raw_input.get("navbars")
    if isinstance(navbars, list):
        output: List[Dict[str, Any]] = []
        for navbar in navbars:
            urls = navbar.get("urls") if isinstance(navbar, dict) else None
            if isinstance(urls, list):
                output.extend(item for item in urls if isinstance(item, dict))
        return output

    desktop_navbars = raw_input.get("desktop_navbars")
    if isinstance(desktop_navbars, list):
        output = []
        for navbar in desktop_navbars:
            urls = navbar.get("urls") if isinstance(navbar, dict) else None
            if isinstance(urls, list):
                output.extend(item for item in urls if isinstance(item, dict))
        return output

    return []


def get_node_children(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []

    raw_children = node.get("children")
    if isinstance(raw_children, list):
        children.extend(child for child in raw_children if isinstance(child, dict))

    sections = node.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue

            section_urls = []
            for entry in section.get("urls", []) or []:
                if not isinstance(entry, dict):
                    continue
                section_urls.append(
                    {
                        "name": entry.get("name"),
                        "url": entry.get("url"),
                        "type": entry.get("type") or "link",
                        "children": entry.get("children") or [],
                    }
                )

            children.append(
                {
                    "name": section.get("title") or section.get("name") or "General",
                    "type": "section",
                    "children": section_urls,
                }
            )

    submenus = node.get("submenus")
    if isinstance(submenus, list):
        for submenu in submenus:
            if isinstance(submenu, dict):
                children.append(
                    {
                        "name": submenu.get("name"),
                        "url": submenu.get("url"),
                        "type": submenu.get("type") or "link",
                        "children": submenu.get("children") or [],
                    }
                )

    return children


def collect_pages_from_navigation_node(
    node: Dict[str, Any],
    pages: List[Dict[str, Any]],
    parent_navigation_path: List[str],
    parent_folder_segments: List[str],
) -> None:
    name = clean_label(node.get("name"))
    node_type = clean_label(node.get("type")).lower()
    url = clean_label(node.get("url"))

    include_as_segment = bool(name) and not (node_type == "section" and name.lower() == "general")

    navigation_path = parent_navigation_path + ([name] if include_as_segment else [])
    folder_segments = parent_folder_segments + (
        [build_page_folder_name(name)] if include_as_segment else []
    )

    if url:
        page_name = name or f"Page_{len(pages) + 1}"
        pages.append(
            {
                "name": page_name,
                "url": url,
                "navigationPath": navigation_path or [page_name],
                "folderSegments": folder_segments or [build_page_folder_name(page_name)],
                "sourceType": node_type or "link",
            }
        )

    for child in get_node_children(node):
        collect_pages_from_navigation_node(
            child,
            pages,
            navigation_path,
            folder_segments,
        )


def extract_navigation_pages(raw_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []

    for root in get_navigation_roots(raw_input):
        collect_pages_from_navigation_node(root, pages, [], [])

    return pages


def extract_pages_from_partner_json(raw_input: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, str]]:
    pages: List[Dict[str, str]] = []
    input_parsing = config["inputParsing"]
    site_url = clean_label(raw_input.get("homepage"))

    homepage = raw_input.get("homepage")
    if input_parsing["includeHomepage"] and isinstance(homepage, str):
        pages.append(
            {
                "name": "Home",
                "url": homepage,
                "siteUrl": site_url or homepage,
                "navigationPath": ["Home"],
                "folderSegments": [build_page_folder_name("Home")],
                "sourceType": "homepage",
            }
        )

    auth = raw_input.get("auth")
    if input_parsing["includeAuthPages"] and isinstance(auth, dict):
        signin = auth.get("signin") if isinstance(auth.get("signin"), dict) else None
        signup = auth.get("signup") if isinstance(auth.get("signup"), dict) else None

        if signin and signin.get("url"):
            signin_name = signin.get("name") or "Sign In"
            pages.append(
                {
                    "name": signin_name,
                    "url": signin["url"],
                    "siteUrl": site_url or signin["url"],
                    "navigationPath": [signin_name],
                    "folderSegments": [build_page_folder_name(signin_name)],
                    "sourceType": "auth",
                }
            )

        if signup and signup.get("url"):
            signup_name = signup.get("name") or "Sign Up"
            pages.append(
                {
                    "name": signup_name,
                    "url": signup["url"],
                    "siteUrl": site_url or signup["url"],
                    "navigationPath": [signup_name],
                    "folderSegments": [build_page_folder_name(signup_name)],
                    "sourceType": "auth",
                }
            )

    for page in extract_navigation_pages(raw_input):
        page["siteUrl"] = site_url or page["url"]
        pages.append(page)

    return [normalize_flat_page(page, index) for index, page in enumerate(pages)]


def parse_input_to_pages(raw_input: Any, config: Dict[str, Any]) -> List[Dict[str, str]]:
    if isinstance(raw_input, list):
        return [normalize_flat_page(page, index) for index, page in enumerate(raw_input)]

    if isinstance(raw_input, dict):
        crawler_error = clean_label(raw_input.get("error"))
        if crawler_error:
            raise ValueError(f"Crawler output reported an error: {crawler_error}")
        return extract_pages_from_partner_json(raw_input, config)

    raise ValueError("Input JSON must be either an array of pages or the partner navigation object.")


def summarize_run(page_results: List[Dict[str, Any]]) -> Dict[str, int]:
    aggregate = {
        "totalClickablesDetected": 0,
        "safeClickables": 0,
        "forbiddenClickables": 0,
        "unknownClickables": 0,
        "safeCandidates": 0,
        "testedInteractions": 0,
        "skippedSafeInteractions": 0,
        "successfulInteractions": 0,
        "failedInteractions": 0,
        "navigationInteractions": 0,
        "domChangeInteractions": 0,
        "popupInteractions": 0,
        "dialogInteractions": 0,
        "noEffectInteractions": 0,
        "errorInteractions": 0,
        "notFoundInteractions": 0,
        "interactionScreenshotsCreated": 0,
    }

    for page_result in page_results:
        clickable_summary = page_result.get("clickableSummary") or {}
        interaction_summary = page_result.get("interactionSummary") or {}

        aggregate["totalClickablesDetected"] += clickable_summary.get("totalDetected", 0)
        aggregate["safeClickables"] += clickable_summary.get("safe", 0)
        aggregate["forbiddenClickables"] += clickable_summary.get("forbidden", 0)
        aggregate["unknownClickables"] += clickable_summary.get("unknown", 0)
        aggregate["safeCandidates"] += interaction_summary.get("safeCandidates", 0)
        aggregate["testedInteractions"] += interaction_summary.get("tested", 0)
        aggregate["skippedSafeInteractions"] += interaction_summary.get("skippedSafe", 0)
        aggregate["successfulInteractions"] += interaction_summary.get("successful", 0)
        aggregate["failedInteractions"] += interaction_summary.get("failed", 0)
        aggregate["navigationInteractions"] += interaction_summary.get("navigations", 0)
        aggregate["domChangeInteractions"] += interaction_summary.get("domChanges", 0)
        aggregate["popupInteractions"] += interaction_summary.get("popups", 0)
        aggregate["dialogInteractions"] += interaction_summary.get("dialogs", 0)
        aggregate["noEffectInteractions"] += interaction_summary.get("noEffects", 0)
        aggregate["errorInteractions"] += interaction_summary.get("errors", 0)
        aggregate["notFoundInteractions"] += interaction_summary.get("notFound", 0)
        aggregate["interactionScreenshotsCreated"] += interaction_summary.get("interactionScreenshotsCreated", 0)

    return aggregate


def build_person_a_output(page_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    pages: List[Dict[str, Any]] = []

    for page_result in page_results:
        person_a = page_result.get("personA")
        if not person_a:
            continue

        page_meta_data = ((person_a.get("pageMeta") or {}).get("data") or {})

        pages.append(
            {
                "pageId": page_meta_data.get("pageId"),
                "name": page_result.get("name"),
                "url": page_result.get("originalUrl"),
                "finalUrl": page_result.get("finalUrl"),
                "status": page_result.get("status"),
                "pageMeta": person_a.get("pageMeta"),
                "titlesAndHeadings": person_a.get("titlesAndHeadings"),
                "navigation": person_a.get("navigation"),
                "textContent": person_a.get("textContent"),
                "forms": person_a.get("forms"),
                "media": person_a.get("media"),
            }
        )

    return {
        "source": "person_a",
        "generatedFrom": "src.main",
        "totalPages": len(pages),
        "pages": pages,
    }


async def run_with_concurrency(items, worker, concurrency: int):
    results: List[Any] = [None] * len(items)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, len(items)) if items else 1))

    async def runner(index, item):
        async with semaphore:
            results[index] = await worker(item, index)

    await asyncio.gather(*(runner(index, item) for index, item in enumerate(items)))
    return results


def get_browser_launcher(playwright, browser_type: str):
    if browser_type == "firefox":
        return playwright.firefox
    if browser_type == "webkit":
        return playwright.webkit
    return playwright.chromium


async def launch_browser(playwright, config: Dict[str, Any]):
    browser_launcher = get_browser_launcher(playwright, config["browser"]["browserType"])
    launch_options: Dict[str, Any] = {
        "headless": config["browser"]["headless"],
        "slow_mo": config["browser"].get("slowMoMs", 0),
    }

    channel = config["browser"].get("channel")
    if channel and config["browser"]["browserType"] == "chromium":
        launch_options["channel"] = channel

    try:
        return await browser_launcher.launch(**launch_options)
    except Exception as error:
        if "channel" not in launch_options:
            raise

        fallback_options = {key: value for key, value in launch_options.items() if key != "channel"}
        print(f"Chrome channel launch failed ({error}). Falling back to Playwright Chromium.")
        return await browser_launcher.launch(**fallback_options)


async def async_main():
    started_at = datetime.now()

    print("Starting audit...")
    print(f"Reading input file: {AUDIT_CONFIG['paths']['inputFile']}")

    ensure_output_dirs(AUDIT_CONFIG["paths"])

    raw_input = read_json_file(AUDIT_CONFIG["paths"]["inputFile"])
    pages_parsed = parse_input_to_pages(raw_input, AUDIT_CONFIG)
    deduped = deduplicate_pages(pages_parsed, AUDIT_CONFIG["urlNormalization"])
    unique_pages = deduped["uniquePages"]
    duplicates = deduped["duplicates"]

    print(f"Total pages extracted from input: {len(pages_parsed)}")
    print(f"Unique pages to visit: {len(unique_pages)}")
    print(f"Duplicates skipped: {len(duplicates)}")

    if not unique_pages:
        raise ValueError(
            "No visitable pages were extracted from the input JSON. "
            "The crawler likely failed or did not return usable navigation links."
        )

    print(
        "Browser mode: "
        f"{'visible' if not AUDIT_CONFIG['browser']['headless'] else 'headless'} "
        f"{AUDIT_CONFIG['browser'].get('channel') or AUDIT_CONFIG['browser']['browserType']}"
    )
    print(f"Page concurrency: {AUDIT_CONFIG['execution']['pageConcurrency']}")

    async with async_playwright() as playwright:
        browser = None
        context = None

        browser = await launch_browser(playwright, AUDIT_CONFIG)
        context = await browser.new_context(
            viewport=AUDIT_CONFIG["browser"]["viewport"],
            ignore_https_errors=AUDIT_CONFIG["browser"].get("ignoreHttpsErrors", False),
        )

        try:
            progress = {"completed": 0}

            async def worker(page_info, index):
                print(f"[START {index + 1}/{len(unique_pages)}] {page_info['name']} -> {page_info['url']}")
                try:
                    result = await run_page_audit(
                        context=context,
                        page_info=page_info,
                        page_index=index,
                        config=AUDIT_CONFIG,
                    )

                    if result["status"] == "success":
                        print(f"  Success -> screenshot saved: {result['screenshotPath']}")
                        clickable_summary = result["clickableSummary"]
                        interaction_summary = result["interactionSummary"]
                        print(
                            "  Clickables -> total: "
                            f"{clickable_summary['totalDetected']}, safe: {clickable_summary['safe']}, "
                            f"forbidden: {clickable_summary['forbidden']}, unknown: {clickable_summary['unknown']}"
                        )
                        print(
                            "  Interactions -> tested: "
                            f"{interaction_summary['tested']}, success: {interaction_summary['successful']}, "
                            f"screenshots: {interaction_summary['interactionScreenshotsCreated']}"
                        )
                    else:
                        print(f"  Failed -> {result['error']}")

                    return result
                finally:
                    progress["completed"] += 1
                    print(f"[DONE  {progress['completed']}/{len(unique_pages)}] {page_info['url']}")

            page_results = await run_with_concurrency(
                unique_pages,
                worker,
                AUDIT_CONFIG["execution"]["pageConcurrency"],
            )
        finally:
            if context:
                await context.close()
            if browser:
                await browser.close()

    finished_at = datetime.now()
    timestamp = build_timestamp_for_file_name(finished_at)
    run_summary = summarize_run(page_results)

    summary = {
        "runStartedAt": started_at.isoformat(),
        "runFinishedAt": finished_at.isoformat(),
        "inputFile": AUDIT_CONFIG["paths"]["inputFile"],
        "browserType": AUDIT_CONFIG["browser"]["browserType"],
        "browserChannel": AUDIT_CONFIG["browser"].get("channel"),
        "headless": AUDIT_CONFIG["browser"]["headless"],
        "slowMoMs": AUDIT_CONFIG["browser"].get("slowMoMs", 0),
        "pageConcurrency": AUDIT_CONFIG["execution"]["pageConcurrency"],
        "totalPagesExtractedFromInput": len(pages_parsed),
        "uniquePagesVisited": len(unique_pages),
        "duplicatePagesSkipped": len(duplicates),
        "pagesSucceeded": len([result for result in page_results if result["status"] == "success"]),
        "pagesFailed": len([result for result in page_results if result["status"] == "failed"]),
        "totalClickablesDetected": run_summary["totalClickablesDetected"],
        "safeClickables": run_summary["safeClickables"],
        "forbiddenClickables": run_summary["forbiddenClickables"],
        "unknownClickables": run_summary["unknownClickables"],
        "safeCandidates": run_summary["safeCandidates"],
        "testedInteractions": run_summary["testedInteractions"],
        "skippedSafeInteractions": run_summary["skippedSafeInteractions"],
        "successfulInteractions": run_summary["successfulInteractions"],
        "failedInteractions": run_summary["failedInteractions"],
        "navigationInteractions": run_summary["navigationInteractions"],
        "domChangeInteractions": run_summary["domChangeInteractions"],
        "popupInteractions": run_summary["popupInteractions"],
        "dialogInteractions": run_summary["dialogInteractions"],
        "noEffectInteractions": run_summary["noEffectInteractions"],
        "errorInteractions": run_summary["errorInteractions"],
        "notFoundInteractions": run_summary["notFoundInteractions"],
        "interactionScreenshotsCreated": run_summary["interactionScreenshotsCreated"],
    }

    output = {
        "summary": summary,
        "duplicatesSkipped": duplicates,
        "pages": page_results,
    }

    results_file_path = join_path(
        AUDIT_CONFIG["paths"]["resultsDir"],
        f"audit-results_{timestamp}.json",
    )
    write_json_file(results_file_path, output)

    person_a_output = build_person_a_output(page_results)
    person_a_file_path = join_path("shared", "generated", "person_a_extraction.json")
    write_json_file(person_a_file_path, person_a_output)

    person_a_cleaned_output = clean_person_a_output(person_a_output)
    person_a_cleaned_file_path = join_path("shared", "generated", "person_a_cleaned.json")
    write_json_file(person_a_cleaned_file_path, person_a_cleaned_output)

    print(f"Results written to: {results_file_path}")
    print(f"Person A extraction written to: {person_a_file_path}")
    print(f"Person A cleaned output written to: {person_a_cleaned_file_path}")
    print("Audit completed.")


def main():
    try:
        asyncio.run(async_main())
    except Exception as error:
        print("Fatal error while running audit:", file=sys.stderr)
        print(error, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()