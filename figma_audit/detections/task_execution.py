from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable
from uuid import uuid4

from figma_audit.audit.context import AuditContext, CONTAINER_TYPES
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import make_issue, normalized_text
from figma_audit.models.issue import AuditIssue, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


DESTRUCTIVE_LABELS = {
    "clear all",
    "delete",
    "discard",
    "erase",
    "remove",
    "reset",
}
RECOVERY_LABELS = {
    "back",
    "cancel",
    "close",
    "dismiss",
    "keep",
    "never mind",
    "no",
    "restore",
    "undo",
}
CONFIRMATION_CONTEXT_LABELS = {
    "are you sure",
    "confirm",
    "confirm delete",
    "delete?",
    "delete item?",
}
TASK_COMPLETION_LABELS = {
    "apply",
    "checkout",
    "confirm",
    "continue",
    "create account",
    "done",
    "finish",
    "log in",
    "login",
    "next",
    "pay",
    "save",
    "search",
    "send",
    "sign in",
    "sign up",
    "start",
    "submit",
}
FORM_FIELD_KEYWORDS = {
    "address",
    "card",
    "city",
    "code",
    "country",
    "date",
    "email",
    "field",
    "form",
    "input",
    "name",
    "otp",
    "password",
    "phone",
    "search",
    "textarea",
    "time",
    "username",
    "zip",
}
FORM_FIELD_PURPOSES = {
    "address",
    "card",
    "city",
    "code",
    "country",
    "date",
    "email",
    "name",
    "otp",
    "password",
    "phone",
    "time",
    "username",
    "zip",
}
TASK_CONTAINER_HINTS = {
    "action sheet",
    "alert",
    "confirmation",
    "dialog",
    "edit menu",
    "menu",
    "modal",
    "popover",
    "sheet",
}
PICKER_CONTEXT_HINTS = {
    "calendar",
    "date picker",
    "datepicker",
    "picker",
    "time picker",
}
COGNITIVE_WALKTHROUGH_SOURCE = {
    "title": "Cognitive walkthroughs: a method for theory-based evaluation of user interfaces",
    "authors": "Polson, Lewis, Rieman, and Wharton",
    "year": 1992,
    "doi": "10.1016/0020-7373(92)90039-N",
}
GENERIC_COMPLETION_LABELS = {
    "apply",
    "continue",
    "done",
    "finish",
    "next",
    "start",
    "submit",
}
TASK_CONTEXT_KEYWORDS = {
    "account",
    "booking",
    "checkout",
    "create",
    "delivery",
    "filter",
    "login",
    "log in",
    "payment",
    "profile",
    "register",
    "reservation",
    "search",
    "settings",
    "shipping",
    "sign in",
    "sign up",
    "signup",
}
WEAK_CONTEXT_LABELS = {
    "description",
    "description...",
    "heading",
    "label",
    "subtitle",
    "title",
}


@dataclass(frozen=True)
class FieldCandidate:
    node: NormalizedNode
    purpose: str
    label: str


@dataclass(frozen=True)
class CompletionAction:
    node: NormalizedNode
    label: str
    is_generic: bool


def _label_matches(label: str, options: Iterable[str]) -> bool:
    return any(label == option or (len(option) > 3 and option in label) for option in options)


def _node_search_text(node: NormalizedNode) -> str:
    return normalized_text(
        " ".join(
            str(value or "")
            for value in (
                node.name,
                node.frame_name,
                node.path,
                node.characters,
            )
        )
    )


def _node_own_text(node: NormalizedNode) -> str:
    return normalized_text(" ".join(str(value or "") for value in (node.name, node.frame_name)))


def _text_labels_in_subtree(ctx: AuditContext, node: NormalizedNode) -> list[str]:
    labels = []
    if node.type == "TEXT" and ctx.has_text(node):
        labels.append(normalized_text(node.characters))
    labels.extend(
        normalized_text(text.characters)
        for text in ctx.text_nodes_in_subtree(node)
        if ctx.has_text(text)
    )
    return [label for label in labels if label]


def _nearest_task_container(ctx: AuditContext, node: NormalizedNode) -> NormalizedNode | None:
    viewport = ctx.mobile_viewport_for(node)
    viewport_area = ctx.bbox_area(viewport) if viewport else 0.0
    fallback: NormalizedNode | None = ctx.get_node(node.parent_id)

    for ancestor in ctx.iter_ancestors(node):
        name = _node_own_text(ancestor)
        labels = _text_labels_in_subtree(ctx, ancestor)
        if any(hint in name for hint in TASK_CONTAINER_HINTS):
            return ancestor
        if 2 <= len(labels) <= 12:
            if viewport_area <= 0 or ctx.bbox_area(ancestor) <= viewport_area * 0.78:
                return ancestor
        if viewport is not None and ancestor.id == viewport.id:
            break

    return fallback


def _has_nearby_recovery_or_confirmation(
    ctx: AuditContext,
    node: NormalizedNode,
    container: NormalizedNode,
) -> tuple[bool, list[str]]:
    labels = [
        label
        for label in _text_labels_in_subtree(ctx, container)
        if label != normalized_text(node.characters)
    ]
    combined = " ".join(labels)
    has_recovery = any(_label_matches(label, RECOVERY_LABELS) for label in labels)
    has_confirmation_context = any(phrase in combined for phrase in CONFIRMATION_CONTEXT_LABELS)
    return has_recovery or has_confirmation_context, labels


def _field_purpose(text: str) -> str | None:
    for purpose in sorted(FORM_FIELD_PURPOSES, key=len, reverse=True):
        if purpose in text:
            return purpose
    if "search" in text:
        return "search"
    return None


def _is_input_container(ctx: AuditContext, node: NormalizedNode) -> bool:
    if node.type not in CONTAINER_TYPES:
        return False
    width = ctx.bbox_width(node)
    height = ctx.bbox_height(node)
    if width < 120 or height < 28 or height > 96:
        return False
    if width / max(height, 1.0) < 2.0:
        return False
    text = _node_search_text(node)
    return any(keyword in text for keyword in FORM_FIELD_KEYWORDS)


def _field_candidate(ctx: AuditContext, node: NormalizedNode) -> FieldCandidate | None:
    if not _is_input_container(ctx, node):
        return None
    labels = _text_labels_in_subtree(ctx, node)
    search_text = " ".join([_node_search_text(node), *labels])
    purpose = _field_purpose(search_text)
    if purpose is None:
        return None
    label = next((label for label in labels if label), node.name)
    return FieldCandidate(node=node, purpose=purpose, label=label)


def _field_key(ctx: AuditContext, node: NormalizedNode) -> tuple[int, int, int, int]:
    box = ctx.render_or_bbox(node) or {}
    return (
        round(float(box.get("x") or 0)),
        round(float(box.get("y") or 0)),
        round(float(box.get("width") or 0)),
        round(float(box.get("height") or 0)),
    )


def _dedupe_field_candidates(ctx: AuditContext, candidates: list[FieldCandidate]) -> list[FieldCandidate]:
    by_box: dict[tuple[int, int, int, int], FieldCandidate] = {}
    for candidate in candidates:
        key = _field_key(ctx, candidate.node)
        existing = by_box.get(key)
        if existing is None or ctx.subtree_count(candidate.node) > ctx.subtree_count(existing.node):
            by_box[key] = candidate
    return list(by_box.values())


def _is_picker_context(node: NormalizedNode | None) -> bool:
    if node is None:
        return False
    text = _node_search_text(node)
    return any(hint in text for hint in PICKER_CONTEXT_HINTS)


def _is_completion_action_label(label: str) -> bool:
    return _label_matches(label, TASK_COMPLETION_LABELS)


def _is_generic_completion_action(label: str) -> bool:
    return _label_matches(label, GENERIC_COMPLETION_LABELS)


def _is_control_label_node(ctx: AuditContext, node: NormalizedNode) -> bool:
    viewport = ctx.mobile_viewport_for(node)
    viewport_area = ctx.bbox_area(viewport) if viewport else 0.0
    for ancestor in ctx.iter_ancestors(node):
        if viewport is not None and ancestor.id == viewport.id:
            break
        if not ctx.is_control_like(ancestor):
            continue
        if viewport_area > 0 and ctx.bbox_area(ancestor) > viewport_area * 0.35:
            continue
        if ctx.bbox_width(ancestor) < 44 or ctx.bbox_height(ancestor) < 24:
            continue
        return True
    return False


def _is_form_field_label_node(ctx: AuditContext, node: NormalizedNode) -> bool:
    return any(_field_candidate(ctx, ancestor) is not None for ancestor in ctx.iter_ancestors(node))


def _viewport_text_nodes(ctx: AuditContext, viewport: NormalizedNode | None) -> list[NormalizedNode]:
    if viewport is None:
        return [
            node
            for node in ctx.client_visible_nodes
            if node.type == "TEXT" and ctx.has_text(node)
        ]
    return [
        node
        for node in ctx.text_nodes_in_subtree(viewport)
        if ctx.has_text(node)
    ]


def _completion_actions_in_viewport(
    ctx: AuditContext,
    viewport: NormalizedNode | None,
) -> list[CompletionAction]:
    actions: list[CompletionAction] = []
    seen: set[str] = set()
    for node in _viewport_text_nodes(ctx, viewport):
        label = normalized_text(node.characters)
        if not _is_completion_action_label(label):
            continue
        if _is_form_field_label_node(ctx, node):
            continue
        if not _is_control_label_node(ctx, node):
            continue
        if node.id in seen:
            continue
        seen.add(node.id)
        actions.append(
            CompletionAction(
                node=node,
                label=label,
                is_generic=_is_generic_completion_action(label),
            )
        )
    return actions


def _has_visible_task_context(
    ctx: AuditContext,
    viewport: NormalizedNode | None,
    fields: list[FieldCandidate],
    actions: list[CompletionAction],
) -> bool:
    action_labels = {action.label for action in actions}
    field_labels = {candidate.label for candidate in fields}
    field_purposes = {candidate.purpose for candidate in fields}
    context_parts: list[str] = []
    if viewport is not None:
        context_parts.append(_node_own_text(viewport))
    for node in _viewport_text_nodes(ctx, viewport):
        label = normalized_text(node.characters)
        if not label or label in action_labels or label in field_labels or label in WEAK_CONTEXT_LABELS:
            continue
        context_parts.append(label)

    context_text = " ".join(context_parts)
    if any(keyword in context_text for keyword in TASK_CONTEXT_KEYWORDS):
        return True
    return any(purpose in context_text for purpose in field_purposes if purpose not in {"name", "code"})


def _walkthrough_evidence(question: str) -> dict[str, object]:
    return {
        "validation_method": "cognitive_walkthrough_static_figma_gate",
        "validation_source": COGNITIVE_WALKTHROUGH_SOURCE,
        "cognitive_walkthrough_question": question,
    }


def _detect_form_without_completion_action(ctx: AuditContext) -> list[AuditIssue]:
    candidates_by_viewport: dict[str, list[FieldCandidate]] = defaultdict(list)
    viewport_by_id: dict[str, NormalizedNode | None] = {}

    for node in ctx.client_visible_nodes:
        candidate = _field_candidate(ctx, node)
        if candidate is None:
            continue
        viewport = ctx.mobile_viewport_for(node)
        viewport_id = viewport.id if viewport else "__visible_fallback__"
        viewport_by_id[viewport_id] = viewport
        candidates_by_viewport[viewport_id].append(candidate)

    ranked: list[tuple[float, AuditIssue]] = []
    for viewport_id, raw_candidates in candidates_by_viewport.items():
        candidates = _dedupe_field_candidates(ctx, raw_candidates)
        meaningful_fields = [
            candidate for candidate in candidates if candidate.purpose != "search"
        ]
        distinct_purposes = {candidate.purpose for candidate in meaningful_fields}
        if len(meaningful_fields) < 2 or len(distinct_purposes) < 2:
            continue

        viewport = viewport_by_id.get(viewport_id)
        if distinct_purposes.issubset({"date", "time"}) or _is_picker_context(viewport):
            continue

        completion_actions = _completion_actions_in_viewport(ctx, viewport)
        action_labels = [action.label for action in completion_actions]
        if completion_actions:
            generic_actions = [action for action in completion_actions if action.is_generic]
            if len(generic_actions) != len(completion_actions):
                continue
            if _has_visible_task_context(ctx, viewport, meaningful_fields, completion_actions):
                continue

            target_action = sorted(
                completion_actions,
                key=lambda action: ctx.visual_priority(action.node),
                reverse=True,
            )[0]
            ranked.append(
                (
                    ctx.visual_priority(target_action.node),
                    make_issue(
                        ctx=ctx,
                        issue_id=f"draft-ambiguous-completion-action-{uuid4().hex}",
                        axis="task_execution",
                        criterion="task_execution",
                        severity=Severity.MEDIUM,
                        message="A visible task completion action does not make its outcome clear.",
                        node=target_action.node,
                        detector_id="ambiguous_completion_action",
                        confidence="medium",
                        confidence_reason=(
                            "A Cognitive Walkthrough check found a generic completion action in a multi-field task "
                            "without visible context that explains the result of tapping it."
                        ),
                        evidence={
                            **_walkthrough_evidence(
                                "Will users associate the visible completion action with the effect they are trying to achieve?"
                            ),
                            "field_count": len(meaningful_fields),
                            "field_purposes": sorted(distinct_purposes),
                            "field_labels": [candidate.label for candidate in meaningful_fields[:8]],
                            "visible_completion_actions": action_labels,
                            "ambiguous_action_label": target_action.label,
                            "viewport_id": viewport_id,
                            "limitations": [
                                "Static Figma data cannot prove runtime routing, hidden explanatory copy, or keyboard submission behavior.",
                            ],
                        },
                    ),
                )
            )
            continue

        target = sorted(
            meaningful_fields,
            key=lambda candidate: ctx.visual_priority(candidate.node),
            reverse=True,
        )[0]
        issue_node = target.node
        ranked.append(
            (
                ctx.visual_priority(issue_node),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-form-no-completion-action-{uuid4().hex}",
                    axis="task_execution",
                    criterion="task_execution",
                    severity=Severity.MEDIUM,
                    message="A visible multi-field task has no clear completion action.",
                    node=issue_node,
                    detector_id="form_without_completion_action",
                    confidence="medium",
                    confidence_reason=(
                        "The mobile viewport contains multiple visible input fields with distinct purposes, "
                        "but no visible save, submit, continue, sign-in, search, or equivalent completion action."
                    ),
                    evidence={
                        **_walkthrough_evidence(
                            "Will users notice that the correct action for completing the task is available?"
                        ),
                        "field_count": len(meaningful_fields),
                        "field_purposes": sorted(distinct_purposes),
                        "field_labels": [candidate.label for candidate in meaningful_fields[:8]],
                        "visible_completion_actions": action_labels,
                        "viewport_id": viewport_id,
                        "limitations": [
                            "Static Figma data cannot prove hidden keyboard submit behavior or runtime flow.",
                        ],
                    },
                ),
            )
        )

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [issue for _, issue in ranked]


def detect_destructive_action_without_recovery(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """
    Detect visible task-execution blockers that can be inferred from static UI.

    The rules are conservative: they require visible mobile UI evidence and avoid
    scoring backend behavior, hidden states, or product strategy guesses.
    """
    ctx = AuditContext(normalized_file)
    ranked: list[tuple[float, AuditIssue]] = []
    seen: set[str] = set()

    for node in ctx.client_visible_nodes:
        if node.type != "TEXT" or not ctx.has_text(node):
            continue

        label = normalized_text(node.characters)
        if label not in DESTRUCTIVE_LABELS:
            continue

        if not any(ctx.is_control_like(ancestor) for ancestor in [node, *ctx.iter_ancestors(node)]):
            continue

        container = _nearest_task_container(ctx, node)
        if container is None:
            continue

        has_safety, nearby_labels = _has_nearby_recovery_or_confirmation(ctx, node, container)
        if has_safety:
            continue

        if node.id in seen:
            continue
        seen.add(node.id)

        ranked.append(
            (
                ctx.visual_priority(node),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-destructive-no-recovery-{uuid4().hex}",
                    axis="task_execution",
                    criterion="task_execution",
                    severity=Severity.MEDIUM,
                    message="A destructive action is visible without a nearby recovery or confirmation cue.",
                    node=node,
                    detector_id="destructive_action_without_recovery",
                    confidence="medium",
                    confidence_reason="The visible destructive control lacks a recovery or confirmation cue in its nearest task container.",
                    evidence={
                        **_walkthrough_evidence(
                            "After choosing a risky action, will users see confirmation, cancellation, or recovery feedback?"
                        ),
                        "destructive_label": label,
                        "task_container_id": container.id,
                        "task_container_name": container.name,
                        "nearby_text": " ".join(nearby_labels)[:240],
                        "limitations": [
                            "Static Figma data cannot prove modal flow or runtime confirmation behavior.",
                        ],
                    },
                ),
            )
        )

    ranked.sort(key=lambda item: item[0], reverse=True)
    issues = [issue for _, issue in ranked]
    issues.extend(_detect_form_without_completion_action(ctx))
    issues.sort(
        key=lambda issue: float(issue.evidence.get("top_layer_priority") or 0),
        reverse=True,
    )
    return issues[:DETECTION_MAX_ISSUES_PER_CHECK]
