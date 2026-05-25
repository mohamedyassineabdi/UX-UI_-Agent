from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections import Counter
from html import escape
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ModuleNotFoundError:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

from figma_audit.ai_reviewer import review_issues_with_ollama
from figma_audit.criteria_matrix import checks_by_axis
from figma_audit.criteria_catalog import load_criteria_catalog
from figma_audit.config import OLLAMA_AI_REVIEW, OLLAMA_REPORT_POLISH
from figma_audit.models.criteria import UxUiCriterion
from figma_audit.models.detection import DetectionResult
from figma_audit.report_polisher import ReportPolishError, polish_report_copy_with_ollama
from figma_audit.utils.io import load_json


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEVERITY_WEIGHT = {"high": 1.35, "medium": 0.9, "low": 0.55}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.78, "low": 0.58}
ROOT_SCREEN_TYPES = {"FRAME", "INSTANCE", "SECTION"}
VISUAL_SCOPE_WEIGHT = {
    "mobile_client_view": 1.08,
    "visible_fallback": 0.88,
}
HEURISTIC_REVIEW_SOURCE = {
    "name": "Interaction Design Foundation - User Interface Design Guidelines: 10 Rules of Thumb",
    "url": "https://www.interaction-design.org/literature/article/user-interface-design-guidelines-10-rules-of-thumb",
    "basis": "Nielsen and Molich usability heuristics applied to modern UI review.",
}
DETECTOR_HEURISTIC_WEIGHTS = {
    "destructive_action_without_recovery": {
        "heuristics": ["User control and freedom", "Error prevention", "Recover from errors"],
        "weight": 1.24,
    },
    "low_text_contrast": {
        "heuristics": ["Visibility of system status", "Recognition rather than recall", "Aesthetic and minimalist design"],
        "weight": 1.18,
    },
    "small_touch_target": {
        "heuristics": ["Error prevention", "User control and freedom", "Recognition rather than recall"],
        "weight": 1.16,
    },
    "crowded_touch_target": {
        "heuristics": ["Error prevention", "User control and freedom"],
        "weight": 1.12,
    },
    "small_text_readability": {
        "heuristics": ["Recognition rather than recall", "Aesthetic and minimalist design"],
        "weight": 1.12,
    },
    "icon_only_unlabeled_control": {
        "heuristics": ["Recognition rather than recall", "Match between system and the real world"],
        "weight": 1.08,
    },
    "generic_navigation_label": {
        "heuristics": ["Recognition rather than recall", "Match between system and the real world"],
        "weight": 1.12,
    },
    "placeholder_or_generic_copy": {
        "heuristics": ["Match between system and the real world", "Recognition rather than recall"],
        "weight": 1.1,
    },
    "component_style_outlier": {
        "heuristics": ["Consistency and standards"],
        "weight": 1.0,
    },
    "flat_visual_hierarchy": {
        "heuristics": ["Aesthetic and minimalist design", "Recognition rather than recall"],
        "weight": 0.86,
    },
}
DETECTOR_PRIORITY = {
    "low_text_contrast": 0,
    "small_touch_target": 1,
    "crowded_touch_target": 2,
    "small_text_readability": 3,
    "icon_only_unlabeled_control": 4,
    "destructive_action_without_recovery": 5,
    "component_style_outlier": 6,
    "generic_navigation_label": 7,
    "placeholder_or_generic_copy": 8,
    "flat_visual_hierarchy": 9,
}

MOJIBAKE_REPLACEMENTS = {
    "âœ¨": "",
    "âœï¸": "",
    "âœ": "",
    "âœ": "",
    "ï¸": "",
    "ï¸": "",
    "Â·": "-",
    "â€™": "'",
    "â€œ": '"',
    "â€": '"',
    "â€”": "-",
    "â€“": "-",
    "Ã©": "e",
    "Ã¨": "e",
    "Ãª": "e",
    "Ã ": "a",
    "Ã¢": "a",
    "Ã®": "i",
    "Ã´": "o",
    "Ã»": "u",
    "Ã§": "c",
}

DETECTOR_RECOMMENDATIONS = {
    "low_text_contrast": (
        "Raise text/background contrast to at least 4.5:1 for normal text, "
        "or 3:1 for large text. In Figma, update the shared color style or "
        "component variant so the correction lands everywhere this pattern appears."
    ),
    "small_touch_target": (
        "Increase the tappable control area to at least 44x44 pt, or keep the visual icon small "
        "inside a larger transparent hit area. Check nearby controls so people can tap accurately."
    ),
    "crowded_touch_target": (
        "Increase spacing around the boxed control or move it away from nearby controls and screen edges. "
        "Keep touch zones visually separated so accidental taps are less likely."
    ),
    "small_text_readability": (
        "Increase the boxed text size and line height, especially for labels, prices, statuses, instructions, and buttons. "
        "Use at least 11 pt for important mobile text unless the text is purely decorative."
    ),
    "icon_only_unlabeled_control": (
        "Add a short visible label, tooltip-equivalent text, or a clearer standard icon for the boxed action. "
        "Do not rely on ambiguous icon shape alone for important controls."
    ),
    "flat_visual_hierarchy": (
        "Create a clearer type and spacing scale for the affected frame: make the "
        "primary information visibly dominant, reduce competing labels, and reserve "
        "small text for support content."
    ),
}

GOOD_CRITERION_SCORE_THRESHOLD = 9.0
CLIENT_VISIBLE_EVIDENCE_TYPES = {
    "real_page_geometry_screenshot",
}
SUPPORTING_VISUAL_EVIDENCE_TYPES = {
    "annotated_screenshot",
    "node_preview_locator",
    "real_page_context_screenshot",
}
CLIENT_REJECTED_VISIBILITY_REASONS = {
    "hidden_in_figma_tree",
    "outside_client_view",
    "target_not_visibly_rendered",
    "rendered_text_foreground_fills_target",
    "rendered_text_color_not_found_at_target",
    "rendered_text_target_contains_unexpected_color",
    "rendered_target_does_not_match_outlier_fill",
    "rendered_target_contrast_is_not_low",
    "target_could_not_be_mapped",
    "nearby_region_has_no_visible_detail",
}


def _html(value: object) -> str:
    return escape(_clean_text(value), quote=True)


def _html_attrs(attrs: dict[str, object | None]) -> str:
    parts = []
    for name, value in attrs.items():
        if value is None:
            continue
        parts.append(f' {name}="{_html(value)}"')
    return "".join(parts)


def _icon_svg(name: str) -> str:
    paths = {
        "image": '<rect x="3" y="4" width="18" height="16" rx="2"></rect><circle cx="8.5" cy="9" r="1.5"></circle><path d="M21 15l-5-5L5 20"></path>',
        "text": '<path d="M4 6h16"></path><path d="M7 6v12"></path><path d="M17 6v12"></path><path d="M8 18h8"></path>',
        "score": '<path d="M12 3v18"></path><path d="M5 8h14"></path><path d="M7 16h10"></path>',
    }
    return (
        '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" '
        'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths[name]}</svg>'
    )


def _edit_button(
    *,
    action: str,
    issue_id: str,
    label: str,
    icon: str,
) -> str:
    return (
        '<button class="icon-button" type="button" '
        f'data-edit-action="{_html(action)}" data-issue-id="{_html(issue_id)}" '
        f'aria-label="{_html(label)}" title="{_html(label)}" data-local-edit-control>{_icon_svg(icon)}</button>'
    )


def _issue_edit_toolbar(
    issue_id: str,
    *,
    image: bool = True,
    copy: bool = True,
    score: bool = True,
) -> str:
    buttons = []
    if image:
        buttons.append(
            _edit_button(
                action="image",
                issue_id=issue_id,
                label="Change screenshot",
                icon="image",
            )
        )
    if copy:
        buttons.append(
            _edit_button(
                action="copy",
                issue_id=issue_id,
                label="Edit issue text",
                icon="text",
            )
        )
    if score:
        buttons.append(
            _edit_button(
                action="score",
                issue_id=issue_id,
                label="Edit issue score",
                icon="score",
            )
        )
    return f'<div class="edit-toolbar" aria-label="Issue edit controls" data-local-edit-control>{"".join(buttons)}</div>'


def _clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    for source, replacement in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(source, replacement)

    cleaned: list[str] = []
    for char in text:
        if char in {"\ufe0e", "\ufe0f"}:
            continue
        category = unicodedata.category(char)
        if category in {"So", "Co"}:
            continue
        if category in {"Cf", "Cc"} and char not in {"\n", "\t"}:
            continue
        cleaned.append(char)
    return " ".join("".join(cleaned).split())


def _safe_asset_name(value: str) -> str:
    cleaned = _clean_text(value).lower()
    safe = "".join(char if char.isalnum() else "-" for char in cleaned)
    return "-".join(part for part in safe.split("-") if part) or "issue"


def _titleize_id(value: str) -> str:
    return _clean_text(value.replace("_", " ").replace("-", " ").title())


def _image_src(image_path: str, report_path: Path) -> str:
    path = Path(image_path)
    if path.exists():
        relative = os.path.relpath(path.resolve(), report_path.parent.resolve())
        return Path(relative).as_posix()
    return image_path.replace("\\", "/")


def _json_script_payload(detection_result: DetectionResult) -> str:
    payload = json.dumps(detection_result.model_dump(mode="json"), ensure_ascii=False)
    return payload.replace("<", "\\u003c").replace(">", "\\u003e")


def _issues(detection_result: DetectionResult) -> list[dict[str, object]]:
    return [
        issue
        for issue in detection_result.model_dump(mode="json")["draft_issues"]
        if not _issue_rejected_by_client_view(issue)
    ]


def _issue_rejected_by_client_view(issue: dict[str, object]) -> bool:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    ai_review = evidence.get("ai_review") if isinstance(evidence.get("ai_review"), dict) else {}
    if ai_review.get("decision") == "reject":
        return True
    return str(evidence.get("client_visibility") or "") in CLIENT_REJECTED_VISIBILITY_REASONS


def _issue_has_client_visible_evidence(issue: dict[str, object]) -> bool:
    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    for artifact in visual_evidence:
        if not isinstance(artifact, dict):
            continue
        image_path = str(artifact.get("image_path") or "")
        rectangle = _rectangle_from_artifact(artifact)
        validation = (
            artifact.get("client_view_validation")
            if isinstance(artifact.get("client_view_validation"), dict)
            else {}
        )
        if str(validation.get("rejected_reason") or "") in CLIENT_REJECTED_VISIBILITY_REASONS:
            continue
        if rectangle is None:
            continue
        rect_x, rect_y, rect_w, rect_h = rectangle
        image_width = artifact.get("image_width")
        image_height = artifact.get("image_height")
        if (
            not isinstance(image_width, (int, float))
            or not isinstance(image_height, (int, float))
            or rect_w < 3
            or rect_h < 3
            or rect_x < 0
            or rect_y < 0
            or rect_x + rect_w > float(image_width)
            or rect_y + rect_h > float(image_height)
        ):
            continue
        if (
            artifact.get("type") == "real_page_geometry_screenshot"
            and image_path
            and Path(image_path).exists()
        ):
            return True
    return False


def _issue_supporting_visual_artifact(issue: dict[str, object]) -> dict[str, object] | None:
    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    for artifact in visual_evidence:
        if not isinstance(artifact, dict):
            continue
        image_path = str(artifact.get("image_path") or "")
        if not image_path or not Path(image_path).exists():
            continue
        artifact_type = str(artifact.get("type") or "")
        if artifact_type in CLIENT_VISIBLE_EVIDENCE_TYPES | SUPPORTING_VISUAL_EVIDENCE_TYPES:
            return artifact
    return None


def _issue_evidence_label(issue: dict[str, object]) -> str:
    if _issue_has_client_visible_evidence(issue):
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        if str(evidence.get("visual_scope") or "") == "visible_fallback":
            return "verified Figma screen screenshot"
        return "verified client screenshot"
    artifact = _issue_supporting_visual_artifact(issue)
    if artifact is None:
        return "static Figma evidence"
    artifact_type = str(artifact.get("type") or "")
    if artifact_type == "node_preview_locator":
        return "Figma geometry evidence"
    if artifact_type == "annotated_screenshot":
        return "Figma-rendered annotation"
    if artifact_type == "real_page_context_screenshot":
        return "context screenshot"
    return "supporting visual evidence"


def _issues_by_criterion(
    detection_result: DetectionResult,
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for issue in _issues(detection_result):
        criterion = str(issue.get("criterion") or issue.get("axis") or "unknown")
        grouped.setdefault(criterion, []).append(issue)
    return grouped


def _criterion_is_actionable(score: float, issue_count: int = 0) -> bool:
    return issue_count > 0 or score < GOOD_CRITERION_SCORE_THRESHOLD


def _actionable_issues(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    actionable_criterion_ids = {
        criterion_id
        for criterion_id, issues in grouped.items()
        if _criterion_is_actionable(_criterion_score(issues), len(issues))
    }
    candidates = [
        issue
        for issue in _issues(detection_result)
        if str(issue.get("criterion") or issue.get("axis") or "unknown")
        in actionable_criterion_ids
    ]
    return _representative_actionable_issues(candidates)


def _issue_pattern_key(issue: dict[str, object]) -> str:
    evidence = _issue_evidence(issue)
    detector_id = str(evidence.get("detector_id") or "")
    if detector_id == "low_text_contrast":
        sample = _clean_text(evidence.get("text_sample") or "").lower()
        role = _clean_text(evidence.get("contrast_text_role") or "text")
        return f"{detector_id}:{role}:{sample}"
    if detector_id == "small_text_readability":
        sample = _clean_text(evidence.get("text_sample") or "").lower()
        role = _clean_text(evidence.get("small_text_role") or evidence.get("accessibility_check") or "text")
        return f"{detector_id}:{role}:{sample}"
    if detector_id in {"crowded_touch_target", "icon_only_unlabeled_control"}:
        location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        node_name = _clean_text(location.get("node_name") or "").lower()
        check = _clean_text(evidence.get("accessibility_check") or detector_id)
        return f"{detector_id}:{check}:{node_name}"
    if detector_id in {"generic_navigation_label", "placeholder_or_generic_copy"}:
        sample = _clean_text(evidence.get("text_sample") or evidence.get("label") or "").lower()
        subdetector = _clean_text(
            evidence.get("content_microcopy_subdetector")
            or ""
        )
        return f"{detector_id}:{subdetector}:{sample}"
    return f"{detector_id}:{str(issue.get('criterion') or issue.get('axis') or 'unknown')}:{str(issue.get('id') or '')}"


def _representative_actionable_issues(issues: list[dict[str, object]]) -> list[dict[str, object]]:
    sorted_issues = sorted(issues, key=_issue_sort_key)
    representatives: list[dict[str, object]] = []
    seen_patterns: set[str] = set()
    detector_counts: dict[str, int] = {}
    for issue in sorted_issues:
        evidence = _issue_evidence(issue)
        detector_id = str(evidence.get("detector_id") or "")
        pattern_key = str(evidence.get("pattern_key") or _issue_pattern_key(issue))
        if pattern_key in seen_patterns:
            continue
        max_per_detector = 2 if detector_id == "low_text_contrast" else 3
        if detector_counts.get(detector_id, 0) >= max_per_detector:
            continue
        seen_patterns.add(pattern_key)
        detector_counts[detector_id] = detector_counts.get(detector_id, 0) + 1
        representatives.append(issue)
    return representatives


def _criterion_lookup(criteria: list[UxUiCriterion]) -> dict[str, UxUiCriterion]:
    return {criterion.id: criterion for criterion in criteria}


def _criterion_name(criterion_id: str, lookup: dict[str, UxUiCriterion]) -> str:
    criterion = lookup.get(criterion_id)
    return criterion.short_name if criterion else _titleize_id(criterion_id)


def _criterion_full_name(criterion_id: str, lookup: dict[str, UxUiCriterion]) -> str:
    criterion = lookup.get(criterion_id)
    return criterion.name if criterion else _titleize_id(criterion_id)


def _issue_evidence(issue: dict[str, object]) -> dict[str, object]:
    evidence = issue.get("evidence")
    return evidence if isinstance(evidence, dict) else {}


def _detector_id(issue: dict[str, object]) -> str:
    return str(_issue_evidence(issue).get("detector_id") or "")


def _heuristic_model(issue: dict[str, object]) -> dict[str, object]:
    return DETECTOR_HEURISTIC_WEIGHTS.get(
        _detector_id(issue),
        {
            "heuristics": ["Visibility of system status", "Aesthetic and minimalist design"],
            "weight": 0.95,
        },
    )


def _numeric_evidence(evidence: dict[str, object], key: str) -> float | None:
    value = evidence.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _issue_impact_multiplier(issue: dict[str, object]) -> float:
    evidence = _issue_evidence(issue)
    detector_id = _detector_id(issue)
    impact = 1.0

    if detector_id == "low_text_contrast":
        contrast_gap = _numeric_evidence(evidence, "contrast_gap")
        required_ratio = _numeric_evidence(evidence, "required_ratio") or 4.5
        contrast_ratio = _numeric_evidence(evidence, "contrast_ratio")
        if contrast_gap is not None:
            impact += min(0.38, max(0.0, contrast_gap) / max(required_ratio, 1.0) * 0.42)
        elif contrast_ratio is not None:
            impact += min(0.34, max(0.0, required_ratio - contrast_ratio) / max(required_ratio, 1.0) * 0.42)
        impact_level = str(evidence.get("user_impact_level") or "")
        if impact_level == "critical":
            impact += 0.16
        elif impact_level == "important":
            impact += 0.06
        elif impact_level == "secondary":
            impact -= 0.18
        role = str(evidence.get("contrast_text_role") or "")
        if role in {"status_or_time_feedback", "choice_or_setting_label"}:
            impact += 0.04

    if detector_id == "small_touch_target":
        shortfall = _numeric_evidence(evidence, "touch_target_shortfall")
        if shortfall is not None:
            impact += min(0.32, max(0.0, shortfall) / 44.0 * 0.38)

    if detector_id == "crowded_touch_target":
        nearest_gap = _numeric_evidence(evidence, "nearest_control_gap")
        edge_gap = _numeric_evidence(evidence, "edge_gap")
        if nearest_gap is not None:
            impact += min(0.24, max(0.0, 8.0 - nearest_gap) / 8.0 * 0.3)
        if edge_gap is not None:
            impact += min(0.18, max(0.0, 12.0 - edge_gap) / 12.0 * 0.24)

    if detector_id == "small_text_readability":
        font_size = _numeric_evidence(evidence, "font_size")
        if font_size is not None:
            impact += min(0.26, max(0.0, 11.0 - font_size) / 11.0 * 0.36)

    if detector_id == "icon_only_unlabeled_control":
        impact += 0.08

    if detector_id == "component_style_outlier":
        distance = _numeric_evidence(evidence, "distance")
        if distance is not None:
            impact += min(0.26, max(0.0, distance) / 120.0)

    if detector_id in {"generic_navigation_label", "placeholder_or_generic_copy"}:
        sample = _clean_text(evidence.get("text_sample") or evidence.get("label") or "")
        if sample and len(sample) <= 14:
            impact += 0.08

    family_size = _numeric_evidence(evidence, "family_sample_size")
    if family_size is not None and family_size > 1:
        impact += min(0.18, (family_size - 1.0) * 0.025)

    text_count = _numeric_evidence(evidence, "text_count")
    if detector_id == "flat_visual_hierarchy" and text_count is not None:
        impact += min(0.16, text_count / 120.0)
    panel_height_ratio = _numeric_evidence(evidence, "panel_height_to_background_ratio")
    panel_gap = _numeric_evidence(evidence, "heading_to_panel_gap")
    if detector_id == "flat_visual_hierarchy" and panel_height_ratio is not None:
        impact += min(0.34, max(0.0, panel_height_ratio - 0.68) * 1.1)
    if detector_id == "flat_visual_hierarchy" and panel_gap is not None:
        impact += min(0.22, max(0.0, 48.0 - panel_gap) / 48.0 * 0.26)
    workspace_count = _numeric_evidence(evidence, "component_set_count")
    workspace_ratio = _numeric_evidence(evidence, "component_workspace_area_ratio")
    if detector_id == "flat_visual_hierarchy" and workspace_count is not None:
        impact += min(0.3, workspace_count * 0.035)
    if detector_id == "flat_visual_hierarchy" and workspace_ratio is not None:
        impact += min(0.24, workspace_ratio * 1.1)

    return max(0.78, min(1.48, impact))


def _issue_penalty(issue: dict[str, object]) -> float:
    severity = str(issue.get("severity") or "low")
    evidence = _issue_evidence(issue)
    confidence = str(evidence.get("confidence") or "low")
    visual_scope = str(evidence.get("visual_scope") or "visible_fallback")
    heuristic_model = _heuristic_model(issue)
    heuristic_weight = heuristic_model.get("weight")
    visual_evidence = issue.get("visual_evidence") if isinstance(issue.get("visual_evidence"), list) else []
    has_real_screenshot = any(
        isinstance(artifact, dict)
        and artifact.get("type") == "real_page_geometry_screenshot"
        for artifact in visual_evidence
    )
    screenshot_weight = 1.05 if has_real_screenshot else 0.92

    return (
        SEVERITY_WEIGHT.get(severity, 0.55)
        * CONFIDENCE_WEIGHT.get(confidence, 0.58)
        * VISUAL_SCOPE_WEIGHT.get(visual_scope, 0.92)
        * (float(heuristic_weight) if isinstance(heuristic_weight, (int, float)) else 0.95)
        * _issue_impact_multiplier(issue)
        * screenshot_weight
    )


def _issue_sort_key(issue: dict[str, object]) -> tuple[int, int, int, int, float, float, str]:
    severity = str(issue.get("severity") or "low")
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    confidence = str(evidence.get("confidence") or "low")
    confidence_order = {"high": 0, "medium": 1, "low": 2}.get(confidence, 3)
    detector_id = str(evidence.get("detector_id") or "")
    detector_order = DETECTOR_PRIORITY.get(detector_id, 99)
    if (
        detector_id == "flat_visual_hierarchy"
        and str(evidence.get("visual_brand_subdetector") or "") == "bad_foreground_panel_position"
    ):
        detector_order = -1
    visual_evidence = issue.get("visual_evidence") if isinstance(issue.get("visual_evidence"), list) else []
    has_real_screenshot = any(
        isinstance(artifact, dict)
        and artifact.get("type") == "real_page_geometry_screenshot"
        for artifact in visual_evidence
    )
    impact_value = 0.0
    for key in ("evaluation_priority", "contrast_gap", "distance", "family_sample_size", "text_count"):
        value = evidence.get(key)
        if isinstance(value, (int, float)):
            impact_value = max(impact_value, float(value))
    return (
        SEVERITY_ORDER.get(severity, 3),
        confidence_order,
        0 if has_real_screenshot else 1,
        detector_order,
        -_issue_penalty(issue),
        -impact_value,
        str(issue.get("id") or ""),
    )


def _criterion_score(issues: list[dict[str, object]]) -> float:
    if not issues:
        return 10.0

    penalties = sorted((_issue_penalty(issue) for issue in issues), reverse=True)
    detector_diversity = len({_detector_id(issue) for issue in issues if _detector_id(issue)})
    repeated_pressure = (
        penalties[0] * 1.55
        + sum(penalty * 0.62 for penalty in penalties[1:4])
        + sum(penalty * 0.24 for penalty in penalties[4:])
    )
    diversity_pressure = min(0.45, max(0, detector_diversity - 1) * 0.15)
    penalty = min(7.6, repeated_pressure + diversity_pressure)
    return round(max(2.4, 10.0 - penalty), 1)


def _overall_score(axis_scores: list[float], issue_count: int) -> float:
    if not axis_scores:
        return 10.0
    average_score = sum(axis_scores) / len(axis_scores)
    weakest_axis_pressure = max(0.0, 10.0 - min(axis_scores)) * 0.22
    issue_pressure = min(2.2, issue_count * 0.16)
    return round(max(0.0, min(10.0, average_score - weakest_axis_pressure - issue_pressure)), 1)


def _score_ring(
    score: float,
    *,
    size: int = 154,
    label: str = "",
    attrs: dict[str, object | None] | None = None,
) -> str:
    radius = round(size * 0.4235, 3)
    center = size / 2
    circumference = 2 * math.pi * radius
    dash_offset = circumference * (1 - max(0.0, min(10.0, score)) / 10)
    label_html = f"<span>{_html(label)}</span>" if label else ""
    attr_html = _html_attrs({"data-score": f"{score:.1f}", **(attrs or {})})
    return f"""
    <div class="score-ring"{attr_html} style="--ring-size:{size}px; --ring-width:{max(9, round(size * 0.075, 1))}px;">
      <svg viewBox="0 0 {size} {size}" aria-hidden="true">
        <circle cx="{center}" cy="{center}" r="{radius}" class="ring-track"></circle>
        <circle cx="{center}" cy="{center}" r="{radius}" class="ring-progress" style="stroke-dasharray:{circumference:.2f};stroke-dashoffset:{dash_offset:.2f};"></circle>
      </svg>
      <div class="score-ring-copy">
        <strong data-score-text>{score:.1f}</strong>
        {label_html}
      </div>
    </div>
    """


def _file_label(detection_result: DetectionResult) -> str:
    issue_list = _issues(detection_result)
    for issue in issue_list:
        location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        path = str(location.get("path") or "")
        if path:
            label = _clean_text(path.split(">")[0].strip())
            return label or "Figma design"
    return "Figma design"


def _status_label(status: object) -> str:
    raw = _clean_text(status or "")
    if raw == "draft_detections_not_final_audit":
        return "Draft audit ready for human review"
    return raw or "Draft audit ready for human review"


def _is_locator_artifact(artifact: dict[str, object]) -> bool:
    artifact_type = str(artifact.get("type") or "")
    return artifact_type.endswith("_locator")


def _is_context_only_artifact(artifact: dict[str, object]) -> bool:
    artifact_type = str(artifact.get("type") or "")
    accuracy = str(artifact.get("accuracy") or "")
    return artifact_type == "real_page_context_screenshot" or "broad_visible_context" in accuracy


def _first_visual_artifact(
    issue: dict[str, object],
    *,
    include_locator_fallback: bool = False,
) -> dict[str, object] | None:
    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    for artifact in visual_evidence:
        if (
            isinstance(artifact, dict)
            and artifact.get("type") == "real_page_geometry_screenshot"
            and artifact.get("image_path")
            and not (not include_locator_fallback and _is_locator_artifact(artifact))
        ):
            return artifact
    for artifact in visual_evidence:
        if not isinstance(artifact, dict) or not artifact.get("image_path"):
            continue
        if not include_locator_fallback and _is_locator_artifact(artifact):
            continue
        return artifact
    return None


def _real_page_screenshot_files(report_path: Path) -> list[Path]:
    real_pages_dir = report_path.parent / "real_pages"
    if not real_pages_dir.exists():
        return []
    return [
        path
        for path in sorted(real_pages_dir.glob("*.png"))
        if path.is_file() and not path.name.startswith("_")
    ]


def _real_page_title(path: Path) -> str:
    stem = path.stem
    if "__" in stem:
        stem = stem.split("__")[-1]
    title = stem.replace("_", " ").replace("-", " ").strip()
    return title or "Captured page"


def _path_is_inside_cwd(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path.cwd().resolve())
        return True
    except ValueError:
        return False


def _annotation_files(report_path: Path) -> list[Path]:
    data_dir = report_path.parent.parent
    candidates = [data_dir / "annotations"]
    if _path_is_inside_cwd(report_path):
        candidates.append(Path("data") / "annotations")
    seen: set[Path] = set()
    files: list[Path] = []
    for directory in candidates:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.png")):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            files.append(path)
    return files


def _node_id_file_key(node_id: str) -> str:
    return node_id.replace(":", "-").replace(";", "-").lower()


def _annotation_for_issue(issue: dict[str, object], report_path: Path) -> Path | None:
    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    node_id = str(location.get("node_id") or "")
    if not node_id:
        return None
    node_key = f"__node-{_node_id_file_key(node_id)}"
    detector_id = _detector_id(issue).replace("_", "-")
    matches = [
        path
        for path in _annotation_files(report_path)
        if node_key in path.stem.lower()
    ]
    if not matches:
        return None
    detector_matches = [
        path
        for path in matches
        if detector_id and detector_id in path.stem.lower()
    ]
    return sorted(detector_matches or matches)[0]


def _red_marker_rect(image: "Image.Image") -> tuple[float, float, float, float] | None:
    rgb = image.convert("RGB")
    width, height = rgb.size
    red_pixels: set[tuple[int, int]] = set()
    for y in range(height):
        for x in range(width):
            red, green, blue = rgb.getpixel((x, y))
            if red >= 190 and green <= 80 and blue <= 80 and red - max(green, blue) >= 110:
                red_pixels.add((x, y))
    if not red_pixels:
        return None

    visited: set[tuple[int, int]] = set()
    candidates: list[tuple[int, int, int, int, int]] = []
    for pixel in red_pixels:
        if pixel in visited:
            continue
        stack = [pixel]
        visited.add(pixel)
        xs: list[int] = []
        ys: list[int] = []
        while stack:
            px, py = stack.pop()
            xs.append(px)
            ys.append(py)
            for neighbor in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                if neighbor in red_pixels and neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        left = min(xs)
        top = min(ys)
        right = max(xs)
        bottom = max(ys)
        marker_width = right - left + 1
        marker_height = bottom - top + 1
        pixel_count = len(xs)
        if marker_width < 24 or marker_height < 8:
            continue
        if marker_width / max(marker_height, 1) < 1.7:
            continue
        candidates.append((left, top, right, bottom, pixel_count))

    if not candidates:
        return None
    left, top, right, bottom, _pixel_count = max(
        candidates,
        key=lambda candidate: (
            (candidate[2] - candidate[0] + 1) * (candidate[3] - candidate[1] + 1),
            candidate[4],
        ),
    )
    return float(left), float(top), float(right - left + 1), float(bottom - top + 1)


def _annotation_callout_image(issue: dict[str, object], report_path: Path) -> dict[str, str] | None:
    if Image is None or ImageDraw is None:
        return None
    annotation_path = _annotation_for_issue(issue, report_path)
    if annotation_path is None:
        return None
    image = Image.open(annotation_path).convert("RGB")
    marker_rect = _red_marker_rect(image)
    if marker_rect is None:
        return {
            "src": _image_src(str(annotation_path), report_path),
            "label": "Static Figma locator with marker",
            "kind": "locator_callout",
        }
    crop, shifted_rect = _crop_around_target(image, marker_rect, None)
    annotated = _draw_rounded_rect_callout(crop, shifted_rect)
    issue_id = _safe_asset_name(str(issue.get("id") or "issue"))
    output_dir = report_path.parent / "callouts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{issue_id}-callout.png"
    annotated.save(output_path)
    return {
        "src": _image_src(str(output_path), report_path),
        "label": "Static Figma locator with marker",
        "kind": "locator_callout",
    }


def _fallback_extraction_paths(report_path: Path) -> list[Path]:
    data_dir = report_path.parent.parent
    candidates = [
        data_dir / "extracted" / "audit_info.json",
        data_dir / "normalized" / "normalized_file.json",
    ]
    if _path_is_inside_cwd(report_path):
        candidates.extend(
            [
                Path("data") / "extracted" / "audit_info.json",
                Path("data") / "normalized" / "normalized_file.json",
            ]
        )
    return candidates


def _nodes_from_payload(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    normalized = payload.get("normalized_file")
    source = normalized if isinstance(normalized, dict) else payload
    nodes = source.get("nodes") if isinstance(source, dict) else None
    if not isinstance(nodes, list):
        return {}
    indexed: dict[str, dict[str, object]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id:
            indexed[node_id] = node
    return indexed


def _fallback_nodes(report_path: Path) -> dict[str, dict[str, object]]:
    seen: set[Path] = set()
    for path in _fallback_extraction_paths(report_path):
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        try:
            payload = load_json(path)
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            nodes = _nodes_from_payload(payload)
            if nodes:
                return nodes
    return {}


def _node_box_from_node(node: dict[str, object]) -> dict[str, float] | None:
    for key in ("absolute_render_bounds", "absolute_bounding_box"):
        box = node.get(key)
        if not isinstance(box, dict):
            continue
        values: dict[str, float] = {}
        for field in ("x", "y", "width", "height"):
            value = box.get(field)
            if not isinstance(value, (int, float)):
                break
            values[field] = float(value)
        else:
            if values["width"] > 0 and values["height"] > 0:
                return values
    return None


def _ancestor_chain(
    node: dict[str, object],
    nodes: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    chain: list[dict[str, object]] = []
    current: dict[str, object] | None = node
    visited: set[str] = set()
    while isinstance(current, dict):
        current_id = current.get("id")
        if not isinstance(current_id, str) or current_id in visited:
            break
        visited.add(current_id)
        chain.append(current)
        parent_id = current.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            break
        current = nodes.get(parent_id)
    return chain


def _context_screenshot_for_node(
    context_node: dict[str, object],
    report_path: Path,
) -> Path | None:
    context_id = str(context_node.get("id") or "")
    context_name = _clean_text(context_node.get("name") or "")
    id_key = _node_id_file_key(context_id)
    name_key = _safe_asset_name(context_name).lower()
    candidates = _real_page_screenshot_files(report_path)
    for path in candidates:
        stem = path.stem.lower()
        if id_key and id_key in stem:
            return path
    for path in candidates:
        title_key = _safe_asset_name(_real_page_title(path)).lower()
        if name_key and title_key == name_key:
            return path
    return None


def _node_and_ancestors_visible_from_nodes(
    node: dict[str, object] | None,
    nodes: dict[str, dict[str, object]],
) -> bool:
    current = node
    visited: set[str] = set()
    while isinstance(current, dict):
        if current.get("visible") is False:
            return False
        current_id = current.get("id")
        if isinstance(current_id, str):
            if current_id in visited:
                return False
            visited.add(current_id)
        parent_id = current.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            break
        current = nodes.get(parent_id)
    return node is not None


def _is_large_page_node(
    node: dict[str, object],
    nodes: dict[str, dict[str, object]],
) -> bool:
    if node.get("type") not in ROOT_SCREEN_TYPES:
        return False
    if not _node_and_ancestors_visible_from_nodes(node, nodes):
        return False
    parent_id = node.get("parent_id")
    if isinstance(parent_id, str) and parent_id:
        parent = nodes.get(parent_id)
        if not isinstance(parent, dict) or parent.get("type") != "CANVAS":
            return False
    box = _node_box_from_node(node)
    if box is None:
        return False
    width = box["width"]
    height = box["height"]
    return width >= 280 and height >= 500 and width * height >= 140000


def _page_node_sort_key(node: dict[str, object]) -> tuple[float, float, str]:
    box = _node_box_from_node(node) or {}
    return (
        float(box.get("y") or 0),
        float(box.get("x") or 0),
        str(node.get("id") or ""),
    )


def _large_page_nodes(report_path: Path) -> list[dict[str, object]]:
    nodes = _fallback_nodes(report_path)
    return sorted(
        [node for node in nodes.values() if _is_large_page_node(node, nodes)],
        key=_page_node_sort_key,
    )


def _render_cache_files(report_path: Path) -> list[Path]:
    data_dir = report_path.parent.parent
    candidates = [data_dir / "annotations" / "_render_cache"]
    if _path_is_inside_cwd(report_path):
        candidates.append(Path("data") / "annotations" / "_render_cache")
    seen: set[Path] = set()
    files: list[Path] = []
    for directory in candidates:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.png")):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            files.append(path)
    return files


def _render_cache_for_node(
    context_node: dict[str, object],
    report_path: Path,
) -> Path | None:
    context_id = str(context_node.get("id") or "")
    if not context_id:
        return None
    node_key = f"__node-{_node_id_file_key(context_id)}__"
    for path in _render_cache_files(report_path):
        if node_key in path.stem.lower():
            return path
    return None


def _phone_context_box_from_chain(chain: list[dict[str, object]]) -> dict[str, float] | None:
    for node in chain:
        box = _node_box_from_node(node)
        if box is not None and _is_phone_like_figma_box(box):
            return box
    return None


def _figma_box_to_cached_render_rect(
    *,
    image: "Image.Image",
    figma_box: dict[str, float],
    render_box: dict[str, float],
) -> tuple[float, float, float, float] | None:
    scale_x = image.width / render_box["width"]
    scale_y = image.height / render_box["height"]
    x = (figma_box["x"] - render_box["x"]) * scale_x
    y = (figma_box["y"] - render_box["y"]) * scale_y
    width = figma_box["width"] * scale_x
    height = figma_box["height"] * scale_y
    if x + width < 0 or y + height < 0 or x > image.width or y > image.height:
        return None
    left = max(0.0, x)
    top = max(0.0, y)
    right = min(float(image.width), x + width)
    bottom = min(float(image.height), y + height)
    if right <= left or bottom <= top:
        return None
    return left, top, right - left, bottom - top


def _fallback_screenshot_context(
    issue: dict[str, object],
    report_path: Path,
) -> tuple[
    "Image.Image",
    tuple[float, float, float, float],
    tuple[float, float, float, float] | None,
    tuple[float, float, float, float] | None,
    str,
    dict[str, object],
    tuple[int, int, int] | None,
] | None:
    if Image is None:
        return None

    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    node_id = str(location.get("node_id") or "")
    if not node_id:
        return None
    nodes = _fallback_nodes(report_path)
    target_node = nodes.get(node_id)
    if target_node is None:
        return None
    target_box = _node_box_from_node(target_node)
    if target_box is None:
        return None

    chain = _ancestor_chain(target_node, nodes)
    for context_node in chain[1:]:
        render_box = _node_box_from_node(context_node)
        if render_box is None:
            continue
        render_cache_path = _render_cache_for_node(context_node, report_path)
        if render_cache_path is not None:
            image = Image.open(render_cache_path).convert("RGB")
            target_rect = _figma_box_to_cached_render_rect(
                image=image,
                figma_box=target_box,
                render_box=render_box,
            )
            if target_rect is not None:
                phone_box = _phone_context_box_from_chain(chain)
                phone_rect = (
                    _figma_box_to_cached_render_rect(
                        image=image,
                        figma_box=phone_box,
                        render_box=render_box,
                    )
                    if phone_box is not None
                    else None
                )
                artifact = {
                    "type": "render_cache_geometry_fallback",
                    "image_path": str(render_cache_path),
                    "target_node_id": node_id,
                    "render_node_id": str(context_node.get("id") or ""),
                    "figma_target_bounding_box": target_box,
                    "figma_render_bounding_box": render_box,
                    "figma_phone_context_bounding_box": phone_box,
                    "accuracy": "report_generated_from_cached_figma_render_and_geometry",
                }
                return (
                    image,
                    target_rect,
                    (0.0, 0.0, float(image.width), float(image.height)),
                    phone_rect,
                    "render_cache_fallback",
                    artifact,
                    None,
                )
        screenshot_path = _context_screenshot_for_node(context_node, report_path)
        if screenshot_path is not None:
            image = Image.open(screenshot_path).convert("RGB")
            artifact = {
                "type": "report_geometry_fallback",
                "image_path": str(screenshot_path),
                "target_node_id": node_id,
                "render_node_id": str(context_node.get("id") or ""),
                "figma_target_bounding_box": target_box,
                "figma_render_bounding_box": render_box,
                "figma_phone_context_bounding_box": _phone_context_box_from_chain(chain),
                "accuracy": "report_generated_from_existing_page_screenshot_and_figma_geometry",
            }
            mapped = _target_rect_and_design_bounds_on_real_screenshot(
                image=image,
                artifact=artifact,
            )
            if mapped is None:
                continue
            target_rect, crop_bounds = mapped
            phone_rect = None
            phone_box = _box_from_artifact_key(artifact, "figma_phone_context_bounding_box")
            if phone_box is not None:
                phone_rect = _figma_box_to_real_rect(
                    image=image,
                    artifact=artifact,
                    figma_box=phone_box,
                    design_bounds=crop_bounds,
                )
                if phone_rect is not None:
                    phone_rect = _expand_phone_rect_to_figma_width(
                        image=image,
                        phone_rect=phone_rect,
                        phone_box=phone_box,
                        design_bounds=crop_bounds,
                    )
            return image, target_rect, crop_bounds, phone_rect, "report_fallback", artifact, None
    return None


def _rectangle_from_artifact(artifact: dict[str, object]) -> tuple[float, float, float, float] | None:
    rectangle = artifact.get("rectangle_px")
    if not isinstance(rectangle, dict):
        return None
    values = []
    for key in ("x", "y", "width", "height"):
        value = rectangle.get(key)
        if not isinstance(value, (int, float)):
            return None
        values.append(float(value))
    if values[2] <= 0 or values[3] <= 0:
        return None
    return values[0], values[1], values[2], values[3]


def _box_from_mapping(artifact: dict[str, object]) -> tuple[dict[str, float], dict[str, float]] | None:
    target = artifact.get("figma_target_bounding_box")
    render = artifact.get("figma_render_bounding_box")
    if not isinstance(target, dict) or not isinstance(render, dict):
        return None

    def box(source: dict[str, object]) -> dict[str, float] | None:
        values: dict[str, float] = {}
        for key in ("x", "y", "width", "height"):
            value = source.get(key)
            if not isinstance(value, (int, float)):
                return None
            values[key] = float(value)
        if values["width"] <= 0 or values["height"] <= 0:
            return None
        return values

    target_box = box(target)
    render_box = box(render)
    if target_box is None or render_box is None:
        return None
    return target_box, render_box


def _box_from_artifact_key(artifact: dict[str, object], key: str) -> dict[str, float] | None:
    source = artifact.get(key)
    if not isinstance(source, dict):
        return None
    values: dict[str, float] = {}
    for field in ("x", "y", "width", "height"):
        value = source.get(field)
        if not isinstance(value, (int, float)):
            return None
        values[field] = float(value)
    if values["width"] <= 0 or values["height"] <= 0:
        return None
    return values


def _is_phone_like_figma_box(box: dict[str, float]) -> bool:
    return 280 <= box["width"] <= 460 and 120 <= box["height"] <= 950


def _find_design_bounds(image: "Image.Image", render_box: dict[str, float]) -> tuple[float, float, float, float]:
    width, height = image.size
    sample_bottom = max(0, height - 280)
    rgb = image.convert("RGB")
    sample_step = max(8, min(width, height) // 80)
    top_edge_samples = [rgb.getpixel((x, 0)) for x in range(0, width, sample_step)]
    edge_samples: list[tuple[int, int, int]] = list(top_edge_samples)
    for x in range(0, width, sample_step):
        edge_samples.append(rgb.getpixel((x, max(0, height - 1))))
    for y in range(0, height, sample_step):
        edge_samples.append(rgb.getpixel((0, y)))
        edge_samples.append(rgb.getpixel((max(0, width - 1), y)))

    def background_bucket(pixel: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(channel // 16 for channel in pixel)

    top_counts = Counter(background_bucket(sample) for sample in top_edge_samples)
    dominant_bucket, dominant_count = top_counts.most_common(1)[0]
    if dominant_count < max(1, len(top_edge_samples) // 2):
        dominant_bucket = Counter(background_bucket(sample) for sample in edge_samples).most_common(1)[0][0]
    dominant_samples = [
        sample for sample in edge_samples if background_bucket(sample) == dominant_bucket
    ]
    background = tuple(
        round(sum(sample[channel] for sample in dominant_samples) / len(dominant_samples))
        for channel in range(3)
    )

    def is_content_pixel(pixel: tuple[int, int, int]) -> bool:
        return max(abs(pixel[index] - background[index]) for index in range(3)) > 18

    rows: list[int] = []
    for y in range(0, sample_bottom, 4):
        content_pixels = 0
        for x in range(0, width, 4):
            if is_content_pixel(rgb.getpixel((x, y))):
                content_pixels += 1
        if content_pixels >= max(16, width // 70):
            rows.append(y)

    if not rows:
        scale = min(width / render_box["width"], height / render_box["height"], 1.0)
        box_w = render_box["width"] * scale
        box_h = render_box["height"] * scale
        return (width - box_w) / 2, 0.0, box_w, box_h

    top = max(0, min(rows))
    bottom = min(sample_bottom, max(rows) + 4)
    cols: list[int] = []
    row_start = int(top)
    row_end = int(bottom)
    row_count = max(1, (row_end - row_start) // 4)
    for x in range(0, width, 4):
        content_pixels = 0
        for y in range(row_start, row_end, 4):
            if is_content_pixel(rgb.getpixel((x, y))):
                content_pixels += 1
        if content_pixels >= max(8, row_count // 20):
            cols.append(x)

    if not cols:
        scale = min(width / render_box["width"], height / render_box["height"], 1.0)
        box_w = render_box["width"] * scale
        box_h = render_box["height"] * scale
        return (width - box_w) / 2, float(top), box_w, box_h

    left = max(0, min(cols))
    right = min(width, max(cols) + 4)
    detected_w = float(right - left)
    detected_h = float(bottom - top)
    render_w = min(float(width), render_box["width"])
    render_h = min(float(sample_bottom - top), render_box["height"])

    # The public prototype screenshot sometimes includes labels or page chrome
    # in the non-black bounds. The Figma render box is the more reliable design
    # size, so keep the detected top-left but snap suspicious dimensions back to
    # the Figma size.
    if detected_w > render_box["width"] * 1.1 or detected_w < render_box["width"] * 0.8:
        detected_w = render_w
    if detected_h > render_box["height"] * 1.1 or detected_h < render_box["height"] * 0.8:
        detected_h = render_h

    if left + detected_w > width:
        left = max(0.0, width - detected_w)
    return float(left), float(top), float(detected_w), float(detected_h)


def _target_rect_and_design_bounds_on_real_screenshot(
    *,
    image: "Image.Image",
    artifact: dict[str, object],
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
] | None:
    mapping = _box_from_mapping(artifact)
    if mapping is None:
        return None
    target_box, render_box = mapping
    design_bounds = _find_design_bounds(image, render_box)
    design_x, design_y, design_w, design_h = design_bounds
    scale_x = design_w / render_box["width"]
    scale_y = design_h / render_box["height"]
    x = design_x + (target_box["x"] - render_box["x"]) * scale_x
    y = design_y + (target_box["y"] - render_box["y"]) * scale_y
    w = target_box["width"] * scale_x
    h = target_box["height"] * scale_y
    if x < 0 or y < 0 or x + w > image.width or y + h > image.height:
        return None
    return (x, y, w, h), design_bounds


def _figma_box_to_real_rect(
    *,
    image: "Image.Image",
    artifact: dict[str, object],
    figma_box: dict[str, float],
    design_bounds: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float] | None:
    render_box = _box_from_artifact_key(artifact, "figma_render_bounding_box")
    if render_box is None:
        return None
    if design_bounds is None:
        design_bounds = _find_design_bounds(image, render_box)
    design_x, design_y, design_w, design_h = design_bounds
    scale_x = design_w / render_box["width"]
    scale_y = design_h / render_box["height"]
    x = design_x + (figma_box["x"] - render_box["x"]) * scale_x
    y = design_y + (figma_box["y"] - render_box["y"]) * scale_y
    width = figma_box["width"] * scale_x
    height = figma_box["height"] * scale_y
    if x + width < 0 or y + height < 0 or x > image.width or y > image.height:
        return None
    return (
        max(0.0, x),
        max(0.0, y),
        min(float(image.width), x + width) - max(0.0, x),
        min(float(image.height), y + height) - max(0.0, y),
    )


def _expand_phone_rect_to_figma_width(
    *,
    image: "Image.Image",
    phone_rect: tuple[float, float, float, float],
    phone_box: dict[str, float],
    design_bounds: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float]:
    x, y, width, height = phone_rect
    figma_width = phone_box.get("width")
    figma_height = phone_box.get("height")
    if (
        not isinstance(figma_width, (int, float))
        or not isinstance(figma_height, (int, float))
        or figma_width <= 0
        or figma_height <= 0
        or height <= 0
    ):
        return phone_rect

    desired_width = height * (float(figma_width) / float(figma_height))
    if desired_width <= width * 1.08:
        return phone_rect

    if design_bounds is None:
        min_x = 0.0
        max_x = float(image.width)
    else:
        min_x, _min_y, bounds_w, _bounds_h = design_bounds
        max_x = min(float(image.width), min_x + bounds_w)

    available_width = max(0.0, max_x - min_x)
    if available_width <= width:
        return phone_rect

    desired_width = min(desired_width, available_width)
    left = max(min_x, min(max_x - desired_width, x))
    return (left, y, desired_width, height)


def _target_rect_on_real_screenshot(
    *,
    image: "Image.Image",
    artifact: dict[str, object],
) -> tuple[float, float, float, float] | None:
    result = _target_rect_and_design_bounds_on_real_screenshot(image=image, artifact=artifact)
    if result is None:
        return None
    target_rect, _ = result
    return target_rect


def _draw_rounded_rect_callout(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
) -> "Image.Image":
    annotated = image.convert("RGBA")
    draw = ImageDraw.Draw(annotated)
    x, y, width, height = target_rect
    pad_x = max(7.0, min(18.0, width * 0.18))
    pad_y = max(5.0, min(14.0, height * 0.38))
    left = max(0.0, x - pad_x)
    top = max(0.0, y - pad_y)
    right = min(float(annotated.width - 1), x + width + pad_x)
    bottom = min(float(annotated.height - 1), y + height + pad_y)
    if right <= left or bottom <= top:
        return annotated.convert("RGB")
    stroke_width = max(3, round(min(annotated.size) / 220))
    radius = max(8, min(18, round(min(right - left, bottom - top) * 0.28)))

    draw.rounded_rectangle(
        [left, top, right, bottom],
        radius=radius,
        outline=(229, 57, 53, 255),
        width=stroke_width,
    )
    return annotated.convert("RGB")


def _crop_around_target(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float] | None = None,
) -> tuple["Image.Image", tuple[float, float, float, float]]:
    x, y, width, height = target_rect
    cx = x + width / 2
    cy = y + height / 2
    if bounds is None:
        min_x = 0.0
        min_y = 0.0
        max_w = float(image.width)
        max_h = float(image.height)
    else:
        min_x, min_y, max_w, max_h = bounds
        max_w = min(max_w, float(image.width) - min_x)
        max_h = min(max_h, float(image.height) - min_y)

    crop_w = min(max_w, max(620.0, min(1400.0, width * 9.0 + 520.0)))
    crop_h = min(max_h, max(460.0, min(1100.0, height * 10.0 + 430.0)))

    left = max(min_x, min(min_x + max_w - crop_w, cx - crop_w / 2))
    top = max(min_y, min(min_y + max_h - crop_h, cy - crop_h / 2))
    right = left + crop_w
    bottom = top + crop_h
    crop = image.crop((round(left), round(top), round(right), round(bottom)))
    shifted = (x - left, y - top, width, height)
    return _trim_background_edges(crop, shifted)


def _crop_phone_context_around_target(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
    phone_rect: tuple[float, float, float, float] | None,
    fallback_bounds: tuple[float, float, float, float] | None,
) -> tuple["Image.Image", tuple[float, float, float, float]]:
    if phone_rect is None:
        return _crop_around_target(image, target_rect, fallback_bounds)

    x, y, width, height = target_rect
    phone_x, phone_y, phone_w, phone_h = phone_rect
    if phone_w <= 0 or phone_h <= 0:
        return _crop_around_target(image, target_rect, fallback_bounds)

    crop_w = phone_w
    crop_h = phone_h

    cx = x + width / 2
    cy = y + height / 2
    left = max(phone_x, min(phone_x + phone_w - crop_w, cx - crop_w / 2))
    top = max(phone_y, min(phone_y + phone_h - crop_h, cy - crop_h / 2))
    right = left + crop_w
    bottom = top + crop_h
    crop = image.crop((round(left), round(top), round(right), round(bottom)))
    shifted = (x - left, y - top, width, height)
    return crop, shifted


def _dominant_edge_color(image: "Image.Image") -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width <= 0 or height <= 0:
        return (0, 0, 0)
    step = max(1, min(width, height) // 80)
    samples: list[tuple[int, int, int]] = []
    for x in range(0, width, step):
        samples.append(rgb.getpixel((x, 0)))
        samples.append(rgb.getpixel((x, height - 1)))
    for y in range(0, height, step):
        samples.append(rgb.getpixel((0, y)))
        samples.append(rgb.getpixel((width - 1, y)))
    buckets: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
    for sample in samples:
        bucket = tuple(channel // 16 for channel in sample)
        buckets.setdefault(bucket, []).append(sample)
    dominant = max(buckets.values(), key=len)
    return tuple(round(sum(sample[index] for sample in dominant) / len(dominant)) for index in range(3))


def _is_background_pixel(pixel: tuple[int, int, int], background: tuple[int, int, int]) -> bool:
    return max(abs(pixel[index] - background[index]) for index in range(3)) <= 16


def _trim_background_edges(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
) -> tuple["Image.Image", tuple[float, float, float, float]]:
    if image.width < 80 or image.height < 80:
        return image, target_rect

    rgb = image.convert("RGB")
    background = _dominant_edge_color(rgb)
    rows: list[int] = []
    cols: list[int] = []
    sample_step = 3
    row_threshold = max(3, image.width // 120)
    col_threshold = max(3, image.height // 120)

    for y in range(0, image.height, sample_step):
        content_pixels = 0
        for x in range(0, image.width, sample_step):
            if not _is_background_pixel(rgb.getpixel((x, y)), background):
                content_pixels += 1
        if content_pixels >= row_threshold:
            rows.append(y)

    for x in range(0, image.width, sample_step):
        content_pixels = 0
        for y in range(0, image.height, sample_step):
            if not _is_background_pixel(rgb.getpixel((x, y)), background):
                content_pixels += 1
        if content_pixels >= col_threshold:
            cols.append(x)

    if not rows or not cols:
        return image, target_rect

    x, y, width, height = target_rect
    pad = 26
    left = max(0, min(min(cols), math.floor(x)) - pad)
    top = max(0, min(min(rows), math.floor(y)) - pad)
    right = min(image.width, max(max(cols) + sample_step, math.ceil(x + width)) + pad)
    bottom = min(image.height, max(max(rows) + sample_step, math.ceil(y + height)) + pad)

    if right - left < 80 or bottom - top < 80:
        return image, target_rect
    if left <= 2 and top <= 2 and right >= image.width - 2 and bottom >= image.height - 2:
        return image, target_rect

    trimmed = image.crop((left, top, right, bottom))
    shifted = (x - left, y - top, width, height)
    return trimmed, shifted


PIXEL_SNAPPED_DETECTORS = {
    "low_text_contrast",
    "small_text_readability",
    "generic_navigation_label",
    "placeholder_or_generic_copy",
}


def _rgb_triplet(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    channels: list[float] = []
    for item in value[:3]:
        if not isinstance(item, (int, float)):
            return None
        channels.append(float(item))
    if max(channels) <= 1.0:
        channels = [channel * 255 for channel in channels]
    rgb = tuple(max(0, min(255, round(channel))) for channel in channels)
    return rgb if len(rgb) == 3 else None


def _expected_target_rgb(
    issue: dict[str, object],
    artifact: dict[str, object],
) -> tuple[int, int, int] | None:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    detector_id = str(evidence.get("detector_id") or "")
    if detector_id not in PIXEL_SNAPPED_DETECTORS:
        return None

    validation = (
        artifact.get("client_view_validation")
        if isinstance(artifact.get("client_view_validation"), dict)
        else {}
    )
    candidates = (
        artifact.get("target_fill_rgb"),
        validation.get("expected_fill_rgb") if isinstance(validation, dict) else None,
        evidence.get("resolved_foreground_rgb"),
        evidence.get("foreground_rgba"),
    )
    for candidate in candidates:
        rgb = _rgb_triplet(candidate)
        if rgb is not None:
            return rgb
    return None


def _expected_color_threshold(expected_rgb: tuple[int, int, int]) -> int:
    if max(expected_rgb) - min(expected_rgb) < 24:
        return 92
    return 112


def _pixel_matches_expected_rgb(
    pixel: tuple[int, int, int],
    expected_rgb: tuple[int, int, int],
) -> bool:
    distance = sum(abs(int(pixel[index]) - expected_rgb[index]) for index in range(3))
    if distance > _expected_color_threshold(expected_rgb):
        return False

    expected_is_neutral = max(expected_rgb) - min(expected_rgb) < 24
    if not expected_is_neutral:
        return True

    pixel_spread = max(pixel) - min(pixel)
    if min(expected_rgb) >= 220:
        return min(pixel) >= 205 and pixel_spread <= 64
    if max(expected_rgb) <= 36:
        return max(pixel) <= 78 and pixel_spread <= 58
    return pixel_spread <= 48


def _integral_expected_color_mask(
    image: "Image.Image",
    expected_rgb: tuple[int, int, int],
) -> list[list[int]]:
    return _integral_from_mask(_expected_color_mask(image, expected_rgb))


def _expected_color_mask(
    image: "Image.Image",
    expected_rgb: tuple[int, int, int],
    *,
    target_width: float | None = None,
    target_height: float | None = None,
) -> list[bytearray]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    mask = [bytearray(width) for _ in range(height)]
    for y in range(height):
        for x in range(width):
            pixel = rgb.getpixel((x, y))
            if _pixel_matches_expected_rgb(pixel, expected_rgb):
                mask[y][x] = 1
    if target_width is not None and target_height is not None:
        return _remove_oversized_mask_components(mask, target_width, target_height)
    return mask


def _integral_from_mask(mask: list[bytearray]) -> list[list[int]]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    integral = [[0] * (width + 1) for _ in range(height + 1)]
    for y, row in enumerate(mask):
        row_total = 0
        for x, value in enumerate(row):
            row_total += 1 if value else 0
            integral[y + 1][x + 1] = integral[y][x + 1] + row_total
    return integral


def _remove_oversized_mask_components(
    mask: list[bytearray],
    target_width: float,
    target_height: float,
) -> list[bytearray]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    if width <= 0 or height <= 0:
        return mask

    visited = [bytearray(width) for _ in range(height)]
    filtered = [bytearray(width) for _ in range(height)]
    max_component_w = max(target_width * 3.2, target_width + 40.0)
    max_component_h = max(target_height * 2.1, target_height + 12.0)
    max_component_area = max(36.0, target_width * target_height * 10.0)

    for start_y in range(height):
        for start_x in range(width):
            if visited[start_y][start_x] or not mask[start_y][start_x]:
                continue
            stack = [(start_x, start_y)]
            visited[start_y][start_x] = 1
            pixels: list[tuple[int, int]] = []
            min_x = max_x = start_x
            min_y = max_y = start_y
            while stack:
                x, y = stack.pop()
                pixels.append((x, y))
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for next_y in range(max(0, y - 1), min(height, y + 2)):
                    for next_x in range(max(0, x - 1), min(width, x + 2)):
                        if visited[next_y][next_x] or not mask[next_y][next_x]:
                            continue
                        visited[next_y][next_x] = 1
                        stack.append((next_x, next_y))

            component_w = max_x - min_x + 1
            component_h = max_y - min_y + 1
            component_area = component_w * component_h
            density = len(pixels) / max(1, component_area)
            oversized = (
                component_w > max_component_w
                or component_h > max_component_h
                or component_area > max_component_area
            )
            solid_control = density > 0.86 and (
                component_w > max(18.0, target_width * 0.65)
                or component_h > max(16.0, target_height * 1.8)
            )
            if oversized or solid_control:
                continue
            for x, y in pixels:
                filtered[y][x] = 1

    return filtered


def _mask_sum(
    integral: list[list[int]],
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> int:
    return (
        integral[bottom][right]
        - integral[top][right]
        - integral[bottom][left]
        + integral[top][left]
    )


def _expected_color_bbox(
    image: "Image.Image",
    expected_rgb: tuple[int, int, int],
    window: tuple[int, int, int, int],
) -> tuple[float, float, float, float] | None:
    rgb = image.convert("RGB")
    left, top, right, bottom = window
    min_x = right
    min_y = bottom
    max_x = left
    max_y = top
    matches = 0
    for y in range(top, bottom):
        for x in range(left, right):
            pixel = rgb.getpixel((x, y))
            if _pixel_matches_expected_rgb(pixel, expected_rgb):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                matches += 1
    if matches < 4 or max_x < min_x or max_y < min_y:
        return None
    return float(min_x), float(min_y), float(max_x - min_x + 1), float(max_y - min_y + 1)


def _expected_color_bbox_from_mask(
    mask: list[bytearray],
    window: tuple[int, int, int, int],
) -> tuple[float, float, float, float] | None:
    left, top, right, bottom = window
    min_x = right
    min_y = bottom
    max_x = left
    max_y = top
    matches = 0
    for y in range(top, bottom):
        row = mask[y]
        for x in range(left, right):
            if not row[x]:
                continue
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
            matches += 1
    if matches < 4 or max_x < min_x or max_y < min_y:
        return None
    return float(min_x), float(min_y), float(max_x - min_x + 1), float(max_y - min_y + 1)


def _snap_rect_to_visible_pixels(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
    expected_rgb: tuple[int, int, int] | None,
) -> tuple[float, float, float, float]:
    if expected_rgb is None or image.width < 24 or image.height < 24:
        return target_rect

    x, y, width, height = target_rect
    if width <= 0 or height <= 0:
        return target_rect

    mask = _expected_color_mask(
        image,
        expected_rgb,
        target_width=width,
        target_height=height,
    )
    integral = _integral_from_mask(mask)
    window_w = max(24, min(image.width, round(width * 2.4 + 30)))
    window_h = max(18, min(image.height, round(height * 2.8 + 24)))
    step = max(2, min(8, round(min(window_w, window_h) / 7)))
    pred_cx = x + width / 2
    pred_cy = y + height / 2
    max_distance = max(1.0, math.hypot(image.width, image.height))
    neutral_expected = max(expected_rgb) - min(expected_rgb) < 24
    ideal_ratio = 0.11 if neutral_expected else 0.18
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []

    max_left = max(0, image.width - window_w)
    max_top = max(0, image.height - window_h)
    for top in range(0, max_top + 1, step):
        for left in range(0, max_left + 1, step):
            right = left + window_w
            bottom = top + window_h
            match_count = _mask_sum(integral, left, top, right, bottom)
            area = window_w * window_h
            ratio = match_count / area if area else 0.0
            if match_count < max(5, area * 0.006):
                continue
            if neutral_expected and ratio > 0.48:
                continue
            if not neutral_expected and ratio > 0.7:
                continue
            cx = left + window_w / 2
            cy = top + window_h / 2
            distance = math.hypot(cx - pred_cx, cy - pred_cy)
            ratio_score = max(0.0, 1.0 - abs(ratio - ideal_ratio) / ideal_ratio)
            proximity_score = max(0.0, 1.0 - distance / max_distance)
            score = match_count + ratio_score * 130.0 + proximity_score * 80.0
            candidates.append((score, (left, top, right, bottom)))

    if not candidates:
        return target_rect

    max_bbox_w = max(width * 3.2, width + 40.0)
    max_bbox_h = max(height * 2.1, height + 12.0)
    min_bbox_w = max(3.0, width * 0.18)
    min_bbox_h = max(2.0, height * 0.18)
    target_aspect = width / max(1.0, height)
    for _, window in sorted(candidates, key=lambda item: item[0], reverse=True):
        bbox = _expected_color_bbox_from_mask(mask, window)
        if bbox is None:
            continue
        bx, by, bw, bh = bbox
        if bw < min_bbox_w or bh < min_bbox_h:
            continue
        if target_aspect >= 2.0 and (bw / max(1.0, bh)) < max(1.8, target_aspect * 0.65):
            continue
        if neutral_expected and (bw > max_bbox_w or bh > max_bbox_h):
            continue
        if neutral_expected and (bw * bh) / max(1.0, image.width * image.height) > 0.18:
            continue
        bbox_left = max(0, math.floor(bx))
        bbox_top = max(0, math.floor(by))
        bbox_right = min(image.width, math.ceil(bx + bw))
        bbox_bottom = min(image.height, math.ceil(by + bh))
        bbox_area = max(1, (bbox_right - bbox_left) * (bbox_bottom - bbox_top))
        bbox_density = _mask_sum(
            integral,
            bbox_left,
            bbox_top,
            bbox_right,
            bbox_bottom,
        ) / bbox_area
        if neutral_expected and bbox_density > 0.72:
            continue
        return bbox

    return target_rect


def _snap_rect_inside_bounds(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float] | None,
    expected_rgb: tuple[int, int, int] | None,
) -> tuple[float, float, float, float]:
    if expected_rgb is None or bounds is None:
        return target_rect

    bounds_x, bounds_y, bounds_w, bounds_h = bounds
    if bounds_w < 24 or bounds_h < 24:
        return target_rect
    left = max(0, round(bounds_x))
    top = max(0, round(bounds_y))
    right = min(image.width, round(bounds_x + bounds_w))
    bottom = min(image.height, round(bounds_y + bounds_h))
    if right - left < 24 or bottom - top < 24:
        return target_rect

    x, y, width, height = target_rect
    local_rect = (x - left, y - top, width, height)
    search_image = image.crop((left, top, right, bottom))
    snapped = _snap_rect_to_visible_pixels(search_image, local_rect, expected_rgb)
    return (snapped[0] + left, snapped[1] + top, snapped[2], snapped[3])


def _same_rect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return all(abs(first[index] - second[index]) <= 0.01 for index in range(4))


def _target_failed_client_visibility(artifact: dict[str, object]) -> bool:
    validation = (
        artifact.get("client_view_validation")
        if isinstance(artifact.get("client_view_validation"), dict)
        else {}
    )
    reason = str(validation.get("rejected_reason") or "") if isinstance(validation, dict) else ""
    return reason in {
        "target_not_visibly_rendered",
        "rendered_text_color_not_found_at_target",
        "rendered_text_foreground_fills_target",
        "rendered_text_target_contains_unexpected_color",
        "rendered_target_does_not_match_outlier_fill",
        "target_could_not_be_mapped",
        "nearby_region_has_no_visible_detail",
    }


def _issue_screenshot_context(
    issue: dict[str, object],
    *,
    report_path: Path | None = None,
    include_locator_fallback: bool = False,
) -> tuple[
    "Image.Image",
    tuple[float, float, float, float],
    tuple[float, float, float, float] | None,
    tuple[float, float, float, float] | None,
    str,
    dict[str, object],
    tuple[int, int, int] | None,
] | None:
    if Image is None:
        return None

    artifact = _first_visual_artifact(
        issue,
        include_locator_fallback=include_locator_fallback,
    )
    if artifact is None:
        if report_path is not None:
            return _fallback_screenshot_context(issue, report_path)
        return None

    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    source_path = evidence.get("real_page_screenshot_path")
    source_kind = "real"
    target_rect: tuple[float, float, float, float] | None = None
    crop_bounds: tuple[float, float, float, float] | None = None
    phone_rect: tuple[float, float, float, float] | None = None

    if isinstance(source_path, str) and Path(source_path).exists():
        image = Image.open(source_path).convert("RGB")
        real_target = _target_rect_and_design_bounds_on_real_screenshot(image=image, artifact=artifact)
        if real_target is not None:
            target_rect, crop_bounds = real_target
            phone_box = _box_from_artifact_key(artifact, "figma_phone_context_bounding_box")
            if phone_box is not None and _is_phone_like_figma_box(phone_box):
                phone_rect = _figma_box_to_real_rect(
                    image=image,
                    artifact=artifact,
                    figma_box=phone_box,
                    design_bounds=crop_bounds,
                )
                if phone_rect is not None:
                    phone_rect = _expand_phone_rect_to_figma_width(
                        image=image,
                        phone_rect=phone_rect,
                        phone_box=phone_box,
                        design_bounds=crop_bounds,
                    )
    else:
        source_path = str(artifact.get("image_path") or "")
        source_kind = "fallback"
        if not source_path or not Path(source_path).exists():
            return None
        image = Image.open(source_path).convert("RGB")
        target_rect = _rectangle_from_artifact(artifact)

    if target_rect is None:
        return None

    expected_rgb = _expected_target_rgb(issue, artifact)
    if expected_rgb is not None and _target_failed_client_visibility(artifact):
        return None

    raw_target_rect = target_rect
    target_rect = _snap_rect_inside_bounds(
        image,
        target_rect,
        phone_rect or crop_bounds,
        expected_rgb,
    )
    if (
        expected_rgb is not None
        and _same_rect(target_rect, raw_target_rect)
        and _target_failed_client_visibility(artifact)
    ):
        return None

    return image, target_rect, crop_bounds, phone_rect, source_kind, artifact, expected_rgb


def _callout_image(issue: dict[str, object], report_path: Path) -> dict[str, str] | None:
    if Image is None or ImageDraw is None:
        return None

    primary_artifact = _first_visual_artifact(issue)
    context = None
    if (
        isinstance(primary_artifact, dict)
        and primary_artifact.get("type") == "real_page_geometry_screenshot"
    ):
        cached_context = _fallback_screenshot_context(issue, report_path)
        if cached_context is not None and cached_context[4] == "render_cache_fallback":
            context = cached_context
    if context is None:
        context = _issue_screenshot_context(
            issue,
            report_path=report_path,
        )
    if context is None:
        return None

    image, target_rect, crop_bounds, phone_rect, source_kind, artifact, expected_rgb = context
    if _is_context_only_artifact(artifact):
        return None
    crop, shifted_rect = _crop_phone_context_around_target(
        image,
        target_rect,
        phone_rect,
        crop_bounds,
    )
    shifted_rect = _snap_rect_to_visible_pixels(
        crop,
        shifted_rect,
        expected_rgb,
    )
    annotated = _draw_rounded_rect_callout(crop, shifted_rect)
    issue_id = _safe_asset_name(str(issue.get("id") or "issue"))
    output_dir = report_path.parent / "callouts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{issue_id}-callout.png"
    annotated.save(output_path)
    label = (
        "Problem area highlighted"
        if source_kind in {"real", "report_fallback"}
        else "Rendered problem area highlighted"
    )
    return {
        "src": _image_src(str(output_path), report_path),
        "label": label,
        "kind": "callout" if source_kind in {"real", "report_fallback"} else "fallback_callout",
    }


def _unmarked_context_image(issue: dict[str, object], report_path: Path) -> dict[str, str] | None:
    if Image is None:
        return None

    context = _issue_screenshot_context(issue, report_path=report_path)
    if context is None:
        return None

    image, target_rect, crop_bounds, phone_rect, source_kind, _artifact, _expected_rgb = context
    crop, _shifted_rect = _crop_phone_context_around_target(
        image,
        target_rect,
        phone_rect,
        crop_bounds,
    )
    issue_id = _safe_asset_name(str(issue.get("id") or "issue"))
    output_dir = report_path.parent / "context_views"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{issue_id}-context.png"
    crop.save(output_path)
    label = "Unmarked client-view context" if source_kind == "real" else "Unmarked fallback context"
    return {
        "src": _image_src(str(output_path), report_path),
        "label": label,
        "kind": "context_view",
    }


def _page_scan_image(issue: dict[str, object], report_path: Path) -> dict[str, str] | None:
    if Image is None:
        return None

    context = _issue_screenshot_context(issue, report_path=report_path)
    if context is None:
        return None

    image, target_rect, crop_bounds, _phone_rect, source_kind, _artifact, _expected_rgb = context
    if crop_bounds is None:
        return None

    left, design_top, design_width, design_height = crop_bounds
    if design_width <= 0 or design_height <= 0:
        return None

    crop_height = max(1.0, design_height * 0.5)
    target_center_y = target_rect[1] + target_rect[3] / 2
    top = max(design_top, min(design_top + design_height - crop_height, target_center_y - crop_height / 2))
    right = min(float(image.width), left + design_width)
    bottom = min(float(image.height), top + crop_height)
    if right <= left or bottom <= top:
        return None

    cropped = image.crop((round(left), round(top), round(right), round(bottom)))
    issue_id = _safe_asset_name(str(issue.get("id") or "issue"))
    output_dir = report_path.parent / "page_views"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{issue_id}-page-scan.png"
    cropped.save(output_path)
    label = "Full-width half-height page screenshot" if source_kind == "real" else "Fallback page screenshot"
    return {
        "src": _image_src(str(output_path), report_path),
        "label": label,
        "kind": "page_scan",
    }


def _client_context_artifact(issue: dict[str, object]) -> dict[str, object] | None:
    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    for artifact in visual_evidence:
        if (
            isinstance(artifact, dict)
            and artifact.get("type") == "real_page_geometry_screenshot"
            and artifact.get("image_path")
        ):
            return artifact
    return None


def _client_view_image(issue: dict[str, object], report_path: Path) -> dict[str, str] | None:
    if Image is None:
        return None
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    source_path = str(evidence.get("real_page_screenshot_path") or "")
    if not source_path or not Path(source_path).exists():
        artifact = _client_context_artifact(issue)
        source_path = str((artifact or {}).get("image_path") or "")
    else:
        artifact = _client_context_artifact(issue)
    if not source_path or not Path(source_path).exists():
        return None

    image = Image.open(source_path).convert("RGB")
    render_box = None
    if isinstance(artifact, dict):
        candidate = artifact.get("figma_render_bounding_box")
        if isinstance(candidate, dict) and all(
            isinstance(candidate.get(key), (int, float))
            for key in ("x", "y", "width", "height")
        ):
            render_box = {key: float(candidate[key]) for key in ("x", "y", "width", "height")}

    if render_box is not None:
        left, top, width, height = _find_design_bounds(image, render_box)
        cropped = image.crop(
            (
                round(left),
                round(top),
                round(left + width),
                round(top + height),
            )
        )
    else:
        cropped = image

    issue_id = _safe_asset_name(str(issue.get("id") or "issue"))
    output_dir = report_path.parent / "client_views"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{issue_id}-client-view.png"
    cropped.save(output_path)
    return {
        "src": _image_src(str(output_path), report_path),
        "label": "Client-view screenshot",
        "kind": "client_view",
    }


def _evidence_images(
    issue: dict[str, object],
    report_path: Path,
    *,
    include_locator_fallback: bool = False,
) -> list[dict[str, str]]:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    images: list[dict[str, str]] = []

    real_page_path = evidence.get("real_page_screenshot_path")
    if isinstance(real_page_path, str) and real_page_path:
        client_view = _client_view_image(issue, report_path)
        if client_view is not None:
            return [client_view]
        images.append(
            {
                "src": _image_src(real_page_path, report_path),
                "label": _clean_text(evidence.get("real_page_screenshot_node_name") or "Whole Figma page"),
                "kind": "whole_page",
            }
        )
        return images

    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    for artifact in visual_evidence:
        if not isinstance(artifact, dict):
            continue
        artifact_type = str(artifact.get("type") or "")
        if not include_locator_fallback and artifact_type.endswith("_locator"):
            continue
        image_path = str(artifact.get("image_path") or "")
        if not image_path:
            continue
        images.append(
            {
                "src": _image_src(image_path, report_path),
                "label": _clean_text(Path(image_path).name),
                "kind": artifact_type or "screenshot",
            }
        )
    return images


def _image_frame(issue: dict[str, object], report_path: Path) -> str:
    issue_id = str(issue.get("id") or "issue")
    callout = _callout_image(issue, report_path)
    images = [callout] if callout else _evidence_images(
        issue,
        report_path,
    )
    if not images or images[0] is None:
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        if evidence.get("client_visibility"):
            return """
            <div class="story-visual-empty">
              <strong>No trusted client-view evidence</strong>
              <span>This finding is hidden from the client issue list until a real screenshot confirms the visible problem.</span>
            </div>
            """
        return """
        <div class="story-visual-empty">
          <strong>No rendered screenshot yet</strong>
          <span>Run detection with Figma-rendered annotations, then rebuild the report.</span>
        </div>
        """

    image = images[0]
    is_callout = image["kind"] in {"callout", "fallback_callout", "locator_callout"}
    is_locator_callout = image["kind"] == "locator_callout"
    is_fallback_callout = image["kind"] == "fallback_callout"
    is_whole_page = image["kind"] == "whole_page"
    is_locator = image["kind"].endswith("_locator")
    is_client_view = image["kind"] in {"client_view", *CLIENT_VISIBLE_EVIDENCE_TYPES}
    badge = (
        "Static Figma locator with marker"
        if is_locator_callout
        else (
            "Problem area highlighted"
            if is_fallback_callout
            else "Problem area highlighted"
        )
        if is_callout
        else (
            "Client-view screenshot"
            if is_client_view
            else (
                "Static Figma locator"
                if is_locator
                else ("Whole page evidence" if is_whole_page else "Supporting visual evidence")
            )
        )
    )
    note = (
        "This is not a rendered client screenshot; the red rectangle marks the detected Figma node."
        if is_locator_callout
        else (
            "The rounded rectangle marks the detected area on the rendered design screenshot."
            if is_fallback_callout
            else "The rounded rectangle marks the exact area that needs attention."
        )
        if is_callout
        else (
            "Captured from the client-view screenshot."
            if is_client_view
            else (
                "This is not a rendered client screenshot; it locates the detected Figma node for review."
                if is_locator
                else (
                    "Captured from the public Figma page/prototype."
                    if is_whole_page
                    else "Supporting visual context; review the static finding before treating it as final."
                )
            )
        )
    )
    return f"""
    <figure class="story-visual-frame" data-issue-id="{_html(issue_id)}">
      <input class="hidden-file-input" type="file" accept="image/*" data-screenshot-input data-issue-id="{_html(issue_id)}" data-local-edit-control>
      <div class="desktop-screen">
        <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
        <div class="desktop-screen-body">
          <img src="{_html(image['src'])}" alt="{_html(image['label'])}" data-editable-image="{_html(issue_id)}">
        </div>
      </div>
      <figcaption><strong>{_html(badge)}</strong><span>{_html(note)}</span></figcaption>
    </figure>
    """


def _representative_pages(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
    report_path: Path,
    polished_copy: dict[str, dict[str, str]],
) -> str:
    seen: set[str] = set()
    cards: list[str] = []
    actionable_issues = _actionable_issues(detection_result, grouped)
    large_page_nodes = _large_page_nodes(report_path)
    for page_node in large_page_nodes:
        image_path = _context_screenshot_for_node(page_node, report_path)
        if image_path is None:
            continue
        src = _image_src(str(image_path), report_path)
        if src in seen:
            continue
        seen.add(src)
        title = _clean_text(page_node.get("name") or "") or _real_page_title(image_path)
        cards.append(
            f"""
            <a class="scan-card" href="{_html(src)}" target="_blank" rel="noreferrer">
              <div class="scan-screen">
                <img src="{_html(src)}" alt="{_html(title)}">
              </div>
              <span>{_html("Full-page screenshot")}</span>
              <strong>{_html(title)}</strong>
              <em>{_html("Unmarked page screenshot")}</em>
            </a>
            """
        )
        if len(cards) >= 11:
            break

    if not cards:
        for issue in actionable_issues:
            image = _page_scan_image(issue, report_path) or _client_view_image(issue, report_path)
            if image is None:
                continue
            src = image["src"]
            if src in seen:
                continue
            seen.add(src)
            issue_id = str(issue.get("id") or "")
            title = _polished_value(polished_copy, issue, "title", _human_issue_title(issue))
            cards.append(
                f"""
                <a class="scan-card" href="{_html(src)}" target="_blank" rel="noreferrer">
                  <div class="scan-screen">
                    <img src="{_html(src)}" alt="{_html(title)}" data-editable-image="{_html(issue_id)}">
                  </div>
                  <span>{_html("Full-page screenshot")}</span>
                  <strong>{_html(title)}</strong>
                  <em>{_html("Full width, half-height page view")}</em>
                </a>
                """
            )
            if len(cards) >= 11:
                break

    if len(cards) < 11 and not actionable_issues and not large_page_nodes:
        for image_path in _real_page_screenshot_files(report_path):
            src = _image_src(str(image_path), report_path)
            if src in seen:
                continue
            seen.add(src)
            title = _real_page_title(image_path)
            cards.append(
                f"""
                <a class="scan-card" href="{_html(src)}" target="_blank" rel="noreferrer">
                  <div class="scan-screen">
                    <img src="{_html(src)}" alt="{_html(title)}">
                  </div>
                  <span>{_html("Public prototype screenshot")}</span>
                  <strong>{_html(title)}</strong>
                  <em>{_html("Unmarked page screenshot")}</em>
                </a>
                """
            )
            if len(cards) >= 11:
                break

    if not cards:
        has_issue_level_findings = bool(actionable_issues)
        empty_title = (
            "No verified full-page screenshots"
            if has_issue_level_findings
            else "No issue screenshots were verified"
        )
        empty_note = (
            "Issue findings below use rendered annotations or saved screen evidence where available."
            if has_issue_level_findings
            else "No client-facing issue is drawn until screenshot evidence is trusted."
        )
        cards.append(
            f"""
            <div class="scan-card scan-empty">
              <div class="scan-screen"><div>No verified screenshots</div></div>
              <span>Evidence guarded</span>
              <strong>{_html(empty_title)}</strong>
              <em>{_html(empty_note)}</em>
            </div>
            """
        )

    rendered_cards = "".join(cards)
    return f"""
    <section class="section-panel" id="evidence">
      <div class="section-head">
        <div>
          <p class="eyebrow">Pages Scanned</p>
          <h2>Representative screen screenshots captured during the audit</h2>
        </div>
        <p>These screenshots are intentionally unmarked client-view screens. The report keeps up to 11 representative screens so each major app screen can be reviewed before the exact issue markers below.</p>
      </div>
      <div class="scan-marquee">
        <div class="scan-strip">
          <div class="scan-track">{rendered_cards}</div>
        </div>
      </div>
    </section>
    """


def _methodology_section() -> str:
    steps = [
        (
            "01",
            "Extract",
            "The pipeline reads the Figma structure, node metadata, text, fills, geometry, component context, and available rendered evidence.",
        ),
        (
            "02",
            "Detect",
            "The audit converts visible design signals into UX/UI issues with confidence, screenshots, and plain-language evidence.",
        ),
        (
            "03",
            "Prioritize",
            "The report groups the issues into visible UX/UI axes and turns the highest-risk findings into concrete recommendations.",
        ),
        (
            "04",
            "Prove visibility",
            "Verified client screenshots are strongest, while rendered Figma annotations and saved context images are labeled by evidence type. Hidden, developer-only, and screenshot-disproved issues are removed.",
        ),
        (
            "05",
            "Align marker and copy",
            "Exact issues get a marker only after the target maps to the screenshot. Findings without a trusted marker are removed from the client issue list.",
        ),
        (
            "06",
            "Keep scores realistic",
            "Good axes stay out of the issue list, repeated visible problems lower the score, and disproved findings do not create client-facing risk.",
        ),
    ]
    cards = "\n".join(
        f"""
        <article class="method-card">
          <span class="floating-step">{number}</span>
          <h4>{_html(title)}</h4>
          <p>{_html(copy)}</p>
        </article>
        """
        for number, title, copy in steps
    )
    return f"""
    <section class="section-panel methodology-section" id="methodology">
      <div class="section-head">
        <p class="eyebrow">Methodology</p>
        <h2>Structured evidence gates for evaluating the Figma user experience</h2>
        <p>The audit is designed to show only findings that a client can connect to the visible screen: evidence, marker, wording, axis, and score must agree.</p>
      </div>
      <div class="method-grid">{cards}</div>
    </section>
    """


def _criteria_matrix_section(lookup: dict[str, UxUiCriterion]) -> str:
    method_labels = {
        "rule": "rule-based",
        "ai_assisted": "AI-assisted",
        "human_review": "human-review",
    }
    grouped = checks_by_axis()
    cards: list[str] = []
    for criterion_id, checks in grouped.items():
        criterion = lookup.get(criterion_id)
        title = criterion.short_name if criterion else _titleize_id(criterion_id)
        important = [check for check in checks if check.priority == "important"]
        secondary = [check for check in checks if check.priority != "important"]

        def render_checks(items: list[object]) -> str:
            rows = []
            for item in items:
                check = item
                status = "active" if check.active else "guardrail"
                method = method_labels.get(check.analysis_method, "review")
                rows.append(
                    f"""
                    <li>
                      <strong>{_html(check.name)}</strong>
                      <span>{_html(check.visible_rule)}</span>
                      <em>{_html(status)} · {_html(method)}</em>
                    </li>
                    """
                )
            return "".join(rows)

        cards.append(
            f"""
            <article class="criteria-card">
              <h4>{_html(title)}</h4>
              <div>
                <span class="criteria-label">Important</span>
                <ul>{render_checks(important)}</ul>
              </div>
              <div>
                <span class="criteria-label">Secondary</span>
                <ul>{render_checks(secondary)}</ul>
              </div>
            </article>
            """
        )

    return f"""
    <section class="section-panel criteria-matrix-section" id="criteria-matrix">
      <div class="section-head">
        <div>
          <p class="eyebrow">Visible Criteria</p>
          <h2>What each axis checks before it becomes a client-facing issue</h2>
        </div>
        <p>Each axis has important and secondary checks. Rule-based checks are measured from visible Figma evidence, AI-assisted checks review judgment-heavy evidence, and human-review guardrails keep broad findings from becoming fake precise issues.</p>
      </div>
      <div class="criteria-matrix">{''.join(cards)}</div>
    </section>
    """


def _heuristic_source_note() -> str:
    return (
        '<p class="source-note">Heuristic basis: '
        f'<a href="{_html(HEURISTIC_REVIEW_SOURCE["url"])}" target="_blank" rel="noreferrer">'
        f'{_html(HEURISTIC_REVIEW_SOURCE["name"])}</a>. '
        f'{_html(HEURISTIC_REVIEW_SOURCE["basis"])} The model raises risk when an issue is visible in a mobile client view, repeated, severe, and tied to high-impact heuristics.</p>'
    )


def _axis_tile(
    criterion_id: str,
    criterion: UxUiCriterion | None,
    score: float,
    issue_count: int,
) -> str:
    title = criterion.short_name if criterion else _titleize_id(criterion_id)
    body = criterion.core_question if criterion else "No criterion description is available."
    if issue_count:
        issue_label = "issue" if issue_count == 1 else "issues"
        risk = (
            f"{issue_count} low-risk draft {issue_label}"
            if score >= GOOD_CRITERION_SCORE_THRESHOLD
            else f"{issue_count} priority {issue_label}"
        )
    else:
        risk = "Good - no reportable issue"
    tone = (
        "tone-clear"
        if issue_count == 0
        else ("tone-high" if score < 6 else "tone-medium")
    )
    return f"""
    <article class="axis-tile {tone}" data-criterion-id="{_html(criterion_id)}">
      <h4>{_html(title)}</h4>
      <p>{_html(body)}</p>
      <div class="axis-tile-meta"><strong data-criterion-score-value="{_html(criterion_id)}">{score:.1f}</strong><span>{_html(risk)}</span></div>
    </article>
    """


def _radar_svg(
    detection_result: DetectionResult,
    lookup: dict[str, UxUiCriterion],
    grouped: dict[str, list[dict[str, object]]],
) -> str:
    statuses = detection_result.criterion_status
    center_x = 260
    center_y = 245
    radius = 165
    labels = []
    points = []
    rings = []
    for ring_index in range(1, 6):
        ring_radius = radius * ring_index / 5
        ring_points = []
        for index, _status in enumerate(statuses):
            angle = -math.pi / 2 + 2 * math.pi * index / len(statuses)
            x = center_x + ring_radius * math.cos(angle)
            y = center_y + ring_radius * math.sin(angle)
            ring_points.append(f"{x:.1f},{y:.1f}")
        rings.append(f'<polygon points="{" ".join(ring_points)}" class="radar-ring"></polygon>')

    for index, status in enumerate(statuses):
        angle = -math.pi / 2 + 2 * math.pi * index / len(statuses)
        score = _criterion_score(grouped.get(status.criterion_id, []))
        point_radius = radius * score / 10
        x = center_x + point_radius * math.cos(angle)
        y = center_y + point_radius * math.sin(angle)
        points.append(f"{x:.1f},{y:.1f}")

        label_radius = radius + 38
        lx = center_x + label_radius * math.cos(angle)
        ly = center_y + label_radius * math.sin(angle)
        label = _criterion_name(status.criterion_id, lookup)
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" class="radar-label">{_html(label)}</text>'
        )

    return f"""
    <svg class="radar-chart" viewBox="0 0 520 500" role="img" aria-label="Seven-axis scoring radar chart">
      {"".join(rings)}
      <polygon points="{" ".join(points)}" class="radar-shape"></polygon>
      {"".join(labels)}
    </svg>
    """


def _scoring_section(
    detection_result: DetectionResult,
    lookup: dict[str, UxUiCriterion],
    grouped: dict[str, list[dict[str, object]]],
) -> str:
    tiles = []
    for status in detection_result.criterion_status:
        issues = grouped.get(status.criterion_id, [])
        tiles.append(
            _axis_tile(
                status.criterion_id,
                lookup.get(status.criterion_id),
                _criterion_score(issues),
                len(issues),
            )
        )
    return f"""
    <section class="section-panel scoring-section" id="scores">
      <div class="section-head">
        <p class="eyebrow">Scores</p>
        <h2>Seven axes at a glance</h2>
        <p>Scores combine severity, confidence, mobile visibility, repeated evidence, screenshot verification, and usability-heuristic weight. They are a triage signal, not formal certification.</p>
        {_heuristic_source_note()}
      </div>
      <div class="score-overview">
        <div class="radar-card">{_radar_svg(detection_result, lookup, grouped)}</div>
        <div class="axis-grid">{"".join(tiles)}</div>
      </div>
    </section>
    """


def _issue_area(issue: dict[str, object]) -> str:
    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    area = _clean_text(
        evidence.get("mobile_viewport_name")
        or location.get("frame_name")
        or location.get("node_name")
        or "this visible area"
    )
    if not area or area.lower() in {"container", "frame"}:
        area = _clean_text(location.get("node_name") or location.get("frame_name") or "this visible area")
    area = area.replace("_", " ").replace("-", " ")
    area = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", area)
    area = re.sub(r"\s+", " ", area).strip(" .:/\\>|")
    return area or "this visible area"


def _evidence_list(evidence: dict[str, object], key: str) -> list[str]:
    value = evidence.get(key)
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, dict):
        return [_clean_text(f"{item_key}: {item_value}") for item_key, item_value in value.items()]
    text = _clean_text(value)
    return [text] if text else []


def _checks(evidence: dict[str, object], key: str) -> dict[str, object]:
    value = evidence.get(key)
    return value if isinstance(value, dict) else {}


def _sentence_list(items: list[str], *, limit: int = 3) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1 or limit == 1:
        return cleaned[0]
    visible = cleaned[:limit]
    if len(visible) == 2:
        return f"{visible[0]} and {visible[1]}"
    return f"{', '.join(visible[:-1])}, and {visible[-1]}"


def _quoted(value: object) -> str:
    text = _clean_text(value)
    return f'"{text}"' if text else ""


def _subdetector(issue: dict[str, object]) -> str:
    evidence = _issue_evidence(issue)
    for key in (
        "accessibility_check",
        "task_execution_subdetector",
        "flow_subdetector",
        "ui_consistency_subdetector",
        "visual_brand_subdetector",
        "content_microcopy_subdetector",
    ):
        value = _clean_text(evidence.get(key))
        if value:
            return value
    return ""


def _issue_visibility_sentence(issue: dict[str, object]) -> str:
    if _issue_has_client_visible_evidence(issue):
        return "The issue is visible in the verified client-view screenshot."
    artifact = _issue_supporting_visual_artifact(issue)
    if artifact is None:
        return "The issue is based on structural Figma evidence and should be checked against a rendered client view."
    artifact_type = str(artifact.get("type") or "")
    if artifact_type == "node_preview_locator":
        return "This finding has only an internal Figma-node locator, so it should be re-rendered before it is treated as client evidence."
    if artifact_type == "real_page_context_screenshot":
        return "The context screenshot supports the finding, but the exact target still needs rendered client-view confirmation."
    if artifact_type == "annotated_screenshot":
        return "The Figma-rendered annotation supports the finding, but it is not a verified client screenshot."
    return "The supplied visual evidence supports the finding, but it should still be verified in the rendered client view."


def _issue_summary(issue: dict[str, object]) -> str:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    ai_review = evidence.get("ai_review") if isinstance(evidence.get("ai_review"), dict) else {}
    ai_reframe = _clean_text(ai_review.get("client_reframe"))
    if ai_review.get("decision") == "soften" and ai_reframe:
        return ai_reframe
    area = _issue_area(issue)
    text_sample = _clean_text(evidence.get("text_sample") or "")
    detector_id = str(evidence.get("detector_id") or "")
    subdetector = _subdetector(issue)

    if detector_id == "low_text_contrast":
        role = str(evidence.get("contrast_text_role") or "")
        role_context = {
            "status_or_time_feedback": "This is status/timing text, so users may miss an update or delivery cue.",
            "choice_or_setting_label": "This label explains a visible choice or setting, so users may not understand the option they are selecting.",
            "action_label": "This is action text, so users may hesitate before tapping.",
            "primary_content": "This is primary content, so the screen's main message becomes harder to scan.",
            "body_content": "This is body copy, so users may skip important explanation.",
            "supporting_metadata": "This is supporting metadata, so it should be treated as a lower-priority pattern unless it affects a decision.",
            "secondary_label": "This is secondary label text, so the issue is lower priority unless it repeats across key screens.",
        }.get(role, _issue_visibility_sentence(issue))
        sample = f' The text shown is "{text_sample}".' if text_sample else ""
        contrast = ""
        if "contrast_ratio" in evidence and "required_ratio" in evidence:
            contrast = (
                f" Its contrast is {evidence.get('contrast_ratio')}:1, "
                f"but it should be at least {evidence.get('required_ratio')}:1."
            )
        return (
            f"In {area}, the text does not stand out enough from the background. "
            f"{role_context}{sample}{contrast}"
        )

    if detector_id == "small_touch_target":
        width = evidence.get("target_width")
        height = evidence.get("target_height")
        size = ""
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            size = f" The measured target is {width:g}x{height:g} pt."
        return (
            f"In {area}, a tappable control is too small for comfortable mobile use. "
            f"It may look fine visually, but it gives users a small area to tap.{size}"
        )

    if detector_id == "crowded_touch_target":
        nearest_gap = evidence.get("nearest_control_gap")
        edge_gap = evidence.get("edge_gap")
        measured = ""
        if isinstance(nearest_gap, (int, float)):
            measured = f" The nearest visible control is only {nearest_gap:g} pt away."
        elif isinstance(edge_gap, (int, float)):
            measured = f" The control is only {edge_gap:g} pt from the screen edge."
        return (
            f"In {area}, the boxed control is too close to another tap target or to the phone edge. "
            f"This makes the tap zone feel crowded on mobile.{measured}"
        )

    if detector_id == "small_text_readability":
        sample = f' The text shown is "{text_sample}".' if text_sample else ""
        font_size = evidence.get("font_size")
        size = f" Its measured font size is {font_size:g} pt." if isinstance(font_size, (int, float)) else ""
        check = _clean_text(evidence.get("accessibility_check") or "")
        if check == "dense_line_spacing":
            return (
                f"In {area}, the boxed text block is dense and tightly spaced for mobile reading."
                f"{sample}{size}"
            )
        return (
            f"In {area}, important visible text is too small to read comfortably on a phone."
            f"{sample}{size}"
        )

    if detector_id == "icon_only_unlabeled_control":
        return (
            f"In {area}, the boxed icon-only control has no visible label and its meaning is not obvious from the screenshot alone. "
            "Users may need to guess what the control does."
        )

    if detector_id == "form_without_completion_action":
        fields = _sentence_list(_evidence_list(evidence, "field_purposes"))
        field_text = f" The visible fields are for {fields}." if fields else ""
        return (
            f"In {area}, users can enter information, but they do not get a clear button to finish the task. "
            f"The screen needs an obvious final action, such as Save, Submit, Continue, or Sign in.{field_text}"
        )

    if detector_id == "ambiguous_completion_action":
        label = _quoted(evidence.get("ambiguous_action_label"))
        fields = _sentence_list(_evidence_list(evidence, "field_purposes"))
        field_text = f" after entering {fields}" if fields else ""
        return (
            f"In {area}, the main button says {label or 'something generic'}, but that wording is not specific enough. "
            f"Users cannot tell what will happen when they tap it{field_text}."
        )

    if detector_id == "destructive_action_without_recovery":
        label = _quoted(evidence.get("destructive_label"))
        return (
            f"In {area}, the action {label or 'this action'} can remove or change something important. "
            "There is no clear Cancel, Undo, or confirmation step beside it."
        )

    if detector_id == "generic_navigation_label":
        labels = _evidence_list(evidence, "navigation_labels")
        duplicates = evidence.get("duplicated_labels")
        if subdetector == "missing_destination_labels_in_primary_navigation":
            checks = _checks(evidence, "visual_search_checks")
            count = checks.get("unlabeled_item_count")
            count_text = f" {count} navigation items" if isinstance(count, (int, float)) else " navigation items"
            return (
                f"In {area}, the bottom navigation shows{count_text} without text labels. "
                "This is a visible page-to-page navigation problem: users can see the icons, "
                "but they may not know which screen each icon opens before tapping it."
            )
        duplicate_text = ""
        if isinstance(duplicates, dict) and duplicates:
            repeated = _sentence_list([f'{_clean_text(key)} repeated {value} times' for key, value in duplicates.items()])
            duplicate_text = f" The repeated label is {repeated}."
        label_text = f" The visible labels are {_sentence_list(labels, limit=5)}." if labels else ""
        return (
            f"In {area}, several navigation items use the same or very generic label. "
            "This is a visible page-to-page navigation problem: the client can see the tabs, "
            f"but cannot clearly predict which screen each tab opens.{duplicate_text}{label_text}"
        )

    if detector_id == "component_style_outlier":
        if subdetector == "lexical_label_outlier":
            dominant = _quoted(evidence.get("dominant_label"))
            outlier = _quoted(evidence.get("outlier_label"))
            size = evidence.get("family_sample_size")
            family_text = f" in a repeated set of {size} controls" if isinstance(size, (int, float)) else ""
            return (
                f"In {area}, one control uses the label {outlier or 'a different word'}, while the matching controls "
                f"use {dominant or 'another label'}{family_text}. The different wording makes it look like a different action."
            )
        field = _clean_text(evidence.get("field") or "style value").replace("_", " ")
        dominant = evidence.get("dominant_value")
        outlier = evidence.get("outlier_value")
        return (
            f"In {area}, one repeated control does not match the others. "
            f"Most matching controls use {dominant} for {field}, but this one uses {outlier}."
        )

    if detector_id == "flat_visual_hierarchy":
        if subdetector == "bad_foreground_panel_position":
            gap = evidence.get("heading_to_panel_gap")
            ratio = evidence.get("panel_height_to_background_ratio")
            gap_text = f" It starts only {gap:g} px below the heading." if isinstance(gap, (int, float)) else ""
            ratio_text = (
                f" The panel uses {ratio * 100:.0f}% of the background height."
                if isinstance(ratio, (int, float))
                else ""
            )
            return (
                f"In {area}, the main white form panel is positioned too high and too large inside the decorative background. "
                "It crowds the title area and makes the screen look like an accidental overlay instead of a composed product screen."
                f"{gap_text}{ratio_text}"
            )
        if subdetector == "visible_component_workspace":
            count = evidence.get("component_set_count")
            count_text = f" {int(count)} component variant groups" if isinstance(count, (int, float)) else " component variant groups"
            names = _sentence_list(_evidence_list(evidence, "workspace_artifact_names"), limit=4)
            names_text = f" Examples visible in the frame include {names}." if names else ""
            return (
                f"In {area}, the reviewed Figma frame mixes the app screen with{count_text} and other work-in-progress UI pieces. "
                "A user or client reviewing the frame sees component documentation artifacts instead of one clean product screen."
                f"{names_text}"
            )
        ratio = evidence.get("largest_to_median_ratio")
        ratio_text = f" The measured size contrast is {ratio}:1." if isinstance(ratio, (int, float)) else ""
        return (
            f"In {area}, the heading and supporting text look almost equally important. "
            f"The client cannot quickly tell what the main message is or what to read first.{ratio_text}"
        )

    if detector_id == "placeholder_or_generic_copy":
        sample = _quoted(text_sample or evidence.get("matched_text"))
        checks = _checks(evidence, "plain_language_checks")
        if subdetector == "generic_cta_without_context":
            context_terms = _sentence_list(_evidence_list(checks, "context_tokens"), limit=4)
            context_text = f" The nearby context only gives weak cues such as {context_terms}." if context_terms else ""
            return (
                f"In {area}, the button text {sample or 'this label'} is too vague. "
                f"It tells users to act, but not what result they will get after tapping it.{context_text}"
            )
        if subdetector == "vague_value_copy":
            terms = _sentence_list(_evidence_list(checks, "vague_terms"), limit=4)
            return (
                f"In {area}, the copy sounds broad"
                f"{f' because it uses words such as {terms}' if terms else ''}. "
                "It does not clearly say what the product does, who it helps, or what benefit the user gets."
            )
        if subdetector == "dense_plain_language_risk":
            words = checks.get("word_count")
            word_text = f" It contains about {words} words in one block." if isinstance(words, (int, float)) else ""
            return (
                f"In {area}, there is too much text in one block for a mobile screen."
                f"{word_text} The main point is harder to notice quickly."
            )
        if subdetector == "truncated_or_clipped_copy":
            return (
                f"In {area}, the visible text {sample or 'in the boxed area'} appears truncated or clipped. "
                "Users cannot read the full label, value, or instruction from the screen."
            )
        return (
            f"In {area}, the screen still shows placeholder text {sample or 'like a template label'}. "
            "This is not final client copy, so it does not explain the real product, section, action, or benefit."
        )

    return _clean_text(issue.get("message") or "A visible UX/UI issue was detected in this area.")


def _why_it_matters(issue: dict[str, object], criterion: UxUiCriterion | None) -> str:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    detector_id = str(evidence.get("detector_id") or "")
    subdetector = _subdetector(issue)
    if detector_id == "low_text_contrast":
        return (
            "People scanning the screen may miss this information, especially on dim screens, "
            "small devices, or for users with reduced vision. The design may look polished but still be hard to read."
        )
    if detector_id == "small_touch_target":
        return (
            "Small controls increase tap errors, especially for users with motor impairments, one-handed use, "
            "or dense mobile layouts. The interface can look neat but feel unreliable in real use."
        )
    if detector_id == "crowded_touch_target":
        return (
            "Crowded tap areas increase accidental taps and make mobile interaction feel imprecise, "
            "especially during one-handed use or when controls are near system gesture zones."
        )
    if detector_id == "small_text_readability":
        return (
            "Small or tightly spaced important text slows scanning and can make labels, prices, statuses, or instructions hard to use on a phone."
        )
    if detector_id == "icon_only_unlabeled_control":
        return (
            "Icon-only controls depend on recognition. When the icon is ambiguous, users may hesitate, tap the wrong action, or avoid the feature."
        )
    if detector_id == "form_without_completion_action":
        return (
            "Users need a visible way to finish a task. Without it, they may hesitate, abandon the form, "
            "or assume the screen is incomplete."
        )
    if detector_id == "ambiguous_completion_action":
        return (
            "A generic action label makes users guess the result of tapping it. That increases hesitation and mistakes, "
            "especially on forms where the next step matters."
        )
    if detector_id == "destructive_action_without_recovery":
        return (
            "Risky actions need visible safety. Without confirmation or recovery, users can lose data or confidence in the product."
        )
    if detector_id == "generic_navigation_label":
        if subdetector == "missing_destination_labels_in_primary_navigation":
            return (
                "Icon-only navigation depends on memory and interpretation. New users may tap the wrong place or avoid exploring."
            )
        return (
            "Navigation should tell people what screen they will reach before they tap. Repeated or generic labels force users "
            "to move from page to page by guessing, which slows wayfinding and weakens trust."
        )
    if detector_id == "component_style_outlier":
        return (
            "Repeated controls should feel like one system. When one item looks or reads differently without a clear reason, "
            "users may think it has a different meaning or state."
        )
    if detector_id == "flat_visual_hierarchy":
        if subdetector == "bad_foreground_panel_position":
            return (
                "Positioning is one of the first things a reviewer notices. When the main panel crowds the title or dominates the background, "
                "the screen feels unfinished, hierarchy becomes unclear, and users cannot tell where the intended content area begins."
            )
        if subdetector == "visible_component_workspace":
            return (
                "Reviewers judge the visible frame as the product surface. If component variants, duplicate buttons, or workspace pieces are visible, "
                "the design looks unfinished and the audit can mistake documentation fragments for real user flow."
            )
        return (
            "When everything looks equally important, reviewers and users need more effort to understand the screen. "
            "That weakens first impression, navigation, and confidence."
        )
    if detector_id == "placeholder_or_generic_copy":
        if subdetector == "placeholder_text":
            return (
                "Placeholder copy makes the experience feel unfinished and prevents the client from judging the real message, "
                "tone, and user value."
            )
        if subdetector == "generic_cta_without_context":
            return (
                "Button text should reduce uncertainty. If the label does not explain the outcome, users may pause or choose the wrong action."
            )
        if subdetector == "dense_plain_language_risk":
            return (
                "Mobile users scan quickly. Dense copy hides the main point and makes the screen harder to understand at a glance."
            )
        if subdetector == "truncated_or_clipped_copy":
            return (
                "Cut-off text forces users to guess the missing information and makes the UI feel unfinished or unreliable."
            )
        return (
            "Generic wording makes the product harder to understand and remember. Users need concrete language to know what value they get."
        )
    if criterion:
        return criterion.user_impact
    return "The issue can slow comprehension and reduce confidence in the interface."


def _human_issue_title(issue: dict[str, object]) -> str:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    detector_id = str(evidence.get("detector_id") or "")
    subdetector = _subdetector(issue)
    text_sample = _clean_text(evidence.get("text_sample") or "")
    if detector_id == "low_text_contrast":
        role = str(evidence.get("contrast_text_role") or "")
        if role == "status_or_time_feedback":
            return f'Status text is hard to read: "{text_sample}"' if text_sample else "Status text is hard to read"
        if role == "choice_or_setting_label":
            return f'Choice label is hard to read: "{text_sample}"' if text_sample else "Choice label is hard to read"
        if role == "supporting_metadata":
            return f'Supporting text has weak contrast: "{text_sample}"' if text_sample else "Supporting text has weak contrast"
        if text_sample:
            return f'Text is hard to read: "{text_sample}"'
        return "Text is hard to read"
    if detector_id == "small_touch_target":
        return "A tap target is too small"
    if detector_id == "crowded_touch_target":
        check = _clean_text(evidence.get("accessibility_check") or "")
        if check == "edge_safe_touch_area":
            return "A control is too close to the screen edge"
        return "Tap targets are too close together"
    if detector_id == "small_text_readability":
        check = _clean_text(evidence.get("accessibility_check") or "")
        if check == "dense_line_spacing":
            return "Text spacing is too dense"
        return f'Text is too small: "{text_sample}"' if text_sample else "Important text is too small"
    if detector_id == "icon_only_unlabeled_control":
        return "Icon-only control needs a visible label"
    if detector_id == "form_without_completion_action":
        return "The form has no clear finish action"
    if detector_id == "ambiguous_completion_action":
        return "The completion action is unclear"
    if detector_id == "destructive_action_without_recovery":
        return "A risky action has no safety step"
    if detector_id == "generic_navigation_label":
        if subdetector == "missing_destination_labels_in_primary_navigation":
            return "Primary navigation needs visible labels"
        return "Navigation labels are too generic"
    if detector_id == "component_style_outlier":
        if subdetector == "lexical_label_outlier":
            return "A repeated control uses inconsistent wording"
        return "A repeated control looks inconsistent"
    if detector_id == "flat_visual_hierarchy":
        if subdetector == "bad_foreground_panel_position":
            return "The main form panel is badly positioned"
        if subdetector == "visible_component_workspace":
            return "Component variants are visible in the screen"
        return "The screen hierarchy is too flat"
    if detector_id == "placeholder_or_generic_copy":
        if subdetector == "generic_cta_without_context":
            return f'CTA is unclear: "{text_sample}"' if text_sample else "CTA is unclear"
        if subdetector == "vague_value_copy":
            return "Copy is too vague to explain value"
        if subdetector == "dense_plain_language_risk":
            return "Copy is too dense for quick scanning"
        if subdetector == "truncated_or_clipped_copy":
            return f'Visible text is clipped: "{text_sample}"' if text_sample else "Visible text is clipped"
        return f'Placeholder copy is visible: "{text_sample}"' if text_sample else "Placeholder copy is visible"
    return _clean_text(issue.get("message") or "UX/UI issue detected")


def _polished_value(
    polished_copy: dict[str, dict[str, str]],
    issue: dict[str, object],
    key: str,
    fallback: str,
) -> str:
    issue_id = str(issue.get("id") or "")
    copy = polished_copy.get(issue_id, {})
    value = copy.get(key)
    return _clean_text(value) if value else fallback


def _recommendation(issue: dict[str, object], criterion: UxUiCriterion | None) -> str:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    detector_id = str(evidence.get("detector_id") or "")
    subdetector = _subdetector(issue)
    area = _issue_area(issue)
    if detector_id == "flat_visual_hierarchy" and subdetector == "bad_foreground_panel_position":
        return (
            "Reposition the form panel so it sits clearly below the title with enough breathing room. "
            "Reduce the panel height or move it lower inside the background, then keep the heading, form card, and supporting controls in one clean vertical composition."
        )
    if detector_id == "flat_visual_hierarchy" and subdetector == "visible_component_workspace":
        return (
            "Separate the client-facing app screen from the design-system/component playground. "
            "Move rating variants, placeholder variants, and duplicate submit-button states outside the reviewed screen frame, "
            "or put them on a documentation page so the audit and the client see only the intended product screen."
        )
    if detector_id in DETECTOR_RECOMMENDATIONS:
        return DETECTOR_RECOMMENDATIONS[detector_id]
    if detector_id == "form_without_completion_action":
        return (
            f"Add a visible primary action in {area}, such as Save, Submit, Continue, or Sign in. "
            "Place it near the fields and use wording that states the exact task outcome."
        )
    if detector_id == "ambiguous_completion_action":
        label = _clean_text(evidence.get("ambiguous_action_label") or "Continue")
        return (
            f"Replace {label!r} with an outcome-based label, such as Create account, Save changes, Apply filters, or Send message. "
            "Add a short heading or helper line if the action still needs context."
        )
    if detector_id == "destructive_action_without_recovery":
        return (
            "Add a confirmation step, a Cancel option, or an Undo path next to the destructive action. "
            "Use clear language that tells users what will be deleted or changed."
        )
    if detector_id == "generic_navigation_label":
        if subdetector == "missing_destination_labels_in_primary_navigation":
            return (
                "Add short visible labels under or beside the primary navigation icons, such as Home, Search, Orders, or Profile. "
                "Keep icons and labels paired so users do not need to memorize meanings."
            )
        return (
            "Replace repeated labels like Label, Tab, or Item with specific destination names. "
            "Each navigation option should describe the screen or task it opens, so the page-to-page path is clear before the user taps."
        )
    if detector_id == "component_style_outlier":
        if subdetector == "lexical_label_outlier":
            return (
                "Choose one term for the repeated action and apply it consistently across the component family. "
                "Use different wording only when the action or state is genuinely different."
            )
        field = _clean_text(evidence.get("field") or "style value").replace("_", " ")
        dominant = evidence.get("dominant_value")
        return (
            f"Align the outlier's {field} with the repeated pattern"
            f"{f' ({dominant})' if dominant is not None else ''}, unless it represents a documented state. "
            "Update the shared component or variant so the pattern stays consistent."
        )
    if detector_id == "placeholder_or_generic_copy":
        if subdetector == "generic_cta_without_context":
            return (
                "Rewrite the CTA as a specific outcome, not a generic instruction. "
                "For example, use Save address, Book demo, Apply filters, or Create account depending on the screen."
            )
        if subdetector == "vague_value_copy":
            return (
                "Replace broad claims with concrete user language. Name who the feature is for, what task it helps with, "
                "and what result the user gets."
            )
        if subdetector == "dense_plain_language_risk":
            return (
                "Break the copy into a short heading, one clear supporting sentence, and optional bullets. "
                "Put the main user benefit first."
            )
        if subdetector == "truncated_or_clipped_copy":
            return (
                "Increase the text container width, reduce the label length, or allow the text to wrap. "
                "Make sure the full user-facing label is visible on the mobile screen."
            )
        return (
            "Replace placeholder words with final product copy before sharing the report with clients. "
            "Name the real product, section, action, or content so the design can be evaluated properly."
        )
    if criterion:
        return criterion.default_fix
    return "Review the affected component, clarify the user-facing purpose, and update the shared design pattern before re-running the audit."


def _issue_score(issue: dict[str, object]) -> float:
    penalty = _issue_penalty(issue)
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    ai_review = evidence.get("ai_review") if isinstance(evidence.get("ai_review"), dict) else {}
    if ai_review.get("decision") == "soften":
        penalty *= 0.72
    return round(max(1.8, min(9.7, 10.0 - penalty * 2.25)), 1)


def _score_model_payload(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
) -> str:
    actionable = _actionable_issues(detection_result, grouped)
    actionable_ids = {str(issue.get("id") or "") for issue in actionable}
    criteria: dict[str, dict[str, object]] = {}
    issues_payload: dict[str, dict[str, object]] = {}

    for status in detection_result.criterion_status:
        criterion_id = status.criterion_id
        criterion_issues = grouped.get(criterion_id, [])
        criteria[criterion_id] = {
            "score": _criterion_score(criterion_issues),
            "issueIds": [
                str(issue.get("id") or "")
                for issue in criterion_issues
                if str(issue.get("id") or "") in actionable_ids
            ],
        }

    for issue in actionable:
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            continue
        issues_payload[issue_id] = {
            "criterion": str(issue.get("criterion") or issue.get("axis") or "unknown"),
            "score": _issue_score(issue),
        }

    payload = json.dumps(
        {
            "criteria": criteria,
            "issues": issues_payload,
            "visibleIssueCount": len(issues_payload),
        },
        ensure_ascii=False,
    )
    return payload.replace("<", "\\u003c").replace(">", "\\u003e")


def _priority_story(
    issue: dict[str, object],
    index: int,
    report_path: Path,
    lookup: dict[str, UxUiCriterion],
    polished_copy: dict[str, dict[str, str]],
) -> str:
    criterion_id = str(issue.get("criterion") or issue.get("axis") or "unknown")
    criterion = lookup.get(criterion_id)
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    confidence = _clean_text(evidence.get("confidence") or "unknown").title()
    issue_id = str(issue.get("id") or f"issue-{index}")
    issue_score = _issue_score(issue)
    heuristic_names = _heuristic_model(issue).get("heuristics")
    heuristic_html = ""
    if isinstance(heuristic_names, list) and heuristic_names:
        heuristic_html = f" - {_html(heuristic_names[0])}"
    evidence_label = _issue_evidence_label(issue)
    return f"""
    <article class="story-row {str(issue.get("severity") or "low")}" id="issue-{_html(issue_id)}" data-issue-id="{_html(issue_id)}" data-criterion-id="{_html(criterion_id)}">
      <div class="story-index">{index:02d}</div>
      <div class="story-media">{_image_frame(issue, report_path)}</div>
      <div class="story-score-pane">
        {_score_ring(issue_score, size=128, label="risk", attrs={"data-score-role": "issue", "data-issue-id": issue_id, "data-criterion-id": criterion_id})}
        <span>{_html(_criterion_name(criterion_id, lookup))}</span>
        <div class="score-editor" data-issue-id="{_html(issue_id)}" data-local-edit-control>
          <label>Issue score <input type="number" min="0" max="10" step="0.1" value="{issue_score:.1f}" data-score-input data-issue-id="{_html(issue_id)}"></label>
        </div>
      </div>
      <div class="story-copy">
        <p class="story-kicker">{_html(confidence)} confidence - {_html(evidence_label)}{heuristic_html}</p>
        <div class="story-title-row">
          <h3 data-editable-field="title">{_html(_polished_value(polished_copy, issue, "title", _human_issue_title(issue)))}</h3>
          {_issue_edit_toolbar(issue_id, image=True, copy=True, score=False)}
        </div>
        <p><strong>What is wrong:</strong> <span data-editable-field="what_is_wrong">{_html(_polished_value(polished_copy, issue, "what_is_wrong", _issue_summary(issue)))}</span></p>
        <p><strong>Why it matters:</strong> <span data-editable-field="why_it_matters">{_html(_polished_value(polished_copy, issue, "why_it_matters", _why_it_matters(issue, criterion)))}</span></p>
        <p><strong>Recommended fix:</strong> <span data-editable-field="recommended_fix">{_html(_polished_value(polished_copy, issue, "recommended_fix", _recommendation(issue, criterion)))}</span></p>
      </div>
    </article>
    """


def _priority_section(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
    report_path: Path,
    lookup: dict[str, UxUiCriterion],
    polished_copy: dict[str, dict[str, str]],
) -> str:
    priority_issues = sorted(_actionable_issues(detection_result, grouped), key=_issue_sort_key)[:5]
    if not priority_issues:
        body = """
        <div class="empty-state">
          No issue-level finding is shown because no non-rejected draft issue remains after the visibility checks.
        </div>
        """
    else:
        body = "".join(
            _priority_story(issue, index, report_path, lookup, polished_copy)
            for index, issue in enumerate(priority_issues, start=1)
        )
    return f"""
    <section class="priority-panel" id="findings">
      <div class="section-head">
        <div>
          <p class="eyebrow">Findings</p>
          <h2>Only the pain points that matter most</h2>
        </div>
        <p>The top findings are selected by severity, confidence, evidence strength, and criterion score. Criteria with no non-rejected issue stay out of the issue list.</p>
      </div>
      <div class="stories">{body}</div>
    </section>
    """


def _axis_story(
    criterion_id: str,
    index: int,
    issues: list[dict[str, object]],
    score: float,
    report_path: Path,
    lookup: dict[str, UxUiCriterion],
    polished_copy: dict[str, dict[str, str]],
) -> str:
    criterion = lookup.get(criterion_id)
    title = criterion.short_name if criterion else _titleize_id(criterion_id)
    impact = criterion.business_impact if criterion else "This axis affects design clarity and user confidence."
    lead_issue_id = ""
    lead_issue_score = score
    if issues:
        lead_issue = sorted(issues, key=_issue_sort_key)[0]
        lead_issue_id = str(lead_issue.get("id") or "")
        lead_issue_score = _issue_score(lead_issue)
        observed = _polished_value(
            polished_copy, lead_issue, "what_is_wrong", _issue_summary(lead_issue)
        )
        matters = _polished_value(
            polished_copy, lead_issue, "why_it_matters", _why_it_matters(lead_issue, criterion)
        )
        recommendation = _polished_value(
            polished_copy,
            lead_issue,
            "recommended_fix",
            _recommendation(lead_issue, criterion),
        )
        lead = _polished_value(
            polished_copy, lead_issue, "title", _human_issue_title(lead_issue)
        )
        media = _image_frame(lead_issue, report_path)
        tone = "tone-high" if score < 6 else "tone-medium"
    else:
        observed = "No active issue was detected for this criterion in the current draft pass."
        matters = "This does not mean the axis is final; it means the automated pass did not find a visible problem."
        recommendation = (
            criterion.default_fix
            if criterion
            else "Keep this axis in the manual review checklist."
        )
        lead = "No draft issue detected for this criterion."
        media = """
        <div class="story-visual-empty positive">
          <strong>No active issue</strong>
          <span>This axis still needs final human review.</span>
        </div>
        """
        tone = "tone-clear"

    return f"""
    <article class="axis-story {tone}" id="axis-{index}" data-criterion-id="{_html(criterion_id)}"{_html_attrs({"data-issue-id": lead_issue_id or None})}>
      <div class="story-index">{index:02d}</div>
      <div class="axis-story-media">{media}</div>
      <div class="axis-story-score">
        {_score_ring(score, size=154, attrs={"data-score-role": "criterion", "data-criterion-id": criterion_id})}
        {f'<div class="score-editor" data-issue-id="{_html(lead_issue_id)}" data-local-edit-control><label>Issue score <input type="number" min="0" max="10" step="0.1" value="{lead_issue_score:.1f}" data-score-input data-issue-id="{_html(lead_issue_id)}"></label></div>' if lead_issue_id else ""}
      </div>
      <div class="axis-story-copy">
        <div class="story-title-row">
          <h3>{_html(title)}</h3>
          {_issue_edit_toolbar(lead_issue_id, image=True, copy=True, score=False) if lead_issue_id else ""}
        </div>
        <p><strong>Commercial impact:</strong> {_html(impact)}</p>
        <p><strong>Lead issue:</strong> <span data-editable-field="title">{_html(lead)}</span></p>
        <p><strong>What is wrong:</strong> <span data-editable-field="what_is_wrong">{_html(observed)}</span></p>
        <p><strong>Why it matters:</strong> <span data-editable-field="why_it_matters">{_html(matters)}</span></p>
        <p><strong>Recommended fix:</strong> <span data-editable-field="recommended_fix">{_html(recommendation)}</span></p>
      </div>
    </article>
    """


def _axis_stories_section(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
    report_path: Path,
    lookup: dict[str, UxUiCriterion],
    polished_copy: dict[str, dict[str, str]],
) -> str:
    stories = []
    index = 1
    for status in detection_result.criterion_status:
        issues = grouped.get(status.criterion_id, [])
        score = _criterion_score(issues)
        if not _criterion_is_actionable(score, len(issues)):
            continue
        stories.append(
            _axis_story(
                status.criterion_id,
                index,
                issues,
                score,
                report_path,
                lookup,
                polished_copy,
            )
        )
        index += 1
    body = "".join(stories) if stories else """
        <div class="empty-state">
          No criterion story is shown because no non-rejected draft issue remains after the visibility checks.
        </div>
    """
    return f"""
    <section class="section-panel" id="axes">
      <div class="section-head">
        <div>
          <p class="eyebrow">Axis Stories</p>
          <h2>One section per UX/UI lens</h2>
        </div>
        <p>Only criteria with non-rejected draft issues appear here. The score still shows severity, but high-scoring issues are no longer silently hidden.</p>
      </div>
      <div class="axis-stories">{body}</div>
    </section>
    """


def _recommendations_section(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
    lookup: dict[str, UxUiCriterion],
    polished_copy: dict[str, dict[str, str]],
) -> str:
    issues = sorted(_actionable_issues(detection_result, grouped), key=_issue_sort_key)[:5]
    if not issues:
        body = """
        <article class="reco-card">
          <span class="reco-orb">01</span>
          <span class="reco-badge">Review</span>
          <h4>No issue-level recommendation is shown</h4>
          <p>No non-rejected draft issue remains after the visibility checks, so the report keeps the recommendation list clear.</p>
          <span class="reco-axis">All axes</span>
          <span class="reco-cta">Recommended move</span>
        </article>
        """
    else:
        cards = []
        for index, issue in enumerate(issues, start=1):
            criterion_id = str(issue.get("criterion") or issue.get("axis") or "unknown")
            criterion = lookup.get(criterion_id)
            cards.append(
                f"""
                <article class="reco-card priority-{_html(str(issue.get("severity") or "low"))}" tabindex="0">
                  <span class="reco-orb">{index:02d}</span>
                  <span class="reco-badge">{_html(str(issue.get("severity") or "draft").title())}</span>
                  <h4>{_html(_polished_value(polished_copy, issue, "title", _human_issue_title(issue)))}</h4>
                  <p>{_html(_polished_value(polished_copy, issue, "recommended_fix", _recommendation(issue, criterion)))}</p>
                  <p class="reco-impact">Why this is prioritized: {_html(_polished_value(polished_copy, issue, "why_it_matters", _why_it_matters(issue, criterion)))}</p>
                  <span class="reco-axis">{_html(_criterion_name(criterion_id, lookup))}</span>
                  <span class="reco-cta">Recommended fix</span>
                </article>
                """
            )
        body = "".join(cards)
    return f"""
    <section class="section-panel" id="recommendations">
      <div class="section-head">
        <div>
          <p class="eyebrow">Recommendations</p>
          <h2>Prioritized actions</h2>
        </div>
      </div>
      <div class="reco-grid">{body}</div>
    </section>
    """


def _style() -> str:
    return """
    :root {
      color-scheme: light;
      --bg: #f6f1e8;
      --paper: rgba(255, 255, 255, 0.84);
      --card: rgba(255, 255, 255, 0.92);
      --ink: #202733;
      --muted: #687386;
      --line: rgba(32, 39, 51, 0.10);
      --gold: #c6a137;
      --gold-soft: rgba(198, 161, 55, 0.14);
      --teal: #11886e;
      --red: #cf513f;
      --shadow: 0 20px 48px rgba(32, 39, 51, 0.08);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(198, 161, 55, 0.15), transparent 22rem),
        radial-gradient(circle at top right, rgba(17, 136, 110, 0.10), transparent 24rem),
        linear-gradient(180deg, #fbf7f0 0%, #f6f1e8 100%);
      font-family: Aptos, "Segoe UI", Arial, sans-serif;
      line-height: 1.55;
    }
    a { color: inherit; }
    .shell { max-width: 1240px; margin: 0 auto; padding: 24px 20px 84px; }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 24px;
      width: 100vw;
      margin: -24px 0 24px calc(50% - 50vw);
      padding: 14px max(24px, calc((100vw - 1240px) / 2 + 20px));
      border-bottom: 1px solid rgba(32, 39, 51, 0.08);
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(16px);
      box-shadow: 0 8px 22px rgba(32, 39, 51, 0.04);
    }
    .brand-lockups { display: flex; align-items: center; gap: 20px; min-width: 0; }
    .brand-primary, .brand-secondary { display: inline-flex; align-items: center; gap: 10px; min-height: 48px; }
    .brand-primary { position: relative; padding-top: 10px; color: var(--ink); }
    .brand-primary::before {
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      width: 44px;
      height: 8px;
      background: #ffd44d;
      transform: skewX(-22deg);
    }
    .brand-ey { display: inline-flex; align-items: flex-end; gap: 6px; }
    .brand-ey strong { font-size: 1.7rem; line-height: 1; letter-spacing: 0; }
    .brand-ey span { font-size: 1rem; line-height: 1.1; padding-bottom: 3px; font-weight: 600; }
    .brand-divider { width: 1px; height: 40px; background: rgba(32, 39, 51, 0.10); }
    .brand-secondary strong { display: block; font-size: 0.95rem; line-height: 1.1; max-width: 24ch; }
    .brand-secondary span {
      display: block;
      font-size: 0.75rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .topnav { display: flex; gap: 28px; flex-wrap: wrap; justify-content: flex-end; font-size: 0.94rem; color: #4f5d6f; font-weight: 600; }
    .topnav a { text-decoration: none; padding-bottom: 2px; border-bottom: 1px solid transparent; }
    .topnav a:hover { border-color: var(--gold); color: var(--ink); }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(260px, 0.36fr);
      gap: 42px;
      align-items: center;
      min-height: 360px;
      padding: 56px 0 46px;
    }
    .hero-copy { display: flex; flex-direction: column; gap: 16px; }
    .eyebrow {
      margin: 0;
      color: #6c7583;
      font-size: 0.82rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font-weight: 700;
    }
    h1, h2, h3, h4 { margin: 0; line-height: 1.08; letter-spacing: 0; }
    h1 { font-size: clamp(2.1rem, 4vw, 3.2rem); }
    h2 { font-size: clamp(1.35rem, 2.2vw, 1.9rem); margin-bottom: 10px; }
    h3 { font-size: clamp(1.08rem, 1.6vw, 1.34rem); }
    p { margin: 0; color: var(--muted); }
    .hero-lead { max-width: 42ch; font-size: clamp(1.3rem, 2.5vw, 2.2rem); line-height: 1.3; color: #66707b; }
    .hero-meta { display: flex; flex-wrap: wrap; gap: 28px; margin-top: 10px; }
    .hero-meta span { display: block; color: #a3a9b3; font-size: 0.78rem; letter-spacing: 0.12em; text-transform: uppercase; }
    .hero-meta strong { display: block; margin-top: 4px; color: var(--ink); font-size: 1rem; }
    .hero-subcopy { max-width: 50ch; color: var(--muted); }
    .hero-side { display: grid; gap: 14px; align-content: start; }
    .hero-score-card { display: grid; place-items: center; gap: 12px; padding: 10px 0 0; }
    .hero-score-card p { text-align: center; max-width: 30ch; font-size: 0.93rem; }
    .score-ring { position: relative; display: inline-grid; place-items: center; width: var(--ring-size); min-width: var(--ring-size); margin: 0 auto; }
    .score-ring svg { width: var(--ring-size); height: var(--ring-size); transform: rotate(-90deg); }
    .ring-track { fill: none; stroke: rgba(32, 39, 51, 0.08); stroke-width: var(--ring-width); }
    .ring-progress { fill: none; stroke-width: var(--ring-width); stroke-linecap: round; stroke: var(--gold); }
    .score-ring-copy {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 6px;
      text-align: center;
      pointer-events: none;
    }
    .score-ring-copy strong { display: block; font-size: clamp(1.7rem, 1.2rem + 1vw, 2.2rem); line-height: 0.95; color: var(--ink); }
    .score-ring-copy span { display: block; font-size: 0.82rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
    .section-panel, .priority-panel { margin-top: 34px; padding: 6px 0; }
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 18px;
      margin-bottom: 16px;
      padding-top: 12px;
      border-top: 1px solid rgba(32, 39, 51, 0.08);
    }
    .section-head p { max-width: 46ch; font-size: 0.96rem; }
    .scan-marquee {
      position: relative;
      overflow-x: auto;
      margin: 6px 0 18px;
      padding: 8px 0 26px;
      scrollbar-color: rgba(32, 39, 51, 0.22) transparent;
    }
    .scan-strip { display: flex; width: max-content; }
    .scan-track { display: flex; gap: 18px; padding-right: 18px; }
    .scan-card { display: block; flex: 0 0 clamp(380px, 40vw, 620px); text-decoration: none; color: inherit; transition: transform 220ms ease, filter 220ms ease; }
    .scan-card:hover { transform: translateY(-6px) scale(1.015); filter: saturate(1.04); }
    .scan-screen {
      overflow: hidden;
      border-radius: 18px;
      border: 1px solid rgba(32, 39, 51, 0.10);
      background: #fff;
      box-shadow: 0 16px 28px rgba(32, 39, 51, 0.10);
      margin-bottom: 10px;
    }
    .scan-card img { display: block; width: 100%; aspect-ratio: 12 / 5; object-fit: cover; object-position: center; background: #fff; }
    .scan-card span, .scan-card strong, .scan-card em { display: block; }
    .scan-card span { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
    .scan-card strong { font-size: 0.98rem; line-height: 1.35; color: var(--ink); margin-top: 4px; }
    .scan-card em { margin-top: 6px; font-style: normal; color: var(--muted); font-size: 0.82rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .scan-empty .scan-screen { min-height: 170px; display: grid; place-items: center; color: var(--muted); }
    .methodology-section .section-head, .scoring-section .section-head {
      display: block;
      max-width: 760px;
      margin: 0 auto 34px;
      text-align: center;
      border-top: none;
    }
    .methodology-section .section-head p, .scoring-section .section-head p { max-width: 58ch; margin: 0 auto; }
    .source-note { margin-top: 12px !important; font-size: 0.84rem !important; color: #6d7784; }
    .source-note a { color: var(--ink); font-weight: 700; text-decoration-color: rgba(198, 161, 55, 0.55); text-underline-offset: 3px; }
    .method-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px; }
    .criteria-matrix { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }
    .criteria-card {
      display: grid;
      gap: 14px;
      padding: 22px;
      border: 1px solid rgba(32, 39, 51, 0.08);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.84);
      box-shadow: 0 18px 36px rgba(32, 39, 51, 0.05);
    }
    .criteria-card h4 { font-size: 1.02rem; }
    .criteria-label {
      display: block;
      margin-bottom: 7px;
      color: var(--muted);
      font-size: 0.74rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      font-weight: 800;
    }
    .criteria-card ul { display: grid; gap: 9px; margin: 0; padding: 0; list-style: none; }
    .criteria-card li { display: grid; gap: 2px; padding-left: 12px; border-left: 2px solid rgba(198, 161, 55, 0.55); }
    .criteria-card li strong { font-size: 0.9rem; color: var(--ink); }
    .criteria-card li span { color: var(--muted); font-size: 0.84rem; }
    .criteria-card li em { color: var(--teal); font-style: normal; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 800; }
    .method-card, .axis-tile, .reco-card {
      position: relative;
      border: 1px solid rgba(32, 39, 51, 0.08);
      border-radius: 2px;
      background: rgba(255, 255, 255, 0.84);
      box-shadow: 0 18px 36px rgba(32, 39, 51, 0.05);
    }
    .method-card { min-height: 180px; padding: 44px 28px 28px; }
    .method-card h4 { font-size: 1.06rem; margin-bottom: 10px; }
    .floating-step {
      position: absolute;
      top: -18px;
      left: 28px;
      display: inline-grid;
      place-items: center;
      width: 40px;
      height: 40px;
      border-radius: 999px;
      background: #ffe100;
      color: #111820;
      font-size: 0.92rem;
      font-weight: 700;
      letter-spacing: 0.08em;
    }
    .score-overview { display: grid; gap: 22px; grid-template-columns: 1fr; justify-items: center; margin-bottom: 18px; }
    .radar-card { width: min(100%, 680px); }
    .radar-chart { width: 100%; height: auto; display: block; }
    .radar-ring { fill: none; stroke: rgba(32, 39, 51, 0.11); stroke-width: 1; }
    .radar-shape { fill: rgba(198, 161, 55, 0.26); stroke: var(--gold); stroke-width: 3; }
    .radar-label { font-size: 12px; fill: #5d6878; font-weight: 700; }
    .axis-grid {
      display: grid;
      gap: 24px 18px;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      width: 100%;
      padding-top: 20px;
    }
    .axis-tile { display: grid; gap: 12px; align-content: start; padding: 40px 24px 24px; min-height: 220px; }
    .axis-tile h4 { font-size: 1rem; line-height: 1.3; color: var(--ink); }
    .axis-tile-meta { display: flex; align-items: baseline; gap: 8px; margin-top: auto; color: var(--muted); font-size: 0.88rem; }
    .axis-tile-meta strong { color: var(--ink); font-size: 1.5rem; }
    .stories, .axis-stories { display: grid; gap: 34px; }
    .story-row, .axis-story {
      display: grid;
      grid-template-columns: 42px minmax(420px, 1.18fr) minmax(155px, 0.34fr) minmax(0, 0.88fr);
      gap: 22px;
      align-items: center;
      padding: 12px 0 0;
    }
    .story-index {
      align-self: start;
      display: inline-grid;
      place-items: center;
      width: 44px;
      height: 44px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.08em;
    }
    .story-media, .axis-story-media { align-self: center; display: grid; gap: 10px; align-content: center; justify-items: center; }
    .story-visual-frame {
      width: 100%;
      max-width: 680px;
      overflow: hidden;
      border-radius: 22px;
      border: 1px solid rgba(32, 39, 51, 0.08);
      background: #fff;
      box-shadow: 0 18px 36px rgba(32, 39, 51, 0.08);
      margin: 0;
    }
    .desktop-screen { overflow: hidden; background: #fff; }
    .desktop-screen-bar {
      display: flex;
      align-items: center;
      gap: 7px;
      height: 28px;
      padding: 0 12px;
      border-bottom: 1px solid rgba(32, 39, 51, 0.08);
      background: linear-gradient(180deg, #f8f8f7, #eeeeec);
    }
    .desktop-screen-bar span { width: 8px; height: 8px; border-radius: 999px; background: #c8c9c7; }
    .desktop-screen-bar span:first-child { background: #e6cf67; }
    .desktop-screen-body { aspect-ratio: 16 / 9; background: #fff; display: grid; place-items: center; overflow: hidden; }
    .story-visual-frame img { display: block; width: 100%; height: 100%; aspect-ratio: 16 / 9; object-fit: contain; object-position: center; background: #fff; }
    figcaption { display: grid; gap: 2px; padding: 9px 12px 12px; color: var(--muted); font-size: 0.78rem; }
    figcaption strong { color: var(--ink); }
    .story-visual-empty {
      display: grid;
      place-items: center;
      min-height: 240px;
      padding: 18px;
      color: var(--muted);
      text-align: center;
      border: 1px dashed var(--line);
      background: rgba(255, 255, 255, 0.74);
      border-radius: 16px;
      width: 100%;
    }
    .story-visual-empty strong { display: block; color: var(--ink); }
    .story-score-pane, .axis-story-score {
      align-self: center;
      display: grid;
      gap: 12px;
      justify-items: center;
      align-content: center;
      width: min(100%, 210px);
      margin-inline: auto;
      text-align: center;
    }
    .story-score-pane > span { color: var(--muted); font-size: 0.86rem; }
    .story-copy, .axis-story-copy { align-self: center; display: grid; gap: 12px; min-width: 0; }
    .story-kicker { color: var(--muted); font-size: 0.82rem; letter-spacing: 0.1em; text-transform: uppercase; font-weight: 700; }
    .story-copy strong, .axis-story-copy strong { color: var(--ink); }
    .location-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 4px 0; }
    dt { color: var(--muted); font-size: 0.78rem; }
    dd { margin: 0; word-break: break-word; }
    code { background: rgba(32, 39, 51, 0.07); padding: 2px 4px; border-radius: 4px; }
    .review-panel {
      display: grid;
      grid-template-columns: repeat(3, max-content);
      gap: 10px;
      align-items: center;
      margin-top: 4px;
      padding: 12px;
      background: rgba(255, 255, 255, 0.62);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .review-panel label { font-size: 0.86rem; color: var(--muted); }
    textarea, button {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.94);
      color: var(--ink);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
    }
    .review-panel textarea { grid-column: 1 / -1; width: 100%; min-height: 64px; resize: vertical; }
    button { cursor: pointer; font-weight: 700; }
    button.primary { background: var(--teal); color: #fff; border-color: var(--teal); }
    .edit-toolbar {
      display: flex;
      align-items: center;
      gap: 6px;
      justify-content: flex-end;
      min-height: 30px;
    }
    .story-visual-frame { position: relative; }
    .story-title-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }
    .story-title-row h3 {
      flex: 1 1 auto;
    }
    .icon-button {
      display: inline-grid;
      place-items: center;
      width: 30px;
      height: 30px;
      padding: 0;
      color: #273242;
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid rgba(32, 39, 51, 0.16);
      border-radius: 4px;
      box-shadow: 0 8px 16px rgba(32, 39, 51, 0.10);
    }
    .icon-button:hover,
    .icon-button[aria-pressed="true"] {
      color: #fff;
      background: var(--teal);
      border-color: var(--teal);
    }
    .hidden-file-input { display: none; }
    .score-editor {
      width: 100%;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.86);
    }
    .score-editor label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }
    .score-editor input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 8px;
      color: var(--ink);
      font: inherit;
      background: #fff;
    }
    [data-editable-field][contenteditable="true"] {
      outline: 2px solid rgba(17, 136, 110, 0.34);
      border-radius: 6px;
      background: rgba(17, 136, 110, 0.08);
      padding: 2px 4px;
    }
    .reco-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
    .reco-card { display: grid; gap: 12px; padding: 28px 24px 24px; min-height: 310px; overflow: hidden; }
    .reco-orb {
      position: absolute;
      top: 16px;
      right: 16px;
      color: rgba(32, 39, 51, 0.18);
      font-size: 2.1rem;
      font-weight: 800;
    }
    .reco-badge { width: max-content; border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; color: var(--muted); font-size: 0.78rem; }
    .reco-impact { font-size: 0.9rem; }
    .reco-axis, .reco-cta { display: block; color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; }
    .reco-cta { margin-top: auto; color: var(--teal); }
    .empty-state {
      background: rgba(255, 255, 255, 0.76);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      color: var(--muted);
    }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .review-only-grid { display: grid; gap: 12px; margin-top: 14px; }
    .review-only-card {
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.74);
    }
    .review-only-card h4 { margin-bottom: 10px; }
    .export-box { width: 100%; min-height: 180px; margin-top: 12px; font-family: Consolas, monospace; font-size: 13px; }
    details summary { cursor: pointer; color: var(--ink); font-weight: 700; }
    .footer {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      margin-top: 56px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.86rem;
    }
    .local-publish-panel {
      display: grid;
      gap: 14px;
      margin-top: 42px;
      padding: 24px;
      border: 1px solid rgba(198,161,55,0.24);
      border-radius: 8px;
      background: rgba(255,255,255,0.76);
      box-shadow: 0 18px 36px rgba(32,39,51,0.05);
    }
    .local-publish-panel h2 {
      font-size: clamp(1.35rem, 2vw, 1.8rem);
      line-height: 1.12;
    }
    .local-publish-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .publish-button {
      border: 1px solid var(--ink);
      border-radius: 4px;
      padding: 12px 16px;
      background: var(--ink);
      color: #fff;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }
    .publish-button:disabled {
      cursor: wait;
      opacity: 0.62;
    }
    .publish-status {
      color: var(--muted);
      font-size: 0.92rem;
    }
    .publish-status a {
      color: var(--ink);
      font-weight: 800;
      text-underline-offset: 3px;
    }
    @media (prefers-reduced-motion: reduce) {
      .scan-strip { animation: none; }
      .scan-marquee { overflow-x: auto; mask-image: none; }
      .scan-card { transition: none; }
    }
    @media (max-width: 1020px) {
      .topbar { position: static; flex-direction: column; align-items: flex-start; }
      .hero, .story-row, .axis-story { grid-template-columns: 1fr; }
      .story-index { align-self: auto; }
      .method-grid { grid-template-columns: 1fr; }
      .section-head { align-items: flex-start; flex-direction: column; }
    }
    @media (max-width: 640px) {
      .shell { padding-inline: 16px; }
      .topnav { gap: 14px; }
      .brand-lockups { flex-wrap: wrap; }
      .brand-divider { display: none; }
      .review-panel { grid-template-columns: 1fr; }
      .hero-meta { gap: 16px; }
    }
    """


def _review_script(
    detection_result: DetectionResult,
    grouped: dict[str, list[dict[str, object]]],
) -> str:
    payload = _score_model_payload(detection_result, grouped)
    return """
  <script type="application/json" id="audit-score-model">__AUDIT_SCORE_MODEL__</script>
  <script>
  (() => {
    const modelNode = document.getElementById("audit-score-model");
    const model = modelNode ? JSON.parse(modelNode.textContent || "{}") : {};
    const criteria = model.criteria || {};
    const issues = model.issues || {};
    const issueScores = new Map(
      Object.entries(issues).map(([issueId, issue]) => [issueId, clampScore(issue.score)])
    );

    function clampScore(value) {
      const score = Number.parseFloat(value);
      if (!Number.isFinite(score)) return 10;
      return Math.max(0, Math.min(10, Math.round(score * 10) / 10));
    }

    function scoreText(score) {
      return clampScore(score).toFixed(1);
    }

    function setRingScore(ring, score) {
      const nextScore = clampScore(score);
      ring.dataset.score = scoreText(nextScore);
      const text = ring.querySelector("[data-score-text]");
      if (text) text.textContent = scoreText(nextScore);
      const progress = ring.querySelector(".ring-progress");
      if (!progress) return;
      const radius = Number.parseFloat(progress.getAttribute("r") || "0");
      if (!Number.isFinite(radius) || radius <= 0) return;
      const circumference = 2 * Math.PI * radius;
      progress.style.strokeDasharray = circumference.toFixed(2);
      progress.style.strokeDashoffset = (circumference * (1 - nextScore / 10)).toFixed(2);
    }

    function elementsForIssue(selector, issueId) {
      return Array.from(document.querySelectorAll(selector)).filter((element) => {
        if (element.dataset.issueId === issueId || element.dataset.editableImage === issueId) {
          return true;
        }
        const host = element.closest("[data-issue-id]");
        return Boolean(host && host.dataset.issueId === issueId);
      });
    }

    function updateIssueScore(issueId, score) {
      const nextScore = clampScore(score);
      issueScores.set(issueId, nextScore);
      elementsForIssue('[data-score-role="issue"]', issueId).forEach((ring) => {
        setRingScore(ring, nextScore);
      });
      elementsForIssue("[data-score-input]", issueId).forEach((input) => {
        if (document.activeElement !== input) input.value = scoreText(nextScore);
      });
      recalculateScores();
    }

    function criterionScore(criterionId) {
      const criterion = criteria[criterionId] || {};
      const issueIds = Array.isArray(criterion.issueIds) ? criterion.issueIds : [];
      const scores = issueIds
        .filter((issueId) => issueScores.has(issueId))
        .map((issueId) => issueScores.get(issueId));
      if (!scores.length) return clampScore(criterion.score);
      return clampScore(scores.reduce((total, score) => total + score, 0) / scores.length);
    }

    function recalculateScores() {
      const criterionIds = Object.keys(criteria);
      const criterionScores = criterionIds.map((criterionId) => {
        const score = criterionScore(criterionId);
        document
          .querySelectorAll(`[data-score-role="criterion"][data-criterion-id="${criterionId}"]`)
          .forEach((ring) => setRingScore(ring, score));
        document
          .querySelectorAll(`[data-criterion-score-value="${criterionId}"]`)
          .forEach((node) => {
            node.textContent = scoreText(score);
          });
        return score;
      });
      if (!criterionScores.length) return;
      const average =
        criterionScores.reduce((total, score) => total + score, 0) / criterionScores.length;
      const overall = clampScore(average);
      document
        .querySelectorAll('[data-score-role="overall"][data-score-locked="true"]')
        .forEach((ring) => setRingScore(ring, overall));
    }

    function toggleCopyEditing(issueId, button) {
      const fields = elementsForIssue("[data-editable-field]", issueId);
      const isEditing = button.getAttribute("aria-pressed") !== "true";
      elementsForIssue('[data-edit-action="copy"]', issueId).forEach((control) => {
        control.setAttribute("aria-pressed", isEditing ? "true" : "false");
      });
      fields.forEach((field) => {
        field.contentEditable = isEditing ? "true" : "false";
      });
      if (isEditing && fields[0]) fields[0].focus();
    }

    function toggleScoreEditor(issueId, button) {
      const editors = elementsForIssue(".score-editor", issueId);
      const isOpen = button.getAttribute("aria-pressed") !== "true";
      elementsForIssue('[data-edit-action="score"]', issueId).forEach((control) => {
        control.setAttribute("aria-pressed", isOpen ? "true" : "false");
      });
      editors.forEach((editor) => {
        editor.hidden = !isOpen;
      });
      if (isOpen) {
        const input = editors[0] ? editors[0].querySelector("[data-score-input]") : null;
        if (input) input.focus();
      }
    }

    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-edit-action]");
      if (!button) return;
      const issueId = button.dataset.issueId;
      if (!issueId) return;
      const action = button.dataset.editAction;
      if (action === "image") {
        const input = elementsForIssue("[data-screenshot-input]", issueId)[0];
        if (input) input.click();
      }
      if (action === "copy") toggleCopyEditing(issueId, button);
      if (action === "score") toggleScoreEditor(issueId, button);
    });

    document.addEventListener("change", (event) => {
      const input = event.target.closest("[data-screenshot-input]");
      if (!input || !input.files || !input.files[0]) return;
      const issueId = input.dataset.issueId;
      const reader = new FileReader();
      reader.addEventListener("load", () => {
        document
          .querySelectorAll(`[data-editable-image="${issueId}"]`)
          .forEach((image) => {
            image.src = String(reader.result || "");
          });
      });
      reader.readAsDataURL(input.files[0]);
    });

    document.addEventListener("input", (event) => {
      const scoreInput = event.target.closest("[data-score-input]");
      if (scoreInput) {
        updateIssueScore(scoreInput.dataset.issueId, scoreInput.value);
        return;
      }
      const field = event.target.closest("[data-editable-field]");
      if (!field) return;
      const host = field.closest("[data-issue-id]");
      if (!host || !host.dataset.issueId) return;
      const issueId = host.dataset.issueId;
      const fieldName = field.dataset.editableField;
      elementsForIssue(`[data-editable-field="${fieldName}"]`, issueId).forEach((other) => {
        if (other !== field) other.textContent = field.textContent;
      });
    });

    recalculateScores();

    const localHosts = new Set(["127.0.0.1", "localhost", "::1"]);
    const publishPanel = document.querySelector("[data-local-publish-panel]");
    const isAuditPath = window.location.pathname.startsWith("/audits/");
    const isKnownStaticDeployment = /(?:^|\\.)vercel\\.app$/i.test(window.location.hostname) || /(?:^|\\.)vercel\\.com$/i.test(window.location.hostname);
    const isLocalEditable = !isKnownStaticDeployment && (isAuditPath || localHosts.has(window.location.hostname) || window.location.protocol === "file:");
    const canDeployFromHere = isAuditPath && !isKnownStaticDeployment && /^https?:$/.test(window.location.protocol);
    if (publishPanel && !isLocalEditable) publishPanel.hidden = true;
    document.querySelectorAll("[data-local-edit-control]").forEach((control) => {
      if (!isLocalEditable) control.hidden = true;
    });

    function cleanCloneForDeployment() {
      const clone = document.documentElement.cloneNode(true);
      clone.querySelectorAll("[contenteditable]").forEach((node) => node.removeAttribute("contenteditable"));
      clone.querySelectorAll("[data-editable-field]").forEach((node) => node.removeAttribute("data-editable-field"));
      clone.querySelectorAll("[data-editable-image]").forEach((node) => node.removeAttribute("data-editable-image"));
      clone.querySelectorAll("[data-local-edit-control], [data-local-publish-panel]").forEach((node) => node.remove());
      clone.querySelectorAll("[data-deploy-status]").forEach((node) => {
        node.textContent = "Published version generated from reviewed local edits.";
      });
      return "<!doctype html>\\n" + clone.outerHTML;
    }

    async function readDeployPayload(response) {
      const text = await response.text();
      if (!text.trim()) return {};
      try {
        return JSON.parse(text);
      } catch (_error) {
        const preview = text.replace(/\\s+/g, " ").trim().slice(0, 180);
        throw new Error(`Deployment endpoint did not return JSON (${response.status}). Open the /audits report from the local Python server, then retry. Response started with: ${preview}`);
      }
    }

    async function postEditedReport(body) {
      const params = new URLSearchParams(window.location.search);
      const configuredApiBase = String(params.get("apiBaseUrl") || params.get("backend") || params.get("api") || "").replace(/\\/+$/, "");
      const requestHeaders = {"Content-Type": "application/json"};
      if (configuredApiBase.includes("ngrok")) requestHeaders["ngrok-skip-browser-warning"] = "true";
      const endpoints = [
        ...(configuredApiBase ? [`${configuredApiBase}/api/reports/deploy`] : []),
        new URL("/api/reports/deploy", window.location.href).href,
        new URL("api/reports/deploy", window.location.href).href,
        new URL("../../api/reports/deploy", window.location.href).href
      ];
      let lastPayload = null;
      let lastResponse = null;
      let lastError = null;
      for (const endpoint of Array.from(new Set(endpoints))) {
        try {
          const response = await fetch(endpoint, {
            method: "POST",
            headers: requestHeaders,
            body
          });
          const payload = await readDeployPayload(response);
          if (response.ok) return payload;
          lastPayload = payload;
          lastResponse = response;
          if (response.status !== 404) break;
        } catch (error) {
          lastError = error;
        }
      }
      if (lastPayload && lastPayload.error) throw new Error(lastPayload.error);
      if (lastError instanceof Error) throw lastError;
      throw new Error(lastResponse ? `Deployment endpoint failed with status ${lastResponse.status}.` : "Deployment endpoint could not be reached.");
    }

    const deployButton = document.querySelector("[data-deploy-edited-report]");
    const deployStatus = document.querySelector("[data-deploy-status]");
    if (deployButton) {
      if (!canDeployFromHere) {
        deployButton.disabled = true;
        if (deployStatus && isLocalEditable) deployStatus.textContent = "Open this report from the local UI server to deploy it.";
      }
      deployButton.addEventListener("click", async () => {
        if (!canDeployFromHere) return;
        deployButton.disabled = true;
        if (deployStatus) deployStatus.textContent = "Saving edited audit and deploying to Vercel...";
        try {
          const payload = await postEditedReport(JSON.stringify({
              path: window.location.pathname,
              html: cleanCloneForDeployment()
            }));
          if (deployStatus) {
            deployStatus.innerHTML = `Deployed: <a href="${payload.url}" target="_blank" rel="noreferrer">${payload.url}</a>`;
          }
        } catch (error) {
          if (deployStatus) deployStatus.textContent = error instanceof Error ? error.message : "Deployment failed.";
          deployButton.disabled = false;
        }
      });
    }
  })();
  </script>
""".replace("__AUDIT_SCORE_MODEL__", payload)


def build_detection_review_report(
    *,
    detections_path: Path,
    output_path: Path,
    polish_copy: bool = OLLAMA_REPORT_POLISH,
    ai_review: bool = OLLAMA_AI_REVIEW,
    force_polish: bool = False,
    log: object = print,
) -> Path:
    """Build a local static HTML review report for draft detections."""
    data = load_json(detections_path)
    detection_result = DetectionResult.model_validate(data)
    catalog = load_criteria_catalog()
    lookup = _criterion_lookup(catalog.criteria)
    active_criteria = set(lookup)
    raw_detection_result = detection_result.model_dump(mode="json")
    raw_detection_result["criterion_status"] = [
        status
        for status in raw_detection_result.get("criterion_status", [])
        if isinstance(status, dict) and str(status.get("criterion_id") or "") in active_criteria
    ]
    raw_detection_result["draft_issues"] = [
        issue
        for issue in raw_detection_result.get("draft_issues", [])
        if isinstance(issue, dict)
        and str(issue.get("criterion") or issue.get("axis") or "") in active_criteria
    ]
    detection_result = DetectionResult.model_validate(raw_detection_result)
    if ai_review:
        raw_issues = detection_result.model_dump(mode="json")["draft_issues"]
        try:
            reviews = review_issues_with_ollama(
                issues=[
                    issue
                    for issue in raw_issues
                    if isinstance(issue, dict) and not _issue_rejected_by_client_view(issue)
                ],
                criteria_by_id=lookup,
                cache_path=output_path.parent / "ai_review.json",
                force=force_polish,
                log=log,
            )
            if reviews:
                for issue in raw_issues:
                    if not isinstance(issue, dict):
                        continue
                    issue_id = str(issue.get("id") or "")
                    review = reviews.get(issue_id)
                    if not review:
                        continue
                    evidence = issue.setdefault("evidence", {})
                    if isinstance(evidence, dict):
                        evidence["ai_review"] = review
                detection_result = DetectionResult.model_validate(
                    {**detection_result.model_dump(mode="json"), "draft_issues": raw_issues}
                )
        except ReportPolishError as exc:
            if callable(log):
                log(f"Warning: {exc}")
    grouped = _issues_by_criterion(detection_result)
    issue_list = _issues(detection_result)
    actionable_issue_list = _actionable_issues(detection_result, grouped)
    actionable_criterion_count = sum(
        1
        for status in detection_result.criterion_status
        if _criterion_is_actionable(_criterion_score(grouped.get(status.criterion_id, [])))
    )
    axis_scores = [
        _criterion_score(grouped.get(status.criterion_id, []))
        for status in detection_result.criterion_status
    ]
    overall = _overall_score(axis_scores, len(actionable_issue_list))
    file_label = _file_label(detection_result)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    for generated_dir, pattern in (
        (output_path.parent / "callouts", "*-callout.png"),
        (output_path.parent / "client_views", "*-client-view.png"),
        (output_path.parent / "context_views", "*-context.png"),
        (output_path.parent / "page_views", "*-page-scan.png"),
    ):
        if generated_dir.exists():
            for stale_image in generated_dir.glob(pattern):
                try:
                    stale_image.unlink()
                except OSError:
                    pass
    polished_copy: dict[str, dict[str, str]] = {}
    if polish_copy:
        try:
            polished_copy = polish_report_copy_with_ollama(
                issues=issue_list,
                criteria_by_id=lookup,
                cache_path=output_path.parent / "client_copy.json",
                force=force_polish,
                log=log,
            )
        except ReportPolishError as exc:
            if callable(log):
                log(f"Warning: {exc}")

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UX/UI Draft Detection Review</title>
  <style>{_style()}</style>
</head>
<body>
  <div class="shell">
    <nav class="topbar" aria-label="Report navigation">
      <div class="brand-lockups">
        <div class="brand-primary">
          <div class="brand-ey"><strong>UX</strong><span>Audit</span></div>
        </div>
        <div class="brand-divider"></div>
        <div class="brand-secondary">
          <div>
            <span>Audit for</span>
            <strong>{_html(file_label)}</strong>
          </div>
        </div>
      </div>
      <div class="topnav">
        <a href="#methodology">Context &amp; Methodology</a>
        <a href="#findings">Findings</a>
        <a href="#scores">Scores</a>
        <a href="#recommendations">Recommendations</a>
      </div>
    </nav>

    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">UX/UI Draft Detection Review</p>
        <h1>Figma audit report</h1>
        <p class="hero-lead">Executive review of the visible Figma design through {_html(detection_result.summary.criteria_total)} axes of UX/UI analysis.</p>
        <div class="hero-meta">
          <div><span>Criteria with problems</span><strong>{_html(actionable_criterion_count)}</strong></div>
          <div><span>Shown issues</span><strong>{_html(len(actionable_issue_list))}</strong></div>
          <div><span>Screenshots</span><strong>{_html(detection_result.summary.screenshot_count)}</strong></div>
          <div><span>Audit axes</span><strong>{_html(detection_result.summary.criteria_total)}</strong></div>
        </div>
        <p class="hero-subcopy">This report is tuned for mobile app Figma reviews: phone-sized visible screens, foreground layers, score overview, priority findings, axis stories, and recommended moves.</p>
      </div>
      <div class="hero-side">
        <div class="hero-score-card">
          {_score_ring(overall, size=170, label="overall", attrs={"data-score-role": "overall", "data-score-locked": "true"})}
          <p>Draft readiness score based on detected issues, severity, confidence, visual evidence strength, heuristic impact, and repetition across the design.</p>
        </div>
      </div>
    </section>

    {_representative_pages(detection_result, grouped, output_path, polished_copy)}

    <section class="section-panel" id="context">
      <div class="section-head">
        <div>
          <p class="eyebrow">Context</p>
          <h2>Audit framing</h2>
        </div>
      </div>
      <div class="axis-grid">
        <article class="axis-tile"><h4>Figma source</h4><p>{_html(file_label)}</p><div class="axis-tile-meta"><strong>{_html(detection_result.summary.screenshot_count)}</strong><span>visual evidence item(s)</span></div></article>
        <article class="axis-tile"><h4>Audit status</h4><p>{_html(_status_label(detection_result.summary.status))}</p><div class="axis-tile-meta"><strong>{_html(len(actionable_issue_list))}</strong><span>priority issue(s)</span></div></article>
        <article class="axis-tile"><h4>Audit scope</h4><p>Static mobile Figma review focused on visible phone screens, foreground/top-layer evidence, confidence, and screenshots. Formal accessibility and usability conclusions still need human validation.</p><div class="axis-tile-meta"><strong>{_html(detection_result.summary.criteria_total)}</strong><span>criteria reviewed</span></div></article>
      </div>
    </section>

    {_criteria_matrix_section(lookup)}
    {_methodology_section()}
    {_scoring_section(detection_result, lookup, grouped)}
    {_priority_section(detection_result, grouped, output_path, lookup, polished_copy)}
    {_axis_stories_section(detection_result, grouped, output_path, lookup, polished_copy)}
    {_recommendations_section(detection_result, grouped, lookup, polished_copy)}

    <section class="local-publish-panel" data-local-publish-panel>
      <div>
        <p class="eyebrow">Finalize</p>
        <h2>Review, edit, then deploy the final audit</h2>
        <p>Adjust text, screenshots, and scores locally. When the report is ready, deploy this edited version to Vercel.</p>
      </div>
      <div class="local-publish-actions">
        <button class="publish-button" type="button" data-deploy-edited-report>Deploy final audit to Vercel</button>
        <span class="publish-status" data-deploy-status>Local edits are not published until you deploy.</span>
      </div>
    </section>

    <footer class="footer">
      <span>Generated from the local Figma audit pipeline.</span>
      <span>{_html(file_label)}</span>
    </footer>
  </div>
  {_review_script(detection_result, grouped)}
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    return output_path
