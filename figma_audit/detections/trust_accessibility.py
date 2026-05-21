from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import uuid4

from figma_audit.audit.context import AuditContext, CONTAINER_TYPES
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import make_issue, normalized_text
from figma_audit.models.issue import AuditIssue, IssueLocation, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


MOBILE_TOUCH_TARGET_RECOMMENDED_MIN = 44.0
MOBILE_TOUCH_TARGET_CRITICAL_MIN = 24.0
MAX_TOUCH_TARGET_ISSUES = 3
TIME_STATUS_RE = re.compile(r"\b(\d+\s*(min|mins|minute|minutes|hour|hours|day|days)\s+ago|eta|arrive|delivery|status)\b", re.I)
CHOICE_LABELS = {
    "mild",
    "medium",
    "hot",
    "spicy",
    "small",
    "regular",
    "large",
    "low",
    "high",
}
OPTION_CONTEXT_WORDS = {
    "choice",
    "customize",
    "filter",
    "option",
    "portion",
    "quantity",
    "select",
    "side",
    "size",
    "spicy",
    "topping",
}
WCAG_22_SOURCE = {
    "title": "Web Content Accessibility Guidelines (WCAG) 2.2",
    "publisher": "W3C",
    "year": 2023,
    "url": "https://www.w3.org/TR/WCAG22/",
    "success_criteria": {
        "contrast": "1.4.3 Contrast (Minimum)",
        "non_text_contrast": "1.4.11 Non-text Contrast",
        "focus_visible": "2.4.7 Focus Visible",
        "focus_appearance": "2.4.13 Focus Appearance",
        "target_size": "2.5.8 Target Size (Minimum)",
        "labels_or_instructions": "3.3.2 Labels or Instructions",
        "consistent_identification": "3.2.4 Consistent Identification",
        "name_role_value": "4.1.2 Name, Role, Value",
    },
}
ACTION_LABELS = {
    "add",
    "apply",
    "back",
    "cancel",
    "close",
    "confirm",
    "continue",
    "delete",
    "done",
    "edit",
    "filter",
    "menu",
    "next",
    "open",
    "remove",
    "save",
    "search",
    "send",
    "settings",
    "share",
    "submit",
}
OBVIOUS_ICON_TERMS = {
    "add",
    "back",
    "calendar",
    "cart",
    "check",
    "close",
    "delete",
    "edit",
    "filter",
    "heart",
    "home",
    "menu",
    "minus",
    "notification",
    "plus",
    "profile",
    "search",
    "send",
    "settings",
    "share",
    "star",
    "trash",
    "user",
}
WEAK_ICON_TERMS = {
    "button",
    "circle",
    "control",
    "ellipse",
    "frame",
    "group",
    "icon",
    "shape",
    "vector",
}
IMPORTANT_TEXT_CONTEXT_WORDS = {
    "amount",
    "button",
    "cta",
    "error",
    "label",
    "price",
    "required",
    "status",
    "submit",
    "title",
    "total",
    "warning",
}
MIN_IMPORTANT_TEXT_SIZE = 11.0
MIN_DENSE_LINE_HEIGHT_RATIO = 1.12
MIN_CONTROL_GAP = 8.0
EDGE_SAFE_AREA = 12.0


@dataclass(frozen=True)
class TouchTargetCandidate:
    node: NormalizedNode
    source_node_id: str
    accessible_text: str


def _font_number(style: dict[str, object], key: str, fallback: float) -> float:
    value = style.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


def _severity_for_contrast(ratio: float, threshold: float) -> Severity:
    if ratio < 3.0:
        return Severity.HIGH
    if threshold - ratio >= 0.75:
        return Severity.MEDIUM
    return Severity.LOW


def _text_sample(node: NormalizedNode) -> str:
    return (node.characters or "").strip().replace("\n", " ")[:120]


def _contrast_text_role(
    ctx: AuditContext,
    node: NormalizedNode,
    *,
    sample: str,
    font_size: float,
) -> tuple[str, str, float]:
    """
    Classify visible text by user impact so contrast issues are ranked like a
    mobile app review, not as a flat list of every failing text node.
    """
    text = normalized_text(sample)
    path = normalized_text(" ".join(part for part in [node.name, node.path, node.frame_name] if part))
    ancestor_context = " ".join(
        normalized_text(ancestor.name)
        for ancestor in ctx.iter_ancestors(node)
        if ancestor.name
    )
    context = f"{path} {ancestor_context}"

    if TIME_STATUS_RE.search(text) or TIME_STATUS_RE.search(context):
        return "status_or_time_feedback", "important", 4.4
    if text in ACTION_LABELS or any(label == text for label in ACTION_LABELS):
        return "action_label", "critical", 4.8
    if text in CHOICE_LABELS or any(word in context for word in OPTION_CONTEXT_WORDS):
        return "choice_or_setting_label", "important", 4.1
    if font_size >= 17 or any(word in context for word in {"title", "heading", "price", "total"}):
        return "primary_content", "critical", 4.6
    if len(text) >= 35:
        return "body_content", "important", 3.8
    if font_size <= 9:
        return "supporting_metadata", "secondary", 2.2
    return "secondary_label", "secondary", 2.6


WCAG_CHECKS_BY_DETECTOR = {
    "text_contrast": ["1.4.3 Contrast (Minimum)"],
    "touch_target_size": ["2.5.8 Target Size (Minimum)"],
    "touch_target_spacing": ["2.5.8 Target Size (Minimum)"],
    "edge_safe_touch_area": ["2.5.8 Target Size (Minimum)"],
    "icon_only_action_label": [
        "3.3.2 Labels or Instructions",
        "4.1.2 Name, Role, Value",
    ],
    "small_important_text": ["1.4.3 Contrast (Minimum)", "1.4.12 Text Spacing"],
    "dense_line_spacing": ["1.4.12 Text Spacing"],
}


def _accessibility_validation_evidence(check: str) -> dict[str, object]:
    return {
        "validation_method": "wcag_22_static_figma_accessibility_gate",
        "validation_source": WCAG_22_SOURCE,
        "validation_question": (
            "Can users with visual, motor, or assistive-technology needs perceive the content and operate visible controls?"
        ),
        "wcag_version": "2.2",
        "wcag_success_criteria": WCAG_CHECKS_BY_DETECTOR.get(check, []),
        "accessibility_check": check,
    }


def _severity_rank(severity: Severity) -> int:
    return {
        Severity.HIGH: 3,
        Severity.MEDIUM: 2,
        Severity.LOW: 1,
    }.get(severity, 0)


def _control_text(ctx: AuditContext, node: NormalizedNode) -> str:
    parts = [
        normalized_text(text.characters)
        for text in ctx.text_nodes_in_subtree(node)
        if ctx.has_text(text)
    ]
    if node.type == "TEXT" and ctx.has_text(node):
        parts.insert(0, normalized_text(node.characters))
    return " ".join(part for part in parts if part)


def _contains_action_label(ctx: AuditContext, node: NormalizedNode) -> bool:
    text = _control_text(ctx, node)
    return any(label == text or (len(label) > 3 and label in text) for label in ACTION_LABELS)


def _line_height_number(style: dict[str, object], font_size: float) -> float | None:
    for key in ("lineHeightPx", "lineHeight"):
        value = style.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    percent = style.get("lineHeightPercent")
    if isinstance(percent, (int, float)) and font_size > 0:
        return float(percent) * font_size / 100.0
    return None


def _text_role_context(ctx: AuditContext, node: NormalizedNode, sample: str) -> str:
    ancestor_context = " ".join(
        normalized_text(ancestor.name)
        for ancestor in ctx.iter_ancestors(node)
        if ancestor.name
    )
    return " ".join(
        part
        for part in [
            normalized_text(sample),
            normalized_text(node.name),
            normalized_text(node.frame_name),
            normalized_text(node.path),
            ancestor_context,
        ]
        if part
    )


def _important_text_reason(ctx: AuditContext, node: NormalizedNode, sample: str, font_size: float) -> str | None:
    text = normalized_text(sample)
    context = _text_role_context(ctx, node, sample)
    if TIME_STATUS_RE.search(text) or TIME_STATUS_RE.search(context):
        return "status_or_time_feedback"
    if text in ACTION_LABELS or any(ctx.is_control_like(ancestor) for ancestor in ctx.iter_ancestors(node)):
        return "action_or_button_label"
    if text in CHOICE_LABELS or any(word in context for word in OPTION_CONTEXT_WORDS):
        return "choice_or_setting_label"
    if any(word in context for word in IMPORTANT_TEXT_CONTEXT_WORDS):
        return "important_labeled_content"
    if len(text) >= 24 and font_size >= 10:
        return "instruction_or_body_copy"
    return None


def _weak_icon_name(value: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", normalized_text(value)))
    if not tokens:
        return True
    if tokens & OBVIOUS_ICON_TERMS:
        return False
    return bool(tokens & WEAK_ICON_TERMS) or len(tokens) <= 2


def _is_reasonable_control_container(
    ctx: AuditContext,
    node: NormalizedNode,
    viewport: NormalizedNode | None,
) -> bool:
    if node.type not in CONTAINER_TYPES:
        return False
    box = ctx.render_or_bbox(node)
    if not box:
        return False
    width = ctx.bbox_width(node)
    height = ctx.bbox_height(node)
    if width <= 0 or height <= 0:
        return False
    if viewport is not None:
        viewport_area = ctx.bbox_area(viewport)
        if viewport_area > 0 and ctx.bbox_area(node) > viewport_area * 0.35:
            return False
    return ctx.is_control_like(node) or _contains_action_label(ctx, node)


def _nearest_control_target(
    ctx: AuditContext,
    node: NormalizedNode,
) -> TouchTargetCandidate | None:
    viewport = ctx.mobile_viewport_for(node)
    candidates: list[NormalizedNode] = []
    for ancestor in ctx.iter_ancestors(node):
        if viewport is not None and ancestor.id == viewport.id:
            break
        if _is_reasonable_control_container(ctx, ancestor, viewport):
            candidates.append(ancestor)
    if _is_reasonable_control_container(ctx, node, viewport):
        candidates.append(node)
    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: (
            min(ctx.bbox_width(candidate), ctx.bbox_height(candidate)),
            ctx.bbox_area(candidate),
        ),
        reverse=True,
    )
    target = candidates[0]
    return TouchTargetCandidate(
        node=target,
        source_node_id=node.id,
        accessible_text=_control_text(ctx, target),
    )


def _candidate_touch_targets(ctx: AuditContext) -> list[TouchTargetCandidate]:
    candidates_by_target: dict[str, TouchTargetCandidate] = {}
    for node in ctx.client_visible_nodes:
        if node.type not in CONTAINER_TYPES and node.type not in {"TEXT", "VECTOR", "BOOLEAN_OPERATION", "ELLIPSE", "LINE"}:
            continue
        if not (ctx.is_control_like(node) or _contains_action_label(ctx, node)):
            continue
        candidate = _nearest_control_target(ctx, node)
        if candidate is None:
            continue
        existing = candidates_by_target.get(candidate.node.id)
        if existing is None or ctx.subtree_count(candidate.node) > ctx.subtree_count(existing.node):
            candidates_by_target[candidate.node.id] = candidate
    return list(candidates_by_target.values())


def _nearest_target_gap(ctx: AuditContext, target: NormalizedNode, targets: list[NormalizedNode]) -> float | None:
    target_box = ctx.render_or_bbox(target)
    if not target_box:
        return None
    gaps: list[float] = []
    x = float(target_box.get("x") or 0)
    y = float(target_box.get("y") or 0)
    width = float(target_box.get("width") or 0)
    height = float(target_box.get("height") or 0)
    for other in targets:
        if other.id == target.id:
            continue
        other_box = ctx.render_or_bbox(other)
        if not other_box:
            continue
        ox = float(other_box.get("x") or 0)
        oy = float(other_box.get("y") or 0)
        ow = float(other_box.get("width") or 0)
        oh = float(other_box.get("height") or 0)
        horizontal_gap = max(0.0, max(ox - (x + width), x - (ox + ow)))
        vertical_gap = max(0.0, max(oy - (y + height), y - (oy + oh)))
        gaps.append((horizontal_gap**2 + vertical_gap**2) ** 0.5)
    if not gaps:
        return None
    return round(min(gaps), 2)


def _detect_small_touch_targets(ctx: AuditContext) -> list[tuple[int, float, float, AuditIssue]]:
    candidates = _candidate_touch_targets(ctx)
    target_nodes = [candidate.node for candidate in candidates]
    ranked: list[tuple[int, float, float, AuditIssue]] = []
    seen: set[str] = set()
    seen_families: set[tuple[str, int, int, str]] = set()
    for candidate in candidates:
        target = candidate.node
        if target.id in seen:
            continue
        seen.add(target.id)
        width = ctx.bbox_width(target)
        height = ctx.bbox_height(target)
        smallest_side = min(width, height)
        if smallest_side >= MOBILE_TOUCH_TARGET_RECOMMENDED_MIN:
            continue

        severity = Severity.HIGH if smallest_side < MOBILE_TOUCH_TARGET_CRITICAL_MIN else Severity.MEDIUM
        shortfall = round(MOBILE_TOUCH_TARGET_RECOMMENDED_MIN - smallest_side, 2)
        family_key = (
            ctx.family_key(target),
            round(width),
            round(height),
            normalized_text(candidate.accessible_text or target.name),
        )
        if family_key in seen_families:
            continue
        seen_families.add(family_key)
        nearest_gap = _nearest_target_gap(ctx, target, target_nodes)
        confidence = "high" if smallest_side < MOBILE_TOUCH_TARGET_CRITICAL_MIN else "medium"
        issue = make_issue(
            ctx=ctx,
            issue_id=f"draft-small-touch-target-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=severity,
            message="A visible control appears smaller than the recommended mobile touch target size.",
            node=target,
            detector_id="small_touch_target",
            confidence=confidence,
            confidence_reason=(
                "A visible mobile control-like container has a measured Figma bounding box below the "
                "recommended touch target size, which can make it harder to tap accurately."
            ),
            evidence={
                **_accessibility_validation_evidence("touch_target_size"),
                "target_width": round(width, 2),
                "target_height": round(height, 2),
                "smallest_side": round(smallest_side, 2),
                "recommended_min_side": MOBILE_TOUCH_TARGET_RECOMMENDED_MIN,
                "critical_min_side": MOBILE_TOUCH_TARGET_CRITICAL_MIN,
                "touch_target_shortfall": shortfall,
                "nearest_control_gap": nearest_gap,
                "visible_control_text": candidate.accessible_text,
                "source_node_id": candidate.source_node_id,
                "limitations": [
                    "Static Figma bounds estimate visible target size but cannot prove implemented hit slop.",
                    "Runtime accessibility services and device testing are still required for formal conformance.",
                ],
            },
        )
        ranked.append((_severity_rank(severity), shortfall, ctx.visual_priority(target), issue))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[:MAX_TOUCH_TARGET_ISSUES]


def _detect_crowded_touch_targets(ctx: AuditContext) -> list[tuple[int, float, float, AuditIssue]]:
    candidates = _candidate_touch_targets(ctx)
    target_nodes = [candidate.node for candidate in candidates]
    ranked: list[tuple[int, float, float, AuditIssue]] = []
    seen: set[str] = set()
    for candidate in candidates:
        target = candidate.node
        if target.id in seen:
            continue
        seen.add(target.id)
        nearest_gap = _nearest_target_gap(ctx, target, target_nodes)
        if nearest_gap is None or nearest_gap >= MIN_CONTROL_GAP:
            continue
        if min(ctx.bbox_width(target), ctx.bbox_height(target)) >= 64 and nearest_gap >= 4:
            continue
        issue = make_issue(
            ctx=ctx,
            issue_id=f"draft-crowded-touch-target-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=Severity.MEDIUM,
            message="Interactive controls are placed too close together for reliable mobile tapping.",
            node=target,
            detector_id="crowded_touch_target",
            confidence="medium",
            confidence_reason=(
                "A visible mobile control sits very close to another control, increasing the chance of accidental taps."
            ),
            evidence={
                **_accessibility_validation_evidence("touch_target_spacing"),
                "nearest_control_gap": nearest_gap,
                "minimum_recommended_gap": MIN_CONTROL_GAP,
                "target_width": round(ctx.bbox_width(target), 2),
                "target_height": round(ctx.bbox_height(target), 2),
                "visible_control_text": candidate.accessible_text,
            },
        )
        ranked.append((2, max(0.0, MIN_CONTROL_GAP - nearest_gap), ctx.visual_priority(target), issue))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[:2]


def _detect_edge_crowded_controls(ctx: AuditContext) -> list[tuple[int, float, float, AuditIssue]]:
    candidates = _candidate_touch_targets(ctx)
    ranked: list[tuple[int, float, float, AuditIssue]] = []
    seen: set[str] = set()
    for candidate in candidates:
        target = candidate.node
        viewport = ctx.mobile_viewport_for(target)
        if viewport is None or target.id in seen:
            continue
        seen.add(target.id)
        viewport_x = ctx.bbox_x(viewport)
        viewport_y = ctx.bbox_y(viewport)
        viewport_w = ctx.bbox_width(viewport)
        viewport_h = ctx.bbox_height(viewport)
        x = ctx.bbox_x(target)
        y = ctx.bbox_y(target)
        width = ctx.bbox_width(target)
        height = ctx.bbox_height(target)
        edge_gap = min(
            x - viewport_x,
            y - viewport_y,
            viewport_x + viewport_w - (x + width),
            viewport_y + viewport_h - (y + height),
        )
        if edge_gap >= EDGE_SAFE_AREA:
            continue
        if width >= 64 and height >= 44 and y - viewport_y > 24:
            continue
        issue = make_issue(
            ctx=ctx,
            issue_id=f"draft-edge-touch-target-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=Severity.LOW,
            message="A visible control sits too close to the phone screen edge.",
            node=target,
            detector_id="crowded_touch_target",
            confidence="medium",
            confidence_reason=(
                "A visible mobile control is near the screen edge where system gestures or one-handed use can make tapping less reliable."
            ),
            evidence={
                **_accessibility_validation_evidence("edge_safe_touch_area"),
                "edge_gap": round(edge_gap, 2),
                "minimum_recommended_edge_gap": EDGE_SAFE_AREA,
                "target_width": round(width, 2),
                "target_height": round(height, 2),
                "visible_control_text": candidate.accessible_text,
            },
        )
        ranked.append((1, max(0.0, EDGE_SAFE_AREA - edge_gap), ctx.visual_priority(target), issue))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[:2]


def _detect_icon_only_unlabeled_controls(ctx: AuditContext) -> list[tuple[int, float, float, AuditIssue]]:
    candidates = _candidate_touch_targets(ctx)
    ranked: list[tuple[int, float, float, AuditIssue]] = []
    seen: set[str] = set()
    for candidate in candidates:
        target = candidate.node
        if target.id in seen:
            continue
        seen.add(target.id)
        if candidate.accessible_text:
            continue
        if not _weak_icon_name(" ".join([target.name, target.path])):
            continue
        width = ctx.bbox_width(target)
        height = ctx.bbox_height(target)
        if width > 72 or height > 72:
            continue
        issue = make_issue(
            ctx=ctx,
            issue_id=f"draft-icon-only-unlabeled-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=Severity.LOW,
            message="An icon-only control does not have a visible label or unmistakable meaning.",
            node=target,
            detector_id="icon_only_unlabeled_control",
            confidence="medium",
            confidence_reason=(
                "A visible control-like icon has no visible text label and its layer name does not indicate an obvious universal icon."
            ),
            evidence={
                **_accessibility_validation_evidence("icon_only_action_label"),
                "target_width": round(width, 2),
                "target_height": round(height, 2),
                "visible_control_text": candidate.accessible_text,
                "icon_name": target.name,
            },
        )
        ranked.append((1, 72.0 - max(width, height), ctx.visual_priority(target), issue))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[:2]


def _detect_small_important_text(ctx: AuditContext) -> list[tuple[int, float, float, AuditIssue]]:
    ranked: list[tuple[int, float, float, AuditIssue]] = []
    seen: set[str] = set()
    for node in ctx.client_visible_nodes:
        if node.type != "TEXT" or not ctx.has_text(node):
            continue
        style = node.text_style or {}
        font_size = _font_number(style, "fontSize", 0)
        if font_size <= 0 or font_size >= MIN_IMPORTANT_TEXT_SIZE:
            continue
        sample = _text_sample(node)
        importance = _important_text_reason(ctx, node, sample, font_size)
        if importance is None:
            continue
        key = f"{importance}:{normalized_text(sample)}"
        if key in seen:
            continue
        seen.add(key)
        issue = make_issue(
            ctx=ctx,
            issue_id=f"draft-small-important-text-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=Severity.MEDIUM if font_size < 9.5 else Severity.LOW,
            message="Important visible text is too small for comfortable mobile reading.",
            node=node,
            detector_id="small_text_readability",
            confidence="high",
            confidence_reason=(
                "A visible important label or instruction uses a small measured Figma font size."
            ),
            evidence={
                **_accessibility_validation_evidence("small_important_text"),
                "text_sample": sample,
                "font_size": font_size,
                "minimum_recommended_font_size": MIN_IMPORTANT_TEXT_SIZE,
                "small_text_role": importance,
                "pattern_key": f"small_text_readability:{importance}:{normalized_text(sample)}",
            },
        )
        ranked.append((_severity_rank(issue.severity), MIN_IMPORTANT_TEXT_SIZE - font_size, ctx.visual_priority(node), issue))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[:3]


def _detect_dense_line_spacing(ctx: AuditContext) -> list[tuple[int, float, float, AuditIssue]]:
    ranked: list[tuple[int, float, float, AuditIssue]] = []
    for node in ctx.client_visible_nodes:
        if node.type != "TEXT" or not ctx.has_text(node):
            continue
        text = node.characters or ""
        if len(re.findall(r"[a-zA-Z0-9]+", text)) < 24:
            continue
        style = node.text_style or {}
        font_size = _font_number(style, "fontSize", 0)
        line_height = _line_height_number(style, font_size)
        if font_size <= 0 or line_height is None:
            continue
        ratio = line_height / max(font_size, 1.0)
        if ratio >= MIN_DENSE_LINE_HEIGHT_RATIO:
            continue
        issue = make_issue(
            ctx=ctx,
            issue_id=f"draft-dense-line-spacing-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=Severity.LOW,
            message="A dense visible text block has tight line spacing.",
            node=node,
            detector_id="small_text_readability",
            confidence="medium",
            confidence_reason=(
                "A long visible text block has a measured line-height ratio that is tight for mobile reading."
            ),
            evidence={
                **_accessibility_validation_evidence("dense_line_spacing"),
                "text_sample": _text_sample(node),
                "font_size": font_size,
                "line_height": round(line_height, 2),
                "line_height_ratio": round(ratio, 2),
                "minimum_recommended_line_height_ratio": MIN_DENSE_LINE_HEIGHT_RATIO,
            },
        )
        ranked.append((1, MIN_DENSE_LINE_HEIGHT_RATIO - ratio, ctx.visual_priority(node), issue))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[:2]


def detect_low_text_contrast(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """
    Detect measurable Trust & Accessibility risks from static Figma data.

    The checks are conservative: contrast is measured only when colors are
    solid, and touch targets are flagged only for visible mobile controls.
    It does not claim formal accessibility conformance.
    """
    ctx = AuditContext(normalized_file)
    contrast_ranked: list[tuple[int, float, float, AuditIssue]] = []
    seen: set[tuple[str, str, float]] = set()

    for node in ctx.client_visible_nodes:
        if node.type != "TEXT" or not ctx.has_text(node):
            continue

        style = node.text_style or {}
        font_size = _font_number(style, "fontSize", 0)
        font_weight = _font_number(style, "fontWeight", 400)
        if font_size <= 0:
            continue

        foreground_fill = ctx.solid_fill_color(node)
        background_kind, background_fill, background_node = ctx.painted_background_for(node)
        if foreground_fill is None or background_kind == "image":
            continue
        if background_fill is None:
            continue

        background = ctx.resolved_background_color(node)
        foreground = ctx.alpha_composite(
            foreground_fill[:3],
            foreground_fill[3],
            background,
        )
        ratio = ctx.contrast_ratio(foreground, background)

        large_text = font_size >= 24 or (font_size >= 18 and font_weight >= 600)
        threshold = 3.0 if large_text else 4.5
        if ratio >= threshold:
            continue

        sample = _text_sample(node)
        dedupe_key = (node.id, sample, ratio)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        role, impact_level, evaluation_priority = _contrast_text_role(
            ctx,
            node,
            sample=sample,
            font_size=font_size,
        )
        gap = round(threshold - ratio, 2)
        if impact_level == "secondary" and ratio >= 3.0 and gap < 1.25:
            continue

        severity = _severity_for_contrast(ratio, threshold)
        if impact_level == "secondary" and severity == Severity.HIGH:
            severity = Severity.MEDIUM
        issue = AuditIssue(
            id=f"draft-low-text-contrast-{uuid4().hex}",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=severity,
            message="Text contrast appears below the recommended threshold for its size.",
            location=IssueLocation(
                page_name=node.page_name,
                frame_name=node.frame_name,
                node_id=node.id,
                node_name=node.name,
                path=node.path,
            ),
            evidence={
                "detector_id": "low_text_contrast",
                "binary_exists": True,
                "confidence": "high",
                "confidence_reason": "Foreground and nearest background are solid Figma colors with measurable contrast.",
                **ctx.visual_scope_evidence(node),
                **_accessibility_validation_evidence("text_contrast"),
                "text_sample": sample,
                "contrast_text_role": role,
                "user_impact_level": impact_level,
                "evaluation_priority": evaluation_priority,
                "pattern_key": f"low_text_contrast:{role}:{normalized_text(sample)}",
                "contrast_ratio": ratio,
                "required_ratio": threshold,
                "contrast_gap": gap,
                "font_size": font_size,
                "font_weight": font_weight,
                "is_large_text": large_text,
                "foreground_rgba": [round(value, 4) for value in foreground_fill],
                "resolved_foreground_rgb": [round(value, 4) for value in foreground],
                "resolved_background_rgb": [round(value, 4) for value in background],
                "background_node_id": background_node.id if background_node else None,
                "background_node_name": background_node.name if background_node else None,
                "limitations": [
                    "Static Figma detection estimates contrast only for solid colors.",
                    "Text over image fills is skipped because contrast needs rendered pixels.",
                    "Formal accessibility conformance requires implemented UI testing.",
                ],
            },
        )
        contrast_ranked.append((_severity_rank(severity), evaluation_priority, gap, ctx.visual_priority(node), issue))

    ranked = [
        *contrast_ranked,
        *_detect_small_touch_targets(ctx),
        *_detect_crowded_touch_targets(ctx),
        *_detect_edge_crowded_controls(ctx),
        *_detect_icon_only_unlabeled_controls(ctx),
        *_detect_small_important_text(ctx),
        *_detect_dense_line_spacing(ctx),
    ]
    ranked.sort(key=lambda item: item[:-1], reverse=True)
    return [issue for *_, issue in ranked[:DETECTION_MAX_ISSUES_PER_CHECK]]
