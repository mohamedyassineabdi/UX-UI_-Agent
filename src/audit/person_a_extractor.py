from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _make_page_id(final_url: str, fallback_name: str) -> str:
    try:
        parsed = urlparse(final_url or "")
        path = (parsed.path or "/").strip()

        if not path or path == "/":
            return "home"

        parts = [segment.strip().lower() for segment in path.split("/") if segment.strip()]
        if not parts:
            return "home"

        safe_parts = []
        for part in parts:
            cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in part)
            cleaned = cleaned.strip("-_")
            if cleaned:
                safe_parts.append(cleaned)

        return "_".join(safe_parts) if safe_parts else "home"
    except Exception:
        fallback = _clean_text(fallback_name).lower().replace(" ", "_")
        return fallback or "page"


def _default_block(data: Dict[str, Any] | None = None, errors: List[str] | None = None) -> Dict[str, Any]:
    return {
        "status": "ok",
        "data": data or {},
        "errors": errors or [],
    }


async def extract_page_meta(
    *,
    page,
    page_info: Dict[str, Any],
    basic_page_info: Dict[str, Any] | None,
    screenshot_path: str | None,
    scroll_screenshot_paths: List[str] | None,
) -> Dict[str, Any]:
    final_url = (
        (basic_page_info or {}).get("finalUrl")
        or page.url
        or page_info.get("url")
    )
    title = (basic_page_info or {}).get("title")
    viewport = (basic_page_info or {}).get("viewport")
    document_metrics = (basic_page_info or {}).get("documentMetrics")

    page_id = _make_page_id(final_url, page_info.get("name", "page"))

    page_type_clues: List[str] = []
    lowered_url = (final_url or "").lower()
    lowered_name = _clean_text(page_info.get("name")).lower()

    for token, label in [
        ("login", "auth"),
        ("signin", "auth"),
        ("sign-in", "auth"),
        ("signup", "auth"),
        ("sign-up", "auth"),
        ("register", "auth"),
        ("contact", "contact"),
        ("about", "content"),
        ("blog", "content"),
        ("search", "search"),
        ("product", "commerce"),
        ("pricing", "pricing"),
        ("cart", "commerce"),
        ("checkout", "commerce"),
    ]:
        if token in lowered_url or token in lowered_name:
            page_type_clues.append(label)

    page_type_clues = sorted(set(page_type_clues))

    language = None
    try:
        language = await page.evaluate(
            "() => document.documentElement.lang || navigator.language || null"
        )
    except Exception:
        language = None

    return _default_block(
        {
            "url": page_info.get("url"),
            "finalUrl": final_url,
            "pageId": page_id,
            "name": page_info.get("name"),
            "title": title,
            "siteUrl": page_info.get("siteUrl"),
            "sourceType": page_info.get("sourceType"),
            "navigationPath": page_info.get("navigationPath") or [],
            "folderSegments": page_info.get("folderSegments") or [],
            "viewport": viewport,
            "documentMetrics": document_metrics or {},
            "language": _clean_text(language) or None,
            "screenshotPaths": {
                "page": screenshot_path,
                "scrolls": scroll_screenshot_paths or [],
            },
            "pageTypeClues": page_type_clues,
        }
    )


async def extract_titles_and_headings(*, page) -> Dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          const headings = Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6")).map((el, index) => ({
            index,
            level: el.tagName.toLowerCase(),
            text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim()
          })).filter(item => item.text);

          const byLevel = { h1: [], h2: [], h3: [], h4: [], h5: [], h6: [] };
          headings.forEach(item => byLevel[item.level].push(item.text));

          const anomalies = [];
          if (byLevel.h1.length === 0) anomalies.push("missing_h1");
          if (byLevel.h1.length > 1) anomalies.push("multiple_h1");

          let previous = 0;
          for (const item of headings) {
            const current = Number(item.level.replace("h", ""));
            if (previous && current > previous + 1) {
              anomalies.push(`skipped_heading_level_${previous}_to_${current}`);
            }
            previous = current;
          }

          return {
            headings,
            h1: byLevel.h1,
            h2: byLevel.h2,
            h3: byLevel.h3,
            h4: byLevel.h4,
            h5: byLevel.h5,
            h6: byLevel.h6,
            anomalies: Array.from(new Set(anomalies))
          };
        }
        """
    )

    return _default_block(
        data or {
            "headings": [],
            "h1": [],
            "h2": [],
            "h3": [],
            "h4": [],
            "h5": [],
            "h6": [],
            "anomalies": [],
        }
    )


async def extract_navigation(*, page) -> Dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          function textOf(el) {
            return ((el.innerText || el.textContent || "") || "").replace(/\\s+/g, " ").trim();
          }

          function hrefOf(el) {
            const href = el.getAttribute("href");
            return href ? el.href : null;
          }

          function collectLinks(root) {
            if (!root) return [];
            return Array.from(root.querySelectorAll("a[href]")).map((a, index) => ({
              index,
              text: textOf(a),
              href: hrefOf(a),
              ariaLabel: a.getAttribute("aria-label"),
              title: a.getAttribute("title")
            })).filter(item => item.text || item.href);
          }

          const primaryNavEl =
            document.querySelector("header nav") ||
            document.querySelector("nav[aria-label*='main' i]") ||
            document.querySelector("nav");

          const footerNavEl = document.querySelector("footer nav") || document.querySelector("footer");
          const asideNavEl = document.querySelector("aside nav") || document.querySelector("aside");

          const breadcrumbEl =
            document.querySelector("[aria-label*='breadcrumb' i]") ||
            document.querySelector(".breadcrumb") ||
            document.querySelector("nav.breadcrumb");

          const logoLink =
            document.querySelector("a[aria-label*='home' i]") ||
            document.querySelector("header a[href='/']") ||
            document.querySelector("a[href='/'] img")?.closest("a");

          const activeCandidates = Array.from(document.querySelectorAll("a[aria-current], .active a, a.active"))
            .map((a, index) => ({
              index,
              text: textOf(a),
              href: hrefOf(a),
              ariaCurrent: a.getAttribute("aria-current")
            }));

          return {
            primaryNav: collectLinks(primaryNavEl),
            footerNav: collectLinks(footerNavEl),
            sideNav: collectLinks(asideNavEl),
            breadcrumbs: collectLinks(breadcrumbEl),
            activeItems: activeCandidates,
            logoToHome: logoLink ? {
              text: textOf(logoLink),
              href: hrefOf(logoLink)
            } : null
          };
        }
        """
    )

    return _default_block(
        data or {
            "primaryNav": [],
            "footerNav": [],
            "sideNav": [],
            "breadcrumbs": [],
            "activeItems": [],
            "logoToHome": None,
        }
    )


async def extract_text_content(*, page) -> Dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          function clean(text) {
            return (text || "").replace(/\\s+/g, " ").trim();
          }

          const paragraphs = Array.from(document.querySelectorAll("p"))
            .map((el, index) => ({ index, text: clean(el.innerText || el.textContent) }))
            .filter(item => item.text);

          const listItems = Array.from(document.querySelectorAll("ul li, ol li"))
            .map((el, index) => ({ index, text: clean(el.innerText || el.textContent) }))
            .filter(item => item.text);

          const labels = Array.from(document.querySelectorAll("label"))
            .map((el, index) => ({ index, text: clean(el.innerText || el.textContent) }))
            .filter(item => item.text);

          const ctas = Array.from(document.querySelectorAll("a, button"))
            .map((el, index) => ({
              index,
              text: clean(el.innerText || el.textContent),
              kind: el.tagName.toLowerCase()
            }))
            .filter(item => item.text)
            .slice(0, 50);

          const longTextBlocks = paragraphs.filter(item => item.text.length >= 140);

          return {
            paragraphs,
            listItems,
            labels,
            ctaTexts: ctas,
            longTextBlocks
          };
        }
        """
    )

    return _default_block(
        data or {
            "paragraphs": [],
            "listItems": [],
            "labels": [],
            "ctaTexts": [],
            "longTextBlocks": [],
        }
    )


async def extract_forms(*, page) -> Dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          function clean(text) {
            return (text || "").replace(/\\s+/g, " ").trim();
          }

          const forms = Array.from(document.querySelectorAll("form")).map((form, formIndex) => {
            const fields = Array.from(
              form.querySelectorAll("input, select, textarea")
            ).map((field, fieldIndex) => {
              const id = field.id || null;
              const label = id
                ? document.querySelector(`label[for="${CSS.escape(id)}"]`)
                : null;

              return {
                index: fieldIndex,
                tag: field.tagName.toLowerCase(),
                type: field.getAttribute("type") || field.tagName.toLowerCase(),
                name: field.getAttribute("name"),
                id,
                label: label ? clean(label.innerText || label.textContent) : null,
                placeholder: field.getAttribute("placeholder"),
                required: field.required || field.getAttribute("aria-required") === "true",
                disabled: field.disabled,
                helperText: null
              };
            });

            return {
              index: formIndex,
              action: form.getAttribute("action"),
              method: form.getAttribute("method") || "get",
              fields
            };
          });

          return {
            items: forms,
            totalForms: forms.length
          };
        }
        """
    )

    return _default_block(data or {"items": [], "totalForms": 0})


async def extract_media(*, page) -> Dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          const images = Array.from(document.querySelectorAll("img")).map((img, index) => ({
            index,
            src: img.currentSrc || img.src || null,
            alt: img.getAttribute("alt"),
            width: img.naturalWidth || null,
            height: img.naturalHeight || null
          }));

          const videos = Array.from(document.querySelectorAll("video")).map((video, index) => ({
            index,
            src: video.currentSrc || video.getAttribute("src") || null,
            controls: !!video.controls,
            autoplay: !!video.autoplay
          }));

          const audios = Array.from(document.querySelectorAll("audio")).map((audio, index) => ({
            index,
            src: audio.currentSrc || audio.getAttribute("src") || null,
            controls: !!audio.controls
          }));

          const captions = Array.from(document.querySelectorAll("track[kind='captions'], track[kind='subtitles']")).length;

          return {
            images,
            videos,
            audios,
            hasCaptionTracks: captions > 0
          };
        }
        """
    )

    return _default_block(
        data or {
            "images": [],
            "videos": [],
            "audios": [],
            "hasCaptionTracks": False,
        }
    )


async def _safe_extract(extractor_name: str, extractor_coro) -> Dict[str, Any]:
    try:
        return await extractor_coro
    except Exception as error:
        return {
            "status": "failed",
            "data": {},
            "errors": [f"{extractor_name}: {str(error)}"],
        }


async def extract_person_a_blocks(
    *,
    page,
    page_info: Dict[str, Any],
    basic_page_info: Dict[str, Any] | None,
    screenshot_path: str | None,
    scroll_screenshot_paths: List[str] | None,
) -> Dict[str, Any]:
    page_meta = await _safe_extract(
        "pageMeta",
        extract_page_meta(
            page=page,
            page_info=page_info,
            basic_page_info=basic_page_info,
            screenshot_path=screenshot_path,
            scroll_screenshot_paths=scroll_screenshot_paths,
        ),
    )

    titles_headings = await _safe_extract(
        "titlesAndHeadings",
        extract_titles_and_headings(page=page),
    )

    navigation = await _safe_extract(
        "navigation",
        extract_navigation(page=page),
    )

    text_content = await _safe_extract(
        "textContent",
        extract_text_content(page=page),
    )

    forms = await _safe_extract(
        "forms",
        extract_forms(page=page),
    )

    media = await _safe_extract(
        "media",
        extract_media(page=page),
    )

    return {
        "pageMeta": page_meta,
        "titlesAndHeadings": titles_headings,
        "navigation": navigation,
        "textContent": text_content,
        "forms": forms,
        "media": media,
    }