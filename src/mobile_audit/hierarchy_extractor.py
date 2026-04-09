from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any


BOUNDS_RE = re.compile(r"\[(?P<x1>\d+),(?P<y1>\d+)\]\[(?P<x2>\d+),(?P<y2>\d+)\]")


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def _parse_bounds(value: str) -> list[int]:
    match = BOUNDS_RE.fullmatch((value or "").strip())
    if not match:
        return [0, 0, 0, 0]
    return [
        int(match.group("x1")),
        int(match.group("y1")),
        int(match.group("x2")),
        int(match.group("y2")),
    ]


def _bounds_size(bounds: list[int]) -> tuple[int, int]:
    if len(bounds) != 4:
        return 0, 0
    return max(0, bounds[2] - bounds[0]), max(0, bounds[3] - bounds[1])


def _resource_hint(resource_id: str) -> str:
    cleaned = str(resource_id or "").strip()
    if not cleaned:
        return ""
    tail = cleaned.split("/")[-1].split(":")[-1]
    return tail.replace("_", " ").replace("-", " ").strip()


def _label_for_element(text: str, content_desc: str, resource_id: str, class_name: str) -> str:
    for candidate in (text, content_desc, _resource_hint(resource_id)):
        if str(candidate or "").strip():
            return str(candidate).strip()
    return class_name.rsplit(".", 1)[-1] if class_name else "Element"


def _meta_flags(elements: list[dict[str, Any]]) -> dict[str, bool]:
    clickable = [element for element in elements if element.get("clickable") and element.get("visible")]
    max_bottom = max((element["bounds"][3] for element in elements if len(element.get("bounds", [])) == 4), default=0)
    bottom_threshold = max_bottom * 0.72 if max_bottom else 0
    has_bottom_nav = sum(1 for element in clickable if element["bounds"][1] >= bottom_threshold) >= 2
    has_back_button = any(
        "back" in " ".join(
            [
                str(element.get("text") or ""),
                str(element.get("content_desc") or ""),
                str(element.get("resource_id") or ""),
            ]
        ).lower()
        for element in clickable
    )
    has_modal = any(
        token in str(element.get("class_name") or "").lower() or token in str(element.get("resource_id") or "").lower()
        for element in elements
        for token in ("dialog", "modal", "popup")
    )
    return {
        "has_bottom_nav": has_bottom_nav,
        "has_back_button": has_back_button,
        "has_modal": has_modal,
    }


def _visible_text(elements: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for element in elements:
        if not element.get("visible"):
            continue
        for candidate in (element.get("text"), element.get("content_desc")):
            value = str(candidate or "").strip()
            if not value or len(value) > 160:
                continue
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return ordered[:40]


def _screen_title_guess(elements: list[dict[str, Any]]) -> str:
    for element in elements:
        if not element.get("visible"):
            continue
        text = str(element.get("text") or "").strip()
        if 2 <= len(text) <= 80:
            return text
    return ""


def build_screen_fingerprint(
    package_name: str,
    activity_name: str,
    visible_text: list[str],
    elements: list[dict[str, Any]],
) -> str:
    class_signature = "|".join(
        f"{element.get('class_name')}:{element.get('resource_id')}:{element.get('bounds')}"
        for element in elements[:60]
    )
    payload = "\n".join(
        [
            package_name or "",
            activity_name or "",
            " | ".join(visible_text[:15]),
            class_signature,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def extract_hierarchy(xml_source: str) -> dict[str, Any]:
    root = ET.fromstring(xml_source)
    elements: list[dict[str, Any]] = []

    for index, node in enumerate(root.iter()):
        if node.tag != "node":
            continue

        attributes = dict(node.attrib)
        bounds = _parse_bounds(attributes.get("bounds", ""))
        width, height = _bounds_size(bounds)
        visible = width > 0 and height > 0 and attributes.get("visible-to-user", "true") != "false"

        text = str(attributes.get("text") or "").strip()
        content_desc = str(attributes.get("content-desc") or "").strip()
        resource_id = str(attributes.get("resource-id") or "").strip()
        class_name = str(attributes.get("class") or "").strip()

        element = {
            "element_id": f"el_{index:04d}",
            "text": text,
            "content_desc": content_desc,
            "class_name": class_name,
            "resource_id": resource_id,
            "package_name": str(attributes.get("package") or "").strip(),
            "bounds": bounds,
            "width": width,
            "height": height,
            "clickable": _as_bool(attributes.get("clickable")),
            "focusable": _as_bool(attributes.get("focusable")),
            "enabled": _as_bool(attributes.get("enabled", "true")),
            "checked": _as_bool(attributes.get("checked")),
            "selected": _as_bool(attributes.get("selected")),
            "scrollable": _as_bool(attributes.get("scrollable")),
            "long_clickable": _as_bool(attributes.get("long-clickable")),
            "visible": visible,
            "label": _label_for_element(text, content_desc, resource_id, class_name),
        }

        # Skip empty structural nodes that do not contribute visible evidence.
        if not any(
            [
                element["text"],
                element["content_desc"],
                element["resource_id"],
                element["class_name"],
                element["clickable"],
            ]
        ):
            continue

        elements.append(element)

    visible_text = _visible_text(elements)
    return {
        "elements": elements,
        "visible_text": visible_text,
        "screen_title_guess": _screen_title_guess(elements),
        "meta": _meta_flags(elements),
    }
