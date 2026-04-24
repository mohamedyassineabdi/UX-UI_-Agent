from __future__ import annotations

import re
from typing import Any, Optional


SAFE_RULES: list[tuple[re.Pattern[str], int, int, str, str]] = [
    (
        re.compile(r"^(close|dismiss|not now|maybe later|later|cancel|back)$", re.IGNORECASE),
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
        re.compile(r"^(home|dashboard|overview|browse|explore)$", re.IGNORECASE),
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
        re.compile(r"^(close|dismiss|not now|later|cancel|back)$", re.IGNORECASE),
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
    (re.compile(r"\b(turn off|disable|delete|remove|erase|clear data|unsubscribe|deactivate)\b", re.IGNORECASE), "destructive or state-changing action"),
    (re.compile(r"\b(log out|logout|sign out)\b", re.IGNORECASE), "session-ending action"),
    (re.compile(r"\b(buy|purchase|checkout|pay|subscribe|confirm|place order)\b", re.IGNORECASE), "commerce or commitment action"),
    (re.compile(r"\b(save|apply|submit|send|post|publish|accept all|allow)\b", re.IGNORECASE), "commits a product or permission state change"),
    (re.compile(r"\b(sign in|log in|login|sign up|register|create account)\b", re.IGNORECASE), "auth or account-creation action is out of scope for bounded safe exploration"),
]

UNSAFE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"search", re.IGNORECASE), "search field or search action"),
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
    if not normalized:
        return False
    if normalized in {"button", "action", "view", "imagebutton", "layout"}:
        return False
    if _looks_like_step_progress_label(normalized):
        return False
    if len(normalized) > 40:
        return False
    if any(token in normalized for token in ("next", "continue", "skip", "back", "close", "dismiss", "later", "not now")):
        return False
    if re.search(r"\b(sign in|log in|login|sign up|register|create account|buy|pay|checkout|subscribe)\b", normalized):
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

    if phase == "modal_followup" and normalized_label in {"close", "dismiss", "not now", "later", "cancel", "back"}:
        exploration_score += 12
        selection_reason = "preferred way to exit a transient modal and continue bounded exploration"

    if surface_profile == "home_dashboard" and normalized_label in {"home", "dashboard"}:
        exploration_score = min(exploration_score, 12)
        selection_reason = "safe but likely redundant on the current home/dashboard surface"

    if surface_profile == "onboarding_screen" and normalized_label in {"next", "continue", "get started", "skip"}:
        exploration_score += 8
        selection_reason = "useful bounded progression action on an onboarding screen"
    if surface_profile == "onboarding_screen" and normalized_label == "next":
        exploration_score -= 10
        selection_reason = "progression control on onboarding, but lower priority than selecting an in-flow option"

    if surface_profile == "auth_screen" and normalized_label in {"continue as guest", "browse as guest", "not now"}:
        exploration_score += 10
        selection_reason = "preferred guest or defer path on an authentication gate"

    return safety_score, exploration_score, reason, selection_reason


def classify_tappable(tappable: dict[str, Any], context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    context = context or {}
    label = _primary_label(tappable)
    normalized_label = label.strip().lower()
    class_name = _text(tappable.get("class_name")).lower()
    resource_id = _text(tappable.get("resource_id"))
    phase = _text(context.get("phase")) or "initial"
    surface_profile = _text(context.get("surface_profile") or context.get("screen_type"))

    if not tappable.get("visible") or not tappable.get("enabled"):
        return {
            **tappable,
            "safe_action": "unsafe",
            "safe_reason": "not visible or not enabled",
            "safety_score": -100,
            "exploration_score": -100,
            "selection_score": -200,
        }

    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(label):
            return {
                **tappable,
                "safe_action": "unsafe",
                "safe_reason": reason,
                "safety_score": -95,
                "exploration_score": -95,
                "selection_score": -190,
            }

    if "edittext" in class_name:
        return {
            **tappable,
            "safe_action": "unsafe",
            "safe_reason": "text entry is out of scope for bounded safe exploration",
            "safety_score": -90,
            "exploration_score": -90,
            "selection_score": -180,
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
                "safe_action": "unsafe",
                "safe_reason": reason,
                "safety_score": -75,
                "exploration_score": -75,
                "selection_score": -150,
            }

    return {
        **tappable,
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
