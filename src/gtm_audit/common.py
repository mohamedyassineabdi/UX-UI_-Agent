from __future__ import annotations

import re
import unicodedata
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
        "description": "How credible, safe, legible, and inclusive the visible experience feels for real-world use.",
        "focus": ["Content", "Labeling", "Forms", "Presentation"],
        "core_question": "Does the visible UI look credible and inclusive enough for a buyer to trust it and use it without avoidable barriers?",
        "look_for": [
            "Proof, contact, policy, or reassurance cues reduce perceived risk where users might hesitate.",
            "Text, controls, and focusable areas appear readable, distinguishable, and usable.",
            "Forms and required inputs show labels or instructions instead of relying on memory or guesswork.",
            "The visible experience avoids patterns that would feel unsafe, inaccessible, or careless.",
        ],
        "healthy_signals": [
            "Contrast, spacing, and label clarity support fast reading and confident interaction.",
            "Proof, client signals, policies, or contact information appear where they help reassure the user.",
            "Critical controls look large enough, separate enough, and identifiable enough to activate safely.",
        ],
        "failure_modes": [
            "Low-contrast text, weak focus cues, tiny targets, or unlabeled fields create visible access risk.",
            "The experience asks for trust without visible proof, reassurance, or contact paths.",
            "Important information is hidden in decorative or ambiguous visual treatment.",
            "The interface shows credibility damage such as broken assets, suspicious imagery, or obviously careless rendering.",
        ],
        "out_of_scope": [
            "Do not declare formal WCAG noncompliance unless the visible evidence clearly supports it.",
            "When keyboard behavior or screen-reader support is not visible, lower confidence instead of inventing failures.",
        ],
        "severity_ladder": {
            "high": "Visible issues would likely reduce trust, exclude some users, or make the product feel unsafe or careless.",
            "medium": "The UI looks mostly usable, but meaningful visible barriers or reassurance gaps remain.",
            "low": "Trust and accessibility cues are broadly present, with only secondary or localized weaknesses.",
        },
        "evidence_expectations": [
            "Cite the exact control, text treatment, proof section, or reassurance element that is visible or missing.",
            "Use cautious wording for synthetic-looking imagery or credibility issues and describe the visible signals.",
        ],
        "default_fix": "Add or strengthen reassurance cues, improve visible legibility and control clarity, and remove avoidable trust-damaging signals.",
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
        "id": "visual_brand",
        "name": "Visual Brand & Hierarchy",
        "short_name": "Visual Brand",
        "description": "How well the visible visual direction communicates brand quality, hierarchy, and differentiation on first impression.",
        "focus": ["Visual hierarchy", "Presentation", "Content"],
        "core_question": "Does the visual system make the product feel credible, memorable, and easy to scan at first glance?",
        "look_for": [
            "Hierarchy directs attention to the most important content, proof, and actions.",
            "Imagery, color, and composition support the product promise instead of diluting it.",
            "The interface balances restraint and distinctiveness rather than looking generic or chaotic.",
            "Visual polish supports trust without overpowering legibility or actionability.",
        ],
        "healthy_signals": [
            "The hero and major sections create a clear focal path from message to proof to action.",
            "Color and imagery feel intentional, brand-appropriate, and commercially credible.",
            "Visual hierarchy helps users distinguish primary information from supporting content.",
        ],
        "failure_modes": [
            "Everything competes for attention, leaving the user without a clear focal point.",
            "Generic, synthetic-looking, or mismatched visuals weaken authenticity or product differentiation.",
            "Decorative treatment overwhelms content, proof, or action cues.",
            "The interface feels visually flat or visually noisy, reducing memorability and confidence.",
        ],
        "out_of_scope": [
            "Do not critique brand taste in the abstract; focus on hierarchy, credibility, and commercial clarity.",
            "Do not double-count pure legibility issues here when trust/accessibility is the more accurate primary axis.",
        ],
        "severity_ladder": {
            "high": "Visible hierarchy or imagery choices materially weaken comprehension, credibility, or brand confidence.",
            "medium": "The experience looks serviceable, but the visual direction undersells the offer or creates friction.",
            "low": "The visual system is broadly effective, with only secondary hierarchy or polish opportunities.",
        },
        "evidence_expectations": [
            "Name the visible focal area, image treatment, or competing element that drives the conclusion.",
            "Explain how the visual treatment changes first-impression trust, hierarchy, or differentiation.",
        ],
        "default_fix": "Strengthen the focal path, reduce visual noise, and use imagery and contrast to reinforce a more credible brand story.",
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
    {
        "id": "market_alignment",
        "name": "Market Alignment & Offer Clarity",
        "short_name": "Market Alignment",
        "description": "How well the visible offer, audience cues, proof points, and CTA strategy support a coherent go-to-market story.",
        "focus": ["Content", "Navigation", "Interaction", "Visual hierarchy"],
        "core_question": "Would a target buyer quickly understand who this is for, why it is credible, and what commercial step to take next?",
        "look_for": [
            "The page signals a specific audience, use case, or business context instead of a generic promise.",
            "Proof, outcomes, or credibility cues support the value proposition before or near the CTA.",
            "The CTA strategy fits the maturity of the offer, such as demo, contact, trial, pricing, or deeper evaluation.",
            "The journey helps the visitor move from interest to evaluation without hunting for commercial next steps.",
        ],
        "healthy_signals": [
            "Audience cues, proof points, and CTA strategy reinforce the same commercial narrative.",
            "The offer is concrete enough that a buyer could explain it back after one pass.",
            "The visible journey gives an appropriate evaluation path for the product's sales motion.",
        ],
        "failure_modes": [
            "The product sounds relevant in general but not for a clearly identifiable buyer or use case.",
            "Proof, pricing, contact, or next-step strategy is missing or disconnected from the promise.",
            "The CTA asks for commitment too early, too late, or without enough supporting evidence.",
            "The journey looks polished, but the commercial story remains generic or under-supported.",
        ],
        "out_of_scope": [
            "Do not demand pricing or trial information if the visible sales motion clearly points to a different next step.",
            "Do not invent market positioning beyond what the page actually signals.",
        ],
        "severity_ladder": {
            "high": "A target buyer would struggle to decide whether the offer is for them or what commercial step to take next.",
            "medium": "The offer is directionally clear, but visible proof or audience specificity is not strong enough yet.",
            "low": "The GTM story is broadly credible, with only secondary gaps in proof, specificity, or CTA sequencing.",
        },
        "evidence_expectations": [
            "Reference the visible audience cue, proof point, or CTA strategy supporting the conclusion.",
            "Tie the finding to buyer understanding, trust, and decision readiness rather than abstract marketing quality.",
        ],
        "default_fix": "Make the target audience, commercial proof, and next-step strategy more explicit and more tightly connected on the page.",
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
    "visual_brand": [
        "visual hierarchy",
        "brand",
        "imagery",
        "hero",
        "focal point",
        "distinctive",
        "polish",
        "visual style",
        "color palette",
        "oversaturated",
        "visual metaphor",
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
    "market_alignment": [
        "audience",
        "value proposition",
        "proof point",
        "case study",
        "testimonial",
        "demo",
        "pricing",
        "offer",
        "conversion",
        "social proof",
        "target buyer",
        "market",
    ],
}

AXIS_IMPACT = {
    "task_execution": "Friction in key tasks increases abandonment and weakens the product story in a live sales context.",
    "flow_architecture": "Weak architecture slows comprehension and makes the offer feel less mature and less navigable.",
    "trust_accessibility": "Visible trust and accessibility gaps increase perceived risk and narrow the reachable audience.",
    "ui_consistency": "Inconsistent UI signals reduce perceived product maturity and make repeat use less efficient.",
    "visual_brand": "A weak visual narrative lowers memorability, credibility, and differentiation on first impression.",
    "content_microcopy": "Unclear messaging makes the value proposition, controls, and next steps harder to understand and repeat.",
    "market_alignment": "Weak GTM alignment makes it harder for prospects to see why the offer is relevant and what to do next.",
}

AXIS_USER_IMPACT = {
    "task_execution": "core tasks demand more effort than they should",
    "flow_architecture": "people struggle to understand where they are, what comes next, or how the experience is organized",
    "trust_accessibility": "parts of the experience may not feel safe, inclusive, or reliable enough",
    "ui_consistency": "recurring patterns do not behave or look consistently, which makes the product feel less mature",
    "visual_brand": "the interface does not project enough confidence, clarity, or brand distinctiveness at first glance",
    "content_microcopy": "the value proposition and interaction cues are harder to understand than they should be",
    "market_alignment": "prospects may not immediately understand why this product is relevant for their context or what step to take next",
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

