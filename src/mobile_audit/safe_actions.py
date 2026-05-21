from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional


SAFE_RULES: list[tuple[re.Pattern[str], int, int, str, str]] = [
    (
        re.compile(r"^(close|dismiss|not now|maybe later|later|cancel|back|fermer|annuler|retour|refuser|plus tard|pas maintenant)$", re.IGNORECASE),
        98,
        92,
        "safe overlay dismissal or bounded back-navigation control",
        "resolves transient UI without making a durable product change",
    ),
    (
        re.compile(r"^(menu|main menu|open menu|more options|options)$", re.IGNORECASE),
        95,
        84,
        "bounded menu control",
        "opens a contained navigation or options surface that is safe to inspect",
    ),
    (
        re.compile(r"^(learn more|read more|more info|details|view details|help|support|faq|about|privacy|terms)$", re.IGNORECASE),
        93,
        78,
        "read-only informational destination",
        "opens explanatory or support content without immediately mutating account or product state",
    ),
    (
        re.compile(r"^(next|continue|skip|get started|start|done|finish|continue as guest|browse as guest)$", re.IGNORECASE),
        88,
        74,
        "bounded progression control",
        "advances an onboarding or lightweight flow while staying within the app context",
    ),
    (
        re.compile(r"^(home|dashboard|overview|browse|explore|accueil)$", re.IGNORECASE),
        92,
        58,
        "safe navigation control",
        "moves within stable app navigation surfaces",
    ),
    (
        re.compile(r"^(profile|account|preferences|settings|notifications)$", re.IGNORECASE),
        84,
        56,
        "contained utility destination",
        "opens an internal utility area that can still be inspected safely in a bounded run",
    ),
    (
        re.compile(r"^(see all|view all|show more|open)$", re.IGNORECASE),
        80,
        52,
        "safe expansion control",
        "reveals additional in-app content without obvious destructive side effects",
    ),
]

MODAL_FOLLOWUP_SAFE_RULES: list[tuple[re.Pattern[str], int, int, str, str]] = [
    (
        re.compile(r"^(close|dismiss|not now|later|cancel|back|fermer|annuler|retour|refuser|plus tard|pas maintenant)$", re.IGNORECASE),
        99,
        96,
        "preferred modal dismissal control",
        "clears the transient overlay and returns to the main screen context",
    ),
    (
        re.compile(r"^(learn more|details|view details|help|support)$", re.IGNORECASE),
        92,
        82,
        "bounded modal follow-up action",
        "opens explanatory content from the transient surface without directly changing settings",
    ),
]

BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(turn off|disable|delete|remove|erase|clear data|unsubscribe|deactivate|supprimer|effacer|desactiver|désactiver)\b", re.IGNORECASE), "destructive or state-changing action"),
    (re.compile(r"\b(log out|logout|sign out)\b", re.IGNORECASE), "session-ending action"),
    (re.compile(r"\b(buy|purchase|checkout|pay|subscribe|confirm|place order|add to cart|add cart|acheter|payer|abonner|confirmer|commande)\b", re.IGNORECASE), "commerce or commitment action"),
    (re.compile(r"\b(save|apply|submit|send|post|publish|accept all|allow|follow|like|rate|review|upload|autoriser|accepter|tout accepter|enregistrer|appliquer|soumettre|envoyer|publier)\b", re.IGNORECASE), "commits a product or permission state change"),
]

APP_NAVIGATION_PHRASES = {
    "home",
    "dashboard",
    "overview",
    "browse",
    "explore",
    "catalog",
    "catalogue",
    "catalogues",
    "stores",
    "store",
    "locations",
    "map",
    "games",
    "rewards",
    "points",
    "card",
    "my card",
    "more",
    "accueil",
    "catalogue",
    "catalogues",
    "magasin",
    "magasins",
    "jeux",
    "plus",
    "carte",
    "ma carte",
    "my mg",
    "conversion des points",
}

CONTENT_CARD_PHRASES = {
    "catalog",
    "catalogue",
    "catalogues",
    "offer",
    "offers",
    "promotion",
    "promotions",
    "deal",
    "deals",
    "details",
    "flyer",
    "brochure",
    "mai",
    "avril",
    "janvier",
    "fevrier",
    "mars",
    "juin",
    "juillet",
    "aout",
    "septembre",
    "octobre",
    "novembre",
    "decembre",
}

UTILITY_ACTION_PHRASES = {
    "scan prix",
    "price scan",
    "scanner prix",
    "scan price",
}

UNSAFE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"voice search|microphone|mic\b", re.IGNORECASE), "voice input action"),
    (re.compile(r"\bshare\b", re.IGNORECASE), "sharing action"),
    (re.compile(r"\bfacebook\b|\byoutube\b|\binstagram\b|\btiktok\b|\bx\b", re.IGNORECASE), "external social or content destination"),
]

UNSAFE_RESOURCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"edit|search|mic|voice", re.IGNORECASE), "search or text-entry control"),
    (re.compile(r"share|send", re.IGNORECASE), "sharing control"),
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold_label(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value).lower())
    without_marks = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", without_marks)).strip()


def _has_phrase(folded_label: str, phrases: set[str]) -> bool:
    padded = f" {folded_label} "
    return any(f" {phrase} " in padded for phrase in phrases)


PROGRESSION_PHRASES = {
    "next",
    "continue",
    "skip",
    "get started",
    "start",
    "done",
    "finish",
    "continue as guest",
    "continue without account",
    "use without account",
    "browse as guest",
    "browse",
    "lets go",
    "got it",
    "guest mode",
    "mode guest",
    "commencer",
    "demarrer",
    "debuter",
    "continuer",
    "suivant",
    "passer",
    "ignorer",
    "terminer",
    "c est parti",
    "allons y",
    "mode invite",
    "invite",
    "sans compte",
}

DEFER_PHRASES = {
    "skip",
    "not now",
    "maybe later",
    "later",
    "continue as guest",
    "browse as guest",
    "guest mode",
    "mode guest",
    "passer",
    "ignorer",
    "plus tard",
    "pas maintenant",
    "mode invite",
    "invite",
    "sans compte",
}

AUTH_ENTRY_PHRASES = {
    "sign in",
    "log in",
    "login",
    "sign up",
    "register",
    "create account",
    "s identifier",
    "identifier",
    "connexion",
    "inscription",
    "se connecter",
    "creer un compte",
}

GENERIC_NAVIGATION_TOKENS = {
    "home",
    "feed",
    "discover",
    "explore",
    "search",
    "profile",
    "account",
    "settings",
    "notifications",
    "messages",
    "inbox",
    "activity",
    "saved",
    "favorites",
    "favourites",
    "cart",
    "basket",
    "menu",
    "more",
    "help",
    "support",
}

NAVIGATION_RESOURCE_TOKENS = {
    "nav",
    "navigation",
    "tab",
    "tabs",
    "bottom",
    "toolbar",
    "menu",
    "drawer",
    "section",
}

CONTENT_DESTINATION_TOKENS = {
    "article",
    "story",
    "post",
    "item",
    "product",
    "detail",
    "details",
    "card",
    "tile",
    "row",
    "cell",
    "category",
    "collection",
    "lesson",
    "course",
    "episode",
    "event",
    "store",
    "location",
    "map",
}

EXPANSION_TOKENS = {
    "more",
    "see all",
    "view all",
    "show all",
    "open",
    "expand",
    "details",
}


def is_progression_label(value: Any) -> bool:
    return _fold_label(value) in PROGRESSION_PHRASES


def is_defer_label(value: Any) -> bool:
    return _fold_label(value) in DEFER_PHRASES


def _is_auth_entry_label(value: Any) -> bool:
    return _has_phrase(_fold_label(value), AUTH_ENTRY_PHRASES)


def _is_app_navigation_label(value: Any) -> bool:
    return _has_phrase(_fold_label(value), APP_NAVIGATION_PHRASES)


def _is_content_card_label(value: Any) -> bool:
    folded = _fold_label(value)
    return _has_phrase(folded, CONTENT_CARD_PHRASES) or bool(re.search(r"\bdu\s+\d{1,2}\s+\w+\s+\d{4}\b", folded))


def _is_utility_action_label(value: Any) -> bool:
    return _has_phrase(_fold_label(value), UTILITY_ACTION_PHRASES)


def _resource_tail(value: Any) -> str:
    resource_id = _text(value).lower()
    if not resource_id:
        return ""
    return resource_id.split("/")[-1].split(":")[-1].replace("_", " ").replace("-", " ").strip()


def _semantic_blob(tappable: dict[str, Any]) -> str:
    parts = [
        _primary_label(tappable),
        _resource_tail(tappable.get("resource_id")),
        _text(tappable.get("class_name")).rsplit(".", 1)[-1],
        _text(tappable.get("control_role")),
    ]
    return _fold_label(" ".join(part for part in parts if part))


def _has_any_token(blob: str, tokens: set[str]) -> bool:
    padded = f" {blob} "
    return any(f" {token} " in padded or token in blob for token in tokens)


def _is_mutating_control_class(tappable: dict[str, Any]) -> bool:
    class_name = _text(tappable.get("class_name")).lower()
    role = _text(tappable.get("control_role")).lower()
    return any(token in class_name or token in role for token in ("switch", "checkbox", "checkedtextview", "toggle"))


def _is_generic_safe_navigation_target(tappable: dict[str, Any], context: dict[str, Any]) -> bool:
    blob = _semantic_blob(tappable)
    if not blob:
        return False
    if _is_bottom_navigation_target(tappable, context):
        return True
    if _has_any_token(blob, NAVIGATION_RESOURCE_TOKENS) and _has_any_token(blob, GENERIC_NAVIGATION_TOKENS):
        return True
    class_name = _text(tappable.get("class_name")).lower()
    if ("tab" in class_name or "menu" in class_name) and not _looks_like_generic_wrapper_label(_primary_label(tappable)):
        return True
    return False


def _is_generic_readonly_destination(tappable: dict[str, Any], context: dict[str, Any]) -> bool:
    label = _primary_label(tappable)
    if _looks_like_generic_wrapper_label(label):
        return False
    if _looks_like_step_progress_label(label):
        return False
    blob = _semantic_blob(tappable)
    if _has_any_token(blob, EXPANSION_TOKENS):
        return True
    if _has_any_token(blob, CONTENT_DESTINATION_TOKENS):
        return True
    role = _text(tappable.get("control_role"))
    surface_profile = _text(context.get("surface_profile") or context.get("screen_type"))
    if role in {"synthetic_text_target", "synthetic_card", "generic_card"}:
        return surface_profile in {
            "list_feed",
            "detail_screen",
            "scrollable_collection",
            "content_feed",
            "home_dashboard",
            "navigation_shell",
            "home_feed",
            "program_overview_screen",
        }
    if surface_profile in {"list_feed", "detail_screen", "scrollable_collection", "content_feed", "home_dashboard", "navigation_shell", "home_feed"}:
        word_count = len([part for part in _fold_label(label).split() if part])
        return 1 <= word_count <= 8 and len(label) <= 80
    return False


def _looks_like_generic_wrapper_label(value: Any) -> bool:
    return _fold_label(value) in {
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


def _has_richer_safe_peer(label: str, context: dict[str, Any]) -> bool:
    folded_label = _fold_label(label)
    for peer in context.get("available_labels") or []:
        folded_peer = _fold_label(peer)
        if not folded_peer or folded_peer == folded_label:
            continue
        if _is_app_navigation_label(peer) or _is_content_card_label(peer) or _is_utility_action_label(peer):
            return True
    return False


def _primary_label(tappable: dict[str, Any]) -> str:
    for key in ("label", "text", "content_desc", "hint_text"):
        value = _text(tappable.get(key))
        if value:
            return value
    resource_id = _text(tappable.get("resource_id"))
    if resource_id:
        tail = resource_id.split("/")[-1].split(":")[-1]
        return tail.replace("_", " ").replace("-", " ").strip()
    return ""


def _looks_like_step_progress_label(value: str) -> bool:
    normalized = _text(value).lower()
    if not normalized:
        return False
    if re.fullmatch(r"\d{1,2}", normalized):
        return True
    if re.fullmatch(r"0\.\d{4,}", normalized):
        return True
    return False


def _looks_like_onboarding_choice(label: str) -> bool:
    normalized = _text(label).lower()
    folded = _fold_label(label)
    if not normalized:
        return False
    if normalized in {"button", "action", "view", "imagebutton", "layout"}:
        return False
    if normalized in {"ft", "cm", "m", "in", "kg", "lb", "lbs", "year", "years", "yr", "yrs"}:
        return False
    if _looks_like_step_progress_label(normalized):
        return False
    if len(normalized) > 40:
        return False
    if any(token in folded for token in ("next", "continue", "skip", "back", "close", "dismiss", "later", "not now", "suivant", "continuer", "commencer", "passer", "ignorer")):
        return False
    if _is_auth_entry_label(label) or re.search(r"\b(buy|pay|checkout|subscribe)\b", folded):
        return False
    word_count = len([part for part in re.split(r"\s+", normalized) if part])
    return 1 <= word_count <= 5


def _apply_contextual_adjustments(
    normalized_label: str,
    base_safety_score: int,
    base_exploration_score: int,
    base_reason: str,
    base_selection_reason: str,
    context: dict[str, Any],
) -> tuple[int, int, str, str]:
    phase = _text(context.get("phase")) or "initial"
    surface_profile = _text(context.get("surface_profile") or context.get("screen_type"))

    safety_score = base_safety_score
    exploration_score = base_exploration_score
    reason = base_reason
    selection_reason = base_selection_reason

    folded_label = _fold_label(normalized_label)

    if phase == "modal_followup" and folded_label in {"close", "dismiss", "not now", "later", "cancel", "back", "plus tard", "pas maintenant"}:
        exploration_score += 12
        selection_reason = "preferred way to exit a transient modal and continue bounded exploration"

    if surface_profile == "home_dashboard" and folded_label in {"home", "dashboard"}:
        exploration_score = min(exploration_score, 12)
        selection_reason = "safe but likely redundant on the current home/dashboard surface"

    if surface_profile == "home_dashboard" and folded_label == "accueil":
        exploration_score = min(exploration_score, 10)
        selection_reason = "current home tab is safe but likely redundant on the dashboard"

    if surface_profile == "onboarding_screen" and is_progression_label(normalized_label):
        exploration_score += 8
        selection_reason = "useful bounded progression action on an onboarding screen"
    if surface_profile == "onboarding_screen" and folded_label == "next":
        exploration_score -= 10
        selection_reason = "progression control on onboarding, but lower priority than selecting an in-flow option"

    if surface_profile == "auth_screen" and is_defer_label(normalized_label):
        exploration_score += 10
        selection_reason = "preferred guest or defer path on an authentication gate"

    return safety_score, exploration_score, reason, selection_reason


def _looks_like_sparse_fullscreen_intro(tappable: dict[str, Any], context: dict[str, Any]) -> bool:
    bounds = tappable.get("bounds") or []
    screen_bounds = context.get("screen_bounds") or []
    if len(bounds) != 4 or len(screen_bounds) != 4:
        return False

    screen_width = max(0, screen_bounds[2] - screen_bounds[0])
    screen_height = max(0, screen_bounds[3] - screen_bounds[1])
    target_width = max(0, bounds[2] - bounds[0])
    target_height = max(0, bounds[3] - bounds[1])
    if screen_width <= 0 or screen_height <= 0:
        return False

    width_ratio = target_width / screen_width
    height_ratio = target_height / screen_height
    if width_ratio < 0.85 or height_ratio < 0.85:
        return False

    surface_profile = _text(context.get("surface_profile") or context.get("screen_type"))
    visible_text_count = int(context.get("visible_text_count") or 0)
    available_labels = context.get("available_labels") or []
    label = _primary_label(tappable).strip().lower()

    if surface_profile not in {"unknown", "onboarding_screen"}:
        return False
    if visible_text_count > 4:
        return False
    if len(available_labels) > 4:
        return False
    if re.search(r"\b(back|close|dismiss|delete|buy|pay|subscribe)\b", _fold_label(label)) or _is_auth_entry_label(label):
        return False
    return True


def _is_bottom_navigation_target(tappable: dict[str, Any], context: dict[str, Any]) -> bool:
    bounds = tappable.get("bounds") or []
    screen_bounds = context.get("screen_bounds") or []
    if len(bounds) != 4 or len(screen_bounds) != 4:
        return False
    screen_height = max(1, int(screen_bounds[3]) - int(screen_bounds[1]))
    target_top = int(bounds[1]) - int(screen_bounds[1])
    return target_top >= int(screen_height * 0.82)


def classify_tappable(tappable: dict[str, Any], context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    context = context or {}
    label = _primary_label(tappable)
    normalized_label = label.strip().lower()
    class_name = _text(tappable.get("class_name")).lower()
    resource_id = _text(tappable.get("resource_id"))
    phase = _text(context.get("phase")) or "initial"
    surface_profile = _text(context.get("surface_profile") or context.get("screen_type"))
    control_type = _text(tappable.get("control_type")) or "action"

    if not tappable.get("visible") or not tappable.get("enabled"):
        return {
            **tappable,
            "action_category": "disabled",
            "safe_action": "unsafe",
            "safe_reason": "not visible or not enabled",
            "safety_score": -100,
            "exploration_score": -100,
            "selection_score": -200,
        }

    if _looks_like_generic_wrapper_label(label):
        return {
            **tappable,
            "action_category": "generic_wrapper",
            "safe_action": "unknown",
            "safe_reason": "generic wrapper without a meaningful label is skipped in favor of explicit child controls",
            "safety_score": 0,
            "exploration_score": 0,
            "selection_score": 0,
        }

    if phase == "modal_followup":
        for pattern, base_safety_score, base_exploration_score, base_reason, base_selection_reason in MODAL_FOLLOWUP_SAFE_RULES:
            if pattern.search(label):
                safety_score, exploration_score, reason, selection_reason = _apply_contextual_adjustments(
                    normalized_label,
                    base_safety_score,
                    base_exploration_score,
                    base_reason,
                    base_selection_reason,
                    context,
                )
                return {
                    **tappable,
                    "action_category": "modal_followup",
                    "safe_action": "safe",
                    "safe_reason": reason,
                    "safety_score": safety_score,
                    "exploration_score": exploration_score,
                    "selection_score": safety_score + exploration_score,
                    "selection_reason": selection_reason,
                }

    if is_progression_label(label):
        base_exploration = 88 if is_defer_label(label) else 82
        safety_score, exploration_score, reason, selection_reason = _apply_contextual_adjustments(
            normalized_label,
            90,
            base_exploration,
            "bounded progression, defer, or guest-mode control",
            "advances onboarding or enters guest mode without submitting data or making a durable product change",
            context,
        )
        return {
            **tappable,
            "action_category": "progression",
            "safe_action": "safe",
            "safe_reason": reason,
            "safety_score": safety_score,
            "exploration_score": exploration_score,
            "selection_score": safety_score + exploration_score,
            "selection_reason": selection_reason,
        }

    if _is_auth_entry_label(label) and surface_profile not in {"auth_screen", "form_screen", "input_screen"}:
        exploration_score = 18 if _has_richer_safe_peer(label, context) else 42
        return {
            **tappable,
            "action_category": "auth_entry",
            "safe_action": "safe",
            "safe_reason": "bounded authentication entry destination",
            "safety_score": 76,
            "exploration_score": exploration_score,
            "selection_score": 76 + exploration_score,
            "selection_reason": (
                "auth entry is safe to inspect, but lower priority than normal home navigation"
                if exploration_score < 42
                else "opens the authentication or registration surface for inspection without entering credentials"
            ),
        }

    if _is_auth_entry_label(label):
        return {
            **tappable,
            "action_category": "auth_submit",
            "safe_action": "unsafe",
            "safe_reason": "auth submission is out of scope for bounded safe exploration",
            "safety_score": -85,
            "exploration_score": -85,
            "selection_score": -170,
        }

    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(label):
            return {
                **tappable,
                "action_category": "blocked",
                "safe_action": "unsafe",
                "safe_reason": reason,
                "safety_score": -95,
                "exploration_score": -95,
                "selection_score": -190,
            }

    if "edittext" in class_name:
        return {
            **tappable,
            "action_category": "text_entry",
            "safe_action": "unsafe",
            "safe_reason": "text entry is out of scope for bounded safe exploration",
            "safety_score": -90,
            "exploration_score": -90,
            "selection_score": -180,
        }

    if _is_mutating_control_class(tappable):
        return {
            **tappable,
            "action_category": "state_toggle",
            "safe_action": "unsafe",
            "safe_reason": "toggle, checkbox, or switch control can mutate app state",
            "safety_score": -88,
            "exploration_score": -88,
            "selection_score": -176,
        }

    if control_type == "slider":
        exploration_score = 72 if surface_profile in {"onboarding_screen", "form_screen"} else 44
        selection_reason = (
            "bounded slider adjustment helps reveal progressable app state"
            if surface_profile in {"onboarding_screen", "form_screen"}
            else "bounded slider adjustment is safe to probe once"
        )
        return {
            **tappable,
            "action_category": "slider_adjustment",
            "safe_action": "safe",
            "safe_reason": "bounded slider adjustment",
            "safety_score": 84,
            "exploration_score": exploration_score,
            "selection_score": 84 + exploration_score,
            "selection_reason": selection_reason,
        }

    if _is_app_navigation_label(label):
        safety_score, exploration_score, reason, selection_reason = _apply_contextual_adjustments(
            normalized_label,
            90,
            78,
            "safe app navigation destination",
            "opens a stable in-app section that helps map the product experience",
            context,
        )
        if surface_profile == "home_dashboard" and _is_bottom_navigation_target(tappable, context):
            folded_label = _fold_label(label)
            exploration_score = min(exploration_score, 44)
            selection_reason = "bottom navigation is safe, but lower priority than visible home actions and content cards"
            if folded_label in {"accueil", "home", "dashboard"}:
                exploration_score = min(exploration_score, 10)
                selection_reason = "current home tab is safe but likely redundant on the dashboard"
        return {
            **tappable,
            "action_category": "navigation",
            "safe_action": "safe",
            "safe_reason": reason,
            "safety_score": safety_score,
            "exploration_score": exploration_score,
            "selection_score": safety_score + exploration_score,
            "selection_reason": selection_reason,
        }

    if _is_generic_safe_navigation_target(tappable, context):
        return {
            **tappable,
            "action_category": "navigation",
            "safe_action": "safe",
            "safe_reason": "generic in-app navigation target",
            "safety_score": 88,
            "exploration_score": 74,
            "selection_score": 162,
            "selection_reason": "opens a likely in-app navigation destination inferred from position, role, label, or resource id",
        }

    if _is_content_card_label(label):
        return {
            **tappable,
            "action_category": "content_card",
            "safe_action": "safe",
            "safe_reason": "read-only content card or catalogue destination",
            "safety_score": 86,
            "exploration_score": 70,
            "selection_score": 156,
            "selection_reason": "opens promotional, catalogue, or detail content without an obvious commitment action",
        }

    if _is_generic_readonly_destination(tappable, context):
        return {
            **tappable,
            "action_category": "content_card",
            "safe_action": "safe",
            "safe_reason": "generic read-only content or detail destination",
            "safety_score": 82,
            "exploration_score": 62,
            "selection_score": 144,
            "selection_reason": "opens likely in-app content, detail, category, or expansion surface without an obvious commitment action",
        }

    if _is_utility_action_label(label):
        return {
            **tappable,
            "action_category": "utility_entry",
            "safe_action": "safe",
            "safe_reason": "bounded utility entry point",
            "safety_score": 80,
            "exploration_score": 78,
            "selection_score": 158,
            "selection_reason": "opens a utility screen for inspection; any camera, permission, or text-entry follow-up remains blocked",
        }

    if _looks_like_sparse_fullscreen_intro(tappable, context):
        return {
            **tappable,
            "action_category": "progression",
            "safe_action": "safe",
            "safe_reason": "sparse full-screen intro card",
            "safety_score": 82,
            "exploration_score": 76,
            "selection_score": 158,
            "selection_reason": "single full-screen intro/interstitial surface is likely a tap-to-continue step",
        }

    if phase == "modal_followup":
        for pattern, base_safety_score, base_exploration_score, base_reason, base_selection_reason in MODAL_FOLLOWUP_SAFE_RULES:
            if pattern.search(label):
                safety_score, exploration_score, reason, selection_reason = _apply_contextual_adjustments(
                    normalized_label,
                    base_safety_score,
                    base_exploration_score,
                    base_reason,
                    base_selection_reason,
                    context,
                )
                return {
                    **tappable,
                    "action_category": "modal_followup",
                    "safe_action": "safe",
                    "safe_reason": reason,
                    "safety_score": safety_score,
                    "exploration_score": exploration_score,
                    "selection_score": safety_score + exploration_score,
                    "selection_reason": selection_reason,
                }

    for pattern, base_safety_score, base_exploration_score, base_reason, base_selection_reason in SAFE_RULES:
        if pattern.search(label):
            safety_score, exploration_score, reason, selection_reason = _apply_contextual_adjustments(
                normalized_label,
                base_safety_score,
                base_exploration_score,
                base_reason,
                base_selection_reason,
                context,
            )
            return {
                **tappable,
                "action_category": (
                    "progression"
                    if is_progression_label(normalized_label)
                    else "navigation"
                ),
                "safe_action": "safe",
                "safe_reason": reason,
                "safety_score": safety_score,
                "exploration_score": exploration_score,
                "selection_score": safety_score + exploration_score,
                "selection_reason": selection_reason,
            }

    if surface_profile == "onboarding_screen" and _looks_like_onboarding_choice(label):
        return {
            **tappable,
            "action_category": "onboarding_choice",
            "safe_action": "safe",
            "safe_reason": "bounded onboarding choice selection",
            "safety_score": 86,
            "exploration_score": 84,
            "selection_score": 170,
            "selection_reason": "selecting an onboarding option helps progress into the actual product experience",
        }

    for pattern, reason in UNSAFE_PATTERNS:
        if pattern.search(label):
            return {
                **tappable,
                "action_category": "unsafe_pattern",
                "safe_action": "unsafe",
                "safe_reason": reason,
                "safety_score": -80,
                "exploration_score": -80,
                "selection_score": -160,
            }

    for pattern, reason in UNSAFE_RESOURCE_PATTERNS:
        if pattern.search(resource_id):
            return {
                **tappable,
                "action_category": "unsafe_resource",
                "safe_action": "unsafe",
                "safe_reason": reason,
                "safety_score": -75,
                "exploration_score": -75,
                "selection_score": -150,
            }

    return {
        **tappable,
        "action_category": "unknown",
        "safe_action": "unknown",
        "safe_reason": (
            "does not match the modal follow-up allowlist"
            if phase == "modal_followup"
            else "does not match the bounded safe exploration allowlist"
        ),
        "safety_score": 0,
        "exploration_score": 0,
        "selection_score": 0,
    }


def classify_tappables(tappables: list[dict[str, Any]], context: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    return [classify_tappable(tappable, context=context) for tappable in tappables]


def rank_safe_tappables(tappables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_candidates = [tappable for tappable in tappables if tappable.get("safe_action") == "safe"]
    safe_candidates.sort(
        key=lambda item: (
            int(item.get("selection_score") or 0),
            int(item.get("exploration_score") or 0),
            int(item.get("safety_score") or 0),
            len(_primary_label(item)),
            -int(bool(item.get("resource_id"))),
        ),
        reverse=True,
    )
    return safe_candidates


def choose_best_safe_tappable(tappables: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    ranked = rank_safe_tappables(tappables)
    if not ranked:
        return None
    return ranked[0]
