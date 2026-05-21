from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from uuid import uuid4

from figma_audit.audit.context import AuditContext
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import make_issue
from figma_audit.models.issue import AuditIssue, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


BALANCED_AESTHETICS_SOURCE = {
    "title": "Balanced Aesthetics: How Shape, Contrast, and Visual Force Affect Interface Layout",
    "authors": "Chen, Lu, and Hao",
    "year": 2024,
    "journal": "International Journal of Human-Computer Interaction",
    "doi": "10.1080/10447318.2023.2289294",
}
VISUAL_WEIGHT_TYPES = {
    "BOOLEAN_OPERATION",
    "COMPONENT",
    "ELLIPSE",
    "FRAME",
    "GROUP",
    "INSTANCE",
    "LINE",
    "POLYGON",
    "RECTANGLE",
    "SECTION",
    "STAR",
    "TEXT",
    "VECTOR",
}
COMPONENT_PATTERN_HINTS = {
    "action sheet",
    "actionsheet",
    "apps widgets",
    "apps + widgets",
    "camera",
    "cell",
    "contextualmenu",
    "date picker",
    "datepicker",
    "drawer",
    "emoji",
    "keyboard",
    "menu",
    "picker",
    "sheet",
    "table",
    "tableview",
    "toolbar",
    "widget",
}
BRAND_SURFACE_HINTS = {
    "cover",
    "home",
    "landing",
    "onboarding",
    "promo",
    "screen",
    "splash",
    "welcome",
}
WORKSPACE_ARTIFACT_TYPES = {"COMPONENT_SET"}


@dataclass(frozen=True)
class VisualWeightSample:
    node: NormalizedNode
    weight: float
    center_x: float
    center_y: float
    contrast: float
    area_ratio: float


def _font_size(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _brand_evidence(subdetector: str, checks: dict[str, object]) -> dict[str, object]:
    return {
        "validation_method": "visual_hierarchy_and_pattern_consistency_static_figma_gate",
        "validation_source": BALANCED_AESTHETICS_SOURCE,
        "visual_brand_subdetector": subdetector,
        "aesthetic_model": "visual_weight_balance",
        "aesthetic_checks": checks,
        "validation_question": (
            "Do hierarchy, contrast, size, and visual-force balance create a clear first-glance focal path?"
        ),
    }


def _node_context(node: NormalizedNode) -> str:
    return " ".join(str(value or "").lower() for value in (node.name, node.frame_name, node.path))


def _is_brand_review_surface(node: NormalizedNode) -> bool:
    context = _node_context(node)
    if any(hint in context for hint in COMPONENT_PATTERN_HINTS):
        return False
    return any(hint in context for hint in BRAND_SURFACE_HINTS) or (node.depth or 0) <= 1


def _solid_rgb(ctx: AuditContext, node: NormalizedNode) -> tuple[float, float, float] | None:
    fill = ctx.solid_fill_color(node)
    if fill is None:
        return None
    background = ctx.resolved_background_color(node)
    return ctx.alpha_composite(fill[:3], fill[3], background)


def _box(node: NormalizedNode) -> dict[str, object] | None:
    return node.absolute_render_bounds or node.absolute_bounding_box


def _box_values(node: NormalizedNode) -> tuple[float, float, float, float] | None:
    box = _box(node)
    if not box:
        return None
    try:
        x = float(box.get("x") or 0)
        y = float(box.get("y") or 0)
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _is_canvas_level_board(ctx: AuditContext, node: NormalizedNode) -> bool:
    if node.type not in {"FRAME", "SECTION"}:
        return False
    box = _box_values(node)
    if box is None:
        return False
    _x, _y, width, height = box
    if width < 600 or height < 700:
        return False
    if node.parent_id is None:
        return True
    parent = ctx.get_node(node.parent_id)
    return parent is not None and parent.type == "CANVAS"


def _component_workspace_issue_for_frame(
    ctx: AuditContext,
    frame: NormalizedNode,
) -> tuple[float, AuditIssue] | None:
    frame_box = _box_values(frame)
    if frame_box is None:
        return None
    _frame_x, _frame_y, frame_width, frame_height = frame_box
    frame_area = frame_width * frame_height
    if frame_area <= 0:
        return None

    direct_children = ctx.direct_children(frame.id)
    component_sets = [
        child
        for child in direct_children
        if child.type in WORKSPACE_ARTIFACT_TYPES and _box_values(child) is not None
    ]
    phone_like_screens = [
        child
        for child in direct_children
        if child.type in {"FRAME", "INSTANCE"}
        and 280 <= ctx.bbox_width(child) <= 460
        and 320 <= ctx.bbox_height(child) <= 950
    ]
    if not phone_like_screens or len(component_sets) < 2:
        return None

    component_area = sum(ctx.bbox_area(child) for child in component_sets)
    component_area_ratio = round(component_area / frame_area, 4)
    if component_area_ratio < 0.045:
        return None

    target = max(component_sets, key=lambda child: ctx.bbox_area(child))
    target_box = _box_values(target)
    if target_box is None:
        return None
    _target_x, target_y, _target_w, _target_h = target_box
    frame_top = frame_box[1]
    target_y_ratio = round((target_y - frame_top) / max(frame_height, 1.0), 4)
    priority = round(component_area_ratio + len(component_sets) * 0.025 + target_y_ratio * 0.15, 4)

    issue = make_issue(
        ctx=ctx,
        issue_id=f"draft-visible-component-workspace-{uuid4().hex}",
        axis="ui_consistency",
        criterion="ui_consistency",
        severity=Severity.HIGH,
        message="Component-library variants are visible inside a reviewed app screen frame.",
        node=target,
        detector_id="flat_visual_hierarchy",
        confidence="high",
        confidence_reason=(
            "The top-level Figma board contains a phone-like app screen plus multiple visible COMPONENT_SET "
            "workspace artifacts. A human reviewer would see unfinished component variants mixed with the screen."
        ),
        evidence={
            **_brand_evidence(
                "visible_component_workspace",
                {
                    "board_node_id": frame.id,
                    "board_name": frame.name,
                    "component_set_count": len(component_sets),
                    "component_workspace_area_ratio": component_area_ratio,
                    "phone_screen_count": len(phone_like_screens),
                    "largest_workspace_node_id": target.id,
                    "largest_workspace_node_name": target.name,
                    "largest_workspace_y_ratio": target_y_ratio,
                },
            ),
            "mobile_viewport_name": frame.name,
            "pattern_key": "flat_visual_hierarchy:visible_component_workspace",
            "evaluation_priority": priority,
            "component_set_count": len(component_sets),
            "component_workspace_area_ratio": component_area_ratio,
            "phone_screen_names": [screen.name for screen in phone_like_screens[:4]],
            "workspace_artifact_names": [child.name for child in component_sets[:8]],
            "limitations": [
                "This rule flags visible Figma workspace artifacts, not runtime behavior.",
                "If these component sets are intentionally part of a design-system documentation board, move them outside the app-screen audit frame or mark the board as documentation.",
            ],
        },
    )
    return priority, issue


def _node_has_light_surface(ctx: AuditContext, node: NormalizedNode) -> bool:
    fill = ctx.solid_fill_color(node)
    if fill is None:
        return False
    red, green, blue, alpha = fill
    if alpha < 0.75:
        return False
    return min(red, green, blue) >= 0.88


def _background_surface_candidates(
    ctx: AuditContext,
    frame: NormalizedNode,
) -> list[NormalizedNode]:
    frame_area = ctx.bbox_area(frame)
    if frame_area <= 0:
        return []
    candidates: list[NormalizedNode] = []
    for child in ctx.direct_children(frame.id):
        if child.type not in {"FRAME", "GROUP", "INSTANCE", "RECTANGLE"}:
            continue
        if ctx.bbox_width(child) < ctx.bbox_width(frame) * 0.45:
            continue
        if ctx.bbox_height(child) < 320:
            continue
        if ctx.bbox_area(child) / frame_area > 0.55:
            continue
        has_painted_surface = bool(child.fills) or any(
            descendant.type == "RECTANGLE" and bool(descendant.fills)
            for descendant in ctx.descendants(child.id)[:8]
        )
        if has_painted_surface:
            candidates.append(child)
    return candidates


def _text_nodes_inside(ctx: AuditContext, container: NormalizedNode) -> list[NormalizedNode]:
    return [
        node
        for node in ctx.text_nodes_in_subtree(container)
        if _is_contained_by(node, container)
    ]


def _primary_heading_in_surface(
    ctx: AuditContext,
    surface: NormalizedNode,
) -> NormalizedNode | None:
    surface_box = _box_values(surface)
    if surface_box is None:
        return None
    surface_x, surface_y, _surface_width, surface_height = surface_box
    candidates: list[tuple[float, NormalizedNode]] = []
    for text in _text_nodes_inside(ctx, surface):
        text_box = _box_values(text)
        if text_box is None:
            continue
        text_x, text_y, text_width, text_height = text_box
        if text_y > surface_y + surface_height * 0.24:
            continue
        font_size = _font_size((text.text_style or {}).get("fontSize"))
        if font_size < 18 and text_height < 24:
            continue
        center_distance = abs((text_x + text_width / 2) - (surface_x + ctx.bbox_width(surface) / 2))
        priority = font_size + text_height * 0.2 - center_distance / 120.0
        candidates.append((priority, text))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _overlap_ratio(first: NormalizedNode, second: NormalizedNode) -> float:
    first_box = _box_values(first)
    second_box = _box_values(second)
    if first_box is None or second_box is None:
        return 0.0
    first_x, first_y, first_w, first_h = first_box
    second_x, second_y, second_w, second_h = second_box
    left = max(first_x, second_x)
    top = max(first_y, second_y)
    right = min(first_x + first_w, second_x + second_w)
    bottom = min(first_y + first_h, second_y + second_h)
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = first_w * first_h
    return overlap / first_area if first_area > 0 else 0.0


def _foreground_panel_position_issue_for_frame(
    ctx: AuditContext,
    frame: NormalizedNode,
) -> tuple[float, AuditIssue] | None:
    best: tuple[float, AuditIssue] | None = None
    for surface in _background_surface_candidates(ctx, frame):
        heading = _primary_heading_in_surface(ctx, surface)
        if heading is None:
            continue
        surface_box = _box_values(surface)
        heading_box = _box_values(heading)
        if surface_box is None or heading_box is None:
            continue
        surface_x, surface_y, surface_w, surface_h = surface_box
        _heading_x, heading_y, _heading_w, heading_h = heading_box
        heading_bottom = heading_y + heading_h

        for child in ctx.direct_children(frame.id):
            if child.id == surface.id or child.type not in {"FRAME", "INSTANCE"}:
                continue
            child_box = _box_values(child)
            if child_box is None or not _node_has_light_surface(ctx, child):
                continue
            child_x, child_y, child_w, child_h = child_box
            if not (280 <= child_w <= 540 and 320 <= child_h <= 900):
                continue
            if _overlap_ratio(child, surface) < 0.82:
                continue
            top_gap = child_y - heading_bottom
            top_gap_ratio = round(top_gap / max(surface_h, 1.0), 4)
            panel_height_ratio = round(child_h / max(surface_h, 1.0), 4)
            panel_top_ratio = round((child_y - surface_y) / max(surface_h, 1.0), 4)
            center_offset_ratio = round(
                abs((child_x + child_w / 2) - (surface_x + surface_w / 2)) / max(surface_w, 1.0),
                4,
            )
            crowds_heading = top_gap < max(44.0, surface_h * 0.075)
            dominates_surface = panel_height_ratio >= 0.72
            if not (crowds_heading and dominates_surface):
                continue
            priority = round(
                (max(0.0, 0.09 - top_gap_ratio) * 5.0)
                + max(0.0, panel_height_ratio - 0.7)
                + center_offset_ratio
                + 0.55,
                4,
            )
            issue = make_issue(
                ctx=ctx,
                issue_id=f"draft-bad-panel-position-{uuid4().hex}",
                axis="ui_consistency",
                criterion="ui_consistency",
                severity=Severity.HIGH,
                message="The main form panel is badly positioned in the screen composition.",
                node=child,
                detector_id="flat_visual_hierarchy",
                confidence="high",
                confidence_reason=(
                    "The foreground panel is a large light surface that starts too close to the main heading "
                    "and occupies most of the decorative background. This is measurable from Figma geometry."
                ),
                evidence={
                    **_brand_evidence(
                        "bad_foreground_panel_position",
                        {
                            "board_node_id": frame.id,
                            "board_name": frame.name,
                            "background_node_id": surface.id,
                            "background_name": surface.name,
                            "heading_node_id": heading.id,
                            "heading_text": heading.characters,
                            "panel_node_id": child.id,
                            "panel_name": child.name,
                            "heading_to_panel_gap": round(top_gap, 2),
                            "heading_to_panel_gap_ratio": top_gap_ratio,
                            "panel_height_to_background_ratio": panel_height_ratio,
                            "panel_top_to_background_ratio": panel_top_ratio,
                            "panel_center_offset_ratio": center_offset_ratio,
                        },
                    ),
                    "mobile_viewport_name": frame.name,
                    "pattern_key": "flat_visual_hierarchy:bad_foreground_panel_position",
                    "evaluation_priority": priority,
                    "heading_to_panel_gap": round(top_gap, 2),
                    "panel_height_to_background_ratio": panel_height_ratio,
                    "panel_top_to_background_ratio": panel_top_ratio,
                    "panel_center_offset_ratio": center_offset_ratio,
                    "limitations": [
                        "This rule flags static composition, not runtime responsiveness.",
                    ],
                },
            )
            candidate = (priority, issue)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best


def _is_contained_by(node: NormalizedNode, ancestor: NormalizedNode) -> bool:
    node_box = _box_values(node)
    ancestor_box = _box_values(ancestor)
    if node_box is None or ancestor_box is None:
        return False
    x, y, width, height = node_box
    ax, ay, aw, ah = ancestor_box
    return ax <= x and ay <= y and x + width <= ax + aw and y + height <= ay + ah


def _sample_contrast(ctx: AuditContext, frame: NormalizedNode, node: NormalizedNode) -> float:
    node_rgb = _solid_rgb(ctx, node)
    frame_rgb = _solid_rgb(ctx, frame) or (1.0, 1.0, 1.0)
    if node.type == "TEXT":
        node_rgb = _solid_rgb(ctx, node)
    if node_rgb is None:
        return 1.0
    return ctx.contrast_ratio(node_rgb, frame_rgb)


def _visual_weight_sample(
    ctx: AuditContext,
    frame: NormalizedNode,
    node: NormalizedNode,
) -> VisualWeightSample | None:
    if node.id == frame.id or node.type not in VISUAL_WEIGHT_TYPES:
        return None
    frame_box = _box_values(frame)
    node_box = _box_values(node)
    if frame_box is None or node_box is None:
        return None
    if not _is_contained_by(node, frame):
        return None

    frame_x, frame_y, frame_width, frame_height = frame_box
    x, y, width, height = node_box
    frame_area = frame_width * frame_height
    area = width * height
    if frame_area <= 0 or area < 36 or area > frame_area * 0.65:
        return None

    area_ratio = area / frame_area
    contrast = _sample_contrast(ctx, frame, node)
    text_bonus = 1.0
    if node.type == "TEXT":
        font_size = _font_size((node.text_style or {}).get("fontSize"))
        if font_size <= 0:
            return None
        text_bonus = max(0.8, min(2.2, font_size / 16.0))

    center_x = x + width / 2
    center_y = y + height / 2
    normalized_x = abs((center_x - (frame_x + frame_width / 2)) / max(frame_width / 2, 1.0))
    normalized_y = abs((center_y - (frame_y + frame_height / 2)) / max(frame_height / 2, 1.0))
    outward_force = max(normalized_x, normalized_y)
    contrast_factor = max(0.7, min(2.2, contrast / 3.0))
    weight = round(area_ratio * contrast_factor * (1.0 + outward_force * 0.45) * text_bonus, 5)
    if weight <= 0:
        return None
    return VisualWeightSample(
        node=node,
        weight=weight,
        center_x=center_x,
        center_y=center_y,
        contrast=contrast,
        area_ratio=round(area_ratio, 5),
    )


def _visual_weight_samples(ctx: AuditContext, frame: NormalizedNode) -> list[VisualWeightSample]:
    raw_samples = [
        sample
        for node in ctx.descendants(frame.id)
        if (sample := _visual_weight_sample(ctx, frame, node)) is not None
    ]
    raw_samples.sort(key=lambda sample: sample.weight, reverse=True)
    kept: list[VisualWeightSample] = []
    for sample in raw_samples:
        # Keep the outer meaningful visual unit when a parent and child occupy
        # almost the same focal area, otherwise balance math double-counts it.
        if any(_is_contained_by(sample.node, kept_sample.node) for kept_sample in kept):
            continue
        kept.append(sample)
    return kept[:18]


def _balance_issue_for_frame(ctx: AuditContext, frame: NormalizedNode) -> tuple[float, AuditIssue] | None:
    samples = _visual_weight_samples(ctx, frame)
    if len(samples) < 4:
        return None
    frame_box = _box_values(frame)
    if frame_box is None:
        return None
    frame_x, frame_y, frame_width, frame_height = frame_box
    total_weight = sum(sample.weight for sample in samples)
    if total_weight <= 0:
        return None
    center_x = frame_x + frame_width / 2
    center_y = frame_y + frame_height / 2
    weighted_x = sum(sample.center_x * sample.weight for sample in samples) / total_weight
    weighted_y = sum(sample.center_y * sample.weight for sample in samples) / total_weight
    x_offset = abs(weighted_x - center_x) / max(frame_width, 1.0)
    y_offset = abs(weighted_y - center_y) / max(frame_height, 1.0)
    imbalance = round(max(x_offset, y_offset), 4)
    top_weight = samples[0].weight
    second_weight = samples[1].weight if len(samples) > 1 else 0.0
    dominance_ratio = round(top_weight / max(second_weight, 0.0001), 2)

    if imbalance < 0.28 or dominance_ratio < 1.7:
        return None

    target = samples[0].node
    if target.type == "TEXT":
        return None
    issue = make_issue(
        ctx=ctx,
        issue_id=f"draft-unbalanced-visual-weight-{uuid4().hex}",
        axis="ui_consistency",
        criterion="ui_consistency",
        severity=Severity.LOW,
        message="A large frame has off-center visual weight that may weaken first-glance brand composition.",
        node=target,
        detector_id="flat_visual_hierarchy",
        confidence="low",
        confidence_reason=(
            "The detector estimated visual weight from element size, contrast, and distance from the frame center. "
            "This is useful for visual-brand review but still needs human judgment."
        ),
        evidence={
            **_brand_evidence(
                "unbalanced_visual_weight",
                {
                    "sample_count": len(samples),
                    "weighted_center_x_offset_ratio": round(x_offset, 4),
                    "weighted_center_y_offset_ratio": round(y_offset, 4),
                    "imbalance_ratio": imbalance,
                    "dominance_ratio": dominance_ratio,
                    "top_weight_node_id": target.id,
                    "top_weight_node_name": target.name,
                    "top_weight": top_weight,
                    "top_weight_contrast": samples[0].contrast,
                    "top_weight_area_ratio": samples[0].area_ratio,
                },
            ),
            "limitations": [
                "Static Figma geometry cannot judge brand intent, authenticity, or emotional fit.",
                "Asymmetry can be intentional; this finding should be reviewed as a composition risk, not an automatic failure.",
            ],
        },
    )
    return imbalance, issue


def detect_flat_visual_hierarchy(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """
    Detect large frames where hierarchy or visual balance weakens the visual system.

    These are lower-confidence visual hierarchy and consistency signals because
    brand meaning is contextual, but the measured font-size and visual-weight
    evidence is objective enough to guide human review.
    """
    ctx = AuditContext(normalized_file)
    ranked: list[tuple[float, float, AuditIssue]] = []
    emitted: set[str] = set()

    workspace_emitted: set[str] = set()
    for frame in ctx.visible_nodes:
        if not _is_canvas_level_board(ctx, frame):
            continue
        position_issue = _foreground_panel_position_issue_for_frame(ctx, frame)
        if position_issue is not None:
            priority, issue = position_issue
            target_node = ctx.get_node(issue.location.node_id) or frame
            ranked.append((priority, ctx.visual_priority(target_node), issue))
        workspace_issue = _component_workspace_issue_for_frame(ctx, frame)
        if workspace_issue is None:
            continue
        key = frame.id
        if key in workspace_emitted:
            continue
        workspace_emitted.add(key)
        priority, issue = workspace_issue
        target_node = ctx.get_node(issue.location.node_id) or frame
        ranked.append((priority, ctx.visual_priority(target_node), issue))

    for node in ctx.client_visible_nodes:
        if not ctx.is_large_frame(node):
            continue
        if not _is_brand_review_surface(node):
            continue

        family = ctx.family_key(node)
        if family in emitted:
            continue

        texts = ctx.text_nodes_in_subtree(node)
        sizes = []
        for text in texts:
            size = _font_size((text.text_style or {}).get("fontSize"))
            if size > 0:
                sizes.append(size)
        if len(sizes) < 5:
            continue

        largest = max(sizes)
        middle = median(sizes)
        if middle <= 0:
            continue

        ratio = round(largest / middle, 2)
        if ratio > 1.15:
            continue

        emitted.add(family)
        checks = {
            "text_count": len(sizes),
            "largest_font_size": largest,
            "median_font_size": round(middle, 2),
            "largest_to_median_ratio": ratio,
            "minimum_clear_hierarchy_ratio": 1.15,
        }
        ranked.append(
            (
                1.15 - ratio,
                ctx.visual_priority(node),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-flat-hierarchy-{uuid4().hex}",
                    axis="ui_consistency",
                    criterion="ui_consistency",
                    severity=Severity.LOW,
                    message="A large frame has very flat text sizing, which may weaken visual hierarchy.",
                    node=node,
                    detector_id="flat_visual_hierarchy",
                    confidence="low",
                    confidence_reason=(
                        "Font-size distribution is objective and indicates weak focal hierarchy; brand hierarchy still needs human review."
                    ),
                    evidence={
                        **_brand_evidence("flat_text_hierarchy", checks),
                        "text_count": len(sizes),
                        "largest_font_size": largest,
                        "median_font_size": round(middle, 2),
                        "largest_to_median_ratio": ratio,
                        "limitations": [
                            "This is a draft signal; imagery, spacing, and product context can change hierarchy quality.",
                        ],
                    },
                ),
            )
        )

    balance_emitted: set[str] = set()
    for frame in ctx.client_visible_nodes:
        if not ctx.is_large_frame(frame):
            continue
        if not _is_brand_review_surface(frame):
            continue
        family = ctx.family_key(frame)
        if family in balance_emitted:
            continue
        balance_issue = _balance_issue_for_frame(ctx, frame)
        if balance_issue is None:
            continue
        balance_emitted.add(family)
        imbalance, issue = balance_issue
        ranked.append((imbalance, ctx.visual_priority(frame), issue))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [issue for _, _, issue in ranked[:DETECTION_MAX_ISSUES_PER_CHECK]]
