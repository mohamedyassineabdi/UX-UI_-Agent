from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _resource_tail(resource_id: str) -> str:
    value = _norm(resource_id)
    if not value:
        return ""
    return value.split("/")[-1].split(":")[-1]


def _labels(elements: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for element in elements:
        for key in ("text", "content_desc", "hint_text", "title_hint", "label", "resource_id", "class_name"):
            value = _norm(element.get(key))
            if value:
                values.add(value)
    return values


def _has_class(elements: list[dict[str, Any]], token: str) -> bool:
    token = token.lower()
    return any(token in _norm(element.get("class_name")) for element in elements)


def _count_class(elements: list[dict[str, Any]], token: str) -> int:
    token = token.lower()
    return sum(1 for element in elements if token in _norm(element.get("class_name")))


def _has_resource(elements: list[dict[str, Any]], token: str) -> bool:
    token = token.lower()
    return any(token in _norm(element.get("resource_id")) for element in elements)


def _count_clickable(elements: list[dict[str, Any]]) -> int:
    return sum(1 for element in elements if element.get("visible") and element.get("clickable"))


def _count_scrollable(elements: list[dict[str, Any]]) -> int:
    return sum(1 for element in elements if element.get("visible") and element.get("scrollable"))


def _has_visible_text_token(visible_text: list[str], *tokens: str) -> bool:
    text_blob = " | ".join(_norm(value) for value in visible_text if _norm(value))
    return any(token.lower() in text_blob for token in tokens)


def _looks_like_shortcut_grid(elements: list[dict[str, Any]]) -> bool:
    tile_titles = [
        element
        for element in elements
        if element.get("visible")
        and _resource_tail(str(element.get("resource_id") or "")) in {"tile_view_title", "most_visited_tile_title"}
        and _norm(element.get("text"))
    ]
    return len(tile_titles) >= 3


def _looks_like_compact_overlay(elements: list[dict[str, Any]], meta: dict[str, Any]) -> bool:
    if not meta.get("has_modal"):
        return False
    visible = [element for element in elements if element.get("visible") and len(element.get("bounds") or []) == 4]
    if not visible:
        return False
    min_x = min(int(element["bounds"][0]) for element in visible)
    min_y = min(int(element["bounds"][1]) for element in visible)
    max_x = max(int(element["bounds"][2]) for element in visible)
    max_y = max(int(element["bounds"][3]) for element in visible)
    width = max(0, max_x - min_x)
    height = max(0, max_y - min_y)
    return width <= 700 and height <= 700


def classify_screen(
    *,
    elements: list[dict[str, Any]],
    visible_text: list[str],
    meta: dict[str, Any],
    package_name: str,
    activity_name: str,
    screen_title_guess: str,
) -> dict[str, Any]:
    labels = _labels(elements)
    package_norm = _norm(package_name)
    title_norm = _norm(screen_title_guess)

    has_recycler = _has_class(elements, "recyclerview")
    has_listview = _has_class(elements, "listview")
    has_webview_class = _has_class(elements, "webview")
    has_webview_desc = any(_norm(element.get("content_desc")) == "web view" for element in elements)
    has_edittext = _has_class(elements, "edittext")
    has_url_bar = _has_resource(elements, "url_bar") or _has_resource(elements, "location_bar")
    has_search_box = _has_resource(elements, "search_box") or _has_visible_text_token(
        visible_text,
        "search or type web address",
    )
    has_shortcuts = _looks_like_shortcut_grid(elements)
    has_discover = _has_visible_text_token(visible_text, "discover", "options for discover")
    has_help = _has_visible_text_token(visible_text, "help", "support.google.com", "google chrome help")
    has_browser_menu_items = _has_visible_text_token(
        visible_text,
        "new tab",
        "history",
        "downloads",
        "bookmarks",
        "settings",
        "help & feedback",
        "find in page",
    )

    screen_type = "unknown"
    ui_patterns: list[str] = []
    interaction_model = "tap"
    content_density = "low"
    navigation_complexity = "low"

    if meta.get("has_modal") and has_listview and _looks_like_compact_overlay(elements, meta):
        screen_type = "modal_menu"
        ui_patterns = ["overlay", "context_menu", "stacked_actions"]
        interaction_model = "tap"
        content_density = "low"
        navigation_complexity = "low"

    elif has_browser_menu_items and has_listview:
        screen_type = "browser_menu"
        ui_patterns = ["overflow_menu", "grouped_actions", "stacked_actions"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "medium"

    elif has_webview_class or has_webview_desc or (has_url_bar and has_help):
        screen_type = "webview_page"
        ui_patterns = ["top_bar", "address_bar", "web_content"]
        interaction_model = "scroll + tap"
        content_density = "medium" if has_help else "high"
        navigation_complexity = "medium"

    elif package_norm == "com.android.chrome" and has_recycler and has_search_box and has_shortcuts:
        screen_type = "home_feed"
        ui_patterns = ["top_bar", "search_bar", "shortcut_grid", "feed"]
        if has_discover:
            ui_patterns.append("discover_feed")
        interaction_model = "scroll + tap"
        content_density = "high"
        navigation_complexity = "medium"

    elif has_listview:
        screen_type = "menu_list"
        ui_patterns = ["stacked_actions", "list_menu"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "medium"

    elif has_recycler and has_edittext:
        screen_type = "content_feed"
        ui_patterns = ["input", "scrollable_feed"]
        interaction_model = "scroll + tap"
        content_density = "high"
        navigation_complexity = "medium"

    elif has_recycler:
        screen_type = "scrollable_collection"
        ui_patterns = ["scrollable_collection"]
        interaction_model = "scroll + tap"
        content_density = "medium"
        navigation_complexity = "medium"

    elif has_edittext:
        screen_type = "input_screen"
        ui_patterns = ["input"]
        interaction_model = "tap + type"
        content_density = "low"
        navigation_complexity = "low"

    ux_signals = {
        "has_primary_cta": _count_clickable(elements) >= 1,
        "is_scrollable": _count_scrollable(elements) >= 1 or bool(meta.get("is_page_like")),
        "has_redundant_actions": has_shortcuts and _count_clickable(elements) >= 8,
        "interaction_cost": (
            "low"
            if screen_type in {"home_feed", "modal_menu", "browser_menu", "menu_list"}
            else "medium"
        ),
        "is_overlay": screen_type in {"modal_menu", "browser_menu"},
        "blocks_background": screen_type in {"modal_menu", "browser_menu"},
        "contains_external_content": screen_type == "webview_page",
    }

    if title_norm in {"learn more", "turn off"} and screen_type == "unknown":
        screen_type = "modal_menu"
        ui_patterns = ["overlay", "context_menu", "stacked_actions"]
        ux_signals["is_overlay"] = True
        ux_signals["blocks_background"] = True

    return {
        "screen_type": screen_type,
        "ui_patterns": ui_patterns,
        "interaction_model": interaction_model,
        "content_density": content_density,
        "navigation_complexity": navigation_complexity,
        "ux_signals": ux_signals,
        "signals": {
            "has_recycler": has_recycler,
            "has_listview": has_listview,
            "has_webview_class": has_webview_class,
            "has_webview_desc": has_webview_desc,
            "has_url_bar": has_url_bar,
            "has_search_box": has_search_box,
            "has_shortcuts": has_shortcuts,
            "has_discover": has_discover,
            "has_help": has_help,
            "has_browser_menu_items": has_browser_menu_items,
        },
        "package_name": package_name,
        "activity_name": activity_name,
        "screen_title_guess": screen_title_guess,
    }