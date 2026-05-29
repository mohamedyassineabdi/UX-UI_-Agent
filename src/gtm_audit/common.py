from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
from pathlib import Path
from statistics import mean as _mean
from typing import Any, Dict, Iterable, List, Optional


AXIS_DEFINITIONS = [
    {
        "id": "task_execution",
        "name": "Task Execution & Conversion Flow",
        "short_name": "Task Execution",
        "description": "How clearly the interface helps a first-time visitor understand the next step and complete the primary task without friction.",
        "focus": ["Interaction", "Forms", "Feedback"],
        "core_question": "Can a target buyer identify the primary action and complete it with confidence from the visible UI?",
        "look_for": [
            "A primary CTA or next step is visible, specific, and not buried by competing actions.",
            "Forms, key controls, and step transitions explain what happens next.",
            "The interface provides visible progress, validation, confirmation, or recovery cues.",
            "Critical actions feel easy to start and finish on the main commercial journey.",
        ],
        "healthy_signals": [
            "The primary CTA uses clear action language tied to an obvious outcome.",
            "Important forms or task flows expose labels, states, and helpful feedback.",
            "The screen sequence supports completion instead of forcing detours or guesswork.",
        ],
        "failure_modes": [
            "The user must infer the next step because the CTA is vague, hidden, or visually weak.",
            "Controls appear disconnected from their consequences, or the form asks for input without enough context.",
            "Error, loading, or confirmation states are missing or not signposted in the visible interface.",
            "Competing actions, repeated CTAs, or destructive controls increase the chance of abandonment or mistakes.",
        ],
        "out_of_scope": [
            "Do not penalize missing backend behavior unless the screenshots visibly show an unclear, broken, or unsafe state.",
            "Do not assume a long task is bad if the screen shows justified guidance or staged progression.",
        ],
        "severity_ladder": {
            "high": "The main commercial action is hard to spot, hard to trust, or visibly risky to complete.",
            "medium": "The flow is usable, but visible ambiguity or weak feedback would slow or discourage completion.",
            "low": "The task is understandable, with only secondary friction or polish issues.",
        },
        "evidence_expectations": [
            "Quote the visible CTA label, form label, state message, or step name when possible.",
            "Point to the exact control or region that makes the action unclear or safe to complete.",
        ],
        "default_fix": "Make the primary action explicit, reduce competing actions, and show clearer completion feedback on the visible flow.",
    },
    {
        "id": "flow_architecture",
        "name": "Flow Logic & Information Architecture",
        "short_name": "Flow & Architecture",
        "description": "How understandable the navigation model, page structure, and wayfinding feel across the visible journey.",
        "focus": ["Navigation", "Presentation"],
        "core_question": "Can a user tell where they are, what each area is for, and how to move to the next relevant place?",
        "look_for": [
            "Navigation labels and repeated structures create clear information scent.",
            "Page sections, headings, and content order match the user's likely questions.",
            "Users can tell where they are and what belongs together without scanning the whole page repeatedly.",
            "Cross-page navigation looks consistent enough to support orientation and return visits.",
        ],
        "healthy_signals": [
            "Repeated navigation appears in a predictable order and uses familiar labels.",
            "Sectioning, grouping, and layout help users scan from overview to detail.",
            "The page sequence supports evaluation, trust-building, and conversion without dead ends.",
        ],
        "failure_modes": [
            "Navigation is visually present but does not explain where important information lives.",
            "Section order follows the company's internal logic instead of the buyer's decision flow.",
            "Different areas of the journey feel disconnected, making users re-orient on every page.",
            "The interface hides key destinations behind generic labels, weak hierarchy, or fragmented page structure.",
        ],
        "out_of_scope": [
            "Do not criticize missing pages or hidden global navigation that is not visible in the provided evidence.",
            "Do not infer broken IA from one unconventional layout if orientation still remains clear.",
        ],
        "severity_ladder": {
            "high": "Users would struggle to understand where to go, what page they are on, or how the offer is organized.",
            "medium": "The structure is usable, but visible wayfinding and grouping choices create avoidable orientation cost.",
            "low": "The journey is understandable, with only minor scan or grouping inefficiencies.",
        },
        "evidence_expectations": [
            "Reference visible navigation labels, section titles, breadcrumb-like cues, or grouping patterns.",
            "Explain how the visible order either supports or interrupts the buyer's likely path.",
        ],
        "default_fix": "Clarify navigation labels, strengthen page grouping, and order content around the buyer's next likely question.",
    },
    {
        "id": "trust_accessibility",
        "name": "Trust, Risk & Accessibility",
        "short_name": "Trust & Accessibility",
        "description": "How reliably the experience satisfies visible and runtime WCAG 2.2 accessibility expectations plus trust-critical usability signals.",
        "focus": ["Content", "Labeling", "Forms", "Presentation"],
        "core_question": "Can users perceive, operate, and understand the audited pages according to WCAG 2.2 expectations without avoidable barriers?",
        "look_for": [
            "WCAG 2.2 contrast issues: text, links, buttons, fields, and non-text UI components meet measurable contrast expectations.",
            "WCAG 2.2 keyboard issues: links, buttons, menus, drawers, and forms can be reached and operated with keyboard focus.",
            "WCAG 2.2 name/role/value and label issues: controls expose meaningful accessible names, roles, labels, and instructions.",
            "WCAG 2.2 target-size and motion issues: targets are large enough, focus is visible, and flashing/motion does not create barriers.",
        ],
        "healthy_signals": [
            "Text and controls meet WCAG contrast thresholds on the captured pages.",
            "Tab order reaches visible controls in a logical order and focus is visibly identifiable.",
            "Links, buttons, and form fields have clear accessible names and work when activated.",
        ],
        "failure_modes": [
            "Low-contrast text or controls fail WCAG 2.2 readability/non-text contrast expectations.",
            "Controls are unreachable by keyboard, have weak focus indicators, or fail when activated.",
            "Links, buttons, and fields have missing/ambiguous accessible names, labels, or instructions.",
            "Targets are too small, motion is distracting, or a drawer/modal creates a keyboard or operation barrier.",
        ],
        "out_of_scope": [
            "Do not claim screen-reader output beyond accessible-name, role, label, and DOM evidence captured by the audit.",
            "Do not invent keyboard failures when the keyboard probe was not run; mark missing runtime coverage as a limitation.",
        ],
        "severity_ladder": {
            "high": "Visible issues would likely reduce trust, exclude some users, or make the product feel unsafe or careless.",
            "medium": "The UI looks mostly usable, but meaningful visible barriers or reassurance gaps remain.",
            "low": "Trust and accessibility cues are broadly present, with only secondary or localized weaknesses.",
        },
        "evidence_expectations": [
            "Cite the WCAG 2.2 success criterion number, measured signal, affected page, and exact visible control or text sample.",
            "Prefer runtime evidence from Playwright, contrast calculations, accessible names, target sizes, and keyboard tab probes.",
        ],
        "default_fix": "Fix the cited WCAG 2.2 barrier, then retest the affected page with contrast checks, keyboard traversal, and control activation.",
    },
    {
        "id": "ui_consistency",
        "name": "UI Consistency & Interaction Predictability",
        "short_name": "UI Consistency",
        "description": "How consistently components, styles, and recurring interaction patterns are applied across the visible experience.",
        "focus": ["Presentation", "Visual hierarchy", "Interaction"],
        "core_question": "Do repeated controls, layouts, and visual treatments behave and look consistent enough to feel mature and predictable?",
        "look_for": [
            "Repeated controls use stable labels, placement, and visual emphasis.",
            "Spacing, typography, and component styling create a coherent system instead of one-off treatments.",
            "Navigation, cards, forms, and CTAs use recognizable patterns across screens or sections.",
            "The interface follows visible platform conventions rather than forcing users to relearn patterns.",
        ],
        "healthy_signals": [
            "Component families feel related through spacing, shape, hierarchy, and behavior cues.",
            "Repeated navigation, CTA, and form patterns stay visually and semantically aligned.",
            "The experience looks intentionally systemized rather than assembled screen by screen.",
        ],
        "failure_modes": [
            "Equivalent actions look unrelated or switch labels, tone, or emphasis across contexts.",
            "Spacing, typography, or card treatments vary enough to weaken predictability and polish.",
            "One section or page looks like a different product, forcing the user to re-evaluate what is interactive.",
            "The UI mixes conventions in a way that makes repeated use less efficient.",
        ],
        "out_of_scope": [
            "Do not force visual sameness when a difference clearly communicates state or priority.",
            "Do not over-penalize brand-led variation unless it breaks predictability or recognition.",
        ],
        "severity_ladder": {
            "high": "Visible inconsistency makes the product feel immature, unreliable, or cognitively expensive to use.",
            "medium": "Most patterns work, but recurring inconsistency would slow trust-building and repeated use.",
            "low": "The interface is coherent overall, with only localized or cosmetic inconsistency.",
        },
        "evidence_expectations": [
            "Compare at least two visible examples when claiming inconsistency.",
            "Name the component family or repeated pattern that changes across contexts.",
        ],
        "default_fix": "Normalize repeated controls, spacing, and emphasis so equivalent actions and components feel predictably related.",
    },
    {
        "id": "content_microcopy",
        "name": "Content, Labels & Microcopy",
        "short_name": "Content & Microcopy",
        "description": "How clearly visible messaging, headings, labels, and CTA text explain value, meaning, and the next step.",
        "focus": ["Content", "Labeling", "Interaction"],
        "core_question": "Do the visible words use the buyer's language and make the message and actions immediately understandable?",
        "look_for": [
            "Headlines and supporting copy explain what the product is, for whom, and why it matters.",
            "Labels and CTA text tell users what they can do without relying on jargon or internal language.",
            "Microcopy reduces hesitation by clarifying consequences, formats, and expectations.",
            "The visible copy is concise enough to scan while still answering key user questions.",
        ],
        "healthy_signals": [
            "Headings answer user questions quickly and match the visible action path.",
            "CTA and control labels use familiar, concrete language tied to an outcome.",
            "Required instructions, helper text, and reassurance appear where the user needs them.",
        ],
        "failure_modes": [
            "Headlines are generic, clever, or internally focused rather than buyer-facing.",
            "Buttons, labels, or helper text force users to guess what a control means or what comes next.",
            "Copy is dense, repetitive, or vague enough to weaken scanning and comprehension.",
            "Jargon appears without explanation, or the page talks about the company without clarifying user value.",
        ],
        "out_of_scope": [
            "Do not infer a weak proposition from missing business context if the visible copy is still clear and specific.",
            "Do not flag every short heading as weak when the surrounding copy and CTA provide enough meaning.",
        ],
        "severity_ladder": {
            "high": "The visible wording makes the product, action, or value proposition hard to understand.",
            "medium": "The copy is understandable, but vague, generic, or inconsistent enough to slow decision-making.",
            "low": "The wording is mostly clear, with only localized wording or tone improvements needed.",
        },
        "evidence_expectations": [
            "Quote the actual heading, label, or CTA text that supports the finding.",
            "Explain what a first-time visitor would still not understand after reading the visible copy.",
        ],
        "default_fix": "Rewrite the visible message and labels in user language, clarify the value proposition, and make CTA outcomes explicit.",
    },
]

AXIS_KEYWORDS = {
    "task_execution": [
        "call to action",
        "cta",
        "submit",
        "continue",
        "next step",
        "primary action",
        "form",
        "input",
        "error message",
        "confirmation",
        "loading",
        "feedback",
        "workflow",
    ],
    "flow_architecture": [
        "navigation",
        "menu",
        "breadcrumb",
        "wayfinding",
        "information architecture",
        "section order",
        "grouped",
        "orientation",
        "hierarchy",
        "findability",
        "page structure",
        "layout",
    ],
    "trust_accessibility": [
        "contrast",
        "focus",
        "focus visible",
        "target size",
        "label",
        "instructions",
        "required",
        "privacy",
        "security",
        "trust",
        "proof",
        "accessibility",
        "wcag",
        "contact",
    ],
    "ui_consistency": [
        "consistent",
        "consistency",
        "design system",
        "pattern",
        "predictable",
        "spacing",
        "same order",
        "visual style",
        "component",
        "layout consistency",
        "repeated",
    ],
    "content_microcopy": [
        "copy",
        "microcopy",
        "heading",
        "headline",
        "label",
        "button text",
        "terminology",
        "jargon",
        "plain language",
        "message",
        "value proposition",
        "cta label",
    ],
}

AXIS_IMPACT = {
    "task_execution": "Friction in key tasks increases abandonment and weakens the product story in a live sales context.",
    "flow_architecture": "Weak architecture slows comprehension and makes the offer feel less mature and less navigable.",
    "trust_accessibility": "Visible trust and accessibility gaps increase perceived risk and narrow the reachable audience.",
    "ui_consistency": "Inconsistent UI signals reduce perceived product maturity and make repeat use less efficient.",
    "content_microcopy": "Unclear messaging makes the value proposition, controls, and next steps harder to understand and repeat.",
}

AXIS_USER_IMPACT = {
    "task_execution": "core tasks demand more effort than they should",
    "flow_architecture": "people struggle to understand where they are, what comes next, or how the experience is organized",
    "trust_accessibility": "parts of the experience may not feel safe, inclusive, or reliable enough",
    "ui_consistency": "recurring patterns do not behave or look consistently, which makes the product feel less mature",
    "content_microcopy": "the value proposition and interaction cues are harder to understand than they should be",
}


# Criteria v2: Market Alignment is removed, and Visual Brand is folded into
# UI Consistency so every audit stays focused on directly observable UX/UI
# evidence.
_ACTIVE_AXIS_IDS = {
    "task_execution",
    "flow_architecture",
    "trust_accessibility",
    "ui_consistency",
    "content_microcopy",
}
AXIS_DEFINITIONS = [axis for axis in AXIS_DEFINITIONS if axis["id"] in _ACTIVE_AXIS_IDS]
for axis in AXIS_DEFINITIONS:
    if axis["id"] == "task_execution":
        axis["name"] = "Performance and Task Execution"
        axis["short_name"] = "Performance & Task"
        axis["description"] = (
            "How quickly and clearly the interface loads, responds, and helps a first-time visitor "
            "complete the primary task without friction."
        )
        axis["look_for"] = [
            "Core Web Vitals evidence when a live URL is available: LCP, INP, and CLS.",
            "Lighthouse-style opportunities such as render blocking resources, heavy images, unused code, and slow server response.",
            "A primary CTA or next step is visible, specific, and not buried by competing actions.",
            "Forms, key controls, and step transitions explain what happens next and provide feedback.",
        ]
        axis["evidence_expectations"] = [
            "Quote the visible CTA label, form label, state message, or step name when possible.",
            "Use measured performance data when available; otherwise mark performance comments as visual or heuristic.",
        ]
    elif axis["id"] == "trust_accessibility":
        axis["name"] = "Trust & Accessibility"
        axis["short_name"] = "Trust & Accessibility"
        axis["look_for"] = [
            "WCAG 2.2 contrast and non-text contrast risks detected from rendered foreground/background measurements.",
            "WCAG 2.2 keyboard, focus order, and focus-visible risks detected from runtime tab traversal when available.",
            "WCAG 2.2 name/role/value, link-purpose, target-size, label, and instruction risks detected from controls and forms.",
            "Links and buttons that fail activation, produce no visible result, or are exposed while off-screen/hidden.",
        ]
    elif axis["id"] == "ui_consistency":
        axis["name"] = "Visual Brand & UI Consistency"
        axis["short_name"] = "Visual & UI Consistency"
        axis["description"] = (
            "How consistently components, hierarchy, visual brand treatments, and recurring interaction "
            "patterns are applied across the visible experience."
        )
        axis["look_for"] = [
            "Repeated controls use stable labels, placement, shape, spacing, and emphasis.",
            "The audit compares component families, such as buttons, cards, nav items, and form fields, and flags unexplained outliers.",
            "Hierarchy directs attention to the most important content, proof, and actions.",
            "Color, typography, imagery, and composition feel like one product system rather than disconnected screens.",
        ]

AXIS_KEYWORDS["task_execution"] = list(
    dict.fromkeys(
        [
            *AXIS_KEYWORDS.get("task_execution", []),
            "largest contentful paint",
            "lcp",
            "interaction to next paint",
            "inp",
            "cumulative layout shift",
            "cls",
            "core web vitals",
            "lighthouse",
            "page speed",
            "render blocking",
            "server response",
            "unused javascript",
            "image optimization",
        ]
    )
)
AXIS_KEYWORDS["ui_consistency"] = list(
    dict.fromkeys(
        [
            *AXIS_KEYWORDS.get("ui_consistency", []),
            "visual hierarchy",
            "brand",
            "imagery",
            "hero",
            "focal point",
            "distinctive",
            "polish",
            "color palette",
            "composition",
        ]
    )
)
AXIS_KEYWORDS = {axis_id: values for axis_id, values in AXIS_KEYWORDS.items() if axis_id in _ACTIVE_AXIS_IDS}
AXIS_IMPACT["task_execution"] = (
    "Slow loading, weak responsiveness, or unclear task flow increases abandonment and weakens the product story."
)
AXIS_IMPACT["trust_accessibility"] = (
    "Visible trust and WCAG 2.2 accessibility gaps increase perceived risk and narrow the reachable audience."
)
AXIS_IMPACT["ui_consistency"] = (
    "Inconsistent components, hierarchy, or brand treatments reduce perceived product maturity and make repeat use less efficient."
)
AXIS_IMPACT = {axis_id: value for axis_id, value in AXIS_IMPACT.items() if axis_id in _ACTIVE_AXIS_IDS}
AXIS_USER_IMPACT["task_execution"] = "the page feels slow, unresponsive, or harder to complete than it should"
AXIS_USER_IMPACT["trust_accessibility"] = "parts of the experience may not meet visible WCAG 2.2 expectations or feel reliable enough"
AXIS_USER_IMPACT["ui_consistency"] = (
    "recurring patterns, hierarchy, or brand treatments do not look consistent, which makes the product feel less mature"
)
AXIS_USER_IMPACT = {axis_id: value for axis_id, value in AXIS_USER_IMPACT.items() if axis_id in _ACTIVE_AXIS_IDS}


ROOT_DIR = Path(__file__).resolve().parents[2]
AUDIT_CRITERIA_CONFIG_PATH = Path(
    os.getenv("AUDIT_CRITERIA_CONFIG_PATH") or ROOT_DIR / "shared" / "config" / "audit_axes.json"
)
EDITABLE_AXIS_FIELDS = {
    "name",
    "short_name",
    "description",
    "focus",
    "core_question",
    "look_for",
    "healthy_signals",
    "failure_modes",
    "out_of_scope",
    "severity_ladder",
    "evidence_expectations",
    "default_fix",
}
_DEFAULT_AXIS_DEFINITIONS = copy.deepcopy(AXIS_DEFINITIONS)
_DEFAULT_AXIS_KEYWORDS = copy.deepcopy(AXIS_KEYWORDS)
_DEFAULT_AXIS_IMPACT = copy.deepcopy(AXIS_IMPACT)
_DEFAULT_AXIS_USER_IMPACT = copy.deepcopy(AXIS_USER_IMPACT)


def _clean_string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _clean_severity_ladder(value: Any, fallback: Dict[str, Any]) -> Dict[str, str]:
    source = value if isinstance(value, dict) else fallback if isinstance(fallback, dict) else {}
    return {
        "high": " ".join(str(source.get("high") or "").split()).strip(),
        "medium": " ".join(str(source.get("medium") or "").split()).strip(),
        "low": " ".join(str(source.get("low") or "").split()).strip(),
    }


def _clean_criteria_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_custom_axis_id(value: Any, fallback: str) -> str:
    raw = _clean_criteria_text(value) or fallback
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return normalized or fallback


def _unique_axis_id(axis_id: str, existing: Dict[str, Dict[str, Any]]) -> str:
    if axis_id not in existing:
        return axis_id
    index = 2
    while f"{axis_id}_{index}" in existing:
        index += 1
    return f"{axis_id}_{index}"


def _blank_custom_axis(axis_id: str, order: int) -> Dict[str, Any]:
    return {
        "id": axis_id,
        "name": f"Custom audit axis {order}",
        "short_name": f"Custom axis {order}",
        "description": "Custom UX/UI audit axis.",
        "focus": [],
        "core_question": "What should the audit decide for this custom axis?",
        "look_for": [],
        "healthy_signals": [],
        "failure_modes": [],
        "out_of_scope": [],
        "severity_ladder": {
            "high": "The issue creates a major user or business risk.",
            "medium": "The issue creates meaningful friction but does not block the journey.",
            "low": "The issue is localized or mostly polish-related.",
        },
        "evidence_expectations": [],
        "default_fix": "Review the cited evidence and improve this custom axis before launch.",
    }


def _criteria_payload_from_parts(
    axes: List[Dict[str, Any]],
    keywords: Dict[str, List[str]],
    impacts: Dict[str, str],
    user_impacts: Dict[str, str],
    *,
    source: str,
) -> Dict[str, Any]:
    payload_axes = []
    for axis in axes:
        axis_id = str(axis.get("id") or "").strip()
        item = copy.deepcopy(axis)
        item["keywords"] = list(keywords.get(axis_id) or [])
        item["business_impact"] = str(impacts.get(axis_id) or "").strip()
        item["user_impact"] = str(user_impacts.get(axis_id) or "").strip()
        payload_axes.append(item)
    return {
        "version": 1,
        "source": source,
        "configPath": str(AUDIT_CRITERIA_CONFIG_PATH),
        "axes": payload_axes,
    }


def default_audit_criteria_payload() -> Dict[str, Any]:
    return _criteria_payload_from_parts(
        copy.deepcopy(_DEFAULT_AXIS_DEFINITIONS),
        copy.deepcopy(_DEFAULT_AXIS_KEYWORDS),
        copy.deepcopy(_DEFAULT_AXIS_IMPACT),
        copy.deepcopy(_DEFAULT_AXIS_USER_IMPACT),
        source="defaults",
    )


def _apply_criteria_payload(payload: Dict[str, Any]) -> None:
    global AXIS_DEFINITIONS, AXIS_KEYWORDS, AXIS_IMPACT, AXIS_USER_IMPACT

    axes_by_id = {axis["id"]: copy.deepcopy(axis) for axis in _DEFAULT_AXIS_DEFINITIONS}
    default_axis_ids = set(axes_by_id)
    keyword_map = copy.deepcopy(_DEFAULT_AXIS_KEYWORDS)
    impact_map = copy.deepcopy(_DEFAULT_AXIS_IMPACT)
    user_impact_map = copy.deepcopy(_DEFAULT_AXIS_USER_IMPACT)

    incoming_axes = payload.get("axes") if isinstance(payload, dict) else None
    if not isinstance(incoming_axes, list):
        raise ValueError("Criteria payload must contain an axes array.")

    requested_order: List[str] = []
    for index, incoming in enumerate(incoming_axes, start=1):
        if not isinstance(incoming, dict):
            continue
        proposed_id = _normalize_custom_axis_id(incoming.get("id"), f"custom_axis_{index}")
        if proposed_id in axes_by_id and proposed_id not in default_axis_ids and proposed_id in requested_order:
            proposed_id = _unique_axis_id(proposed_id, axes_by_id)
        axis_id = proposed_id
        if axis_id not in axes_by_id:
            axes_by_id[axis_id] = _blank_custom_axis(axis_id, index)
            keyword_map[axis_id] = []
            impact_map[axis_id] = "This custom axis affects the quality and credibility of the audited experience."
            user_impact_map[axis_id] = "this custom area may create avoidable user friction"
        requested_order.append(axis_id)
        current = axes_by_id[axis_id]
        for field in EDITABLE_AXIS_FIELDS:
            if field not in incoming:
                continue
            if field in {"focus", "look_for", "healthy_signals", "failure_modes", "out_of_scope", "evidence_expectations"}:
                cleaned = _clean_string_list(incoming.get(field))
                if cleaned:
                    current[field] = cleaned
            elif field == "severity_ladder":
                current[field] = _clean_severity_ladder(incoming.get(field), current.get(field) or {})
            else:
                text = _clean_criteria_text(incoming.get(field))
                if text:
                    current[field] = text
        keywords = _clean_string_list(incoming.get("keywords"))
        if keywords:
            keyword_map[axis_id] = keywords
        business_impact = _clean_criteria_text(incoming.get("business_impact"))
        if business_impact:
            impact_map[axis_id] = business_impact
        user_impact = _clean_criteria_text(incoming.get("user_impact"))
        if user_impact:
            user_impact_map[axis_id] = user_impact

    default_order = [axis["id"] for axis in _DEFAULT_AXIS_DEFINITIONS]
    ordered_ids: List[str] = []
    for axis_id in requested_order:
        if axis_id in axes_by_id and axis_id not in ordered_ids:
            ordered_ids.append(axis_id)
    for axis_id in default_order:
        if axis_id in axes_by_id and axis_id not in ordered_ids:
            ordered_ids.append(axis_id)

    AXIS_DEFINITIONS = [axes_by_id[axis_id] for axis_id in ordered_ids]
    AXIS_KEYWORDS = {axis_id: keyword_map[axis_id] for axis_id in ordered_ids if axis_id in keyword_map}
    AXIS_IMPACT = {axis_id: impact_map[axis_id] for axis_id in ordered_ids if axis_id in impact_map}
    AXIS_USER_IMPACT = {axis_id: user_impact_map[axis_id] for axis_id in ordered_ids if axis_id in user_impact_map}


def load_audit_criteria_config() -> Dict[str, Any]:
    if not AUDIT_CRITERIA_CONFIG_PATH.exists():
        _apply_criteria_payload(default_audit_criteria_payload())
        return current_audit_criteria_payload(source="defaults")
    try:
        payload = json.loads(AUDIT_CRITERIA_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Criteria file must contain a JSON object.")
        _apply_criteria_payload(payload)
        return current_audit_criteria_payload(source="custom")
    except Exception as exc:
        print(f"Warning: could not load audit criteria from {AUDIT_CRITERIA_CONFIG_PATH}: {exc}")
        _apply_criteria_payload(default_audit_criteria_payload())
        return current_audit_criteria_payload(source="defaults")


def current_audit_criteria_payload(*, source: str = "current") -> Dict[str, Any]:
    return _criteria_payload_from_parts(
        copy.deepcopy(AXIS_DEFINITIONS),
        copy.deepcopy(AXIS_KEYWORDS),
        copy.deepcopy(AXIS_IMPACT),
        copy.deepcopy(AXIS_USER_IMPACT),
        source=source,
    )


def save_audit_criteria_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Criteria payload must be a JSON object.")
    _apply_criteria_payload(payload)
    saved = current_audit_criteria_payload(source="custom")
    AUDIT_CRITERIA_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_CRITERIA_CONFIG_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return saved


def reset_audit_criteria_payload() -> Dict[str, Any]:
    global AXIS_DEFINITIONS, AXIS_KEYWORDS, AXIS_IMPACT, AXIS_USER_IMPACT
    AXIS_DEFINITIONS = copy.deepcopy(_DEFAULT_AXIS_DEFINITIONS)
    AXIS_KEYWORDS = copy.deepcopy(_DEFAULT_AXIS_KEYWORDS)
    AXIS_IMPACT = copy.deepcopy(_DEFAULT_AXIS_IMPACT)
    AXIS_USER_IMPACT = copy.deepcopy(_DEFAULT_AXIS_USER_IMPACT)
    if AUDIT_CRITERIA_CONFIG_PATH.exists():
        AUDIT_CRITERIA_CONFIG_PATH.unlink()
    return current_audit_criteria_payload(source="defaults")


load_audit_criteria_config()


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


def count_keyword_hits(texts: Iterable[str], keywords: Iterable[str]) -> int:
    haystack = " ".join(clean_text(text) for text in texts).lower()
    if not haystack:
        return 0

    hits = 0
    seen = set()
    for keyword in keywords:
        needle = clean_text(keyword).lower()
        if not needle or needle in seen:
            continue
        if " " in needle or "-" in needle:
            matched = needle in haystack
        else:
            matched = re.search(rf"\b{re.escape(needle)}\b", haystack) is not None
        if matched:
            seen.add(needle)
            hits += 1
    return hits


def contains_keyword(texts: Iterable[str], keywords: Iterable[str]) -> bool:
    return count_keyword_hits(texts, keywords) > 0


def axis_definition_by_id(axis_id: Any) -> Optional[Dict[str, Any]]:
    key = clean_text(axis_id)
    for axis in AXIS_DEFINITIONS:
        if axis["id"] == key:
            return axis
    return None


def axis_prompt_contract(axis: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": axis.get("id"),
        "name": axis.get("short_name") or axis.get("name"),
        "description": axis.get("description"),
        "core_question": axis.get("core_question"),
        "look_for": list(axis.get("look_for") or []),
        "healthy_signals": list(axis.get("healthy_signals") or []),
        "failure_modes": list(axis.get("failure_modes") or []),
        "out_of_scope": list(axis.get("out_of_scope") or []),
        "severity_ladder": dict(axis.get("severity_ladder") or {}),
        "evidence_expectations": list(axis.get("evidence_expectations") or []),
    }
