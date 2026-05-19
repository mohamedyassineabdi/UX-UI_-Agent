from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NormalizedPage(BaseModel):
    """
    Represents a Figma page (CANVAS).
    """
    id: str
    name: str


class NormalizedFrame(BaseModel):
    """
    Represents a frame-like top-level audit container.

    We keep FRAME, COMPONENT, COMPONENT_SET, and SECTION here when relevant,
    because all of them can act like meaningful screen/group containers
    during audits.
    """
    id: str
    name: str
    type: str

    page_id: str | None = None
    page_name: str | None = None
    parent_id: str | None = None

    path: str

    visible: bool | None = None
    absolute_bounding_box: dict[str, Any] | None = None
    absolute_render_bounds: dict[str, Any] | None = None


class NormalizedComponent(BaseModel):
    """
    Represents a reusable component definition in the file.
    """
    node_id: str
    key: str | None = None
    name: str

    page_id: str | None = None
    page_name: str | None = None

    path: str


class NormalizedToken(BaseModel):
    """
    Represents a normalized Figma variable/token.
    """
    id: str
    name: str
    token_type: str

    collection_id: str | None = None
    collection_name: str | None = None

    values_by_mode: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list)


class NormalizedNode(BaseModel):
    """
    Represents a single normalized node extracted from the Figma tree.

    This is the main object that later audit rules will inspect.
    """
    id: str
    name: str
    type: str

    parent_id: str | None = None

    page_id: str | None = None
    page_name: str | None = None

    frame_id: str | None = None
    frame_name: str | None = None

    path: str
    depth: int

    visible: bool | None = None
    absolute_bounding_box: dict[str, Any] | None = None
    absolute_render_bounds: dict[str, Any] | None = None

    fills: list[dict[str, Any]] = Field(default_factory=list)
    strokes: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    style: dict[str, Any] | None = None

    layout_mode: str | None = None
    layout_wrap: str | None = None
    item_spacing: float | int | None = None
    counter_axis_spacing: float | int | None = None

    padding_left: float | int | None = None
    padding_right: float | int | None = None
    padding_top: float | int | None = None
    padding_bottom: float | int | None = None

    corner_radius: float | int | None = None
    rectangle_corner_radii: list[float | int] = Field(default_factory=list)

    characters: str | None = None
    text_style: dict[str, Any] | None = None

    component_id: str | None = None
    component_properties: dict[str, Any] = Field(default_factory=dict)
    is_instance: bool = False

    bound_variables: dict[str, Any] = Field(default_factory=dict)
    raw_plugin_data: dict[str, Any] = Field(default_factory=dict)


class NormalizedFigmaFile(BaseModel):
    """
    Final output of Layer B (Normalization).
    """
    file_key: str
    file_name: str | None = None
    last_modified: str | None = None
    version: str | None = None
    editor_type: str | None = None

    pages: list[NormalizedPage] = Field(default_factory=list)
    frames: list[NormalizedFrame] = Field(default_factory=list)
    nodes: list[NormalizedNode] = Field(default_factory=list)
    components: list[NormalizedComponent] = Field(default_factory=list)
    tokens: list[NormalizedToken] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
