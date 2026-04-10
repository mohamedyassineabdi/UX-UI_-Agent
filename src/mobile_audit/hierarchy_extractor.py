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


def _title_hint(text: str, content_desc: str, resource_id: str, hint_text: str, class_name: str) -> str:
    for candidate in (text, content_desc, hint_text, _resource_hint(resource_id)):
        if str(candidate or "").strip():
            return str(candidate).strip()
    return class_name.rsplit(".", 1)[-1] if class_name else "Element"


def _normalize_text(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()


def _dedupe_key(element: dict[str, Any]) -> tuple[Any, ...]:
    return (
        element.get("class_name") or "",
        element.get("resource_id") or "",
        element.get("text") or "",
        element.get("content_desc") or "",
        tuple(element.get("bounds") or []),
    )


def _is_probably_visible(attributes: dict[str, Any], width: int, height: int) -> bool:
    displayed = attributes.get("displayed")
    visible_to_user = attributes.get("visible-to-user")
    if width <= 0 or height <= 0:
        return False
    if displayed is not None and not _as_bool(displayed):
        return False
    if visible_to_user is not None and not _as_bool(visible_to_user):
        return False
    return True


def _collect_candidate_strings(element: dict[str, Any]) -> list[str]:
    values = [
        element.get("text"),
        element.get("content_desc"),
        element.get("hint_text"),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in values:
        value = _normalize_text(candidate)
        if not value or len(value) > 160:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _visible_bounds_union(elements: list[dict[str, Any]]) -> list[int]:
    visible_elements = [
        element
        for element in elements
        if element.get("visible") and len(element.get("bounds") or []) == 4
    ]
    if not visible_elements:
        return [0, 0, 0, 0]
    return [
        min(element["bounds"][0] for element in visible_elements),
        min(element["bounds"][1] for element in visible_elements),
        max(element["bounds"][2] for element in visible_elements),
        max(element["bounds"][3] for element in visible_elements),
    ]


def _contains_long_form_text(visible_text: list[str]) -> bool:
    return any(len(str(value or "").strip()) >= 40 for value in visible_text)


def _meta_flags(elements: list[dict[str, Any]], visible_text: list[str], screen_width: int, screen_height: int) -> dict[str, bool]:
    clickable = [element for element in elements if element.get("clickable") and element.get("visible")]
    visible_elements = [element for element in elements if element.get("visible")]
    class_names = [str(element.get("class_name") or "").lower() for element in visible_elements]
    resource_ids = [str(element.get("resource_id") or "").lower() for element in visible_elements]
    text_values = [str(value or "").strip().lower() for value in visible_text if str(value or "").strip()]
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
    explicit_modal = any(
        token in class_name or token in resource_id
        for class_name, resource_id in zip(class_names, resource_ids)
        for token in ("dialog", "modal", "popup", "list_menu", "app_menu", "sheet")
    )
    union_bounds = _visible_bounds_union(elements)
    union_width, union_height = _bounds_size(union_bounds)
    compact_overlay = False
    if screen_width > 0 and screen_height > 0 and union_width > 0 and union_height > 0:
        width_ratio = union_width / screen_width
        height_ratio = union_height / screen_height
        compact_overlay = (
            width_ratio <= 0.45
            and height_ratio <= 0.32
            and len(visible_text) <= 8
            and len(clickable) <= 8
        )
    has_webview = any("webview" in class_name for class_name in class_names)
    has_address_bar = any("url_bar" in resource_id or "location_bar" in resource_id for resource_id in resource_ids)
    has_page_controls = any(
        value in text_values
        for value in ("main menu", "search help center", "sign in", "google chrome help")
    )
    has_help_or_article_structure = any(
        token in text
        for text in text_values
        for token in ("help", "support", "customize your new tab page", "helpcenter sections")
    ) or _contains_long_form_text(visible_text)
    is_page_like = (
        (has_webview and has_address_bar)
        or (has_webview and len(visible_text) >= 8)
        or (has_address_bar and has_page_controls)
        or (has_help_or_article_structure and len(visible_text) >= 6)
    )
    has_modal = compact_overlay or (explicit_modal and not is_page_like)
    return {
        "has_bottom_nav": has_bottom_nav,
        "has_back_button": has_back_button,
        "has_modal": has_modal,
        "has_webview": has_webview,
        "has_address_bar": has_address_bar,
        "is_page_like": is_page_like,
        "has_help_or_article_structure": has_help_or_article_structure,
    }


def _visible_text(elements: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for element in elements:
        if not element.get("visible"):
            continue
        for value in _collect_candidate_strings(element):
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return ordered[:40]


def _screen_title_guess(elements: list[dict[str, Any]]) -> str:
    for element in elements:
        if not element.get("visible"):
            continue
        for candidate in _collect_candidate_strings(element):
            if 2 <= len(candidate) <= 80:
                return candidate
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


def _node_class_name(node: ET.Element, attributes: dict[str, Any]) -> str:
    return _normalize_text(attributes.get("class") or node.tag)


def _extract_node(node: ET.Element, index: int) -> dict[str, Any]:
    attributes = dict(node.attrib)
    bounds = _parse_bounds(attributes.get("bounds", ""))
    width, height = _bounds_size(bounds)

    text = _normalize_text(attributes.get("text"))
    content_desc = _normalize_text(attributes.get("content-desc"))
    resource_id = _normalize_text(attributes.get("resource-id"))
    class_name = _node_class_name(node, attributes)
    package_name = _normalize_text(attributes.get("package"))
    hint_text = _normalize_text(
        attributes.get("hint-text")
        or attributes.get("hint")
        or attributes.get("pane-title")
        or attributes.get("tooltip-text")
    )
    title_hint = _title_hint(text, content_desc, resource_id, hint_text, class_name)
    visible = _is_probably_visible(attributes, width, height)

    return {
        "element_id": f"el_{index:04d}",
        "text": text,
        "content_desc": content_desc,
        "hint_text": hint_text,
        "title_hint": title_hint,
        "class_name": class_name,
        "resource_id": resource_id,
        "package_name": package_name,
        "bounds": bounds,
        "width": width,
        "height": height,
        "clickable": _as_bool(attributes.get("clickable")),
        "enabled": _as_bool(attributes.get("enabled", "true")),
        "focusable": _as_bool(attributes.get("focusable")),
        "focused": _as_bool(attributes.get("focused")),
        "scrollable": _as_bool(attributes.get("scrollable")),
        "long_clickable": _as_bool(attributes.get("long-clickable")),
        "selected": _as_bool(attributes.get("selected")),
        "checked": _as_bool(attributes.get("checked")),
        "displayed": _as_bool(attributes.get("displayed", "true")),
        "visible": visible,
        "label": title_hint,
    }


def extract_hierarchy(xml_source: str) -> dict[str, Any]:
    root = ET.fromstring(xml_source)
    screen_width = int(str(root.attrib.get("width") or "0") or "0")
    screen_height = int(str(root.attrib.get("height") or "0") or "0")
    elements: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for index, node in enumerate(root.iter()):
        if node is root:
            continue

        element = _extract_node(node, index)
        dedupe_key = _dedupe_key(element)
        if dedupe_key in seen:
            continue

        # Skip completely empty, non-visible structural wrappers.
        if not any(
            [
                element["text"],
                element["content_desc"],
                element["hint_text"],
                element["resource_id"],
                element["clickable"],
                element["focusable"],
                element["scrollable"],
                element["visible"],
            ]
        ):
            continue

        seen.add(dedupe_key)
        elements.append(element)

    visible_text = _visible_text(elements)
    return {
        "elements": elements,
        "visible_text": visible_text,
        "screen_title_guess": _screen_title_guess(elements),
        "meta": _meta_flags(elements, visible_text, screen_width, screen_height),
    }
