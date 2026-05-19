from __future__ import annotations

import re
from uuid import uuid4

from figma_audit.audit.context import AuditContext
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import make_issue, normalized_text, text_nodes, text_sample
from figma_audit.models.issue import AuditIssue, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


CONTENT_SIMPLIFICATION_SOURCE = {
    "title": "Designing user interfaces for content simplification aimed at people with cognitive impairments",
    "authors": "Moreno, Petrie, Martinez, and Alarcon",
    "year": 2024,
    "journal": "Universal Access in the Information Society",
    "doi": "10.1007/s10209-023-00986-z",
}
PLACEHOLDER_TEXT_RE = re.compile(
    r"^(lorem ipsum|sample text|placeholder|text here|your text|headline|heading|title|subtitle|subheading|description|app name|company name|product name|tagline|menu item|label)$",
    re.IGNORECASE,
)
GENERIC_CTA_LABELS = {
    "button",
    "click here",
    "get started",
    "learn more",
    "more",
    "read more",
    "start",
    "submit",
    "next",
    "continue",
}
TASK_ACTION_TERMS = {
    "add",
    "book",
    "buy",
    "call",
    "cancel",
    "checkout",
    "choose",
    "confirm",
    "contact",
    "create",
    "delete",
    "edit",
    "log",
    "order",
    "pay",
    "register",
    "reset",
    "save",
    "search",
    "send",
    "sign",
    "subscribe",
    "track",
    "upload",
    "verify",
}
TASK_OBJECT_TERMS = {
    "account",
    "address",
    "appointment",
    "booking",
    "cart",
    "delivery",
    "driver",
    "email",
    "file",
    "invoice",
    "message",
    "notification",
    "order",
    "password",
    "payment",
    "photo",
    "profile",
    "rating",
    "review",
    "search",
    "settings",
    "subscription",
    "ticket",
}
CONCRETE_TASK_PHRASES = {
    "create account",
    "forgot password",
    "reset password",
    "sign in",
    "sign up",
    "log in",
    "check out",
    "checkout",
    "place order",
    "track order",
    "book appointment",
    "send message",
    "contact driver",
}
VAGUE_VALUE_TERMS = {
    "all in one",
    "amazing",
    "better",
    "effortless",
    "empower",
    "experience",
    "innovative",
    "intuitive",
    "next generation",
    "optimize",
    "powerful",
    "revolutionary",
    "seamless",
    "simple",
    "smart",
    "solution",
    "streamline",
    "transform",
    "unlock",
    "world class",
}
CONCRETE_COPY_TERMS = TASK_OBJECT_TERMS | {
    "calendar",
    "customer",
    "dashboard",
    "event",
    "form",
    "health",
    "home",
    "map",
    "music",
    "product",
    "sale",
    "task",
    "team",
    "trip",
    "wallet",
}
STOP_CONTEXT_WORDS = {
    "a",
    "an",
    "and",
    "bar",
    "button",
    "card",
    "component",
    "default",
    "frame",
    "group",
    "icon",
    "ios",
    "label",
    "mobile",
    "primary",
    "screen",
    "state",
    "tab",
    "text",
    "the",
    "to",
    "ui",
    "variant",
    "with",
}
WORD_RE = re.compile(r"[a-z][a-z'-]*")
SENTENCE_RE = re.compile(r"[.!?]+")


def _content_evidence(subdetector: str, checks: dict[str, object]) -> dict[str, object]:
    return {
        "validation_method": "content_simplification_static_figma_gate",
        "validation_source": CONTENT_SIMPLIFICATION_SOURCE,
        "content_microcopy_subdetector": subdetector,
        "plain_language_checks": checks,
        "validation_question": (
            "Do visible words make the meaning, action, and expected outcome clear without forcing inference?"
        ),
    }


def _tokens(value: str) -> list[str]:
    return WORD_RE.findall(normalized_text(value))


def _specific_tokens(value: str) -> set[str]:
    return {
        token
        for token in _tokens(value)
        if len(token) >= 3 and token not in STOP_CONTEXT_WORDS
    }


def _word_count(value: str) -> int:
    return len(_tokens(value))


def _average_sentence_words(value: str) -> float:
    sentences = [
        sentence
        for sentence in SENTENCE_RE.split(value.strip())
        if _word_count(sentence) > 0
    ]
    if not sentences:
        return float(_word_count(value))
    return round(sum(_word_count(sentence) for sentence in sentences) / len(sentences), 2)


def _is_control_label(ctx: AuditContext, node: NormalizedNode) -> bool:
    if ctx.contains_action_text(node):
        return True
    return any(ctx.is_control_like(ancestor) for ancestor in ctx.iter_ancestors(node))


def _visible_context_text(ctx: AuditContext, node: NormalizedNode, label: str) -> str:
    parts = [node.name, node.frame_name, node.path]
    viewport = ctx.mobile_viewport_for(node)
    if viewport is not None:
        parts.extend([viewport.name, viewport.path])
        for text_node in ctx.text_nodes_in_subtree(viewport):
            if text_node.id == node.id:
                continue
            sample = normalized_text(text_node.characters)
            if sample and len(sample) <= 100:
                parts.append(sample)
    else:
        for ancestor in ctx.iter_ancestors(node):
            parts.extend([ancestor.name, ancestor.frame_name, ancestor.path])

    context = normalized_text(" ".join(str(part or "") for part in parts))
    if label:
        context = re.sub(rf"\b{re.escape(label)}\b", " ", context)
    return re.sub(r"\s+", " ", context).strip()


def _has_meaningful_action_context(
    ctx: AuditContext,
    node: NormalizedNode,
    label: str,
) -> tuple[bool, dict[str, object]]:
    context_text = _visible_context_text(ctx, node, label)
    tokens = _specific_tokens(context_text)
    matching_phrases = sorted(phrase for phrase in CONCRETE_TASK_PHRASES if phrase in context_text)
    action_terms = sorted(tokens & TASK_ACTION_TERMS)
    object_terms = sorted(tokens & TASK_OBJECT_TERMS)
    has_context = bool(matching_phrases or (action_terms and object_terms))
    return has_context, {
        "context_tokens": sorted(tokens)[:16],
        "matching_task_phrases": matching_phrases,
        "action_terms": action_terms,
        "object_terms": object_terms,
        "has_visible_task_context": has_context,
    }


def _vague_value_hits(label: str) -> list[str]:
    normalized = normalized_text(label)
    return sorted(term for term in VAGUE_VALUE_TERMS if term in normalized)


def _is_dense_plain_language_risk(label: str) -> tuple[bool, dict[str, object]]:
    word_count = _word_count(label)
    average_sentence_words = _average_sentence_words(label)
    has_structural_breaks = "\n" in label or ";" in label or ":" in label
    is_dense = word_count >= 36 and average_sentence_words >= 22 and not has_structural_breaks
    return is_dense, {
        "word_count": word_count,
        "average_sentence_words": average_sentence_words,
        "has_structural_breaks": has_structural_breaks,
        "maximum_words_for_unbroken_mobile_copy": 35,
        "maximum_average_sentence_words": 21,
    }


def _is_vague_value_copy(label: str) -> tuple[bool, dict[str, object]]:
    hits = _vague_value_hits(label)
    tokens = _specific_tokens(label)
    concrete_hits = sorted(tokens & CONCRETE_COPY_TERMS)
    has_vague_claim = len(hits) >= 2 and _word_count(label) >= 5 and not concrete_hits
    return has_vague_claim, {
        "vague_terms": hits,
        "concrete_terms": concrete_hits,
        "minimum_vague_terms": 2,
    }


def _is_truncated_or_clipped_text(label: str) -> tuple[bool, dict[str, object]]:
    normalized = normalized_text(label)
    has_ellipsis = "..." in label or "…" in label
    ends_abruptly = normalized.endswith(("...", "…"))
    return bool(has_ellipsis or ends_abruptly), {
        "has_ellipsis": has_ellipsis,
        "ends_abruptly": ends_abruptly,
    }


def detect_placeholder_or_generic_copy(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """
    Detect visible mobile copy that makes users infer meaning or outcomes.

    The gate is intentionally conservative and relies on static Figma evidence:
    placeholder labels, generic actions without visible task context, vague value
    claims without concrete nouns, and dense unbroken text.
    """
    ctx = AuditContext(normalized_file)
    ranked: list[tuple[float, float, int, AuditIssue]] = []
    seen: set[tuple[str, str]] = set()

    for node in text_nodes(ctx):
        label = normalized_text(node.characters)
        if not label:
            continue

        is_placeholder = bool(PLACEHOLDER_TEXT_RE.match(label))
        is_generic_cta = label in GENERIC_CTA_LABELS and _is_control_label(ctx, node)
        has_action_context = False
        action_context_checks: dict[str, object] = {}
        if is_generic_cta:
            has_action_context, action_context_checks = _has_meaningful_action_context(ctx, node, label)
            is_generic_cta = not has_action_context
        is_dense, dense_checks = _is_dense_plain_language_risk(node.characters or "")
        is_vague_value, vague_checks = _is_vague_value_copy(node.characters or "")
        is_truncated, truncated_checks = _is_truncated_or_clipped_text(node.characters or "")

        subdetector = ""
        severity = Severity.LOW
        confidence = "low"
        confidence_reason = ""
        score = 0.0
        checks: dict[str, object] = {
            "matched_text": label,
            "word_count": _word_count(node.characters or ""),
            "is_control_label": _is_control_label(ctx, node),
        }
        if is_placeholder:
            subdetector = "placeholder_text"
            severity = Severity.MEDIUM
            confidence = "high"
            score = 3.0
            confidence_reason = (
                "The visible text is an exact placeholder or template label, so it does not explain real user meaning."
            )
            checks.update(
                {
                    "is_placeholder": True,
                    "is_generic_cta_without_context": False,
                    "is_vague_value_copy": False,
                    "is_dense_plain_language_risk": False,
                }
            )
        elif is_generic_cta:
            subdetector = "generic_cta_without_context"
            severity = Severity.LOW
            confidence = "medium"
            score = 2.0
            confidence_reason = (
                "The action label is generic and nearby visible copy does not provide a concrete task or outcome."
            )
            checks.update(
                {
                    "is_placeholder": False,
                    "is_generic_cta_without_context": True,
                    "is_vague_value_copy": False,
                    "is_dense_plain_language_risk": False,
                    **action_context_checks,
                }
            )
        elif is_vague_value:
            subdetector = "vague_value_copy"
            severity = Severity.LOW
            confidence = "medium"
            score = 1.5
            confidence_reason = (
                "The visible wording uses broad value terms but lacks concrete product, task, or user-language anchors."
            )
            checks.update(
                {
                    "is_placeholder": False,
                    "is_generic_cta_without_context": False,
                    "is_vague_value_copy": True,
                    "is_dense_plain_language_risk": False,
                    **vague_checks,
                }
            )
        elif is_truncated:
            subdetector = "truncated_or_clipped_copy"
            severity = Severity.MEDIUM
            confidence = "high"
            score = 2.6
            confidence_reason = (
                "The visible text contains an ellipsis or abrupt clipping marker, so important copy may be cut off."
            )
            checks.update(
                {
                    "is_placeholder": False,
                    "is_generic_cta_without_context": False,
                    "is_vague_value_copy": False,
                    "is_dense_plain_language_risk": False,
                    "is_truncated_or_clipped_copy": True,
                    **truncated_checks,
                }
            )
        elif is_dense:
            subdetector = "dense_plain_language_risk"
            severity = Severity.LOW
            confidence = "low"
            score = 1.0
            confidence_reason = (
                "The text block is long and unbroken for a mobile screen, which can reduce scanning and comprehension."
            )
            checks.update(
                {
                    "is_placeholder": False,
                    "is_generic_cta_without_context": False,
                    "is_vague_value_copy": False,
                    "is_dense_plain_language_risk": True,
                    **dense_checks,
                }
            )
        else:
            continue

        seen_key = (subdetector, label)
        if seen_key in seen:
            continue
        seen.add(seen_key)
        ranked.append(
            (
                score,
                ctx.visual_priority(node),
                min(len(label), 120),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-placeholder-copy-{uuid4().hex}",
                    axis="content_microcopy",
                    criterion="content_microcopy",
                    severity=severity,
                    message="Visible copy is placeholder-like or too generic to explain meaning or outcome.",
                    node=node,
                    detector_id="placeholder_or_generic_copy",
                    confidence=confidence,
                    confidence_reason=confidence_reason,
                    evidence={
                        **_content_evidence(subdetector, checks),
                        "text_sample": text_sample(node),
                        "matched_text": label,
                        "is_placeholder": is_placeholder,
                        "is_generic_cta": is_generic_cta,
                        "has_action_context": has_action_context,
                        "limitations": [
                            "Static Figma data cannot judge brand voice, audience vocabulary, or hidden flows with certainty.",
                            "Generic wording is accepted when nearby visible context makes the action outcome clear.",
                        ],
                    },
                ),
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [issue for _, _, _, issue in ranked[:DETECTION_MAX_ISSUES_PER_CHECK]]
