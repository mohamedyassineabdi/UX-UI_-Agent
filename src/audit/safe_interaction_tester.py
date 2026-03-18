import asyncio
from urllib.parse import urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.audit.element_detector import detect_clickables
from src.utils.file_utils import ensure_dir, join_path
from src.utils.url_utils import (
    build_page_folder_name,
    build_website_folder_name,
    get_origin_safe,
    safe_normalize_url,
    slugify,
)


def clean_text(value):
    return " ".join(str(value or "").split()).strip()


def build_fingerprint(clickable):
    return "||".join(
        [
            clickable.get("tag", ""),
            clean_text(clickable.get("text", "")),
            clean_text(clickable.get("href", "")),
            clean_text(clickable.get("ariaLabel", "")),
            clean_text(clickable.get("role", "")),
            clean_text(clickable.get("id", "")),
            clean_text(clickable.get("className", "")),
            clean_text(clickable.get("title", "")),
            clean_text(clickable.get("name", "")),
            clean_text(clickable.get("type", "")),
            clean_text(clickable.get("value", "")),
            clean_text(clickable.get("xpathHint", "")),
        ]
    )


def find_matching_clickable_index(target_clickable, fresh_clickables):
    target_fingerprint = build_fingerprint(target_clickable)

    for index, clickable in enumerate(fresh_clickables):
        if build_fingerprint(clickable) == target_fingerprint:
            return index

    for index, clickable in enumerate(fresh_clickables):
        if (
            clickable.get("tag") == target_clickable.get("tag")
            and clean_text(clickable.get("text")) == clean_text(target_clickable.get("text"))
            and clean_text(clickable.get("href")) == clean_text(target_clickable.get("href"))
        ):
            return index

    return -1


def should_skip_safe_clickable(clickable, page_url, config):
    if config["interactionTesting"]["onlyVisible"] and not clickable.get("visible"):
        return "not visible"

    if clickable.get("disabled"):
        return "disabled"

    if clickable.get("tag") == "a" and clickable.get("href"):
        href = clickable["href"].strip()

        if not config["interactionTesting"]["testSamePageAnchors"] and href.startswith("#"):
            return "same-page hash anchor skipped"

        try:
            target_url = urljoin(page_url, href)
            page_origin = get_origin_safe(page_url)
            target_origin = get_origin_safe(target_url)

            if (
                config["interactionTesting"]["skipExternalOrigins"]
                and page_origin
                and target_origin
                and page_origin != target_origin
            ):
                return "external origin skipped"
        except Exception:
            return "invalid href"

    return None


def should_capture_interaction_screenshot(outcome_type, config):
    if config["interactionTesting"].get("captureAllInteractionScreenshots"):
        return True

    if not config["interactionTesting"]["captureSuccessfulInteractionScreenshots"]:
        return False

    return outcome_type in {"navigation", "dom_change", "popup", "dialog"}


async def wait_shortly_after_action(page, delay_ms):
    if delay_ms > 0:
        await page.wait_for_timeout(delay_ms)


async def resolve_popup_task(popup_task):
    if not popup_task:
        return None

    if popup_task.done():
        try:
            return popup_task.result()
        except PlaywrightTimeoutError:
            return None
        except asyncio.CancelledError:
            return None
    else:
        popup_task.cancel()
        try:
            await popup_task
        except (asyncio.CancelledError, PlaywrightTimeoutError):
            pass

    return None


async def capture_page_state(page):
    return await page.evaluate(
        """
        () => {
          const body = document.body;
          const text = body ? body.innerText || '' : '';

          return {
            title: document.title || '',
            textLength: text.trim().length,
            bodyLength: body ? (body.innerHTML || '').length : 0
          };
        }
        """
    )


def attach_dialog_tracker(page):
    dialog_info = {"value": None}

    async def dialog_handler(dialog):
        dialog_info["value"] = {
            "type": dialog.type,
            "message": dialog.message,
        }

        try:
            await dialog.dismiss()
        except Exception:
            pass

    page.once("dialog", dialog_handler)

    return {
        "get_dialog_info": lambda: dialog_info["value"],
        "handler": dialog_handler,
    }


async def save_interaction_screenshot(*, page, page_info, clickable, outcome_type, interaction_order, config):
    site_url = page_info.get("siteUrl") or page_info["url"]
    website_folder_name = build_website_folder_name(site_url)
    folder_segments = page_info.get("folderSegments") or [
        build_page_folder_name(page_info["name"], "page")
    ]
    interactions_folder_path = join_path(
        config["paths"]["screenshotDir"],
        website_folder_name,
        *folder_segments,
        "interactions",
    )

    ensure_dir(interactions_folder_path)

    safe_element_name = (
        slugify(
            clickable.get("text")
            or clickable.get("ariaLabel")
            or clickable.get("title")
            or clickable.get("name")
            or clickable.get("tag")
            or "element"
        )
        or "element"
    )

    file_name = "_".join(
        [
            str(interaction_order).zfill(3),
            safe_element_name,
            outcome_type,
        ]
    ) + f".{config['screenshot']['type']}"

    screenshot_path = join_path(interactions_folder_path, file_name)
    await page.screenshot(
        path=screenshot_path,
        full_page=True,
        type=config["screenshot"]["type"],
    )

    return screenshot_path


async def maybe_capture_interaction_screenshot(
    *,
    page,
    page_info,
    clickable,
    outcome_type,
    interaction_order,
    config,
):
    if not page or not should_capture_interaction_screenshot(outcome_type, config):
        return None

    try:
        return await save_interaction_screenshot(
            page=page,
            page_info=page_info,
            clickable=clickable,
            outcome_type=outcome_type,
            interaction_order=interaction_order,
            config=config,
        )
    except Exception:
        return None


def build_skipped_result(clickable, page_url, reason):
    return {
        "clickableIndex": clickable["index"],
        "clickableText": clickable.get("text"),
        "clickableTag": clickable.get("tag"),
        "classification": clickable.get("classification"),
        "tested": False,
        "outcomeType": "skipped",
        "success": False,
        "reason": reason,
        "beforeUrl": page_url,
        "afterUrl": None,
        "normalizedAfterUrl": None,
        "openedNewTab": False,
        "dialog": None,
        "domChanged": False,
        "screenshotPath": None,
        "error": None,
    }


async def test_safe_clickables(*, context, page_info, classified_clickables, config):
    if not config["interactionTesting"]["enabled"]:
        return {
            "testedCount": 0,
            "skippedSafeCount": 0,
            "interactionScreenshotsCreated": 0,
            "safeInteractionResults": [],
        }

    safe_clickables = [item for item in classified_clickables if item.get("classification") == "safe"]
    max_safe_interactions = config["interactionTesting"].get("maxSafeInteractionsPerPage")
    if isinstance(max_safe_interactions, int) and max_safe_interactions > 0:
        safe_clickables = safe_clickables[:max_safe_interactions]

    results = []
    skipped_safe_count = 0
    interaction_screenshots_created = 0

    test_page = await context.new_page()

    try:
        for clickable in safe_clickables:
            skip_reason = should_skip_safe_clickable(clickable, page_info["url"], config)

            if skip_reason:
                skipped_safe_count += 1
                results.append(build_skipped_result(clickable, page_info["url"], skip_reason))
                continue

            interaction_result = {
                "clickableIndex": clickable["index"],
                "clickableText": clickable.get("text"),
                "clickableTag": clickable.get("tag"),
                "classification": clickable.get("classification"),
                "tested": True,
                "outcomeType": "unknown",
                "success": False,
                "reason": None,
                "beforeUrl": page_info["url"],
                "afterUrl": None,
                "normalizedAfterUrl": None,
                "openedNewTab": False,
                "dialog": None,
                "domChanged": False,
                "screenshotPath": None,
                "error": None,
            }

            popup_page = None
            popup_task = None
            dialog_tracker = None

            try:
                await test_page.goto(
                    page_info["url"],
                    wait_until=config["navigation"]["waitUntil"],
                    timeout=config["navigation"]["timeoutMs"],
                )

                if config["navigation"]["postLoadDelayMs"] > 0:
                    await test_page.wait_for_timeout(config["navigation"]["postLoadDelayMs"])

                before_url = test_page.url
                before_state = await capture_page_state(test_page)
                dialog_tracker = attach_dialog_tracker(test_page)

                fresh_clickables = await detect_clickables(test_page, config)
                target_index = find_matching_clickable_index(clickable, fresh_clickables)

                if target_index == -1:
                    interaction_result["outcomeType"] = "not_found"
                    interaction_result["reason"] = "matching clickable not found on fresh page load"
                    interaction_result["screenshotPath"] = await maybe_capture_interaction_screenshot(
                        page=test_page,
                        page_info=page_info,
                        clickable=clickable,
                        outcome_type=interaction_result["outcomeType"],
                        interaction_order=clickable["index"],
                        config=config,
                    )
                    if interaction_result["screenshotPath"]:
                        interaction_screenshots_created += 1
                    results.append(interaction_result)
                    continue

                selector = ", ".join(config["clickableDetection"]["selectors"])
                locator = test_page.locator(selector).nth(target_index)

                await locator.scroll_into_view_if_needed()

                popup_task = asyncio.create_task(
                    test_page.wait_for_event(
                        "popup",
                        timeout=config["interactionTesting"]["actionTimeoutMs"],
                    )
                )

                await locator.click(timeout=config["interactionTesting"]["actionTimeoutMs"])

                try:
                    popup_page = await resolve_popup_task(popup_task)
                finally:
                    popup_task = None

                if popup_page:
                    try:
                        await popup_page.wait_for_load_state(
                            "domcontentloaded",
                            timeout=config["interactionTesting"]["actionTimeoutMs"],
                        )
                    except Exception:
                        pass

                await wait_shortly_after_action(test_page, config["interactionTesting"]["postClickDelayMs"])

                after_url = test_page.url
                after_state = await capture_page_state(test_page)
                normalized_after_url = safe_normalize_url(after_url, config["urlNormalization"])
                normalized_before_url = safe_normalize_url(before_url, config["urlNormalization"])

                interaction_result["afterUrl"] = after_url
                interaction_result["normalizedAfterUrl"] = normalized_after_url
                interaction_result["dialog"] = dialog_tracker["get_dialog_info"]() if dialog_tracker else None
                interaction_result["openedNewTab"] = bool(popup_page)
                interaction_result["domChanged"] = (
                    before_state["title"] != after_state["title"]
                    or before_state["textLength"] != after_state["textLength"]
                    or before_state["bodyLength"] != after_state["bodyLength"]
                )

                if popup_page:
                    interaction_result["outcomeType"] = "popup"
                    interaction_result["success"] = True
                    interaction_result["reason"] = "interaction opened a new tab or window"
                elif normalized_before_url and normalized_after_url and normalized_before_url != normalized_after_url:
                    interaction_result["outcomeType"] = "navigation"
                    interaction_result["success"] = True
                    interaction_result["reason"] = "interaction changed the page URL"
                elif interaction_result["domChanged"]:
                    interaction_result["outcomeType"] = "dom_change"
                    interaction_result["success"] = True
                    interaction_result["reason"] = "interaction changed page DOM without URL change"
                elif interaction_result["dialog"]:
                    interaction_result["outcomeType"] = "dialog"
                    interaction_result["success"] = True
                    interaction_result["reason"] = "interaction opened a dialog"
                else:
                    interaction_result["outcomeType"] = "no_effect"
                    interaction_result["success"] = False
                    interaction_result["reason"] = "no visible navigation or DOM change detected"

                screenshot_source_page = popup_page or test_page
                interaction_result["screenshotPath"] = await maybe_capture_interaction_screenshot(
                    page=screenshot_source_page,
                    page_info=page_info,
                    clickable=clickable,
                    outcome_type=interaction_result["outcomeType"],
                    interaction_order=clickable["index"],
                    config=config,
                )
                if interaction_result["screenshotPath"]:
                    interaction_screenshots_created += 1
            except Exception as error:
                interaction_result["outcomeType"] = "error"
                interaction_result["success"] = False
                interaction_result["error"] = str(error)
                interaction_result["reason"] = "interaction threw an error"
                screenshot_source_page = popup_page or test_page
                interaction_result["screenshotPath"] = await maybe_capture_interaction_screenshot(
                    page=screenshot_source_page,
                    page_info=page_info,
                    clickable=clickable,
                    outcome_type=interaction_result["outcomeType"],
                    interaction_order=clickable["index"],
                    config=config,
                )
                if interaction_result["screenshotPath"]:
                    interaction_screenshots_created += 1
            finally:
                if dialog_tracker:
                    try:
                        test_page.remove_listener("dialog", dialog_tracker["handler"])
                    except Exception:
                        pass

                if popup_task:
                    try:
                        await resolve_popup_task(popup_task)
                    except Exception:
                        pass

                if popup_page:
                    try:
                        await popup_page.close()
                    except Exception:
                        pass

            results.append(interaction_result)
    finally:
        try:
            await test_page.close()
        except Exception:
            pass

    return {
        "testedCount": len([result for result in results if result["tested"] and result["outcomeType"] != "skipped"]),
        "skippedSafeCount": skipped_safe_count,
        "interactionScreenshotsCreated": interaction_screenshots_created,
        "safeInteractionResults": results,
    }
