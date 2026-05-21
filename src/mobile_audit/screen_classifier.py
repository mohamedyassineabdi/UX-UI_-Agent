from __future__ import annotations

import re
import unicodedata
from typing import Any


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    without_marks = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", without_marks)).strip()


PROGRESSION_TEXT_VALUES = {
    "next",
    "continue",
    "skip",
    "get started",
    "start",
    "done",
    "finish",
    "commencer",
    "demarrer",
    "debuter",
    "continuer",
    "suivant",
    "passer",
    "ignorer",
    "terminer",
    "mode invite",
    "sans compte",
    "continue without account",
    "use without account",
    "browse",
    "lets go",
    "got it",
}


def _has_folded_phrase(value: Any, phrases: set[str]) -> bool:
    folded = _fold(value)
    if folded in phrases:
        return True
    parts = [_fold(part) for part in str(value or "").split("|")]
    return any(part in phrases for part in parts)


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


def _bounds_area(bounds: list[int]) -> int:
    if len(bounds) != 4:
        return 0
    return max(0, int(bounds[2]) - int(bounds[0])) * max(0, int(bounds[3]) - int(bounds[1]))


def _has_visible_text_token(visible_text: list[str], *tokens: str) -> bool:
    text_blob = " | ".join(_norm(value) for value in visible_text if _norm(value))
    return any(token.lower() in text_blob for token in tokens)


def _text_blob(visible_text: list[str]) -> str:
    return " | ".join(_norm(value) for value in visible_text if _norm(value))


def _looks_like_intro_landing(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False
    text_blob = _text_blob(visible_text)
    folded_blob = _fold(text_blob)
    has_entry_cta = any(_has_folded_phrase(value, PROGRESSION_TEXT_VALUES) for value in visible_text)
    has_market_proof = any(token in text_blob for token in ("users", "million", "countries", "worldwide", "covered"))
    has_intro_copy = any(
        token in folded_blob
        for token in (
            "welcome",
            "bienvenue",
            "application",
            "tout en un",
            "a portee de main",
            "favorite",
            "prefere",
        )
    )
    return has_entry_cta and (has_market_proof or has_intro_copy) and int(meta.get("clickable_count") or 0) <= 3


def _looks_like_proof_interstitial(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False
    text_blob = _text_blob(visible_text)
    has_progression = _has_folded_phrase(text_blob, PROGRESSION_TEXT_VALUES)
    has_proof = any(
        token in text_blob
        for token in (
            "people just like you",
            "users found",
            "easy to follow",
            "visible changes",
            "#1",
            "100k",
            "97.",
            "millions like you",
        )
    )
    return has_progression and has_proof


def _looks_like_prediction_interstitial(visible_text: list[str], meta: dict[str, Any], screen_title_guess: str) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False
    text_blob = _text_blob(visible_text)
    title = _norm(screen_title_guess)
    has_progression = _has_folded_phrase(text_blob, PROGRESSION_TEXT_VALUES)
    has_prediction = any(
        token in text_blob or token in title
        for token in (
            "we predict",
            "you'll be",
            "realistic target",
            "great potential",
            "according to your answer",
            "may ",
            "week 1",
            "today",
        )
    )
    return has_progression and has_prediction


def _looks_like_coaching_interstitial(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False
    text_blob = _text_blob(visible_text)
    has_progression = _has_folded_phrase(text_blob, PROGRESSION_TEXT_VALUES)
    has_coaching_content = any(
        token in text_blob
        for token in (
            "count on us",
            "tailor your workout plan",
            "based on your preferences",
            "busy schedule",
            "keep your workouts simple",
            "weight loss can change",
            "change your body",
            "get ready",
        )
    )
    return has_progression and has_coaching_content


def _looks_like_opaque_visual_surface(elements: list[dict[str, Any]], visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False

    visible_text_count = int(meta.get("visible_text_count") or len(visible_text))
    clickable_count = int(meta.get("clickable_count") or 0)
    if visible_text_count > 1 or clickable_count < 1 or clickable_count > 3:
        return False

    screen_bounds = list(meta.get("screen_bounds_union") or [0, 0, 0, 0])
    screen_area = _bounds_area(screen_bounds)
    if screen_area <= 0:
        return False

    has_visual_framework_root = any(
        token in _norm(element.get("class_name"))
        for element in elements
        for token in ("composeview", "flutterview", "reactrootview", "surfaceview")
    )
    has_fullscreen_click_target = any(
        element.get("visible")
        and element.get("clickable")
        and _bounds_area(list(element.get("bounds") or [])) >= int(screen_area * 0.75)
        for element in elements
    )
    return has_visual_framework_root and has_fullscreen_click_target


def _looks_like_result_summary(visible_text: list[str], meta: dict[str, Any], activity_name: str) -> bool:
    text_blob = _text_blob(visible_text)
    activity = _norm(activity_name)
    has_social_proof = any(
        token in text_blob
        for token in (
            "followers",
            "following",
            "getting fit feels",
            "glowing up",
            "millions like you",
            "testimonial",
            "likes",
        )
    )
    return "result" in activity or (has_social_proof and int(meta.get("clickable_count") or 0) == 0)


def _looks_like_paywall_or_offer(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal"):
        return False
    text_blob = _text_blob(visible_text)
    return any(
        token in text_blob
        for token in (
            "free trial",
            "trial",
            "subscribe",
            "subscription",
            "per week",
            "per month",
            "limited offer",
            "unlock",
            "continue to plan",
        )
    )


def _looks_like_blocked_no_actions(visible_text: list[str], meta: dict[str, Any]) -> bool:
    return (
        int(meta.get("visible_text_count") or len(visible_text)) >= 3
        and int(meta.get("clickable_count") or 0) == 0
        and not meta.get("has_modal")
    )


def _looks_like_onboarding_screen(
    *,
    visible_text: list[str],
    meta: dict[str, Any],
    screen_title_guess: str,
    has_edittext: bool,
) -> bool:
    if has_edittext:
        return False
    if meta.get("has_modal") or meta.get("has_webview") or meta.get("has_bottom_nav"):
        return False

    clickable_count = int(meta.get("clickable_count") or 0)
    visible_text_count = int(meta.get("visible_text_count") or 0)
    scrollable_count = int(meta.get("scrollable_count") or 0)
    title_norm = _norm(screen_title_guess)
    text_values = [_norm(value) for value in visible_text if _norm(value)]

    has_progression = any(_has_folded_phrase(value, PROGRESSION_TEXT_VALUES) for value in text_values)
    has_question_title = "?" in str(screen_title_guess or "") or any(
        token in title_norm
        for token in (
            "what ",
            "which ",
            "choose",
            "select",
            "your goal",
            "goal",
            "motivate",
            "motivation",
            "level",
        )
    )

    option_like_labels = [
        value
        for value in text_values
        if not _has_folded_phrase(value, PROGRESSION_TEXT_VALUES)
        and 2 <= len(value) <= 40
        and 1 <= len(value.split()) <= 5
    ]

    has_option_grid = clickable_count >= 3 and len(option_like_labels) >= 3
    has_picker_structure = clickable_count >= 2 and (has_question_title or has_progression) and visible_text_count <= 18

    return has_option_grid and has_picker_structure and (has_progression or has_question_title or scrollable_count >= 1)


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


def _looks_like_program_overview_screen(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False

    normalized = [_norm(value) for value in visible_text if _norm(value)]
    if not normalized:
        return False

    has_day_card = any(value.startswith("day ") for value in normalized)
    has_duration_or_calories = any("min" in value or "kcal" in value for value in normalized)
    has_progress_signal = any("%" in value for value in normalized)
    has_result_headline = any(
        token in value
        for value in normalized
        for token in ("fitness plans finished", "changes in the face", "plans finished")
    )

    return has_day_card and (has_duration_or_calories or has_progress_signal or has_result_headline)


def _looks_like_mobile_home_dashboard(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False

    folded_blob = _fold(_text_blob(visible_text))
    clickable_count = int(meta.get("clickable_count") or 0)
    has_bottom_nav = bool(meta.get("has_bottom_nav"))
    has_home_tab = any(token in folded_blob for token in ("accueil", "home"))
    has_dashboard_sections = any(
        token in folded_blob
        for token in (
            "acces rapide",
            "quick access",
            "catalogue",
            "catalogues",
            "ma carte",
            "scan prix",
            "magasins",
            "jeux",
            "plus",
            "for you",
            "featured",
            "recent",
            "recommended",
            "popular",
            "categories",
            "profile",
            "notifications",
            "messages",
            "settings",
        )
    )
    return clickable_count >= 5 and (has_bottom_nav or has_home_tab) and (has_dashboard_sections or int(meta.get("visible_text_count") or 0) >= 6)


def _looks_like_navigation_shell(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False
    clickable_count = int(meta.get("clickable_count") or 0)
    visible_text_count = int(meta.get("visible_text_count") or len(visible_text))
    return bool(meta.get("has_bottom_nav")) and clickable_count >= 3 and visible_text_count >= 2


def _looks_like_auth_gate(visible_text: list[str], meta: dict[str, Any]) -> bool:
    if meta.get("has_modal") or meta.get("has_webview"):
        return False
    folded_blob = _fold(_text_blob(visible_text))
    has_auth_prompt = any(
        token in folded_blob
        for token in (
            "connexion",
            "connectez vous",
            "identifiez vous",
            "sign in",
            "log in",
            "login",
        )
    )
    has_guest_route = any(token in folded_blob for token in ("mode invite", "guest", "sans compte"))
    return has_auth_prompt and has_guest_route


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

    elif _looks_like_paywall_or_offer(visible_text, meta):
        screen_type = "paywall_or_offer"
        ui_patterns = ["offer", "subscription_prompt", "conversion_gate"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "medium"

    elif _looks_like_result_summary(visible_text, meta, activity_name):
        screen_type = "result_summary"
        ui_patterns = ["result_state", "social_proof", "testimonial"]
        interaction_model = "read"
        content_density = "medium"
        navigation_complexity = "low"

    elif _looks_like_intro_landing(visible_text, meta):
        screen_type = "intro_landing"
        ui_patterns = ["hero_image", "market_proof", "primary_cta"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "low"

    elif _looks_like_onboarding_screen(
        visible_text=visible_text,
        meta=meta,
        screen_title_guess=screen_title_guess,
        has_edittext=has_edittext,
    ):
        screen_type = "onboarding_screen"
        ui_patterns = ["question_prompt", "choice_selection", "progression_cta"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "low"

    elif _looks_like_proof_interstitial(visible_text, meta):
        screen_type = "proof_interstitial"
        ui_patterns = ["social_proof", "progression_cta"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "low"

    elif _looks_like_prediction_interstitial(visible_text, meta, screen_title_guess):
        screen_type = "prediction_interstitial"
        ui_patterns = ["personalized_projection", "progression_cta"]
        interaction_model = "tap"
        content_density = "low"
        navigation_complexity = "low"

    elif _looks_like_coaching_interstitial(visible_text, meta):
        screen_type = "coaching_interstitial"
        ui_patterns = ["educational_content", "progression_cta"]
        interaction_model = "tap"
        content_density = "low"
        navigation_complexity = "low"

    elif _looks_like_opaque_visual_surface(elements, visible_text, meta):
        screen_type = "opaque_visual_surface"
        ui_patterns = ["visual_only_controls", "missing_action_semantics"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "medium"

    elif _looks_like_blocked_no_actions(visible_text, meta):
        screen_type = "blocked_no_actions"
        ui_patterns = ["readable_content", "missing_action_semantics"]
        interaction_model = "blocked"
        content_density = "medium"
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

    elif _looks_like_program_overview_screen(visible_text, meta):
        screen_type = "program_overview_screen"
        ui_patterns = ["progress_summary", "day_cards", "workout_plan"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "medium"

    elif _looks_like_mobile_home_dashboard(visible_text, meta):
        screen_type = "home_dashboard"
        ui_patterns = ["account_summary", "quick_actions", "content_cards", "bottom_navigation"]
        interaction_model = "scroll + tap"
        content_density = "high"
        navigation_complexity = "medium"

    elif _looks_like_auth_gate(visible_text, meta):
        screen_type = "auth_gate"
        ui_patterns = ["access_gate", "account_benefits", "guest_route"]
        interaction_model = "tap"
        content_density = "medium"
        navigation_complexity = "low"

    elif has_recycler and has_search_box and has_shortcuts:
        screen_type = "home_feed"
        ui_patterns = ["top_bar", "search_bar", "shortcut_grid", "feed"]
        if has_discover:
            ui_patterns.append("discover_feed")
        interaction_model = "scroll + tap"
        content_density = "high"
        navigation_complexity = "medium"

    elif _looks_like_navigation_shell(visible_text, meta):
        screen_type = "navigation_shell"
        ui_patterns = ["bottom_navigation", "content_surface"]
        interaction_model = "scroll + tap"
        content_density = "medium"
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
            else "high"
            if screen_type == "blocked_no_actions"
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
