from __future__ import annotations

import re
from uuid import uuid4

from figma_audit.audit.context import AuditContext
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import descendant_text, make_issue, normalized_text
from figma_audit.models.issue import AuditIssue, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile


VALUE_COMMUNICATION_SOURCE = {
    "title": "Which Hierarchical Levels of Value Description of Design Concepts Enhance Anticipated UX? Effects of Product Type on User Expectations",
    "authors": "Toshihisa Doi and Sayoko Doi",
    "year": 2023,
    "journal": "Systems",
    "doi": "10.3390/systems11050230",
}
GENERIC_OFFER_PHRASES = {
    "all in one",
    "better way",
    "company name",
    "headline",
    "product name",
    "tagline",
    "unlock more",
    "welcome",
    "your product",
}
PLACEHOLDER_IDENTITY_PHRASES = {
    "app name",
    "company name",
    "product name",
    "tagline",
}
ABSTRACT_VALUE_PHRASES = {
    "amazing experience",
    "best solution",
    "effortless experience",
    "empower",
    "future of",
    "innovative solution",
    "next generation",
    "powerful solution",
    "seamless experience",
    "smart solution",
    "transform everything",
    "unlock",
    "world class",
}
AUDIENCE_TERMS = {
    "agencies",
    "admins",
    "buyers",
    "creators",
    "customers",
    "designers",
    "developers",
    "drivers",
    "enterprise",
    "families",
    "freelancers",
    "founders",
    "managers",
    "parents",
    "patients",
    "professionals",
    "sellers",
    "students",
    "teams",
    "users",
}
USE_CASE_TERMS = {
    "analyze",
    "automate",
    "book",
    "build",
    "checkout",
    "collaborate",
    "compare",
    "create",
    "deliver",
    "design",
    "edit",
    "learn",
    "manage",
    "monitor",
    "order",
    "pay",
    "plan",
    "schedule",
    "search",
    "sell",
    "ship",
    "track",
}
PRODUCT_ATTRIBUTE_TERMS = {
    "app",
    "automation",
    "calendar",
    "dashboard",
    "delivery",
    "editor",
    "file",
    "form",
    "invoice",
    "message",
    "payment",
    "platform",
    "report",
    "review",
    "screen",
    "tool",
    "workflow",
}
FUNCTIONAL_BENEFIT_PHRASES = {
    "automate",
    "compare",
    "find",
    "manage",
    "organize",
    "reduce",
    "save time",
    "schedule",
    "simplify",
    "speed up",
    "track",
}
EMOTIONAL_BENEFIT_PHRASES = {
    "confidence",
    "feel",
    "peace of mind",
    "stress free",
    "trusted",
}
COMMERCIAL_CTA_KEYWORDS = {
    "book",
    "book demo",
    "buy",
    "contact",
    "demo",
    "donate",
    "get started",
    "open",
    "pricing",
    "request demo",
    "subscribe",
    "trial",
}
PROOF_KEYWORDS = {
    "award",
    "case study",
    "clients",
    "customers",
    "rating",
    "ratings",
    "review",
    "reviews",
    "stars",
    "testimonial",
    "trusted",
}
COMMERCIAL_SURFACE_TERMS = (
    PLACEHOLDER_IDENTITY_PHRASES
    | COMMERCIAL_CTA_KEYWORDS
    | PROOF_KEYWORDS
    | {"app category", "developer", "plans", "pricing", "version history"}
)
MARKET_SURFACE_NAME_HINTS = {
    "appstore",
    "app store",
    "landing",
    "offer",
    "onboarding",
    "plans",
    "pricing",
    "product",
    "signup",
    "subscription",
    "welcome",
}
NON_MARKET_PATTERN_HINTS = {
    "action sheet",
    "actionsheet",
    "apps + widgets",
    "apps widgets",
    "contextualmenu",
    "home screen",
    "homescreen",
    "keyboard",
    "lockscreen",
    "lock screen",
    "picker",
    "toolbar",
}
WORD_RE = re.compile(r"[a-z][a-z'-]*")


def _contains_any(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _matched_phrases(text: str, phrases: set[str]) -> list[str]:
    return sorted(phrase for phrase in phrases if phrase in text)


def _tokens(text: str) -> set[str]:
    return set(WORD_RE.findall(normalized_text(text)))


def _has_audience_signal(text: str, tokens: set[str]) -> bool:
    if tokens & AUDIENCE_TERMS:
        return True
    return bool(re.search(r"\b(for|built for|made for)\s+[a-z][a-z\s'-]{2,28}\b", text))


def _value_layer_checks(text: str) -> dict[str, object]:
    tokens = _tokens(text)
    has_product_attribute = bool(tokens & PRODUCT_ATTRIBUTE_TERMS)
    has_use_case = bool(tokens & USE_CASE_TERMS)
    has_functional_benefit = _contains_any(text, FUNCTIONAL_BENEFIT_PHRASES)
    has_emotional_benefit = _contains_any(text, EMOTIONAL_BENEFIT_PHRASES)
    has_audience = _has_audience_signal(text, tokens)
    has_proof = _contains_any(text, PROOF_KEYWORDS) or bool(
        re.search(r"\b\d+(\.\d+)?\s*(k|m|%)?\s+(customers|users|reviews|ratings|stars)\b", text)
    )
    has_cta = _contains_any(text, COMMERCIAL_CTA_KEYWORDS)
    abstract_hits = _matched_phrases(text, ABSTRACT_VALUE_PHRASES)
    placeholder_hits = _matched_phrases(text, PLACEHOLDER_IDENTITY_PHRASES)
    generic_hits = _matched_phrases(text, GENERIC_OFFER_PHRASES)
    layers = {
        "audience_or_buyer": has_audience,
        "product_attribute": has_product_attribute,
        "use_case": has_use_case,
        "functional_benefit": has_functional_benefit,
        "emotional_benefit": has_emotional_benefit,
        "proof": has_proof,
        "commercial_next_step": has_cta,
    }
    return {
        "value_layers_present": layers,
        "value_layer_count": sum(1 for value in layers.values() if value),
        "placeholder_identity_hits": placeholder_hits,
        "generic_offer_hits": generic_hits,
        "abstract_value_hits": abstract_hits,
        "matched_audience_terms": sorted(tokens & AUDIENCE_TERMS),
        "matched_use_case_terms": sorted(tokens & USE_CASE_TERMS),
        "matched_product_attribute_terms": sorted(tokens & PRODUCT_ATTRIBUTE_TERMS),
        "matched_functional_benefit_phrases": _matched_phrases(text, FUNCTIONAL_BENEFIT_PHRASES),
        "matched_emotional_benefit_phrases": _matched_phrases(text, EMOTIONAL_BENEFIT_PHRASES),
        "has_proof": has_proof,
        "has_commercial_cta": has_cta,
        "is_commercial_surface": _contains_any(text, COMMERCIAL_SURFACE_TERMS),
    }


def _node_context(node) -> str:  # type: ignore[no-untyped-def]
    return normalized_text(" ".join(str(value or "") for value in (node.name, node.frame_name, node.path)))


def _is_market_review_surface(node, text: str, checks: dict[str, object]) -> bool:  # type: ignore[no-untyped-def]
    context = _node_context(node)
    has_surface_hint = _contains_any(context, MARKET_SURFACE_NAME_HINTS)
    is_app_store_like = any(
        phrase in text
        for phrase in ("app category", "developer", "ratings", "version history")
    )
    has_real_cta = any(
        phrase in text
        for phrase in COMMERCIAL_CTA_KEYWORDS
        if phrase != "open"
    )
    if _contains_any(context, NON_MARKET_PATTERN_HINTS) and not (has_surface_hint or is_app_store_like):
        return False
    if has_surface_hint or is_app_store_like:
        return True
    if has_real_cta and (
        checks["has_proof"]
        or checks["matched_audience_terms"]
        or checks["matched_use_case_terms"]
        or checks["abstract_value_hits"]
    ):
        return True
    if checks["abstract_value_hits"]:
        return True
    return False


def _surface_key(ctx: AuditContext, node) -> str:  # type: ignore[no-untyped-def]
    for current in [node, *ctx.iter_ancestors(node)]:
        context = _node_context(current)
        if _contains_any(context, MARKET_SURFACE_NAME_HINTS):
            return current.id
    return ctx.family_key(node)


def _market_alignment_evidence(subdetector: str, checks: dict[str, object]) -> dict[str, object]:
    return {
        "validation_method": "value_communication_static_figma_gate",
        "validation_source": VALUE_COMMUNICATION_SOURCE,
        "market_alignment_subdetector": subdetector,
        "value_communication_checks": checks,
        "validation_question": (
            "Is there exact visible placeholder identity text that can be boxed and replaced with final copy?"
        ),
    }


def _placeholder_identity_text_node(
    ctx: AuditContext,
    frame,  # type: ignore[no-untyped-def]
    placeholder_hits: list[str],
):
    if not placeholder_hits:
        return None
    hit_set = {normalized_text(hit) for hit in placeholder_hits}
    for node in ctx.text_nodes_in_subtree(frame):
        label = normalized_text(node.characters)
        if label in hit_set:
            return node
    return None


def _issue_profile(checks: dict[str, object]) -> tuple[str, Severity, str, str, float] | None:
    layers = checks["value_layers_present"]
    assert isinstance(layers, dict)
    value_layer_count = int(checks["value_layer_count"])
    placeholder_hits = checks["placeholder_identity_hits"]
    has_use_case = bool(layers["use_case"])

    if placeholder_hits and (value_layer_count < 4 or not has_use_case):
        return (
            "placeholder_offer_identity",
            Severity.MEDIUM,
            "high",
            "The exact visible identity text is still a placeholder, so it can be boxed and corrected without making business assumptions.",
            4.0,
        )
    return None


def detect_generic_offer_without_proof(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """
    Detect only boxable, visible offer-identity placeholder text.

    Market fit, proof, and business credibility are not reported as
    client-facing issues unless the screenshot contains exact visible copy that
    can be boxed.
    """
    ctx = AuditContext(normalized_file)
    ranked: list[tuple[float, float, float, AuditIssue]] = []
    emitted: set[str] = set()

    for node in ctx.client_visible_nodes:
        if not ctx.is_large_frame(node):
            continue

        text = descendant_text(ctx, node)
        if not text:
            continue

        checks = _value_layer_checks(text)
        if not _is_market_review_surface(node, text, checks):
            continue
        profile = _issue_profile(checks)
        if profile is None:
            continue

        subdetector, severity, confidence, confidence_reason, score = profile
        if subdetector != "placeholder_offer_identity":
            continue
        target_node = _placeholder_identity_text_node(
            ctx,
            node,
            list(checks.get("placeholder_identity_hits") or []),
        )
        if target_node is None:
            continue
        dedupe_key = f"{_surface_key(ctx, node)}:{subdetector}"
        if dedupe_key in emitted:
            continue
        emitted.add(dedupe_key)

        ranked.append(
            (
                score,
                ctx.visual_priority(node),
                ctx.bbox_area(node),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-generic-offer-{uuid4().hex}",
                    axis="market_alignment",
                    criterion="market_alignment",
                    severity=severity,
                    message="Visible placeholder identity text is still present in a client-facing screen.",
                    node=target_node,
                    detector_id="generic_offer_without_proof",
                    confidence=confidence,
                    confidence_reason=confidence_reason,
                    evidence={
                        **_market_alignment_evidence(subdetector, checks),
                        "text_sample": str(target_node.characters or "").strip(),
                        "visible_placeholder_identity": str(target_node.characters or "").strip(),
                        "matched_generic_offer": bool(checks["generic_offer_hits"]),
                        "has_commercial_cta_keyword": bool(checks["has_commercial_cta"]),
                        "has_proof_keyword": bool(checks["has_proof"]),
                        "limitations": [
                            "The client-facing issue is limited to the boxed visible placeholder text.",
                            "No business, market, or credibility conclusion is made without visible text evidence.",
                        ],
                    },
                ),
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [issue for _, _, _, issue in ranked[:DETECTION_MAX_ISSUES_PER_CHECK]]
