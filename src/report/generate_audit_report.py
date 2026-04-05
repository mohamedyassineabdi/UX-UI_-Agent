from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
RESULTS_DIR = ROOT_DIR / "shared" / "output" / "results"
ASSETS_DIR = Path(__file__).resolve().parent / "site_assets"

DEFAULT_WEBSITE_MENU = GENERATED_DIR / "website_menu.json"
DEFAULT_CLEANED = GENERATED_DIR / "person_a_cleaned.json"
DEFAULT_RENDERED = GENERATED_DIR / "rendered_ui_extraction.json"
DEFAULT_CHECKS = GENERATED_DIR / "person_a_sheet_checks_v2.json"
DEFAULT_WORKBOOK = GENERATED_DIR / "UX-Audit-Workbook-final.xlsx"
DEFAULT_OUTPUT_DIR = GENERATED_DIR / "audit-report"
SPOTLIGHT_FRAME_WIDTH = 1920
SPOTLIGHT_FRAME_HEIGHT = 1080

MATCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "de", "des", "du", "en", "et",
    "for", "from", "how", "if", "in", "is", "la", "le", "les", "of", "on", "or",
    "that", "the", "this", "to", "un", "une", "with", "your", "you",
}

COMPONENT_PRIORITY_BY_SHEET = {
    "Navigation": ["navigation", "nav-link", "button", "link", "heading", "section"],
    "Forms": ["input", "button", "text-block", "card", "section"],
    "Feedback": ["dialog", "button", "text-block", "heading", "card", "section"],
    "Content": ["text-block", "heading", "section", "card", "link"],
    "Labeling": ["button", "link", "nav-link", "input", "heading", "text-block"],
}
NON_SPOTLIGHT_SHEETS = {"Presentation", "Interaction", "Visual hierarchy"}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_match_text(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9à-ÿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize_for_match(value: Any) -> List[str]:
    return [
        token
        for token in normalize_match_text(value).split()
        if token and token not in MATCH_STOPWORDS and len(token) >= 2
    ]


def normalize_path(raw_path: str) -> Path:
    normalized = raw_path.replace("\\", "/").strip()
    return Path(normalized)


def absolute_from_repo(raw_path: str) -> Optional[Path]:
    normalized = clean_text(raw_path)
    if not normalized:
        return None

    candidate = normalize_path(normalized)
    if candidate.is_absolute():
        return candidate
    return ROOT_DIR / candidate


def href_from_path(raw_path: str, output_dir: Path) -> str:
    absolute = absolute_from_repo(raw_path)
    if not absolute or not absolute.exists():
        return ""
    relative = os.path.relpath(absolute, output_dir)
    return quote(Path(relative).as_posix(), safe="/:#?&=%")


def component_text(component: Dict[str, Any]) -> str:
    bits = [
        component.get("text"),
        component.get("accessibleName"),
        component.get("label"),
        component.get("placeholder"),
        component.get("name"),
        component.get("href"),
        component.get("semanticType"),
        component.get("uxRole"),
        component.get("className"),
    ]
    return clean_text(" ".join(clean_text(bit) for bit in bits if clean_text(bit)))


def page_identity_keys(page: Dict[str, Any]) -> List[str]:
    keys = []
    for value in (
        page.get("name"),
        page.get("url"),
        page.get("finalUrl"),
        (page.get("pageMeta") or {}).get("data", {}).get("name"),
        (page.get("pageMeta") or {}).get("data", {}).get("url"),
        (page.get("pageMeta") or {}).get("data", {}).get("finalUrl"),
    ):
        normalized = normalize_match_text(value)
        if normalized and normalized not in keys:
            keys.append(normalized)
    return keys


def build_rendered_page_lookup(rendered_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for page in rendered_data.get("pages", []):
        for key in page_identity_keys(page):
            lookup[key] = page
    return lookup


def build_cleaned_page_lookup(cleaned_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for page in cleaned_data.get("pages", []):
        for key in page_identity_keys(page):
            lookup[key] = page
    return lookup


def iter_page_components(rendered_page: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    rendered_ui = rendered_page.get("renderedUi") or {}
    components = rendered_ui.get("components") or {}
    for bucket_name, items in components.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            rect = item.get("rect") or {}
            if rect.get("width") in (None, 0) or rect.get("height") in (None, 0):
                continue
            merged = dict(item)
            merged["_bucket"] = bucket_name
            merged["_componentText"] = component_text(item)
            yield merged


def candidate_score(component: Dict[str, Any], phrases: List[str], phrase_tokens: List[str], preferred_types: List[str]) -> float:
    text = normalize_match_text(component.get("_componentText"))
    if not text:
        return 0.0

    tokens = set(tokenize_for_match(text))
    score = 0.0

    for phrase in phrases:
        normalized_phrase = normalize_match_text(phrase)
        if not normalized_phrase or len(normalized_phrase) < 4:
            continue
        if normalized_phrase in text:
            score += min(18.0, 6.0 + len(normalized_phrase) / 18.0)
        elif text in normalized_phrase and len(text) >= 4:
            score += min(12.0, 4.0 + len(text) / 24.0)

    if phrase_tokens and tokens:
        overlap = len(tokens.intersection(phrase_tokens))
        score += overlap * 2.8
        score += (overlap / max(len(phrase_tokens), 1)) * 8.0

    semantic = normalize_match_text(component.get("semanticType"))
    bucket = normalize_match_text(component.get("_bucket"))
    ux_role = normalize_match_text(component.get("uxRole"))
    combined_type = " ".join(part for part in (semantic, bucket, ux_role) if part)

    for index, preferred in enumerate(preferred_types):
        if preferred in combined_type:
            score += max(1.0, 4.5 - index * 0.45)
            break

    if component.get("visible") is True:
        score += 1.2
    if component.get("isAboveTheFold"):
        score += 0.6

    return score


def pick_best_component(
    item: Dict[str, Any],
    rendered_page: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not rendered_page:
        return None

    phrases = []
    phrases.extend(item.get("evidence") or [])
    if clean_text(item.get("rationale")):
        phrases.append(item["rationale"])
    if clean_text(item.get("criterion")):
        phrases.append(item["criterion"])

    phrase_tokens: List[str] = []
    for phrase in phrases:
        phrase_tokens.extend(tokenize_for_match(phrase))
    preferred_types = COMPONENT_PRIORITY_BY_SHEET.get(item.get("sheet"), ["text-block", "heading", "button", "link", "section"])

    best_component = None
    best_score = 0.0
    for component in iter_page_components(rendered_page):
        score = candidate_score(component, phrases, phrase_tokens, preferred_types)
        if score > best_score:
            best_score = score
            best_component = component

    if best_component and best_score >= 4.0:
        return best_component

    for preferred in preferred_types:
        for component in iter_page_components(rendered_page):
            combined_type = " ".join(
                normalize_match_text(component.get(key))
                for key in ("semanticType", "_bucket", "uxRole")
                if normalize_match_text(component.get(key))
            )
            if preferred in combined_type:
                return component

    return None


def create_spotlight_image(
    screenshot_path: str,
    component: Optional[Dict[str, Any]],
    output_path: Path,
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

        pad_x = max(140, int(width * 0.75))
        pad_y = max(140, int(height * 0.85))

        left = max(0, int(x - pad_x))
        top = max(0, int(y - pad_y))
        right = min(image_width, int(x + width + pad_x))
        bottom = min(image_height, int(y + height + pad_y))

        crop = source.crop((left, top, right, bottom))
        draw = ImageDraw.Draw(crop, "RGBA")

        rect_left = int(x - left)
        rect_top = int(y - top)
        rect_right = int(x - left + width)
        rect_bottom = int(y - top + height)

        halo = 14
        draw.rounded_rectangle(
            (rect_left - halo, rect_top - halo, rect_right + halo, rect_bottom + halo),
            radius=26,
            outline=(255, 63, 63, 170),
            width=10,
        )
        draw.rounded_rectangle(
            (rect_left, rect_top, rect_right, rect_bottom),
            radius=20,
            outline=(255, 36, 36, 255),
            width=6,
        )

        target_width = min(1100, crop.width)
        if crop.width > target_width:
            scale = target_width / crop.width
            crop = crop.resize((target_width, max(1, int(crop.height * scale))))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        crop.convert("RGB").save(output_path, format="PNG", optimize=True)
        return True


    return False


def build_spotlight_payload(
    item: Dict[str, Any],
    output_dir: Path,
    rendered_lookup: Dict[str, Dict[str, Any]],
    cleaned_lookup: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if clean_text(item.get("sheet")) in NON_SPOTLIGHT_SHEETS:
        return None

    screenshot_raw = clean_text(item.get("screenshotRaw"))
    if not screenshot_raw:
        return None

    page_key_candidates = [
        normalize_match_text(item.get("pageUrl")),
        normalize_match_text(item.get("pageName")),
    ]
    rendered_page = None
    cleaned_page = None
    for key in page_key_candidates:
        if key and key in rendered_lookup:
            rendered_page = rendered_lookup[key]
        if key and key in cleaned_lookup:
            cleaned_page = cleaned_lookup[key]
        if rendered_page and cleaned_page:
            break

    component = pick_best_component(item, rendered_page)
    if not component:
        return None

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

    viewport = ((cleaned_page or {}).get("pageMeta") or {}).get("data", {}).get("viewport", {}) or {}
    viewport_width = safe_int(viewport.get("width"), 1440)
    target_ratio = SPOTLIGHT_FRAME_WIDTH / SPOTLIGHT_FRAME_HEIGHT

    crop_width = max(int(width * 2.8), int(height * target_ratio * 1.55), 960)
    if viewport_width:
        crop_width = min(crop_width, viewport_width)
    crop_height = int(round(crop_width / target_ratio))

    center_x = x + width / 2
    center_y = y + height / 2

    crop_x = int(round(center_x - crop_width / 2))
    crop_y = int(round(center_y - crop_height / 2))

    if viewport_width:
        crop_x = max(0, min(crop_x, viewport_width - crop_width))
    else:
        crop_x = max(0, crop_x)
    crop_y = max(0, crop_y)

    slug = re.sub(r"[^a-z0-9]+", "-", normalize_match_text(item.get("criterion") or item.get("pageName") or "evidence")).strip("-")
    filename = f"{normalize_match_text(item.get('sheet') or 'sheet')}-{safe_int(item.get('row')):02d}-{slug[:60] or 'evidence'}.png"
    output_path = output_dir / "evidence" / filename

    evidence_shot = ""
    if create_spotlight_image(screenshot_raw, component, output_path):
        relative = os.path.relpath(output_path, output_dir)
        evidence_shot = quote(Path(relative).as_posix(), safe="/:#?&=%")

    return {
        "image": item.get("screenshot", ""),
        "fullImage": item.get("screenshot", ""),
        "preRenderedImage": evidence_shot,
        "crop": {
            "x": crop_x,
            "y": crop_y,
            "width": crop_width,
            "height": crop_height,
        },
        "highlight": {
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        },
        "frame": {
            "width": SPOTLIGHT_FRAME_WIDTH,
            "height": SPOTLIGHT_FRAME_HEIGHT,
        },
    }


def load_latest_results(results_dir: Path) -> Optional[Dict[str, Any]]:
    candidates = sorted(results_dir.glob("audit-results_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return load_json(candidates[0])


def format_iso_timestamp(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return clean_text(value)
    return dt.strftime("%B %d, %Y at %H:%M")


def derive_site_title(homepage: str) -> str:
    host = urlparse(homepage).netloc.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "UX/UI Audit"
    return f"{host} UX/UI Audit"


def derive_site_display_name(homepage: str, cleaned_data: Dict[str, Any]) -> str:
    for page in cleaned_data.get("pages", []):
        meta = ((page.get("pageMeta") or {}).get("data") or {})
        if clean_text(meta.get("sourceType")).lower() != "homepage":
            continue
        title = clean_text(meta.get("title"))
        if not title:
            continue
        candidate = re.split(r"\s*[\-|–|—|:|•|·]\s*", title, maxsplit=1)[0]
        candidate = clean_text(candidate)
        if candidate:
            return candidate

    host = urlparse(homepage).netloc.strip()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "Client site"
    base = host.split(".")[0].replace("-", " ").replace("_", " ")
    return clean_text(base.title()) or host


def derive_site_logo(cleaned_data: Dict[str, Any]) -> str:
    candidates: List[tuple[int, int, int, str]] = []

    for page in cleaned_data.get("pages", []):
        meta = ((page.get("pageMeta") or {}).get("data") or {})
        page_priority = 0 if clean_text(meta.get("sourceType")).lower() == "homepage" else 1
        media = ((page.get("media") or {}).get("data") or {})
        for image in media.get("images") or []:
            if not isinstance(image, dict):
                continue
            src = clean_text(image.get("src"))
            if not src:
                continue
            alt = clean_text(image.get("alt")).lower()
            src_lower = src.lower()
            width = safe_int(image.get("width"))
            height = safe_int(image.get("height"))
            area = width * height

            match_score = 0
            if "logo" in src_lower:
                match_score += 8
            if "brand" in src_lower:
                match_score += 4
            if "logo" in alt:
                match_score += 6
            if page_priority == 0:
                match_score += 5
            if width and height:
                ratio = width / max(height, 1)
                if 1.4 <= ratio <= 5.5:
                    match_score += 3
                if width >= 120 and height >= 36:
                    match_score += 2

            if match_score > 0:
                candidates.append((page_priority, -match_score, -area, src))

    if not candidates:
        return ""

    candidates.sort()
    return candidates[0][3]


def derive_severity(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


def evidence_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    out: List[str] = []
    seen = set()
    for item in items:
        cleaned = clean_text(item)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def build_source_pages(source_pages: Iterable[Dict[str, Any]], output_dir: Path) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for page in source_pages or []:
        if not isinstance(page, dict):
            continue
        out.append(
            {
                "name": clean_text(page.get("page_name") or page.get("page_id")),
                "url": clean_text(page.get("page_url") or page.get("final_url")),
                "screenshot": href_from_path(clean_text(page.get("screenshot_path")), output_dir),
            }
        )
    return out


def build_check_item(
    item: Dict[str, Any],
    sheet_name: str,
    output_dir: Path,
    rendered_lookup: Dict[str, Dict[str, Any]],
    cleaned_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    status = clean_text(item.get("status")).upper() or "N/A"
    confidence = safe_float(item.get("confidence"))
    screenshot_raw = clean_text(item.get("screenshot_path"))
    page_name = clean_text(item.get("page_name"))
    page_url = clean_text(item.get("page_url") or item.get("final_url"))
    check_item = {
        "sheet": sheet_name,
        "row": safe_int(item.get("row")),
        "criterion": clean_text(item.get("criterion")),
        "status": status,
        "confidence": round(confidence, 2),
        "confidencePercent": int(round(confidence * 100)),
        "severity": derive_severity(confidence) if status == "FALSE" else "info",
        "rationale": clean_text(item.get("rationale")),
        "decisionBasis": clean_text(item.get("decision_basis")),
        "evidence": evidence_list(item.get("evidence")),
        "pageName": page_name,
        "pageUrl": page_url,
        "screenshot": href_from_path(screenshot_raw, output_dir),
        "screenshotRaw": screenshot_raw,
        "sourcePages": build_source_pages(item.get("source_pages") or [], output_dir),
    }
    spotlight = build_spotlight_payload(check_item, output_dir, rendered_lookup, cleaned_lookup)
    check_item["spotlight"] = spotlight
    check_item["evidenceShot"] = (spotlight or {}).get("preRenderedImage", "")
    return check_item


def build_sheet_summary(
    sheet_name: str,
    sheet_payload: Dict[str, Any],
    output_dir: Path,
    rendered_lookup: Dict[str, Dict[str, Any]],
    cleaned_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    raw_items = [
        build_check_item(item, sheet_name, output_dir, rendered_lookup, cleaned_lookup)
        for item in sheet_payload.get("results", [])
    ]
    passed = [item for item in raw_items if item["status"] == "TRUE"]
    failed = [item for item in raw_items if item["status"] == "FALSE"]
    na_items = [item for item in raw_items if item["status"] not in {"TRUE", "FALSE"}]
    denominator = len(passed) + len(failed)
    score = int(round((len(passed) / denominator) * 100)) if denominator else 0

    failed.sort(key=lambda item: (-item["confidence"], item["row"]))
    passed.sort(key=lambda item: (-item["confidence"], item["row"]))
    na_items.sort(key=lambda item: (item["row"], item["criterion"]))

    return {
        "name": sheet_name,
        "total": len(raw_items),
        "passed": len(passed),
        "failed": len(failed),
        "na": len(na_items),
        "score": score,
        "findings": failed,
        "strengths": passed,
        "openQuestions": na_items,
    }


def build_pages(cleaned_data: Dict[str, Any], rendered_data: Dict[str, Any], output_dir: Path) -> List[Dict[str, Any]]:
    rendered_by_url: Dict[str, Dict[str, Any]] = {}
    for page in rendered_data.get("pages", []):
        key = clean_text(page.get("finalUrl") or page.get("url"))
        if key:
            rendered_by_url[key] = page

    pages: List[Dict[str, Any]] = []
    for page in cleaned_data.get("pages", []):
        meta = (page.get("pageMeta") or {}).get("data") or {}
        screenshot_paths = meta.get("screenshotPaths") or {}
        final_url = clean_text(page.get("finalUrl") or meta.get("finalUrl") or page.get("url"))
        rendered = rendered_by_url.get(final_url, {})
        rendered_ui = rendered.get("renderedUi") or {}
        consistency = rendered_ui.get("consistencyMetrics") or {}
        pages.append(
            {
                "name": clean_text(page.get("name") or meta.get("name")),
                "title": clean_text(meta.get("title")),
                "url": clean_text(page.get("url") or meta.get("url")),
                "finalUrl": final_url,
                "language": clean_text(meta.get("language")),
                "screenshot": href_from_path(clean_text(screenshot_paths.get("page")), output_dir),
                "scrolls": [href_from_path(clean_text(item), output_dir) for item in screenshot_paths.get("scrolls", []) if clean_text(item)],
                "navigationPath": [clean_text(item) for item in meta.get("navigationPath", []) if clean_text(item)],
                "pageTypeClues": [clean_text(item) for item in meta.get("pageTypeClues", []) if clean_text(item)],
                "forms": safe_int(meta.get("documentMetrics", {}).get("forms")),
                "images": safe_int(meta.get("documentMetrics", {}).get("images")),
                "links": safe_int(meta.get("documentMetrics", {}).get("links")),
                "designHealth": safe_int(consistency.get("overallDesignSystemHealth")),
                "componentConsistency": safe_int(consistency.get("componentConsistency")),
            }
        )

    return pages


def collect_navigation_items(items: Iterable[Dict[str, Any]]) -> int:
    count = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        count += 1
        count += collect_navigation_items(item.get("children") or [])
    return count


def build_navigation_tree(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "name": clean_text(item.get("name")),
                "type": clean_text(item.get("type") or "link"),
                "url": clean_text(item.get("url")),
                "children": build_navigation_tree(item.get("children") or []),
            }
        )
    return out


def build_visual_summary(rendered_data: Dict[str, Any]) -> Dict[str, Any]:
    font_families = set()
    text_colors = set()
    backgrounds = set()
    health_scores: List[float] = []
    consistency_scores: List[float] = []

    for page in rendered_data.get("pages", []):
        rendered_ui = page.get("renderedUi") or {}
        design = rendered_ui.get("designSummary") or {}
        typography = design.get("typography", {})
        colors = design.get("colors", {})
        metrics = rendered_ui.get("consistencyMetrics") or {}

        font_families.update(clean_text(item) for item in typography.get("fontFamilies", []) if clean_text(item))
        text_colors.update(clean_text(item) for item in colors.get("text", []) if clean_text(item))
        backgrounds.update(clean_text(item) for item in colors.get("backgrounds", []) if clean_text(item))

        if metrics.get("overallDesignSystemHealth") is not None:
            health_scores.append(safe_float(metrics.get("overallDesignSystemHealth")))
        if metrics.get("componentConsistency") is not None:
            consistency_scores.append(safe_float(metrics.get("componentConsistency")))

    return {
        "fontFamilies": sorted(font_families)[:6],
        "textColors": sorted(text_colors)[:6],
        "backgrounds": sorted(backgrounds)[:6],
        "designHealth": int(round(mean(health_scores))) if health_scores else 0,
        "componentConsistency": int(round(mean(consistency_scores))) if consistency_scores else 0,
    }


def build_methodology() -> List[Dict[str, str]]:
    return [
        {
            "step": "Navigation Crawl",
            "description": "The crawler mapped primary navigation, subcategories, auth links, and search entry points from the homepage, with AI assistance for weak or hidden menus.",
            "outputs": "website_menu.json",
        },
        {
            "step": "Page Audit",
            "description": "Each discovered page was visited in Playwright, screens were captured, visible clickables were classified, and safe interactions were tested.",
            "outputs": "screenshots, audit-results_*.json",
        },
        {
            "step": "Structured Extraction",
            "description": "Page structure, headings, navigation, forms, media, and rendered UI signals were normalized into machine-readable audit inputs.",
            "outputs": "person_a_cleaned.json, rendered_ui_extraction.json",
        },
        {
            "step": "Checks Engine",
            "description": "The audit rules in Content, Labeling, Navigation, Feedback, and Forms produced scored TRUE / FALSE / N/A decisions with rationale and evidence.",
            "outputs": "person_a_sheet_checks_v2.json, workbook",
        },
    ]


def build_executive_summary(site_title: str, sheet_summaries: List[Dict[str, Any]], overall_score: int) -> str:
    failures = sum(sheet["failed"] for sheet in sheet_summaries)
    strongest = max(sheet_summaries, key=lambda sheet: sheet["score"], default=None)
    weakest = min(
        [sheet for sheet in sheet_summaries if sheet["total"]],
        key=lambda sheet: sheet["score"],
        default=None,
    )

    fragments = [f"{site_title} scored {overall_score}/100 across the automated UX/UI audit checks."]
    if failures:
        fragments.append(f"The audit surfaced {failures} concrete friction points that deserve design or content follow-up.")
    if strongest:
        fragments.append(f"The strongest dimension in this run was {strongest['name'].lower()} ({strongest['score']}/100).")
    if weakest:
        fragments.append(f"The weakest dimension was {weakest['name'].lower()} ({weakest['score']}/100).")
    return " ".join(fragments)


def build_artifact_links(
    output_dir: Path,
    website_menu_path: Path,
    cleaned_path: Path,
    rendered_path: Path,
    checks_path: Path,
    workbook_path: Optional[Path],
) -> Dict[str, str]:
    links = {
        "checksJson": href_from_path(os.path.relpath(checks_path, ROOT_DIR), output_dir),
        "websiteMenu": href_from_path(os.path.relpath(website_menu_path, ROOT_DIR), output_dir),
        "cleanedJson": href_from_path(os.path.relpath(cleaned_path, ROOT_DIR), output_dir),
        "renderedJson": href_from_path(os.path.relpath(rendered_path, ROOT_DIR), output_dir),
    }
    if workbook_path and workbook_path.exists():
        links["workbook"] = href_from_path(os.path.relpath(workbook_path, ROOT_DIR), output_dir)
    else:
        links["workbook"] = ""
    return links


def copy_assets(output_dir: Path) -> None:
    for asset_name in ("styles.css", "app.js"):
        shutil.copyfile(ASSETS_DIR / asset_name, output_dir / asset_name)


def build_report_data(
    *,
    website_menu: Dict[str, Any],
    cleaned_data: Dict[str, Any],
    rendered_data: Dict[str, Any],
    checks_data: Dict[str, Any],
    results_data: Optional[Dict[str, Any]],
    output_dir: Path,
    website_menu_path: Path,
    cleaned_path: Path,
    rendered_path: Path,
    checks_path: Path,
    workbook_path: Optional[Path],
) -> Dict[str, Any]:
    homepage = clean_text(website_menu.get("homepage"))
    site_title = derive_site_title(homepage)
    site_display_name = derive_site_display_name(homepage, cleaned_data)
    site_logo = derive_site_logo(cleaned_data)
    domain = urlparse(homepage).netloc or homepage
    rendered_lookup = build_rendered_page_lookup(rendered_data)
    cleaned_lookup = build_cleaned_page_lookup(cleaned_data)

    sheet_summaries = [
        build_sheet_summary(sheet_name, sheet_payload, output_dir, rendered_lookup, cleaned_lookup)
        for sheet_name, sheet_payload in checks_data.get("sheets", {}).items()
    ]
    sheet_summaries.sort(key=lambda sheet: sheet["name"])

    total_passed = sum(sheet["passed"] for sheet in sheet_summaries)
    total_failed = sum(sheet["failed"] for sheet in sheet_summaries)
    total_na = sum(sheet["na"] for sheet in sheet_summaries)
    denominator = total_passed + total_failed
    overall_score = int(round((total_passed / denominator) * 100)) if denominator else 0

    all_findings = []
    all_strengths = []
    for sheet in sheet_summaries:
        all_findings.extend(sheet["findings"])
        all_strengths.extend(sheet["strengths"])

    all_findings.sort(key=lambda item: ({"high": 0, "medium": 1, "low": 2}.get(item["severity"], 3), -item["confidence"], item["sheet"], item["row"]))
    all_strengths.sort(key=lambda item: (-item["confidence"], item["sheet"], item["row"]))

    pages = build_pages(cleaned_data, rendered_data, output_dir)
    navigation = build_navigation_tree(website_menu.get("navigation") or [])
    results_summary = (results_data or {}).get("summary") or {}
    generated_at = format_iso_timestamp(clean_text(results_summary.get("runFinishedAt"))) if results_summary else datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    return {
        "site": {
            "title": site_title,
            "displayName": site_display_name,
            "domain": domain,
            "homepage": homepage,
            "logo": site_logo,
            "language": clean_text(website_menu.get("language") or website_menu.get("requested_language")),
            "generatedAt": generated_at,
        },
        "summary": {
            "overallScore": overall_score,
            "passed": total_passed,
            "failed": total_failed,
            "notApplicable": total_na,
            "totalChecks": total_passed + total_failed + total_na,
            "pagesAudited": len(pages),
            "navigationItems": collect_navigation_items(website_menu.get("navigation") or []),
            "topLevelNavigation": len(website_menu.get("navigation") or []),
            "interactionsTested": safe_int(results_summary.get("testedInteractions")),
            "screenshotsCreated": safe_int(results_summary.get("interactionScreenshotsCreated")) + len(pages),
            "successPages": safe_int(results_summary.get("pagesSucceeded"), len(pages)),
            "failurePages": safe_int(results_summary.get("pagesFailed")),
        },
        "executiveSummary": build_executive_summary(site_title, sheet_summaries, overall_score),
        "sheets": sheet_summaries,
        "topFindings": all_findings[:10],
        "topStrengths": all_strengths[:8],
        "pages": pages,
        "navigation": navigation,
        "visualSummary": build_visual_summary(rendered_data),
        "methodology": build_methodology(),
        "artifacts": build_artifact_links(
            output_dir,
            website_menu_path,
            cleaned_path,
            rendered_path,
            checks_path,
            workbook_path,
        ),
    }


def render_index_html(report_data: Dict[str, Any]) -> str:
    payload = json.dumps(report_data, ensure_ascii=False)
    title = report_data["site"]["title"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="Automated UX/UI audit report">
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <div class="report-shell">
    <header class="report-topbar">
      <a class="brand-suite" href="#hero" aria-label="Go to audit overview">
        <span class="ey-brand" aria-hidden="true">
          <span class="ey-slash"></span>
          <span class="ey-wordmark">
            <span class="ey-wordmark-ey">EY</span>
            <span class="ey-wordmark-studio">Studio<span class="ey-wordmark-plus">+</span></span>
          </span>
        </span>
        <span class="brand-divider" aria-hidden="true"></span>
        <span class="client-brand">
          <span id="client-brand-logo-wrap" class="client-brand-logo-wrap is-empty">
            <img id="client-brand-logo" class="client-brand-logo" alt="">
          </span>
          <span class="client-brand-copy">
            <span class="client-brand-label">Client site</span>
            <span id="client-brand-name" class="client-brand-name"></span>
          </span>
        </span>
      </a>
      <nav class="topnav">
        <a href="#overview">Overview</a>
        <a href="#findings">Findings</a>
        <a href="#strengths">Strengths</a>
        <a href="#pages">Pages</a>
        <a href="#navigation">Navigation</a>
        <a href="#process">Process</a>
      </nav>
    </header>

    <main>
      <section id="hero" class="hero-panel">
        <div class="hero-copy">
          <p class="eyebrow">Automated UX/UI Audit</p>
          <h1 id="hero-title"></h1>
          <p id="hero-summary" class="hero-summary"></p>
          <div id="hero-meta" class="hero-meta"></div>
          <div class="hero-actions">
            <a class="button primary" href="#findings">Review key findings</a>
            <a id="download-workbook" class="button ghost" href="#" target="_blank" rel="noreferrer">Download workbook</a>
          </div>
        </div>
        <div class="hero-side">
          <div class="score-card">
            <p class="score-label">Audit score</p>
            <p id="overall-score" class="score-value"></p>
          </div>
          <div id="hero-shot" class="hero-shot"></div>
        </div>
      </section>

      <section id="overview" class="section-block">
        <div class="section-head">
          <p class="eyebrow">At a glance</p>
          <h2>Audit scope and performance</h2>
        </div>
        <div id="stats-grid" class="stats-grid"></div>
        <div id="sheet-grid" class="sheet-grid"></div>
      </section>

      <section id="findings" class="section-block">
        <div class="section-head">
          <p class="eyebrow">Priority issues</p>
          <h2>What needs attention first</h2>
        </div>
        <div id="priority-findings" class="issue-grid"></div>
      </section>

      <section id="strengths" class="section-block">
        <div class="section-head">
          <p class="eyebrow">Strengths</p>
          <h2>Signals the product is already getting right</h2>
        </div>
        <div id="top-strengths" class="issue-grid compact"></div>
      </section>

      <section id="deep-dive" class="section-block">
        <div class="section-head">
          <p class="eyebrow">Deep dive</p>
          <h2>Check-by-check review by audit dimension</h2>
        </div>
        <div id="sheet-sections" class="sheet-sections"></div>
      </section>

      <section id="pages" class="section-block">
        <div class="section-head">
          <p class="eyebrow">Page library</p>
          <h2>Audited screens and captured evidence</h2>
        </div>
        <div id="page-gallery" class="page-gallery"></div>
      </section>

      <section id="navigation" class="section-block">
        <div class="section-head">
          <p class="eyebrow">Site map</p>
          <h2>Navigation structure captured by the crawler</h2>
        </div>
        <div id="navigation-tree" class="navigation-tree"></div>
      </section>

      <section id="process" class="section-block">
        <div class="section-head">
          <p class="eyebrow">Methodology</p>
          <h2>Visual system snapshot</h2>
        </div>
        <div id="visual-summary" class="visual-summary"></div>
      </section>
    </main>

    <footer class="report-footer">
      <p>Generated from the crawler, page audit, checks engine, workbook export, and screenshot evidence.</p>
    </footer>
  </div>

  <dialog id="lightbox" class="lightbox">
    <button id="lightbox-close" class="lightbox-close" aria-label="Close image">Close</button>
    <img id="lightbox-image" alt="">
  </dialog>

  <script id="report-data" type="application/json">{payload}</script>
  <script src="./app.js"></script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a static audit landing page from the audit artifacts.")
    parser.add_argument("--website-menu", default=str(DEFAULT_WEBSITE_MENU), help="Path to website_menu.json")
    parser.add_argument("--cleaned", default=str(DEFAULT_CLEANED), help="Path to person_a_cleaned.json")
    parser.add_argument("--rendered", default=str(DEFAULT_RENDERED), help="Path to rendered_ui_extraction.json")
    parser.add_argument("--checks", default=str(DEFAULT_CHECKS), help="Path to person_a_sheet_checks_v2.json")
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK), help="Optional workbook path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for the generated report site")
    return parser.parse_args()


def to_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT_DIR / path


def main() -> None:
    args = parse_args()

    website_menu_path = to_path(args.website_menu)
    cleaned_path = to_path(args.cleaned)
    rendered_path = to_path(args.rendered)
    checks_path = to_path(args.checks)
    workbook_path = to_path(args.workbook) if clean_text(args.workbook) else None
    output_dir = to_path(args.output_dir)

    for required in (website_menu_path, cleaned_path, rendered_path, checks_path):
        if not required.exists():
            raise FileNotFoundError(f"Required input file not found: {required}")

    ensure_dir(output_dir)

    website_menu = load_json(website_menu_path)
    cleaned_data = load_json(cleaned_path)
    rendered_data = load_json(rendered_path)
    checks_data = load_json(checks_path)
    results_data = load_latest_results(RESULTS_DIR)

    report_data = build_report_data(
        website_menu=website_menu,
        cleaned_data=cleaned_data,
        rendered_data=rendered_data,
        checks_data=checks_data,
        results_data=results_data,
        output_dir=output_dir,
        website_menu_path=website_menu_path,
        cleaned_path=cleaned_path,
        rendered_path=rendered_path,
        checks_path=checks_path,
        workbook_path=workbook_path,
    )

    copy_assets(output_dir)
    (output_dir / "index.html").write_text(render_index_html(report_data), encoding="utf-8")

    print(f"Audit report generated at: {output_dir / 'index.html'}")


if __name__ == "__main__":
    main()
