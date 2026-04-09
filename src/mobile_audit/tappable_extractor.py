from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bounds_area(bounds: list[int]) -> int:
    if len(bounds) != 4:
        return 0
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _contains_bounds(outer: list[int], inner: list[int]) -> bool:
    if len(outer) != 4 or len(inner) != 4:
        return False
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _nested_text_candidate(element: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    outer_bounds = element.get("bounds") or []
    candidates: list[dict[str, Any]] = []
    for candidate in elements:
        if candidate is element:
            continue
        if not candidate.get("visible"):
            continue
        inner_bounds = candidate.get("bounds") or []
        if not _contains_bounds(outer_bounds, inner_bounds):
            continue
        text = _text(candidate.get("text"))
        content_desc = _text(candidate.get("content_desc"))
        hint_text = _text(candidate.get("hint_text"))
        title_hint = _text(candidate.get("title_hint"))
        if not any((text, content_desc, hint_text, title_hint)):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (_bounds_area(item.get("bounds") or []), len(_text(item.get("text")) or _text(item.get("content_desc")) or _text(item.get("title_hint")))))
    return candidates[0]


def _label_for_tappable(element: dict[str, Any], elements: list[dict[str, Any]]) -> str:
    for key in ("text", "content_desc", "hint_text"):
        value = _text(element.get(key))
        if value:
            return value
    nested_candidate = _nested_text_candidate(element, elements)
    if nested_candidate:
        for key in ("text", "content_desc", "hint_text", "title_hint", "label"):
            value = _text(nested_candidate.get(key))
            if value:
                return value
    element_label = _text(element.get("text")) or _text(element.get("title_hint")) or _text(element.get("label"))
    class_name = _text(element.get("class_name"))
    if element_label and element_label.lower() not in {class_name.rsplit(".", 1)[-1].lower(), class_name.lower()}:
        return element_label
    resource_id = _text(element.get("resource_id"))
    if resource_id:
        tail = resource_id.split("/")[-1].split(":")[-1]
        return tail.replace("_", " ").replace("-", " ").strip() or "Action"
    return class_name.rsplit(".", 1)[-1] if class_name else "Action"


def _is_actionable(element: dict[str, Any]) -> bool:
    if not element.get("visible") or not element.get("enabled"):
        return False
    class_name = _text(element.get("class_name")).lower()
    if ("listview" in class_name or "recyclerview" in class_name) and not element.get("clickable") and not element.get("long_clickable"):
        return False
    if element.get("clickable") or element.get("long_clickable") or element.get("focusable"):
        return True
    return any(
        token in class_name
        for token in (
            "button",
            "edittext",
            "imagebutton",
            "checkbox",
            "switch",
            "tabwidget",
            "seekbar",
        )
    )


def _dedupe_signature(element: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(element.get("bounds") or []),
        _text(element.get("resource_id")),
        _text(element.get("text")),
        _text(element.get("content_desc")),
        _text(element.get("class_name")),
    )


def build_tappables(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [element for element in elements if _is_actionable(element)]
    candidates.sort(key=lambda item: (_bounds_area(item.get("bounds") or []), len(_label_for_tappable(item, elements))), reverse=True)

    tappables: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    occupied_bounds: set[tuple[int, int, int, int]] = set()

    for element in candidates:
        signature = _dedupe_signature(element)
        if signature in seen:
            continue

        bounds = tuple(int(value) for value in (element.get("bounds") or [0, 0, 0, 0]))
        if bounds in occupied_bounds and _text(element.get("resource_id")) == "":
            continue

        nested_candidate = _nested_text_candidate(element, elements)
        resolved_text = _text(element.get("text")) or _text((nested_candidate or {}).get("text"))
        resolved_content_desc = _text(element.get("content_desc")) or _text((nested_candidate or {}).get("content_desc"))
        resolved_hint_text = _text(element.get("hint_text")) or _text((nested_candidate or {}).get("hint_text"))
        seen.add(signature)
        occupied_bounds.add(bounds)
        tappables.append(
            {
                "element_id": element.get("element_id"),
                "class_name": element.get("class_name"),
                "resource_id": element.get("resource_id"),
                "text": resolved_text,
                "content_desc": resolved_content_desc,
                "hint_text": resolved_hint_text,
                "label": _label_for_tappable(element, elements),
                "bounds": list(bounds),
                "clickable": bool(element.get("clickable")),
                "enabled": bool(element.get("enabled")),
                "visible": bool(element.get("visible")),
                "focusable": bool(element.get("focusable")),
                "scrollable": bool(element.get("scrollable")),
            }
        )

    return tappables
