from __future__ import annotations

import re
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


def _same_label(left: str, right: str) -> bool:
    left_clean = re.sub(r"\s+", " ", _text(left).lower()).strip()
    right_clean = re.sub(r"\s+", " ", _text(right).lower()).strip()
    return bool(left_clean) and left_clean == right_clean


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


def _label_richness(element: dict[str, Any], elements: list[dict[str, Any]]) -> int:
    label = _label_for_tappable(element, elements)
    text = _text(element.get("text"))
    content_desc = _text(element.get("content_desc"))
    hint_text = _text(element.get("hint_text"))
    resource_id = _text(element.get("resource_id"))

    richness = 0
    if text:
        richness += 50
    if content_desc:
        richness += 35
    if hint_text:
        richness += 20
    if resource_id:
        richness += 10
    richness += min(len(label), 40)
    return richness


def _control_type(element: dict[str, Any]) -> str:
    class_name = _text(element.get("class_name")).lower()
    resource_id = _text(element.get("resource_id")).lower()
    width = int(element.get("width") or 0)
    height = int(element.get("height") or 0)

    if "seekbar" in class_name or "slider" in class_name or "seekbar" in resource_id or "slider" in resource_id:
        return "slider"

    if (
        "progressbar" in class_name
        and (element.get("clickable") or element.get("focusable"))
        and width >= max(height * 3, 120)
    ):
        return "slider"

    return "action"


def _is_generic_label(value: str) -> bool:
    return _text(value).lower() in {
        "",
        "view",
        "viewgroup",
        "button",
        "imagebutton",
        "action",
        "layout",
        "framelayout",
        "linearlayout",
        "constraintlayout",
        "coordinatorlayout",
        "composeview",
        "scrollview",
        "recyclerview",
        "content",
        "android id content",
        "action bar root",
        "root",
    }


def _is_system_back_like(element: dict[str, Any], label: str) -> bool:
    bounds = element.get("bounds") or []
    if len(bounds) != 4:
        return False
    class_name = _text(element.get("class_name")).lower()
    resource_id = _text(element.get("resource_id")).lower()
    width = max(0, int(bounds[2]) - int(bounds[0]))
    height = max(0, int(bounds[3]) - int(bounds[1]))
    is_top_left = int(bounds[0]) <= 80 and int(bounds[1]) <= 180 and width <= 180 and height <= 180
    if "back" in resource_id or _text(label).lower() == "back":
        return True
    return is_top_left and "button" in class_name and _is_generic_label(label)


def _control_role(element: dict[str, Any], label: str) -> str:
    if _is_system_back_like(element, label):
        return "system_back"
    if _control_type(element) == "slider":
        return "slider"
    class_name = _text(element.get("class_name")).lower()
    if "edittext" in class_name:
        return "text_input"
    if _is_generic_label(label):
        return "generic_wrapper"
    return "action"


def _looks_like_synthetic_text_target(text: str) -> bool:
    value = _text(text)
    if not value:
        return False
    if _is_generic_label(value):
        return False
    if len(value) < 2 or len(value) > 90:
        return False
    if re.fullmatch(r"[\W_]+", value):
        return False
    return True


def _synthetic_text_tappables(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    synthetic: list[dict[str, Any]] = []
    seen_bounds: set[tuple[int, int, int, int]] = set()
    seen_labels: set[str] = set()

    for element in elements:
        if not element.get("visible") or not element.get("enabled"):
            continue
        label = (
            _text(element.get("text"))
            or _text(element.get("content_desc"))
            or _text(element.get("hint_text"))
            or _text(element.get("title_hint"))
        )
        if not _looks_like_synthetic_text_target(label):
            continue

        bounds = tuple(int(value) for value in (element.get("bounds") or [0, 0, 0, 0]))
        if len(bounds) != 4 or bounds in seen_bounds:
            continue
        if _bounds_area(list(bounds)) < 44 * 44:
            continue
        label_key = re.sub(r"\s+", " ", label.lower()).strip()
        if label_key in seen_labels:
            continue

        seen_bounds.add(bounds)
        seen_labels.add(label_key)
        synthetic.append(
            {
                "element_id": element.get("element_id"),
                "class_name": element.get("class_name"),
                "resource_id": element.get("resource_id"),
                "text": label,
                "content_desc": _text(element.get("content_desc")),
                "hint_text": _text(element.get("hint_text")),
                "label": label,
                "bounds": list(bounds),
                "clickable": False,
                "enabled": True,
                "visible": True,
                "focusable": False,
                "scrollable": False,
                "control_type": "action",
                "control_role": "synthetic_text_target",
                "is_generic_label": False,
            }
        )
        if len(synthetic) >= 12:
            break

    return synthetic


def build_tappables(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [element for element in elements if _is_actionable(element)]
    candidates.sort(
        key=lambda item: (
            _label_richness(item, elements),
            int(bool(_text(item.get("resource_id")))),
            int(bool(_text(item.get("text")) or _text(item.get("content_desc")))),
            -_bounds_area(item.get("bounds") or []),
        ),
        reverse=True,
    )

    tappables: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    occupied_bounds: set[tuple[int, int, int, int]] = set()
    kept_label_bounds: list[tuple[str, list[int]]] = []

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
        label = _label_for_tappable(element, elements)
        if any(
            _same_label(label, kept_label)
            and _contains_bounds(list(bounds), kept_bounds)
            and _bounds_area(kept_bounds) > 0
            and _bounds_area(list(bounds)) > _bounds_area(kept_bounds) * 1.35
            for kept_label, kept_bounds in kept_label_bounds
        ):
            continue
        control_type = _control_type(element)
        seen.add(signature)
        occupied_bounds.add(bounds)
        kept_label_bounds.append((label, list(bounds)))
        tappables.append(
            {
                "element_id": element.get("element_id"),
                "class_name": element.get("class_name"),
                "resource_id": element.get("resource_id"),
                "text": resolved_text,
                "content_desc": resolved_content_desc,
                "hint_text": resolved_hint_text,
                "label": label,
                "bounds": list(bounds),
                "clickable": bool(element.get("clickable")),
                "enabled": bool(element.get("enabled")),
                "visible": bool(element.get("visible")),
                "focusable": bool(element.get("focusable")),
                "scrollable": bool(element.get("scrollable")),
                "control_type": control_type,
                "control_role": _control_role(element, label),
                "is_generic_label": _is_generic_label(label),
            }
        )

    if len(tappables) < 3:
        occupied_bounds.update(
            tuple(int(value) for value in item.get("bounds", []))
            for item in tappables
            if len(item.get("bounds", [])) == 4
        )
        for synthetic in _synthetic_text_tappables(elements):
            bounds = tuple(int(value) for value in synthetic.get("bounds", []))
            if bounds in occupied_bounds:
                continue
            tappables.append(synthetic)
            occupied_bounds.add(bounds)
            if len(tappables) >= 8:
                break

    return tappables
