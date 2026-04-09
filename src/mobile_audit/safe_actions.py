from __future__ import annotations

import re
from typing import Any, Optional


SAFE_RULES: list[tuple[re.Pattern[str], int, int, str, str]] = [
    (re.compile(r"^options for discover$", re.IGNORECASE), 95, 88, "bounded menu control", "reveals bounded discover options"),
    (re.compile(r"^update available\.\s*more options$", re.IGNORECASE), 94, 84, "bounded update menu control", "reveals bounded chrome options"),
    (
        re.compile(r"^\d+\s+open\s+tabs?(?:,\s*tap to switch tabs)?$", re.IGNORECASE),
        96,
        82,
        "tab switcher control",
        "opens a contained tab-management surface",
    ),
    (re.compile(r"^home$", re.IGNORECASE), 100, 8, "home navigation control", "safe but often a no-op on the current home surface"),
]

MODAL_FOLLOWUP_SAFE_RULES: list[tuple[re.Pattern[str], int, int, str, str]] = [
    (
        re.compile(r"^learn more$", re.IGNORECASE),
        92,
        91,
        "bounded follow-up action inside a compact menu",
        "opens explanatory content without immediately changing product state",
    ),
]

BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^turn off$", re.IGNORECASE), "changes product state and is blocked for mini Block 3"),
    (re.compile(r"\bturn off\b", re.IGNORECASE), "changes product state and is blocked for mini Block 3"),
]

UNSAFE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"search", re.IGNORECASE), "search field or search action"),
    (re.compile(r"voice search", re.IGNORECASE), "voice input action"),
    (re.compile(r"\bshare\b", re.IGNORECASE), "share action"),
    (re.compile(r"\bfacebook\b|\byoutube\b|\binstagram\b|\btiktok\b|\bx\b", re.IGNORECASE), "content or external destination"),
    (re.compile(r"\bnews\b|\barticle\b|\bstory\b|\bdiscover\b", re.IGNORECASE), "content navigation surface"),
]

UNSAFE_RESOURCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"edit|search|mic|voice", re.IGNORECASE), "search or text-entry control"),
    (re.compile(r"share|send", re.IGNORECASE), "sharing control"),
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_label(tappable: dict[str, Any]) -> str:
    return _primary_label(tappable).strip().lower()


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


def classify_tappable(tappable: dict[str, Any], context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    context = context or {}
    label = _primary_label(tappable)
    normalized_label = label.strip().lower()
    class_name = _text(tappable.get("class_name")).lower()
    resource_id = _text(tappable.get("resource_id"))
    phase = _text(context.get("phase")) or "initial"

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
            "safe_reason": "text input is out of scope for Block 2",
            "safety_score": -90,
            "exploration_score": -90,
            "selection_score": -180,
        }

    if phase == "modal_followup":
        for pattern, safety_score, exploration_score, reason, exploration_reason in MODAL_FOLLOWUP_SAFE_RULES:
            if pattern.search(label):
                return {
                    **tappable,
                    "safe_action": "safe",
                    "safe_reason": reason,
                    "safety_score": safety_score,
                    "exploration_score": exploration_score,
                    "selection_score": safety_score + exploration_score,
                    "selection_reason": exploration_reason,
                }

        if normalized_label in {"home", "options for discover", "update available. more options"}:
            return {
                **tappable,
                "safe_action": "unknown",
                "safe_reason": "already inside a bounded menu; chrome controls are deprioritized for the modal follow-up step",
                "safety_score": 5,
                "exploration_score": -30,
                "selection_score": -25,
            }

    for pattern, safety_score, exploration_score, reason, exploration_reason in SAFE_RULES:
        if pattern.search(label):
            return {
                **tappable,
                "safe_action": "safe",
                "safe_reason": reason,
                "safety_score": safety_score,
                "exploration_score": exploration_score,
                "selection_score": safety_score + exploration_score,
                "selection_reason": exploration_reason,
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
            "does not match the mini Block 3 modal allowlist"
            if phase == "modal_followup"
            else "does not match the strict Block 2 allowlist"
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
