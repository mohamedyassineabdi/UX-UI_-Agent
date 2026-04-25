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


def _has_any_token(values: set[str], tokens: tuple[str, ...]) -> bool:
    return any(token in value for value in values for token in tokens)


def _looks_like_assessment_prompt(
    *,
    screen_title_guess: str,
    visible_text: list[str],
    clickable_count: int,
    input_count: int,
    meta: dict[str, Any],
) -> bool:
    if input_count > 0:
        return False
    if bool(meta.get("has_modal")):
        return False
    title = str(screen_title_guess or "").strip()
    if not title:
        return False
    question_like_title = title.endswith("?")
    assessment_signals = _has_visible_text_token(visible_text, "fitness assessment", "assessment")
    if not question_like_title and not assessment_signals:
        return False
    visible_text_count = int(meta.get("visible_text_count") or len(visible_text))
    return 2 <= clickable_count <= 8 and visible_text_count <= 16


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
    return width <= 700 and height <= 900


def _infer_content_density(visible_text_count: int, clickable_count: int, scrollable_count: int) -> str:
    density_score = visible_text_count + clickable_count + (scrollable_count * 2)
    if density_score >= 20:
        return "high"
    if density_score >= 8:
        return "medium"
    return "low"


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
    title_norm = _norm(screen_title_guess)

    has_recycler = _has_class(elements, "recyclerview")
    has_listview = _has_class(elements, "listview")
    has_webview_class = _has_class(elements, "webview")
    has_webview_desc = any(_norm(element.get("content_desc")) == "web view" for element in elements)
    has_edittext = _has_class(elements, "edittext")
    has_toolbar = _has_class(elements, "toolbar") or _has_class(elements, "appbarlayout")
    has_bottom_nav = bool(meta.get("has_bottom_nav"))
    has_search_box = _has_resource(elements, "search_box") or _has_resource(elements, "search_src_text")

    clickable_count = _count_clickable(elements)
    scrollable_count = _count_scrollable(elements)
    input_count = int(meta.get("input_count") or 0)
    visible_text_count = int(meta.get("visible_text_count") or len(visible_text))
    content_density = _infer_content_density(visible_text_count, clickable_count, scrollable_count)

    auth_tokens = (
        "sign in",
        "log in",
        "login",
        "password",
        "email",
        "forgot password",
        "create account",
        "register",
        "username",
        "otp",
        "verification code",
    )
    onboarding_tokens = (
        "welcome",
        "get started",
        "continue",
        "skip",
        "next",
        "intro",
        "allow notifications",
        "maybe later",
        "not now",
        "let's go",
    )
    dashboard_tokens = (
        "home",
        "dashboard",
        "overview",
        "browse",
        "explore",
        "discover",
        "feed",
        "for you",
    )
    support_tokens = (
        "help",
        "support",
        "faq",
        "privacy",
        "terms",
    )
    menu_tokens = (
        "menu",
        "settings",
        "help & feedback",
        "notifications",
        "history",
        "downloads",
        "bookmarks",
        "options",
    )

    screen_type = "unknown"
    ui_patterns: list[str] = []
    interaction_model = "tap"
    navigation_complexity = "low"

    if meta.get("has_modal") and _looks_like_compact_overlay(elements, meta):
        screen_type = "modal_surface"
        ui_patterns = ["overlay", "dialog", "stacked_actions"]
        interaction_model = "tap"
        navigation_complexity = "low"

    elif meta.get("has_modal"):
        screen_type = "menu_surface"
        ui_patterns = ["overlay", "menu", "stacked_actions"]
        interaction_model = "tap"
        navigation_complexity = "low"

    elif has_webview_class or has_webview_desc or meta.get("has_webview"):
        screen_type = "webview_screen"
        ui_patterns = ["top_bar", "web_content"]
        interaction_model = "scroll + tap"
        navigation_complexity = "medium"

    elif input_count >= 2 and (_has_any_token(labels, auth_tokens) or _has_visible_text_token(visible_text, *auth_tokens)):
        screen_type = "auth_screen"
        ui_patterns = ["input", "form", "authentication"]
        interaction_model = "tap + type"
        navigation_complexity = "medium"

    elif _has_any_token(labels, onboarding_tokens) and visible_text_count <= 18:
        screen_type = "onboarding_screen"
        ui_patterns = ["hero", "pager", "progression"]
        interaction_model = "tap"
        navigation_complexity = "low"

    elif _looks_like_assessment_prompt(
        screen_title_guess=screen_title_guess,
        visible_text=visible_text,
        clickable_count=clickable_count,
        input_count=input_count,
        meta=meta,
    ):
        screen_type = "onboarding_screen"
        ui_patterns = ["questionnaire", "assessment", "progression"]
        interaction_model = "tap"
        navigation_complexity = "low"

    elif input_count >= 1 and has_edittext:
        screen_type = "form_screen"
        ui_patterns = ["input", "form"]
        interaction_model = "tap + type"
        navigation_complexity = "medium"

    elif has_bottom_nav and (has_recycler or has_listview or clickable_count >= 4):
        screen_type = "home_dashboard"
        ui_patterns = ["top_bar", "bottom_nav", "dashboard"]
        interaction_model = "scroll + tap"
        navigation_complexity = "high"

    elif (has_recycler or has_listview) and clickable_count >= 4:
        screen_type = "list_feed"
        ui_patterns = ["scrollable_collection", "cards_or_rows"]
        interaction_model = "scroll + tap"
        navigation_complexity = "medium"

    elif scrollable_count >= 1 and visible_text_count >= 8:
        screen_type = "detail_screen"
        ui_patterns = ["scrollable_content", "detail"]
        interaction_model = "scroll + tap"
        navigation_complexity = "medium"

    elif has_toolbar and clickable_count >= 2:
        screen_type = "navigation_shell"
        ui_patterns = ["top_bar", "navigation"]
        interaction_model = "tap"
        navigation_complexity = "medium"

    elif has_recycler or has_listview or scrollable_count >= 1:
        screen_type = "scrollable_content"
        ui_patterns = ["scrollable_content"]
        interaction_model = "scroll + tap"
        navigation_complexity = "medium"

    elif clickable_count >= 2 and (_has_any_token(labels, dashboard_tokens) or title_norm in dashboard_tokens):
        screen_type = "home_dashboard"
        ui_patterns = ["dashboard"]
        interaction_model = "tap"
        navigation_complexity = "medium"

    ux_signals = {
        "has_primary_cta": clickable_count >= 1,
        "is_scrollable": scrollable_count >= 1 or bool(meta.get("is_page_like")),
        "has_redundant_actions": clickable_count >= 8,
        "interaction_cost": "low" if screen_type in {"home_dashboard", "modal_surface", "menu_surface"} else "medium",
        "is_overlay": screen_type in {"modal_surface", "menu_surface"},
        "blocks_background": screen_type in {"modal_surface", "menu_surface"},
        "contains_external_content": screen_type == "webview_screen",
        "has_support_signals": _has_any_token(labels, support_tokens),
        "has_menu_signals": _has_any_token(labels, menu_tokens),
    }

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
            "has_search_box": has_search_box,
            "has_toolbar": has_toolbar,
            "has_bottom_nav": has_bottom_nav,
            "clickable_count": clickable_count,
            "scrollable_count": scrollable_count,
            "input_count": input_count,
            "visible_text_count": visible_text_count,
        },
        "package_name": package_name,
        "activity_name": activity_name,
        "screen_title_guess": screen_title_guess,
    }
