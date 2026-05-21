from __future__ import annotations

from typing import Any

from figma_audit.models.normalized_models import (
    NormalizedComponent,
    NormalizedFigmaFile,
    NormalizedFrame,
    NormalizedNode,
    NormalizedPage,
)
from figma_audit.models.raw_bundle import RawFigmaBundle
from figma_audit.normalization.token_normalizer import normalize_tokens
from figma_audit.normalization.tree_walker import walk_nodes

FRAME_LIKE_TYPES = {"FRAME", "COMPONENT", "COMPONENT_SET", "SECTION"}


def _safe_dict(value: Any) -> dict[str, Any]:
    """
    Return the value if it is a dict, otherwise return an empty dict.
    """
    return value if isinstance(value, dict) else {}


def _safe_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """
    Return a list containing only dict items if the input is a list,
    otherwise return an empty list.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalize_figma_bundle(bundle: RawFigmaBundle) -> NormalizedFigmaFile:
    """
    Convert a RawFigmaBundle into a NormalizedFigmaFile.

    This is the core Layer B function.
    """
    raw_file = bundle.raw_file
    document = raw_file.get("document", {})
    document = _safe_dict(document)

    pages: list[NormalizedPage] = []
    frames: list[NormalizedFrame] = []
    nodes: list[NormalizedNode] = []
    components: list[NormalizedComponent] = []

    seen_page_ids: set[str] = set()
    seen_frame_ids: set[str] = set()
    seen_component_ids: set[str] = set()

    if document.get("type") == "DOCUMENT":
        traversal_roots = document.get("children", [])
        if not isinstance(traversal_roots, list):
            traversal_roots = []
    else:
        traversal_roots = [document]

    for child in traversal_roots:
        if not isinstance(child, dict):
            continue

        child_id = child.get("id", "")
        child_name = child.get("name", "")
        child_type = child.get("type", "")

        if child_type == "CANVAS" and child_id and child_id not in seen_page_ids:
            pages.append(
                NormalizedPage(
                    id=child_id,
                    name=child_name,
                )
            )
            seen_page_ids.add(child_id)

        for item in walk_nodes(child):
            node = item["node"]
            node_id = node.get("id", "")
            node_name = node.get("name", "")
            node_type = node.get("type", "")

            page_id = item["page_id"]
            page_name = item["page_name"]
            frame_id = item["frame_id"]
            frame_name = item["frame_name"]
            parent_id = item["parent_id"]
            path = item["path"]
            depth = item["depth"]

            absolute_bounding_box = _safe_dict(node.get("absoluteBoundingBox")) or None
            absolute_render_bounds = _safe_dict(node.get("absoluteRenderBounds")) or None
            fills = _safe_list_of_dicts(node.get("fills"))
            strokes = _safe_list_of_dicts(node.get("strokes"))
            effects = _safe_list_of_dicts(node.get("effects"))
            style = _safe_dict(node.get("style")) or None
            component_properties = _safe_dict(node.get("componentProperties"))
            bound_variables = _safe_dict(node.get("boundVariables"))
            raw_plugin_data = _safe_dict(node.get("pluginData"))

            if node_type in FRAME_LIKE_TYPES and node_id and node_id not in seen_frame_ids:
                frames.append(
                    NormalizedFrame(
                        id=node_id,
                        name=node_name,
                        type=node_type,
                        page_id=page_id,
                        page_name=page_name,
                        parent_id=parent_id,
                        path=path,
                        visible=node.get("visible"),
                        absolute_bounding_box=absolute_bounding_box,
                        absolute_render_bounds=absolute_render_bounds,
                    )
                )
                seen_frame_ids.add(node_id)

            if node_type == "COMPONENT" and node_id and node_id not in seen_component_ids:
                components.append(
                    NormalizedComponent(
                        node_id=node_id,
                        key=node.get("key"),
                        name=node_name,
                        page_id=page_id,
                        page_name=page_name,
                        path=path,
                    )
                )
                seen_component_ids.add(node_id)

            text_style = style if node_type == "TEXT" else None

            nodes.append(
                NormalizedNode(
                    id=node_id,
                    name=node_name,
                    type=node_type,
                    parent_id=parent_id,
                    page_id=page_id,
                    page_name=page_name,
                    frame_id=frame_id,
                    frame_name=frame_name,
                    path=path,
                    depth=depth,
                    visible=node.get("visible"),
                    absolute_bounding_box=absolute_bounding_box,
                    absolute_render_bounds=absolute_render_bounds,
                    fills=fills,
                    strokes=strokes,
                    effects=effects,
                    style=style,
                    layout_mode=node.get("layoutMode"),
                    layout_wrap=node.get("layoutWrap"),
                    item_spacing=node.get("itemSpacing"),
                    counter_axis_spacing=node.get("counterAxisSpacing"),
                    padding_left=node.get("paddingLeft"),
                    padding_right=node.get("paddingRight"),
                    padding_top=node.get("paddingTop"),
                    padding_bottom=node.get("paddingBottom"),
                    corner_radius=node.get("cornerRadius"),
                    rectangle_corner_radii=node.get("rectangleCornerRadii") or [],
                    characters=node.get("characters"),
                    text_style=text_style,
                    component_id=node.get("componentId"),
                    component_properties=component_properties,
                    is_instance=(node_type == "INSTANCE"),
                    bound_variables=bound_variables,
                    raw_plugin_data=raw_plugin_data,
                )
            )

    tokens = normalize_tokens(bundle.raw_variables)

    return NormalizedFigmaFile(
        file_key=bundle.file_key,
        file_name=raw_file.get("name"),
        last_modified=raw_file.get("lastModified"),
        version=raw_file.get("version"),
        editor_type=raw_file.get("editorType"),
        pages=pages,
        frames=frames,
        nodes=nodes,
        components=components,
        tokens=tokens,
        warnings=bundle.warnings,
    )
