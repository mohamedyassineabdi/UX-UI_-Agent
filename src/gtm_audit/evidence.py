from __future__ import annotations

import os
import re
from os import getenv
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from src.report.generate_audit_report import (
    COMPONENT_PRIORITY_BY_SHEET,
    SPOTLIGHT_FRAME_HEIGHT,
    SPOTLIGHT_FRAME_WIDTH,
    absolute_from_repo,
    build_rendered_page_lookup,
    candidate_score,
    clean_text,
    iter_page_components,
    load_json,
    normalize_match_text,
    pick_best_component,
    tokenize_for_match,
)
from .vision_client import run_spotlight_candidate_review


SEARCH_TERMS = ("search", "recherche", "chercher", "loupe", "magnifier", "find")
HEADER_TYPES = ("navigation", "nav-link", "button", "link", "section")
DEFAULT_SPOTLIGHT_REVIEW = "0"
CONTEXT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_CONTEXT_SCREENSHOT_USAGE: Dict[str, int] = {}
SPECIFIC_REGION_TERMS = {
    "button",
    "cta",
    "link",
    "text",
    "copy",
    "label",
    "heading",
    "title",
    "photo",
    "image",
    "picture",
    "icon",
    "logo",
    "form",
    "field",
    "input",
    "card",
    "menu",
    "nav",
    "header",
    "control",
    "component",
    "section",
    "area",
}
GENERAL_REGION_TERMS = {
    "full page",
    "whole page",
    "full screen",
    "whole screen",
    "full viewport",
    "whole viewport",
    "entire page",
    "entire screen",
    "viewport",
    "website",
    "overall",
    "general",
    "responsive",
    "layout failure",
    "performance",
    "web vitals",
}


def _combined_component_type(component: Dict[str, Any]) -> str:
    return " ".join(
        normalize_match_text(component.get(key))
        for key in ("semanticType", "_bucket", "uxRole")
        if normalize_match_text(component.get(key))
    )


def _issue_text(item: Dict[str, Any]) -> str:
    parts = [
        item.get("title"),
        item.get("explanation"),
        item.get("evidence"),
        item.get("recommendation"),
    ]
    return normalize_match_text(" ".join(clean_text(part) for part in parts if clean_text(part)))


def _rect_union(components: list[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    rects = [component.get("rect") or {} for component in components if isinstance(component.get("rect"), dict)]
    usable = []
    for rect in rects:
        try:
            x = float(rect.get("x"))
            y = float(rect.get("y"))
            width = float(rect.get("width"))
            height = float(rect.get("height"))
        except Exception:
            continue
        if width <= 0 or height <= 0:
            continue
        usable.append((x, y, width, height))
    if not usable:
        return None
    left = min(item[0] for item in usable)
    top = min(item[1] for item in usable)
    right = max(item[0] + item[2] for item in usable)
    bottom = max(item[1] + item[3] for item in usable)
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def _visual_region(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("visualRegion", "visual_region", "region", "boundingBox", "bounding_box"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return None


def _component_from_visual_region(item: Dict[str, Any], screenshot_path: str) -> Optional[Dict[str, Any]]:
    region = _visual_region(item)
    if not isinstance(region, dict):
        return None

    absolute = absolute_from_repo(screenshot_path)
    if not absolute or not absolute.exists():
        return None

    try:
        from PIL import Image

        with Image.open(absolute) as image:
            image_width, image_height = image.size
    except Exception:
        return None

    try:
        x = float(region.get("x"))
        y = float(region.get("y"))
        width = float(region.get("width"))
        height = float(region.get("height"))
    except Exception:
        return None

    if max(abs(x), abs(y), abs(width), abs(height)) <= 1.5 or "normalized" in clean_text(region.get("coordinate_system")).lower():
        x *= image_width
        width *= image_width
        y *= image_height
        height *= image_height

    if width <= 0 or height <= 0:
        return None

    width = min(width, image_width)
    height = min(height, image_height)
    x = max(0.0, min(x, image_width - width))
    y = max(0.0, min(y, image_height - height))
    return {
        "rect": {"x": x, "y": y, "width": width, "height": height},
        "semanticType": clean_text(region.get("description") or "visual-region"),
        "uxRole": clean_text(item.get("axisName") or item.get("sourceSheet") or "visual-region"),
        "_bucket": "visual-region",
    }


def _header_focus_component(rendered_page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    top_components = []
    for component in iter_page_components(rendered_page):
        rect = component.get("rect") or {}
        try:
            y = float(rect.get("y"))
            height = float(rect.get("height"))
            width = float(rect.get("width"))
        except Exception:
            continue
        if width <= 0 or height <= 0 or y > 220:
            continue
        combined_type = _combined_component_type(component)
        if any(preferred in combined_type for preferred in HEADER_TYPES):
            top_components.append(component)

    if not top_components:
        return None

    navigation_components = [component for component in top_components if "navigation" in _combined_component_type(component)]
    rect = _rect_union(navigation_components or top_components)
    if not rect:
        return None
    return {
        "rect": rect,
        "semanticType": "navigation",
        "uxRole": "header-focus",
        "_bucket": "navigation",
    }


def _search_focus_component(item: Dict[str, Any], rendered_page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = []
    for component in iter_page_components(rendered_page):
        text = normalize_match_text(component.get("_componentText"))
        combined_type = _combined_component_type(component)
        score = 0.0
        has_search_signal = any(term in text for term in SEARCH_TERMS)
        if has_search_signal:
            score += 12.0
        if has_search_signal and "input" in combined_type:
            score += 4.0
        if has_search_signal and any(preferred in combined_type for preferred in ("navigation", "nav-link", "button", "link")):
            score += 2.0
        rect = component.get("rect") or {}
        try:
            y = float(rect.get("y"))
        except Exception:
            y = 9999.0
        if has_search_signal and y < 260:
            score += 1.5
        if score > 0:
            candidates.append((score, component))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    return _header_focus_component(rendered_page)


def _pick_gtm_component(item: Dict[str, Any], rendered_page: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rendered_page:
        return None

    issue_text = _issue_text(item)
    if any(term in issue_text for term in SEARCH_TERMS):
        search_component = _search_focus_component(item, rendered_page)
        if search_component:
            return search_component

    component = pick_best_component(
        {
            "sheet": item.get("sourceSheet") or item.get("axisName") or "Content",
            "criterion": item.get("title"),
            "rationale": item.get("explanation") or item.get("evidence"),
            "evidence": [item.get("evidence"), item.get("whyItMatters")],
        },
        rendered_page,
    )
    if component:
        return component

    if any(term in issue_text for term in ("navigation", "menu", "header", "nav", "search")):
        return _header_focus_component(rendered_page)

    return None


def _evidence_bundle_component(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bundle = item.get("evidenceBundle")
    if not isinstance(bundle, dict):
        return None
    target = bundle.get("target")
    if not isinstance(target, dict):
        return None
    rect = target.get("rect")
    if not isinstance(rect, dict):
        return None
    try:
        width = float(rect.get("width"))
        height = float(rect.get("height"))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "rect": rect,
        "semanticType": clean_text(target.get("component_type") or target.get("target_kind") or "evidence-target"),
        "uxRole": clean_text(target.get("issue_kind") or "evidence-target"),
        "_bucket": clean_text(target.get("target_kind") or "evidence"),
    }


def _field_text(component: Dict[str, Any], key: str) -> str:
    return normalize_match_text(component.get(key))


def _symbol_normalized(value: Any) -> str:
    text = clean_text(value)
    return normalize_match_text(text.replace("✕", "x").replace("×", "x"))


def _cited_example_terms(item: Dict[str, Any]) -> list[Dict[str, str]]:
    texts = [
        clean_text(item.get("title")),
        clean_text(item.get("evidence")),
        clean_text(item.get("explanation")),
        clean_text(item.get("recommendation")),
    ]
    bundle = item.get("evidenceBundle")
    if isinstance(bundle, dict):
        raw = bundle.get("raw")
        if isinstance(raw, dict):
            samples = raw.get("samples")
            if isinstance(samples, list):
                for sample in samples:
                    if isinstance(sample, dict):
                        texts.append(json_safe_text(sample))
                    else:
                        texts.append(clean_text(sample))
    haystack = "\n".join(text for text in texts if text)
    if not haystack:
        return []

    terms: list[Dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, value: str, tag: str = "") -> None:
        normalized = normalize_match_text(value)
        if not normalized:
            return
        key = (kind, normalized, normalize_match_text(tag))
        if key in seen:
            return
        seen.add(key)
        terms.append({"kind": kind, "value": normalized, "tag": normalize_match_text(tag)})

    for tag, element_id in re.findall(r"\b([a-zA-Z][\w-]*)#([A-Za-z0-9_-]+)\b", haystack):
        add("id", element_id, tag)

    for element_id in re.findall(r"\bid=[\"']([^\"']+)[\"']", haystack, flags=re.IGNORECASE):
        add("id", element_id)

    for element_id in re.findall(r"(?<![\w-])#([A-Za-z][\w-]+)\b", haystack):
        add("id", element_id)

    example_match = re.search(r"\bexamples?\s*(?:include|:)\s*([^.;\n]+)", haystack, flags=re.IGNORECASE)
    if example_match:
        for raw_token in re.split(r",|\band\b|\bet\b", example_match.group(1), flags=re.IGNORECASE):
            token = clean_text(raw_token).strip(" '\"`")
            token = re.sub(r":.*$", "", token).strip()
            if not token:
                continue
            selector_match = re.match(r"([a-zA-Z][\w-]*)#([A-Za-z0-9_-]+)$", token)
            if selector_match:
                add("id", selector_match.group(2), selector_match.group(1))
            elif len(token) <= 48:
                add("label", token)

    return terms[:8]


def json_safe_text(value: Dict[str, Any]) -> str:
    parts = []
    for key in (
        "clickableText",
        "clickableAriaLabel",
        "clickableTag",
        "reason",
        "error",
        "id",
        "name",
        "label",
        "placeholder",
    ):
        text = clean_text(value.get(key))
        if text:
            parts.append(text)
    return " ".join(parts)


def _component_visible_in_screenshot(component: Dict[str, Any], screenshot_path: str) -> bool:
    rect_values = _component_rect_values(component)
    size = _image_size(screenshot_path)
    if not rect_values or not size:
        return False
    x, y, width, height = rect_values
    image_width, image_height = size
    return x < image_width and y < image_height and x + width > 0 and y + height > 0


def _component_match_score(component: Dict[str, Any], term: Dict[str, str]) -> float:
    kind = term.get("kind") or ""
    value = term.get("value") or ""
    tag = term.get("tag") or ""
    if not value:
        return 0.0

    component_tag = _field_text(component, "tag")
    if tag and component_tag != tag:
        return 0.0

    score = 0.0
    if kind == "id" and _field_text(component, "id") == value:
        score += 100.0
    if kind == "id" and _field_text(component, "name") == value:
        score += 82.0
    if kind == "label":
        if _symbol_normalized(component.get("text")) == value:
            score += 92.0
        if _symbol_normalized(component.get("label")) == value:
            score += 86.0
        if _field_text(component, "placeholder") == value:
            score += 76.0
        if _field_text(component, "id") == value or _field_text(component, "name") == value:
            score += 70.0

    searchable = " ".join(
        _symbol_normalized(component.get(key))
        for key in ("text", "label", "placeholder", "id", "name", "className", "semanticType", "_componentText")
        if clean_text(component.get(key))
    )
    if value and value in searchable:
        score += 24.0
    return score


def _component_from_cited_examples(
    item: Dict[str, Any],
    rendered_page: Optional[Dict[str, Any]],
    screenshot_path: str,
) -> Optional[Dict[str, Any]]:
    if not rendered_page:
        return None
    terms = _cited_example_terms(item)
    if not terms:
        return None

    ranked: list[tuple[float, Dict[str, Any], Dict[str, str]]] = []
    for component in iter_page_components(rendered_page):
        if component.get("visible") is False:
            continue
        if not _component_visible_in_screenshot(component, screenshot_path):
            continue
        best_score = 0.0
        best_term: Dict[str, str] = {}
        for term in terms:
            score = _component_match_score(component, term)
            if score > best_score:
                best_score = score
                best_term = term
        if best_score >= 70:
            ranked.append((best_score, component, best_term))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    score, component, term = ranked[0]
    return {
        **component,
        "semanticType": clean_text(component.get("semanticType") or term.get("tag") or "cited-example"),
        "uxRole": clean_text(component.get("uxRole") or "cited evidence example"),
        "_bucket": "cited-example",
    }


def _component_rect_values(component: Dict[str, Any]) -> Optional[tuple[float, float, float, float]]:
    rect = component.get("rect") or {}
    try:
        x = float(rect.get("x"))
        y = float(rect.get("y"))
        width = float(rect.get("width"))
        height = float(rect.get("height"))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _image_size(path: str) -> Optional[tuple[int, int]]:
    absolute = absolute_from_repo(path)
    if not absolute or not absolute.exists():
        return None
    try:
        from PIL import Image

        with Image.open(absolute) as image:
            return image.size
    except Exception:
        return None


def _component_precision_text(item: Dict[str, Any], component: Dict[str, Any]) -> str:
    parts = [
        component.get("semanticType"),
        component.get("uxRole"),
        component.get("_bucket"),
        component.get("_componentText"),
        component.get("text"),
        component.get("label"),
        item.get("title"),
        item.get("sourceSheet"),
        item.get("axisName"),
        item.get("evidence"),
    ]
    return normalize_match_text(" ".join(clean_text(part) for part in parts if clean_text(part)))


def _should_mark_component(item: Dict[str, Any], component: Optional[Dict[str, Any]], screenshot_path: str) -> bool:
    if not component or item.get("responsiveFailure"):
        return False
    bundle = item.get("evidenceBundle")
    bundle_source = clean_text((bundle or {}).get("source") if isinstance(bundle, dict) else "").lower()
    if bundle_source in {"playwright_performance_snapshot", "playwright_performance_kpi"}:
        return False

    rect_values = _component_rect_values(component)
    size = _image_size(screenshot_path)
    if not rect_values or not size:
        return False

    x, y, width, height = rect_values
    image_width, image_height = size
    width = min(width, float(image_width))
    height = min(height, float(image_height))
    width_ratio = width / max(float(image_width), 1.0)
    height_ratio = height / max(float(image_height), 1.0)
    area_ratio = (width * height) / max(float(image_width * image_height), 1.0)
    is_full_view = x <= image_width * 0.03 and y <= image_height * 0.03 and width_ratio >= 0.92 and height_ratio >= 0.86
    if is_full_view or area_ratio >= 0.68:
        return False

    text = _component_precision_text(item, component)
    has_general_signal = any(term in text for term in GENERAL_REGION_TERMS)
    has_specific_signal = any(term in text for term in SPECIFIC_REGION_TERMS)
    if has_general_signal and not has_specific_signal:
        return False
    if area_ratio > 0.42 and not has_specific_signal:
        return False
    return True


def _spotlight_review_enabled() -> bool:
    return clean_text(getenv("GTM_VISION_VERIFY_SPOTLIGHTS", DEFAULT_SPOTLIGHT_REVIEW)).lower() not in {"0", "false", "no", "off"}


def _heuristic_spotlights_enabled() -> bool:
    return clean_text(getenv("GTM_HEURISTIC_SPOTLIGHTS", "0")).lower() not in {"0", "false", "no", "off"}


def _component_preview_text(component: Dict[str, Any]) -> str:
    return clean_text(component.get("_componentText") or component.get("text") or component.get("label") or component.get("placeholder"))


def _candidate_reason(component: Dict[str, Any]) -> str:
    combined_type = _combined_component_type(component)
    if "navigation" in combined_type:
        return "Header/navigation context"
    if "input" in combined_type:
        return "Form or input control"
    if "button" in combined_type or "nav-link" in combined_type or "link" in combined_type:
        return "Interactive control"
    if "heading" in combined_type or "text" in combined_type:
        return "Visible text or heading"
    return "Relevant UI region"


def _issue_payload_for_matching(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sheet": item.get("sourceSheet") or item.get("axisName") or "Content",
        "criterion": item.get("title"),
        "rationale": item.get("explanation") or item.get("evidence"),
        "evidence": [item.get("evidence"), item.get("whyItMatters")],
    }


def _candidate_components(item: Dict[str, Any], rendered_page: Dict[str, Any], preferred_component: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issue_payload = _issue_payload_for_matching(item)
    phrases = []
    phrases.extend(issue_payload.get("evidence") or [])
    if clean_text(issue_payload.get("rationale")):
        phrases.append(issue_payload["rationale"])
    if clean_text(issue_payload.get("criterion")):
        phrases.append(issue_payload["criterion"])

    phrase_tokens: List[str] = []
    for phrase in phrases:
        phrase_tokens.extend(tokenize_for_match(phrase))
    preferred_types = COMPONENT_PRIORITY_BY_SHEET.get(issue_payload.get("sheet"), ["text-block", "heading", "button", "link", "section"])

    ranked = []
    for component in iter_page_components(rendered_page):
        score = candidate_score(component, phrases, phrase_tokens, preferred_types)
        if score <= 0:
            continue
        ranked.append((score, component))
    ranked.sort(key=lambda item: item[0], reverse=True)

    candidates = []
    seen = set()

    def add_candidate(component: Optional[Dict[str, Any]]) -> None:
        if not component:
            return
        rect = component.get("rect") or {}
        key = (
            round(float(rect.get("x", 0)), 1),
            round(float(rect.get("y", 0)), 1),
            round(float(rect.get("width", 0)), 1),
            round(float(rect.get("height", 0)), 1),
        )
        if key in seen:
            return
        seen.add(key)
        candidates.append(component)

    add_candidate(preferred_component)

    if any(term in _issue_text(item) for term in SEARCH_TERMS):
        add_candidate(_header_focus_component(rendered_page))

    for _, component in ranked:
        add_candidate(component)
        if len(candidates) >= 4:
            break

    return candidates[:4]


def _spotlight_crop_box(
    image_width: int,
    image_height: int,
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[int, int, int, int]:
    target_ratio = SPOTLIGHT_FRAME_WIDTH / SPOTLIGHT_FRAME_HEIGHT
    image_ratio = image_width / max(image_height, 1)
    if image_ratio >= target_ratio:
        crop_height = image_height
        crop_width = min(image_width, int(round(crop_height * target_ratio)))
        center_x = image_width / 2 if width / max(image_width, 1) > 0.55 else x + width / 2
        left = int(round(max(0, min(center_x - crop_width / 2, image_width - crop_width))))
        return left, 0, left + crop_width, image_height

    crop_width = image_width
    crop_height = min(image_height, int(round(crop_width / target_ratio)))
    if y <= crop_height * 0.25:
        top = 0
    else:
        center_y = y + height / 2
        top = int(round(max(0, min(center_y - crop_height * 0.45, image_height - crop_height))))
    return 0, top, crop_width, top + crop_height


def _clamp_bounds(bounds: tuple[float, float, float, float], image_width: int, image_height: int, inset: int) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    return (
        max(inset, min(left, image_width - inset)),
        max(inset, min(top, image_height - inset)),
        max(inset, min(right, image_width - inset)),
        max(inset, min(bottom, image_height - inset)),
    )


def _draw_component_highlight(draw: Any, bounds: tuple[float, float, float, float], *, broad: bool = False) -> None:
    stroke_width = 12
    soft_stroke_width = 18
    inset = max(stroke_width, soft_stroke_width)
    bounds = _clamp_bounds(bounds, SPOTLIGHT_FRAME_WIDTH, SPOTLIGHT_FRAME_HEIGHT, inset)
    soft_bounds = _clamp_bounds(
        (bounds[0] - 10, bounds[1] - 10, bounds[2] + 10, bounds[3] + 10),
        SPOTLIGHT_FRAME_WIDTH,
        SPOTLIGHT_FRAME_HEIGHT,
        inset,
    )
    if broad:
        radius = max(26, int(min(bounds[2] - bounds[0], bounds[3] - bounds[1]) * 0.035))
        draw.rounded_rectangle(soft_bounds, radius=radius + 8, outline=(255, 52, 52, 110), width=soft_stroke_width)
        draw.rounded_rectangle(bounds, radius=radius, outline=(255, 52, 52, 240), width=stroke_width)
        return

    draw.ellipse(soft_bounds, outline=(255, 52, 52, 110), width=soft_stroke_width)
    draw.ellipse(bounds, outline=(255, 52, 52, 240), width=stroke_width)


def _create_circular_spotlight_image(
    screenshot_path: str,
    component: Optional[Dict[str, Any]],
    output_path: Path,
    label: str = "",
    *,
    contain_tall_source: bool = False,
) -> bool:
    if not component:
        return False

    absolute = absolute_from_repo(screenshot_path)
    if not absolute or not absolute.exists():
        return False

    rect = component.get("rect") or {}
    try:
        x = float(rect.get("x"))
        y = float(rect.get("y"))
        width = float(rect.get("width"))
        height = float(rect.get("height"))
    except Exception:
        return False

    if width <= 0 or height <= 0:
        return False

    try:
        from PIL import Image, ImageDraw
    except Exception:
        return False

    with Image.open(absolute) as source:
        source = source.convert("RGBA")
        image_width, image_height = source.size

        width = min(width, float(image_width))
        height = min(height, float(image_height))
        x = max(0.0, min(x, float(image_width) - width))
        y = max(0.0, min(y, float(image_height) - height))

        if contain_tall_source and image_width / max(float(image_height), 1.0) < 0.8:
            canvas = Image.new("RGBA", (SPOTLIGHT_FRAME_WIDTH, SPOTLIGHT_FRAME_HEIGHT), (250, 246, 238, 255))
            scale = min((SPOTLIGHT_FRAME_WIDTH - 120) / max(float(image_width), 1.0), (SPOTLIGHT_FRAME_HEIGHT - 80) / max(float(image_height), 1.0))
            scaled_width = max(1, int(round(image_width * scale)))
            scaled_height = max(1, int(round(image_height * scale)))
            offset_x = int(round((SPOTLIGHT_FRAME_WIDTH - scaled_width) / 2))
            offset_y = int(round((SPOTLIGHT_FRAME_HEIGHT - scaled_height) / 2))
            scaled = source.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
            canvas.paste(scaled, (offset_x, offset_y))
            draw = ImageDraw.Draw(canvas, "RGBA")

            comp_left = offset_x + x * scale
            comp_top = offset_y + y * scale
            comp_right = offset_x + (x + width) * scale
            comp_bottom = offset_y + (y + height) * scale
            halo = max(18, int(max(comp_right - comp_left, comp_bottom - comp_top) * 0.08))
            _draw_component_highlight(
                draw,
                (comp_left - halo, comp_top - halo, comp_right + halo, comp_bottom + halo),
                broad=True,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
            return True

        left, top, right, bottom = _spotlight_crop_box(image_width, image_height, x, y, width, height)

        crop = source.crop((left, top, right, bottom))
        scale_x = SPOTLIGHT_FRAME_WIDTH / crop.width
        scale_y = SPOTLIGHT_FRAME_HEIGHT / crop.height
        crop = crop.resize((SPOTLIGHT_FRAME_WIDTH, SPOTLIGHT_FRAME_HEIGHT))
        draw = ImageDraw.Draw(crop, "RGBA")

        comp_left = (x - left) * scale_x
        comp_top = (y - top) * scale_y
        comp_right = (x + width - left) * scale_x
        comp_bottom = (y + height - top) * scale_y

        halo = max(24, int(max(comp_right - comp_left, comp_bottom - comp_top) * 0.12))
        broad = (
            (comp_right - comp_left) / max(float(SPOTLIGHT_FRAME_WIDTH), 1.0) > 0.72
            or (comp_bottom - comp_top) / max(float(SPOTLIGHT_FRAME_HEIGHT), 1.0) > 0.62
            or ((comp_right - comp_left) * (comp_bottom - comp_top)) / max(float(SPOTLIGHT_FRAME_WIDTH * SPOTLIGHT_FRAME_HEIGHT), 1.0) > 0.34
        )
        _draw_component_highlight(
            draw,
            (comp_left - halo, comp_top - halo, comp_right + halo, comp_bottom + halo),
            broad=broad,
        )

        if clean_text(label):
            try:
                from PIL import ImageFont
                font = ImageFont.load_default()
            except Exception:
                font = None
            badge = (24, 24, 112, 84)
            draw.rounded_rectangle(badge, radius=18, fill=(255, 52, 52, 230))
            draw.text((58, 43), clean_text(label), fill=(255, 255, 255, 255), anchor="mm", font=font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        crop.convert("RGB").save(output_path, format="PNG", optimize=True)
        return True


def _create_context_spotlight_image(
    screenshot_path: str,
    output_path: Path,
    *,
    contain_tall_source: bool = False,
) -> bool:
    absolute = absolute_from_repo(screenshot_path)
    if not absolute or not absolute.exists():
        return False

    try:
        from PIL import Image
    except Exception:
        return False

    try:
        with Image.open(absolute) as source_image:
            source = source_image.convert("RGBA")
    except Exception:
        return False

    image_width, image_height = source.size
    if image_width <= 0 or image_height <= 0:
        return False

    if contain_tall_source:
        canvas = Image.new("RGBA", (SPOTLIGHT_FRAME_WIDTH, SPOTLIGHT_FRAME_HEIGHT), (250, 246, 238, 255))
        scale = min(
            (SPOTLIGHT_FRAME_WIDTH - 120) / max(float(image_width), 1.0),
            (SPOTLIGHT_FRAME_HEIGHT - 80) / max(float(image_height), 1.0),
        )
        scaled_width = max(1, int(round(image_width * scale)))
        scaled_height = max(1, int(round(image_height * scale)))
        offset_x = int(round((SPOTLIGHT_FRAME_WIDTH - scaled_width) / 2))
        offset_y = int(round((SPOTLIGHT_FRAME_HEIGHT - scaled_height) / 2))
        canvas.paste(source.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS), (offset_x, offset_y))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
        return True

    target_ratio = SPOTLIGHT_FRAME_WIDTH / SPOTLIGHT_FRAME_HEIGHT
    source_ratio = image_width / max(float(image_height), 1.0)
    if source_ratio >= target_ratio:
        crop_height = image_height
        crop_width = min(image_width, int(round(crop_height * target_ratio)))
        left = int(round(max(0, (image_width - crop_width) / 2)))
        top = 0
    else:
        crop_width = image_width
        crop_height = min(image_height, int(round(crop_width / target_ratio)))
        left = 0
        top = 0

    crop = source.crop((left, top, left + crop_width, top + crop_height)).resize(
        (SPOTLIGHT_FRAME_WIDTH, SPOTLIGHT_FRAME_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    crop.convert("RGB").save(output_path, format="PNG", optimize=True)
    return True


def _domain_screenshot_root(screenshot_path: str) -> Optional[Path]:
    absolute = absolute_from_repo(screenshot_path)
    if not absolute:
        return None
    parts = absolute.parts
    try:
        index = parts.index("screenshots")
    except ValueError:
        return None
    if len(parts) <= index + 1:
        return None
    root = Path(*parts[: index + 2])
    return root if root.exists() else None


def _context_keywords(item: Dict[str, Any]) -> List[str]:
    issue_text = normalize_match_text(
        " ".join(
            clean_text(item.get(key))
            for key in ("title", "axisName", "pageName", "evidence", "explanation", "recommendation")
            if clean_text(item.get(key))
        )
    )
    keyword_sets = [
        (("form", "questions", "field", "input"), ["contact", "hello", "email", "form", "field"]),
        (("search", "find"), ["search", "menu", "nav", "header"]),
        (("browser", "back", "forward", "copy", "paste"), ["interactions", "navigation", "dom_change"]),
        (("visual style", "consistent", "component", "button", "control"), ["work", "studio", "branding", "consultancy", "digital", "interactions"]),
        (("color", "saturation", "vibrate", "fatigue"), ["work", "branding", "studio", "initial", "bottom"]),
        (("device", "browser", "screen", "resolution", "responsive", "phone", "mobile"), ["responsive", "mobile"]),
        (("trust", "proof", "client", "testimonial", "credibility"), ["work", "studio", "client", "case", "about"]),
        (("value", "market", "audience", "commercial", "proposition"), ["studio", "work", "about", "initial"]),
        (("navigation", "menu", "architecture", "flow"), ["navigation", "menu", "work", "studio"]),
        (("cta", "action", "verb", "label"), ["interactions", "navigation", "contact", "work", "studio"]),
    ]
    keywords: List[str] = []
    for triggers, additions in keyword_sets:
        if any(trigger in issue_text for trigger in triggers):
            keywords.extend(additions)
    keywords.extend(token for token in tokenize_for_match(issue_text) if len(token) >= 4)
    return list(dict.fromkeys(keywords))[:24]


def _select_context_screenshot(item: Dict[str, Any], fallback_path: str, issue_index: int) -> str:
    root = _domain_screenshot_root(fallback_path)
    if not root:
        return fallback_path

    fallback_absolute = absolute_from_repo(fallback_path)
    keywords = _context_keywords(item)
    page_name = normalize_match_text(item.get("pageName"))
    title = normalize_match_text(item.get("title"))
    candidates: List[tuple[float, str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CONTEXT_IMAGE_EXTENSIONS:
            continue
        normalized = normalize_match_text(str(path.relative_to(root)))
        score = 0.0
        if path == fallback_absolute:
            score -= 4.0
        usage = _CONTEXT_SCREENSHOT_USAGE.get(str(path.resolve()), 0)
        if usage:
            score -= usage * 4.0
        if "\\page\\main" in str(path).lower().replace("/", "\\"):
            score -= 2.0
        if "responsive" in normalized:
            score += 3.0 if any(word in title for word in ("responsive", "phone", "mobile", "device", "screen")) else -1.0
        if "interactions" in normalized:
            score += 1.5
        if "scrolls" in normalized:
            score += 1.0
        if page_name and page_name in normalized:
            score += 1.0
        for keyword in keywords:
            if keyword and keyword in normalized:
                score += 2.0
        candidates.append((score, normalized, path))

    if not candidates:
        return fallback_path

    viable = [candidate for candidate in candidates if candidate[0] > 0]
    pool = viable or candidates
    pool.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    selected = pool[issue_index % min(len(pool), 12)]
    _CONTEXT_SCREENSHOT_USAGE[str(selected[2].resolve())] = _CONTEXT_SCREENSHOT_USAGE.get(str(selected[2].resolve()), 0) + 1
    try:
        return str(selected[2].relative_to(Path.cwd()))
    except ValueError:
        return str(selected[2])


def _review_spotlight_candidates(
    *,
    item: Dict[str, Any],
    screenshot_path: str,
    output_dir: Path,
    filename_stem: str,
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not candidates or not _spotlight_review_enabled():
        return None

    review_dir = output_dir / "evidence" / "_candidate_reviews"
    review_inputs = []
    for index, component in enumerate(candidates):
        label = chr(65 + index)
        preview_path = review_dir / f"{filename_stem}-{label}.png"
        if not _create_circular_spotlight_image(screenshot_path, component, preview_path, label=label):
            continue
        review_inputs.append(
            {
                "image_path": str(preview_path),
                "label": label,
                "component_type": _combined_component_type(component),
                "component_text": _component_preview_text(component),
                "reason": _candidate_reason(component),
                "component": component,
            }
        )

    if not review_inputs:
        return None

    issue_context = {
        "title": clean_text(item.get("title")),
        "explanation": clean_text(item.get("explanation")),
        "evidence": clean_text(item.get("evidence")),
        "page_name": clean_text(item.get("pageName")),
        "page_url": clean_text(item.get("pageUrl")),
    }
    review = run_spotlight_candidate_review(issue=issue_context, candidates=review_inputs)
    result = review.get("result") or {}
    try:
        selected = int(result.get("best_candidate"))
    except Exception:
        selected = -1
    confidence = float(result.get("confidence") or 0.0)
    if selected < 0 or selected >= len(review_inputs) or confidence < 0.45:
        return None
    return review_inputs[selected]["component"]


def build_gtm_spotlight(
    *,
    item: Dict[str, Any],
    output_dir: Path,
    cleaned_path: Path,
    rendered_path: Path,
    issue_index: int,
) -> str:
    if not cleaned_path.exists() or not rendered_path.exists():
        return ""

    rendered_lookup = build_rendered_page_lookup(load_json(rendered_path))

    page_key_candidates = [
        normalize_match_text(item.get("pageUrl")),
        normalize_match_text(item.get("pageName")),
    ]
    rendered_page = None
    for key in page_key_candidates:
        if key and key in rendered_lookup:
            rendered_page = rendered_lookup[key]
            break

    bundle_target = ((item.get("evidenceBundle") or {}).get("target") or {}) if isinstance(item.get("evidenceBundle"), dict) else {}
    screenshot_path = clean_text(bundle_target.get("screenshot_path")) or clean_text(item.get("screenshotPath"))
    bundle_component = _evidence_bundle_component(item)
    region_component = _component_from_visual_region(item, screenshot_path)
    cited_component = _component_from_cited_examples(item, rendered_page, screenshot_path)
    component = bundle_component or region_component or cited_component
    has_direct_region = bool(component)

    filename_stem = f"issue-{str(issue_index).zfill(2)}-{normalize_match_text(item.get('title') or 'issue')[:60].replace(' ', '-')}"
    filename = f"{filename_stem}.png"
    output_path = output_dir / "evidence" / filename
    if not has_direct_region and rendered_page and _heuristic_spotlights_enabled():
        component = _pick_gtm_component(item, rendered_page)
        candidate_components = _candidate_components(item, rendered_page, component)
        reviewed_component = _review_spotlight_candidates(
            item=item,
            screenshot_path=clean_text(item.get("screenshotPath")),
            output_dir=output_dir,
            filename_stem=filename_stem,
            candidates=candidate_components,
        )
        if reviewed_component:
            component = reviewed_component
        has_direct_region = bool(component)

    contain_tall_source = bool(item.get("responsiveFailure"))
    should_mark = _should_mark_component(item, component, screenshot_path)
    if not has_direct_region or not should_mark:
        screenshot_path = _select_context_screenshot(item, screenshot_path, issue_index)
        if not _create_context_spotlight_image(screenshot_path, output_path, contain_tall_source=contain_tall_source):
            return ""
        relative = os.path.relpath(output_path, output_dir)
        return quote(Path(relative).as_posix(), safe="/:#?&=%")

    if not component:
        return ""

    if not _create_circular_spotlight_image(screenshot_path, component, output_path, contain_tall_source=contain_tall_source):
        return ""

    relative = os.path.relpath(output_path, output_dir)
    return quote(Path(relative).as_posix(), safe="/:#?&=%")
