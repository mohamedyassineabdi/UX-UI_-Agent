from __future__ import annotations

from collections.abc import Generator
from typing import Any


FRAME_LIKE_TYPES = {"FRAME", "COMPONENT", "COMPONENT_SET", "SECTION"}


def walk_nodes(
    node: dict[str, Any],
    *,
    parent_id: str | None = None,
    page_id: str | None = None,
    page_name: str | None = None,
    frame_id: str | None = None,
    frame_name: str | None = None,
    path: str = "",
    depth: int = 0,
) -> Generator[dict[str, Any], None, None]:
    """
    Recursively walk a Figma node tree and yield normalized traversal context.

    Yields dictionaries with:
    - node: the raw current node
    - parent_id: id of the parent node
    - page_id / page_name: current page context
    - frame_id / frame_name: current top-level frame-like context
    - path: full readable hierarchy path
    - depth: nesting depth from the traversal root

    Context rules:
    - CANVAS establishes page context
    - FRAME establishes frame context
    - COMPONENT / COMPONENT_SET / SECTION can also act as frame-like containers
      when no frame has been established yet
    """
    node_id = node.get("id")
    node_name = node.get("name", "")
    node_type = node.get("type", "")

    current_path = f"{path} > {node_name}" if path else node_name

    current_page_id = page_id
    current_page_name = page_name
    current_frame_id = frame_id
    current_frame_name = frame_name

    if node_type == "CANVAS":
        current_page_id = node_id
        current_page_name = node_name

    if node_type == "FRAME":
        current_frame_id = node_id
        current_frame_name = node_name
    elif node_type in FRAME_LIKE_TYPES and current_frame_id is None:
        current_frame_id = node_id
        current_frame_name = node_name

    yield {
        "node": node,
        "parent_id": parent_id,
        "page_id": current_page_id,
        "page_name": current_page_name,
        "frame_id": current_frame_id,
        "frame_name": current_frame_name,
        "path": current_path,
        "depth": depth,
    }

    children = node.get("children", [])
    if not isinstance(children, list):
        return

    for child in children:
        if not isinstance(child, dict):
            continue

        yield from walk_nodes(
            child,
            parent_id=node_id,
            page_id=current_page_id,
            page_name=current_page_name,
            frame_id=current_frame_id,
            frame_name=current_frame_name,
            path=current_path,
            depth=depth + 1,
        )