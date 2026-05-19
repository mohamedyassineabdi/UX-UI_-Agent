from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode


def _bbox_area(node: NormalizedNode) -> float | None:
    box = node.absolute_bounding_box or {}
    width = box.get("width")
    height = box.get("height")
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None
    return round(float(width) * float(height), 2)


def _solid_color_fills(node: NormalizedNode) -> list[dict[str, Any]]:
    colors: list[dict[str, Any]] = []
    for fill in node.fills:
        if fill.get("type") != "SOLID":
            continue
        color = fill.get("color")
        if not isinstance(color, dict):
            continue
        colors.append(
            {
                "node_id": node.id,
                "node_name": node.name,
                "node_type": node.type,
                "path": node.path,
                "fill": fill,
            }
        )
    return colors


def _text_payload(node: NormalizedNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "characters": node.characters,
        "page_name": node.page_name,
        "frame_id": node.frame_id,
        "frame_name": node.frame_name,
        "parent_id": node.parent_id,
        "path": node.path,
        "visible": node.visible,
        "style": node.text_style or node.style,
        "fills": node.fills,
        "absolute_bounding_box": node.absolute_bounding_box,
        "absolute_render_bounds": node.absolute_render_bounds,
    }


def _layout_payload(node: NormalizedNode) -> dict[str, Any] | None:
    fields = {
        "layout_mode": node.layout_mode,
        "layout_wrap": node.layout_wrap,
        "item_spacing": node.item_spacing,
        "counter_axis_spacing": node.counter_axis_spacing,
        "padding_left": node.padding_left,
        "padding_right": node.padding_right,
        "padding_top": node.padding_top,
        "padding_bottom": node.padding_bottom,
        "corner_radius": node.corner_radius,
        "rectangle_corner_radii": node.rectangle_corner_radii,
    }
    if not any(value not in (None, [], {}) for value in fields.values()):
        return None
    return {
        "id": node.id,
        "name": node.name,
        "type": node.type,
        "page_name": node.page_name,
        "frame_id": node.frame_id,
        "frame_name": node.frame_name,
        "parent_id": node.parent_id,
        "path": node.path,
        **fields,
    }


def build_audit_extraction(normalized_file: NormalizedFigmaFile) -> dict[str, Any]:
    """
    Build the complete static audit input before any analysis runs.

    The payload intentionally contains the full normalized file plus derived
    indexes. That keeps the audit input complete for current rules and makes
    future rules less likely to need another Figma fetch.
    """
    normalized_payload = normalized_file.model_dump(mode="json")
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    node_ids_by_type: dict[str, list[str]] = defaultdict(list)
    node_type_counts: Counter[str] = Counter()
    page_ids: set[str] = set()
    frame_ids: set[str] = set()
    text_nodes: list[dict[str, Any]] = []
    layout_nodes: list[dict[str, Any]] = []
    color_fills: list[dict[str, Any]] = []
    geometry_nodes: list[dict[str, Any]] = []

    for node in normalized_file.nodes:
        children_by_parent[node.parent_id or "__root__"].append(node.id)
        node_ids_by_type[node.type].append(node.id)
        node_type_counts[node.type] += 1
        if node.page_id:
            page_ids.add(node.page_id)
        if node.frame_id:
            frame_ids.add(node.frame_id)
        if node.type == "TEXT" and node.characters:
            text_nodes.append(_text_payload(node))

        layout_payload = _layout_payload(node)
        if layout_payload is not None:
            layout_nodes.append(layout_payload)

        color_fills.extend(_solid_color_fills(node))

        if node.absolute_bounding_box or node.absolute_render_bounds:
            geometry_nodes.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "type": node.type,
                    "page_name": node.page_name,
                    "frame_id": node.frame_id,
                    "frame_name": node.frame_name,
                    "parent_id": node.parent_id,
                    "path": node.path,
                    "absolute_bounding_box": node.absolute_bounding_box,
                    "absolute_render_bounds": node.absolute_render_bounds,
                    "area": _bbox_area(node),
                }
            )

    return {
        "schema_version": "audit_extraction.v1",
        "purpose": "Complete static Figma audit input saved before analysis.",
        "completeness_note": (
            "Includes every normalized node/component/token field used by this audit pipeline. "
            "The raw Figma API bundle is preserved separately for source-level debugging."
        ),
        "file": {
            "file_key": normalized_file.file_key,
            "file_name": normalized_file.file_name,
            "last_modified": normalized_file.last_modified,
            "version": normalized_file.version,
            "editor_type": normalized_file.editor_type,
            "warnings": normalized_file.warnings,
        },
        "counts": {
            "pages": len(normalized_file.pages),
            "frames": len(normalized_file.frames),
            "nodes": len(normalized_file.nodes),
            "components": len(normalized_file.components),
            "tokens": len(normalized_file.tokens),
            "text_nodes": len(text_nodes),
            "layout_nodes": len(layout_nodes),
            "color_fills": len(color_fills),
            "geometry_nodes": len(geometry_nodes),
        },
        "normalized_file": normalized_payload,
        "indexes": {
            "children_by_parent": dict(children_by_parent),
            "node_ids_by_type": dict(node_ids_by_type),
            "node_type_counts": dict(node_type_counts),
            "page_ids": sorted(page_ids),
            "frame_ids": sorted(frame_ids),
            "component_node_ids": [component.node_id for component in normalized_file.components],
            "text_node_ids": [node["id"] for node in text_nodes],
        },
        "audit_views": {
            "pages": normalized_payload["pages"],
            "frames": normalized_payload["frames"],
            "components": normalized_payload["components"],
            "tokens": normalized_payload["tokens"],
            "text_content": text_nodes,
            "layout": layout_nodes,
            "colors": color_fills,
            "geometry": geometry_nodes,
        },
    }


def normalized_file_from_audit_extraction(extraction: dict[str, Any]) -> NormalizedFigmaFile:
    """Rebuild the analysis input from the saved audit extraction payload."""
    normalized_payload = extraction.get("normalized_file")
    if not isinstance(normalized_payload, dict):
        raise ValueError("Audit extraction is missing normalized_file.")
    return NormalizedFigmaFile.model_validate(normalized_payload)
