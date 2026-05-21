from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None  # type: ignore[assignment]

from figma_audit.evidence_quality import attach_evidence_quality
from figma_audit.ingestion.url_parser import parse_figma_url
from figma_audit.models.detection import DetectionResult
from figma_audit.models.normalized_models import NormalizedFigmaFile
from figma_audit.reports import _target_rect_and_design_bounds_on_real_screenshot
from figma_audit.utils.io import load_json, save_json


CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]
DETECTOR_PRIORITY = {
    "low_text_contrast": 0,
    "small_touch_target": 1,
    "crowded_touch_target": 2,
    "small_text_readability": 3,
    "icon_only_unlabeled_control": 4,
    "destructive_action_without_recovery": 5,
    "component_style_outlier": 6,
    "generic_navigation_label": 7,
    "placeholder_or_generic_copy": 8,
    "flat_visual_hierarchy": 9,
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
ROOT_SCREEN_TYPES = {"FRAME", "INSTANCE", "SECTION"}


def _chrome_executable() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Chrome or Edge was not found on this machine.")


def _chrome_executables() -> list[Path]:
    return [candidate for candidate in CHROME_CANDIDATES if candidate.exists()]


def _command_error_summary(completed: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if isinstance(part, str) and part.strip()
    )
    if len(output) > 1200:
        output = output[-1200:]
    return output or "no browser output"


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _valid_box(node: dict[str, object]) -> bool:
    box = node.get("absolute_bounding_box") or node.get("absolute_render_bounds")
    if not isinstance(box, dict):
        return False
    return all(isinstance(box.get(key), (int, float)) and float(box[key]) > 0 for key in ("width", "height"))


def _node_box(node: dict[str, object], *keys: str) -> dict[str, float] | None:
    if not isinstance(node, dict):
        return None
    for key in keys:
        box = node.get(key)
        if not isinstance(box, dict):
            continue
        values: dict[str, float] = {}
        for field in ("x", "y", "width", "height"):
            value = box.get(field)
            if not isinstance(value, (int, float)):
                break
            values[field] = float(value)
        else:
            if values["width"] > 0 and values["height"] > 0:
                return values
    return None


def _node_and_ancestors_visible(
    node: dict[str, object] | None,
    nodes_by_id: dict[str, dict[str, object]],
) -> bool:
    current = node
    visited: set[str] = set()
    while isinstance(current, dict):
        if current.get("visible") is False:
            return False
        current_id = current.get("id")
        if isinstance(current_id, str):
            if current_id in visited:
                return False
            visited.add(current_id)
        parent_id = current.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            break
        current = nodes_by_id.get(parent_id)
    return node is not None


def _is_root_screen_node(
    node: dict[str, object],
    nodes_by_id: dict[str, dict[str, object]],
) -> bool:
    if node.get("type") not in ROOT_SCREEN_TYPES:
        return False
    if not _node_and_ancestors_visible(node, nodes_by_id):
        return False
    parent_id = node.get("parent_id")
    if isinstance(parent_id, str) and parent_id:
        parent = nodes_by_id.get(parent_id)
        if not isinstance(parent, dict) or parent.get("type") != "CANVAS":
            return False
    box = _node_box(node, "absolute_bounding_box", "absolute_render_bounds")
    if box is None:
        return False
    width = box["width"]
    height = box["height"]
    area = width * height
    return width >= 280 and height >= 500 and area >= 140000


def _context_sort_key(node: dict[str, object] | None) -> tuple[float, float, str]:
    if not isinstance(node, dict):
        return (float("inf"), float("inf"), "")
    box = _node_box(node, "absolute_bounding_box", "absolute_render_bounds") or {}
    return (
        float(box.get("y") or 0),
        float(box.get("x") or 0),
        str(node.get("id") or ""),
    )


def _root_screen_context_node_ids(nodes_by_id: dict[str, dict[str, object]]) -> list[str]:
    candidates = [
        str(node.get("id"))
        for node in nodes_by_id.values()
        if node.get("id") and _is_root_screen_node(node, nodes_by_id)
    ]
    return sorted(
        candidates,
        key=lambda node_id: _context_sort_key(nodes_by_id.get(node_id)),
    )


def _box_intersection_area(first: dict[str, float], second: dict[str, float]) -> float:
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    return max(0.0, right - left) * max(0.0, bottom - top)


def _visible_ratio_in_render(target_box: dict[str, float], render_box: dict[str, float]) -> float:
    target_area = target_box["width"] * target_box["height"]
    if target_area <= 0:
        return 0.0
    return _box_intersection_area(target_box, render_box) / target_area


def _rectangle_pixels(
    *,
    target_box: dict[str, float],
    render_box: dict[str, float],
    image_width: int,
    image_height: int,
) -> dict[str, int] | None:
    if _visible_ratio_in_render(target_box, render_box) < 0.98:
        return None

    scale_x = image_width / render_box["width"]
    scale_y = image_height / render_box["height"]
    x = round((target_box["x"] - render_box["x"]) * scale_x)
    y = round((target_box["y"] - render_box["y"]) * scale_y)
    width = round(target_box["width"] * scale_x)
    height = round(target_box["height"] * scale_y)
    if width <= 0 or height <= 0:
        return None
    if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _sampled_luminance_values(image: "Image.Image") -> list[float]:
    width, height = image.size
    if width <= 0 or height <= 0:
        return []
    step = max(1, min(width, height) // 36)
    values: list[float] = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            red, green, blue = image.getpixel((x, y))[:3]
            values.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    return values


def _sampled_luminance_range(image: "Image.Image") -> float:
    values = _sampled_luminance_values(image)
    if not values:
        return 0.0
    return max(values) - min(values)


def _relative_luminance_from_255(value: float) -> float:
    channel = max(0.0, min(value / 255.0, 1.0))
    if channel <= 0.03928:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _contrast_ratio_from_luminance_values(values: list[float]) -> float:
    if not values:
        return 1.0
    light = _relative_luminance_from_255(max(values))
    dark = _relative_luminance_from_255(min(values))
    lighter = max(light, dark)
    darker = min(light, dark)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def _solid_fill_rgb_from_node(node: dict[str, object] | None) -> tuple[int, int, int] | None:
    if not isinstance(node, dict):
        return None
    fills = node.get("fills")
    if not isinstance(fills, list):
        return None
    solid_fills = [
        fill
        for fill in fills
        if isinstance(fill, dict)
        and fill.get("visible") is not False
        and fill.get("type") == "SOLID"
        and isinstance(fill.get("color"), dict)
    ]
    if not solid_fills:
        return None

    red = green = blue = 255.0
    for fill in solid_fills:
        color = fill["color"]
        assert isinstance(color, dict)
        opacity = float(fill.get("opacity", 1.0))
        alpha = max(0.0, min(float(color.get("a", 1.0)) * opacity, 1.0))
        fill_red = float(color.get("r", 0.0)) * 255.0
        fill_green = float(color.get("g", 0.0)) * 255.0
        fill_blue = float(color.get("b", 0.0)) * 255.0
        red = (fill_red * alpha) + (red * (1 - alpha))
        green = (fill_green * alpha) + (green * (1 - alpha))
        blue = (fill_blue * alpha) + (blue * (1 - alpha))

    return round(red), round(green), round(blue)


def _color_match_ratio(image: "Image.Image", expected_rgb: tuple[int, int, int]) -> float:
    width, height = image.size
    if width <= 0 or height <= 0:
        return 0.0
    step = max(1, min(width, height) // 30)
    matches = 0
    total = 0
    for y in range(0, height, step):
        for x in range(0, width, step):
            pixel = image.getpixel((x, y))[:3]
            distance = sum(abs(int(pixel[index]) - expected_rgb[index]) for index in range(3))
            if distance <= 96:
                matches += 1
            total += 1
    if total == 0:
        return 0.0
    return matches / total


def _saturated_pixel_ratio(image: "Image.Image") -> float:
    width, height = image.size
    if width <= 0 or height <= 0:
        return 0.0
    step = max(1, min(width, height) // 30)
    saturated = 0
    total = 0
    for y in range(0, height, step):
        for x in range(0, width, step):
            pixel = image.getpixel((x, y))[:3]
            if max(pixel) - min(pixel) > 40:
                saturated += 1
            total += 1
    if total == 0:
        return 0.0
    return saturated / total


def _crop_target_region(
    image: "Image.Image",
    target_rect: tuple[float, float, float, float],
    design_bounds: tuple[float, float, float, float],
    *,
    pad: float = 2.0,
) -> "Image.Image":
    x, y, width, height = target_rect
    design_x, design_y, design_w, design_h = design_bounds
    left = max(design_x, x - pad)
    top = max(design_y, y - pad)
    right = min(design_x + design_w, x + width + pad)
    bottom = min(design_y + design_h, y + height + pad)
    if right <= left or bottom <= top:
        return image.crop((0, 0, 0, 0))
    return image.crop((round(left), round(top), round(right), round(bottom)))


def _validate_detector_target(
    *,
    issue: dict[str, object],
    target_node: dict[str, object] | None,
    target_crop: "Image.Image",
) -> tuple[bool, dict[str, object]]:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    detector_id = str(evidence.get("detector_id") or "")

    luminance_values = _sampled_luminance_values(target_crop)
    luminance_range = (max(luminance_values) - min(luminance_values)) if luminance_values else 0.0
    rendered_contrast = _contrast_ratio_from_luminance_values(luminance_values)
    validation: dict[str, object] = {
        "target_luminance_range": round(luminance_range, 2),
        "rendered_contrast_estimate": rendered_contrast,
    }

    if luminance_range < 5.0:
        validation["rejected_reason"] = "target_not_visibly_rendered"
        return False, validation

    expected_rgb = _solid_fill_rgb_from_node(target_node)
    target_type = str((target_node or {}).get("type") or "")
    match_ratio: float | None = None
    if expected_rgb is not None:
        match_ratio = _color_match_ratio(target_crop, expected_rgb)
        saturated_ratio = _saturated_pixel_ratio(target_crop)
        validation["expected_fill_rgb"] = list(expected_rgb)
        validation["expected_fill_match_ratio"] = round(match_ratio, 4)
        validation["saturated_pixel_ratio"] = round(saturated_ratio, 4)
        expected_is_neutral = max(expected_rgb) - min(expected_rgb) < 24
        if target_type == "TEXT" and match_ratio > 0.52:
            validation["rejected_reason"] = "rendered_text_foreground_fills_target"
            return False, validation
        if target_type == "TEXT" and match_ratio < 0.01:
            validation["rejected_reason"] = "rendered_text_color_not_found_at_target"
            return False, validation
        if target_type == "TEXT" and expected_is_neutral and saturated_ratio > 0.18:
            validation["rejected_reason"] = "rendered_text_target_contains_unexpected_color"
            return False, validation

    if detector_id == "low_text_contrast":
        required = evidence.get("required_ratio")
        if isinstance(required, (int, float)) and rendered_contrast >= float(required) * 0.92:
            validation["rejected_reason"] = "rendered_target_contrast_is_not_low"
            return False, validation
        return True, validation

    if expected_rgb is not None and match_ratio is not None:
        if detector_id == "component_style_outlier" and match_ratio < 0.35:
            validation["rejected_reason"] = "rendered_target_does_not_match_outlier_fill"
            return False, validation

    return True, validation


def _artifact_has_client_visible_detail(
    *,
    screenshot_path: Path,
    artifact: dict[str, object],
    issue: dict[str, object],
    target_node: dict[str, object] | None,
) -> tuple[bool, dict[str, int] | None, tuple[int, int] | None, dict[str, object]]:
    if Image is None:
        return True, None, None, {"status": "skipped_pillow_unavailable"}

    try:
        image = Image.open(screenshot_path).convert("RGB")
    except OSError:
        return False, None, None, {"rejected_reason": "screenshot_unreadable"}

    full_image_range = _sampled_luminance_range(image)
    target_result = _target_rect_and_design_bounds_on_real_screenshot(
        image=image,
        artifact=artifact,
    )
    if target_result is None:
        return False, None, image.size, {"rejected_reason": "target_could_not_be_mapped"}

    target_rect, design_bounds = target_result
    x, y, width, height = target_rect
    design_x, design_y, design_w, design_h = design_bounds
    rectangle_px = {
        "x": round(x),
        "y": round(y),
        "width": max(1, round(width)),
        "height": max(1, round(height)),
    }

    # Unit tests and some solid-color exports can be intentionally flat. In
    # real browser captures, reject targets that land on a blank part of the
    # prototype canvas instead of a visible piece of the UI.
    if full_image_range <= 4:
        return True, rectangle_px, image.size, {"status": "accepted_flat_test_image"}

    pad_x = max(24.0, min(96.0, width * 1.25))
    pad_y = max(20.0, min(88.0, height * 1.5))
    left = max(design_x, x - pad_x)
    top = max(design_y, y - pad_y)
    right = min(design_x + design_w, x + width + pad_x)
    bottom = min(design_y + design_h, y + height + pad_y)
    if right <= left or bottom <= top:
        return False, rectangle_px, image.size, {"rejected_reason": "empty_target_region"}

    crop = image.crop((round(left), round(top), round(right), round(bottom)))
    if _sampled_luminance_range(crop) <= 10:
        return False, rectangle_px, image.size, {"rejected_reason": "nearby_region_has_no_visible_detail"}

    target_crop = _crop_target_region(image, target_rect, design_bounds)
    accepted, validation = _validate_detector_target(
        issue=issue,
        target_node=target_node,
        target_crop=target_crop,
    )
    return accepted, rectangle_px, image.size, validation


def _issue_has_real_visible_evidence(issue: dict[str, object]) -> bool:
    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    return any(
        isinstance(artifact, dict)
        and artifact.get("type") == "real_page_geometry_screenshot"
        for artifact in visual_evidence
    )


def _real_screenshot_artifact_count(issue: dict[str, object]) -> int:
    visual_evidence = (
        issue.get("visual_evidence")
        if isinstance(issue.get("visual_evidence"), list)
        else []
    )
    return sum(
        1
        for artifact in visual_evidence
        if isinstance(artifact, dict)
        and artifact.get("type") == "real_page_geometry_screenshot"
    )


def _issue_should_be_removed_from_client_audit(issue: dict[str, object]) -> bool:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    return evidence.get("client_visibility") in {
        "hidden_in_figma_tree",
        "outside_client_view",
        "target_not_visibly_rendered",
        "rendered_text_foreground_fills_target",
        "rendered_text_color_not_found_at_target",
        "rendered_text_target_contains_unexpected_color",
        "rendered_target_does_not_match_outlier_fill",
        "rendered_target_contrast_is_not_low",
        "target_could_not_be_mapped",
        "nearby_region_has_no_visible_detail",
    }


def _confidence_rank(value: object) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(value or "").lower(), 0)


def _issue_priority_sort_key(issue: dict[str, object]) -> tuple[int, int, int, int, float, str]:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    visual_evidence = issue.get("visual_evidence") if isinstance(issue.get("visual_evidence"), list) else []
    has_real_screenshot = any(
        isinstance(artifact, dict)
        and artifact.get("type") == "real_page_geometry_screenshot"
        for artifact in visual_evidence
    )
    impact_value = 0.0
    for key in ("contrast_gap", "distance", "family_sample_size", "text_count"):
        value = evidence.get(key)
        if isinstance(value, (int, float)):
            impact_value = max(impact_value, float(value))
    return (
        SEVERITY_ORDER.get(str(issue.get("severity") or "low"), 3),
        -_confidence_rank(evidence.get("confidence")),
        0 if has_real_screenshot else 1,
        DETECTOR_PRIORITY.get(str(evidence.get("detector_id") or ""), 99),
        -impact_value,
        str(issue.get("id") or ""),
    )


def _refresh_detection_payload(detections: dict[str, object]) -> None:
    issues = detections.get("draft_issues", [])
    if not isinstance(issues, list):
        issues = []
        detections["draft_issues"] = issues

    issues_by_criterion: dict[str, list[dict[str, object]]] = {}
    issues.sort(key=lambda issue: _issue_priority_sort_key(issue) if isinstance(issue, dict) else (99, 99, 99, 99, 0.0, ""))
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        criterion = issue.get("criterion") or issue.get("axis")
        if isinstance(criterion, str) and criterion:
            issues_by_criterion.setdefault(criterion, []).append(issue)

    statuses = detections.get("criterion_status", [])
    if isinstance(statuses, list):
        for status in statuses:
            if not isinstance(status, dict):
                continue
            criterion_id = status.get("criterion_id")
            criterion_issues = issues_by_criterion.get(str(criterion_id), [])
            detector_ids = sorted(
                {
                    str(evidence.get("detector_id"))
                    for issue in criterion_issues
                    if isinstance(issue, dict)
                    for evidence in [issue.get("evidence")]
                    if isinstance(evidence, dict) and evidence.get("detector_id")
                }
            )
            best_confidence = None
            for issue in criterion_issues:
                evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
                confidence = evidence.get("confidence")
                if _confidence_rank(confidence) > _confidence_rank(best_confidence):
                    best_confidence = confidence
            status["exists"] = bool(criterion_issues)
            status["issue_count"] = len(criterion_issues)
            status["confidence"] = best_confidence
            status["detector_ids"] = detector_ids

    summary = detections.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        detections["summary"] = summary
    summary["draft_issue_count"] = len([issue for issue in issues if isinstance(issue, dict)])
    screenshot_count = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        visual_evidence = issue.get("visual_evidence")
        if not isinstance(visual_evidence, list):
            continue
        screenshot_count += _real_screenshot_artifact_count(issue)
    summary["screenshot_count"] = screenshot_count
    if isinstance(statuses, list):
        summary["criteria_total"] = len([status for status in statuses if isinstance(status, dict)])
        summary["criteria_with_detected_problems"] = sum(
            1
            for status in statuses
            if isinstance(status, dict) and status.get("exists") is True
        )


def _save_analysis_summary(detections_path: Path, detections: dict[str, object]) -> None:
    summary = detections.get("summary")
    if isinstance(summary, dict):
        save_json(detections_path.parent / "analysis_summary.json", summary)


def _top_level_context_node_id(
    *,
    node_id: str,
    nodes_by_id: dict[str, dict[str, object]],
) -> str | None:
    node = nodes_by_id.get(node_id)
    if node is None:
        return None

    highest = node if _valid_box(node) else None
    current = node
    while True:
        parent_id = current.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            break
        parent = nodes_by_id.get(parent_id)
        if parent is None:
            break
        if parent.get("type") == "CANVAS":
            break
        if _valid_box(parent):
            highest = parent
        current = parent

    return str(highest["id"]) if highest else None


def _phone_context_box(
    *,
    target_node: dict[str, object] | None,
    context_node: dict[str, object],
    nodes_by_id: dict[str, dict[str, object]],
) -> dict[str, float] | None:
    target_box = _node_box(target_node, "absolute_render_bounds", "absolute_bounding_box")
    if target_box is None:
        return None

    candidates: list[tuple[float, float, dict[str, float]]] = []
    current = target_node
    visited: set[str] = set()
    while isinstance(current, dict):
        current_id = str(current.get("id") or "")
        if current_id in visited:
            break
        visited.add(current_id)
        box = _node_box(current, "absolute_render_bounds", "absolute_bounding_box")
        if box is not None and _visible_ratio_in_render(target_box, box) >= 0.95:
            width = box["width"]
            height = box["height"]
            area = width * height
            # iPhone UI-kit sections are commonly 393pt wide. Pick the nearest
            # useful ancestor instead of the whole Figma cover.
            if 280 <= width <= 460 and 120 <= height <= 950:
                width_score = abs(width - 393.0)
                candidates.append((width_score, -area, box))
        parent_id = current.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            break
        current = nodes_by_id.get(parent_id)

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    return None


def _prototype_url_for_node(source_url: str, node_id: str) -> str:
    parsed_url = urlparse(source_url)
    parsed_figma = parse_figma_url(source_url)
    path_parts = [part for part in parsed_url.path.split("/") if part]
    slug = path_parts[2] if len(path_parts) >= 3 else "file"
    query = parse_qs(parsed_url.query)
    params = {"node-id": node_id.replace(":", "-")}
    if query.get("t"):
        params["t"] = query["t"][0]
    return urlunparse(
        (
            parsed_url.scheme or "https",
            parsed_url.netloc or "www.figma.com",
            f"/proto/{parsed_figma['file_key']}/{slug}",
            "",
            urlencode(params),
            "",
        )
    )


def _capture_url(
    *,
    url: str,
    output_path: Path,
    width: int,
    height: int,
    timeout_seconds: int,
) -> None:
    browsers = _chrome_executables()
    if not browsers:
        raise FileNotFoundError("Chrome or Edge was not found on this machine.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    errors: list[str] = []
    for browser in browsers:
        for headless_arg in ("--headless=new", "--headless"):
            profile_dir = (
                output_path.parent
                / "_chrome_profiles"
                / f"{_safe_name(output_path.stem)}-{uuid.uuid4().hex[:8]}"
            )
            profile_dir.mkdir(parents=True, exist_ok=True)
            command = [
                str(browser),
                headless_arg,
                "--disable-gpu",
                "--no-first-run",
                "--disable-extensions",
                "--disable-crash-reporter",
                "--disable-breakpad",
                "--disable-crashpad",
                "--disable-dev-shm-usage",
                "--disable-features=Crashpad",
                "--no-sandbox",
                "--hide-scrollbars",
                "--run-all-compositor-stages-before-draw",
                f"--user-data-dir={profile_dir}",
                f"--window-size={width},{height}",
                "--virtual-time-budget=15000",
                f"--screenshot={output_path}",
                url,
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    timeout=timeout_seconds,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired as exc:
                errors.append(f"{browser.name} {headless_arg}: timed out after {timeout_seconds}s")
                continue

            if output_path.exists() and output_path.stat().st_size > 0:
                return

            errors.append(
                f"{browser.name} {headless_arg}: exit {completed.returncode}; "
                f"{_command_error_summary(completed)}"
            )

    raise RuntimeError(
        "Browser could not capture a real Figma screenshot. "
        "Tried Chrome/Edge in headless mode. Last errors: "
        + " | ".join(errors[-4:])
    )


def capture_real_page_screenshots(
    *,
    source_url: str,
    detections_path: Path,
    extraction_path: Path,
    output_dir: Path,
    width: int = 1440,
    height: int = 5200,
    timeout_seconds: int = 60,
    log: object = print,
) -> list[Path]:
    detections = load_json(detections_path)
    extraction = load_json(extraction_path)
    normalized_file = extraction.get("normalized_file") if isinstance(extraction, dict) else {}
    nodes = normalized_file.get("nodes", []) if isinstance(normalized_file, dict) else []
    nodes_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }

    issues = detections.get("draft_issues", [])
    if not isinstance(issues, list):
        return []

    issue_contexts: dict[str, str] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        node_id = location.get("node_id")
        if not isinstance(node_id, str):
            continue
        context_node_id = _top_level_context_node_id(node_id=node_id, nodes_by_id=nodes_by_id)
        if context_node_id:
            issue_contexts[str(issue.get("id"))] = context_node_id

    root_screen_contexts = _root_screen_context_node_ids(nodes_by_id)
    unique_contexts = sorted(
        set(root_screen_contexts) | set(issue_contexts.values()),
        key=lambda node_id: _context_sort_key(nodes_by_id.get(node_id)),
    )
    screenshots_by_context: dict[str, Path] = {}
    for context_node_id in unique_contexts:
        context_node = nodes_by_id.get(context_node_id, {})
        context_name = str(context_node.get("name") or context_node_id)
        screenshot_url = _prototype_url_for_node(source_url, context_node_id)
        screenshot_path = output_dir / f"real_figma_page__{_safe_name(context_node_id)}__{_safe_name(context_name)}.png"
        if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
            if callable(log):
                log(f"Reusing real Figma page screenshot for {context_name} ({context_node_id}).")
        else:
            if callable(log):
                log(f"Capturing real Figma page screenshot for {context_name} ({context_node_id})...")
            _capture_url(
                url=screenshot_url,
                output_path=screenshot_path,
                width=width,
                height=height,
                timeout_seconds=timeout_seconds,
            )
        screenshots_by_context[context_node_id] = screenshot_path

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("id"))
        context_node_id = issue_contexts.get(issue_id)
        screenshot_path = screenshots_by_context.get(context_node_id or "")
        if not screenshot_path:
            continue
        existing = issue.get("visual_evidence") if isinstance(issue.get("visual_evidence"), list) else []
        issue["visual_evidence"] = [
            item
            for item in existing
            if not (
                isinstance(item, dict)
                and item.get("type") == "real_page_geometry_screenshot"
            )
        ]
        context_node = nodes_by_id.get(context_node_id or "", {})
        evidence = issue.setdefault("evidence", {})
        if isinstance(evidence, dict):
            evidence.pop("real_page_screenshot_path", None)
            evidence.pop("real_page_screenshot_node_id", None)
            evidence.pop("real_page_screenshot_node_name", None)
            evidence.pop("real_page_screenshot_note", None)
            evidence.pop("client_view_validation", None)
            evidence["real_page_screenshot_path"] = str(screenshot_path)
            evidence["real_page_screenshot_node_id"] = context_node_id
            evidence["real_page_screenshot_node_name"] = context_node.get("name")
            evidence["real_page_screenshot_note"] = (
                "Real browser screenshot of the public Figma prototype page/section. "
                "Use this to inspect the actual design; the report circle is mapped from Figma geometry."
            )
        location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        node_id = location.get("node_id")
        target_node = nodes_by_id.get(str(node_id)) if isinstance(node_id, str) else None
        if not _node_and_ancestors_visible(target_node, nodes_by_id):
            evidence = issue.setdefault("evidence", {})
            if isinstance(evidence, dict):
                evidence["client_visibility"] = "hidden_in_figma_tree"
                evidence["client_visibility_note"] = (
                    "Removed from client-facing findings because the target node or one of its ancestors is hidden."
                )
            continue
        target_box = (
            _node_box(target_node, "absolute_render_bounds", "absolute_bounding_box")
            if target_node
            else None
        )
        render_box = _node_box(context_node, "absolute_bounding_box", "absolute_render_bounds")
        if isinstance(node_id, str) and target_box and render_box:
            phone_context_box = _phone_context_box(
                target_node=target_node,
                context_node=context_node,
                nodes_by_id=nodes_by_id,
            )
            target_fill_rgb = _solid_fill_rgb_from_node(target_node)
            rectangle_px = _rectangle_pixels(
                target_box=target_box,
                render_box=render_box,
                image_width=width,
                image_height=height,
            )
            if rectangle_px is None:
                evidence = issue.setdefault("evidence", {})
                if isinstance(evidence, dict):
                    evidence["client_visibility"] = "outside_client_view"
                    evidence["client_visibility_note"] = (
                        "Removed from client-facing findings because the target is not fully visible in the captured client-view screenshot."
                    )
                continue
            artifact = {
                "type": "real_page_geometry_screenshot",
                "image_path": str(screenshot_path),
                "target_node_id": node_id,
                "render_node_id": context_node_id,
                "rectangle_px": rectangle_px,
                "image_width": width,
                "image_height": height,
                "figma_target_bounding_box": target_box,
                "figma_render_bounding_box": render_box,
                "figma_phone_context_bounding_box": phone_context_box,
                "target_fill_rgb": list(target_fill_rgb) if target_fill_rgb else None,
                "coordinate_strategy": (
                    "Real browser screenshot with Figma geometry mapping and visible-pixel snapping for the rounded-rectangle callout"
                ),
                "coordinate_source": "absoluteRenderBounds"
                if target_node and target_node.get("absolute_render_bounds")
                else "absoluteBoundingBox",
                "accuracy": "real_page_screenshot_with_static_figma_geometry",
                "notes": [
                    "Screenshot captured from the public Figma prototype page.",
                    "Rounded rectangle placement uses Figma geometry mapped onto the captured page, then snaps to matching visible pixels when possible.",
                ],
            }
            has_visible_detail, real_rectangle_px, real_image_size, validation = _artifact_has_client_visible_detail(
                screenshot_path=screenshot_path,
                artifact=artifact,
                issue=issue,
                target_node=target_node,
            )
            artifact["client_view_validation"] = validation
            evidence = issue.setdefault("evidence", {})
            if isinstance(evidence, dict):
                evidence["client_view_validation"] = validation
            if real_image_size is not None:
                artifact["image_width"] = real_image_size[0]
                artifact["image_height"] = real_image_size[1]
            if real_rectangle_px is not None:
                artifact["rectangle_px"] = real_rectangle_px
            if not has_visible_detail:
                evidence = issue.setdefault("evidence", {})
                if isinstance(evidence, dict):
                    evidence["client_visibility"] = str(
                        validation.get("rejected_reason") or "no_visible_detail_in_client_view"
                    )
                    evidence["client_visibility_note"] = (
                        "Removed from client-facing findings because the mapped target did not match the detector condition in the client-view screenshot."
                    )
                continue
            issue["visual_evidence"] = [
                artifact,
                *[
                    item
                    for item in existing
                    if not (
                        isinstance(item, dict)
                        and item.get("type") == "real_page_geometry_screenshot"
                    )
                ],
            ]

    kept_issues = [
        issue
        for issue in issues
        if isinstance(issue, dict)
        and not _issue_should_be_removed_from_client_audit(issue)
        and _issue_has_real_visible_evidence(issue)
    ]
    removed_count = len([issue for issue in issues if isinstance(issue, dict)]) - len(kept_issues)
    if removed_count and callable(log):
        log(
            f"Removed {removed_count} draft issue(s) hidden in Figma or outside the captured client view."
        )
    detections["draft_issues"] = kept_issues
    _refresh_detection_payload(detections)

    try:
        normalized_model = NormalizedFigmaFile.model_validate(normalized_file)
        detection_model = DetectionResult.model_validate(detections)
        parsed_url = parse_figma_url(source_url)
        validation_quality = attach_evidence_quality(
            normalized_file=normalized_model,
            detection_result=detection_model,
            source_url=source_url,
            node_id=parsed_url.get("node_id"),
        )
        detections = detection_model.model_dump(mode="json")
        save_json(detections_path.parent / "evidence_quality.json", validation_quality)
    except Exception as exc:
        if callable(log):
            log(f"Warning: could not refresh evidence quality after real screenshots: {exc}")

    save_json(detections_path, detections)
    _save_analysis_summary(detections_path, detections)
    return list(screenshots_by_context.values())
