from __future__ import annotations

import os
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


def _spotlight_review_enabled() -> bool:
    return clean_text(getenv("GTM_VISION_VERIFY_SPOTLIGHTS", DEFAULT_SPOTLIGHT_REVIEW)).lower() not in {"0", "false", "no", "off"}


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


def _create_circular_spotlight_image(
    screenshot_path: str,
    component: Optional[Dict[str, Any]],
    output_path: Path,
    label: str = "",
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

    target_ratio = SPOTLIGHT_FRAME_WIDTH / SPOTLIGHT_FRAME_HEIGHT

    with Image.open(absolute) as source:
        source = source.convert("RGBA")
        image_width, image_height = source.size

        pad_x = max(180, int(width * 1.0))
        pad_y = max(180, int(height * 1.0))

        crop_width = max(int(width + pad_x * 2), 960)
        crop_height = int(round(crop_width / target_ratio))
        if crop_height < height + pad_y * 2:
            crop_height = int(height + pad_y * 2)
            crop_width = int(round(crop_height * target_ratio))

        crop_width = min(crop_width, image_width)
        crop_height = min(crop_height, image_height)

        center_x = x + width / 2
        center_y = y + height / 2

        left = int(round(center_x - crop_width / 2))
        top = int(round(center_y - crop_height / 2))
        left = max(0, min(left, image_width - crop_width))
        top = max(0, min(top, image_height - crop_height))
        right = left + crop_width
        bottom = top + crop_height

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
        draw.ellipse(
            (
                comp_left - halo,
                comp_top - halo,
                comp_right + halo,
                comp_bottom + halo,
            ),
            outline=(255, 52, 52, 240),
            width=12,
        )
        draw.ellipse(
            (
                comp_left - halo - 10,
                comp_top - halo - 10,
                comp_right + halo + 10,
                comp_bottom + halo + 10,
            ),
            outline=(255, 52, 52, 110),
            width=18,
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

    component = _pick_gtm_component(item, rendered_page)
    bundle_component = _evidence_bundle_component(item)
    if bundle_component:
        component = bundle_component

    if not component:
        return ""

    filename_stem = f"issue-{str(issue_index).zfill(2)}-{normalize_match_text(item.get('title') or 'issue')[:60].replace(' ', '-')}"
    if not bundle_component:
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

    filename = f"{filename_stem}.png"
    output_path = output_dir / "evidence" / filename
    bundle_target = ((item.get("evidenceBundle") or {}).get("target") or {}) if isinstance(item.get("evidenceBundle"), dict) else {}
    screenshot_path = clean_text(bundle_target.get("screenshot_path")) or clean_text(item.get("screenshotPath"))
    if not _create_circular_spotlight_image(screenshot_path, component, output_path):
        return ""

    relative = os.path.relpath(output_path, output_dir)
    return quote(Path(relative).as_posix(), safe="/:#?&=%")
