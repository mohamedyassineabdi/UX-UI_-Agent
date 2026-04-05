# src/audit/checks/visual_hierarchy_checks.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple, DefaultDict
from collections import Counter, defaultdict
import math
import re
from .ai_review_layer import review_page_criterion_with_ai
from .ai_reconciliation import (
    should_run_ai_review,
    reconcile_deterministic_and_ai,
    has_suspicious_metrics,
)
# ============================================================
# Constants
# ============================================================

CATEGORY = "visual_hierarchy"

STATUS_PASS = "pass"
STATUS_WARNING = "warning"
STATUS_FAIL = "fail"
STATUS_NA = "not_applicable"

SEVERITY_WARNING = "warning"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"

_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)"
    r"(?:\s*,\s*(\d+(?:\.\d+)?))?\s*\)",
    re.I,
)
_HEX_RE = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.I)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

TASK_KEYWORDS = {
    "buy", "shop", "add to cart", "cart", "checkout", "contact", "send",
    "search", "filter", "apply", "book", "start", "sign up", "subscribe",
    "continue", "next", "submit", "request", "demo", "pricing", "trial",
    "acheter", "panier", "commander", "contacter", "envoyer", "recherche",
    "filtrer", "continuer", "demander", "essai",
}

CONTENT_PAGE_HINTS = {
    "blog", "article", "news", "guide", "insight", "post", "documentation",
    "docs", "help", "support", "learn",
}

CATALOG_HINTS = {
    "shop", "product", "products", "store", "category", "categories",
    "catalog", "collection", "collections", "produit", "produits",
}

FORM_HINTS = {
    "contact", "support", "request", "signup", "sign-up", "register",
    "login", "checkout", "quote", "book",
}


@dataclass(frozen=True)
class ScoreBand:
    fail_below: float
    warning_below: float


CRITERION_BANDS: Dict[str, ScoreBand] = {
    "information-order-importance": ScoreBand(55, 72),
    "visual-hierarchy-reflects-priority": ScoreBand(55, 72),
    "required-action-direction": ScoreBand(55, 72),
    "cta-primary-visual-element": ScoreBand(58, 75),
    "visual-grouping-proximity-alignment": ScoreBand(55, 72),
    "negative-space-purpose": ScoreBand(55, 72),
    "similar-information-consistency": ScoreBand(58, 78),

    "ui-uses-no-more-than-3-primary-colors": ScoreBand(55, 75),
    "chrome-desaturated-colors": ScoreBand(55, 75),
    "colors-reinforce-hierarchy": ScoreBand(55, 72),
    "color-scheme-consistency": ScoreBand(58, 78),
    "no-oversaturated-colors": ScoreBand(55, 75),

    "most-important-items-have-most-contrast": ScoreBand(55, 72),
    "contrast-primary-mechanism-for-hierarchy": ScoreBand(48, 72),
    "contrast-separates-content-from-controls": ScoreBand(60, 78),
    "contrast-separates-labels-from-content": ScoreBand(55, 72),
    "foreground-distinguished-from-background": ScoreBand(70, 84),

    "no-more-than-two-font-families": ScoreBand(60, 78),
    "content-fonts-at-least-12px": ScoreBand(60, 78),
    "font-size-weight-differentiate-content-types": ScoreBand(58, 75),
    "font-consistency-across-screens": ScoreBand(58, 78),
    "fonts-reinforce-hierarchy": ScoreBand(58, 75),
    "fonts-separate-labels-from-content": ScoreBand(52, 70),
    "fonts-separate-content-from-controls": ScoreBand(52, 70),
}


@dataclass
class PageProfile:
    archetype: str
    expects_primary_cta: bool = False
    expects_top_action: bool = False
    expects_strong_heading: bool = True
    allows_dense_lists: bool = False
    stronger_content_structure: bool = False


PAGE_PROFILES: Dict[str, PageProfile] = {
    "home": PageProfile(
        archetype="home",
        expects_primary_cta=False,
        expects_top_action=False,
        expects_strong_heading=True,
        allows_dense_lists=False,
        stronger_content_structure=True,
    ),
    "content": PageProfile(
        archetype="content",
        expects_primary_cta=False,
        expects_top_action=False,
        expects_strong_heading=True,
        allows_dense_lists=False,
        stronger_content_structure=True,
    ),
    "catalog": PageProfile(
        archetype="catalog",
        expects_primary_cta=False,
        expects_top_action=True,
        expects_strong_heading=True,
        allows_dense_lists=True,
        stronger_content_structure=True,
    ),
    "task": PageProfile(
        archetype="task",
        expects_primary_cta=True,
        expects_top_action=True,
        expects_strong_heading=True,
        allows_dense_lists=False,
        stronger_content_structure=False,
    ),
    "conversion": PageProfile(
        archetype="conversion",
        expects_primary_cta=True,
        expects_top_action=True,
        expects_strong_heading=True,
        allows_dense_lists=False,
        stronger_content_structure=False,
    ),
    "generic": PageProfile(
        archetype="generic",
        expects_primary_cta=False,
        expects_top_action=False,
        expects_strong_heading=True,
        allows_dense_lists=False,
        stronger_content_structure=False,
    ),
}


# ============================================================
# Generic helpers
# ============================================================

def _safe_get(d: Dict[str, Any], *keys: str, default=None):
    current: Any = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _text(v: Any) -> str:
    return str(v or "").strip()


def _lower(v: Any) -> str:
    return _text(v).lower()


def _parse_float(v: Any) -> Optional[float]:
    s = _text(v)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        match = _NUMBER_RE.search(s)
        if not match:
            return None
        try:
            return float(match.group(0))
        except Exception:
            return None


def _parse_int(v: Any) -> Optional[int]:
    f = _parse_float(v)
    if f is None:
        return None
    try:
        return int(round(f))
    except Exception:
        return None


def _parse_px(v: Any) -> Optional[float]:
    s = _lower(v)
    if not s:
        return None
    if "px" in s:
        return _parse_float(s.replace("px", "").strip())
    return None


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def _mean(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _median(values: Iterable[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    m = n // 2
    if n % 2 == 1:
        return vals[m]
    return (vals[m - 1] + vals[m]) / 2.0


def _ratio(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def _normalize_score(v: Optional[float], min_v: float, max_v: float) -> float:
    if v is None:
        return 0.0
    if max_v <= min_v:
        return 0.0
    return _clamp((v - min_v) / (max_v - min_v), 0.0, 1.0)


def _page_ref(page: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": page.get("name", ""),
        "url": page.get("url", ""),
        "finalUrl": page.get("finalUrl", page.get("url", "")),
    }


def _issue_pages_refs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        ref = {
            "name": item.get("name", ""),
            "url": item.get("url", ""),
            "finalUrl": item.get("finalUrl", item.get("url", "")),
        }
        key = (ref["name"], ref["url"], ref["finalUrl"])
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def _band_to_status(score: Optional[float], criterion: str) -> str:
    if score is None:
        return STATUS_NA
    band = CRITERION_BANDS[criterion]
    if score < band.fail_below:
        return STATUS_FAIL
    if score < band.warning_below:
        return STATUS_WARNING
    return STATUS_PASS


def _status_severity(status: str) -> Optional[str]:
    if status == STATUS_FAIL:
        return SEVERITY_MEDIUM
    if status == STATUS_WARNING:
        return SEVERITY_WARNING
    return None


def _confidence_from_coverage(coverage: float) -> str:
    if coverage >= 0.75:
        return "high"
    if coverage >= 0.45:
        return "medium"
    return "low"


def _make_result(
    *,
    criterion: str,
    status: str,
    title: str,
    description: str,
    pages: List[Dict[str, Any]],
    severity: Optional[str] = None,
    recommendation: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    confidence: Optional[str] = None,
    method: Optional[List[str]] = None,
    score: Optional[float] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "category": CATEGORY,
        "criterion": criterion,
        "status": status,
        "severity": severity,
        "title": title,
        "description": description,
        "pages": pages,
        "recommendation": recommendation,
    }
    if evidence is not None:
        result["evidence"] = evidence
    if confidence is not None:
        result["confidence"] = confidence
    if method is not None:
        result["method"] = method
    if score is not None:
        result["score"] = round(score, 2)
    return result

def _apply_ai_review_to_page_items(
    criterion: str,
    page_items: list[dict],
    page_summaries: list,
) -> list[dict]:
    summary_map = {
        (p.page_ref["name"], p.page_ref["url"]): p
        for p in page_summaries
    }

    reviewed_items: list[dict] = []

    for item in page_items:
        enriched = dict(item)
        enriched["criterion"] = criterion
        if item.get("status") == "pass" and isinstance(item.get("score"), (int, float)) and item["score"] >= 85:
            reviewed_items.append(enriched)
            continue
        if not should_run_ai_review(enriched):
            reviewed_items.append(enriched)
            continue

        summary = summary_map.get((item["name"], item["url"]))
        if not summary:
            reviewed_items.append(enriched)
            continue

        extracted_summary = {
            "top_elements_count": len(summary.top_elements),
            "heading_count": len(summary.headings),
            "content_count": len(summary.content),
            "control_count": len(summary.controls),
            "cta_count": len(summary.ctas),
            "labels_count": len(summary.labels),
            "dominant_color_clusters": summary.dominant_color_clusters[:5],
            "neutral_color_clusters": summary.neutral_color_clusters[:5],
            "typography": {
                "heading_size_med": summary.typography.heading_size_med,
                "content_size_med": summary.typography.content_size_med,
                "control_size_med": summary.typography.control_size_med,
                "label_size_med": summary.typography.label_size_med,
            },
            "prominence": {
                "primary_heading": summary.primary_heading_prominence,
                "primary_cta": summary.primary_cta_prominence,
                "primary_control": summary.primary_control_prominence,
            },
        }

        ai_result = review_page_criterion_with_ai(
            criterion=criterion,
            page_name=item["name"],
            page_url=item["url"],
            page_type=item.get("archetype", "generic"),
            deterministic_result={
                "status": item.get("status"),
                "score": item.get("score"),
                "details": item.get("details"),
            },
            page_metrics=item.get("metrics") or {},
            extracted_summary=extracted_summary,
        )

        reconciliation = reconcile_deterministic_and_ai(
            deterministic_status=item.get("status", "warning"),
            ai_verdict=ai_result.get("final_verdict", item.get("status", "warning")),
            deterministic_score=item.get("score"),
        )

        enriched["ai_review"] = ai_result
        enriched["status"] = reconciliation["final_status"]
        enriched["ai_reconciliation"] = reconciliation

        reviewed_items.append(enriched)

    return reviewed_items
# ============================================================
# Color helpers
# ============================================================

def _parse_color(value: Any) -> Optional[Tuple[int, int, int, float]]:
    text = _lower(value)
    if not text or text in {"transparent", "none", "inherit", "initial"}:
        return None

    rgb_match = _RGB_RE.match(text)
    if rgb_match:
        r = int(float(rgb_match.group(1)))
        g = int(float(rgb_match.group(2)))
        b = int(float(rgb_match.group(3)))
        a = float(rgb_match.group(4)) if rgb_match.group(4) is not None else 1.0
        return (r, g, b, a)

    hex_match = _HEX_RE.match(text)
    if hex_match:
        raw = hex_match.group(1)
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        try:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), 1.0)
        except Exception:
            return None

    return None


def _rgb_to_key(rgb: Tuple[int, int, int, float], ignore_alpha: bool = False) -> str:
    r, g, b, a = rgb
    if ignore_alpha or a >= 0.999:
        return f"rgb({r}, {g}, {b})"
    return f"rgba({r}, {g}, {b}, {round(a, 3)})"


def _relative_luminance(rgb: Tuple[int, int, int, float]) -> float:
    r, g, b, _ = rgb

    def ch(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    rs, gs, bs = ch(r), ch(g), ch(b)
    return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs


def _contrast_ratio(fg: Tuple[int, int, int, float], bg: Tuple[int, int, int, float]) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _rgb_to_hsl(rgb: Tuple[int, int, int, float]) -> Tuple[float, float, float]:
    r, g, b, _ = rgb
    r /= 255.0
    g /= 255.0
    b /= 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, l)

    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)

    if mx == r:
        h = ((g - b) / d + (6 if g < b else 0)) / 6.0
    elif mx == g:
        h = ((b - r) / d + 2) / 6.0
    else:
        h = ((r - g) / d + 4) / 6.0
    return (h, s, l)


def _color_distance(c1: Tuple[int, int, int, float], c2: Tuple[int, int, int, float]) -> float:
    h1, s1, l1 = _rgb_to_hsl(c1)
    h2, s2, l2 = _rgb_to_hsl(c2)
    dh = min(abs(h1 - h2), 1.0 - abs(h1 - h2))
    ds = abs(s1 - s2)
    dl = abs(l1 - l2)
    return math.sqrt((dh * 1.8) ** 2 + (ds * 1.2) ** 2 + (dl * 1.0) ** 2)


def _is_neutral(rgb: Tuple[int, int, int, float]) -> bool:
    _, s, _ = _rgb_to_hsl(rgb)
    return s < 0.12


def _cluster_colors(colors: List[Tuple[int, int, int, float]], threshold: float = 0.11) -> List[List[Tuple[int, int, int, float]]]:
    clusters: List[List[Tuple[int, int, int, float]]] = []
    for color in colors:
        placed = False
        for cluster in clusters:
            anchor = cluster[0]
            if _color_distance(color, anchor) <= threshold:
                cluster.append(color)
                placed = True
                break
        if not placed:
            clusters.append([color])
    return clusters


# ============================================================
# Element model
# ============================================================

@dataclass
class ElementModel:
    raw: Dict[str, Any]
    page_name: str
    page_url: str
    kind: str
    family: str
    text: str
    visible: bool
    above_fold: bool
    x: float
    y: float
    width: float
    height: float
    area: float
    dom_depth: int
    font_family: str
    font_size: Optional[float]
    font_weight: Optional[int]
    text_color: Optional[Tuple[int, int, int, float]]
    bg_color: Optional[Tuple[int, int, int, float]]
    contrast: Optional[float]
    landmark: str
    parent_display: str
    sibling_count: int
    prominence_score: float = 0.0

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass
class PairwiseTypographyStats:
    heading_size_med: Optional[float] = None
    heading_weight_med: Optional[float] = None
    content_size_med: Optional[float] = None
    content_weight_med: Optional[float] = None
    control_size_med: Optional[float] = None
    control_weight_med: Optional[float] = None
    label_size_med: Optional[float] = None
    label_weight_med: Optional[float] = None


@dataclass
class PageSummary:
    page_ref: Dict[str, Any]
    archetype: str
    profile: PageProfile
    all_elements: List[ElementModel]
    visible_elements: List[ElementModel]
    top_elements: List[ElementModel]
    headings: List[ElementModel]
    content: List[ElementModel]
    controls: List[ElementModel]
    ctas: List[ElementModel]
    labels: List[ElementModel]
    values: List[ElementModel]
    forms: List[ElementModel]
    cards: List[ElementModel]
    nav: List[ElementModel]
    typography: PairwiseTypographyStats
    viewport_height: Optional[float]
    scroll_height: Optional[float]
    page_width: Optional[float]
    page_height: Optional[float]
    meaningful_h1_count: int
    h1_count: int
    h2_count: int
    h3_count: int
    buttons_count: int
    forms_count: int
    quality_flags: List[str]
    density_per_1000px: float
    top_zone_count: int
    primary_heading_prominence: Optional[float]
    primary_cta_prominence: Optional[float]
    primary_control_prominence: Optional[float]
    repeated_top_text_max: int
    repeated_top_texts: List[Dict[str, Any]]
    dominant_color_clusters: List[Dict[str, Any]] = field(default_factory=list)
    neutral_color_clusters: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================
# Data maps
# ============================================================

def _rendered_page_map(rendered_ui_data: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {
        (page.get("name", ""), page.get("url", "")): page
        for page in rendered_ui_data.get("pages", [])
        if isinstance(page, dict)
    }


def _persona_page_map(person_a_data: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {
        (page.get("name", ""), page.get("url", "")): page
        for page in person_a_data.get("pages", [])
        if isinstance(page, dict)
    }


def _get_all_rendered_elements(rendered_page: Dict[str, Any]) -> List[Dict[str, Any]]:
    rendered_ui = rendered_page.get("renderedUi") or {}
    components = rendered_ui.get("components") or {}
    out: List[Dict[str, Any]] = []
    for value in components.values():
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
    return out


# ============================================================
# Primitive extraction helpers
# ============================================================

def _font_family_normalized(raw: Any) -> str:
    text = _text(raw).replace('"', "").replace("'", "").strip().lower()
    if not text:
        return ""
    primary = text.split(",")[0].strip()
    ignore = {"arial", "helvetica", "sans-serif", "serif", "monospace", "system-ui"}
    return "" if primary in ignore else primary


def _element_text_name(el: Dict[str, Any]) -> str:
    return (
        _text(el.get("accessibleName"))
        or _text(el.get("label"))
        or _text(el.get("text"))
        or _text(el.get("name"))
    )


def _element_font_size(el: Dict[str, Any]) -> Optional[float]:
    return _parse_px(_safe_get(el, "tokens", "fontSize")) or _parse_px(_safe_get(el, "styles", "fontSize"))


def _element_font_weight(el: Dict[str, Any]) -> Optional[int]:
    return _parse_int(_safe_get(el, "tokens", "fontWeight")) or _parse_int(_safe_get(el, "styles", "fontWeight"))


def _element_text_color(el: Dict[str, Any]) -> Optional[Tuple[int, int, int, float]]:
    return _parse_color(_safe_get(el, "tokens", "textColor")) or _parse_color(_safe_get(el, "styles", "color"))


def _element_bg_color(el: Dict[str, Any]) -> Optional[Tuple[int, int, int, float]]:
    return (
        _parse_color(_safe_get(el, "effectiveBackgroundColor"))
        or _parse_color(_safe_get(el, "effectiveBackground", "color"))
        or _parse_color(_safe_get(el, "tokens", "backgroundColor"))
        or _parse_color(_safe_get(el, "styles", "backgroundColor"))
    )


def _element_contrast(el: Dict[str, Any]) -> Optional[float]:
    explicit = _parse_float(el.get("contrastAgainstEffectiveBackground"))
    if explicit is not None:
        return explicit
    fg = _element_text_color(el)
    bg = _element_bg_color(el)
    if fg and bg:
        return round(_contrast_ratio(fg, bg), 2)
    return None


def _element_family(el: Dict[str, Any]) -> str:
    semantic = _lower(el.get("semanticType"))
    ux_role = _lower(el.get("uxRole"))
    tag = _lower(el.get("tag"))
    variant = _lower(el.get("componentVariant"))

    if ux_role in {"primary-cta", "secondary-cta"}:
        return f"cta::{ux_role}"
    if semantic in {"cta-link"}:
        return f"cta::{variant or 'default'}"
    if semantic in {"button", "button-ghost"} or tag == "button":
        return f"button::{variant or semantic or 'default'}"
    if semantic in {"link", "nav-link"} or tag == "a":
        return f"link::{ux_role or semantic or 'default'}"
    if semantic == "heading" or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return f"heading::{tag or 'generic'}"
    if semantic in {"input", "textarea", "select", "form"}:
        return f"form::{semantic}"
    if semantic == "card":
        return "card"
    if semantic == "badge":
        return "badge"
    if ux_role in {"label"}:
        return "label"
    if ux_role in {"value"}:
        return "value"
    if semantic == "text-block":
        return "text-block"
    return semantic or tag or "generic"


def _element_kind(el: Dict[str, Any]) -> str:
    semantic = _lower(el.get("semanticType"))
    ux_role = _lower(el.get("uxRole"))
    tag = _lower(el.get("tag"))
    text = _lower(_element_text_name(el))

    if ux_role in {"primary-cta", "secondary-cta", "search-submit"}:
        return "cta"
    if semantic == "cta-link":
        return "cta"
    if semantic in {"button", "button-ghost"} or tag == "button":
        return "control"
    if semantic in {"link", "nav-link"} or tag == "a":
        if ux_role in {"catalog-link"}:
            return "content-link"
        if "menu" in ux_role or "nav" in ux_role:
            return "nav"
        return "control"
    if semantic == "heading" or tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or ux_role in {"page-title", "section-heading"}:
        return "heading"
    if semantic in {"input", "textarea", "select", "form"}:
        return "form"
    if semantic == "card":
        return "card"
    if ux_role in {"label"}:
        return "label"
    if ux_role in {"value"}:
        return "value"
    if any(k in text for k in TASK_KEYWORDS):
        return "control" if semantic not in {"text-block"} else "content"
    if _element_text_name(el):
        return "content"
    return "other"


def _element_xywh(el: Dict[str, Any]) -> Tuple[float, float, float, float]:
    rect = el.get("rect") or {}
    x = _parse_float(rect.get("x")) or 0.0
    y = _parse_float(rect.get("y")) or 0.0
    width = _parse_float(rect.get("width")) or 0.0
    height = _parse_float(rect.get("height")) or 0.0
    return x, y, max(width, 0.0), max(height, 0.0)


def _element_prominence_score(el: Dict[str, Any], viewport_height: Optional[float]) -> float:
    x, y, width, height = _element_xywh(el)
    area = width * height
    font_size = _element_font_size(el) or 0.0
    weight = _element_font_weight(el) or 400
    contrast = _element_contrast(el) or 0.0
    kind = _element_kind(el)
    tag = _lower(el.get("tag"))
    visible = el.get("visible") is not False
    above_fold = False
    if viewport_height is not None:
        above_fold = y < (viewport_height * 0.95)
    else:
        above_fold = bool(el.get("isAboveTheFold") is True)

    kind_boost = 0.0
    if kind == "cta":
        kind_boost = 16.0
    elif kind == "heading":
        kind_boost = 12.0
    elif kind == "control":
        kind_boost = 8.0
    elif kind == "form":
        kind_boost = 6.0
    elif kind == "card":
        kind_boost = 4.0

    heading_tag_boost = {"h1": 5.0, "h2": 3.5, "h3": 2.0}.get(tag, 0.0)
    above_fold_boost = 8.0 if above_fold else 0.0
    area_score = min(area / 15000.0, 14.0)
    font_score = min(max(font_size - 12.0, 0.0), 16.0)
    weight_score = min(max(weight - 400, 0.0) / 60.0, 6.0)
    contrast_score = min(max(contrast - 3.0, 0.0), 8.0)

    center_bonus = 0.0
    if 180 <= x <= 1150:
        center_bonus = 3.0

    depth_penalty = min((_parse_int(el.get("domDepth")) or 0) * 0.35, 8.0)
    visibility_penalty = 0.0 if visible else 100.0

    return round(
        kind_boost
        + heading_tag_boost
        + above_fold_boost
        + area_score
        + font_score
        + weight_score
        + contrast_score
        + center_bonus
        - depth_penalty
        - visibility_penalty,
        2,
    )


def _top_elements(elements: List[ElementModel], limit: int = 12) -> List[ElementModel]:
    return sorted(elements, key=lambda e: e.prominence_score, reverse=True)[:limit]


# ============================================================
# Archetype detection
# ============================================================

def _page_type_hint(page: Dict[str, Any]) -> str:
    combined = " ".join([
        _lower(page.get("name")),
        _lower(page.get("url")),
        _lower(page.get("finalUrl")),
    ])

    if any(k in combined for k in CATALOG_HINTS):
        return "catalog"
    if any(k in combined for k in FORM_HINTS):
        return "task"
    if any(k in combined for k in CONTENT_PAGE_HINTS):
        return "content"

    final_url = _lower(page.get("finalUrl"))
    url = _lower(page.get("url"))
    name = _lower(page.get("name"))

    root_like = (
        (final_url.endswith("/") and final_url.count("/") <= 3) or
        (url.endswith("/") and url.count("/") <= 3)
    )
    if name in {"home", "homepage"} or root_like:
        return "home"
    return "generic"


def _detect_page_archetype(persona_page: Optional[Dict[str, Any]], rendered_page: Optional[Dict[str, Any]], elements: List[ElementModel]) -> str:
    page_hint = _page_type_hint(rendered_page or persona_page or {})
    if page_hint != "generic":
        return page_hint

    headings = [e for e in elements if e.kind == "heading"]
    ctas = [e for e in elements if e.kind == "cta"]
    controls = [e for e in elements if e.kind == "control"]
    forms = [e for e in elements if e.kind == "form"]
    content = [e for e in elements if e.kind == "content"]
    cards = [e for e in elements if e.kind == "card"]
    nav = [e for e in elements if e.kind == "nav"]

    text_blob = " ".join(_lower(e.text) for e in elements[:60] if e.text)

    if len(forms) >= 1 and (len(ctas) + len(controls)) >= 1:
        return "task"
    if len(ctas) >= 2 and len(headings) <= 6:
        return "conversion"
    if len(cards) >= 6 or ("price" in text_blob and "filter" in text_blob):
        return "catalog"
    if len(content) >= 8 and len(headings) >= 3:
        return "content"
    if len(nav) >= 5 and len(headings) >= 2 and len(ctas) >= 1:
        return "home"
    return "generic"


# ============================================================
# Build page summary
# ============================================================

def _build_elements_for_page(
    rendered_page: Optional[Dict[str, Any]],
    page_name: str,
    page_url: str,
    viewport_height: Optional[float],
) -> List[ElementModel]:
    out: List[ElementModel] = []

    for raw in _get_all_rendered_elements(rendered_page or {}):
        kind = _element_kind(raw)
        family = _element_family(raw)
        text = _element_text_name(raw)
        visible = raw.get("visible") is not False
        x, y, width, height = _element_xywh(raw)
        area = width * height
        dom_depth = _parse_int(raw.get("domDepth")) or 0
        font_family = (
            _font_family_normalized(_safe_get(raw, "styles", "fontFamily"))
            or _font_family_normalized(_safe_get(raw, "tokens", "fontFamily"))
        )
        font_size = _element_font_size(raw)
        font_weight = _element_font_weight(raw)
        text_color = _element_text_color(raw)
        bg_color = _element_bg_color(raw)
        contrast = _element_contrast(raw)

        layout = raw.get("layoutContext") or {}
        parent_display = _lower(layout.get("parentDisplay"))
        sibling_count = _parse_int(layout.get("siblingCount")) or 0

        landmark = ""
        closest = raw.get("closestLandmark") or {}
        landmark = (
            _text(closest.get("xpathHint"))
            or _text(closest.get("className"))
            or _text(closest.get("tag"))
        )

        above_fold = raw.get("isAboveTheFold") is True
        if viewport_height is not None:
            above_fold = y < (viewport_height * 0.95)

        prominence = _element_prominence_score(raw, viewport_height)

        out.append(
            ElementModel(
                raw=raw,
                page_name=page_name,
                page_url=page_url,
                kind=kind,
                family=family,
                text=text,
                visible=visible,
                above_fold=above_fold,
                x=x,
                y=y,
                width=width,
                height=height,
                area=area,
                dom_depth=dom_depth,
                font_family=font_family,
                font_size=font_size,
                font_weight=font_weight,
                text_color=text_color,
                bg_color=bg_color,
                contrast=contrast,
                landmark=landmark,
                parent_display=parent_display,
                sibling_count=sibling_count,
                prominence_score=prominence,
            )
        )
    return out


def _page_palette_from_elements(elements: List[ElementModel]) -> Dict[str, Any]:
    colors: List[Tuple[int, int, int, float]] = []
    for el in elements:
        if el.text_color:
            colors.append(el.text_color)
        if el.bg_color:
            colors.append(el.bg_color)

    unique = []
    seen = set()
    for c in colors:
        key = _rgb_to_key(c, ignore_alpha=True)
        if key not in seen:
            seen.add(key)
            unique.append(c)

    neutral = [c for c in unique if _is_neutral(c)]
    accent = [c for c in unique if not _is_neutral(c)]

    neutral_clusters = _cluster_colors(neutral, threshold=0.08) if neutral else []
    accent_clusters = _cluster_colors(accent, threshold=0.11) if accent else []

    def serialize(cluster_list: List[List[Tuple[int, int, int, float]]]) -> List[Dict[str, Any]]:
        out = []
        for cluster in cluster_list:
            anchor = cluster[0]
            out.append({
                "anchor": _rgb_to_key(anchor, ignore_alpha=True),
                "count": len(cluster),
                "members": [_rgb_to_key(c, ignore_alpha=True) for c in cluster[:8]],
            })
        out.sort(key=lambda x: x["count"], reverse=True)
        return out

    return {
        "neutralClusters": serialize(neutral_clusters),
        "accentClusters": serialize(accent_clusters),
    }


def _top_text_repetition(elements: List[ElementModel]) -> Dict[str, Any]:
    names = [_lower(e.text) for e in elements if e.text]
    counts = Counter(names)
    repeated = [{"text": k, "count": v} for k, v in counts.items() if v >= 3]
    repeated.sort(key=lambda x: x["count"], reverse=True)
    return {
        "maxRepeat": max(counts.values()) if counts else 0,
        "repeated": repeated[:8],
    }


def _typography_stats(
    headings: List[ElementModel],
    content: List[ElementModel],
    controls: List[ElementModel],
    labels: List[ElementModel],
) -> PairwiseTypographyStats:
    return PairwiseTypographyStats(
        heading_size_med=_median([e.font_size for e in headings if e.font_size is not None]),
        heading_weight_med=_median([float(e.font_weight) for e in headings if e.font_weight is not None]),
        content_size_med=_median([e.font_size for e in content if e.font_size is not None]),
        content_weight_med=_median([float(e.font_weight) for e in content if e.font_weight is not None]),
        control_size_med=_median([e.font_size for e in controls if e.font_size is not None]),
        control_weight_med=_median([float(e.font_weight) for e in controls if e.font_weight is not None]),
        label_size_med=_median([e.font_size for e in labels if e.font_size is not None]),
        label_weight_med=_median([float(e.font_weight) for e in labels if e.font_weight is not None]),
    )


def _build_page_summary(persona_page: Optional[Dict[str, Any]], rendered_page: Optional[Dict[str, Any]]) -> PageSummary:
    page_ref = _page_ref(rendered_page or persona_page or {})
    viewport_height = _parse_float(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "viewportHeight"))
    scroll_height = _parse_float(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "scrollHeight"))
    page_width = _parse_float(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "scrollWidth"))
    page_height = scroll_height

    elements = _build_elements_for_page(
        rendered_page,
        page_ref["name"],
        page_ref["url"],
        viewport_height,
    )
    visible = [e for e in elements if e.visible]

    archetype = _detect_page_archetype(persona_page, rendered_page, visible)
    profile = PAGE_PROFILES.get(archetype, PAGE_PROFILES["generic"])

    headings = [e for e in visible if e.kind == "heading"]
    content = [e for e in visible if e.kind == "content"]
    controls = [e for e in visible if e.kind == "control"]
    ctas = [e for e in visible if e.kind == "cta"]
    labels = [e for e in visible if e.kind == "label"]
    values = [e for e in visible if e.kind == "value"]
    forms = [e for e in visible if e.kind == "form"]
    cards = [e for e in visible if e.kind == "card"]
    nav = [e for e in visible if e.kind == "nav"]

    top = _top_elements(visible, limit=12)
    rep = _top_text_repetition(top)

    top_zone_height = viewport_height or 900.0
    top_zone_count = sum(1 for e in visible if e.y < top_zone_height)

    density = 0.0
    if scroll_height and scroll_height > 0:
        density = len(visible) / max(scroll_height / 1000.0, 1.0)

    primary_heading = max(headings, key=lambda e: e.prominence_score, default=None)
    primary_cta = max(ctas, key=lambda e: e.prominence_score, default=None)
    primary_control = max(controls, key=lambda e: e.prominence_score, default=None)

    h1_count = len(_safe_get(persona_page or {}, "titlesAndHeadings", "data", "h1", default=[]) or [])
    meaningful_h1_count = int(_safe_get(persona_page or {}, "qualitySignals", "summary", "meaningfulH1Count", default=0) or 0)
    h2_count = len(_safe_get(persona_page or {}, "titlesAndHeadings", "data", "h2", default=[]) or [])
    h3_count = len(_safe_get(persona_page or {}, "titlesAndHeadings", "data", "h3", default=[]) or [])
    buttons_count = int(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "buttons", default=0) or 0)
    forms_count = int(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "forms", default=0) or 0)
    quality_flags = _safe_get(persona_page or {}, "qualitySignals", "flags", default=[]) or []

    palette = _page_palette_from_elements(visible)

    return PageSummary(
        page_ref=page_ref,
        archetype=archetype,
        profile=profile,
        all_elements=elements,
        visible_elements=visible,
        top_elements=top,
        headings=headings,
        content=content,
        controls=controls,
        ctas=ctas,
        labels=labels,
        values=values,
        forms=forms,
        cards=cards,
        nav=nav,
        typography=_typography_stats(headings, content, controls + ctas, labels),
        viewport_height=viewport_height,
        scroll_height=scroll_height,
        page_width=page_width,
        page_height=page_height,
        meaningful_h1_count=meaningful_h1_count,
        h1_count=h1_count,
        h2_count=h2_count,
        h3_count=h3_count,
        buttons_count=buttons_count,
        forms_count=forms_count,
        quality_flags=quality_flags,
        density_per_1000px=round(density, 2),
        top_zone_count=top_zone_count,
        primary_heading_prominence=primary_heading.prominence_score if primary_heading else None,
        primary_cta_prominence=primary_cta.prominence_score if primary_cta else None,
        primary_control_prominence=primary_control.prominence_score if primary_control else None,
        repeated_top_text_max=rep["maxRepeat"],
        repeated_top_texts=rep["repeated"],
        dominant_color_clusters=palette["accentClusters"],
        neutral_color_clusters=palette["neutralClusters"],
    )


# ============================================================
# Geometry / grouping / style helpers
# ============================================================

def _vertical_distance(a: ElementModel, b: ElementModel) -> float:
    if a.bottom <= b.y:
        return b.y - a.bottom
    if b.bottom <= a.y:
        return a.y - b.bottom
    return 0.0


def _horizontal_alignment_delta(a: ElementModel, b: ElementModel) -> float:
    return abs(a.x - b.x)


def _same_landmark(a: ElementModel, b: ElementModel) -> bool:
    return bool(a.landmark and b.landmark and a.landmark == b.landmark)


def _style_signature(el: ElementModel) -> str:
    tc = _rgb_to_key(el.text_color, ignore_alpha=True) if el.text_color else ""
    bg = _rgb_to_key(el.bg_color, ignore_alpha=True) if el.bg_color else ""
    fs = f"{round(el.font_size, 1)}" if el.font_size is not None else ""
    fw = f"{el.font_weight}" if el.font_weight is not None else ""
    rounded = _text(_safe_get(el.raw, "tokens", "radius")) or _text(_safe_get(el.raw, "styles", "borderTopLeftRadius"))
    border = _text(_safe_get(el.raw, "tokens", "border")) or _text(_safe_get(el.raw, "styles", "border"))
    return " | ".join([el.family, el.font_family, fs, fw, tc, bg, rounded, border])

# ============================================================
# Contrast helpers
# ============================================================

def _effective_element_contrast(el: ElementModel) -> Optional[float]:
    """
    Prefer explicit extracted contrast, but recompute from text/background
    when possible. Clamp to WCAG-like range.
    """
    if el.contrast is not None and 1.0 <= el.contrast <= 21.0:
        return float(el.contrast)

    if el.text_color and el.bg_color:
        try:
            value = _contrast_ratio(el.text_color, el.bg_color)
            if 1.0 <= value <= 21.0:
                return round(value, 2)
        except Exception:
            return None

    return None


def _is_large_text(el: ElementModel) -> bool:
    fs = el.font_size or 0.0
    fw = el.font_weight or 400
    return fs >= 18.0 or (fs >= 14.0 and fw >= 600)


def _target_min_contrast(el: ElementModel) -> float:
    return 3.0 if _is_large_text(el) else 4.5


def _contrast_samples(elements: List[ElementModel]) -> List[Tuple[ElementModel, float]]:
    out: List[Tuple[ElementModel, float]] = []
    for el in elements:
        c = _effective_element_contrast(el)
        if c is not None and 1.0 <= c <= 21.0:
            out.append((el, c))
    return out


def _priority_elements(summary: PageSummary) -> List[ElementModel]:
    """
    Priority elements = top headings + top actions in top zone.
    """
    top = summary.top_elements[:10]
    out: List[ElementModel] = []
    for el in top:
        if el.kind in {"heading", "cta", "control", "form"}:
            out.append(el)

    if len(out) < 4:
        extra = sorted(
            [e for e in summary.visible_elements if e.kind in {"heading", "cta", "control"}],
            key=lambda e: e.prominence_score,
            reverse=True,
        )
        seen = {id(e) for e in out}
        for el in extra:
            if id(el) not in seen:
                out.append(el)
                seen.add(id(el))
            if len(out) >= 6:
                break

    return out


def _body_like_content_elements(summary: PageSummary) -> List[ElementModel]:
    out = [
        e for e in summary.content
        if e.text
        and (e.font_size is None or 12 <= e.font_size <= 19)
        and e.area > 0
    ]
    if len(out) >= 3:
        return out

    return [
        e for e in summary.visible_elements
        if e.kind == "content"
        and e.text
        and e.area > 0
    ]


def _infer_label_like_elements(summary: PageSummary) -> List[ElementModel]:
    """
    Use explicit labels first. If missing, infer likely labels from short,
    nearby text around forms/values.
    """
    if len(summary.labels) >= 2:
        return summary.labels

    inferred: List[ElementModel] = []
    forms_and_values = summary.forms + summary.values

    if not forms_and_values:
        return inferred

    for txt in summary.visible_elements:
        if txt.kind not in {"content", "heading"}:
            continue
        if not txt.text or len(txt.text.strip()) > 40:
            continue
        if txt.height <= 0 or txt.width <= 0:
            continue

        for target in forms_and_values:
            near_vertical = abs(txt.bottom - target.y) <= 48 or abs(target.bottom - txt.y) <= 48
            near_horizontal = abs(txt.x - target.x) <= 140
            if near_vertical and near_horizontal:
                inferred.append(txt)
                break

    seen = set()
    unique: List[ElementModel] = []
    for el in inferred:
        key = (el.text, round(el.x, 1), round(el.y, 1), round(el.width, 1), round(el.height, 1))
        if key not in seen:
            seen.add(key)
            unique.append(el)

    return unique[:30]


def _contrast_distribution_metrics(samples: List[Tuple[ElementModel, float]]) -> Dict[str, Any]:
    values = [c for _, c in samples]
    if not values:
        return {
            "count": 0,
            "median": None,
            "mean": None,
            "under3Ratio": None,
            "under4_5Ratio": None,
            "under7Ratio": None,
        }

    count = len(values)
    return {
        "count": count,
        "median": round(_median(values) or 0.0, 2),
        "mean": round(_mean(values) or 0.0, 2),
        "under3Ratio": round(sum(1 for v in values if v < 3.0) / count, 3),
        "under4_5Ratio": round(sum(1 for v in values if v < 4.5) / count, 3),
        "under7Ratio": round(sum(1 for v in values if v < 7.0) / count, 3),
    }


def _text_vs_controls_color_separation(summary: PageSummary) -> float:
    controls = [e for e in summary.visible_elements if e.kind in {"cta", "control"}]
    content = _body_like_content_elements(summary)

    if len(controls) < 2 or len(content) < 2:
        return 0.5

    control_fg = {_rgb_to_key(e.text_color, ignore_alpha=True) for e in controls if e.text_color}
    control_bg = {_rgb_to_key(e.bg_color, ignore_alpha=True) for e in controls if e.bg_color}
    content_fg = {_rgb_to_key(e.text_color, ignore_alpha=True) for e in content if e.text_color}
    content_bg = {_rgb_to_key(e.bg_color, ignore_alpha=True) for e in content if e.bg_color}

    score = 0.0

    if control_fg.difference(content_fg):
        score += 0.35
    if control_bg and control_bg.difference(content_bg.union(content_fg)):
        score += 0.45

    size_gap = 0.0
    if summary.typography.control_size_med is not None and summary.typography.content_size_med is not None:
        size_gap = abs(summary.typography.control_size_med - summary.typography.content_size_med)

    score += 0.20 * _normalize_score(size_gap, 0.5, 4.0)
    return _clamp(score, 0.0, 1.0)
# ============================================================
# Criterion scoring
# ============================================================

def _score_information_order(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []

    top = summary.top_elements[:8]
    top_headings = [e for e in top if e.kind == "heading"]
    top_actions = [e for e in top if e.kind in {"cta", "control", "form"}]
    top_cards = [e for e in top if e.kind == "card"]
    top_content_links = [e for e in top if e.kind == "content-link"]

    if len(summary.visible_elements) < 4:
        return None, [], {"reason": "too_few_visible_elements"}

    heading_signal = 1.0 if top_headings else 0.0
    meaningful_h1_signal = 1.0 if summary.meaningful_h1_count >= 1 else 0.0
    repetition_penalty = 1.0 - _clamp(_ratio(summary.repeated_top_text_max - 1, 5), 0.0, 1.0)
    clutter_penalty = 1.0 - _clamp(_ratio(len(top_cards) + len(top_content_links), max(len(top), 1)), 0.0, 1.0)
    top_action_signal = _clamp(_ratio(len(top_actions), 2.0), 0.0, 1.0) if summary.profile.expects_top_action else 1.0

    score = (
        28 * heading_signal
        + 20 * meaningful_h1_signal
        + 22 * repetition_penalty
        + 15 * clutter_penalty
        + 15 * top_action_signal
    )

    if not top_headings and summary.profile.expects_strong_heading:
        reasons.append("top-priority-zone-lacks-clear-heading")
    if summary.meaningful_h1_count == 0 and "heavy_picker_or_locale_noise" not in summary.quality_flags:
        reasons.append("missing-meaningful-primary-title")
    if summary.repeated_top_text_max >= 3:
        reasons.append("repeated-elements-dominate-top-priority-zone")
    if summary.profile.expects_top_action and len(top_actions) == 0:
        reasons.append("top-zone-does-not-surface-next-action")

    return round(score, 2), reasons, {
        "topElementCount": len(top),
        "topHeadingCount": len(top_headings),
        "topActionCount": len(top_actions),
        "meaningfulH1Count": summary.meaningful_h1_count,
        "repeatedTopTextMax": summary.repeated_top_text_max,
    }


def _score_visual_hierarchy_reflects_priority(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []

    if not summary.headings and not summary.content:
        return None, [], {"reason": "no_heading_or_content_elements"}

    heading_prom = summary.primary_heading_prominence or 0.0
    cta_prom = summary.primary_cta_prominence or 0.0
    ctrl_prom = summary.primary_control_prominence or 0.0
    content_prom = _median([e.prominence_score for e in summary.content]) or 0.0
    top_heading_presence = 1.0 if any(e.kind == "heading" for e in summary.top_elements[:5]) else 0.0

    heading_vs_content = _normalize_score(heading_prom - content_prom, 4.0, 18.0)
    action_vs_content = _normalize_score(max(cta_prom, ctrl_prom) - content_prom, 4.0, 18.0)
    title_structure_signal = top_heading_presence

    type_gap = 0.0
    if summary.typography.heading_size_med is not None and summary.typography.content_size_med is not None:
        type_gap = _normalize_score(summary.typography.heading_size_med - summary.typography.content_size_med, 2.0, 10.0)

    score = 32 * heading_vs_content + 28 * action_vs_content + 20 * title_structure_signal + 20 * type_gap

    if heading_vs_content < 0.35 and summary.profile.expects_strong_heading:
        reasons.append("headings-are-not-prominent-enough-vs-content")
    if summary.profile.expects_top_action and action_vs_content < 0.35:
        reasons.append("actions-do-not-stand-out-enough-vs-content")
    if not any(e.kind == "heading" for e in summary.top_elements[:5]) and summary.profile.expects_strong_heading:
        reasons.append("no-heading-in-top-priority-band")

    return round(score, 2), reasons, {
        "primaryHeadingProminence": round(heading_prom, 2),
        "primaryActionProminence": round(max(cta_prom, ctrl_prom), 2),
        "contentMedianProminence": round(content_prom, 2),
        "headingSizeMedian": summary.typography.heading_size_med,
        "contentSizeMedian": summary.typography.content_size_med,
    }


def _score_required_action_direction(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    if not summary.profile.expects_top_action:
        return None, [], {"reason": "not_applicable_for_page_archetype"}

    reasons: List[str] = []
    top = summary.top_elements[:8]
    top_actions = [e for e in top if e.kind in {"cta", "control", "form"}]

    if not top and not summary.visible_elements:
        return None, [], {"reason": "no_visible_elements"}

    action_prom = max([e.prominence_score for e in top_actions], default=0.0)
    action_count_signal = _clamp(_ratio(len(top_actions), 2.0), 0.0, 1.0)
    action_prom_signal = _normalize_score(action_prom, 14.0, 34.0)

    score = 45 * action_count_signal + 55 * action_prom_signal

    if not top_actions:
        reasons.append("no-clear-next-action-in-top-priority-band")
    elif action_prom_signal < 0.4:
        reasons.append("top-action-exists-but-is-not-visually-strong")

    return round(score, 2), reasons, {
        "topActionCount": len(top_actions),
        "topActionProminence": round(action_prom, 2),
    }


def _score_cta_primary(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    if not summary.profile.expects_primary_cta:
        return None, [], {"reason": "not_applicable_for_page_archetype"}

    reasons: List[str] = []
    if not summary.ctas:
        return 20.0, ["no-primary-cta-detected"], {"ctaCount": 0}

    best_cta = max(summary.ctas, key=lambda e: e.prominence_score)
    top5 = summary.top_elements[:5]
    rank_signal = 1.0 if best_cta in top5 else 0.0
    cta_prom_signal = _normalize_score(best_cta.prominence_score, 18.0, 36.0)
    cta_contrast_signal = _normalize_score(best_cta.contrast, 3.0, 6.0)

    competitors = [e.prominence_score for e in summary.top_elements[:5] if e is not best_cta]
    competitor_med = _median(competitors) or 0.0
    relative_signal = _normalize_score(best_cta.prominence_score - competitor_med, 2.0, 12.0)

    score = 30 * rank_signal + 30 * cta_prom_signal + 20 * cta_contrast_signal + 20 * relative_signal

    if best_cta not in top5:
        reasons.append("main-cta-not-among-top-visual-elements")
    if cta_contrast_signal < 0.35:
        reasons.append("main-cta-contrast-is-too-weak")
    if relative_signal < 0.35:
        reasons.append("main-cta-does-not-dominate-other-top-elements")

    return round(score, 2), reasons, {
        "ctaCount": len(summary.ctas),
        "bestCtaProminence": round(best_cta.prominence_score, 2),
        "bestCtaContrast": best_cta.contrast,
        "bestCtaInTop5": best_cta in top5,
    }


def _score_grouping(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []
    elements = summary.visible_elements[:300]
    if len(elements) < 6:
        return None, [], {"reason": "too_few_elements_for_grouping"}

    same_landmark_pairs = 0
    aligned_close_pairs = 0
    total_checked = 0

    sort_y = sorted(elements, key=lambda e: (e.y, e.x))
    for i in range(min(len(sort_y) - 1, 120)):
        a = sort_y[i]
        b = sort_y[i + 1]
        total_checked += 1
        if _same_landmark(a, b):
            same_landmark_pairs += 1
        if _vertical_distance(a, b) <= 24 and _horizontal_alignment_delta(a, b) <= 16:
            aligned_close_pairs += 1

    landmark_signal = _ratio(same_landmark_pairs, max(total_checked, 1))
    alignment_signal = _ratio(aligned_close_pairs, max(total_checked, 1))

    weak_layout = 0
    layout_total = 0
    for e in elements:
        if e.sibling_count >= 3:
            layout_total += 1
            if e.parent_display not in {"flex", "grid", "block"}:
                weak_layout += 1
    layout_signal = 1.0 - _ratio(weak_layout, max(layout_total, 1))

    score = 40 * landmark_signal + 35 * alignment_signal + 25 * layout_signal

    if landmark_signal < 0.25:
        reasons.append("related-elements-do-not-show-strong-common-container-patterns")
    if alignment_signal < 0.35:
        reasons.append("adjacent-elements-show-weak-proximity-or-alignment-patterns")
    if layout_signal < 0.5:
        reasons.append("many-repeated-areas-lack-clear-layout-structure")

    return round(score, 2), reasons, {
        "landmarkSignal": round(landmark_signal, 3),
        "alignmentSignal": round(alignment_signal, 3),
        "layoutSignal": round(layout_signal, 3),
        "checkedPairs": total_checked,
    }


def _score_negative_space(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []

    if len(summary.visible_elements) < 6:
        return None, [], {"reason": "too_few_elements_for_spacing"}

    density = summary.density_per_1000px
    density_signal = 1.0 - _normalize_score(density, 45.0, 85.0)

    y_values = sorted(e.y for e in summary.visible_elements if e.height > 0)
    gaps = [max(y_values[i + 1] - y_values[i], 0.0) for i in range(len(y_values) - 1)]
    if not gaps:
        return None, [], {"reason": "insufficient_y_gaps"}

    big_gap_ratio = _ratio(sum(1 for g in gaps if g >= 24), max(len(gaps), 1))
    medium_gap_ratio = _ratio(sum(1 for g in gaps if g >= 16), max(len(gaps), 1))
    gap_signal = _clamp(big_gap_ratio * 0.65 + medium_gap_ratio * 0.35, 0.0, 1.0)

    top_zone_density = 0.0
    if summary.viewport_height and summary.viewport_height > 0:
        top_zone_density = summary.top_zone_count / max(summary.viewport_height / 1000.0, 1.0)
    top_zone_signal = 1.0 - _normalize_score(top_zone_density, 18.0, 45.0)

    score = 40 * density_signal + 35 * gap_signal + 25 * top_zone_signal

    if density_signal < 0.4:
        reasons.append("page-appears-visually-dense")
    if gap_signal < 0.35:
        reasons.append("section-and-component-spacing-is-limited")
    if top_zone_signal < 0.35:
        reasons.append("above-the-fold-region-appears-crowded")

    return round(score, 2), reasons, {
        "densityPer1000px": density,
        "bigGapRatio": round(big_gap_ratio, 3),
        "mediumGapRatio": round(medium_gap_ratio, 3),
        "topZoneDensity": round(top_zone_density, 3),
    }


def _score_similar_information_consistency(site_pages: List[PageSummary]) -> Tuple[Optional[float], List[Dict[str, Any]], Dict[str, Any]]:
    family_variants: DefaultDict[str, DefaultDict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    total_instances = 0
    for page in site_pages:
        for el in page.visible_elements:
            sig = _style_signature(el)
            family_variants[el.family][sig].append({
                "page": page.page_ref["name"],
                "url": page.page_ref["url"],
                "finalUrl": page.page_ref["finalUrl"],
                "text": el.text[:80],
            })
            total_instances += 1

    if total_instances < 12:
        return None, [], {"reason": "too_few_instances_for_consistency"}

    family_issues: List[Dict[str, Any]] = []
    family_scores: List[float] = []

    for family, variants in family_variants.items():
        total = sum(len(items) for items in variants.values())
        if total < 4:
            continue

        dominant = max(len(v) for v in variants.values())
        dominant_ratio = _ratio(dominant, total)
        variant_count = len(variants)

        tolerance_bonus = 0.0
        if family == "card":
            tolerance_bonus = 0.08
        elif family.startswith("heading::"):
            tolerance_bonus = 0.05

        score = 100.0
        score -= max(0.0, (variant_count - 2)) * 9.0
        score -= max(0.0, (0.78 + tolerance_bonus - dominant_ratio)) * 70.0
        score = _clamp(score, 0.0, 100.0)
        family_scores.append(score)

        if score < 78:
            family_issues.append({
                "family": family,
                "variantCount": variant_count,
                "totalInstances": total,
                "dominantRatio": round(dominant_ratio, 3),
                "score": round(score, 2),
            })

    if not family_scores:
        return None, [], {"reason": "no_eligible_families_for_consistency"}

    site_score = round(_mean(family_scores) or 0.0, 2)

    if family_issues:
        lowest_issue_score = min(issue["score"] for issue in family_issues)
        site_score = min(site_score, lowest_issue_score)

    return site_score, family_issues, {
        "familyCount": len(family_variants),
        "eligibleFamilyCount": len(family_scores),
    }


def _score_primary_color_count(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []
    primary_count = len(summary.dominant_color_clusters)
    if primary_count == 0:
        return None, [], {"reason": "no_dominant_accent_clusters"}

    score = 100.0
    if primary_count <= 3:
        return score, reasons, {"primaryAccentClusterCount": primary_count}
    if primary_count == 4:
        score = 72.0
    elif primary_count == 5:
        score = 58.0
    else:
        score = max(30.0, 58.0 - (primary_count - 5) * 6.0)
    reasons.append("too-many-distinct-primary-accent-color-families")
    return round(score, 2), reasons, {"primaryAccentClusterCount": primary_count}


def _score_chrome_desaturated(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []
    chrome = [e for e in summary.visible_elements if e.kind not in {"content", "heading", "cta"} and e.bg_color]
    if len(chrome) < 3:
        return None, [], {"reason": "too_few_chrome_surfaces"}

    sats = [_rgb_to_hsl(e.bg_color)[1] for e in chrome if e.bg_color]
    cta_sats = [_rgb_to_hsl(e.bg_color)[1] for e in summary.ctas if e.bg_color]
    chrome_sat = _mean(sats) or 0.0
    cta_sat = _mean(cta_sats) or 0.0

    recede_signal = 1.0 - _normalize_score(chrome_sat, 0.22, 0.48)
    relative_signal = 0.7 if not cta_sats else _clamp((cta_sat - chrome_sat + 0.10) / 0.35, 0.0, 1.0)
    score = 60 * recede_signal + 40 * relative_signal

    if recede_signal < 0.4:
        reasons.append("supporting-ui-surfaces-are-too-saturated")
    if relative_signal < 0.35:
        reasons.append("chrome-colors-compete-with-actions-or-content")

    return round(score, 2), reasons, {
        "chromeSurfaceCount": len(chrome),
        "meanChromeSaturation": round(chrome_sat, 3),
        "meanCtaSaturation": round(cta_sat, 3) if cta_sats else None,
    }


def _score_colors_reinforce_hierarchy(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []
    if not summary.headings and not summary.ctas:
        return None, [], {"reason": "no_heading_or_cta_elements"}

    heading_colors = {_rgb_to_key(e.text_color, ignore_alpha=True) for e in summary.headings if e.text_color}
    content_colors = {_rgb_to_key(e.text_color, ignore_alpha=True) for e in summary.content if e.text_color}
    cta_bg = {_rgb_to_key(e.bg_color, ignore_alpha=True) for e in summary.ctas if e.bg_color}

    heading_distinct = 1.0 if heading_colors and heading_colors.difference(content_colors) else 0.35
    cta_distinct = 0.7 if not summary.profile.expects_primary_cta else (
        1.0 if cta_bg and cta_bg.difference(content_colors.union(heading_colors)) else 0.35
    )
    cta_contrast = _median([e.contrast for e in summary.ctas if e.contrast is not None]) or 0.0
    cta_contrast_signal = 0.7 if not summary.profile.expects_primary_cta else _normalize_score(cta_contrast, 3.0, 6.0)

    score = 30 * heading_distinct + 40 * cta_distinct + 30 * cta_contrast_signal

    if heading_distinct < 0.5 and summary.profile.expects_strong_heading:
        reasons.append("heading-color-does-not-help-separate-information-levels")
    if summary.profile.expects_primary_cta and cta_distinct < 0.5:
        reasons.append("cta-colors-are-not-distinct-enough-from-surrounding-content")
    if summary.profile.expects_primary_cta and cta_contrast_signal < 0.35:
        reasons.append("cta-color-contrast-is-too-weak")

    return round(score, 2), reasons, {
        "headingColorCount": len(heading_colors),
        "contentColorCount": len(content_colors),
        "ctaBgColorCount": len(cta_bg),
        "ctaMedianContrast": round(cta_contrast, 2) if cta_contrast else None,
    }


def _site_color_scheme_consistency(site_pages: List[PageSummary]) -> Tuple[Optional[float], Dict[str, Any]]:
    if len(site_pages) <= 1:
        return None, {"reason": "too_few_pages"}

    page_sets = []
    for page in site_pages:
        color_set = {cluster["anchor"] for cluster in page.dominant_color_clusters[:5]}
        if color_set:
            page_sets.append(color_set)

    if len(page_sets) <= 1:
        return None, {"reason": "too_few_palette_pages"}

    overlaps = []
    for i in range(len(page_sets)):
        for j in range(i + 1, len(page_sets)):
            a = page_sets[i]
            b = page_sets[j]
            overlaps.append(_ratio(len(a.intersection(b)), max(len(a.union(b)), 1)))

    avg_overlap = _mean(overlaps) or 0.0
    score = _clamp(25 + avg_overlap * 95, 0.0, 100.0)
    return round(score, 2), {
        "averagePaletteOverlap": round(avg_overlap, 3),
        "pairCount": len(overlaps),
    }


def _score_no_oversaturation(summary: PageSummary) -> Tuple[Optional[float], List[Dict[str, Any]], Dict[str, Any]]:
    problems: List[Dict[str, Any]] = []
    colors = []
    for cluster in summary.dominant_color_clusters:
        anchor = _parse_color(cluster["anchor"])
        if anchor:
            colors.append(anchor)

    if not colors:
        return None, [], {"reason": "no_dominant_accent_clusters"}

    for c in colors:
        _, s, l = _rgb_to_hsl(c)
        if s >= 0.85 and 0.20 <= l <= 0.80:
            problems.append({
                "color": _rgb_to_key(c, ignore_alpha=True),
                "saturation": round(s, 3),
                "lightness": round(l, 3),
            })

    score = 100.0 if not problems else max(40.0, 82.0 - len(problems) * 12.0)
    return round(score, 2), problems, {"accentClusterCount": len(colors)}


def _score_important_items_have_most_contrast(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []

    priority = _priority_elements(summary)
    body = _body_like_content_elements(summary)

    priority_samples = _contrast_samples(priority)
    body_samples = _contrast_samples(body)

    if len(priority_samples) < 3 or len(body_samples) < 3:
        return None, [], {"reason": "insufficient_priority_or_body_contrast_samples"}

    priority_vals = [c for _, c in priority_samples]
    body_vals = [c for _, c in body_samples]

    priority_med = _median(priority_vals) or 0.0
    body_med = _median(body_vals) or 0.0
    priority_p75 = sorted(priority_vals)[max(0, int(len(priority_vals) * 0.75) - 1)]
    body_p75 = sorted(body_vals)[max(0, int(len(body_vals) * 0.75) - 1)]

    median_delta = priority_med - body_med
    p75_delta = priority_p75 - body_p75

    delta_signal = _normalize_score((median_delta * 0.7) + (p75_delta * 0.3), 0.10, 1.50)
    sufficiency_signal = _normalize_score(priority_med, 4.5, 9.0)

    score = 55 * delta_signal + 45 * sufficiency_signal

    if priority_med < 4.5:
        reasons.append("priority-elements-do-not-have-strong-enough-contrast")
    if median_delta <= 0.10:
        reasons.append("contrast-advantage-on-priority-elements-is-limited")
    elif median_delta < 0:
        reasons.append("priority-elements-do-not-out-rank-body-content-in-contrast")

    return round(score, 2), reasons, {
        "priorityMedianContrast": round(priority_med, 2),
        "bodyMedianContrast": round(body_med, 2),
        "priorityP75Contrast": round(priority_p75, 2),
        "bodyP75Contrast": round(body_p75, 2),
        "medianDelta": round(median_delta, 2),
        "p75Delta": round(p75_delta, 2),
        "prioritySampleCount": len(priority_vals),
        "bodySampleCount": len(body_vals),
    }

def _score_contrast_primary_mechanism(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []

    heading_samples = _contrast_samples(summary.headings)
    body_samples = _contrast_samples(_body_like_content_elements(summary))

    if len(heading_samples) < 2 or len(body_samples) < 3:
        return None, [], {"reason": "insufficient_heading_or_body_contrast_samples"}

    heading_vals = [c for _, c in heading_samples]
    body_vals = [c for _, c in body_samples]

    heading_med = _median(heading_vals) or 0.0
    body_med = _median(body_vals) or 0.0
    contrast_gap = heading_med - body_med

    size_gap = 0.0
    if summary.typography.heading_size_med is not None and summary.typography.content_size_med is not None:
        size_gap = summary.typography.heading_size_med - summary.typography.content_size_med

    contrast_signal = _normalize_score(contrast_gap, 0.15, 1.20)
    type_support_signal = _normalize_score(size_gap, 2.0, 10.0)

    score = 70 * contrast_signal + 30 * type_support_signal

    if contrast_gap <= 0.10:
        reasons.append("headings-and-content-have-limited-contrast-separation")
    if contrast_gap <= 0.10 and size_gap < 4.0:
        reasons.append("contrast-is-not-a-meaningful-hierarchy-driver")
    if size_gap <= 1.0:
        reasons.append("type-scale-does-not-strongly-support-hierarchy")

    return round(score, 2), reasons, {
        "headingMedianContrast": round(heading_med, 2),
        "contentMedianContrast": round(body_med, 2),
        "contrastGap": round(contrast_gap, 2),
        "sizeGap": round(size_gap, 2),
        "headingSampleCount": len(heading_vals),
        "contentSampleCount": len(body_vals),
    }

def _score_content_vs_controls_contrast(summary: PageSummary) -> Tuple[Optional[float], List[Dict[str, Any]], Dict[str, Any]]:
    controls = [e for e in summary.visible_elements if e.kind in {"cta", "control"}]
    if len(controls) < 2:
        return None, [], {"reason": "too_few_controls"}

    control_samples = _contrast_samples(controls)
    if len(control_samples) < 2:
        return None, [], {"reason": "insufficient_control_contrast_samples"}

    low = []
    weak = []

    for e, c in control_samples:
        min_target = _target_min_contrast(e)
        if c < min_target:
            low.append({
                "text": e.text[:80],
                "contrast": round(c, 2),
                "family": e.family,
                "targetMin": min_target,
            })
        elif c < max(4.5, min_target + 0.5):
            weak.append({
                "text": e.text[:80],
                "contrast": round(c, 2),
                "family": e.family,
                "targetMin": min_target,
            })

    median_control_contrast = _median([c for _, c in control_samples]) or 0.0
    separation_signal = _text_vs_controls_color_separation(summary)
    contrast_signal = _normalize_score(median_control_contrast, 4.2, 8.5)

    score = 60 * contrast_signal + 40 * separation_signal
    score -= min(len(low) * 10.0, 35.0)
    score -= min(len(weak) * 3.0, 12.0)
    score = _clamp(score, 0.0, 100.0)

    issues = (low + weak)[:12]
    return round(score, 2), issues, {
        "controlCount": len(controls),
        "controlContrastSampleCount": len(control_samples),
        "lowContrastControlCount": len(low),
        "weakContrastControlCount": len(weak),
        "medianControlContrast": round(median_control_contrast, 2),
        "controlSeparationSignal": round(separation_signal, 3),
    }
def _score_labels_vs_content_contrast(summary: PageSummary) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
    reasons: List[str] = []

    labels = _infer_label_like_elements(summary)
    content = _body_like_content_elements(summary)

    label_samples = _contrast_samples(labels)
    content_samples = _contrast_samples(content)

    if len(label_samples) < 2 or len(content_samples) < 3:
        return None, [], {"reason": "insufficient_label_or_content_elements"}

    label_vals = [c for _, c in label_samples]
    content_vals = [c for _, c in content_samples]

    label_med = _median(label_vals) or 0.0
    content_med = _median(content_vals) or 0.0
    contrast_gap = abs(label_med - content_med)

    weight_gap = 0.0
    if summary.typography.label_weight_med is not None and summary.typography.content_weight_med is not None:
        weight_gap = abs(summary.typography.label_weight_med - summary.typography.content_weight_med)
    else:
        inferred_weights = [float(e.font_weight) for e in labels if e.font_weight is not None]
        content_weights = [float(e.font_weight) for e in content if e.font_weight is not None]
        if inferred_weights and content_weights:
            weight_gap = abs((_median(inferred_weights) or 0.0) - (_median(content_weights) or 0.0))

    score = 55 * _normalize_score(contrast_gap, 0.12, 1.20) + 45 * _normalize_score(weight_gap, 20.0, 180.0)

    if contrast_gap < 0.15 and weight_gap < 25:
        reasons.append("labels-and-content-have-very-similar-visual-treatment")
    if label_med < 3.5:
        reasons.append("labels-do-not-have-sufficient-contrast")

    return round(score, 2), reasons, {
        "labelMedianContrast": round(label_med, 2),
        "contentMedianContrast": round(content_med, 2),
        "contrastGap": round(contrast_gap, 2),
        "weightGap": round(weight_gap, 2),
        "labelCount": len(labels),
        "labelContrastSampleCount": len(label_samples),
        "contentContrastSampleCount": len(content_samples),
    }
def _score_foreground_background(summary: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    samples = _contrast_samples(summary.visible_elements)
    if len(samples) < 8:
        return None, {"reason": "insufficient_contrast_samples"}

    metrics = _contrast_distribution_metrics(samples)
    median_c = metrics["median"] or 0.0
    under3 = metrics["under3Ratio"] or 0.0
    under4_5 = metrics["under4_5Ratio"] or 0.0
    under7 = metrics["under7Ratio"] or 0.0

    score = (
        35 * _normalize_score(median_c, 4.5, 10.0)
        + 25 * (1.0 - _clamp(under3 / 0.12, 0.0, 1.0))
        + 25 * (1.0 - _clamp(under4_5 / 0.35, 0.0, 1.0))
        + 15 * (1.0 - _clamp(under7 / 0.75, 0.0, 1.0))
    )

    if median_c >= 10.0 and under3 == 0.0 and under4_5 == 0.0:
        score = min(score, 96.0)

    return round(score, 2), {
        "medianContrast": metrics["median"],
        "meanContrast": metrics["mean"],
        "under3Ratio": under3,
        "under4_5Ratio": under4_5,
        "under7Ratio": under7,
        "contrastSampleCount": metrics["count"],
    }

def _site_font_family_score(site_pages: List[PageSummary]) -> Tuple[Optional[float], Dict[str, Any]]:
    usage: Counter = Counter()
    for page in site_pages:
        for e in page.visible_elements:
            if e.font_family:
                usage[e.font_family] += 1

    dominant = [fam for fam, count in usage.most_common() if count >= 3]
    if not dominant:
        return None, {"reason": "no_dominant_font_families"}

    if len(dominant) <= 2:
        return 92.0, {"fontUsage": dict(usage), "dominantFamilies": dominant}

    if len(dominant) == 3:
        score = 72.0
    elif len(dominant) == 4:
        score = 58.0
    else:
        score = max(30.0, 58.0 - (len(dominant) - 4) * 6.0)

    return round(score, 2), {"fontUsage": dict(usage), "dominantFamilies": dominant}


def _score_font_size_minimum(summary: PageSummary) -> Tuple[Optional[float], List[Dict[str, Any]], Dict[str, Any]]:
    too_small = []
    eligible_count = 0
    for e in summary.visible_elements:
        if e.kind in {"content", "heading", "control", "cta", "label"} and e.font_size is not None:
            eligible_count += 1
            if e.font_size < 12:
                too_small.append({
                    "text": e.text[:80],
                    "fontSizePx": round(e.font_size, 2),
                    "family": e.family,
                })

    if eligible_count < 4:
        return None, [], {"reason": "too_few_typed_elements"}

    score = 94.0 if not too_small else max(20.0, 92.0 - len(too_small) * 8.0)
    return round(score, 2), too_small[:12], {"eligibleTextElementCount": eligible_count, "tooSmallCount": len(too_small)}


def _score_font_differentiation(summary: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    hs = summary.typography.heading_size_med
    cs = summary.typography.content_size_med
    hw = summary.typography.heading_weight_med
    cw = summary.typography.content_weight_med

    if hs is None or cs is None:
        return None, {"reason": "missing_heading_or_content_typography"}

    size_gap = hs - cs
    weight_gap = (hw or 0.0) - (cw or 0.0)

    score = 55 * _normalize_score(size_gap, 2.0, 10.0) + 45 * _normalize_score(weight_gap, 40.0, 260.0)

    return round(score, 2), {
        "headingSizeMedian": hs,
        "contentSizeMedian": cs,
        "headingWeightMedian": hw,
        "contentWeightMedian": cw,
        "sizeGap": round(size_gap, 2),
        "weightGap": round(weight_gap, 2),
    }


def _site_font_consistency_score(site_pages: List[PageSummary]) -> Tuple[Optional[float], Dict[str, Any]]:
    page_profiles = []
    for page in site_pages:
        families = sorted({e.font_family for e in page.visible_elements if e.font_family})
        sizes = sorted({round(e.font_size, 1) for e in page.visible_elements if e.font_size is not None})
        weights = sorted({e.font_weight for e in page.visible_elements if e.font_weight is not None})
        page_profiles.append({
            "page": page.page_ref["name"],
            "fontFamilies": families,
            "fontSizes": sizes[:12],
            "fontWeights": weights[:12],
        })

    if not page_profiles:
        return None, {"reason": "no_page_typography_profiles"}

    family_sets = {tuple(p["fontFamilies"]) for p in page_profiles}
    weight_sets = {tuple(p["fontWeights"]) for p in page_profiles}

    family_var = len(family_sets)
    weight_var = len(weight_sets)
    score = 100.0 - max(0, family_var - 1) * 12.0 - max(0, weight_var - 1) * 10.0
    score = _clamp(score, 25.0, 100.0)

    return round(score, 2), {
        "pageSummaries": page_profiles,
        "familyVariation": family_var,
        "weightVariation": weight_var,
    }


def _score_fonts_reinforce_hierarchy(summary: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    hs = summary.typography.heading_size_med
    cs = summary.typography.content_size_med
    ks = summary.typography.control_size_med

    if hs is None or cs is None:
        return None, {"reason": "missing_heading_or_content_typography"}

    heading_signal = _normalize_score(hs - cs, 2.0, 10.0)
    control_signal = 0.7 if ks is None else _normalize_score(abs(ks - cs), 0.8, 5.0)

    score = 65 * heading_signal + 35 * control_signal
    return round(score, 2), {
        "medianHeadingSize": hs,
        "medianContentSize": cs,
        "medianControlSize": ks,
    }


def _score_fonts_separate_labels_from_content(summary: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    ls = summary.typography.label_size_med
    cs = summary.typography.content_size_med
    lw = summary.typography.label_weight_med
    cw = summary.typography.content_weight_med

    if ls is None or cs is None:
        return None, {"reason": "missing_label_or_content_typography"}

    size_signal = _normalize_score(abs(ls - cs), 0.6, 4.0)
    weight_signal = _normalize_score(abs((lw or 0.0) - (cw or 0.0)), 20.0, 180.0)
    score = 50 * size_signal + 50 * weight_signal

    return round(score, 2), {
        "medianLabelSize": ls,
        "medianContentSize": cs,
        "medianLabelWeight": lw,
        "medianContentWeight": cw,
    }


def _score_fonts_separate_content_from_controls(summary: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    controls = summary.controls + summary.ctas
    if len(controls) < 2 or len(summary.content) < 2:
        return None, {"reason": "insufficient_control_or_content_elements"}

    control_families = {e.font_family for e in controls if e.font_family}
    content_families = {e.font_family for e in summary.content if e.font_family}

    family_signal = 1.0 if control_families.difference(content_families) else 0.35

    ks = summary.typography.control_size_med
    cs = summary.typography.content_size_med
    if ks is None or cs is None:
        return None, {"reason": "missing_control_or_content_typography"}

    size_signal = _normalize_score(abs(ks - cs), 0.8, 4.5)

    score = 40 * family_signal + 60 * size_signal
    return round(score, 2), {
        "controlFamilies": sorted(control_families),
        "contentFamilies": sorted(content_families),
        "medianControlSize": ks,
        "medianContentSize": cs,
    }


# ============================================================
# Strict aggregation helpers
# ============================================================

def _build_page_level_items(
    criterion: str,
    page_summaries: List[PageSummary],
    scoring_fn,
) -> Tuple[List[Dict[str, Any]], float]:
    items: List[Dict[str, Any]] = []

    for page in page_summaries:
        score, details, metrics = scoring_fn(page)

        if score is None:
            status = STATUS_NA
        else:
            status = _band_to_status(score, criterion)

        items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": details,
            "metrics": metrics,
            "archetype": page.archetype,
        })

    applicable_scores = [item["score"] for item in items if item["score"] is not None]
    site_score = round(_mean(applicable_scores) or 0.0, 2) if applicable_scores else 0.0
    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return items, coverage


def _strict_site_result_from_page_items(
    *,
    criterion: str,
    page_items: List[Dict[str, Any]],
    coverage: float,
    title_pass: str,
    title_warn: str,
    title_fail: str,
    title_na: str,
    description_pass: str,
    description_warn: str,
    description_fail: str,
    description_na: str,
    recommendation: Optional[str],
    method: List[str],
    extra_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    applicable_items = [item for item in page_items if item["status"] != STATUS_NA]
    na_items = [item for item in page_items if item["status"] == STATUS_NA]
    failing_items = [item for item in applicable_items if item["status"] == STATUS_FAIL]
    warning_items = [item for item in applicable_items if item["status"] == STATUS_WARNING]
    passing_items = [item for item in applicable_items if item["status"] == STATUS_PASS]

    site_score = round(_mean([item["score"] for item in applicable_items if item["score"] is not None]) or 0.0, 2) if applicable_items else None

    if not applicable_items:
        return _make_result(
            criterion=criterion,
            status=STATUS_NA,
            severity=None,
            title=title_na,
            description=description_na,
            pages=[],
            recommendation=None,
            evidence={
                "siteScore": None,
                "coverage": round(coverage, 3),
                "checkedPages": len(page_items),
                "applicablePagesCount": 0,
                "notApplicablePagesCount": len(na_items),
                "failingPages": [],
                "warningPages": [],
                "pageResults": page_items,
                **(extra_evidence or {}),
            },
            confidence="low",
            method=method,
            score=None,
        )

    if failing_items:
        final_status = STATUS_FAIL
        final_title = title_fail
        final_description = description_fail
        final_severity = SEVERITY_MEDIUM
        result_pages = _issue_pages_refs(failing_items)
    elif warning_items:
        final_status = STATUS_WARNING
        final_title = title_warn
        final_description = description_warn
        final_severity = SEVERITY_WARNING
        result_pages = _issue_pages_refs(warning_items)
    else:
        final_status = STATUS_PASS
        final_title = title_pass
        final_description = description_pass
        final_severity = None
        result_pages = _issue_pages_refs(passing_items)

    return _make_result(
        criterion=criterion,
        status=final_status,
        severity=final_severity,
        title=final_title,
        description=final_description,
        pages=result_pages,
        recommendation=recommendation if final_status != STATUS_PASS else None,
        evidence={
            "siteScore": site_score,
            "coverage": round(coverage, 3),
            "checkedPages": len(page_items),
            "applicablePagesCount": len(applicable_items),
            "notApplicablePagesCount": len(na_items),
            "failingPagesCount": len(failing_items),
            "warningPagesCount": len(warning_items),
            "passingPagesCount": len(passing_items),
            "failingPages": failing_items,
            "warningPages": warning_items,
            "notApplicablePages": na_items,
            "pageResults": page_items,
            **(extra_evidence or {}),
        },
        confidence=_confidence_from_coverage(coverage),
        method=method,
        score=site_score,
    )


def _single_site_result(
    *,
    criterion: str,
    score: Optional[float],
    pages: List[Dict[str, Any]],
    evidence: Dict[str, Any],
    method: List[str],
    recommendation: Optional[str],
    title_pass: str,
    title_warn: str,
    title_fail: str,
    title_na: str,
    description_pass: str,
    description_warn: str,
    description_fail: str,
    description_na: str,
    confidence: str = "high",
) -> Dict[str, Any]:
    if score is None:
        return _make_result(
            criterion=criterion,
            status=STATUS_NA,
            title=title_na,
            description=description_na,
            pages=[],
            severity=None,
            recommendation=None,
            evidence=evidence,
            confidence="low",
            method=method,
            score=None,
        )

    status = _band_to_status(score, criterion)
    title = title_pass if status == STATUS_PASS else title_warn if status == STATUS_WARNING else title_fail
    description = description_pass if status == STATUS_PASS else description_warn if status == STATUS_WARNING else description_fail

    return _make_result(
        criterion=criterion,
        status=status,
        severity=_status_severity(status),
        title=title,
        description=description,
        pages=pages,
        recommendation=recommendation if status != STATUS_PASS else None,
        evidence=evidence,
        confidence=confidence,
        method=method,
        score=score,
    )


# ============================================================
# Public checks
# ============================================================

def check_information_order_importance(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
    "information-order-importance",
    page_summaries,
    _score_information_order,
    )

    page_items = _apply_ai_review_to_page_items(
        "information-order-importance",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="information-order-importance",
        page_items=page_items,
        coverage=coverage,
        title_pass="Information order passes",
        title_warn="Information order is inconsistent",
        title_fail="Information order does not pass",
        title_na="Information order could not be evaluated",
        description_pass="The audited pages generally present information in a clear order of importance.",
        description_warn="Some pages only partially present information in a clear order of importance.",
        description_fail="At least one page does not present information in a clear order of importance.",
        description_na="There was not enough reliable evidence to evaluate information order.",
        recommendation="Strengthen the first-read experience by elevating page titles, key context, and next actions before repeated or secondary content.",
        method=["page-archetype-scoring", "top-zone-analysis", "prominence-analysis"],
    )


def check_visual_hierarchy_reflects_priority(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
    "visual-hierarchy-reflects-priority",
    page_summaries,
    _score_visual_hierarchy_reflects_priority,
    )

    page_items = _apply_ai_review_to_page_items(
        "visual-hierarchy-reflects-priority",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="visual-hierarchy-reflects-priority",
        page_items=page_items,
        coverage=coverage,
        title_pass="Visual hierarchy reflects priority",
        title_warn="Visual hierarchy only partially reflects priority",
        title_fail="Visual hierarchy does not reflect priority",
        title_na="Visual hierarchy could not be evaluated",
        description_pass="Important information is generally more visually prominent than supporting content.",
        description_warn="Some pages show only partial separation between important information and supporting content.",
        description_fail="At least one page does not visually elevate important information enough over supporting content.",
        description_na="There was not enough reliable evidence to evaluate visual hierarchy.",
        recommendation="Increase differentiation between headings, actions, and supporting content using size, contrast, position, and surrounding space.",
        method=["prominence-delta-analysis", "typographic-hierarchy-analysis"],
    )


def check_required_action_direction(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
    "required-action-direction",
    page_summaries,
    _score_required_action_direction,
    )

    page_items = _apply_ai_review_to_page_items(
        "required-action-direction",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="required-action-direction",
        page_items=page_items,
        coverage=coverage,
        title_pass="Required action direction passes",
        title_warn="Required action direction is only partially clear",
        title_fail="Required action direction does not pass",
        title_na="Required action direction is not applicable",
        description_pass="Task-oriented pages generally surface the next required action clearly.",
        description_warn="Some applicable pages surface the next action, but not strongly enough.",
        description_fail="At least one applicable page does not clearly surface the next required action.",
        description_na="This criterion was not applicable to the available page set.",
        recommendation="Place the next-step action higher in the visual hierarchy and reduce competing signals around it.",
        method=["task-page-archetype-analysis", "top-action-prominence-analysis"],
    )


def check_cta_primary_visual_element(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
    "cta-primary-visual-element",
    page_summaries,
    _score_cta_primary,
    )

    page_items = _apply_ai_review_to_page_items(
        "cta-primary-visual-element",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="cta-primary-visual-element",
        page_items=page_items,
        coverage=coverage,
        title_pass="CTA prominence passes",
        title_warn="CTA prominence is only partially effective",
        title_fail="CTA prominence does not pass",
        title_na="CTA prominence is not applicable",
        description_pass="Where applicable, primary calls to action receive appropriate visual prominence.",
        description_warn="Some applicable pages include a CTA, but it does not fully dominate the visual hierarchy.",
        description_fail="At least one applicable page does not make the main CTA a primary visual element.",
        description_na="This criterion was not applicable to the available page set.",
        recommendation="Increase CTA prominence through stronger contrast, placement, scale, and isolation from competing controls.",
        method=["conversion-page-archetype-analysis", "cta-ranking-analysis", "cta-contrast-analysis"],
    )


def check_visual_grouping_proximity_alignment(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
    "visual-grouping-proximity-alignment",
    page_summaries,
    _score_grouping,
    )

    page_items = _apply_ai_review_to_page_items(
        "visual-grouping-proximity-alignment",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="visual-grouping-proximity-alignment",
        page_items=page_items,
        coverage=coverage,
        title_pass="Visual grouping passes",
        title_warn="Visual grouping is only partially effective",
        title_fail="Visual grouping does not pass",
        title_na="Visual grouping could not be evaluated",
        description_pass="Related items are generally grouped using clear proximity and alignment patterns.",
        description_warn="Some pages show grouping patterns, but they are inconsistent or weak.",
        description_fail="At least one page does not group related items clearly enough through proximity and alignment.",
        description_na="There was not enough reliable evidence to evaluate grouping and alignment.",
        recommendation="Use clearer grouping containers, alignment systems, and predictable intra-group versus inter-group spacing.",
        method=["landmark-grouping-analysis", "alignment-proximity-analysis", "layout-context-analysis"],
    )


def check_negative_space_purpose(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
    "negative-space-purpose",
    page_summaries,
    _score_negative_space,
    )

    page_items = _apply_ai_review_to_page_items(
        "negative-space-purpose",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="negative-space-purpose",
        page_items=page_items,
        coverage=coverage,
        title_pass="Negative space use passes",
        title_warn="Negative space use is only partially effective",
        title_fail="Negative space use does not pass",
        title_na="Negative space use could not be evaluated",
        description_pass="Spacing generally supports scanning, grouping, and separation across the audited pages.",
        description_warn="Some pages appear somewhat dense or unevenly spaced.",
        description_fail="At least one page appears dense or under-spaced in ways that likely reduce scanability.",
        description_na="There was not enough reliable evidence to evaluate spacing and negative space.",
        recommendation="Increase breathing room between sections, groups, and actions while keeping spacing rhythms consistent.",
        method=["density-analysis", "spacing-rhythm-analysis", "top-zone-crowding-analysis"],
    )


def check_similar_information_consistency(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    score, family_issues, meta = _score_similar_information_consistency(page_summaries)
    pages = _issue_pages_refs([page.page_ref for page in page_summaries])

    return _single_site_result(
        criterion="similar-information-consistency",
        score=score,
        pages=pages if score is not None else [],
        evidence={
            "checkedPages": len(page_summaries),
            "familyIssues": family_issues,
            **meta,
        },
        method=["family-variant-analysis", "style-signature-analysis"],
        recommendation="Standardize repeated visual families such as headings, buttons, links, cards, and labels.",
        title_pass="Consistency of similar information passes",
        title_warn="Consistency of similar information is uneven",
        title_fail="Consistency of similar information does not pass",
        title_na="Consistency of similar information could not be evaluated",
        description_pass="Repeated information families generally use stable visual treatments across the audited pages.",
        description_warn="Some repeated information families vary more than expected across pages or templates.",
        description_fail="Repeated information families vary substantially across pages or templates.",
        description_na="There was not enough repeated component evidence to evaluate consistency.",
    )


def check_primary_color_count(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
        "ui-uses-no-more-than-3-primary-colors",
        page_summaries,
        _score_primary_color_count,
    )
    return _strict_site_result_from_page_items(
        criterion="ui-uses-no-more-than-3-primary-colors",
        page_items=page_items,
        coverage=coverage,
        title_pass="Primary color count passes",
        title_warn="Primary color count is slightly high",
        title_fail="Primary color count does not pass",
        title_na="Primary color count could not be evaluated",
        description_pass="The site generally uses a restrained number of dominant accent color families.",
        description_warn="Some pages use more dominant accent color families than expected.",
        description_fail="At least one page relies on too many dominant accent color families.",
        description_na="There was not enough palette evidence to evaluate primary color count.",
        recommendation="Reduce the number of dominant accent color families and treat nearby tonal shades as a single system.",
        method=["accent-color-clustering", "page-palette-analysis"],
    )


def check_chrome_desaturated(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
        "chrome-desaturated-colors",
        page_summaries,
        _score_chrome_desaturated,
    )
    return _strict_site_result_from_page_items(
        criterion="chrome-desaturated-colors",
        page_items=page_items,
        coverage=coverage,
        title_pass="UI chrome desaturation passes",
        title_warn="UI chrome is somewhat visually competitive",
        title_fail="UI chrome desaturation does not pass",
        title_na="UI chrome desaturation could not be evaluated",
        description_pass="Supporting UI chrome generally recedes enough for content and actions to stay in focus.",
        description_warn="Some supporting surfaces appear visually competitive with primary content or actions.",
        description_fail="At least one page has supporting UI chrome that competes too strongly with content or actions.",
        description_na="There was not enough chrome surface evidence to evaluate desaturation.",
        recommendation="Reduce saturation on utility surfaces, navigation chrome, borders, and secondary backgrounds.",
        method=["surface-saturation-analysis", "chrome-vs-cta-relative-analysis"],
    )


def check_colors_reinforce_hierarchy(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
        "colors-reinforce-hierarchy",
        page_summaries,
        _score_colors_reinforce_hierarchy,
    )

    page_items = _apply_ai_review_to_page_items(
        "colors-reinforce-hierarchy",
        page_items,
        page_summaries,
    )

    return _strict_site_result_from_page_items(
        criterion="colors-reinforce-hierarchy",
        page_items=page_items,
        coverage=coverage,
        title_pass="Color hierarchy reinforcement passes",
        title_warn="Color hierarchy reinforcement is partial",
        title_fail="Color hierarchy reinforcement does not pass",
        title_na="Color hierarchy reinforcement could not be evaluated",
        description_pass="Color is generally used to support hierarchy and action emphasis.",
        description_warn="Some pages use color for hierarchy, but not consistently or strongly enough.",
        description_fail="At least one page does not use color strongly enough to support hierarchy and action emphasis.",
        description_na="There was not enough heading or CTA color evidence to evaluate color hierarchy reinforcement.",
        recommendation="Reserve stronger accent colors for primary actions and hierarchy-critical elements.",
        method=["cta-vs-content-color-analysis", "heading-color-distinction-analysis", "contrast-analysis"],
    )


def check_color_scheme_consistency(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    score, evidence = _site_color_scheme_consistency(page_summaries)
    pages = [page.page_ref for page in page_summaries]

    return _single_site_result(
        criterion="color-scheme-consistency",
        score=score,
        pages=pages if score is not None else [],
        evidence=evidence,
        method=["cross-page-palette-overlap", "dominant-accent-family-analysis"],
        recommendation="Review shared palette tokens across templates and align accent families site-wide.",
        title_pass="Color scheme consistency passes",
        title_warn="Color scheme consistency is moderate",
        title_fail="Color scheme consistency does not pass",
        title_na="Color scheme consistency could not be evaluated",
        description_pass="Cross-page palette evidence suggests a stable site-wide color system.",
        description_warn="Cross-page palettes show partial consistency, but some templates diverge noticeably.",
        description_fail="Cross-page palettes diverge too much to suggest a stable site-wide color system.",
        description_na="There were not enough comparable page palettes to evaluate cross-page color consistency.",
        confidence="medium",
    )


def check_no_oversaturation(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, problems, metrics = _score_no_oversaturation(page)
        status = STATUS_NA if score is None else _band_to_status(score, "no-oversaturated-colors")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": problems,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="no-oversaturated-colors",
        page_items=page_items,
        coverage=coverage,
        title_pass="Oversaturation criterion passes",
        title_warn="Some oversaturated colors were detected",
        title_fail="Oversaturation criterion does not pass",
        title_na="Oversaturation criterion could not be evaluated",
        description_pass="The extracted palettes do not show strong signs of visually fatiguing saturation.",
        description_warn="Some pages include highly saturated colors that may feel visually aggressive in context.",
        description_fail="At least one page includes highly saturated colors likely to feel visually fatiguing.",
        description_na="There was not enough accent palette evidence to evaluate oversaturation.",
        recommendation="Reduce saturation on dominant accent colors and large surfaces, reserving intense colors for small intentional accents.",
        method=["palette-saturation-analysis", "accent-cluster-analysis"],
    )


def check_contrast_on_most_important_items(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
        "most-important-items-have-most-contrast",
        page_summaries,
        _score_important_items_have_most_contrast,
    )
    return _strict_site_result_from_page_items(
        criterion="most-important-items-have-most-contrast",
        page_items=page_items,
        coverage=coverage,
        title_pass="Priority contrast criterion passes",
        title_warn="Priority contrast criterion is moderate",
        title_fail="Priority contrast criterion does not pass",
        title_na="Priority contrast criterion could not be evaluated",
        description_pass="Higher-priority elements generally receive at least comparable or stronger contrast treatment.",
        description_warn="Some pages only partially align contrast emphasis with priority.",
        description_fail="At least one page does not align contrast emphasis strongly enough with the highest-priority elements.",
        description_na="There were not enough contrast samples to compare priority vs average elements.",
        recommendation="Increase contrast on primary headings and key actions where they compete with surrounding content.",
        method=["contrast-vs-priority-analysis", "top-band-contrast-analysis"],
    )


def check_contrast_primary_mechanism_for_hierarchy(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
        "contrast-primary-mechanism-for-hierarchy",
        page_summaries,
        _score_contrast_primary_mechanism,
    )
    return _strict_site_result_from_page_items(
        criterion="contrast-primary-mechanism-for-hierarchy",
        page_items=page_items,
        coverage=coverage,
        title_pass="Hierarchy contrast criterion passes",
        title_warn="Hierarchy contrast criterion is moderate",
        title_fail="Hierarchy contrast criterion does not pass",
        title_na="Hierarchy contrast criterion could not be evaluated",
        description_pass="Contrast generally contributes meaningfully to information hierarchy.",
        description_warn="Some pages rely on hierarchy cues, but contrast contributes only moderately.",
        description_fail="At least one page does not use contrast strongly enough to support hierarchy.",
        description_na="There was not enough heading/body contrast evidence to evaluate this criterion.",
        recommendation="Increase contrast separation between headings, key labels, and body content while preserving a coherent type scale.",
        method=["heading-vs-content-contrast-analysis", "type-scale-support-analysis"],
    )


def check_contrast_separates_content_from_controls(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, issues, metrics = _score_content_vs_controls_contrast(page)
        status = STATUS_NA if score is None else _band_to_status(score, "contrast-separates-content-from-controls")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": issues,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="contrast-separates-content-from-controls",
        page_items=page_items,
        coverage=coverage,
        title_pass="Control separation contrast criterion passes",
        title_warn="Control separation contrast criterion is moderate",
        title_fail="Control separation contrast criterion does not pass",
        title_na="Control separation contrast criterion could not be evaluated",
        description_pass="Controls generally have enough contrast to stand apart from surrounding content.",
        description_warn="Some controls have only moderate contrast relative to surrounding content.",
        description_fail="At least one page includes low-contrast controls that do not stand apart strongly enough from content.",
        description_na="There were too few controls to evaluate this criterion reliably.",
        recommendation="Increase contrast on buttons, links, and action triggers so they stand apart more clearly from nearby content.",
        method=["control-contrast-analysis"],
    )


def check_contrast_separates_labels_from_content(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items, coverage = _build_page_level_items(
        "contrast-separates-labels-from-content",
        page_summaries,
        _score_labels_vs_content_contrast,
    )
    return _strict_site_result_from_page_items(
        criterion="contrast-separates-labels-from-content",
        page_items=page_items,
        coverage=coverage,
        title_pass="Label/content contrast separation passes",
        title_warn="Label/content contrast separation is moderate",
        title_fail="Label/content contrast separation does not pass",
        title_na="Label/content contrast separation could not be evaluated",
        description_pass="Labels generally show enough visual distinction from the content they describe.",
        description_warn="Some pages show only limited contrast distinction between labels and content.",
        description_fail="At least one page does not show enough visual distinction between labels and the content they describe.",
        description_na="There were too few label/content elements to evaluate this criterion reliably.",
        recommendation="Use contrast and weight more deliberately for field labels, metadata labels, and descriptor text.",
        method=["label-vs-content-contrast-analysis", "label-vs-content-weight-analysis"],
    )


def check_foreground_distinguished_from_background(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, metrics = _score_foreground_background(page)
        status = STATUS_NA if score is None else _band_to_status(score, "foreground-distinguished-from-background")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": metrics,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="foreground-distinguished-from-background",
        page_items=page_items,
        coverage=coverage,
        title_pass="Foreground/background distinction passes",
        title_warn="Foreground/background distinction is moderate",
        title_fail="Foreground/background distinction does not pass",
        title_na="Foreground/background distinction could not be evaluated",
        description_pass="Foreground elements generally have acceptable contrast against their effective backgrounds.",
        description_warn="Some pages show only moderate contrast between foreground and background surfaces.",
        description_fail="At least one page shows weak foreground/background contrast likely to reduce readability or discoverability.",
        description_na="There were not enough contrast samples to evaluate foreground/background distinction reliably.",
        recommendation="Strengthen text and control contrast against their effective background surfaces.",
        method=["global-foreground-background-contrast"],
    )


def check_font_family_count(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    score, evidence = _site_font_family_score(page_summaries)

    return _single_site_result(
        criterion="no-more-than-two-font-families",
        score=score,
        pages=[page.page_ref for page in page_summaries] if score is not None else [],
        evidence=evidence,
        method=["font-family-usage-analysis"],
        recommendation="Reduce repeated font families or consolidate typography into a smaller branded system.",
        title_pass="Font family count passes",
        title_warn="Font family count is slightly high",
        title_fail="Font family count does not pass",
        title_na="Font family count could not be evaluated",
        description_pass="Rendered text usage appears limited to a restrained set of font families.",
        description_warn="The site uses more repeated font families than expected for a tightly controlled design system.",
        description_fail="The site relies on too many repeated font families to feel typographically restrained.",
        description_na="There was not enough font-family evidence to evaluate this criterion.",
    )


def check_content_font_size_minimum(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, issues, metrics = _score_font_size_minimum(page)
        status = STATUS_NA if score is None else _band_to_status(score, "content-fonts-at-least-12px")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": issues,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="content-fonts-at-least-12px",
        page_items=page_items,
        coverage=coverage,
        title_pass="Minimum content font size passes",
        title_warn="Minimum content font size is partially respected",
        title_fail="Minimum content font size does not pass",
        title_na="Minimum content font size could not be evaluated",
        description_pass="No strong evidence of undersized rendered content text was found.",
        description_warn="Some rendered text falls below the expected minimum size.",
        description_fail="At least one page contains rendered text elements below the expected minimum size.",
        description_na="There were too few typed elements to evaluate minimum font size reliably.",
        recommendation="Increase font size for small content, labels, controls, and utility text that falls below 12px.",
        method=["font-size-minimum-analysis"],
    )


def check_font_size_weight_differentiate_content_types(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, metrics = _score_font_differentiation(page)
        status = STATUS_NA if score is None else _band_to_status(score, "font-size-weight-differentiate-content-types")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": metrics,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="font-size-weight-differentiate-content-types",
        page_items=page_items,
        coverage=coverage,
        title_pass="Typography differentiation passes",
        title_warn="Typography differentiation is moderate",
        title_fail="Typography differentiation does not pass",
        title_na="Typography differentiation could not be evaluated",
        description_pass="Headings and content generally show meaningful typographic separation.",
        description_warn="Some pages show only moderate typographic separation between headings and content.",
        description_fail="At least one page does not show meaningful typographic separation between headings and content.",
        description_na="There was not enough heading/content typography data to evaluate this criterion.",
        recommendation="Increase size and/or weight separation between headings, subheadings, and paragraph text.",
        method=["typographic-differentiation-analysis"],
    )


def check_font_consistency_across_screens(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    score, evidence = _site_font_consistency_score(page_summaries)

    return _single_site_result(
        criterion="font-consistency-across-screens",
        score=score,
        pages=[page.page_ref for page in page_summaries] if score is not None else [],
        evidence=evidence,
        method=["cross-page-typography-comparison"],
        recommendation="Review shared typography tokens across templates and keep type families, sizes, and weights stable site-wide.",
        title_pass="Cross-screen font consistency passes",
        title_warn="Cross-screen font consistency is moderate",
        title_fail="Cross-screen font consistency does not pass",
        title_na="Cross-screen font consistency could not be evaluated",
        description_pass="The audited pages generally use a stable set of typography families, sizes, and weights.",
        description_warn="Typography varies somewhat across pages or templates.",
        description_fail="Typography varies too much across pages or templates to suggest a stable type system.",
        description_na="There was not enough typography profile evidence to evaluate cross-screen consistency.",
        confidence="medium",
    )


def check_fonts_reinforce_hierarchy(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, metrics = _score_fonts_reinforce_hierarchy(page)
        status = STATUS_NA if score is None else _band_to_status(score, "fonts-reinforce-hierarchy")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": metrics,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="fonts-reinforce-hierarchy",
        page_items=page_items,
        coverage=coverage,
        title_pass="Font hierarchy reinforcement passes",
        title_warn="Font hierarchy reinforcement is moderate",
        title_fail="Font hierarchy reinforcement does not pass",
        title_na="Font hierarchy reinforcement could not be evaluated",
        description_pass="Typography generally supports the distinction between headings, content, and actions.",
        description_warn="Typography supports hierarchy on some pages, but only moderately.",
        description_fail="At least one page has typography that does not support hierarchy strongly enough.",
        description_na="There was not enough typography data to evaluate font hierarchy reinforcement.",
        recommendation="Define a clearer type scale so headings, content, and controls form a stronger hierarchy system.",
        method=["hierarchical-type-scale-analysis"],
    )


def check_fonts_separate_labels_from_content(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, metrics = _score_fonts_separate_labels_from_content(page)
        status = STATUS_NA if score is None else _band_to_status(score, "fonts-separate-labels-from-content")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": metrics,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="fonts-separate-labels-from-content",
        page_items=page_items,
        coverage=coverage,
        title_pass="Label/content typography separation passes",
        title_warn="Label/content typography separation is moderate",
        title_fail="Label/content typography separation does not pass",
        title_na="Label/content typography separation is not applicable",
        description_pass="Labels generally show enough typographic distinction from content.",
        description_warn="Some pages show only moderate typographic distinction between labels and content.",
        description_fail="At least one page does not show enough typographic distinction between labels and content.",
        description_na="There were too few usable label elements to evaluate this criterion reliably.",
        recommendation="Differentiate labels from content more consistently through size, weight, case, or spacing.",
        method=["label-vs-content-typography-analysis"],
    )

def _apply_ai_review_to_page_items(
    criterion: str,
    page_items: list[dict],
    page_summaries: list,
) -> list[dict]:
    summary_map = {
        (p.page_ref["name"], p.page_ref["url"]): p
        for p in page_summaries
    }

    reviewed_items: list[dict] = []

    for item in page_items:
        enriched = dict(item)
        enriched["criterion"] = criterion

        if item.get("status") == "pass" and isinstance(item.get("score"), (int, float)) and item["score"] >= 85:
            reviewed_items.append(enriched)
            continue

        if not should_run_ai_review(enriched):
            reviewed_items.append(enriched)
            continue

        summary = summary_map.get((item["name"], item["url"]))
        if not summary:
            reviewed_items.append(enriched)
            continue

        top_heading_samples = [
            {
                "text": e.text[:80],
                "font_size": e.font_size,
                "font_weight": e.font_weight,
                "contrast": e.contrast,
                "prominence": e.prominence_score,
            }
            for e in sorted(summary.headings, key=lambda x: x.prominence_score, reverse=True)[:3]
        ]

        top_content_samples = [
            {
                "text": e.text[:80],
                "font_size": e.font_size,
                "font_weight": e.font_weight,
                "contrast": e.contrast,
                "prominence": e.prominence_score,
            }
            for e in sorted(summary.content, key=lambda x: x.prominence_score, reverse=True)[:3]
        ]

        top_control_samples = [
            {
                "text": e.text[:80],
                "font_size": e.font_size,
                "font_weight": e.font_weight,
                "contrast": e.contrast,
                "prominence": e.prominence_score,
                "family": e.family,
            }
            for e in sorted(summary.controls + summary.ctas, key=lambda x: x.prominence_score, reverse=True)[:3]
        ]

        extracted_summary = {
            "top_elements_count": len(summary.top_elements),
            "heading_count": len(summary.headings),
            "content_count": len(summary.content),
            "control_count": len(summary.controls),
            "cta_count": len(summary.ctas),
            "labels_count": len(summary.labels),
            "dominant_color_clusters": summary.dominant_color_clusters[:5],
            "neutral_color_clusters": summary.neutral_color_clusters[:5],
            "typography": {
                "heading_size_med": summary.typography.heading_size_med,
                "content_size_med": summary.typography.content_size_med,
                "control_size_med": summary.typography.control_size_med,
                "label_size_med": summary.typography.label_size_med,
                "heading_weight_med": summary.typography.heading_weight_med,
                "content_weight_med": summary.typography.content_weight_med,
                "control_weight_med": summary.typography.control_weight_med,
                "label_weight_med": summary.typography.label_weight_med,
            },
            "prominence": {
                "primary_heading": summary.primary_heading_prominence,
                "primary_cta": summary.primary_cta_prominence,
                "primary_control": summary.primary_control_prominence,
            },
            "samples": {
                "headings": top_heading_samples,
                "content": top_content_samples,
                "controls": top_control_samples,
            },
        }

        ai_result = review_page_criterion_with_ai(
            criterion=criterion,
            page_name=item["name"],
            page_url=item["url"],
            page_type=item.get("archetype", "generic"),
            deterministic_result={
                "status": item.get("status"),
                "score": item.get("score"),
                "details": item.get("details"),
            },
            page_metrics=item.get("metrics") or {},
            extracted_summary=extracted_summary,
        )

        suspicious_metrics = has_suspicious_metrics(enriched) or bool(ai_result.get("suspicious_metrics"))

        reconciliation = reconcile_deterministic_and_ai(
            deterministic_status=item.get("status", "warning"),
            ai_verdict=ai_result.get("final_verdict", item.get("status", "warning")),
            deterministic_score=item.get("score"),
            ai_confidence=ai_result.get("confidence", "low"),
            suspicious_metrics=suspicious_metrics,
        )

        enriched["ai_review"] = ai_result
        enriched["status"] = reconciliation["final_status"]
        enriched["ai_reconciliation"] = reconciliation
        enriched["metrics_suspicious"] = suspicious_metrics

        reviewed_items.append(enriched)

    return reviewed_items
def check_fonts_separate_content_from_controls(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, metrics = _score_fonts_separate_content_from_controls(page)
        status = STATUS_NA if score is None else _band_to_status(score, "fonts-separate-content-from-controls")
        page_items.append({
            **page.page_ref,
            "score": round(score, 2) if score is not None else None,
            "status": status,
            "details": metrics,
            "metrics": metrics,
            "archetype": page.archetype,
        })
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion="fonts-separate-content-from-controls",
        page_items=page_items,
        coverage=coverage,
        title_pass="Content/control typography separation passes",
        title_warn="Content/control typography separation is moderate",
        title_fail="Content/control typography separation does not pass",
        title_na="Content/control typography separation could not be evaluated",
        description_pass="The available evidence does not show strong conflicts between content and control typography.",
        description_warn="Some pages show only moderate typographic separation between content and controls.",
        description_fail="At least one page does not show enough typographic separation between content and controls.",
        description_na="There were too few control/content elements to evaluate this criterion reliably.",
        recommendation="Use stronger size, weight, casing, spacing, or family rules to distinguish interactive text from body content.",
        method=["control-vs-content-typography-analysis"],
    )


# ============================================================
# Orchestrator
# ============================================================

def _collect_page_summaries(
    person_a_data: Dict[str, Any],
    rendered_ui_data: Dict[str, Any],
) -> List[PageSummary]:
    persona_map = _persona_page_map(person_a_data)
    rendered_map = _rendered_page_map(rendered_ui_data)

    page_summaries: List[PageSummary] = []
    all_keys = sorted(set(persona_map.keys()) | set(rendered_map.keys()))
    for key in all_keys:
        persona_page = persona_map.get(key)
        rendered_page = rendered_map.get(key)
        if not persona_page and not rendered_page:
            continue
        page_summaries.append(_build_page_summary(persona_page, rendered_page))
    return page_summaries


def run_visual_hierarchy_checks(
    person_a_data: Dict[str, Any],
    rendered_ui_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    page_summaries = _collect_page_summaries(person_a_data, rendered_ui_data)
    if not page_summaries:
        return [
            _make_result(
                criterion="visual-hierarchy-checks",
                status=STATUS_NA,
                title="Visual hierarchy checks could not be evaluated",
                description="No page data was available to evaluate the visual hierarchy checklist.",
                pages=[],
                severity=None,
                recommendation="Verify that both person_a_cleaned.json and rendered_ui_extraction.json contain page-level data.",
                evidence={"checkedPages": 0},
                confidence="low",
                method=["page-summary-build"],
            )
        ]

    results: List[Dict[str, Any]] = []

    # General
    results.append(check_information_order_importance(page_summaries))
    results.append(check_visual_hierarchy_reflects_priority(page_summaries))
    results.append(check_required_action_direction(page_summaries))
    results.append(check_cta_primary_visual_element(page_summaries))
    results.append(check_visual_grouping_proximity_alignment(page_summaries))
    results.append(check_negative_space_purpose(page_summaries))
    results.append(check_similar_information_consistency(page_summaries))

    # Color
    results.append(check_primary_color_count(page_summaries))
    results.append(check_chrome_desaturated(page_summaries))
    results.append(check_colors_reinforce_hierarchy(page_summaries))
    results.append(check_color_scheme_consistency(page_summaries))
    results.append(check_no_oversaturation(page_summaries))

    # Contrast
    results.append(check_contrast_on_most_important_items(page_summaries))
    results.append(check_contrast_primary_mechanism_for_hierarchy(page_summaries))
    results.append(check_contrast_separates_content_from_controls(page_summaries))
    results.append(check_contrast_separates_labels_from_content(page_summaries))
    results.append(check_foreground_distinguished_from_background(page_summaries))

    # Fonts
    results.append(check_font_family_count(page_summaries))
    results.append(check_content_font_size_minimum(page_summaries))
    results.append(check_font_size_weight_differentiate_content_types(page_summaries))
    results.append(check_font_consistency_across_screens(page_summaries))
    results.append(check_fonts_reinforce_hierarchy(page_summaries))
    results.append(check_fonts_separate_labels_from_content(page_summaries))
    results.append(check_fonts_separate_content_from_controls(page_summaries))

    return results
