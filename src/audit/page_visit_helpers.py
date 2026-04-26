import re
from typing import Any, Dict, List

from src.utils.file_utils import join_path, write_text_file
from src.utils.url_utils import slugify


COOKIE_BUTTON_TEXTS = [
    "accept",
    "accept all",
    "agree",
    "allow all",
    "got it",
    "continue",
    "consent",
    "i agree",
    "okay",
    "ok",
]


def build_scroll_shot_name(page_label: str, stage: str, screenshot_type: str) -> str:
    safe_label = slugify(page_label) or "page"
    return f"{safe_label}_{stage}.{screenshot_type}"


async def dismiss_cookie_banners(page) -> List[str]:
    clicked: List[str] = []

    for text in COOKIE_BUTTON_TEXTS:
        candidates = [
            page.get_by_role("button", name=re.compile(rf"^{re.escape(text)}$", re.I)),
            page.get_by_text(re.compile(rf"^{re.escape(text)}$", re.I)),
        ]
        for locator in candidates:
            try:
                count = await locator.count()
                for index in range(min(count, 2)):
                    item = locator.nth(index)
                    if await item.is_visible():
                        await item.click(timeout=1500)
                        clicked.append(text)
                        await page.wait_for_timeout(150)
                        return clicked
            except Exception:
                continue

    return clicked


async def wait_for_page_ready(page, config) -> None:
    readiness = config.get("pageReadiness", {})
    network_idle_timeout = readiness.get("networkIdleTimeoutMs", 0)
    asset_timeout = readiness.get("assetTimeoutMs", 0)
    settle_delay_ms = readiness.get("settleDelayMs", 0)

    if network_idle_timeout and network_idle_timeout > 0:
        try:
            await page.wait_for_load_state("networkidle", timeout=network_idle_timeout)
        except Exception:
            pass

    try:
        await page.evaluate(
            """
            async () => {
              if (document.fonts && document.fonts.ready) {
                try {
                  await document.fonts.ready;
                } catch (error) {
                  /* ignore */
                }
              }
            }
            """
        )
    except Exception:
        pass

    if asset_timeout and asset_timeout > 0:
        try:
            await page.wait_for_function(
                """
                () => {
                  const images = Array.from(document.images || []);
                  return images.every((image) => {
                    if (image.loading === 'lazy' && !image.currentSrc) {
                      return true;
                    }

                    return image.complete;
                  });
                }
                """,
                timeout=asset_timeout,
            )
        except Exception:
            pass

    if settle_delay_ms and settle_delay_ms > 0:
        try:
            await page.wait_for_timeout(settle_delay_ms)
        except Exception:
            pass


async def smart_scroll(*, page, screenshots_dir: str, page_label: str, screenshot_type: str, max_rounds: int = 4) -> List[str]:
    shots: List[str] = []
    last_height = -1

    initial_path = join_path(
        screenshots_dir,
        build_scroll_shot_name(page_label, "initial", screenshot_type),
    )
    try:
        await page.screenshot(path=initial_path, full_page=False, type=screenshot_type)
        shots.append(initial_path)
    except Exception:
        pass

    for _ in range(max_rounds):
        try:
            current_height = await page.evaluate("() => document.body ? document.body.scrollHeight : 0")
            viewport_height = await page.evaluate("() => window.innerHeight || 0")
            await page.mouse.wheel(0, int(max(viewport_height * 0.9, 500)))
            await page.wait_for_timeout(150)
            new_height = await page.evaluate("() => document.body ? document.body.scrollHeight : 0")

            if new_height == current_height == last_height:
                break
            last_height = new_height
        except Exception:
            break

    bottom_path = join_path(
        screenshots_dir,
        build_scroll_shot_name(page_label, "bottom", screenshot_type),
    )
    try:
        await page.evaluate("() => window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
        await page.wait_for_timeout(200)
        await page.screenshot(path=bottom_path, full_page=False, type=screenshot_type)
        shots.append(bottom_path)
    except Exception:
        pass

    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(100)
    except Exception:
        pass

    return shots


async def extract_basic_page_info(page, requested_url: str) -> Dict[str, Any]:
    try:
        title = await page.title()
    except Exception:
        title = None

    info: Dict[str, Any] = {
        "requestedUrl": requested_url,
        "finalUrl": page.url,
        "title": title,
    }

    try:
        info["viewport"] = await page.evaluate(
            "() => ({ width: window.innerWidth, height: window.innerHeight })"
        )
    except Exception:
        info["viewport"] = None

    try:
        info["documentMetrics"] = await page.evaluate(
            """() => ({
                scrollHeight: document.body ? document.body.scrollHeight : null,
                scrollWidth: document.body ? document.body.scrollWidth : null,
                links: document.querySelectorAll('a[href]').length,
                buttons: document.querySelectorAll('button, [role="button"]').length,
                forms: document.querySelectorAll('form').length,
                images: document.querySelectorAll('img').length,
                iframes: document.querySelectorAll('iframe').length
            })"""
        )
    except Exception:
        info["documentMetrics"] = None

    return info


async def save_dom_snapshot(page, output_path: str) -> None:
    try:
        html = await page.content()
        write_text_file(output_path, html)
    except Exception as error:
        write_text_file(output_path, f"<!-- failed to capture DOM snapshot: {error} -->")


def collect_network_log(page, store: List[Dict[str, Any]]) -> None:
    def on_response(response):
        try:
            store.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "ok": response.ok,
                    "resourceType": response.request.resource_type,
                    "method": response.request.method,
                }
            )
        except Exception:
            pass

    page.on("response", on_response)
