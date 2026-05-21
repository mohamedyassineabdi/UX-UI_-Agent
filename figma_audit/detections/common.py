from __future__ import annotations

import re

from figma_audit.audit.context import AuditContext
from figma_audit.models.issue import AuditIssue, IssueLocation, Severity
from figma_audit.models.normalized_models import NormalizedNode

NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _visible_criterion_name(detector_id: str, evidence: dict[str, object] | None) -> str:
    evidence = evidence or {}
    subdetector = str(
        evidence.get("accessibility_check")
        or evidence.get("task_execution_subdetector")
        or evidence.get("flow_subdetector")
        or evidence.get("ui_consistency_subdetector")
        or evidence.get("visual_brand_subdetector")
        or evidence.get("content_microcopy_subdetector")
        or ""
    )
    if detector_id == "low_text_contrast":
        return "Text has enough visible contrast"
    if detector_id == "small_touch_target":
        return "Buttons and icons are large enough to tap"
    if detector_id == "crowded_touch_target":
        if subdetector == "edge_safe_touch_area":
            return "Important controls are not too close to screen edges"
        return "Interactive elements have enough spacing"
    if detector_id == "small_text_readability":
        if subdetector == "dense_line_spacing":
            return "Dense text blocks have readable spacing"
        return "Important mobile text is large enough to read"
    if detector_id == "icon_only_unlabeled_control":
        return "Icon-only controls have clear visible meaning"
    if detector_id == "ambiguous_completion_action":
        return "Action labels explain the result"
    if detector_id == "form_without_completion_action":
        return "Forms show a clear completion path"
    if detector_id == "destructive_action_without_recovery":
        return "Risky actions show recovery or confirmation"
    if detector_id == "generic_navigation_label":
        if subdetector == "missing_destination_labels_in_primary_navigation":
            return "Primary navigation labels or icons are clear"
        return "Navigation destinations are specific and distinct"
    if detector_id == "component_style_outlier":
        if subdetector == "lexical_label_outlier":
            return "Repeated actions use consistent wording"
        field = str(evidence.get("field") or "")
        if field in {"padding_left", "padding_right", "padding_top", "padding_bottom", "item_spacing"}:
            return "Repeated components keep consistent spacing"
        if field == "height":
            return "Repeated controls keep consistent sizing"
        if field == "corner_radius":
            return "Repeated components keep consistent shape"
        return "Repeated components behave consistently"
    if detector_id == "flat_visual_hierarchy":
        if subdetector == "bad_foreground_panel_position":
            return "The main panel is positioned with clear visual hierarchy"
        if subdetector == "visible_component_workspace":
            return "Only user-facing screen content is visible in the reviewed frame"
        if subdetector == "flat_text_hierarchy":
            return "Headings and body text show clear hierarchy"
        if subdetector == "unbalanced_visual_weight":
            return "The screen has a clear first-glance focal path"
        return "Visual hierarchy is clear"
    if detector_id == "placeholder_or_generic_copy":
        if subdetector == "placeholder_text":
            return "Placeholder text is not visible to users"
        if subdetector == "generic_cta_without_context":
            return "CTA text explains the action outcome"
        if subdetector == "truncated_or_clipped_copy":
            return "Important content is not clipped or truncated"
        if subdetector == "dense_plain_language_risk":
            return "Visible copy is easy to scan"
        if subdetector == "vague_value_copy":
            return "Visible copy explains the screen purpose"
        return "Content copy is clear and specific"
    return "Visible issue can be verified in the screenshot"


def normalized_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def canonical_text(value: str | None) -> str:
    return NON_ALNUM_RE.sub("", normalized_text(value))


def text_sample(node: NormalizedNode, limit: int = 120) -> str:
    return (node.characters or "").strip().replace("\n", " ")[:limit]


def make_issue(
    *,
    ctx: AuditContext | None = None,
    issue_id: str,
    axis: str,
    criterion: str,
    severity: Severity,
    message: str,
    node: NormalizedNode,
    detector_id: str,
    confidence: str,
    confidence_reason: str,
    evidence: dict[str, object] | None = None,
) -> AuditIssue:
    payload: dict[str, object] = {
        "detector_id": detector_id,
        "criterion_name": _visible_criterion_name(detector_id, evidence),
        "binary_exists": True,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
    }
    if ctx is not None:
        payload.update(ctx.visual_scope_evidence(node))
    if evidence:
        payload.update(evidence)

    return AuditIssue(
        id=issue_id,
        axis=axis,
        criterion=criterion,
        severity=severity,
        message=message,
        location=IssueLocation(
            page_name=node.page_name,
            frame_name=node.frame_name,
            node_id=node.id,
            node_name=node.name,
            path=node.path,
        ),
        evidence=payload,
    )


def text_nodes(ctx: AuditContext) -> list[NormalizedNode]:
    return [node for node in ctx.client_visible_nodes if node.type == "TEXT" and ctx.has_text(node)]


def descendant_text(ctx: AuditContext, node: NormalizedNode) -> str:
    texts = []
    if node.type == "TEXT" and ctx.has_text(node):
        texts.append(node.characters or "")
    texts.extend(text.characters or "" for text in ctx.text_nodes_in_subtree(node))
    return normalized_text(" ".join(texts))


def bbox_dimension(node: NormalizedNode, key: str) -> float:
    box = node.absolute_bounding_box or {}
    value = box.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0
