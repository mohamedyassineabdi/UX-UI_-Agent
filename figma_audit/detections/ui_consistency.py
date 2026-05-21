from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import uuid4

from figma_audit.audit.context import AuditContext
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import make_issue, normalized_text
from figma_audit.models.issue import AuditIssue, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


WORD_RE = re.compile(r"[a-z0-9]+")
CONSISTENCY_INSPECTION_SOURCE = {
    "title": "Inter-Platform Consistency Inspection Method",
    "authors": "Khalid Majrashi",
    "year": 2023,
    "journal": "International Journal of Technology and Human Interaction",
    "doi": "10.4018/IJTHI.326058",
}
STATE_KEYWORDS = {
    "active",
    "checked",
    "danger",
    "destructive",
    "disabled",
    "error",
    "focus",
    "focused",
    "hover",
    "inactive",
    "off",
    "on",
    "pressed",
    "selected",
    "success",
    "warning",
}
WEAK_LABELS = {
    "action",
    "button",
    "default",
    "icon",
    "item",
    "label",
    "menu item",
    "state",
    "tab",
}


@dataclass(frozen=True)
class FieldRule:
    field_name: str
    dimension: str
    minimum_distance: float
    max_outlier_share: float = 0.2
    requires_action_family: bool = False


@dataclass(frozen=True)
class ConsistencyCandidate:
    node: NormalizedNode
    label: str
    state_tokens: frozenset[str]


TRACKED_FIELD_RULES = (
    FieldRule("corner_radius", "perceptual", 4),
    FieldRule("padding_left", "compositional", 4),
    FieldRule("padding_right", "compositional", 4),
    FieldRule("padding_top", "compositional", 4),
    FieldRule("padding_bottom", "compositional", 4),
    FieldRule("item_spacing", "compositional", 4),
    FieldRule("height", "compositional", 8, 0.16, True),
)


def _numeric_value(node: NormalizedNode, field_name: str) -> float | None:
    if field_name == "width":
        return round(float((node.absolute_bounding_box or {}).get("width") or 0), 2) or None
    if field_name == "height":
        return round(float((node.absolute_bounding_box or {}).get("height") or 0), 2) or None
    value = getattr(node, field_name, None)
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return None


def _inspection_evidence(dimension: str) -> dict[str, object]:
    return {
        "validation_method": "consistency_inspection_static_figma_gate",
        "validation_source": CONSISTENCY_INSPECTION_SOURCE,
        "consistency_dimensions": [dimension],
        "validation_question": (
            "Do equivalent visible controls preserve consistent perceptual, lexical, operational, and compositional cues?"
        ),
    }


def _node_text(ctx: AuditContext, node: NormalizedNode) -> str:
    labels = []
    if node.type == "TEXT" and ctx.has_text(node):
        labels.append(node.characters or "")
    labels.extend(text.characters or "" for text in ctx.text_nodes_in_subtree(node) if ctx.has_text(text))
    return normalized_text(" ".join(labels))


def _node_context_text(ctx: AuditContext, node: NormalizedNode) -> str:
    return normalized_text(" ".join(str(value or "") for value in (node.name, node.frame_name, node.path, _node_text(ctx, node))))


def _state_tokens(ctx: AuditContext, node: NormalizedNode) -> frozenset[str]:
    context = _node_context_text(ctx, node)
    words = set(WORD_RE.findall(context))
    return frozenset(token for token in STATE_KEYWORDS if token in words)


def _is_meaningful_state_variant(
    ctx: AuditContext,
    *,
    target: NormalizedNode,
    peers: list[NormalizedNode],
) -> bool:
    target_states = _state_tokens(ctx, target)
    if not target_states:
        return False
    peer_states = [
        _state_tokens(ctx, peer)
        for peer in peers
        if peer.id != target.id
    ]
    if not peer_states:
        return False
    dominant_state, dominant_count = Counter(peer_states).most_common(1)[0]
    return target_states != dominant_state and dominant_count / len(peer_states) >= 0.6


def _candidate_label(ctx: AuditContext, node: NormalizedNode) -> str:
    label = _node_text(ctx, node)
    if label:
        return " ".join(WORD_RE.findall(label))
    return " ".join(WORD_RE.findall(normalized_text(node.name)))


def _is_action_like_family(ctx: AuditContext, nodes: list[NormalizedNode]) -> bool:
    family_text = " ".join(normalized_text(node.name) for node in nodes[:8])
    return any(token in family_text for token in ("action", "button", "btn", "cta", "control", "menu", "tab"))


def _family_candidates(ctx: AuditContext, nodes: list[NormalizedNode]) -> list[ConsistencyCandidate]:
    candidates = [
        ConsistencyCandidate(
            node=node,
            label=_candidate_label(ctx, node),
            state_tokens=_state_tokens(ctx, node),
        )
        for node in nodes
    ]
    return [
        candidate
        for candidate in candidates
        if candidate.label and candidate.label not in WEAK_LABELS and len(candidate.label) <= 28
    ]


def _detect_lexical_outliers(
    ctx: AuditContext,
    families: dict[str, list[NormalizedNode]],
) -> list[tuple[float, float, AuditIssue]]:
    ranked: list[tuple[float, float, AuditIssue]] = []
    seen_patterns: set[tuple[str, str, tuple[str, ...]]] = set()
    for family, nodes in families.items():
        if len(nodes) < 4 or not _is_action_like_family(ctx, nodes):
            continue
        candidates = _family_candidates(ctx, nodes)
        if len(candidates) < 4:
            continue
        labels = [candidate.label for candidate in candidates]
        dominant_label, dominant_count = Counter(labels).most_common(1)[0]
        dominant_ratio = dominant_count / len(labels)
        if dominant_ratio < 0.75:
            continue
        outlier_candidates = [
            candidate
            for candidate in candidates
            if candidate.label != dominant_label and candidate.state_tokens == frozenset()
        ]
        if len(outlier_candidates) != 1:
            continue
        target = outlier_candidates[0].node
        pattern = (
            dominant_label,
            _candidate_label(ctx, target),
            tuple(sorted(outlier_candidates[0].state_tokens)),
        )
        if pattern in seen_patterns:
            continue
        seen_patterns.add(pattern)
        ranked.append(
            (
                6.0,
                ctx.visual_priority(target),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-lexical-consistency-outlier-{uuid4().hex}",
                    axis="ui_consistency",
                    criterion="ui_consistency",
                    severity=Severity.LOW,
                    message="A repeated control family uses one inconsistent visible label.",
                    node=target,
                    detector_id="component_style_outlier",
                    confidence="medium",
                    confidence_reason=(
                        "The consistency inspection found one lexical outlier in a repeated action-like component family."
                    ),
                    evidence={
                        **_inspection_evidence("lexical"),
                        "ui_consistency_subdetector": "lexical_label_outlier",
                        "family_key": family,
                        "dominant_label": dominant_label,
                        "outlier_label": _candidate_label(ctx, target),
                        "family_sample_size": len(labels),
                        "dominant_ratio": round(dominant_ratio, 2),
                        "label_distribution": dict(Counter(labels)),
                        "limitations": [
                            "Static Figma data cannot prove that different labels represent different runtime actions.",
                        ],
                    },
                ),
            )
        )
    return ranked


def detect_component_style_outlier(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """
    Detect repeated control-like families with one clear consistency outlier.
    """
    ctx = AuditContext(normalized_file)
    families: dict[str, list[NormalizedNode]] = defaultdict(list)

    for node in ctx.client_visible_nodes:
        if node.type not in {"FRAME", "INSTANCE", "COMPONENT"}:
            continue
        if ctx.is_control_like(node):
            families[ctx.family_key(node)].append(node)

    ranked: list[tuple[float, float, AuditIssue]] = []

    for family, nodes in families.items():
        if len(nodes) < 4:
            continue

        for rule in TRACKED_FIELD_RULES:
            field_name = rule.field_name
            if rule.requires_action_family and not _is_action_like_family(ctx, nodes):
                continue
            values = [value for node in nodes if (value := _numeric_value(node, field_name)) is not None]
            if len(values) < 4:
                continue
            if field_name == "height" and max(values) > 128:
                continue

            dominant, dominant_count = Counter(values).most_common(1)[0]
            dominant_ratio = dominant_count / len(values)
            if dominant_ratio < 0.75:
                continue

            outliers = [
                value
                for value in sorted(set(values))
                if value != dominant and abs(value - dominant) >= rule.minimum_distance
            ]
            if not outliers:
                continue

            outlier = outliers[0]
            outlier_count = values.count(outlier)
            if outlier_count > 2 or (outlier_count > 1 and outlier_count / len(values) > rule.max_outlier_share):
                continue

            target = next(node for node in nodes if _numeric_value(node, field_name) == outlier)
            if _is_meaningful_state_variant(ctx, target=target, peers=nodes):
                continue
            distance = abs(outlier - dominant)
            ranked.append(
                (
                    distance,
                    ctx.visual_priority(target),
                    make_issue(
                        ctx=ctx,
                        issue_id=f"draft-style-outlier-{uuid4().hex}",
                        axis="ui_consistency",
                        criterion="ui_consistency",
                        severity=Severity.MEDIUM,
                        message="A repeated control-like component has a clear style value outlier.",
                        node=target,
                        detector_id="component_style_outlier",
                        confidence="high",
                        confidence_reason=(
                            "The consistency inspection found a repeated control-like family with a dominant "
                            "style or layout value and one rare outlier, excluding visible state variants."
                        ),
                        evidence={
                            **_inspection_evidence(rule.dimension),
                            "ui_consistency_subdetector": "numeric_value_outlier",
                            "family_key": family,
                            "field": field_name,
                            "field_dimension": rule.dimension,
                            "dominant_value": dominant,
                            "outlier_value": outlier,
                            "distance": distance,
                            "family_sample_size": len(values),
                            "dominant_ratio": round(dominant_ratio, 2),
                            "value_distribution": dict(Counter(values)),
                            "target_state_tokens": sorted(_state_tokens(ctx, target)),
                            "limitations": [
                                "Static Figma data cannot prove whether an undocumented variation is intentional.",
                            ],
                        },
                    ),
                )
            )

    ranked.extend(_detect_lexical_outliers(ctx, families))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [issue for _, _, issue in ranked[:DETECTION_MAX_ISSUES_PER_CHECK]]
