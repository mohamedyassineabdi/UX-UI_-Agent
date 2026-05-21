from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IssueLocation(BaseModel):
    """
    Exact location of the issue inside the Figma file.
    """

    page_name: str | None = None
    frame_name: str | None = None
    node_id: str | None = None
    node_name: str | None = None
    path: str | None = None


class RectanglePixels(BaseModel):
    """Rectangle coordinates in the annotated screenshot image coordinate space."""

    x: int
    y: int
    width: int
    height: int


class ScreenshotArtifact(BaseModel):
    """Annotated screenshot linked to a specific issue."""

    type: str = "annotated_screenshot"
    image_path: str
    target_node_id: str
    render_node_id: str
    rectangle_px: RectanglePixels
    image_width: int
    image_height: int
    figma_target_bounding_box: dict[str, Any]
    figma_render_bounding_box: dict[str, Any]
    figma_phone_context_bounding_box: dict[str, Any] | None = None
    target_fill_rgb: list[int] | None = None
    client_view_validation: dict[str, Any] | None = None
    coordinate_strategy: str
    coordinate_source: str = "absoluteBoundingBox"
    accuracy: str
    notes: list[str] = Field(default_factory=list)


class AuditIssue(BaseModel):
    """
    Generic issue object for future audit rules.
    """

    id: str
    axis: str | None = None
    criterion: str | None = None
    severity: Severity
    message: str
    location: IssueLocation
    evidence: dict[str, Any] = Field(default_factory=dict)
    visual_evidence: list[ScreenshotArtifact] = Field(default_factory=list)
