from __future__ import annotations

import re
import unicodedata
from statistics import mean as _mean
from typing import Any, Iterable, List


AXIS_DEFINITIONS = [
    {
        "id": "task_execution",
        "name": "Expérience & Exécution des Tâches",
        "short_name": "Task Execution",
        "description": "How clearly the product helps users complete key actions without friction.",
        "focus": ["Interaction", "Forms", "Feedback"],
    },
    {
        "id": "flow_architecture",
        "name": "Logique de Flux & Architecture",
        "short_name": "Flow & Architecture",
        "description": "How understandable the information architecture and navigation model feel.",
        "focus": ["Navigation", "Presentation"],
    },
    {
        "id": "trust_accessibility",
        "name": "Confiance & Accessibilité (WCAG 2.2)",
        "short_name": "Trust & Accessibility",
        "description": "How credible, safe, and accessible the experience appears for real-world usage.",
        "focus": ["Content", "Labeling", "Forms", "Presentation"],
    },
    {
        "id": "ui_consistency",
        "name": "Cohérence UI & Design System",
        "short_name": "UI Consistency",
        "description": "How consistently components, styles, spacing, and interaction patterns are applied.",
        "focus": ["Presentation", "Visual hierarchy", "Interaction"],
    },
    {
        "id": "visual_brand",
        "name": "Design Visuel & Expression de Marque",
        "short_name": "Visual Brand",
        "description": "How the visual direction expresses the brand and supports persuasive storytelling.",
        "focus": ["Visual hierarchy", "Presentation", "Content"],
    },
    {
        "id": "content_microcopy",
        "name": "Contenu & Microcopy",
        "short_name": "Content & Microcopy",
        "description": "How clearly messaging, labels, and calls to action communicate value and next steps.",
        "focus": ["Content", "Labeling", "Interaction"],
    },
    {
        "id": "market_alignment",
        "name": "Adéquation Stratégique au Marché",
        "short_name": "Market Alignment",
        "description": "How well the offer, audience cues, proof points, and CTA strategy support a GTM story.",
        "focus": ["Content", "Navigation", "Interaction", "Visual hierarchy"],
    },
]

AXIS_KEYWORDS = {
    "task_execution": [
        "action",
        "button",
        "call to action",
        "cta",
        "control",
        "error",
        "feedback",
        "flow",
        "form",
        "input",
        "submit",
        "task",
        "workflow",
    ],
    "flow_architecture": [
        "architecture",
        "breadcrumb",
        "find",
        "flow",
        "hierarchy",
        "menu",
        "navigation",
        "orientation",
        "page layout",
        "structure",
        "wayfinding",
    ],
    "trust_accessibility": [
        "accessible",
        "accessibility",
        "alt",
        "aria",
        "caption",
        "contrast",
        "error",
        "label",
        "plain language",
        "required",
        "trust",
        "wcag",
    ],
    "ui_consistency": [
        "consistent",
        "design system",
        "font",
        "layout",
        "pattern",
        "spacing",
        "style",
        "visual style",
    ],
    "visual_brand": [
        "brand",
        "color",
        "contrast",
        "hierarchy",
        "visual",
        "look",
        "feel",
        "cta",
    ],
    "content_microcopy": [
        "content",
        "copy",
        "heading",
        "label",
        "language",
        "message",
        "microcopy",
        "terminology",
        "text",
    ],
    "market_alignment": [
        "audience",
        "business",
        "conversion",
        "demo",
        "market",
        "offer",
        "proof",
        "value",
        "why",
    ],
}


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_status(value: Any) -> str:
    text = clean_text(value).upper()
    if text in {"TRUE", "PASS", "PASSED", "YES", "Y"}:
        return "TRUE"
    if text in {"FALSE", "FAIL", "FAILED", "NO", "N"}:
        return "FALSE"
    return "N/A"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def mean(values: Iterable[float], default: float = 0.0) -> float:
    cleaned = [float(v) for v in values if v is not None]
    if not cleaned:
        return default
    return float(_mean(cleaned))


def slugify(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_text).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug or "item"


def score_to_severity(score: float) -> str:
    if score < 45:
        return "high"
    if score < 65:
        return "medium"
    return "low"


def dedupe_strings(values: Iterable[Any], limit: int = 999) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def contains_keyword(texts: Iterable[str], keywords: Iterable[str]) -> bool:
    haystack = " ".join(clean_text(text) for text in texts).lower()
    if not haystack:
        return False
    return any(keyword.lower() in haystack for keyword in keywords)

