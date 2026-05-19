
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterable

from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


CONTAINER_TYPES = {"FRAME", "GROUP", "INSTANCE", "SECTION", "COMPONENT", "COMPONENT_SET"}
MOBILE_VIEWPORT_TYPES = {"FRAME", "INSTANCE", "COMPONENT", "SECTION"}
FRONT_LAYER_OCCLUDER_TYPES = {
    "BOOLEAN_OPERATION",
    "COMPONENT",
    "ELLIPSE",
    "FRAME",
    "GROUP",
    "INSTANCE",
    "POLYGON",
    "RECTANGLE",
    "SECTION",
    "STAR",
    "VECTOR",
}
ACTION_KEYWORDS = {
    "action",
    "apply",
    "back",
    "cancel",
    "close",
    "confirm",
    "continue",
    "delete",
    "done",
    "edit",
    "menu",
    "next",
    "open",
    "remove",
    "save",
    "send",
    "submit",
}
CONTROL_KEYWORDS = {
    "button",
    "btn",
    "chip",
    "cta",
    "field",
    "input",
    "pill",
    "search",
    "segmented",
    "select",
    "switch",
    "tab",
}
NAVIGATION_KEYWORDS = {
    "app bar",
    "bottom bar",
    "bottom nav",
    "header",
    "menu",
    "nav",
    "navigation",
    "sidebar",
    "tab bar",
    "tabs",
    "toolbar",
}
GENERIC_TOKEN_RE = re.compile(r"\b(xs|sm|md|lg|xl|xxl|desktop|mobile|tablet|default|variant)\b", re.IGNORECASE)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _round_number(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _safe_text(value: str | None) -> str:
    return (value or "").strip()


def _normalize_name(value: str | None) -> str:
    text = NON_ALNUM_RE.sub(" ", _safe_text(value).lower())
    text = re.sub(r"\b\d+\b", " ", text)
    text = GENERIC_TOKEN_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_text_label(value: str | None) -> str:
    text = _safe_text(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_text_canonical(value: str | None) -> str:
    text = _normalize_text_label(value)
    return NON_ALNUM_RE.sub("", text)


@dataclass
class AuditContext:
    normalized_file: NormalizedFigmaFile
    nodes_by_id: dict[str, NormalizedNode] = field(init=False)
    children_by_parent: dict[str | None, list[NormalizedNode]] = field(init=False)
    _descendants_cache: dict[str, list[NormalizedNode]] = field(init=False, default_factory=dict)
    _text_nodes_in_subtree_cache: dict[str, list[NormalizedNode]] = field(init=False, default_factory=dict)
    _ancestor_id_cache: dict[str, set[str]] = field(init=False, default_factory=dict)
    _front_layer_ratio_cache: dict[str, float] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.nodes_by_id = {node.id: node for node in self.normalized_file.nodes}
        self.children_by_parent = defaultdict(list)
        for node in self.normalized_file.nodes:
            self.children_by_parent[node.parent_id].append(node)
        self.node_order = {
            node.id: index for index, node in enumerate(self.normalized_file.nodes)
        }

    def is_node_visible(self, node: NormalizedNode) -> bool:
        if node.visible is False:
            return False
        for ancestor in self.iter_ancestors(node):
            if ancestor.visible is False:
                return False
        return True

    @cached_property
    def visible_nodes(self) -> list[NormalizedNode]:
        return [node for node in self.normalized_file.nodes if self.is_node_visible(node)]

    def get_node(self, node_id: str | None) -> NormalizedNode | None:
        if not node_id:
            return None
        return self.nodes_by_id.get(node_id)

    def direct_children(self, node_id: str | None) -> list[NormalizedNode]:
        return [child for child in self.children_by_parent.get(node_id, []) if self.is_node_visible(child)]

    def iter_ancestors(self, node: NormalizedNode) -> Iterable[NormalizedNode]:
        current = self.get_node(node.parent_id)
        while current is not None:
            yield current
            current = self.get_node(current.parent_id)

    def descendants(self, node_id: str) -> list[NormalizedNode]:
        if node_id in self._descendants_cache:
            return self._descendants_cache[node_id]

        collected: list[NormalizedNode] = []
        stack = list(reversed(self.direct_children(node_id)))
        while stack:
            node = stack.pop()
            collected.append(node)
            stack.extend(reversed(self.direct_children(node.id)))

        self._descendants_cache[node_id] = collected
        return collected

    @cached_property
    def subtree_node_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        def walk(node_id: str) -> int:
            if node_id in counts:
                return counts[node_id]
            total = 1
            for child in self.direct_children(node_id):
                total += walk(child.id)
            counts[node_id] = total
            return total

        for node in self.visible_nodes:
            walk(node.id)
        return counts

    @cached_property
    def subtree_depths(self) -> dict[str, int]:
        depths: dict[str, int] = {}

        def walk(node_id: str) -> int:
            if node_id in depths:
                return depths[node_id]
            children = self.direct_children(node_id)
            if not children:
                depths[node_id] = 1
                return 1
            value = 1 + max(walk(child.id) for child in children)
            depths[node_id] = value
            return value

        for node in self.visible_nodes:
            walk(node.id)
        return depths

    def subtree_count(self, node: NormalizedNode) -> int:
        return self.subtree_node_counts.get(node.id, 1)

    def subtree_depth(self, node: NormalizedNode) -> int:
        return self.subtree_depths.get(node.id, 1)

    def family_key(self, node: NormalizedNode) -> str:
        if node.component_id:
            return f"component:{node.component_id}"
        normalized_name = _normalize_name(node.name)
        if normalized_name:
            return f"{node.type.lower()}:{normalized_name}"
        return f"id:{node.id}"

    def normalized_name(self, value: str | None) -> str:
        return _normalize_name(value)

    def text_label(self, value: str | None) -> str:
        return _normalize_text_label(value)

    def text_canonical(self, value: str | None) -> str:
        return _normalize_text_canonical(value)

    def bbox_area(self, node: NormalizedNode) -> float:
        box = node.absolute_bounding_box or {}
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        return round(width * height, 2)

    def render_or_bbox(self, node: NormalizedNode) -> dict[str, object] | None:
        return node.absolute_render_bounds or node.absolute_bounding_box

    def bbox_width(self, node: NormalizedNode) -> float:
        box = self.render_or_bbox(node) or {}
        return float(box.get("width") or 0)

    def bbox_height(self, node: NormalizedNode) -> float:
        box = self.render_or_bbox(node) or {}
        return float(box.get("height") or 0)

    def bbox_x(self, node: NormalizedNode) -> float:
        box = node.absolute_bounding_box or {}
        return float(box.get("x") or 0)

    def bbox_y(self, node: NormalizedNode) -> float:
        box = node.absolute_bounding_box or {}
        return float(box.get("y") or 0)

    def _box_values(self, node: NormalizedNode) -> tuple[float, float, float, float] | None:
        box = self.render_or_bbox(node)
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

    def _ancestor_ids(self, node: NormalizedNode) -> set[str]:
        if node.id not in self._ancestor_id_cache:
            self._ancestor_id_cache[node.id] = {ancestor.id for ancestor in self.iter_ancestors(node)}
        return self._ancestor_id_cache[node.id]

    def _is_ancestor_or_descendant(self, first: NormalizedNode, second: NormalizedNode) -> bool:
        return (
            first.id in self._ancestor_ids(second)
            or second.id in self._ancestor_ids(first)
        )

    @staticmethod
    def _point_in_values(
        values: tuple[float, float, float, float],
        point: tuple[float, float],
    ) -> bool:
        x, y, width, height = values
        return x <= point[0] <= x + width and y <= point[1] <= y + height

    @staticmethod
    def _boxes_intersect(
        first: tuple[float, float, float, float],
        second: tuple[float, float, float, float],
    ) -> bool:
        first_x, first_y, first_w, first_h = first
        second_x, second_y, second_w, second_h = second
        return not (
            first_x + first_w <= second_x
            or second_x + second_w <= first_x
            or first_y + first_h <= second_y
            or second_y + second_h <= first_y
        )

    @staticmethod
    def _sample_points(values: tuple[float, float, float, float]) -> list[tuple[float, float]]:
        x, y, width, height = values
        inset_x = min(width * 0.18, 10.0)
        inset_y = min(height * 0.18, 10.0)
        left = x + inset_x
        center_x = x + width / 2
        right = x + width - inset_x
        top = y + inset_y
        center_y = y + height / 2
        bottom = y + height - inset_y
        return [
            (center_x, center_y),
            (left, top),
            (center_x, top),
            (right, top),
            (left, center_y),
            (right, center_y),
            (left, bottom),
            (center_x, bottom),
            (right, bottom),
        ]

    def _is_front_layer_occluder(self, node: NormalizedNode) -> bool:
        if node.type not in FRONT_LAYER_OCCLUDER_TYPES:
            return False
        fill_type = self.first_visible_fill_type(node)
        if fill_type is None:
            return False
        if fill_type == "SOLID":
            fill = self.solid_fill_color(node)
            return bool(fill and fill[3] >= 0.72)
        return True

    def front_layer_visible_ratio(self, node: NormalizedNode) -> float:
        if node.id in self._front_layer_ratio_cache:
            return self._front_layer_ratio_cache[node.id]
        values = self._box_values(node)
        if values is None:
            ratio = 1.0
            self._front_layer_ratio_cache[node.id] = ratio
            return ratio
        node_index = self.node_order.get(node.id, -1)
        samples = self._sample_points(values)
        visible_count = 0
        for point in samples:
            covered = False
            for candidate in self.visible_nodes:
                candidate_index = self.node_order.get(candidate.id, -1)
                if candidate_index <= node_index:
                    continue
                if candidate.id == node.id or self._is_ancestor_or_descendant(node, candidate):
                    continue
                candidate_values = self._box_values(candidate)
                if candidate_values is None or not self._boxes_intersect(values, candidate_values):
                    continue
                if not self._is_front_layer_occluder(candidate):
                    continue
                if self._point_in_values(candidate_values, point):
                    covered = True
                    break
            if not covered:
                visible_count += 1
        ratio = round(visible_count / max(len(samples), 1), 4)
        self._front_layer_ratio_cache[node.id] = ratio
        return ratio

    def is_front_layer_visible(self, node: NormalizedNode) -> bool:
        if not self.is_node_visible(node):
            return False
        values = self._box_values(node)
        if values is None:
            return True
        ratio = self.front_layer_visible_ratio(node)
        if node.type == "TEXT":
            return ratio >= 0.34
        return ratio >= 0.25

    def is_mobile_viewport_candidate(self, node: NormalizedNode) -> bool:
        if node.type not in MOBILE_VIEWPORT_TYPES:
            return False
        box = self.render_or_bbox(node)
        if not box:
            return False
        width = self.bbox_width(node)
        height = self.bbox_height(node)
        if width <= 0 or height <= 0:
            return False
        name = _safe_text(node.name).lower()
        path = _safe_text(node.path).lower()
        if "desktop" in name or "desktop" in path or "tablet" in name or "tablet" in path:
            return False
        if node.type == "COMPONENT" and any(
            ancestor.type == "COMPONENT_SET" for ancestor in self.iter_ancestors(node)
        ):
            return False
        # iOS/Android app screens and modals commonly sit around 320-430pt
        # wide. Very short component examples, palette panels, and button
        # variants are not treated as user-facing viewports.
        return 280 <= width <= 460 and 320 <= height <= 950

    @cached_property
    def mobile_viewport_roots(self) -> list[NormalizedNode]:
        candidates = [
            node for node in self.visible_nodes if self.is_mobile_viewport_candidate(node)
        ]
        candidates.sort(
            key=lambda node: (
                abs(self.bbox_width(node) - 393.0),
                -self.bbox_area(node),
                self.depth_for_mobile_scope(node),
            )
        )
        return candidates

    def depth_for_mobile_scope(self, node: NormalizedNode) -> int:
        return max(0, int(node.depth or 0))

    def mobile_viewport_for(self, node: NormalizedNode) -> NormalizedNode | None:
        if not self.mobile_viewport_roots:
            return None
        target_box = self.render_or_bbox(node)
        if not target_box:
            return None
        target_center = self._box_center(target_box)
        if target_center is None:
            return None
        containing = [
            viewport
            for viewport in self.mobile_viewport_roots
            if self._box_contains_point(self.render_or_bbox(viewport) or {}, target_center)
        ]
        if not containing:
            return None
        containing.sort(key=lambda candidate: self.bbox_area(candidate), reverse=True)
        return containing[0]

    def is_in_mobile_viewport(self, node: NormalizedNode) -> bool:
        if not self.mobile_viewport_roots:
            return True
        if node in self.mobile_viewport_roots:
            return True
        return self.mobile_viewport_for(node) is not None

    @cached_property
    def client_visible_nodes(self) -> list[NormalizedNode]:
        """
        Visible nodes scoped to what a mobile app user can see.

        When the file contains phone-sized frames, detectors inspect only nodes
        inside those frames. If no phone-sized frame exists, the audit falls
        back to all visible nodes so generic fixtures and non-mobile files still
        produce useful results.
        """
        return [
            node
            for node in self.visible_nodes
            if self.is_in_mobile_viewport(node) and self.is_front_layer_visible(node)
        ]

    def visual_priority(self, node: NormalizedNode) -> float:
        total_nodes = max(1, len(self.normalized_file.nodes))
        paint_order = self.node_order.get(node.id, 0) / total_nodes
        viewport = self.mobile_viewport_for(node)
        viewport_bonus = 8.0 if viewport is not None else 0.0
        text_bonus = 1.25 if node.type == "TEXT" and self.has_text(node) else 0.0
        return round(viewport_bonus + paint_order * 4.0 + text_bonus, 4)

    def visual_scope_evidence(self, node: NormalizedNode) -> dict[str, object]:
        viewport = self.mobile_viewport_for(node)
        return {
            "visual_scope": "mobile_client_view" if viewport else "visible_fallback",
            "mobile_viewport_id": viewport.id if viewport else None,
            "mobile_viewport_name": viewport.name if viewport else None,
            "mobile_viewport_width": round(self.bbox_width(viewport), 2) if viewport else None,
            "mobile_viewport_height": round(self.bbox_height(viewport), 2) if viewport else None,
            "top_layer_priority": self.visual_priority(node),
            "front_layer_visible_ratio": self.front_layer_visible_ratio(node),
        }

    def has_text(self, node: NormalizedNode) -> bool:
        return bool(_safe_text(node.characters))

    def text_nodes_in_subtree(self, node: NormalizedNode) -> list[NormalizedNode]:
        if node.id not in self._text_nodes_in_subtree_cache:
            self._text_nodes_in_subtree_cache[node.id] = [
                child
                for child in self.descendants(node.id)
                if child.type == "TEXT"
                and self.has_text(child)
                and self.is_front_layer_visible(child)
            ]
        return self._text_nodes_in_subtree_cache[node.id]

    def contains_action_text(self, node: NormalizedNode) -> bool:
        texts = [node] if node.type == "TEXT" else self.text_nodes_in_subtree(node)
        for text_node in texts:
            label = self.text_label(text_node.characters)
            if any(keyword in label for keyword in ACTION_KEYWORDS):
                return True
        return False

    def is_control_like(self, node: NormalizedNode) -> bool:
        text = " ".join(
            filter(
                None,
                [
                    _safe_text(node.name).lower(),
                    _safe_text(node.frame_name).lower(),
                    _safe_text(node.path).lower(),
                ],
            )
        )
        if any(keyword in text for keyword in CONTROL_KEYWORDS):
            return True
        if node.component_id and any(keyword in text for keyword in ACTION_KEYWORDS):
            return True
        return self.contains_action_text(node)

    def is_navigation_like(self, node: NormalizedNode) -> bool:
        text = " ".join(
            filter(
                None,
                [
                    _safe_text(node.name).lower(),
                    _safe_text(node.frame_name).lower(),
                    _safe_text(node.path).lower(),
                ],
            )
        )
        if any(keyword in text for keyword in NAVIGATION_KEYWORDS):
            return True
        children = self.direct_children(node.id)
        if len(children) < 3 or len(children) > 8:
            return False
        text_count = sum(1 for child in self.descendants(node.id) if child.type == "TEXT" and self.has_text(child))
        iconish_count = sum(1 for child in self.descendants(node.id) if child.type in {"VECTOR", "BOOLEAN_OPERATION", "ELLIPSE"})
        return node.layout_mode == "HORIZONTAL" and text_count >= 2 and iconish_count >= 2

    def solid_fill_color(self, node: NormalizedNode) -> tuple[float, float, float, float] | None:
        visible_fills = [
            fill for fill in node.fills if fill.get("visible") is not False
        ]
        solid_fills = [
            fill for fill in visible_fills if fill.get("type") == "SOLID"
        ]
        if not solid_fills:
            return None

        if len(solid_fills) == 1:
            fill = solid_fills[0]
            color = fill.get("color") or {}
            opacity = float(fill.get("opacity", 1.0))
            alpha = max(0.0, min(float(color.get("a", 1.0)) * opacity, 1.0))
            return (
                float(color.get("r", 0.0)),
                float(color.get("g", 0.0)),
                float(color.get("b", 0.0)),
                alpha,
            )

        background = (1.0, 1.0, 1.0)
        for fill in solid_fills:
            color = fill.get("color") or {}
            opacity = float(fill.get("opacity", 1.0))
            alpha = max(0.0, min(float(color.get("a", 1.0)) * opacity, 1.0))
            foreground = (
                float(color.get("r", 0.0)),
                float(color.get("g", 0.0)),
                float(color.get("b", 0.0)),
            )
            background = self.alpha_composite(foreground, alpha, background)

        return (
            background[0],
            background[1],
            background[2],
            1.0,
        )

    def first_visible_fill_type(self, node: NormalizedNode) -> str | None:
        for fill in node.fills:
            if fill.get("visible") is False:
                continue
            if fill.get("type") != "SOLID":
                return str(fill.get("type"))
            return "SOLID"
        return None

    @staticmethod
    def _box_center(box: dict[str, object]) -> tuple[float, float] | None:
        try:
            return (
                float(box["x"]) + (float(box["width"]) / 2),
                float(box["y"]) + (float(box["height"]) / 2),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _box_contains_point(box: dict[str, object], point: tuple[float, float]) -> bool:
        try:
            x = float(box["x"])
            y = float(box["y"])
            width = float(box["width"])
            height = float(box["height"])
        except (KeyError, TypeError, ValueError):
            return False

        return x <= point[0] <= x + width and y <= point[1] <= y + height

    def painted_background_for(
        self,
        node: NormalizedNode,
    ) -> tuple[str, tuple[float, float, float, float] | None, NormalizedNode | None]:
        """
        Return the nearest visible painted background under a node.

        This checks earlier paint-order nodes in the same frame before falling
        back to ancestors. Image fills are returned as IMAGE because static
        contrast cannot be measured against pixels we have not rendered.
        """
        target_box = node.absolute_render_bounds or node.absolute_bounding_box
        if not target_box:
            return "none", None, None

        target_center = self._box_center(target_box)
        if target_center is None:
            return "none", None, None

        node_index = self.node_order.get(node.id, len(self.normalized_file.nodes))
        best_candidate: NormalizedNode | None = None
        best_index = -1

        for candidate in self.visible_nodes:
            candidate_index = self.node_order.get(candidate.id, -1)
            if candidate_index >= node_index:
                continue
            if candidate.id == node.id:
                continue
            if candidate.frame_id != node.frame_id and candidate.id != node.frame_id:
                continue
            if not candidate.fills or self.first_visible_fill_type(candidate) is None:
                continue
            candidate_box = candidate.absolute_render_bounds or candidate.absolute_bounding_box
            if not candidate_box or not self._box_contains_point(candidate_box, target_center):
                continue
            if candidate_index > best_index:
                best_candidate = candidate
                best_index = candidate_index

        if best_candidate is not None:
            fill_type = self.first_visible_fill_type(best_candidate)
            if fill_type == "IMAGE":
                return "image", None, best_candidate
            fill = self.solid_fill_color(best_candidate)
            if fill and fill[3] > 0:
                return "solid", fill, best_candidate

        for ancestor in self.iter_ancestors(node):
            fill_type = self.first_visible_fill_type(ancestor)
            if fill_type == "IMAGE":
                return "image", None, ancestor
            fill = self.solid_fill_color(ancestor)
            if fill and fill[3] > 0:
                return "solid", fill, ancestor
        frame = self.get_node(node.frame_id)
        if frame:
            fill_type = self.first_visible_fill_type(frame)
            if fill_type == "IMAGE":
                return "image", None, frame
            fill = self.solid_fill_color(frame)
            if fill and fill[3] > 0:
                return "solid", fill, frame
        return "none", None, None

    def nearest_background_fill(self, node: NormalizedNode) -> tuple[float, float, float, float] | None:
        kind, fill, _ = self.painted_background_for(node)
        if kind == "solid":
            return fill
        return None

    def resolved_background_color(self, node: NormalizedNode) -> tuple[float, float, float]:
        fill = self.nearest_background_fill(node)
        if not fill:
            return (1.0, 1.0, 1.0)
        r, g, b, alpha = fill
        return self.alpha_composite((r, g, b), alpha, (1.0, 1.0, 1.0))

    @staticmethod
    def alpha_composite(
        foreground: tuple[float, float, float],
        alpha: float,
        background: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        return tuple((foreground[idx] * alpha) + (background[idx] * (1 - alpha)) for idx in range(3))

    @staticmethod
    def relative_luminance(color: tuple[float, float, float]) -> float:
        def channel(value: float) -> float:
            if value <= 0.03928:
                return value / 12.92
            return ((value + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(component) for component in color)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def contrast_ratio(
        self,
        foreground: tuple[float, float, float],
        background: tuple[float, float, float],
    ) -> float:
        fg = self.relative_luminance(foreground)
        bg = self.relative_luminance(background)
        lighter = max(fg, bg)
        darker = min(fg, bg)
        return round((lighter + 0.05) / (darker + 0.05), 2)

    def node_signature(self, node: NormalizedNode, *, max_depth: int = 2) -> str:
        def walk(current: NormalizedNode, depth: int) -> str:
            descriptor = [
                current.type.lower(),
                self.normalized_name(current.name) or "unnamed",
                current.layout_mode.lower() if current.layout_mode else "free",
                f"children:{len(self.direct_children(current.id))}",
                f"text:{1 if self.has_text(current) else 0}",
            ]
            if depth >= max_depth:
                return "[" + "|".join(descriptor) + "]"
            child_bits = [walk(child, depth + 1) for child in self.direct_children(current.id)[:6]]
            return "[" + "|".join(descriptor + child_bits) + "]"

        return walk(node, 0)

    def spacing_values(self, node: NormalizedNode) -> dict[str, float]:
        values: dict[str, float] = {}
        for field_name in (
            "item_spacing",
            "counter_axis_spacing",
            "padding_left",
            "padding_right",
            "padding_top",
            "padding_bottom",
            "corner_radius",
        ):
            value = _round_number(getattr(node, field_name, None))
            if value is not None:
                values[field_name] = value
        return values

    def average_spacing(self, node: NormalizedNode) -> float | None:
        samples: list[float] = []
        for current in [node, *self.descendants(node.id)]:
            samples.extend(
                value
                for key, value in self.spacing_values(current).items()
                if key != "corner_radius" and 0 < value <= 80
            )
        if not samples:
            return None
        return round(sum(samples) / len(samples), 2)

    def is_large_frame(self, node: NormalizedNode) -> bool:
        if node.type not in {"FRAME", "INSTANCE", "SECTION"}:
            return False
        area = self.bbox_area(node)
        return area >= 120000 or (node.parent_id is None and area >= 50000)

    def spatial_distance(self, first: NormalizedNode, second: NormalizedNode) -> float:
        return math.dist((self.bbox_x(first), self.bbox_y(first)), (self.bbox_x(second), self.bbox_y(second)))
