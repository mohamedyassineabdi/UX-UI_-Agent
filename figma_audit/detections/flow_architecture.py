from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable
from uuid import uuid4

from figma_audit.audit.context import AuditContext, CONTAINER_TYPES
from figma_audit.config import DETECTION_MAX_ISSUES_PER_CHECK
from figma_audit.detections.common import make_issue, normalized_text, text_nodes
from figma_audit.models.issue import AuditIssue, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


GENERIC_NAV_LABELS = {
    "item",
    "label",
    "link",
    "menu",
    "nav",
    "page",
    "section",
    "tab",
}
NAV_CONTAINER_KEYWORDS = {
    "bottom bar",
    "bottom nav",
    "breadcrumb",
    "menu",
    "nav",
    "navigation",
    "segmented",
    "tab bar",
    "tabs",
    "toolbar",
}
NON_DESTINATION_LABELS = {
    "9:41",
    "cancel",
    "done",
    "edit",
    "end",
    "open",
    "search",
}
CONTENT_CONTAINER_KEYWORDS = {
    "app",
    "apps",
    "card",
    "carousel",
    "collection",
    "content",
    "grid",
    "list",
    "product",
    "widget",
}
CONTENT_PLACEHOLDER_LABELS = {
    "app name",
    "company name",
    "description",
    "heading",
    "placeholder",
    "subtitle",
    "title",
    "widget",
}
ALNUM_RE = re.compile(r"[a-z0-9]")
ICON_TYPES = {"BOOLEAN_OPERATION", "ELLIPSE", "LINE", "POLYGON", "STAR", "VECTOR"}
VISUAL_SEARCH_CODING_SOURCE = {
    "title": "Effects of color coding and layout coding on users' visual search on mobile map navigation interfaces",
    "authors": "Wang, Zeng, and Liu",
    "year": 2022,
    "journal": "Frontiers in Psychology",
    "doi": "10.3389/fpsyg.2022.1040533",
}


@dataclass(frozen=True)
class NavigationLabel:
    node: NormalizedNode
    label: str


@dataclass
class NavigationItem:
    container: NormalizedNode
    labels: list[NavigationLabel]
    icon_signatures: list[str]


def _own_node_text(node: NormalizedNode) -> str:
    return normalized_text(" ".join(str(value or "") for value in (node.name, node.frame_name)))


def _has_navigation_keyword(node: NormalizedNode) -> bool:
    own_text = _own_node_text(node)
    return any(keyword in own_text for keyword in NAV_CONTAINER_KEYWORDS)


def _has_content_container_keyword(node: NormalizedNode) -> bool:
    own_text = _own_node_text(node)
    return any(keyword in own_text for keyword in CONTENT_CONTAINER_KEYWORDS)


def _is_probable_navigation_container(ctx: AuditContext, node: NormalizedNode) -> bool:
    if node.type not in CONTAINER_TYPES:
        return False
    if _has_content_container_keyword(node) and not _has_navigation_keyword(node):
        return False
    text_count = sum(
        1 for child in ctx.descendants(node.id) if child.type == "TEXT" and ctx.has_text(child)
    )
    iconish_count = sum(1 for child in ctx.descendants(node.id) if child.type in ICON_TYPES)
    width = ctx.bbox_width(node)
    height = ctx.bbox_height(node)
    if _has_navigation_keyword(node):
        return width >= 160 and 28 <= height <= 220 and (text_count >= 2 or iconish_count >= 3)

    children = ctx.direct_children(node.id)
    if len(children) < 3 or len(children) > 8:
        return False

    looks_like_bar = width >= 220 and 36 <= height <= 130
    return text_count >= 2 and iconish_count >= 2 and node.layout_mode == "HORIZONTAL" and looks_like_bar


def _navigation_ancestor(ctx: AuditContext, node: NormalizedNode) -> NormalizedNode | None:
    viewport = ctx.mobile_viewport_for(node)
    for ancestor in ctx.iter_ancestors(node):
        if viewport is not None and ancestor.id == viewport.id:
            break
        if _is_probable_navigation_container(ctx, ancestor):
            return ancestor
    return None


def _visible_nav_labels(ctx: AuditContext, nav: NormalizedNode) -> list[NavigationLabel]:
    labels: list[NavigationLabel] = []
    for node in ctx.text_nodes_in_subtree(nav):
        label = normalized_text(node.characters)
        if not _is_destination_label(label):
            continue
        if label in NON_DESTINATION_LABELS:
            continue
        labels.append(NavigationLabel(node=node, label=label))
    return labels


def _is_destination_label(label: str) -> bool:
    return bool(
        label
        and len(label) <= 28
        and ALNUM_RE.search(label)
        and label not in NON_DESTINATION_LABELS
        and label not in CONTENT_PLACEHOLDER_LABELS
    )


def _is_descendant_of(ctx: AuditContext, node: NormalizedNode, ancestor: NormalizedNode) -> bool:
    return any(parent.id == ancestor.id for parent in ctx.iter_ancestors(node))


def _dedupe_navigation_candidates(ctx: AuditContext, candidates: Iterable[NormalizedNode]) -> list[NormalizedNode]:
    sorted_candidates = sorted(
        {candidate.id: candidate for candidate in candidates}.values(),
        key=lambda node: (ctx.bbox_area(node), -ctx.visual_priority(node)),
    )
    kept: list[NormalizedNode] = []
    for candidate in sorted_candidates:
        if any(_is_descendant_of(ctx, kept_candidate, candidate) for kept_candidate in kept):
            continue
        kept.append(candidate)
    return kept


def _item_container_for_descendant(
    ctx: AuditContext,
    nav: NormalizedNode,
    descendant: NormalizedNode,
) -> NormalizedNode:
    current = descendant
    while True:
        parent = ctx.get_node(current.parent_id)
        if parent is None:
            return descendant
        if parent.id == nav.id:
            return current
        current = parent


def _fill_signature(node: NormalizedNode) -> str:
    fill = next((item for item in node.fills if item.get("visible") is not False), None)
    if not isinstance(fill, dict):
        return "no-fill"
    color = fill.get("color")
    if not isinstance(color, dict):
        return str(fill.get("type") or "unknown-fill")
    channels = tuple(round(float(color.get(channel, 0.0)), 2) for channel in ("r", "g", "b", "a"))
    return f"{fill.get('type', 'fill')}:{channels}"


def _icon_signature(ctx: AuditContext, node: NormalizedNode) -> str:
    if node.component_id:
        return f"component:{node.component_id}"
    return "|".join(
        [
            node.type.lower(),
            ctx.normalized_name(node.name) or "unnamed",
            _fill_signature(node),
            f"{round(ctx.bbox_width(node))}x{round(ctx.bbox_height(node))}",
        ]
    )


def _navigation_items(ctx: AuditContext, nav: NormalizedNode) -> list[NavigationItem]:
    by_id: dict[str, NavigationItem] = {}

    def ensure_item(container: NormalizedNode) -> NavigationItem:
        if container.id not in by_id:
            by_id[container.id] = NavigationItem(container=container, labels=[], icon_signatures=[])
        return by_id[container.id]

    for text in ctx.text_nodes_in_subtree(nav):
        label = normalized_text(text.characters)
        if not _is_destination_label(label):
            continue
        item = ensure_item(_item_container_for_descendant(ctx, nav, text))
        item.labels.append(NavigationLabel(node=text, label=label))

    for descendant in ctx.descendants(nav.id):
        if descendant.type not in ICON_TYPES:
            continue
        item = ensure_item(_item_container_for_descendant(ctx, nav, descendant))
        item.icon_signatures.append(_icon_signature(ctx, descendant))

    return sorted(by_id.values(), key=lambda item: (ctx.bbox_x(item.container), ctx.bbox_y(item.container)))


def _relative_spread(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    if average <= 0:
        return 0.0
    return round((max(values) - min(values)) / average, 4)


def _is_primary_icon_navigation_region(ctx: AuditContext, nav: NormalizedNode) -> bool:
    own_text = _own_node_text(nav)
    if any(keyword in own_text for keyword in ("bottom", "tab", "navigation")):
        return True
    viewport = ctx.mobile_viewport_for(nav)
    if viewport is None:
        return False
    viewport_width = ctx.bbox_width(viewport)
    viewport_y = ctx.bbox_y(viewport)
    viewport_height = ctx.bbox_height(viewport)
    nav_y = ctx.bbox_y(nav)
    return ctx.bbox_width(nav) >= viewport_width * 0.55 and nav_y >= viewport_y + (viewport_height * 0.58)


def _visual_search_summary(
    ctx: AuditContext,
    nav: NormalizedNode,
    labels: list[NavigationLabel],
) -> dict[str, object]:
    items = _navigation_items(ctx, nav)
    widths = [ctx.bbox_width(item.container) for item in items if ctx.bbox_width(item.container) > 0]
    centers = [
        ctx.bbox_x(item.container) + (ctx.bbox_width(item.container) / 2)
        for item in items
        if ctx.bbox_width(item.container) > 0
    ]
    centers.sort()
    gaps = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
    width_spread = _relative_spread(widths)
    gap_spread = _relative_spread(gaps)
    icon_signatures = [
        signature
        for item in items
        for signature in item.icon_signatures[:1]
    ]
    repeated_icon_signatures = {
        signature: count
        for signature, count in sorted(Counter(icon_signatures).items())
        if count > 1
    }
    labeled_item_count = sum(1 for item in items if item.labels)
    icon_item_count = sum(1 for item in items if item.icon_signatures)
    item_count = max(len(items), len(labels))
    regular_layout = item_count < 3 or (width_spread <= 0.45 and gap_spread <= 0.45)
    distinct_icon_count = len(set(icon_signatures))
    icon_coding = "not_available"
    if icon_item_count:
        icon_coding = "distinct" if distinct_icon_count >= min(3, icon_item_count) else "repeated_or_generic"

    return {
        "source": "static_figma_geometry",
        "item_count": item_count,
        "labeled_item_count": labeled_item_count,
        "unlabeled_item_count": max(0, item_count - labeled_item_count),
        "icon_item_count": icon_item_count,
        "distinct_icon_count": distinct_icon_count,
        "repeated_icon_signatures": repeated_icon_signatures,
        "layout_regular": regular_layout,
        "item_width_spread": width_spread,
        "item_center_gap_spread": gap_spread,
        "icon_coding": icon_coding,
    }


def _visual_search_evidence() -> dict[str, object]:
    return {
        "validation_method": "visual_search_coding_static_figma_gate",
        "validation_source": VISUAL_SEARCH_CODING_SOURCE,
        "validation_question": (
            "Can users visually distinguish navigation destinations from labels, icon coding, and spatial layout?"
        ),
    }


def _label_problem(labels: list[NavigationLabel]) -> tuple[list[str], dict[str, int]]:
    label_counts = Counter(label.label for label in labels)
    generic = sorted({label for label in label_counts if label in GENERIC_NAV_LABELS})
    duplicates = {
        label: count
        for label, count in sorted(label_counts.items())
        if count > 1 and label not in NON_DESTINATION_LABELS
    }
    return generic, duplicates


def detect_generic_navigation_labels(normalized_file: NormalizedFigmaFile) -> list[AuditIssue]:
    """Detect visible mobile navigation that weakens wayfinding."""
    ctx = AuditContext(normalized_file)
    ranked: list[tuple[float, int, AuditIssue]] = []
    nav_candidates: dict[str, NormalizedNode] = {
        node.id: node
        for node in ctx.client_visible_nodes
        if _is_probable_navigation_container(ctx, node)
    }
    seen_problem_patterns: set[tuple[str, tuple[str, ...], tuple[tuple[str, int], ...]]] = set()

    for node in text_nodes(ctx):
        nav_ancestor = _navigation_ancestor(ctx, node)
        if nav_ancestor is None:
            continue
        nav_candidates[nav_ancestor.id] = nav_ancestor

    for nav_ancestor in _dedupe_navigation_candidates(ctx, nav_candidates.values()):
        labels = _visible_nav_labels(ctx, nav_ancestor)
        generic_labels, duplicated_labels = _label_problem(labels)
        visual_summary = _visual_search_summary(ctx, nav_ancestor, labels)
        missing_destination_coding = (
            visual_summary["item_count"] >= 3
            and visual_summary["icon_item_count"] >= 3
            and visual_summary["labeled_item_count"] < 3
            and visual_summary["unlabeled_item_count"] >= 2
            and _is_primary_icon_navigation_region(ctx, nav_ancestor)
        )
        if len(labels) < 3 and not missing_destination_coding:
            continue
        if not generic_labels and not duplicated_labels and not missing_destination_coding:
            continue

        generic_count = sum(1 for label in labels if label.label in GENERIC_NAV_LABELS)
        duplicate_count = sum(count for count in duplicated_labels.values())
        if generic_count == 1 and not duplicated_labels and not _has_navigation_keyword(nav_ancestor):
            continue
        problem_pattern = (
            normalized_text(nav_ancestor.name),
            tuple(label.label for label in labels),
            tuple(sorted(duplicated_labels.items())),
        )
        if missing_destination_coding:
            problem_pattern = (
                normalized_text(nav_ancestor.name),
                ("__missing_destination_labels__",),
                tuple(sorted(visual_summary["repeated_icon_signatures"].items())),  # type: ignore[union-attr]
            )
        if problem_pattern in seen_problem_patterns:
            continue
        seen_problem_patterns.add(problem_pattern)

        target = nav_ancestor
        if labels:
            target = next(
                (
                    label.node
                    for label in labels
                    if label.label in GENERIC_NAV_LABELS or label.label in duplicated_labels
                ),
                labels[0].node,
            )
        label_problem_is_strong = generic_count >= 2 or duplicate_count >= 3
        weak_visual_coding = (
            missing_destination_coding
            or visual_summary["icon_coding"] in {"repeated_or_generic", "not_available"}
            or visual_summary["layout_regular"] is False
        )
        severity = Severity.MEDIUM if label_problem_is_strong or missing_destination_coding else Severity.LOW
        confidence = "high" if _has_navigation_keyword(nav_ancestor) and weak_visual_coding else "medium"
        subdetector = (
            "missing_destination_labels_in_primary_navigation"
            if missing_destination_coding
            else "generic_or_repeated_destination_labels"
        )
        message = (
            "Primary mobile navigation relies on icons without visible destination labels."
            if missing_destination_coding
            else "Navigation uses generic or repeated destination labels that weaken wayfinding."
        )

        issue_node = nav_ancestor if missing_destination_coding else target
        ranked.append(
            (
                ctx.visual_priority(target),
                len(labels),
                make_issue(
                    ctx=ctx,
                    issue_id=f"draft-generic-nav-label-{uuid4().hex}",
                    axis="flow_architecture",
                    criterion="flow_architecture",
                    severity=severity,
                    message=message,
                    node=issue_node,
                    detector_id="generic_navigation_label",
                    confidence=confidence,
                    confidence_reason=(
                        "A visual-search coding check found that visible mobile navigation choices are not "
                        "clearly distinguishable by destination labels, icon coding, and spatial layout."
                    ),
                    evidence={
                        **_visual_search_evidence(),
                        "flow_subdetector": subdetector,
                        "navigation_labels": [label.label for label in labels],
                        "generic_labels": generic_labels,
                        "duplicated_labels": duplicated_labels,
                        "visual_search_checks": visual_summary,
                        "navigation_ancestor_id": nav_ancestor.id,
                        "navigation_ancestor_name": nav_ancestor.name,
                        "navigation_ancestor_path": nav_ancestor.path,
                        "navigation_label_count": len(labels),
                        "problem_label_node_id": target.id if target.type == "TEXT" else None,
                        "problem_label": target.characters if target.type == "TEXT" else None,
                        "limitations": [
                            "Static Figma data cannot prove hidden routing, destination content, or learned product-specific icon meaning.",
                        ],
                    },
                ),
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [issue for _, _, issue in ranked[:DETECTION_MAX_ISSUES_PER_CHECK]]
