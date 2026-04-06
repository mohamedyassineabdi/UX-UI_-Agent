# src/audit/checks/interaction_controls_checks.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple, Set
from collections import Counter, defaultdict
import math
import re

from .ai_review_layer import review_page_criterion_with_ai
from .ai_reconciliation import should_run_ai_review, reconcile_deterministic_and_ai
from .common import build_evidence_bundle


# ============================================================
# Constants
# ============================================================

CATEGORY = "interaction_controls"

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
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+")

# Good action verbs
ACTION_VERBS = {
    # English
    "add", "apply", "buy", "book", "cancel", "change", "check", "choose",
    "clear", "close", "confirm", "continue", "copy", "create", "delete",
    "download", "edit", "filter", "find", "go", "join", "learn", "load",
    "log", "login", "logout", "next", "open", "pay", "preview", "register",
    "remove", "reset", "retry", "save", "search", "select", "send", "shop",
    "show", "sign", "start", "submit", "subscribe", "try", "update", "view",

    # French
    "ajouter", "acheter", "annuler", "appliquer", "changer", "choisir",
    "chercher", "commander", "confirmer", "continuer", "copier", "créer",
    "envoyer", "fermer", "filtrer", "inscrire", "modifier", "ouvrir",
    "payer", "rechercher", "réessayer", "réinitialiser", "retirer", "supprimer",
    "sauvegarder", "sélectionner", "soumettre", "suivant", "télécharger",
    "valider", "voir",
}

# Terms that often feel system-oriented or technical to normal users
SYSTEM_ORIENTED_TERMS = {
    "execute", "trigger", "invoke", "dispatch", "sync", "synchronize",
    "pipeline", "process", "submit form", "endpoint", "payload", "mutation",
    "query", "crud", "workflow engine", "handler", "continue flow", "commit",
    "rollback", "apply changeset", "save object", "persist", "userinput",
    "rpc", "webhook", "job",

    # French / mixed
    "exécuter", "déclencher", "pipeline", "workflow", "synchroniser",
    "point de terminaison", "payload", "mutation", "requête", "persister",
    "commit", "rollback",
}

# Strong destructive words
DESTRUCTIVE_TERMS = {
    "delete", "remove", "clear", "reset", "erase", "destroy", "discard",
    "unsubscribe", "deactivate", "close account", "cancel order", "empty cart",
    "supprimer", "retirer", "vider", "réinitialiser", "effacer", "annuler",
    "désactiver", "désinscrire",
}

# Secondary / lightweight actions
SECONDARY_ACTION_TERMS = {
    "cancel", "close", "hide", "dismiss", "back", "later", "skip",
    "annuler", "fermer", "masquer", "retour", "plus tard", "ignorer",
}

# Frequent utility features
FREQUENT_FEATURE_TERMS = {
    "search", "filter", "cart", "account", "contact", "menu", "sort",
    "recherche", "filtrer", "panier", "compte", "contact", "menu", "trier",
}

# Widget hints for autocompletion / editable dropdown behavior
EDITABLE_DROPDOWN_HINTS = {
    "search", "combobox", "autocomplete", "filter", "type to search",
    "recherche", "saisie", "auto", "suggest",
}


@dataclass(frozen=True)
class ScoreBand:
    fail_below: float
    warning_below: float


CRITERION_BANDS: Dict[str, ScoreBand] = {
    "cta-clearly-labeled-and-clickable": ScoreBand(58, 75),
    "verbs-used-for-actions": ScoreBand(60, 80),
    "interactive-labeling-familiar-not-system-oriented": ScoreBand(58, 78),
    "users-have-control-over-interactive-workflows": ScoreBand(50, 72),
    "ui-responds-consistently-to-user-actions": ScoreBand(52, 74),
    "frequently-used-features-readily-available": ScoreBand(58, 78),
    "default-primary-actions-not-destructive": ScoreBand(68, 84),
    "destructive-actions-confirmed-before-execution": ScoreBand(45, 70),
    "red-reserved-for-destructive-actions": ScoreBand(58, 78),
    "standard-browser-functions-supported": ScoreBand(55, 75),

    "controls-placed-consistently": ScoreBand(58, 78),
    "controls-related-to-surrounding-information": ScoreBand(58, 78),
    "interactive-elements-not-abstracted": ScoreBand(58, 78),
    "editable-droplists-where-applicable": ScoreBand(48, 72),
    "controls-provide-hints-help-tooltips-where-applicable": ScoreBand(48, 72),
    "primary-secondary-tertiary-controls-visually-distinct": ScoreBand(58, 78),
    "secondary-actions-displayed-as-links": ScoreBand(52, 74),
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
        m = _NUMBER_RE.search(s)
        if not m:
            return None
        try:
            return float(m.group(0))
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
    return _parse_float(s)


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def _ratio(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def _mean(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _median(values: Iterable[float]) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    m = n // 2
    if n % 2 == 1:
        return vals[m]
    return (vals[m - 1] + vals[m]) / 2.0


def _normalize_score(v: Optional[float], min_v: float, max_v: float) -> float:
    if v is None or max_v <= min_v:
        return 0.0
    return _clamp((v - min_v) / (max_v - min_v), 0.0, 1.0)


def _page_ref(page: Dict[str, Any]) -> Dict[str, Any]:
    page_meta = (page.get("pageMeta") or {}).get("data", {}) if isinstance(page, dict) else {}
    screenshot_paths = page_meta.get("screenshotPaths", {}) or {}
    return {
        "name": page.get("name", ""),
        "url": page.get("url", ""),
        "finalUrl": page.get("finalUrl", page.get("url", "")),
        "screenshotPath": screenshot_paths.get("page", "") if isinstance(screenshot_paths, dict) else "",
    }


def _issue_pages_refs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[Tuple[str, str, str]] = set()
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

def _page_names(items: List[Dict[str, Any]]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        name = _text(item.get("name"))
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _visible_pages_for_status(
    failing: List[Dict[str, Any]],
    warning: List[Dict[str, Any]],
    passing: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    What should be exposed in top-level `pages`:
    - fail => failing pages
    - warning => warning pages
    - pass => passing pages
    """
    if failing:
        return _issue_pages_refs(failing)
    if warning:
        return _issue_pages_refs(warning)
    return _issue_pages_refs(passing)


def _count_manual_review_pages(items: List[Dict[str, Any]]) -> int:
    count = 0
    for item in items:
        ai_review = item.get("ai_review") or {}
        if ai_review.get("needs_manual_review") is True:
            count += 1
    return count


def _count_suspicious_pages(items: List[Dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("metrics_suspicious") is True)
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
    evidence_bundle: Optional[Dict[str, Any]] = None,
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
    if evidence_bundle is not None:
        result["evidence_bundle"] = evidence_bundle
    if confidence is not None:
        result["confidence"] = confidence
    if method is not None:
        result["method"] = method
    if score is not None:
        result["score"] = round(score, 2)
    return result


# ============================================================
# AI bridge
# ============================================================

def _apply_ai_review_to_page_items(
    criterion: str,
    page_items: List[Dict[str, Any]],
    page_summaries: List["PageSummary"],
) -> List[Dict[str, Any]]:
    summary_map = {(p.page_ref["name"], p.page_ref["url"]): p for p in page_summaries}
    reviewed: List[Dict[str, Any]] = []

    for item in page_items:
        enriched = dict(item)
        enriched["criterion"] = criterion

        if item.get("status") == STATUS_PASS and isinstance(item.get("score"), (int, float)) and item["score"] >= 88:
            reviewed.append(enriched)
            continue

        if not should_run_ai_review(enriched):
            reviewed.append(enriched)
            continue

        summary = summary_map.get((item.get("name", ""), item.get("url", "")))
        if not summary:
            reviewed.append(enriched)
            continue

        extracted_summary = {
            "controls_count": len(summary.controls),
            "ctas_count": len(summary.ctas),
            "forms_count": len(summary.forms),
            "selects_count": len(summary.select_controls),
            "destructive_count": len(summary.destructive_controls),
            "top_controls_count": len(summary.top_controls),
            "families": {
                "control_families": list(summary.control_families)[:10],
                "cta_families": list(summary.cta_families)[:10],
            },
            "layout": {
                "top_zone_controls": len(summary.top_controls),
                "nav_controls": len(summary.nav_controls),
                "touch_target_failures": len(summary.small_touch_targets),
            },
            "labels": {
                "missing_labels": len(summary.controls_missing_labels),
                "verb_ratio": summary.action_verb_ratio,
            },
            "destructive": {
                "has_destructive_controls": bool(summary.destructive_controls),
                "destructive_red_ratio": summary.destructive_red_ratio,
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
            deterministic_status=item.get("status", STATUS_WARNING),
            ai_verdict=ai_result.get("final_verdict", item.get("status", STATUS_WARNING)),
            deterministic_score=item.get("score"),
        )

        enriched["ai_review"] = ai_result
        enriched["status"] = reconciliation["final_status"]
        enriched["ai_reconciliation"] = reconciliation
        reviewed.append(enriched)

    return reviewed


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


def _is_red_like(rgb: Tuple[int, int, int, float]) -> bool:
    h, s, l = _rgb_to_hsl(rgb)
    red_zone = (h <= 0.05) or (h >= 0.95)
    return red_zone and s >= 0.35 and 0.15 <= l <= 0.75


# ============================================================
# Element model
# ============================================================

@dataclass
class ElementModel:
    raw: Dict[str, Any]
    page_name: str
    page_url: str
    screenshot_path: str

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

    font_family: str
    font_size: Optional[float]
    font_weight: Optional[int]

    text_color: Optional[Tuple[int, int, int, float]]
    bg_color: Optional[Tuple[int, int, int, float]]
    contrast: Optional[float]

    semantic_type: str
    component_variant: str
    ux_role: str
    business_role: str
    layout_mode: str
    parent_display: str
    sibling_count: int
    landmark: str

    placeholder: str
    label: str
    accessible_name: str
    href: str
    role_attr: str
    tag: str
    input_type: str

    touch_target_pass: Optional[bool]
    interactive_hint: bool
    has_visible_label: bool
    has_associated_label: bool
    disabled: bool
    required: bool
    read_only: bool
    checked: bool

    style_signature: str = ""
    component_group_id: str = ""
    prominence_score: float = 0.0

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def clickable(self) -> bool:
        return self.kind in {"cta", "control", "select", "dropdown", "link-control"}


@dataclass
class FormField:
    tag: str
    type: str
    name: str
    id: str
    label: str
    placeholder: str
    required: bool
    disabled: bool


@dataclass
class PageSummary:
    page_ref: Dict[str, Any]
    archetype: str
    all_elements: List[ElementModel]
    visible_elements: List[ElementModel]

    controls: List[ElementModel]
    ctas: List[ElementModel]
    links: List[ElementModel]
    select_controls: List[ElementModel]
    dropdown_like_controls: List[ElementModel]
    forms: List[ElementModel]
    form_fields: List[FormField]
    user_input_fields: List[FormField]

    destructive_controls: List[ElementModel]
    top_controls: List[ElementModel]
    nav_controls: List[ElementModel]
    small_touch_targets: List[ElementModel]
    controls_missing_labels: List[ElementModel]

    control_families: Set[str] = field(default_factory=set)
    cta_families: Set[str] = field(default_factory=set)

    action_verb_ratio: float = 0.0
    destructive_red_ratio: Optional[float] = None
    page_width: Optional[float] = None
    scroll_height: Optional[float] = None
    buttons_count: int = 0
    forms_count: int = 0


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
# Primitive extraction
# ============================================================

def _font_family_normalized(raw: Any) -> str:
    text = _text(raw).replace('"', "").replace("'", "").strip().lower()
    if not text:
        return ""
    return text.split(",")[0].strip()


def _element_text_name(el: Dict[str, Any]) -> str:
    return (
        _text(el.get("accessibleName"))
        or _text(el.get("label"))
        or _text(el.get("text"))
        or _text(el.get("name"))
        or _text(el.get("ariaLabel"))
        or _text(el.get("title"))
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
        try:
            return round(_contrast_ratio(fg, bg), 2)
        except Exception:
            return None
    return None


def _element_xywh(el: Dict[str, Any]) -> Tuple[float, float, float, float]:
    rect = el.get("rect") or {}
    x = _parse_float(rect.get("x")) or 0.0
    y = _parse_float(rect.get("y")) or 0.0
    w = _parse_float(rect.get("width")) or 0.0
    h = _parse_float(rect.get("height")) or 0.0
    return x, y, max(w, 0.0), max(h, 0.0)


def _is_action_like_label(text: str) -> bool:
    if not text:
        return False
    first = _WORD_RE.findall(_lower(text))
    if not first:
        return False
    token = first[0]
    return token in ACTION_VERBS


def _looks_system_oriented(text: str) -> bool:
    t = _lower(text)
    return any(term in t for term in SYSTEM_ORIENTED_TERMS)


def _looks_destructive(text: str, ux_role: str, href: str) -> bool:
    blob = " ".join([_lower(text), _lower(ux_role), _lower(href)])
    return any(term in blob for term in DESTRUCTIVE_TERMS)


def _looks_secondary_action(text: str, ux_role: str) -> bool:
    blob = " ".join([_lower(text), _lower(ux_role)])
    return any(term in blob for term in SECONDARY_ACTION_TERMS)


def _looks_dropdown(el: Dict[str, Any]) -> bool:
    tag = _lower(el.get("tag"))
    semantic = _lower(el.get("semanticType"))
    ux = _lower(el.get("uxRole"))
    role_attr = _lower(el.get("role"))
    text = _lower(_element_text_name(el))
    if tag == "select" or semantic == "select":
        return True
    if role_attr in {"combobox", "listbox"}:
        return True
    if "dropdown" in ux or "select" in ux:
        return True
    return any(h in text for h in {"sort", "trier", "country", "pays/région"})


def _looks_editable_dropdown(el: Dict[str, Any]) -> bool:
    tag = _lower(el.get("tag"))
    role_attr = _lower(el.get("role"))
    input_type = _lower(el.get("type"))
    placeholder = _lower(el.get("placeholder"))
    text = _lower(_element_text_name(el))
    if tag == "input" and input_type in {"search", "text"}:
        return True
    if role_attr == "combobox":
        return True
    blob = " ".join([placeholder, text])
    return any(h in blob for h in EDITABLE_DROPDOWN_HINTS)


def _element_family(el: Dict[str, Any]) -> str:
    semantic = _lower(el.get("semanticType"))
    ux_role = _lower(el.get("uxRole"))
    tag = _lower(el.get("tag"))
    variant = _lower(el.get("componentVariant"))

    if ux_role in {"primary-cta", "secondary-cta"}:
        return f"cta::{ux_role}"
    if semantic == "cta-link":
        return f"cta::{variant or 'default'}"
    if tag == "select" or semantic == "select":
        return f"select::{variant or 'default'}"
    if semantic in {"button", "button-ghost"} or tag == "button" or _lower(el.get("role")) == "button":
        return f"button::{variant or semantic or 'default'}"
    if tag == "a":
        return f"link::{ux_role or variant or 'default'}"
    if semantic in {"input", "textarea", "form"}:
        return f"form::{semantic or tag}"
    return semantic or tag or "generic"


def _element_kind(el: Dict[str, Any]) -> str:
    semantic = _lower(el.get("semanticType"))
    ux_role = _lower(el.get("uxRole"))
    tag = _lower(el.get("tag"))
    role_attr = _lower(el.get("role"))

    if ux_role in {"primary-cta", "secondary-cta", "search-submit"} or semantic == "cta-link":
        return "cta"

    if tag == "select" or semantic == "select":
        return "select"

    if _looks_dropdown(el):
        return "dropdown"

    if semantic in {"button", "button-ghost"} or tag == "button" or role_attr == "button":
        return "control"

    if tag == "a":
        if "menu" in ux_role or "nav" in ux_role:
            return "nav"
        return "link-control"

    if semantic in {"input", "textarea", "form"}:
        return "form"

    return "other"


def _element_prominence_score(el: Dict[str, Any], viewport_height: Optional[float]) -> float:
    x, y, width, height = _element_xywh(el)
    area = width * height
    font_size = _element_font_size(el) or 0.0
    font_weight = _element_font_weight(el) or 400
    contrast = _element_contrast(el) or 0.0
    kind = _element_kind(el)
    above_fold = bool(el.get("isAboveTheFold") is True)
    if viewport_height is not None:
        above_fold = y < viewport_height * 0.95

    kind_boost = {
        "cta": 18.0,
        "control": 12.0,
        "select": 11.0,
        "dropdown": 10.0,
        "link-control": 8.0,
        "form": 7.0,
    }.get(kind, 0.0)

    area_score = min(area / 14000.0, 14.0)
    font_score = min(max(font_size - 12.0, 0.0), 16.0)
    weight_score = min(max(font_weight - 400, 0.0) / 60.0, 6.0)
    contrast_score = min(max(contrast - 3.0, 0.0), 8.0)
    fold_boost = 8.0 if above_fold else 0.0

    return round(kind_boost + area_score + font_score + weight_score + contrast_score + fold_boost, 2)


# ============================================================
# Archetype detection
# ============================================================

def _detect_page_archetype(persona_page: Optional[Dict[str, Any]], rendered_page: Optional[Dict[str, Any]], visible_elements: List[ElementModel]) -> str:
    page = rendered_page or persona_page or {}
    blob = " ".join([
        _lower(page.get("name")),
        _lower(page.get("url")),
        _lower(page.get("finalUrl")),
    ])

    if any(k in blob for k in {"cart", "checkout", "contact", "login", "register", "account", "support", "panier"}):
        return "task"
    if any(k in blob for k in {"collection", "collections", "catalog", "shop", "product", "sort", "filter"}):
        return "catalog"

    controls = [e for e in visible_elements if e.kind in {"cta", "control", "select", "dropdown", "link-control"}]
    forms = [e for e in visible_elements if e.kind == "form"]

    if len(forms) >= 1 and len(controls) >= 2:
        return "task"
    if len(controls) >= 8:
        return "catalog"
    return "generic"


# ============================================================
# Summary builders
# ============================================================

def _build_elements_for_page(
    rendered_page: Optional[Dict[str, Any]],
    page_name: str,
    page_url: str,
    screenshot_path: str,
    viewport_height: Optional[float],
) -> List[ElementModel]:
    out: List[ElementModel] = []

    for raw in _get_all_rendered_elements(rendered_page or {}):
        kind = _element_kind(raw)
        if kind == "other":
            continue

        x, y, width, height = _element_xywh(raw)

        landmark_info = raw.get("closestLandmark") or {}
        landmark = (
            _text(landmark_info.get("xpathHint"))
            or _text(landmark_info.get("className"))
            or _text(landmark_info.get("tag"))
        )

        parent_display = _lower(_safe_get(raw, "layoutContext", "parentDisplay"))
        sibling_count = _parse_int(_safe_get(raw, "layoutContext", "siblingCount")) or 0

        font_family = (
            _font_family_normalized(_safe_get(raw, "styles", "fontFamily"))
            or _font_family_normalized(_safe_get(raw, "tokens", "fontFamily"))
        )

        model = ElementModel(
            raw=raw,
            page_name=page_name,
            page_url=page_url,
            screenshot_path=screenshot_path,
            kind=kind,
            family=_element_family(raw),
            text=_element_text_name(raw),
            visible=raw.get("visible") is not False,
            above_fold=bool(raw.get("isAboveTheFold") is True),
            x=x,
            y=y,
            width=width,
            height=height,
            area=width * height,
            font_family=font_family,
            font_size=_element_font_size(raw),
            font_weight=_element_font_weight(raw),
            text_color=_element_text_color(raw),
            bg_color=_element_bg_color(raw),
            contrast=_element_contrast(raw),
            semantic_type=_lower(raw.get("semanticType")),
            component_variant=_lower(raw.get("componentVariant")),
            ux_role=_lower(raw.get("uxRole")),
            business_role=_lower(raw.get("businessRole")),
            layout_mode=_lower(raw.get("layoutMode")),
            parent_display=parent_display,
            sibling_count=sibling_count,
            landmark=landmark,
            placeholder=_text(raw.get("placeholder")),
            label=_text(raw.get("label")),
            accessible_name=_text(raw.get("accessibleName")),
            href=_text(raw.get("href")),
            role_attr=_text(raw.get("role")),
            tag=_lower(raw.get("tag")),
            input_type=_lower(raw.get("type")),
            touch_target_pass=raw.get("touchTargetPass"),
            interactive_hint=bool(raw.get("interactiveHint")),
            has_visible_label=bool(raw.get("hasVisibleLabel")),
            has_associated_label=bool(raw.get("hasAssociatedLabel")),
            disabled=bool(raw.get("disabled")),
            required=bool(raw.get("required")),
            read_only=bool(raw.get("readOnly")),
            checked=bool(raw.get("checked")),
            style_signature=_text(raw.get("styleSignature")),
            component_group_id=_text(raw.get("componentGroupId")),
            prominence_score=_element_prominence_score(raw, viewport_height),
        )

        if viewport_height is not None:
            model.above_fold = model.y < viewport_height * 0.95

        out.append(model)

    return out


def _extract_form_fields(persona_page: Optional[Dict[str, Any]]) -> Tuple[List[FormField], List[FormField]]:
    items = _safe_get(persona_page or {}, "forms", "data", "items", default=[]) or []
    all_fields: List[FormField] = []
    user_fields: List[FormField] = []

    def _to_field(raw: Dict[str, Any]) -> FormField:
        return FormField(
            tag=_lower(raw.get("tag")),
            type=_lower(raw.get("type")),
            name=_text(raw.get("name")),
            id=_text(raw.get("id")),
            label=_text(raw.get("label")),
            placeholder=_text(raw.get("placeholder")),
            required=bool(raw.get("required")),
            disabled=bool(raw.get("disabled")),
        )

    for form in items:
        for field in form.get("allFields", []) or []:
            if isinstance(field, dict):
                all_fields.append(_to_field(field))
        for field in form.get("userInputFields", []) or []:
            if isinstance(field, dict):
                user_fields.append(_to_field(field))

    return all_fields, user_fields


def _build_page_summary(persona_page: Optional[Dict[str, Any]], rendered_page: Optional[Dict[str, Any]]) -> PageSummary:
    page_ref = _page_ref(persona_page or rendered_page or {})
    viewport_height = _parse_float(_safe_get(persona_page or {}, "pageMeta", "data", "viewport", "height"))
    scroll_height = _parse_float(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "scrollHeight"))
    page_width = _parse_float(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "scrollWidth"))
    buttons_count = int(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "buttons", default=0) or 0)
    forms_count = int(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "forms", default=0) or 0)

    elements = _build_elements_for_page(rendered_page, page_ref["name"], page_ref["url"], page_ref.get("screenshotPath", ""), viewport_height)
    visible = [e for e in elements if e.visible]

    archetype = _detect_page_archetype(persona_page, rendered_page, visible)
    controls = [e for e in visible if e.kind == "control"]
    ctas = [e for e in visible if e.kind == "cta"]
    links = [e for e in visible if e.kind == "link-control"]
    selects = [e for e in visible if e.kind == "select"]
    dropdowns = [e for e in visible if e.kind == "dropdown"]
    forms = [e for e in visible if e.kind == "form"]

    all_fields, user_fields = _extract_form_fields(persona_page)

    all_clickables = controls + ctas + links + selects + dropdowns
    top_controls = sorted([e for e in all_clickables if e.above_fold], key=lambda e: e.prominence_score, reverse=True)[:12]
    nav_controls = [e for e in all_clickables if "nav" in e.kind or "menu" in e.ux_role or "nav" in e.ux_role]
    destructive = [e for e in all_clickables if _looks_destructive(e.text or e.accessible_name, e.ux_role, e.href)]
    small_touch_targets = [e for e in all_clickables if e.touch_target_pass is False]
    controls_missing_labels = [e for e in all_clickables if not (e.text or e.accessible_name or e.label)]

    action_like = [e for e in all_clickables if (e.text or e.accessible_name)]
    action_verb_ratio = _ratio(
        sum(1 for e in action_like if _is_action_like_label(e.text or e.accessible_name)),
        len(action_like),
    )

    destructive_red_ratio: Optional[float] = None
    if destructive:
        destructive_red_ratio = _ratio(
            sum(1 for e in destructive if ((e.bg_color and _is_red_like(e.bg_color)) or (e.text_color and _is_red_like(e.text_color)))),
            len(destructive),
        )

    return PageSummary(
        page_ref=page_ref,
        archetype=archetype,
        all_elements=elements,
        visible_elements=visible,
        controls=controls,
        ctas=ctas,
        links=links,
        select_controls=selects,
        dropdown_like_controls=dropdowns,
        forms=forms,
        form_fields=all_fields,
        user_input_fields=user_fields,
        destructive_controls=destructive,
        top_controls=top_controls,
        nav_controls=nav_controls,
        small_touch_targets=small_touch_targets,
        controls_missing_labels=controls_missing_labels,
        control_families={e.family for e in controls + selects + dropdowns + links},
        cta_families={e.family for e in ctas},
        action_verb_ratio=round(action_verb_ratio, 3),
        destructive_red_ratio=round(destructive_red_ratio, 3) if destructive_red_ratio is not None else None,
        page_width=page_width,
        scroll_height=scroll_height,
        buttons_count=buttons_count,
        forms_count=forms_count,
    )


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


# ============================================================
# Shared strict site result builder
# ============================================================

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
    page_summaries: Optional[List[PageSummary]] = None,
) -> Dict[str, Any]:
    reviewed_items = page_items
    if page_summaries:
        reviewed_items = _apply_ai_review_to_page_items(criterion, page_items, page_summaries)
    evidence_bundle = _interaction_evidence_bundle(criterion, reviewed_items, page_summaries)

    applicable = [p for p in reviewed_items if p.get("status") != STATUS_NA]
    failing = [p for p in applicable if p.get("status") == STATUS_FAIL]
    warning = [p for p in applicable if p.get("status") == STATUS_WARNING]
    passing = [p for p in applicable if p.get("status") == STATUS_PASS]
    not_applicable = [p for p in reviewed_items if p.get("status") == STATUS_NA]

    applicable_scores = [p["score"] for p in applicable if isinstance(p.get("score"), (int, float))]
    site_score = round(_mean(applicable_scores) or 0.0, 2) if applicable_scores else None

    manual_review_pages_count = _count_manual_review_pages(reviewed_items)
    suspicious_pages_count = _count_suspicious_pages(reviewed_items)

    if not applicable:
        return _make_result(
            criterion=criterion,
            status=STATUS_NA,
            severity=None,
            title=title_na,
            description=description_na,
            pages=[],
            recommendation=recommendation,
            evidence={
                "siteScore": None,
                "coverage": round(coverage, 3),
                "checkedPages": len(reviewed_items),
                "applicablePagesCount": 0,
                "notApplicablePagesCount": len(not_applicable),
                "failingPagesCount": 0,
                "warningPagesCount": 0,
                "passingPagesCount": 0,
                "manualReviewPagesCount": manual_review_pages_count,
                "suspiciousPagesCount": suspicious_pages_count,
                "failingPages": [],
                "warningPages": [],
                "passingPages": [],
                "notApplicablePages": not_applicable,
                "summary": {
                    "failedPageNames": [],
                    "warningPageNames": [],
                    "passedPageNames": [],
                    "notApplicablePageNames": _page_names(not_applicable),
                },
                "pageResults": reviewed_items,
            },
            evidence_bundle=evidence_bundle,
            confidence=_confidence_from_coverage(coverage),
            method=method,
            score=None,
        )

    if failing:
        status = STATUS_FAIL
        title = title_fail
        description = description_fail
    elif warning:
        status = STATUS_WARNING
        title = title_warn
        description = description_warn
    else:
        status = STATUS_PASS
        title = title_pass
        description = description_pass

    visible_pages = _visible_pages_for_status(failing, warning, passing)

    return _make_result(
        criterion=criterion,
        status=status,
        severity=_status_severity(status),
        title=title,
        description=description,
        pages=visible_pages,
        recommendation=recommendation,
        evidence={
            "siteScore": site_score,
            "coverage": round(coverage, 3),
            "checkedPages": len(reviewed_items),
            "applicablePagesCount": len(applicable),
            "notApplicablePagesCount": len(not_applicable),
            "failingPagesCount": len(failing),
            "warningPagesCount": len(warning),
            "passingPagesCount": len(passing),
            "manualReviewPagesCount": manual_review_pages_count,
            "suspiciousPagesCount": suspicious_pages_count,
            "failingPages": failing,
            "warningPages": warning,
            "passingPages": passing,
            "notApplicablePages": not_applicable,
            "summary": {
                "failedPageNames": _page_names(failing),
                "warningPageNames": _page_names(warning),
                "passedPageNames": _page_names(passing),
                "notApplicablePageNames": _page_names(not_applicable),
            },
            "pageResults": reviewed_items,
        },
        evidence_bundle=evidence_bundle,
        confidence=_confidence_from_coverage(coverage),
        method=method,
        score=site_score,
    )
# ============================================================
# Criterion scoring helpers
# ============================================================

def _score_clickable_cta_labeling(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.ctas + page.controls + page.links
    if not candidates:
        return None, {"reason": "no_clickable_controls"}

    labeled = [e for e in candidates if (e.text or e.accessible_name or e.label)]
    pointer_like = [
        e for e in candidates
        if _lower(_safe_get(e.raw, "styles", "cursor")) == "pointer"
        or e.tag in {"button", "a"}
        or e.role_attr.lower() == "button"
    ]

    label_signal = _ratio(len(labeled), len(candidates))
    pointer_signal = _ratio(len(pointer_like), len(candidates))
    touch_signal = _ratio(sum(1 for e in candidates if e.touch_target_pass is not False), len(candidates))

    score = 45 * label_signal + 35 * pointer_signal + 20 * touch_signal
    details = []
    if label_signal < 0.9:
        details.append("some-clickable-controls-lack-clear-labels")
    if pointer_signal < 0.85:
        details.append("some-actions-do-not-strongly-appear-clickable")

    return round(score, 2), {
        "controlCount": len(candidates),
        "labeledCount": len(labeled),
        "pointerLikeCount": len(pointer_like),
        "touchTargetPassCount": sum(1 for e in candidates if e.touch_target_pass is not False),
        "details": details,
    }


def _score_verbs_used_for_actions(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = [e for e in (page.ctas + page.controls + page.links) if (e.text or e.accessible_name)]
    if len(candidates) < 2:
        return None, {"reason": "too_few_named_actions"}

    verb_like = [e for e in candidates if _is_action_like_label(e.text or e.accessible_name)]
    score = 100 * _ratio(len(verb_like), len(candidates))

    bad_examples = [e.text or e.accessible_name for e in candidates if not _is_action_like_label(e.text or e.accessible_name)][:8]
    return round(score, 2), {
        "actionCount": len(candidates),
        "verbActionCount": len(verb_like),
        "verbRatio": round(_ratio(len(verb_like), len(candidates)), 3),
        "nonVerbExamples": bad_examples,
    }


def _score_familiar_labeling(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = [e for e in (page.ctas + page.controls + page.links + page.select_controls) if (e.text or e.accessible_name)]
    if not candidates:
        return None, {"reason": "no_named_interactives"}

    systemish = [e for e in candidates if _looks_system_oriented(e.text or e.accessible_name)]
    score = 100 * (1.0 - _ratio(len(systemish), len(candidates)))

    return round(score, 2), {
        "interactiveLabelCount": len(candidates),
        "systemOrientedCount": len(systemish),
        "systemOrientedExamples": [(e.text or e.accessible_name) for e in systemish[:8]],
    }


def _score_user_control_over_workflows(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    # Honest limitation: current extraction is mostly static.
    # We infer only weak signals from presence of close/cancel/back/search/filter controls.
    candidates = page.controls + page.ctas + page.links
    if not candidates:
        return None, {"reason": "no_interactive_workflow_controls"}

    control_terms = {"cancel", "close", "back", "search", "filter", "fermer", "annuler", "retour", "recherche", "filtrer"}
    control_like = [
        e for e in candidates
        if any(t in _lower(e.text or e.accessible_name) for t in control_terms)
    ]

    if len(control_like) < 1 and page.archetype not in {"task", "catalog"}:
        return None, {"reason": "insufficient_workflow_evidence"}

    score = 45 + 55 * _clamp(_ratio(len(control_like), 3.0), 0.0, 1.0)
    return round(score, 2), {
        "workflowSupportControlCount": len(control_like),
        "examples": [(e.text or e.accessible_name) for e in control_like[:8]],
        "evidenceQuality": "static_only",
    }


def _score_response_consistency(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    # With current extraction there are no hover/focus/active diffs.
    # So we estimate consistency from control family reuse and style-group stability.
    candidates = page.controls + page.ctas + page.links + page.select_controls + page.dropdown_like_controls
    if len(candidates) < 3:
        return None, {"reason": "too_few_controls"}

    family_counts = Counter(e.family for e in candidates)
    group_counts = Counter(e.component_group_id for e in candidates if e.component_group_id)

    dominant_family_ratio = max(family_counts.values()) / len(candidates) if family_counts else 0.0
    dominant_group_ratio = max(group_counts.values()) / len(candidates) if group_counts else 0.0

    score = 40 * dominant_family_ratio + 40 * dominant_group_ratio + 20 * _ratio(
        sum(1 for e in candidates if e.touch_target_pass is not False),
        len(candidates),
    )

    return round(score, 2), {
        "controlCount": len(candidates),
        "familyVariantCount": len(family_counts),
        "componentGroupVariantCount": len(group_counts),
        "dominantFamilyRatio": round(dominant_family_ratio, 3),
        "dominantGroupRatio": round(dominant_group_ratio, 3),
        "evidenceQuality": "static_only",
    }


def _score_frequent_features_available(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.top_controls + page.nav_controls
    if not candidates:
        return None, {"reason": "no_top_or_nav_controls"}

    hits = []
    for e in candidates:
        blob = " ".join([_lower(e.text), _lower(e.accessible_name), _lower(e.ux_role)])
        if any(term in blob for term in FREQUENT_FEATURE_TERMS):
            hits.append(e)

    score = 35 + 65 * _clamp(_ratio(len(hits), 4.0), 0.0, 1.0)
    return round(score, 2), {
        "topOrNavControlCount": len(candidates),
        "frequentFeatureCount": len(hits),
        "examples": [(e.text or e.accessible_name) for e in hits[:8]],
    }


def _score_default_primary_non_destructive(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    primary_candidates = sorted(page.ctas, key=lambda e: e.prominence_score, reverse=True)
    if not primary_candidates:
        return None, {"reason": "no_primary_cta_detected"}

    primary = primary_candidates[0]
    destructive = _looks_destructive(primary.text or primary.accessible_name, primary.ux_role, primary.href)
    score = 100.0 if not destructive else 15.0

    return round(score, 2), {
        "primaryAction": primary.text or primary.accessible_name,
        "primaryLooksDestructive": destructive,
        "primaryProminence": round(primary.prominence_score, 2),
    }


def _score_destructive_confirmation(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    # Honest limitation: no runtime click outcome or dialog extraction yet.
    # We can only detect destructive controls + whether confirmation hints exist in labels/nearby text.
    destructive = page.destructive_controls
    if not destructive:
        return None, {"reason": "no_destructive_controls_detected"}

    confirm_hint_terms = {"confirm", "confirmation", "are you sure", "confirm delete", "confirmer", "confirmation"}
    hinted = []
    for e in destructive:
        blob = " ".join([
            _lower(e.text),
            _lower(e.accessible_name),
            _lower(e.label),
            _lower(_safe_get(e.raw, "ariaDescribedBy")),
            _lower(_safe_get(e.raw, "title")),
        ])
        if any(t in blob for t in confirm_hint_terms):
            hinted.append(e)

    # conservative because current extraction is static
    score = 25 + 45 * _ratio(len(hinted), len(destructive))
    return round(score, 2), {
        "destructiveControlCount": len(destructive),
        "confirmationHintCount": len(hinted),
        "destructiveExamples": [(e.text or e.accessible_name) for e in destructive[:8]],
        "evidenceQuality": "static_only_needs_runtime_click_path",
    }


def _score_red_reserved_for_destructive(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    if not page.destructive_controls:
        return None, {"reason": "no_destructive_controls_detected"}

    red_ratio = page.destructive_red_ratio if page.destructive_red_ratio is not None else 0.0
    score = 100 * red_ratio
    return round(score, 2), {
        "destructiveControlCount": len(page.destructive_controls),
        "destructiveRedRatio": round(red_ratio, 3),
        "redDestructiveCount": sum(
            1 for e in page.destructive_controls
            if ((e.bg_color and _is_red_like(e.bg_color)) or (e.text_color and _is_red_like(e.text_color)))
        ),
    }


def _score_standard_browser_support(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    # Current extraction cannot reliably prove back/forward/copy/paste behavior.
    # Only weak static inference from form/input presence and lack of obvious browser-blocking widgets.
    if not page.user_input_fields and page.archetype not in {"task", "catalog"}:
        return None, {"reason": "insufficient_input_evidence"}

    score = 60.0
    return round(score, 2), {
        "userInputFieldCount": len(page.user_input_fields),
        "evidenceQuality": "static_only_manual_or_runtime_needed",
    }


def _score_control_placement_consistency(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.controls + page.ctas + page.links + page.select_controls
    if len(candidates) < 4:
        return None, {"reason": "too_few_controls"}

    by_landmark = Counter(e.landmark or "no-landmark" for e in candidates)
    dominant_landmark_ratio = max(by_landmark.values()) / len(candidates) if by_landmark else 0.0

    by_parent = Counter(e.parent_display or "unknown" for e in candidates)
    dominant_parent_ratio = max(by_parent.values()) / len(candidates) if by_parent else 0.0

    score = 55 * dominant_landmark_ratio + 45 * dominant_parent_ratio
    return round(score, 2), {
        "controlCount": len(candidates),
        "landmarkBuckets": len(by_landmark),
        "parentDisplayBuckets": len(by_parent),
        "dominantLandmarkRatio": round(dominant_landmark_ratio, 3),
        "dominantParentDisplayRatio": round(dominant_parent_ratio, 3),
    }


def _score_controls_related_to_context(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.controls + page.ctas + page.links + page.select_controls
    if len(candidates) < 3:
        return None, {"reason": "too_few_controls"}

    with_landmark = [e for e in candidates if e.landmark]
    with_context = [e for e in candidates if e.landmark or e.sibling_count >= 1]
    score = 50 * _ratio(len(with_landmark), len(candidates)) + 50 * _ratio(len(with_context), len(candidates))

    return round(score, 2), {
        "controlCount": len(candidates),
        "withLandmarkCount": len(with_landmark),
        "withContextCount": len(with_context),
    }


def _score_not_abstracted(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.controls + page.ctas + page.links
    if len(candidates) < 2:
        return None, {"reason": "too_few_interactives"}

    explicit_controls = [
        e for e in candidates
        if e.tag in {"button", "a", "select", "input"}
        or e.role_attr.lower() == "button"
        or e.interactive_hint
    ]

    score = 70 * _ratio(len(explicit_controls), len(candidates)) + 30 * _ratio(
        sum(1 for e in candidates if e.touch_target_pass is not False),
        len(candidates),
    )

    return round(score, 2), {
        "interactiveCount": len(candidates),
        "explicitControlCount": len(explicit_controls),
        "smallTouchTargetCount": len(page.small_touch_targets),
    }


def _score_editable_droplists(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    relevant = page.select_controls + page.dropdown_like_controls
    if not relevant:
        return None, {"reason": "no_dropdowns_or_selects"}

    editable = [e for e in relevant if _looks_editable_dropdown(e.raw)]
    if not editable and not page.user_input_fields:
        # Native selects may be valid but not editable; criterion says "where applicable"
        # so keep warning/NA depending on evidence.
        return 60.0, {
            "dropdownCount": len(relevant),
            "editableDropdownCount": 0,
            "evidenceQuality": "static_only_needs_runtime_typing_test",
        }

    score = 45 + 55 * _ratio(len(editable), len(relevant))
    return round(score, 2), {
        "dropdownCount": len(relevant),
        "editableDropdownCount": len(editable),
        "editableExamples": [(e.text or e.accessible_name or e.placeholder) for e in editable[:8]],
        "evidenceQuality": "static_only_needs_runtime_typing_test",
    }


def _score_hints_help_tooltips(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    if not page.user_input_fields and not page.controls:
        return None, {"reason": "no_fields_or_controls"}

    helper_like = 0
    placeholder_supported = 0

    for f in page.user_input_fields:
        if f.placeholder:
            placeholder_supported += 1
            helper_like += 1

    tooltip_like_controls = [
        e for e in page.controls + page.ctas + page.links
        if _text(_safe_get(e.raw, "title")) or _text(_safe_get(e.raw, "ariaDescribedBy"))
    ]
    helper_like += len(tooltip_like_controls)

    population = max(len(page.user_input_fields) + len(page.controls), 1)
    score = 100 * _clamp(_ratio(helper_like, population), 0.0, 1.0)

    return round(score, 2), {
        "userInputFieldCount": len(page.user_input_fields),
        "placeholderCount": placeholder_supported,
        "tooltipLikeControlCount": len(tooltip_like_controls),
        "evidenceQuality": "partial_static_only",
    }


def _score_control_tier_distinction(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.ctas + page.controls + page.links
    if len(candidates) < 3:
        return None, {"reason": "too_few_controls"}

    families = Counter(e.family for e in candidates)
    variants = Counter((e.semantic_type, e.component_variant) for e in candidates)
    style_signatures = Counter(e.style_signature for e in candidates if e.style_signature)

    family_diversity = _clamp(_ratio(len(families), 3.0), 0.0, 1.0)
    variant_diversity = _clamp(_ratio(len(variants), 4.0), 0.0, 1.0)
    signature_diversity = _clamp(_ratio(len(style_signatures), 4.0), 0.0, 1.0)

    primary = sorted(page.ctas, key=lambda e: e.prominence_score, reverse=True)[:2]
    primary_prom = _mean([e.prominence_score for e in primary]) or 0.0
    non_primary_prom = _median([e.prominence_score for e in page.controls + page.links]) or 0.0
    prominence_gap = _normalize_score(primary_prom - non_primary_prom, 2.0, 15.0)

    score = 20 * family_diversity + 25 * variant_diversity + 25 * signature_diversity + 30 * prominence_gap
    return round(score, 2), {
        "familyCount": len(families),
        "variantCount": len(variants),
        "styleSignatureCount": len(style_signatures),
        "primaryProminence": round(primary_prom, 2),
        "nonPrimaryProminenceMedian": round(non_primary_prom, 2),
    }


def _score_secondary_actions_as_links(page: PageSummary) -> Tuple[Optional[float], Dict[str, Any]]:
    candidates = page.links + page.controls + page.ctas
    secondary = [e for e in candidates if _looks_secondary_action(e.text or e.accessible_name, e.ux_role)]
    if not secondary:
        return None, {"reason": "no_secondary_actions_detected"}

    as_links = [e for e in secondary if e.tag == "a" or e.kind == "link-control"]
    score = 100 * _ratio(len(as_links), len(secondary))
    return round(score, 2), {
        "secondaryActionCount": len(secondary),
        "secondaryAsLinksCount": len(as_links),
        "examples": [(e.text or e.accessible_name) for e in secondary[:8]],
    }


# ============================================================
# Criterion wrappers
# ============================================================
def _page_item(page: PageSummary, criterion: str, score: Optional[float], metrics: Dict[str, Any]) -> Dict[str, Any]:
    details = metrics.get("details", [])
    metrics_suspicious = bool(metrics.get("metrics_suspicious", False))

    return {
        **page.page_ref,
        "score": round(score, 2) if score is not None else None,
        "status": STATUS_NA if score is None else _band_to_status(score, criterion),
        "details": details,
        "metrics": metrics,
        "archetype": page.archetype,
        "criterion": criterion,
        "metrics_suspicious": metrics_suspicious,
    }


def _element_target(element: ElementModel, *, reason: str, issue_kind: str = "presence") -> Dict[str, Any]:
    return {
        "target_kind": "component",
        "issue_kind": issue_kind,
        "page_name": element.page_name,
        "page_url": element.page_url,
        "final_url": element.page_url,
        "screenshot_path": element.screenshot_path,
        "component_type": element.semantic_type or element.tag or element.ux_role or element.kind,
        "component_text": element.text or element.accessible_name or element.label or element.placeholder,
        "highlight_shape": "circle",
        "reason": reason,
        "rect": {
            "x": round(element.x, 2),
            "y": round(element.y, 2),
            "width": round(element.width, 2),
            "height": round(element.height, 2),
        },
    }


def _region_target(page: PageSummary, elements: List[ElementModel], *, reason: str, issue_kind: str = "absence") -> Optional[Dict[str, Any]]:
    usable = [element for element in elements if element.width > 0 and element.height > 0]
    if not usable:
        return None
    left = min(element.x for element in usable)
    top = min(element.y for element in usable)
    right = max(element.right for element in usable)
    bottom = max(element.bottom for element in usable)
    return {
        "target_kind": "region",
        "issue_kind": issue_kind,
        "page_name": page.page_ref.get("name", ""),
        "page_url": page.page_ref.get("url", ""),
        "final_url": page.page_ref.get("finalUrl", page.page_ref.get("url", "")),
        "screenshot_path": page.page_ref.get("screenshotPath", ""),
        "component_type": "region",
        "component_text": "",
        "highlight_shape": "circle",
        "reason": reason,
        "rect": {
            "x": round(left, 2),
            "y": round(top, 2),
            "width": round(right - left, 2),
            "height": round(bottom - top, 2),
        },
    }


def _summary_map(page_summaries: Optional[List[PageSummary]]) -> Dict[Tuple[str, str], PageSummary]:
    return {(page.page_ref.get("name", ""), page.page_ref.get("url", "")): page for page in (page_summaries or [])}


def _interaction_evidence_bundle(
    criterion: str,
    reviewed_items: List[Dict[str, Any]],
    page_summaries: Optional[List[PageSummary]],
) -> Optional[Dict[str, Any]]:
    summary_map = _summary_map(page_summaries)
    failing = [item for item in reviewed_items if item.get("status") == STATUS_FAIL]
    warning = [item for item in reviewed_items if item.get("status") == STATUS_WARNING]
    source_items = failing or warning
    if not source_items:
        return None

    primary_item = source_items[0]
    page = summary_map.get((primary_item.get("name", ""), primary_item.get("url", "")))
    if not page:
        return None

    target = None
    notes = ""

    if criterion == "cta-clearly-labeled-and-clickable":
        candidates = page.ctas + page.controls + page.links
        weak = [
            element for element in candidates
            if not (element.text or element.accessible_name or element.label)
            or not (
                _lower(_safe_get(element.raw, "styles", "cursor")) == "pointer"
                or element.tag in {"button", "a"}
                or element.role_attr.lower() == "button"
            )
            or element.touch_target_pass is False
        ]
        if weak:
            target = _element_target(
                weak[0],
                reason="This control is a representative CTA/clickability issue on the failing page.",
            )
            notes = "Chosen from controls that lack a clear label, clickable cue, or adequate touch target."

    elif criterion == "verbs-used-for-actions":
        candidates = [element for element in (page.ctas + page.controls + page.links) if (element.text or element.accessible_name)]
        non_verbs = [element for element in candidates if not _is_action_like_label(element.text or element.accessible_name)]
        if non_verbs:
            target = _element_target(
                non_verbs[0],
                reason="This action label is representative of the non-verb naming issue.",
            )
            notes = "Chosen from visible actions whose label does not start with a clear action verb."

    elif criterion == "frequently-used-features-readily-available":
        region_elements = page.top_controls + page.nav_controls
        target = _region_target(
            page,
            region_elements[:8],
            reason="Header/top-control area highlighted because high-frequency features are not sufficiently surfaced here.",
            issue_kind="absence",
        )
        notes = "This issue is best represented as a header/navigation zone rather than a single control."

    elif criterion == "primary-secondary-tertiary-controls-visually-distinct":
        primary = sorted(page.ctas, key=lambda element: element.prominence_score, reverse=True)[:1]
        supporting = sorted(page.controls + page.links, key=lambda element: element.prominence_score, reverse=True)[:2]
        target = _region_target(
            page,
            primary + supporting,
            reason="This grouped control area is used to judge whether control tiers are visually differentiated.",
            issue_kind="presence",
        )
        notes = "This issue is represented as a grouped control region so the contrast between priority tiers stays visible."

    if not target:
        return None

    return build_evidence_bundle(
        criterion=criterion,
        source="interaction_controls_check",
        target=target,
        notes=notes,
    )

def _wrap_pagewise(
    *,
    criterion: str,
    page_summaries: List[PageSummary],
    scorer,
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
) -> Dict[str, Any]:
    page_items: List[Dict[str, Any]] = []
    applicable_scores: List[float] = []

    for page in page_summaries:
        score, metrics = scorer(page)
        item = _page_item(page, criterion, score, metrics)
        page_items.append(item)
        if score is not None:
            applicable_scores.append(score)

    coverage = _ratio(len(applicable_scores), max(len(page_summaries), 1))
    return _strict_site_result_from_page_items(
        criterion=criterion,
        page_items=page_items,
        coverage=coverage,
        title_pass=title_pass,
        title_warn=title_warn,
        title_fail=title_fail,
        title_na=title_na,
        description_pass=description_pass,
        description_warn=description_warn,
        description_fail=description_fail,
        description_na=description_na,
        recommendation=recommendation,
        method=method,
        page_summaries=page_summaries,
    )


def check_cta_clearly_labeled_and_clickable(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="cta-clearly-labeled-and-clickable",
        page_summaries=page_summaries,
        scorer=_score_clickable_cta_labeling,
        title_pass="Calls to action are clearly labeled and appear clickable",
        title_warn="CTA labeling and clickability are only partially effective",
        title_fail="CTA labeling and clickability do not pass",
        title_na="CTA labeling and clickability could not be evaluated",
        description_pass="The available evidence suggests calls to action are named clearly and visually behave like clickable controls.",
        description_warn="Some actions are labeled or styled clearly, but others may not strongly signal clickability.",
        description_fail="At least one page contains actions that are weakly labeled or do not strongly appear clickable.",
        description_na="There was not enough interactive evidence to evaluate CTA labeling and clickability.",
        recommendation="Ensure interactive actions have explicit labels and strong clickable cues such as pointer cursor, control semantics, and adequate hit area.",
        method=["label-presence-analysis", "clickable-cue-analysis", "touch-target-analysis"],
    )


def check_verbs_used_for_actions(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="verbs-used-for-actions",
        page_summaries=page_summaries,
        scorer=_score_verbs_used_for_actions,
        title_pass="Action labels generally use verbs",
        title_warn="Some action labels do not clearly use verbs",
        title_fail="Action labels do not pass the verb criterion",
        title_na="Action-verb criterion could not be evaluated",
        description_pass="Most visible actions use verb-led labels such as search, add, continue, or confirm.",
        description_warn="Some actions are named with nouns or unclear labels instead of direct verbs.",
        description_fail="At least one page relies too heavily on non-verb action labels.",
        description_na="Too few named actions were available to evaluate this criterion.",
        recommendation="Prefer direct verb-led action labels such as Search, Add to cart, Continue, Save, or Send.",
        method=["action-label-token-analysis"],
    )


def check_interactive_labeling_familiar(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="interactive-labeling-familiar-not-system-oriented",
        page_summaries=page_summaries,
        scorer=_score_familiar_labeling,
        title_pass="Interactive labeling appears familiar to users",
        title_warn="Interactive labeling is only partially user-friendly",
        title_fail="Interactive labeling does not pass",
        title_na="Interactive labeling could not be evaluated",
        description_pass="The visible control labels generally avoid technical or system-oriented terminology.",
        description_warn="Some visible controls may use wording that is less familiar or more system-oriented than ideal.",
        description_fail="At least one page contains interactive labels that are too technical or system-oriented.",
        description_na="There were too few interactive labels to evaluate this criterion.",
        recommendation="Use plain, user-oriented language for controls and avoid internal or technical system terms.",
        method=["label-language-analysis", "system-terminology-detection"],
    )


def check_users_have_control_over_workflows(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="users-have-control-over-interactive-workflows",
        page_summaries=page_summaries,
        scorer=_score_user_control_over_workflows,
        title_pass="The interface appears to support user control over flows",
        title_warn="User control over flows is only partially evidenced",
        title_fail="User control over flows does not pass",
        title_na="User control over flows could not be evaluated",
        description_pass="The available interface signals suggest users can navigate, stop, close, search, or refine workflows with reasonable control.",
        description_warn="Some workflow control signals exist, but the evidence is incomplete without runtime behavior checks.",
        description_fail="The available signals do not strongly support user control over interactive flows.",
        description_na="Static evidence was insufficient to evaluate workflow control reliably.",
        recommendation="Expose back, cancel, close, search, filter, and escape routes consistently, and add runtime checks to verify actual flow control.",
        method=["static-workflow-control-detection"],
    )


def check_ui_responds_consistently_to_actions(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="ui-responds-consistently-to-user-actions",
        page_summaries=page_summaries,
        scorer=_score_response_consistency,
        title_pass="Controls appear visually consistent across interaction patterns",
        title_warn="Control response consistency is only partially evidenced",
        title_fail="Control response consistency does not pass",
        title_na="Control response consistency could not be evaluated",
        description_pass="The extracted control families and variants suggest reasonably consistent interactive treatment.",
        description_warn="Some consistency exists, but runtime state changes are not yet available to fully verify the criterion.",
        description_fail="The visible control styles are too inconsistent to support reliable interaction behavior expectations.",
        description_na="There were too few controls to evaluate response consistency.",
        recommendation="Standardize control variants and add runtime hover, focus, active, and disabled-state extraction.",
        method=["control-family-analysis", "component-group-consistency-analysis"],
    )


def check_frequently_used_features_available(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="frequently-used-features-readily-available",
        page_summaries=page_summaries,
        scorer=_score_frequent_features_available,
        title_pass="Frequently used features appear readily available",
        title_warn="Frequently used features are only partially surfaced",
        title_fail="Frequently used features are not surfaced strongly enough",
        title_na="Frequently used features could not be evaluated",
        description_pass="Search, filtering, cart, menu, account, or similar frequent controls appear accessible in top or navigation areas.",
        description_warn="Some frequent features are present, but not all of them appear easy to access immediately.",
        description_fail="Frequently used controls are not sufficiently visible in top or navigation zones.",
        description_na="There was not enough top/navigation evidence to evaluate feature availability.",
        recommendation="Expose high-frequency features such as search, filter, cart, account, and menu in predictable top or navigation regions.",
        method=["top-zone-feature-presence", "navigation-feature-presence"],
    )


def check_default_primary_actions_not_destructive(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="default-primary-actions-not-destructive",
        page_summaries=page_summaries,
        scorer=_score_default_primary_non_destructive,
        title_pass="Primary actions do not appear destructive",
        title_warn="Primary actions may be somewhat risky or unclear",
        title_fail="Primary actions do not pass the non-destructive criterion",
        title_na="Primary action destructiveness could not be evaluated",
        description_pass="The most prominent action does not appear to be destructive.",
        description_warn="Some primary actions may need clearer differentiation from destructive operations.",
        description_fail="At least one page appears to present a destructive action as the main primary action.",
        description_na="No strong primary action was available to evaluate this criterion.",
        recommendation="Keep destructive actions visually secondary and never style them as the main default action.",
        method=["primary-action-risk-analysis"],
    )


def check_destructive_actions_confirmed(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="destructive-actions-confirmed-before-execution",
        page_summaries=page_summaries,
        scorer=_score_destructive_confirmation,
        title_pass="Destructive actions appear to be guarded appropriately",
        title_warn="Destructive action confirmation is only partially evidenced",
        title_fail="Destructive action confirmation does not pass",
        title_na="Destructive action confirmation could not be evaluated",
        description_pass="The available destructive actions show at least some evidence of confirmation or guarded handling.",
        description_warn="Destructive actions were detected, but static extraction alone cannot verify the full confirmation path.",
        description_fail="Destructive actions were detected without enough evidence of confirmation safeguards.",
        description_na="No destructive actions were detected, or runtime proof was unavailable.",
        recommendation="Add runtime click-path extraction to verify confirmation dialogs and ensure destructive actions never execute immediately.",
        method=["destructive-action-detection", "static-confirmation-hint-analysis"],
    )


def check_red_reserved_for_destructive(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="red-reserved-for-destructive-actions",
        page_summaries=page_summaries,
        scorer=_score_red_reserved_for_destructive,
        title_pass="Red appears reserved for destructive actions",
        title_warn="Red usage around destructive actions is only partially correct",
        title_fail="Red usage for destructive actions does not pass",
        title_na="Red/destructive action mapping could not be evaluated",
        description_pass="Detected destructive actions are visually treated with red or red-like emphasis.",
        description_warn="Some destructive actions use red treatment, but not consistently.",
        description_fail="Detected destructive actions are not visually distinguished with red treatment strongly enough.",
        description_na="No destructive actions were available to evaluate this criterion.",
        recommendation="Use red consistently for destructive controls and avoid applying the same red treatment to ordinary actions.",
        method=["destructive-color-analysis"],
    )


def check_standard_browser_functions_supported(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="standard-browser-functions-supported",
        page_summaries=page_summaries,
        scorer=_score_standard_browser_support,
        title_pass="Standard browser support appears acceptable",
        title_warn="Standard browser support is only partially evidenced",
        title_fail="Standard browser support does not pass",
        title_na="Standard browser support could not be evaluated",
        description_pass="The current structure does not show strong anti-pattern evidence against normal browser behavior.",
        description_warn="The available extraction is too static to fully verify back, forward, copy, and paste support.",
        description_fail="The available evidence raises concerns about normal browser behavior support.",
        description_na="There was not enough evidence to evaluate browser support reliably.",
        recommendation="Add runtime navigation and field interaction checks to verify back, forward, copy, paste, and native input behavior.",
        method=["static-browser-support-inference"],
    )


def check_controls_placed_consistently(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="controls-placed-consistently",
        page_summaries=page_summaries,
        scorer=_score_control_placement_consistency,
        title_pass="Controls are placed consistently",
        title_warn="Control placement is only partially consistent",
        title_fail="Control placement does not pass",
        title_na="Control placement could not be evaluated",
        description_pass="Controls tend to appear within stable landmarks and parent layout structures.",
        description_warn="Some controls are grouped consistently, but layout placement varies more than expected.",
        description_fail="Controls appear in inconsistent structural positions across the page.",
        description_na="There were too few controls to evaluate placement consistency.",
        recommendation="Place equivalent controls in repeatable landmarks and layout zones across screens or templates.",
        method=["landmark-placement-analysis", "parent-layout-consistency-analysis"],
    )


def check_controls_related_to_surrounding_information(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="controls-related-to-surrounding-information",
        page_summaries=page_summaries,
        scorer=_score_controls_related_to_context,
        title_pass="Controls appear related to surrounding information",
        title_warn="Some controls are only partially tied to surrounding content",
        title_fail="Control/context relation does not pass",
        title_na="Control/context relation could not be evaluated",
        description_pass="Most controls appear inside meaningful landmarks or contextual layout groupings.",
        description_warn="Some controls have contextual grouping, but others lack strong structural relation to nearby information.",
        description_fail="Too many controls appear weakly anchored to surrounding information.",
        description_na="There were too few controls to evaluate contextual relation.",
        recommendation="Keep controls inside clear contextual containers such as cards, forms, rows, sections, or field groups.",
        method=["landmark-context-analysis", "layout-context-analysis"],
    )


def check_interactive_elements_not_abstracted(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="interactive-elements-not-abstracted",
        page_summaries=page_summaries,
        scorer=_score_not_abstracted,
        title_pass="Interactive elements are not overly abstracted",
        title_warn="Some interactive elements may be visually abstracted",
        title_fail="Interactive elements are too abstracted",
        title_na="Interactive abstraction could not be evaluated",
        description_pass="Most visible actions use recognizably interactive HTML or button/link semantics.",
        description_warn="Some controls may rely too heavily on weak visual cues or compact targets.",
        description_fail="Too many actions do not strongly read as buttons, links, or explicit controls.",
        description_na="There were too few interactives to evaluate abstraction.",
        recommendation="Use explicit control semantics and strong visual affordances so users can immediately recognize interactive elements.",
        method=["semantic-control-detection", "touch-target-analysis"],
    )


def check_editable_droplists_where_applicable(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="editable-droplists-where-applicable",
        page_summaries=page_summaries,
        scorer=_score_editable_droplists,
        title_pass="Editable droplists appear supported where applicable",
        title_warn="Editable droplist support is only partially evidenced",
        title_fail="Editable droplist support does not pass",
        title_na="Editable droplist support could not be evaluated",
        description_pass="Dropdown-like controls show some evidence of editable or searchable behavior where relevant.",
        description_warn="Dropdown controls were found, but runtime typing support is not fully verifiable from static extraction.",
        description_fail="Relevant dropdowns do not show enough evidence of editable/searchable behavior.",
        description_na="No dropdown-like controls were available to evaluate this criterion.",
        recommendation="Add runtime typing tests for select/combobox controls and verify suggestions, filtering, and keyboard support.",
        method=["dropdown-structure-detection", "editable-combobox-inference"],
    )


def check_controls_provide_hints_help_tooltips(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="controls-provide-hints-help-tooltips-where-applicable",
        page_summaries=page_summaries,
        scorer=_score_hints_help_tooltips,
        title_pass="Hints, help, or tooltip support appears present where applicable",
        title_warn="Hint/help support is only partially present",
        title_fail="Hint/help support does not pass",
        title_na="Hint/help support could not be evaluated",
        description_pass="Inputs or controls show some support for placeholders, helper text, or tooltip-like attributes.",
        description_warn="Some help signals exist, but coverage is incomplete or only placeholder-based.",
        description_fail="Controls do not provide enough visible support cues where help would be expected.",
        description_na="No relevant inputs or controls were available to evaluate hint/help support.",
        recommendation="Add helper text, tooltip descriptions, or clear inline guidance where the task would benefit from extra context.",
        method=["placeholder-analysis", "tooltip-attribute-analysis"],
    )


def check_control_tiers_visually_distinct(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="primary-secondary-tertiary-controls-visually-distinct",
        page_summaries=page_summaries,
        scorer=_score_control_tier_distinction,
        title_pass="Primary, secondary, and tertiary controls appear visually distinct",
        title_warn="Control tiers are only partially differentiated",
        title_fail="Control tiers do not pass the distinction criterion",
        title_na="Control tier distinction could not be evaluated",
        description_pass="The interface shows enough variation in families, variants, and prominence to separate control tiers.",
        description_warn="Some distinction exists, but primary and secondary controls may not separate strongly enough.",
        description_fail="The control system does not show enough visual differentiation between priority tiers.",
        description_na="There were too few controls to evaluate tier distinction.",
        recommendation="Create clearer visual differences between primary, secondary, and tertiary controls through fill, border, weight, and prominence.",
        method=["control-family-diversity", "variant-diversity", "prominence-gap-analysis"],
    )


def check_secondary_actions_as_links(page_summaries: List[PageSummary]) -> Dict[str, Any]:
    return _wrap_pagewise(
        criterion="secondary-actions-displayed-as-links",
        page_summaries=page_summaries,
        scorer=_score_secondary_actions_as_links,
        title_pass="Secondary actions are generally displayed as links",
        title_warn="Secondary actions are only partially displayed as links",
        title_fail="Secondary actions do not pass the link-style criterion",
        title_na="Secondary-action link treatment could not be evaluated",
        description_pass="Detected secondary actions are commonly rendered as links or link-like lightweight controls.",
        description_warn="Some secondary actions appear link-like, while others use heavier control treatment.",
        description_fail="Detected secondary actions rely too often on heavy button treatment rather than lightweight link treatment.",
        description_na="No secondary actions were detected to evaluate this criterion.",
        recommendation="Render cancel, close, hide, dismiss, and similarly low-priority actions as links or lightweight controls where appropriate.",
        method=["secondary-action-detection", "link-treatment-analysis"],
    )


# ============================================================
# Orchestrator
# ============================================================

def run_interaction_controls_checks(
    person_a_data: Dict[str, Any],
    rendered_ui_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    page_summaries = _collect_page_summaries(person_a_data, rendered_ui_data)

    if not page_summaries:
        return [
            _make_result(
                criterion="interaction-controls-checks",
                status=STATUS_NA,
                severity=None,
                title="Interaction and controls checks could not be evaluated",
                description="No usable page data was available to evaluate the interaction and controls checklist.",
                pages=[],
                recommendation="Verify that both person_a_cleaned.json and rendered_ui_extraction.json contain page-level data.",
                evidence={"checkedPages": 0},
                confidence="low",
                method=["page-summary-build"],
            )
        ]

    results: List[Dict[str, Any]] = []

    # General
    results.append(check_cta_clearly_labeled_and_clickable(page_summaries))
    results.append(check_verbs_used_for_actions(page_summaries))

    # Interaction / general
    results.append(check_interactive_labeling_familiar(page_summaries))
    results.append(check_users_have_control_over_workflows(page_summaries))
    results.append(check_ui_responds_consistently_to_actions(page_summaries))
    results.append(check_frequently_used_features_available(page_summaries))
    results.append(check_default_primary_actions_not_destructive(page_summaries))
    results.append(check_destructive_actions_confirmed(page_summaries))
    results.append(check_red_reserved_for_destructive(page_summaries))
    results.append(check_standard_browser_functions_supported(page_summaries))

    # Controls
    results.append(check_controls_placed_consistently(page_summaries))
    results.append(check_controls_related_to_surrounding_information(page_summaries))
    results.append(check_interactive_elements_not_abstracted(page_summaries))
    results.append(check_editable_droplists_where_applicable(page_summaries))
    results.append(check_controls_provide_hints_help_tooltips(page_summaries))
    results.append(check_control_tiers_visually_distinct(page_summaries))
    results.append(check_secondary_actions_as_links(page_summaries))

    return results
