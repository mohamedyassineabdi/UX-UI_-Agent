from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

try:
    from PIL import Image, ImageDraw
except ModuleNotFoundError as exc:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    PIL_IMPORT_ERROR = exc
else:
    PIL_IMPORT_ERROR = None

from figma_audit.config import (
    ANNOTATION_IMAGE_SCALE,
    ANNOTATION_MAX_IMAGES,
    ANNOTATION_WORKERS,
    ANNOTATIONS_OUTPUT_DIR,
)
from figma_audit.ingestion.figma_client import FigmaClient
from figma_audit.models.issue import AuditIssue
from figma_audit.models.issue import RectanglePixels, ScreenshotArtifact
from figma_audit.models.normalized_models import NormalizedFigmaFile, NormalizedNode
from figma_audit.utils.progress import progress_bar


class IssueContainer(Protocol):
    issues: list[AuditIssue]


@dataclass(frozen=True)
class AnnotationCandidate:
    issue: AuditIssue
    target_node: NormalizedNode
    target_box: dict[str, Any]
    coordinate_source: str
    render_node: NormalizedNode
    render_box: dict[str, Any]


class RealScreenshotRequiredError(RuntimeError):
    """Raised when the caller requested real Figma screenshots only."""


def _require_pillow() -> None:
    if PIL_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Pillow is required for screenshot annotation. "
            "Install dependencies with: python -m pip install -r requirements.txt"
        ) from PIL_IMPORT_ERROR


def _bbox_number(box: dict[str, Any], key: str) -> float | None:
    value = box.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _valid_box(box: dict[str, Any] | None) -> dict[str, Any] | None:
    if not box:
        return None

    required = ("x", "y", "width", "height")
    if any(_bbox_number(box, key) is None for key in required):
        return None

    if float(box["width"]) <= 0 or float(box["height"]) <= 0:
        return None

    return box


def _required_bbox(node: NormalizedNode) -> dict[str, Any] | None:
    return _valid_box(node.absolute_bounding_box)


def _annotation_box(node: NormalizedNode) -> tuple[dict[str, Any] | None, str]:
    """
    Prefer visible render bounds for the red rectangle when Figma provides them.

    absoluteRenderBounds better reflects the pixels Figma rendered for strokes,
    effects, and non-layout visual overflow. We fall back to absoluteBoundingBox
    because some nodes do not expose render bounds.
    """
    render_bounds = _valid_box(node.absolute_render_bounds)
    if render_bounds is not None:
        return render_bounds, "absoluteRenderBounds"
    return _required_bbox(node), "absoluteBoundingBox"


def _contains(parent_box: dict[str, Any], child_box: dict[str, Any]) -> bool:
    parent_x = float(parent_box["x"])
    parent_y = float(parent_box["y"])
    parent_right = parent_x + float(parent_box["width"])
    parent_bottom = parent_y + float(parent_box["height"])

    child_x = float(child_box["x"])
    child_y = float(child_box["y"])
    child_right = child_x + float(child_box["width"])
    child_bottom = child_y + float(child_box["height"])

    return (
        child_x >= parent_x
        and child_y >= parent_y
        and child_right <= parent_right
        and child_bottom <= parent_bottom
    )


def _box_area(box: dict[str, Any]) -> float:
    return float(box["width"]) * float(box["height"])


def _has_useful_context(candidate_box: dict[str, Any], target_box: dict[str, Any]) -> bool:
    return (
        float(candidate_box["width"]) >= float(target_box["width"]) * 2
        and float(candidate_box["height"]) >= float(target_box["height"]) * 4
        and _box_area(candidate_box) >= _box_area(target_box) * 8
    )


def _sanitize_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _render_cache_path(
    *,
    output_dir: Path,
    file_key: str,
    file_version: str | None,
    render_node_id: str,
    image_format: str,
    image_scale: float,
) -> Path | None:
    if not file_version:
        return None

    format_label = _sanitize_id(image_format.lower() or "png")
    scale_label = _sanitize_id(f"{image_scale:g}")
    return (
        output_dir
        / "_render_cache"
        / (
            f"{_sanitize_id(file_key)}__v-{_sanitize_id(file_version)}__"
            f"node-{_sanitize_id(render_node_id)}__{format_label}__scale-{scale_label}."
            f"{format_label}"
        )
    )


def _read_render_cache(path: Path, log: Callable[[str], None] | None) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if log is not None:
            log(f"Could not read cached rendered image {path}: {exc}")
        return None


def _write_render_cache(path: Path, image_bytes: bytes, log: Callable[[str], None] | None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
    except OSError as exc:
        if log is not None:
            log(f"Could not write cached rendered image {path}: {exc}")


def _node_lookup(normalized_file: NormalizedFigmaFile) -> dict[str, NormalizedNode]:
    return {node.id: node for node in normalized_file.nodes}


def _children_by_parent(normalized_file: NormalizedFigmaFile) -> dict[str | None, list[NormalizedNode]]:
    children: dict[str | None, list[NormalizedNode]] = {}
    for node in normalized_file.nodes:
        children.setdefault(node.parent_id, []).append(node)
    return children


def choose_render_node(
    target_node: NormalizedNode,
    nodes_by_id: dict[str, NormalizedNode],
    *,
    scope: str = "context",
) -> NormalizedNode:
    """
    Pick the context node to render for the annotation.

    Preference order:
    1. The target's containing frame, if it has a usable bounding box.
    2. The nearest ancestor with a usable bounding box that contains the target.
    3. The target itself.
    """
    target_box, _ = _annotation_box(target_node)
    if target_box is None:
        return target_node

    if scope == "page":
        highest: NormalizedNode | None = None
        current = nodes_by_id.get(target_node.parent_id or "")
        while current is not None:
            current_box = _required_bbox(current)
            if current_box and _contains(current_box, target_box):
                highest = current
            current = nodes_by_id.get(current.parent_id or "")
        if highest is not None:
            return highest

    fallback: NormalizedNode | None = None
    current = nodes_by_id.get(target_node.parent_id or "")
    while current is not None:
        current_box = _required_bbox(current)
        if current_box and _contains(current_box, target_box):
            if fallback is None:
                fallback = current
            if _has_useful_context(current_box, target_box):
                return current
        current = nodes_by_id.get(current.parent_id or "")

    if target_node.frame_id and target_node.frame_id != target_node.id:
        frame = nodes_by_id.get(target_node.frame_id)
        frame_box = _required_bbox(frame) if frame else None
        if frame and frame_box and _contains(frame_box, target_box):
            if _has_useful_context(frame_box, target_box):
                return frame
            fallback = fallback or frame

    return fallback or target_node


def calculate_rectangle_pixels(
    *,
    target_box: dict[str, Any],
    render_box: dict[str, Any],
    image_width: int,
    image_height: int,
) -> RectanglePixels:
    """Map Figma absolute coordinates into rendered screenshot pixel space."""
    render_width = float(render_box["width"])
    render_height = float(render_box["height"])

    if render_width <= 0 or render_height <= 0:
        raise ValueError("Render bounding box must have positive dimensions.")

    scale_x = image_width / render_width
    scale_y = image_height / render_height

    x = round((float(target_box["x"]) - float(render_box["x"])) * scale_x)
    y = round((float(target_box["y"]) - float(render_box["y"])) * scale_y)
    width = round(float(target_box["width"]) * scale_x)
    height = round(float(target_box["height"]) * scale_y)

    x = max(0, min(x, image_width - 1))
    y = max(0, min(y, image_height - 1))
    width = max(1, min(width, image_width - x))
    height = max(1, min(height, image_height - y))

    return RectanglePixels(x=x, y=y, width=width, height=height)


def _draw_precise_rectangle(image: Image.Image, rectangle: RectanglePixels) -> Image.Image:
    _require_pillow()
    annotated = image.convert("RGBA")
    draw = ImageDraw.Draw(annotated)

    stroke_width = max(3, round(min(annotated.size) / 160))
    left = rectangle.x
    top = rectangle.y
    right = rectangle.x + rectangle.width - 1
    bottom = rectangle.y + rectangle.height - 1

    halo_width = stroke_width + 2
    for offset in range(halo_width, 0, -1):
        draw.rectangle(
            [
                max(0, left - offset),
                max(0, top - offset),
                min(annotated.width - 1, right + offset),
                min(annotated.height - 1, bottom + offset),
            ],
            outline=(255, 255, 255, 255),
        )

    for offset in range(stroke_width):
        draw.rectangle(
            [
                max(0, left - offset),
                max(0, top - offset),
                min(annotated.width - 1, right + offset),
                min(annotated.height - 1, bottom + offset),
            ],
            outline=(255, 0, 0, 255),
        )

    return annotated.convert("RGB")


def _locator_image_size(render_box: dict[str, Any]) -> tuple[int, int]:
    render_width = max(1.0, float(render_box["width"]))
    render_height = max(1.0, float(render_box["height"]))
    scale = min(3.0, 1200.0 / render_width, 900.0 / render_height)
    scale = max(0.2, scale)
    return max(120, round(render_width * scale)), max(90, round(render_height * scale))


def _solid_fill_rgba(node: NormalizedNode) -> tuple[int, int, int, int] | None:
    for fill in node.fills:
        if fill.get("visible") is False or fill.get("type") != "SOLID":
            continue
        color = fill.get("color")
        if not isinstance(color, dict):
            continue
        opacity = float(fill.get("opacity", 1.0) or 1.0)
        alpha = max(0.0, min(float(color.get("a", 1.0)) * opacity, 1.0))
        return (
            round(max(0.0, min(float(color.get("r", 0.0)), 1.0)) * 255),
            round(max(0.0, min(float(color.get("g", 0.0)), 1.0)) * 255),
            round(max(0.0, min(float(color.get("b", 0.0)), 1.0)) * 255),
            round(alpha * 255),
        )
    return None


def _first_visible_fill_type(node: NormalizedNode) -> str | None:
    for fill in node.fills:
        if fill.get("visible") is False:
            continue
        fill_type = fill.get("type")
        if isinstance(fill_type, str):
            return fill_type
    return None


def _descendants(
    node_id: str,
    children_by_parent: dict[str | None, list[NormalizedNode]],
) -> list[NormalizedNode]:
    collected: list[NormalizedNode] = []
    stack = list(reversed(children_by_parent.get(node_id, [])))
    while stack:
        node = stack.pop()
        if node.visible is not False:
            collected.append(node)
            stack.extend(reversed(children_by_parent.get(node.id, [])))
    return collected


def _draw_translucent_rect(
    image: Image.Image,
    rectangle: RectanglePixels,
    color: tuple[int, int, int, int],
    *,
    outline: tuple[int, int, int, int] | None = None,
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    box = [
        rectangle.x,
        rectangle.y,
        rectangle.x + rectangle.width - 1,
        rectangle.y + rectangle.height - 1,
    ]
    draw.rectangle(box, fill=color, outline=outline)
    image.alpha_composite(overlay)


def _draw_image_fill_placeholder(image: Image.Image, rectangle: RectanglePixels) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    box = [
        rectangle.x,
        rectangle.y,
        rectangle.x + rectangle.width - 1,
        rectangle.y + rectangle.height - 1,
    ]
    draw.rectangle(box, fill=(51, 65, 85, 210), outline=(71, 85, 105, 255))
    step = max(14, min(rectangle.width, rectangle.height) // 8)
    for offset in range(-rectangle.height, rectangle.width, step):
        draw.line(
            [
                (rectangle.x + offset, rectangle.y + rectangle.height),
                (rectangle.x + offset + rectangle.height, rectangle.y),
            ],
            fill=(100, 116, 139, 130),
            width=1,
        )
    if rectangle.width >= 36 and rectangle.height >= 14:
        draw.text((rectangle.x + 4, rectangle.y + 3), "IMAGE", fill=(226, 232, 240, 255))
    image.alpha_composite(overlay)


def _box_right(box: dict[str, Any]) -> float:
    return float(box["x"]) + float(box["width"])


def _box_bottom(box: dict[str, Any]) -> float:
    return float(box["y"]) + float(box["height"])


def _boxes_intersect(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return not (
        _box_right(first) <= float(second["x"])
        or _box_right(second) <= float(first["x"])
        or _box_bottom(first) <= float(second["y"])
        or _box_bottom(second) <= float(first["y"])
    )


def _clamped_crop_box(
    *,
    target_box: dict[str, Any],
    render_box: dict[str, Any],
    panel_width: int,
    panel_height: int,
    width_multiplier: float,
    height_multiplier: float,
    min_width: float,
    min_height: float,
) -> dict[str, Any]:
    render_width = float(render_box["width"])
    render_height = float(render_box["height"])
    target_width = float(target_box["width"])
    target_height = float(target_box["height"])
    panel_aspect = panel_width / panel_height

    crop_width = min(render_width, max(min_width, target_width * width_multiplier))
    crop_height = min(render_height, max(min_height, target_height * height_multiplier))

    if crop_width / crop_height > panel_aspect:
        crop_height = min(render_height, crop_width / panel_aspect)
    else:
        crop_width = min(render_width, crop_height * panel_aspect)

    target_center_x = float(target_box["x"]) + (target_width / 2)
    target_center_y = float(target_box["y"]) + (target_height / 2)
    min_x = float(render_box["x"])
    min_y = float(render_box["y"])
    max_x = min_x + render_width - crop_width
    max_y = min_y + render_height - crop_height

    x = max(min_x, min(target_center_x - (crop_width / 2), max_x))
    y = max(min_y, min(target_center_y - (crop_height / 2), max_y))

    return {
        "x": x,
        "y": y,
        "width": crop_width,
        "height": crop_height,
    }


def _draw_panel_border(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str) -> None:
    x, y, width, height = box
    draw.rectangle([x, y, x + width, y + height], fill=(255, 255, 255), outline=(203, 213, 225), width=1)
    draw.text((x + 12, y + 10), title, fill=(15, 23, 42))


def _render_preview_panel(
    *,
    candidate: AnnotationCandidate,
    crop_box: dict[str, Any],
    width: int,
    height: int,
    children_by_parent: dict[str | None, list[NormalizedNode]],
) -> tuple[Image.Image, RectanglePixels]:
    panel = Image.new("RGBA", (width, height), (248, 250, 252, 255))
    _draw_simplified_figma_preview(
        image=panel,
        render_node=candidate.render_node,
        render_box=crop_box,
        children_by_parent=children_by_parent,
    )
    rectangle = calculate_rectangle_pixels(
        target_box=candidate.target_box,
        render_box=crop_box,
        image_width=width,
        image_height=height,
    )
    panel = _draw_precise_rectangle(panel.convert("RGB"), rectangle).convert("RGBA")
    return panel, rectangle


def _evidence_rgb(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    channels: list[int] = []
    for channel in value[:3]:
        if not isinstance(channel, (int, float)):
            return None
        channels.append(round(max(0.0, min(float(channel), 1.0)) * 255))
    return channels[0], channels[1], channels[2]


def _short_text(value: object, limit: int = 46) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "."


def _image_text(value: object, limit: int = 90) -> str:
    text = _short_text(value, limit)
    ascii_text = text.encode("ascii", "ignore").decode("ascii").strip()
    return ascii_text or text


def _wrap_text(text: str, max_chars: int) -> list[str]:
    words = text.replace("\n", " ").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + len(word) + 1 <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_key_value(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    key: str,
    value: object,
    max_chars: int = 42,
) -> int:
    draw.text((x, y), key, fill=(71, 85, 105))
    y += 16
    for line in _wrap_text(_image_text(value, max_chars * 2), max_chars):
        draw.text((x, y), line, fill=(15, 23, 42))
        y += 15
    return y + 8


def _draw_swatch(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    label: str,
    color: tuple[int, int, int] | None,
) -> int:
    draw.text((x, y), label, fill=(71, 85, 105))
    if color is None:
        draw.rectangle([x, y + 17, x + 42, y + 45], outline=(203, 213, 225), fill=(248, 250, 252))
        draw.line([x, y + 17, x + 42, y + 45], fill=(239, 68, 68), width=2)
    else:
        draw.rectangle([x, y + 17, x + 42, y + 45], outline=(15, 23, 42), fill=color)
        draw.text((x + 50, y + 24), f"rgb{color}", fill=(15, 23, 42))
    return y + 58


def _draw_simplified_figma_preview(
    *,
    image: Image.Image,
    render_node: NormalizedNode,
    render_box: dict[str, Any],
    children_by_parent: dict[str | None, list[NormalizedNode]],
) -> None:
    render_fill = _solid_fill_rgba(render_node)
    if render_fill:
        _draw_translucent_rect(
            image,
            RectanglePixels(x=0, y=0, width=image.width, height=image.height),
            render_fill,
            outline=(148, 163, 184, 255),
        )

    draw = ImageDraw.Draw(image)
    for node in _descendants(render_node.id, children_by_parent):
        node_box = _valid_box(node.absolute_render_bounds) or _valid_box(node.absolute_bounding_box)
        if node_box is None:
            continue
        if not _boxes_intersect(node_box, render_box):
            continue

        try:
            rectangle = calculate_rectangle_pixels(
                target_box=node_box,
                render_box=render_box,
                image_width=image.width,
                image_height=image.height,
            )
        except Exception:
            continue

        if rectangle.width <= 1 or rectangle.height <= 1:
            continue

        fill = _solid_fill_rgba(node)
        if fill and fill[3] > 0:
            _draw_translucent_rect(
                image,
                rectangle,
                fill,
                outline=(203, 213, 225, 120) if min(rectangle.width, rectangle.height) > 8 else None,
            )
        elif _first_visible_fill_type(node) == "IMAGE":
            _draw_image_fill_placeholder(image, rectangle)
        elif node.type in {"FRAME", "INSTANCE", "COMPONENT", "COMPONENT_SET", "SECTION", "GROUP"}:
            draw.rectangle(
                [
                    rectangle.x,
                    rectangle.y,
                    rectangle.x + rectangle.width - 1,
                    rectangle.y + rectangle.height - 1,
                ],
                outline=(203, 213, 225, 170),
                width=1,
            )

        if node.type == "TEXT" and node.characters and rectangle.width >= 16 and rectangle.height >= 7:
            text_color = _solid_fill_rgba(node) or (15, 23, 42, 255)
            text = node.characters.strip().replace("\n", " ")
            if text:
                max_chars = max(3, rectangle.width // 5)
                if len(text) > max_chars:
                    text = text[: max_chars - 1] + "."
                draw.text(
                    (rectangle.x, rectangle.y),
                    text,
                    fill=text_color[:3],
                )


def _draw_geometry_locator_image(
    *,
    candidate: AnnotationCandidate,
    warning: str,
    output_dir: Path,
    children_by_parent: dict[str | None, list[NormalizedNode]] | None = None,
) -> None:
    """
    Create a local evidence image when Figma's render endpoint is unavailable.

    This is not a rendered design screenshot. It builds a focused, readable
    preview from cached Figma geometry, fills, and text so the issue location
    is understandable even when /v1/images is rate-limited.
    """
    image_width, image_height = 1200, 760
    image = Image.new("RGBA", (image_width, image_height), (246, 247, 249, 255))
    draw = ImageDraw.Draw(image)

    draw.text((32, 24), "Problem evidence preview", fill=(15, 23, 42))
    draw.text(
        (32, 44),
        "Generated from Figma node data because Figma's render screenshot endpoint is unavailable.",
        fill=(71, 85, 105),
    )
    draw.text((32, 66), _image_text(candidate.issue.message, 128), fill=(185, 28, 28))

    context_x, context_y, context_w, context_h = 32, 116, 720, 540
    zoom_x, zoom_y, zoom_w, zoom_h = 784, 116, 384, 248
    details_x, details_y = 784, 396

    _draw_panel_border(draw, (context_x, context_y, context_w, context_h), "Cropped context around the issue")
    _draw_panel_border(draw, (zoom_x, zoom_y, zoom_w, zoom_h), "Zoomed target")

    if children_by_parent is None:
        children_by_parent = {}

    context_crop = _clamped_crop_box(
        target_box=candidate.target_box,
        render_box=candidate.render_box,
        panel_width=context_w - 24,
        panel_height=context_h - 52,
        width_multiplier=7,
        height_multiplier=12,
        min_width=260,
        min_height=180,
    )
    context_panel, context_rectangle = _render_preview_panel(
        candidate=candidate,
        crop_box=context_crop,
        width=context_w - 24,
        height=context_h - 52,
        children_by_parent=children_by_parent,
    )
    image.alpha_composite(context_panel, (context_x + 12, context_y + 40))

    zoom_crop = _clamped_crop_box(
        target_box=candidate.target_box,
        render_box=candidate.render_box,
        panel_width=zoom_w - 24,
        panel_height=zoom_h - 52,
        width_multiplier=3,
        height_multiplier=7,
        min_width=90,
        min_height=70,
    )
    zoom_panel, _ = _render_preview_panel(
        candidate=candidate,
        crop_box=zoom_crop,
        width=zoom_w - 24,
        height=zoom_h - 52,
        children_by_parent=children_by_parent,
    )
    image.alpha_composite(zoom_panel, (zoom_x + 12, zoom_y + 40))

    global_rectangle = RectanglePixels(
        x=context_x + 12 + context_rectangle.x,
        y=context_y + 40 + context_rectangle.y,
        width=context_rectangle.width,
        height=context_rectangle.height,
    )

    evidence = candidate.issue.evidence
    detail_y = details_y
    detail_y = _draw_key_value(draw, x=details_x, y=detail_y, key="Problem node", value=candidate.target_node.name)
    detail_y = _draw_key_value(draw, x=details_x, y=detail_y, key="Node ID", value=candidate.target_node.id)
    if candidate.target_node.frame_name:
        detail_y = _draw_key_value(draw, x=details_x, y=detail_y, key="Frame", value=candidate.target_node.frame_name)
    if evidence.get("contrast_ratio") is not None:
        detail_y = _draw_key_value(
            draw,
            x=details_x,
            y=detail_y,
            key="Contrast",
            value=f"{evidence.get('contrast_ratio')} / required {evidence.get('required_ratio')}",
        )
    if evidence.get("text_sample") is not None:
        detail_y = _draw_key_value(draw, x=details_x, y=detail_y, key="Text", value=evidence.get("text_sample"))

    foreground_rgb = _evidence_rgb(evidence.get("resolved_foreground_rgb") or evidence.get("foreground_rgba"))
    background_rgb = _evidence_rgb(evidence.get("resolved_background_rgb"))
    if foreground_rgb or background_rgb:
        swatch_y = min(detail_y + 4, image_height - 126)
        _draw_swatch(draw, x=details_x, y=swatch_y, label="Text color", color=foreground_rgb)
        _draw_swatch(draw, x=details_x + 178, y=swatch_y, label="Background", color=background_rgb)

    draw.rectangle(
        [32, image_height - 58, image_width - 32, image_height - 24],
        fill=(255, 251, 235),
        outline=(251, 191, 36),
    )
    draw.text(
        (44, image_height - 47),
        "Note: this is a focused fallback preview, not a pixel-perfect Figma render. The red box uses Figma coordinates.",
        fill=(120, 53, 15),
    )

    image_path = output_dir / (
        f"{_sanitize_id(candidate.issue.id)}__node-"
        f"{_sanitize_id(candidate.target_node.id)}__locator.png"
    )
    image.convert("RGB").save(image_path)

    artifact = ScreenshotArtifact(
        type="node_preview_locator",
        image_path=str(image_path),
        target_node_id=candidate.target_node.id,
        render_node_id=candidate.render_node.id,
        rectangle_px=global_rectangle,
        image_width=image_width,
        image_height=image_height,
        figma_target_bounding_box=candidate.target_box,
        figma_render_bounding_box=candidate.render_box,
        coordinate_strategy="Focused fallback preview generated from cached Figma geometry, fills, text, and absolute coordinates",
        coordinate_source=candidate.coordinate_source,
        accuracy="focused_node_preview_not_rendered_screenshot",
        notes=[
            "Figma's image render endpoint was unavailable, so this is a focused node preview, not the rendered design.",
            "The red rectangle uses the same Figma coordinate mapping as rendered annotations.",
            warning,
        ],
    )
    candidate.issue.visual_evidence.append(artifact)


def annotate_issue_screenshots(
    *,
    normalized_file: NormalizedFigmaFile,
    audit_result: IssueContainer,
    output_dir: Path = ANNOTATIONS_OUTPUT_DIR,
    client: FigmaClient | None = None,
    image_scale: float = ANNOTATION_IMAGE_SCALE,
    max_images: int | None = ANNOTATION_MAX_IMAGES,
    workers: int | None = None,
    render_scope: str = "context",
    allow_preview_fallback: bool = True,
    log: Callable[[str], None] | None = None,
) -> AuditResult:
    """
    Render issue context screenshots and attach red-rectangle artifacts.

    Each generated image is linked back to its issue through
    issue.visual_evidence[].image_path and the artifact records the exact pixel
    rectangle used for the overlay.
    """
    if not audit_result.issues:
        return audit_result

    _require_pillow()
    if render_scope not in {"context", "page"}:
        raise ValueError("render_scope must be 'context' or 'page'.")

    nodes_by_id = _node_lookup(normalized_file)
    child_nodes_by_parent = _children_by_parent(normalized_file)
    figma_client = client or FigmaClient()
    output_dir.mkdir(parents=True, exist_ok=True)
    render_attempt_count = 0
    candidates: list[AnnotationCandidate] = []

    for issue in audit_result.issues:
        if max_images is not None and render_attempt_count >= max_images:
            issue.evidence.setdefault(
                "annotation_warning",
                f"Annotation skipped because max_images={max_images} render attempts was reached.",
            )
            continue

        node_id = issue.location.node_id
        if not node_id:
            continue

        target_node = nodes_by_id.get(node_id)
        if target_node is None:
            issue.evidence.setdefault("annotation_warning", "Issue node_id was not found in normalized nodes.")
            continue

        target_box, coordinate_source = _annotation_box(target_node)
        if target_box is None:
            issue.evidence.setdefault("annotation_warning", "Issue node has no usable bounding box.")
            continue

        render_node = choose_render_node(target_node, nodes_by_id, scope=render_scope)
        render_box = _required_bbox(render_node)
        if render_box is None:
            issue.evidence.setdefault("annotation_warning", "Render node has no usable absoluteBoundingBox.")
            continue

        render_attempt_count += 1
        candidates.append(
            AnnotationCandidate(
                issue=issue,
                target_node=target_node,
                target_box=target_box,
                coordinate_source=coordinate_source,
                render_node=render_node,
                render_box=render_box,
            )
        )

    if not candidates:
        return audit_result

    image_format = "png"
    render_node_ids = sorted({candidate.render_node.id for candidate in candidates})
    render_cache_paths: dict[str, Path] = {}
    render_downloads: dict[str, bytes] = {}

    for render_node_id in render_node_ids:
        cache_path = _render_cache_path(
            output_dir=output_dir,
            file_key=normalized_file.file_key,
            file_version=normalized_file.version,
            render_node_id=render_node_id,
            image_format=image_format,
            image_scale=image_scale,
        )
        if cache_path is None:
            continue
        render_cache_paths[render_node_id] = cache_path
        cached_bytes = _read_render_cache(cache_path, log)
        if cached_bytes:
            render_downloads[render_node_id] = cached_bytes

    missing_render_node_ids = [
        render_node_id
        for render_node_id in render_node_ids
        if render_node_id not in render_downloads
    ]
    image_urls: dict[str, str] = {}
    image_request_failures: dict[str, str] = {}

    if log is not None:
        cached_count = len(render_downloads)
        if cached_count:
            log(f"Using {cached_count} cached rendered image(s).")

    figma_client = client
    if missing_render_node_ids:
        figma_client = figma_client or FigmaClient()
        if log is not None:
            log(
                f"Requesting {len(missing_render_node_ids)} Figma render URL(s) "
                f"for {len(candidates)} annotation candidate(s)..."
            )
        try:
            image_urls = figma_client.get_image_urls(
                normalized_file.file_key,
                missing_render_node_ids,
                image_format=image_format,
                scale=image_scale,
            )
        except Exception as exc:
            if not allow_preview_fallback:
                raise RealScreenshotRequiredError(
                    "Could not request real Figma screenshots. "
                    "Figma returned an error for the Images API; wait for the rate limit to clear "
                    "or use preview fallback."
                ) from exc
            for render_node_id in missing_render_node_ids:
                image_request_failures[render_node_id] = (
                    f"Could not request rendered image for {render_node_id}: {exc}"
                )

    download_targets = {
        render_node_id: image_urls[render_node_id]
        for render_node_id in missing_render_node_ids
        if image_urls.get(render_node_id)
    }

    worker_count = max(1, min(workers or ANNOTATION_WORKERS, len(download_targets) or 1))
    if log is not None and download_targets:
        log(f"Downloading {len(download_targets)} rendered image(s) with {worker_count} worker(s)...")

    if worker_count == 1:
        for index, (render_node_id, image_url) in enumerate(download_targets.items(), start=1):
            try:
                if figma_client is None:
                    raise RuntimeError("Figma client is unavailable for image download.")
                image_bytes = figma_client.download_binary(image_url)
                render_downloads[render_node_id] = image_bytes
                cache_path = render_cache_paths.get(render_node_id)
                if cache_path is not None:
                    _write_render_cache(cache_path, image_bytes, log)
            except Exception as exc:
                for candidate in candidates:
                    if candidate.render_node.id == render_node_id:
                        candidate.issue.evidence.setdefault(
                            "annotation_warning",
                            f"Could not download rendered image for {render_node_id}: {exc}",
                        )
            if log is not None:
                log(f"Image downloads {progress_bar(index, len(download_targets))}")
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            if figma_client is None:
                raise RuntimeError("Figma client is unavailable for image download.")
            futures = {
                executor.submit(figma_client.download_binary, image_url): render_node_id
                for render_node_id, image_url in download_targets.items()
            }
            completed_count = 0
            for future in as_completed(futures):
                completed_count += 1
                render_node_id = futures[future]
                try:
                    image_bytes = future.result()
                    render_downloads[render_node_id] = image_bytes
                    cache_path = render_cache_paths.get(render_node_id)
                    if cache_path is not None:
                        _write_render_cache(cache_path, image_bytes, log)
                except Exception as exc:
                    for candidate in candidates:
                        if candidate.render_node.id == render_node_id:
                            candidate.issue.evidence.setdefault(
                                "annotation_warning",
                                f"Could not download rendered image for {render_node_id}: {exc}",
                            )
                if log is not None:
                    log(f"Image downloads {progress_bar(completed_count, len(download_targets))}")

    image_cache: dict[str, Image.Image] = {}

    for index, candidate in enumerate(candidates, start=1):
        issue = candidate.issue
        render_node = candidate.render_node

        try:
            if render_node.id in image_cache:
                image = image_cache[render_node.id]
            else:
                image_bytes = render_downloads.get(render_node.id)
                if image_bytes is None:
                    warning = image_request_failures.get(render_node.id)
                    if warning is None and not image_urls.get(render_node.id):
                        warning = f"Figma did not return an image URL for {render_node.id}."
                    if warning is None:
                        warning = f"Could not download rendered image for {render_node.id}."
                    if not allow_preview_fallback:
                        raise RealScreenshotRequiredError(warning)
                    issue.evidence.setdefault("annotation_warning", warning)
                    _draw_geometry_locator_image(
                        candidate=candidate,
                        warning=warning,
                        output_dir=output_dir,
                        children_by_parent=child_nodes_by_parent,
                    )
                    continue
                image = Image.open(BytesIO(image_bytes))
                image.load()
                image_cache[render_node.id] = image.copy()
        except Exception as exc:
            warning = f"Could not download or open rendered image for {render_node.id}: {exc}"
            if not allow_preview_fallback:
                raise RealScreenshotRequiredError(warning) from exc
            issue.evidence.setdefault(
                "annotation_warning",
                warning,
            )
            _draw_geometry_locator_image(
                candidate=candidate,
                warning=warning,
                output_dir=output_dir,
                children_by_parent=child_nodes_by_parent,
            )
            continue

        rectangle = calculate_rectangle_pixels(
            target_box=candidate.target_box,
            render_box=candidate.render_box,
            image_width=image.width,
            image_height=image.height,
        )
        annotated = _draw_precise_rectangle(image, rectangle)

        image_path = output_dir / f"{_sanitize_id(issue.id)}__node-{_sanitize_id(candidate.target_node.id)}.png"
        annotated.save(image_path)

        artifact = ScreenshotArtifact(
            image_path=str(image_path),
            target_node_id=candidate.target_node.id,
            render_node_id=render_node.id,
            rectangle_px=rectangle,
            image_width=image.width,
            image_height=image.height,
            figma_target_bounding_box=candidate.target_box,
            figma_render_bounding_box=candidate.render_box,
            coordinate_strategy="Figma absolute coordinates mapped to rendered context image pixels",
            coordinate_source=candidate.coordinate_source,
            accuracy="high_static_geometry",
            notes=[
                f"Rectangle is computed from Figma {candidate.coordinate_source} coordinates.",
                "Accuracy depends on Figma render bounds matching normalized geometry.",
                "Runtime-only states, CSS transforms, and implementation differences are outside static Figma evidence.",
            ],
        )
        issue.visual_evidence.append(artifact)

        if log is not None:
            log(f"Annotated issue {issue.id}: {image_path}")
            log(f"Annotations {progress_bar(index, len(candidates))}")

    return audit_result
