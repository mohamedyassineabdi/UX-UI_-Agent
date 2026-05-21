from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from figma_audit.audit.context import AuditContext
from figma_audit.criteria_matrix import checks_by_axis
from figma_audit.models.detection import DetectionResult
from figma_audit.models.issue import AuditIssue
from figma_audit.models.normalized_models import NormalizedFigmaFile


CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1}
QUALITY_ORDER = {"strong": 3, "moderate": 2, "weak": 1, "none": 0}

REAL_SCREENSHOT_TYPES = {"real_page_geometry_screenshot"}
FIGMA_RENDER_TYPES = {"annotated_screenshot"}
FALLBACK_PREVIEW_TYPES = {"node_preview_locator"}

DETECTOR_EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    "low_text_contrast": (
        "contrast_ratio",
        "required_ratio",
        "resolved_foreground_rgb",
        "resolved_background_rgb",
    ),
    "small_touch_target": (
        "target_width",
        "target_height",
        "recommended_min_side",
    ),
    "crowded_touch_target": (
        "nearest_control_gap",
        "minimum_recommended_gap",
    ),
    "small_text_readability": (
        "font_size",
        "minimum_recommended_font_size",
    ),
    "icon_only_unlabeled_control": (
        "icon_name",
        "visible_control_text",
    ),
    "destructive_action_without_recovery": (
        "destructive_label",
        "nearby_text",
        "task_container_id",
    ),
    "ambiguous_completion_action": (
        "field_count",
        "field_purposes",
        "visible_completion_actions",
    ),
    "form_without_completion_action": (
        "field_count",
        "field_purposes",
        "visible_completion_actions",
    ),
    "generic_navigation_label": (
        "navigation_labels",
        "visual_search_checks",
        "navigation_ancestor_id",
    ),
    "component_style_outlier": (
        "family_key",
        "dominant_value",
        "outlier_value",
        "value_distribution",
    ),
    "flat_visual_hierarchy": (
        "visual_brand_checks",
        "largest_to_median_ratio",
        "imbalance_ratio",
    ),
    "placeholder_or_generic_copy": (
        "text_sample",
        "matched_text",
        "plain_language_checks",
    ),
}


def _artifact_type_counts(issue: AuditIssue) -> Counter[str]:
    counts: Counter[str] = Counter()
    for artifact in issue.visual_evidence:
        counts[str(artifact.type)] += 1
    return counts


def _visual_evidence_score(issue: AuditIssue) -> tuple[int, str]:
    artifact_counts = _artifact_type_counts(issue)
    if any(artifact_type in artifact_counts for artifact_type in REAL_SCREENSHOT_TYPES):
        return 3, "real_client_screenshot"
    if any(artifact_type in artifact_counts for artifact_type in FIGMA_RENDER_TYPES):
        return 2, "figma_rendered_annotation"
    if any(artifact_type in artifact_counts for artifact_type in FALLBACK_PREVIEW_TYPES):
        return 1, "geometry_preview_fallback"
    return 0, "no_visual_evidence"


def _issue_node_has_geometry(
    issue: AuditIssue,
    nodes_by_id: dict[str, Any],
) -> bool:
    node_id = issue.location.node_id
    if not node_id:
        return False
    node = nodes_by_id.get(node_id)
    if node is None:
        return False
    return bool(node.absolute_render_bounds or node.absolute_bounding_box)


def _detector_key_coverage(detector_id: str, evidence: dict[str, Any]) -> tuple[int, list[str]]:
    expected_keys = DETECTOR_EVIDENCE_KEYS.get(detector_id, ())
    if not expected_keys:
        return 0, []
    present = [
        key
        for key in expected_keys
        if evidence.get(key) not in (None, "", [], {})
    ]
    if len(present) >= 3:
        return 2, present
    if present:
        return 1, present
    return 0, present


def _quality_from_score(score: int) -> str:
    if score >= 7:
        return "strong"
    if score >= 4:
        return "moderate"
    return "weak"


def _issue_quality(
    issue: AuditIssue,
    *,
    nodes_by_id: dict[str, Any],
) -> dict[str, Any]:
    evidence = issue.evidence
    detector_id = str(evidence.get("detector_id") or "")
    confidence = str(evidence.get("confidence") or "low").lower()
    confidence_score = CONFIDENCE_SCORE.get(confidence, 1)
    visual_score, visual_mode = _visual_evidence_score(issue)
    geometry_available = _issue_node_has_geometry(issue, nodes_by_id)
    detector_score, present_keys = _detector_key_coverage(detector_id, evidence)

    score = confidence_score + visual_score + detector_score
    if geometry_available:
        score += 1

    if str(evidence.get("client_visibility") or ""):
        score = min(score, 2)

    quality = _quality_from_score(score)
    summary = {
        "quality": quality,
        "score": score,
        "confidence": confidence,
        "visual_evidence": visual_mode,
        "geometry_available": geometry_available,
        "detector_evidence_keys": present_keys,
        "rule": (
            "Strong findings have measured detector evidence plus real or rendered visual evidence. "
            "Moderate findings are useful static-Figma signals. Weak findings should be treated as prompts for manual review."
        ),
    }
    evidence["evidence_quality"] = summary
    return summary


def _axis_evidence_quality(
    issues: list[AuditIssue],
) -> dict[str, int]:
    counts = {"strong": 0, "moderate": 0, "weak": 0, "none": 0}
    if not issues:
        counts["none"] = 1
        return counts
    for issue in issues:
        evidence_quality = issue.evidence.get("evidence_quality")
        quality = "weak"
        if isinstance(evidence_quality, dict):
            quality = str(evidence_quality.get("quality") or quality)
        if quality not in counts:
            quality = "weak"
        counts[quality] += 1
    return counts


def _best_quality(counts: dict[str, int]) -> str:
    for quality in ("strong", "moderate", "weak"):
        if counts.get(quality, 0) > 0:
            return quality
    return "none"


def _check_id(axis_id: str, check_name: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in check_name)
    slug = "_".join(part for part in slug.split("_") if part)
    return f"{axis_id}.{slug}"


def _issue_detector_id(issue: AuditIssue) -> str:
    return str(issue.evidence.get("detector_id") or "")


def _issue_quality_label(issue: AuditIssue) -> str:
    quality = issue.evidence.get("evidence_quality")
    if isinstance(quality, dict):
        value = str(quality.get("quality") or "weak")
        if value in QUALITY_ORDER:
            return value
    return "weak"


def _best_issue_quality(issues: list[AuditIssue]) -> str:
    if not issues:
        return "none"
    return max((_issue_quality_label(issue) for issue in issues), key=lambda item: QUALITY_ORDER.get(item, 0))


def _check_status(
    *,
    active: bool,
    analysis_method: str,
    detector_ids: tuple[str, ...],
    matching_issues: list[AuditIssue],
) -> str:
    if not active:
        return "not_evaluated"
    if matching_issues:
        return "needs_improvement" if analysis_method == "rule" else "needs_review_with_evidence"
    if not detector_ids or analysis_method != "rule":
        return "manual_review_required"
    return "no_static_issue_detected"


def _check_evaluation_note(status: str) -> str:
    notes = {
        "needs_improvement": "One or more visible static findings matched this check.",
        "needs_review_with_evidence": "Static evidence suggests this check needs expert review before being treated as final.",
        "no_static_issue_detected": "The automated static detector did not find a visible issue for this check; this is not a formal compliance guarantee.",
        "manual_review_required": "This check cannot be proven accurately from static Figma JSON alone.",
        "not_evaluated": "This check is kept in the framework but is currently disabled for automated runs.",
    }
    return notes.get(status, "Review the visible design and supporting evidence.")


def _criteria_evaluations(issues: list[AuditIssue]) -> list[dict[str, Any]]:
    issues_by_detector: dict[str, list[AuditIssue]] = defaultdict(list)
    for issue in issues:
        detector_id = _issue_detector_id(issue)
        if detector_id:
            issues_by_detector[detector_id].append(issue)

    evaluations: list[dict[str, Any]] = []
    for axis_id, checks in checks_by_axis().items():
        for check in checks:
            matching_issues: list[AuditIssue] = []
            for detector_id in check.detector_ids:
                matching_issues.extend(issues_by_detector.get(detector_id, []))

            unique_matching: dict[str, AuditIssue] = {
                issue.id: issue for issue in matching_issues
            }
            matched = list(unique_matching.values())
            status = _check_status(
                active=check.active,
                analysis_method=check.analysis_method,
                detector_ids=check.detector_ids,
                matching_issues=matched,
            )
            evaluations.append(
                {
                    "check_id": _check_id(axis_id, check.name),
                    "criterion_id": axis_id,
                    "name": check.name,
                    "priority": check.priority,
                    "visible_rule": check.visible_rule,
                    "analysis_method": check.analysis_method,
                    "active": check.active,
                    "detector_ids": list(check.detector_ids),
                    "status": status,
                    "applicability": (
                        "visible_static_evidence_found"
                        if matched
                        else "inactive_check"
                        if not check.active
                        else "not_determinable_from_static_figma"
                        if not check.detector_ids or check.analysis_method != "rule"
                        else "no_matching_static_evidence"
                    ),
                    "matched_issue_count": len(matched),
                    "matched_issue_ids": [issue.id for issue in matched],
                    "best_evidence_quality": _best_issue_quality(matched),
                    "note": _check_evaluation_note(status),
                }
            )
    return evaluations


def attach_evidence_quality(
    *,
    normalized_file: NormalizedFigmaFile,
    detection_result: DetectionResult,
    source_url: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """
    Add evidence-quality metadata to draft issues and return an audit coverage summary.

    This makes the audit safer for any Figma link: every criterion declares what
    was measured, what was visually supported, and what still needs human review.
    """
    ctx = AuditContext(normalized_file)
    nodes_by_id = ctx.nodes_by_id
    issue_quality = [
        _issue_quality(issue, nodes_by_id=nodes_by_id)
        for issue in detection_result.draft_issues
    ]

    issues_by_axis: dict[str, list[AuditIssue]] = defaultdict(list)
    for issue in detection_result.draft_issues:
        axis = issue.criterion or issue.axis or "__unknown__"
        issues_by_axis[axis].append(issue)

    status_by_id = detection_result.status_by_id()
    axis_rows: list[dict[str, Any]] = []
    for axis_id, checks in checks_by_axis().items():
        active_checks = [check for check in checks if check.active]
        automated_checks = [check for check in active_checks if check.detector_ids]
        human_review_checks = [
            check
            for check in active_checks
            if not check.detector_ids or check.analysis_method != "rule"
        ]
        axis_issues = issues_by_axis.get(axis_id, [])
        quality_counts = _axis_evidence_quality(axis_issues)
        status = status_by_id.get(axis_id)
        detected = bool(status and status.exists)
        if detected:
            validation_status = "detected_with_evidence"
        elif automated_checks:
            validation_status = "no_static_issue_detected"
        else:
            validation_status = "manual_review_required"

        axis_rows.append(
            {
                "criterion_id": axis_id,
                "active_checks": len(active_checks),
                "automated_checks": len(automated_checks),
                "human_review_checks": len(human_review_checks),
                "detected_issue_count": len(axis_issues),
                "validation_status": validation_status,
                "best_evidence_quality": _best_quality(quality_counts),
                "evidence_quality_counts": quality_counts,
                "detector_ids": sorted(status.detector_ids) if status else [],
                "human_review_note": (
                    "Some checks cannot be proven from static Figma JSON alone; inspect prototype behavior, backend states, and business context."
                    if human_review_checks
                    else None
                ),
            }
        )

    artifact_counts: Counter[str] = Counter()
    for issue in detection_result.draft_issues:
        artifact_counts.update(_artifact_type_counts(issue))

    text_node_count = sum(
        1 for node in normalized_file.nodes if node.type == "TEXT" and bool(node.characters)
    )
    geometry_node_count = sum(
        1
        for node in normalized_file.nodes
        if bool(node.absolute_bounding_box or node.absolute_render_bounds)
    )
    issue_quality_counts = Counter(
        str(item.get("quality") or "weak") for item in issue_quality
    )
    criteria_evaluations = _criteria_evaluations(detection_result.draft_issues)
    evaluation_status_counts = Counter(
        str(item["status"]) for item in criteria_evaluations
    )

    return {
        "scope": {
            "source_url": source_url,
            "source_scope": "selected_node" if node_id else "full_file",
            "file_key": normalized_file.file_key,
            "file_name": normalized_file.file_name,
            "node_id": node_id,
            "pages": len(normalized_file.pages),
            "frames": len(normalized_file.frames),
            "nodes": len(normalized_file.nodes),
            "text_nodes": text_node_count,
            "geometry_nodes": geometry_node_count,
            "mobile_viewport_candidates": len(ctx.mobile_viewport_roots),
        },
        "evidence_summary": {
            "issue_count": len(detection_result.draft_issues),
            "strong_issue_count": issue_quality_counts.get("strong", 0),
            "moderate_issue_count": issue_quality_counts.get("moderate", 0),
            "weak_issue_count": issue_quality_counts.get("weak", 0),
            "real_client_screenshot_count": sum(
                artifact_counts.get(kind, 0) for kind in REAL_SCREENSHOT_TYPES
            ),
            "figma_rendered_annotation_count": sum(
                artifact_counts.get(kind, 0) for kind in FIGMA_RENDER_TYPES
            ),
            "fallback_preview_count": sum(
                artifact_counts.get(kind, 0) for kind in FALLBACK_PREVIEW_TYPES
            ),
            "artifact_type_counts": dict(sorted(artifact_counts.items())),
        },
        "criteria_coverage": axis_rows,
        "criteria_evaluations": criteria_evaluations,
        "evaluation_summary": {
            "total_checks": len(criteria_evaluations),
            "active_checks": sum(1 for item in criteria_evaluations if item["active"]),
            "needs_improvement": evaluation_status_counts.get("needs_improvement", 0),
            "needs_review_with_evidence": evaluation_status_counts.get("needs_review_with_evidence", 0),
            "no_static_issue_detected": evaluation_status_counts.get("no_static_issue_detected", 0),
            "manual_review_required": evaluation_status_counts.get("manual_review_required", 0),
            "not_evaluated": evaluation_status_counts.get("not_evaluated", 0),
            "status_counts": dict(sorted(evaluation_status_counts.items())),
        },
        "accuracy_contract": [
            "Accept any valid Figma file/design/proto/board URL, with or without node-id.",
            "Prefer selected-node audits for focused flows and full-file audits for broad IA/design-system review.",
            "Use measured Figma geometry, text, color, layout, and component data before making a finding.",
            "Promote findings to strong only when detector evidence is paired with real or rendered visual evidence.",
            "Keep business, market, runtime, hidden-state, and accessibility-conformance claims as human review unless visible evidence supports them.",
        ],
    }
