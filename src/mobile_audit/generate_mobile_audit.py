from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from src.gtm_audit.common import AXIS_DEFINITIONS, AXIS_IMPACT, AXIS_USER_IMPACT, clean_text, mean, score_to_severity
from src.gtm_audit.generate_screenshot_gtm_audit import (
    _build_axes,
    _recommendations,
    _run_text_refinement,
    _top_priorities,
)
from src.gtm_audit.vision_client import run_gtm_vision_review
from src.mobile_audit.safe_actions import is_defer_label, is_progression_label
from src.mobile_audit.screen_classifier import classify_screen


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT_DIR / "shared" / "generated" / "mobile_gtm_audit.json"

MOBILE_SOURCES = [
    {
        "name": "Android Core App Quality",
        "url": "https://developer.android.com/tools/testing/what_to_test",
        "principles": [
            "navigate all screens, dialogs, settings, and user flows",
            "support standard back and gesture navigation",
            "preserve user/app state across app switches and back navigation",
            "use readable text, accessible touch targets, contrast, and content descriptions",
        ],
    },
    {
        "name": "Android Accessibility",
        "url": "https://developer.android.com/guide/topics/ui/accessibility/apps.html",
        "principles": [
            "interactive elements should have at least 48dp by 48dp focusable/touch target area",
            "text contrast should meet 4.5:1 for small text and 3:1 for large text",
            "non-text UI elements need meaningful content descriptions",
        ],
    },
    {
        "name": "Material Design Accessibility",
        "url": "https://m1.material.io/usability/accessibility.html",
        "principles": [
            "touch targets should be at least 48dp by 48dp",
            "touch targets should normally have 8dp or more spacing",
            "related controls and values should be grouped closely",
        ],
    },
    {
        "name": "Apple Human Interface Guidelines - Accessibility",
        "url": "https://developer.apple.com/design/human-interface-guidelines/accessibility",
        "principles": [
            "controls need comfortable minimum sizes and spacing",
            "interfaces should support familiar, simple interactions and alternatives to gestures",
            "screens should reduce cognitive load and avoid time-boxed or fast-moving UI where possible",
        ],
    },
    {
        "name": "WCAG Target Size",
        "url": "https://www.w3.org/WAI/WCAG21/Understanding/target-size",
        "principles": [
            "custom pointer targets should be at least 44 by 44 CSS pixels unless an exception applies",
        ],
    },
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def to_path(raw: str, default: Path) -> Path:
    value = clean_text(raw)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _bounds_size(bounds: Any) -> tuple[int, int]:
    if not isinstance(bounds, list) or len(bounds) != 4:
        return 0, 0
    return max(0, _safe_int(bounds[2]) - _safe_int(bounds[0])), max(0, _safe_int(bounds[3]) - _safe_int(bounds[1]))


def _bounds_center(bounds: Any) -> tuple[float, float]:
    if not isinstance(bounds, list) or len(bounds) != 4:
        return 0.0, 0.0
    return (_safe_int(bounds[0]) + _safe_int(bounds[2])) / 2, (_safe_int(bounds[1]) + _safe_int(bounds[3])) / 2


def _screen_width(screen: dict[str, Any]) -> int:
    bounds = ((screen.get("meta") or {}).get("screen_bounds_union") or [0, 0, 1080, 2280])
    width, _height = _bounds_size(bounds)
    return width or 1080


def _dp_to_px(screen: dict[str, Any], dp: int) -> int:
    # Appium bounds are physical pixels. Estimate the density from a 360dp compact-phone width.
    return max(dp, round(dp * (_screen_width(screen) / 360.0)))


def _screenshot_path(output_dir: Path, screen: dict[str, Any]) -> str:
    raw = clean_text(screen.get("screenshot_path"))
    if not raw:
        return ""
    path = output_dir / raw
    return str(path if path.exists() else Path(raw))


def _screen_label(screen: dict[str, Any]) -> str:
    return clean_text(screen.get("screen_title_guess")) or clean_text(screen.get("screen_id")) or "Mobile screen"


def _screenshot_index_for_screen(screen_id: str, screenshots: list[dict[str, Any]]) -> int:
    for item in screenshots:
        if clean_text(item.get("screen_id")) == clean_text(screen_id):
            return _safe_int(item.get("screenshot_index"), 0)
    return 0


def _visual_region_from_bounds(bounds: Any, screen: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(bounds, list) or len(bounds) != 4:
        return None
    union = ((screen.get("meta") or {}).get("screen_bounds_union") or [0, 0, 1080, 2280])
    screen_w, screen_h = _bounds_size(union)
    if screen_w <= 0 or screen_h <= 0:
        return None
    x = max(0.0, min(1.0, _safe_int(bounds[0]) / screen_w))
    y = max(0.0, min(1.0, _safe_int(bounds[1]) / screen_h))
    width = max(0.02, min(1.0, (_safe_int(bounds[2]) - _safe_int(bounds[0])) / screen_w))
    height = max(0.02, min(1.0, (_safe_int(bounds[3]) - _safe_int(bounds[1])) / screen_h))
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "width": round(width, 4),
        "height": round(height, 4),
        "coordinate_system": "normalized_0_1",
        "description": "Affected mobile UI element bounds",
    }


def _issue(
    *,
    axis_id: str,
    title: str,
    severity: str,
    confidence: float,
    screen: dict[str, Any],
    screenshots: list[dict[str, Any]],
    visible_signals: list[str],
    reason: str,
    evidence: str,
    recommendation: str,
    bounds: Any = None,
) -> dict[str, Any]:
    return {
        "axis_id": axis_id,
        "title": title,
        "severity": severity,
        "confidence": confidence,
        "page_name": _screen_label(screen),
        "page_url": clean_text(screen.get("activity_name")),
        "screenshot_index": _screenshot_index_for_screen(clean_text(screen.get("screen_id")), screenshots),
        "visible_signals": visible_signals[:4],
        "reason": reason,
        "evidence": evidence[:260],
        "commercial_risk": f"This can reduce activation, comprehension, or completion confidence on {_screen_label(screen)}.",
        "recommendation": recommendation,
        "visual_region": _visual_region_from_bounds(bounds, screen),
        "outside_workbook": True,
    }


def _is_generic_label(value: Any) -> bool:
    return clean_text(value).lower() in {"", "view", "button", "imagebutton", "action", "layout", "framelayout", "linearlayout"}


def _screen_type(screen: dict[str, Any]) -> str:
    return clean_text((screen.get("semantic") or {}).get("screen_type")) or clean_text((screen.get("meta") or {}).get("screen_type"))


def _primary_tappable_label(tappable: dict[str, Any]) -> str:
    return clean_text(
        tappable.get("label")
        or tappable.get("text")
        or tappable.get("content_desc")
        or tappable.get("hint_text")
        or tappable.get("resource_id")
    )


def _is_system_or_wrapper_tappable(tappable: dict[str, Any]) -> bool:
    role = clean_text(tappable.get("control_role")).lower()
    if role == "system_back":
        return True
    if role == "generic_wrapper" and not clean_text(tappable.get("resource_id")):
        return True
    label = clean_text(tappable.get("label")).lower()
    class_name = clean_text(tappable.get("class_name")).lower()
    bounds = tappable.get("bounds") or []
    if isinstance(bounds, list) and len(bounds) == 4:
        width, height = _bounds_size(bounds)
        is_top_left = _safe_int(bounds[0]) <= 80 and _safe_int(bounds[1]) <= 180 and width <= 180 and height <= 180
        if is_top_left and label in {"", "button", "imagebutton", "view"} and "button" in class_name:
            return True
    if role == "" and _is_generic_label(label) and not clean_text(tappable.get("resource_id")) and class_name in {"android.view.view", "android.widget.linearlayout"}:
        return True
    return False


def _auditworthy_tappables(screen: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in screen.get("tappables") or []
        if item.get("visible")
        and item.get("enabled")
        and not _is_system_or_wrapper_tappable(item)
    ]


def _deterministic_mobile_issues(screens: list[dict[str, Any]], interactions: list[dict[str, Any]], screenshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    onboarding_screens = [screen for screen in screens if _screen_type(screen) == "onboarding_screen"]
    first_non_onboarding_index = next(
        (
            index
            for index, screen in enumerate(screens)
            if _screen_type(screen) not in {
                "intro_landing",
                "onboarding_screen",
                "proof_interstitial",
                "prediction_interstitial",
                "coaching_interstitial",
            }
        ),
        len(screens),
    )
    progression_taps = [
        item
        for item in interactions
        if is_progression_label((item.get("result_details") or {}).get("trigger_label"))
        and not is_defer_label((item.get("result_details") or {}).get("trigger_label"))
    ]
    skip_or_defer_seen = any(
        is_defer_label((item.get("result_details") or {}).get("trigger_label"))
        for item in interactions
    ) or any(
        any(is_defer_label(tappable.get("label")) for tappable in screen.get("tappables") or [])
        for screen in screens[: max(first_non_onboarding_index, 1)]
    )
    no_action_screens = [
        screen
        for screen in screens
        if not _auditworthy_tappables(screen)
        and _screen_type(screen) not in {"modal_surface", "menu_surface"}
    ]

    if len(onboarding_screens) >= 12:
        screen = screens[min(len(screens) - 1, max(0, first_non_onboarding_index - 1))]
        visible_signals = [
            f"{len(onboarding_screens)} onboarding screens captured",
            f"{len(progression_taps)} progression taps recorded",
        ]
        if not skip_or_defer_seen:
            visible_signals.append("No skip/defer route detected")
        visible_signals.append(_screen_label(screen))
        issues.append(
            _issue(
                axis_id="task_execution",
                title="Forced onboarding path delays product activation",
                severity="medium" if len(onboarding_screens) < 25 else "high",
                confidence=0.88,
                screen=screen,
                screenshots=screenshots,
                visible_signals=visible_signals,
                reason="The live mobile scan captured a long first-run questionnaire before the app reached usable product content, which can postpone perceived value and increase drop-off risk.",
                evidence=(
                    f"onboarding_screen_count={len(onboarding_screens)}; "
                    f"screens_before_first_non_onboarding={first_non_onboarding_index}; "
                    f"progression_tap_count={len(progression_taps)}; "
                    f"skip_or_defer_detected={skip_or_defer_seen}"
                ),
                recommendation="Shorten the first-run path, add a skip/defer route, or batch optional questions after the user reaches the core product.",
            )
        )

    for screen in screens:
        tappables = _auditworthy_tappables(screen)
        if not tappables:
            continue
        min_touch_px = _dp_to_px(screen, 48)
        small = []
        unlabeled = []
        for tappable in tappables:
            width, height = _bounds_size(tappable.get("bounds"))
            if width and height and (width < min_touch_px or height < min_touch_px):
                small.append(tappable)
            class_name = clean_text(tappable.get("class_name")).lower()
            has_explicit_semantics = bool(clean_text(tappable.get("resource_id")) or clean_text(tappable.get("content_desc")))
            if (
                _is_generic_label(tappable.get("label"))
                and has_explicit_semantics
                and any(token in class_name for token in ("image", "view", "button"))
            ):
                unlabeled.append(tappable)
        if small:
            sample = small[0]
            width, height = _bounds_size(sample.get("bounds"))
            issues.append(
                _issue(
                    axis_id="trust_accessibility",
                    title="Small touch target risk",
                    severity="medium",
                    confidence=0.78,
                    screen=screen,
                    screenshots=screenshots,
                    visible_signals=[f"{len(small)} small candidate targets", f"target={_primary_tappable_label(sample) or sample.get('element_id')}"],
                    reason="At least one enabled mobile control appears smaller than the Android/Material 48dp target guidance after density estimation.",
                    evidence=f"{_primary_tappable_label(sample) or sample.get('element_id')} bounds={sample.get('bounds')} size={width}x{height}px estimated_min={min_touch_px}px",
                    recommendation="Increase the tappable area with padding/min size, or wrap the visible control in a larger touch target.",
                    bounds=sample.get("bounds"),
                )
            )
        if unlabeled:
            sample = unlabeled[0]
            issues.append(
                _issue(
                    axis_id="trust_accessibility",
                    title="Ambiguous or unlabeled interactive control",
                    severity="medium",
                    confidence=0.72,
                    screen=screen,
                    screenshots=screenshots,
                    visible_signals=[f"{len(unlabeled)} generic-label controls", f"class={clean_text(sample.get('class_name'))}"],
                    reason="A visible enabled control exposes only a generic label despite having explicit control semantics, which can weaken screen-reader output and make automated interaction evidence less reliable.",
                    evidence=f"element={sample.get('element_id')} label={sample.get('label')} class={sample.get('class_name')} bounds={sample.get('bounds')}",
                    recommendation="Add a user-facing label or Android contentDescription that describes the control purpose.",
                    bounds=sample.get("bounds"),
                )
            )

        centers = [(item, *_bounds_center(item.get("bounds"))) for item in tappables if isinstance(item.get("bounds"), list)]
        min_gap_px = _dp_to_px(screen, 8)
        cramped_pair: tuple[dict[str, Any], dict[str, Any], float] | None = None
        for index, (left_item, left_x, left_y) in enumerate(centers):
            for right_item, right_x, right_y in centers[index + 1 :]:
                distance = ((left_x - right_x) ** 2 + (left_y - right_y) ** 2) ** 0.5
                if 0 < distance < min_gap_px:
                    cramped_pair = (left_item, right_item, distance)
                    break
            if cramped_pair:
                break
        if cramped_pair:
            left_item, right_item, distance = cramped_pair
            issues.append(
                _issue(
                    axis_id="trust_accessibility",
                    title="Adjacent controls may be too close for reliable touch",
                    severity="low",
                    confidence=0.66,
                    screen=screen,
                    screenshots=screenshots,
                    visible_signals=[
                        clean_text(left_item.get("label")) or clean_text(left_item.get("element_id")),
                        clean_text(right_item.get("label")) or clean_text(right_item.get("element_id")),
                    ],
                    reason="Two enabled controls appear closer than the Material 8dp spacing guidance after density estimation.",
                    evidence=f"estimated_center_gap={round(distance, 1)}px estimated_min_gap={min_gap_px}px",
                    recommendation="Increase spacing or merge related controls so adjacent touch areas do not compete.",
                    bounds=left_item.get("bounds"),
                )
            )

    for screen in no_action_screens[:3]:
        visible = [clean_text(value) for value in screen.get("visible_text") or [] if clean_text(value)]
        if len(visible) >= 3:
            issues.append(
                _issue(
                    axis_id="flow_architecture",
                    title="Screen exposes content but no accessible actions",
                    severity="medium",
                    confidence=0.74,
                    screen=screen,
                    screenshots=screenshots,
                    visible_signals=visible[:3],
                    reason="The extracted hierarchy shows readable app content but no enabled tappable or focusable controls, which can block scan coverage and may signal inaccessible Compose semantics.",
                    evidence=f"screen_id={screen.get('screen_id')} tappable_count=0 visible_text_count={len(visible)}",
                    recommendation="Expose card/button semantics through accessibility roles, labels, and focusable/tappable bounds so users and automated scanners can traverse the screen.",
                )
            )

    error_interactions = [item for item in interactions if clean_text(item.get("result")) == "error"]
    if error_interactions:
        source_id = clean_text(error_interactions[0].get("source_screen_id"))
        screen = next((item for item in screens if clean_text(item.get("screen_id")) == source_id), screens[-1] if screens else {})
        issues.append(
            _issue(
                axis_id="ui_consistency",
                title="Interaction recovery errors occurred during safe exploration",
                severity="medium",
                confidence=0.7,
                screen=screen,
                screenshots=screenshots,
                visible_signals=[clean_text(error_interactions[0].get("notes"))[:120]],
                reason="The scanner recorded at least one error while attempting a bounded safe interaction.",
                evidence=f"error_interaction_count={len(error_interactions)}",
                recommendation="Review the affected control for unstable state transitions, overlays, or custom gesture handling.",
            )
        )

    return issues


def _refresh_screen_semantics(screens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        screen_copy = dict(screen)
        elements = screen_copy.get("elements") if isinstance(screen_copy.get("elements"), list) else []
        visible_text = screen_copy.get("visible_text") if isinstance(screen_copy.get("visible_text"), list) else []
        meta = dict(screen_copy.get("meta") or {})
        semantic = classify_screen(
            elements=elements,
            visible_text=visible_text,
            meta=meta,
            package_name=clean_text(screen_copy.get("package_name")),
            activity_name=clean_text(screen_copy.get("activity_name")),
            screen_title_guess=clean_text(screen_copy.get("screen_title_guess")),
        )
        meta.update(
            {
                "screen_type": semantic["screen_type"],
                "ui_patterns": semantic["ui_patterns"],
                "interaction_model": semantic["interaction_model"],
                "content_density": semantic["content_density"],
                "navigation_complexity": semantic["navigation_complexity"],
                "ux_signals": semantic["ux_signals"],
                "classifier_signals": semantic["signals"],
            }
        )
        screen_copy["semantic"] = semantic
        screen_copy["meta"] = meta
        refreshed.append(screen_copy)
    return refreshed


def _screen_metadata(output_dir: Path, screens: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    out = []
    for index, screen in enumerate(screens[:limit]):
        screenshot_path = _screenshot_path(output_dir, screen)
        out.append(
            {
                "screenshot_index": index,
                "screen_id": clean_text(screen.get("screen_id")),
                "page_name": _screen_label(screen),
                "page_url": clean_text(screen.get("activity_name")),
                "title": _screen_label(screen),
                "source_type": "live_android_app_screen",
                "file_name": Path(screenshot_path).name if screenshot_path else "",
                "image_width": _safe_int(((screen.get("meta") or {}).get("screen_bounds_union") or [0, 0, 0, 0])[2]),
                "image_height": _safe_int(((screen.get("meta") or {}).get("screen_bounds_union") or [0, 0, 0, 0])[3]),
                "reason": "Captured during Appium live mobile exploration",
                "screenshot_path": screenshot_path,
                "visible_text": [clean_text(value) for value in (screen.get("visible_text") or [])[:8] if clean_text(value)],
                "screen_type": clean_text((screen.get("semantic") or {}).get("screen_type")) or clean_text((screen.get("meta") or {}).get("screen_type")),
            }
        )
    return out


def _mobile_vision_screen_limit() -> int:
    raw = clean_text(os.getenv("MOBILE_VISION_SCREEN_LIMIT") or os.getenv("GTM_VISION_SCREEN_LIMIT"))
    try:
        return max(4, min(24, int(raw)))
    except Exception:
        return 14


def _focus_screenshots(screenshots: list[dict[str, Any]], issues: list[dict[str, Any]], max_count: int | None = None) -> list[dict[str, Any]]:
    max_count = max_count or _mobile_vision_screen_limit()
    indexes: list[int] = []
    if screenshots:
        indexes.append(0)
        indexes.append(len(screenshots) - 1)
    for issue in issues:
        indexes.append(_safe_int(issue.get("screenshot_index"), 0))
    wanted_screen_types = {
        "intro_landing",
        "onboarding_screen",
        "proof_interstitial",
        "prediction_interstitial",
        "coaching_interstitial",
        "result_summary",
        "paywall_or_offer",
        "blocked_no_actions",
    }
    seen_types: set[str] = set()
    for index, screenshot in enumerate(screenshots):
        screen_type = clean_text(screenshot.get("screen_type"))
        if screen_type in wanted_screen_types and screen_type not in seen_types:
            indexes.append(index)
            seen_types.add(screen_type)
    if len(screenshots) > 4:
        indexes.extend([len(screenshots) // 3, (len(screenshots) * 2) // 3])

    selected = []
    seen = set()
    for index in indexes:
        if index < 0 or index >= len(screenshots) or index in seen:
            continue
        seen.add(index)
        if clean_text(screenshots[index].get("screenshot_path")):
            selected.append(screenshots[index])
        if len(selected) >= max_count:
            break
    return selected


def _screen_type_counts(screens: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(_screen_type(screen) or "unknown" for screen in screens))


def _is_system_package(package_name: str) -> bool:
    package_name = clean_text(package_name).lower()
    if not package_name:
        return True
    prefixes = (
        "android",
        "com.android.",
        "com.google.android.",
        "com.sec.android.",
        "com.samsung.android.",
    )
    return any(package_name == prefix.rstrip(".") or package_name.startswith(prefix) for prefix in prefixes)


def _humanize_app_package(package_name: str) -> str:
    tail = clean_text(package_name).split(".")[-1]
    if not tail:
        return "Android App Audit"
    if tail.lower() == "mymg":
        return "MyMG"
    words = re.sub(r"[_-]+", " ", tail).strip()
    return " ".join(part.capitalize() for part in words.split()) or "Android App Audit"


def _infer_mobile_app_identity(app_info: dict[str, Any], screens: list[dict[str, Any]], app_label: str) -> dict[str, str]:
    configured_package = clean_text(app_info.get("appPackage"))
    configured_activity = clean_text(app_info.get("appActivity"))
    package_counts: dict[str, int] = {}
    activity_counts: dict[str, int] = {}
    for screen in screens:
        package_name = clean_text(screen.get("package_name"))
        activity_name = clean_text(screen.get("activity_name"))
        if package_name and not _is_system_package(package_name):
            package_counts[package_name] = package_counts.get(package_name, 0) + 1
            if activity_name:
                activity_counts[activity_name] = activity_counts.get(activity_name, 0) + 1

    inferred_package = max(package_counts.items(), key=lambda item: item[1])[0] if package_counts else ""
    inferred_activity = max(activity_counts.items(), key=lambda item: item[1])[0] if activity_counts else ""
    configured_package_is_app = bool(configured_package and not _is_system_package(configured_package))
    package_name = configured_package if configured_package_is_app else inferred_package or configured_package
    activity_name = configured_activity if configured_package_is_app else inferred_activity or configured_activity
    label = clean_text(app_label)
    if label.lower() in {"", "android app audit", "mainactivity", "main activity", "launcheractivity", "splashactivity"}:
        label = _humanize_app_package(package_name)
    return {
        "package": package_name or "live-android-app",
        "activity": activity_name,
        "label": label or _humanize_app_package(package_name),
    }


def _screen_journey_sample(screens: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    sample = []
    for index, screen in enumerate(screens[:limit]):
        visible_text = [clean_text(value) for value in (screen.get("visible_text") or []) if clean_text(value)]
        tappables = [_primary_tappable_label(item) for item in _auditworthy_tappables(screen)]
        sample.append(
            {
                "index": index,
                "screen_id": clean_text(screen.get("screen_id")),
                "title": _screen_label(screen),
                "screen_type": _screen_type(screen) or "unknown",
                "activity": clean_text(screen.get("activity_name")),
                "visible_text": visible_text[:6],
                "primary_actions": [value for value in tappables if value][:6],
            }
        )
    return sample


def _mobile_journey_summary(
    screens: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
    deterministic_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    screen_types = [_screen_type(screen) or "unknown" for screen in screens]
    first_non_onboarding_index = next(
        (
            index
            for index, screen_type in enumerate(screen_types)
            if screen_type
            not in {
                "intro_landing",
                "onboarding_screen",
                "proof_interstitial",
                "prediction_interstitial",
                "coaching_interstitial",
            }
        ),
        None,
    )
    progression_taps = [
        item
        for item in interactions
        if is_progression_label((item.get("result_details") or {}).get("trigger_label"))
        and not is_defer_label((item.get("result_details") or {}).get("trigger_label"))
    ]
    defer_taps = [
        item
        for item in interactions
        if is_defer_label((item.get("result_details") or {}).get("trigger_label"))
    ]
    no_action_indexes = [
        index
        for index, screen in enumerate(screens)
        if not _auditworthy_tappables(screen) and _screen_type(screen) not in {"modal_surface", "menu_surface"}
    ]
    return {
        "screenTypeCounts": _screen_type_counts(screens),
        "firstNonOnboardingIndex": first_non_onboarding_index,
        "lastScreenType": screen_types[-1] if screen_types else "",
        "lastScreenTitle": _screen_label(screens[-1]) if screens else "",
        "paywallReached": any(screen_type == "paywall_or_offer" for screen_type in screen_types),
        "resultSummaryReached": any(screen_type == "result_summary" for screen_type in screen_types),
        "progressionTapCount": len(progression_taps),
        "skipOrDeferTapCount": len(defer_taps),
        "blockedOrNoActionScreenIndexes": no_action_indexes[:12],
        "deterministicIssueTitles": [clean_text(issue.get("title")) for issue in deterministic_issues if clean_text(issue.get("title"))],
        "screenJourneySample": _screen_journey_sample(screens),
    }


def _base_vision_result(issues: list[dict[str, Any]], screens: list[dict[str, Any]]) -> dict[str, Any]:
    issues_by_axis = {axis["id"]: [] for axis in AXIS_DEFINITIONS}
    for issue in issues:
        issues_by_axis.setdefault(clean_text(issue.get("axis_id")), []).append(issue)

    axes = {}
    for axis in AXIS_DEFINITIONS:
        axis_issues = issues_by_axis.get(axis["id"], [])
        if axis_issues:
            worst = min({"high": 35, "medium": 58, "low": 74}.get(clean_text(item.get("severity")).lower(), 64) for item in axis_issues)
            score = worst
            observation = f"{len(axis_issues)} mobile-specific issue(s) were detected for {axis['short_name']}."
        else:
            score = 82
            observation = f"No deterministic mobile-specific blocker was detected for {axis['short_name']} from the extracted hierarchy."
        axes[axis["id"]] = {
            "observation": observation,
            "what_is_working": [],
            "proof_points": [],
            "missing_context": "",
            "score": score,
            "severity": score_to_severity(score),
            "confidence": 0.72 if axis_issues else 0.55,
        }

    return {
        "site_summary": f"Live Android audit captured {len(screens)} screens and generated mobile-specific UX evidence.",
        "axes": axes,
        "priority_issues": issues[:8],
        "criteria_discoveries": issues[8:],
        "visual_trust_findings": [],
        "strengths": [],
        "market_positioning": "Use the live app evidence to reduce activation friction and strengthen mobile accessibility before scale.",
    }


def _merge_vision_results(base: dict[str, Any], vision: dict[str, Any]) -> dict[str, Any]:
    if not vision:
        return base
    merged = dict(base)
    merged["site_summary"] = clean_text(vision.get("site_summary")) or base.get("site_summary")
    merged["market_positioning"] = clean_text(vision.get("market_positioning")) or base.get("market_positioning")
    merged["strengths"] = list(vision.get("strengths") or []) or list(base.get("strengths") or [])
    merged["priority_issues"] = _dedupe_issue_list(list(base.get("priority_issues") or []) + list(vision.get("priority_issues") or []))
    merged["criteria_discoveries"] = _dedupe_issue_list(list(base.get("criteria_discoveries") or []) + list(vision.get("criteria_discoveries") or []))
    merged["visual_trust_findings"] = _dedupe_issue_list(list(base.get("visual_trust_findings") or []) + list(vision.get("visual_trust_findings") or []))
    axes = dict(base.get("axes") or {})
    for axis_id, axis_review in (vision.get("axes") or {}).items():
        if isinstance(axis_review, dict):
            axes[axis_id] = axis_review
    merged["axes"] = axes
    return merged


def _dedupe_issue_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            clean_text(item.get("axis_id")).lower(),
            clean_text(item.get("title") or item.get("criterion")).lower(),
            _safe_int(item.get("screenshot_index"), -1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _remap_vision_screenshot_indexes(vision_result: dict[str, Any], focus_screenshots: list[dict[str, Any]]) -> dict[str, Any]:
    if not vision_result:
        return vision_result

    index_map = {
        index: _safe_int(item.get("screenshot_index"), index)
        for index, item in enumerate(focus_screenshots)
    }
    remapped = dict(vision_result)
    for key in ("priority_issues", "criteria_discoveries", "visual_trust_findings"):
        items = []
        for issue in remapped.get(key) or []:
            if not isinstance(issue, dict):
                continue
            issue_copy = dict(issue)
            local_index = _safe_int(issue_copy.get("screenshot_index"), -1)
            if local_index in index_map:
                issue_copy["screenshot_index"] = index_map[local_index]
            items.append(issue_copy)
        remapped[key] = items
    return remapped


def _vision_enabled() -> bool:
    raw = clean_text(os.getenv("MOBILE_LIVE_SKIP_VISION") or os.getenv("GTM_SKIP_VISION") or "").lower()
    return raw not in {"1", "true", "yes", "on"}


def _llm_refinement_enabled() -> bool:
    raw = clean_text(
        os.getenv("MOBILE_LIVE_SKIP_LLM")
        or os.getenv("MOBILE_LIVE_SKIP_TEXT_REFINEMENT")
        or os.getenv("GTM_SKIP_LLM")
        or ""
    ).lower()
    return raw not in {"1", "true", "yes", "on"}


def build_payload(output_dir: Path, app_label: str = "Android App Audit") -> dict[str, Any]:
    app_info = load_json(output_dir / "app_info.json")
    extraction = load_json(output_dir / "mobile_ui_extraction.json")
    screens = extraction.get("screens") if isinstance(extraction.get("screens"), list) else []
    screens = _refresh_screen_semantics(screens)
    interactions = extraction.get("interactions") if isinstance(extraction.get("interactions"), list) else []
    screenshots = _screen_metadata(output_dir, screens)
    deterministic_issues = _deterministic_mobile_issues(screens, interactions, screenshots)
    journey_summary = _mobile_journey_summary(screens, interactions, deterministic_issues)
    app_identity = _infer_mobile_app_identity(app_info, screens, app_label)

    site_context = {
        "site": {
            "homepage": "",
            "domain": app_identity["package"],
            "display_name": app_identity["label"],
            "language": "",
        },
        "counts": {
            "pages": len(screens),
            "topLevelNavigation": "N/A",
            "navigationItems": len(interactions),
        },
        "source": "live Android Appium extraction",
        "experience_type": "mobile_app",
        "mobile_methodology_sources": MOBILE_SOURCES,
        "mobile_evidence_summary": {
            "screensCaptured": len(screens),
            "interactionsRecorded": len(interactions),
            "appPackage": app_identity["package"],
            "appActivity": app_identity["activity"],
            **journey_summary,
        },
        "mobile_ai_guidance": [
            "Treat the captured screens as one live mobile app journey, not isolated website pages.",
            "Use the screenJourneySample and screenTypeCounts to reason about coverage, activation friction, repeated onboarding steps, result/paywall arrival, and no-action screens.",
            "Use deterministicIssueTitles as scanner evidence to verify, sharpen, or de-prioritize findings, but do not invent issues that are not visible in screenshots or extraction metadata.",
            "Prefer mobile-specific recommendations: shorter first-run paths, clearer primary actions, accessible touch targets, content descriptions, stable back/navigation behavior, and complete result/paywall context.",
            "When VLM evidence and extraction evidence disagree, keep the finding lower-confidence and explain the missing context instead of overstating.",
        ],
        "visual_review_goal": "Evaluate the live Android app journey against the active GTM UX/UI axes plus mobile-specific touch, navigation, accessibility, activation, onboarding, result, and paywall expectations.",
    }

    base_result = _base_vision_result(deterministic_issues, screens)
    vision = {"enabled": False, "error": "", "result": base_result}
    focus_screenshots = _focus_screenshots(screenshots, deterministic_issues)
    if _vision_enabled() and focus_screenshots:
        vision = run_gtm_vision_review(site_context=site_context, screenshots=focus_screenshots)
    raw_vision_result = vision.get("result") if isinstance(vision.get("result"), dict) else {}
    vision_result = _merge_vision_results(base_result, _remap_vision_screenshot_indexes(raw_vision_result, focus_screenshots))
    vision["result"] = vision_result

    if _llm_refinement_enabled():
        text_refinement = _run_text_refinement(
            site_context=site_context,
            screenshots=focus_screenshots,
            vision_result=vision_result,
        )
    else:
        text_refinement = {"enabled": False, "error": "Disabled by MOBILE_LIVE_SKIP_LLM.", "result": None}
    if isinstance(text_refinement.get("result"), dict):
        vision_result = _merge_vision_results(base_result, text_refinement["result"])
        vision["result"] = vision_result
    vision["textRefinement"] = {
        "enabled": bool(text_refinement.get("enabled")),
        "model": clean_text(text_refinement.get("model")),
        "backend": clean_text(text_refinement.get("backend")),
        "error": clean_text(text_refinement.get("error")),
    }

    axes = _build_axes(vision_result, screenshots)
    overall_score = int(round(mean([axis["score"] for axis in axes], default=65.0)))
    strongest = max(axes, key=lambda axis: axis["score"], default=None)
    weakest = min(axes, key=lambda axis: axis["score"], default=None)
    priorities = _top_priorities(axes)

    return {
        "version": 1,
        "mode": "screenshot",
        "generator": "src.mobile_audit.generate_mobile_audit",
        "surfaceType": "mobile_app",
        "site": site_context["site"],
        "context": {
            "siteType": "Live Android mobile app audit",
            "pagesAudited": len(screens),
            "topLevelNavigation": "Live app exploration",
            "auditAxes": len(AXIS_DEFINITIONS),
            "approach": "Live Appium scan, structured hierarchy extraction, deterministic mobile heuristics, optional VLM review, LLM synthesis, and GTM report generation.",
        },
        "methodology": [
            {"step": "Scan", "description": "Appium launches the Android app and performs bounded safe navigation across in-app screens."},
            {"step": "Extract", "description": "The pipeline captures screenshots, XML hierarchies, screen semantics, tappables, and interaction outcomes."},
            {"step": "Analyze", "description": "Deterministic mobile checks evaluate touch targets, content descriptions, onboarding length, action availability, and navigation evidence before optional VLM/LLM synthesis."},
            {"step": "Report", "description": "The shared active-axis GTM report translates mobile evidence into prioritized UX/UI recommendations."},
        ],
        "mobileMethodologySources": MOBILE_SOURCES,
        "profile": site_context,
        "focusScreenshots": focus_screenshots[:10],
        "scannedPages": screenshots,
        "visionReview": vision,
        "aiDiscoveredFindings": priorities,
        "axes": axes,
        "executiveSummary": {
            "overallScore": overall_score,
            "strongestAxis": strongest,
            "weakestAxis": weakest,
            "summary": f"{site_context['site']['display_name']} captured {len(screens)} mobile screens and scores {overall_score}/100 on the live mobile GTM UX/UI audit.",
            "positioningHook": clean_text(vision_result.get("market_positioning")) or "Use the live app evidence to reduce activation friction and strengthen mobile accessibility before scale.",
            "topPriorities": priorities,
        },
        "recommendations": _recommendations(priorities),
        "artifacts": {
            "websiteMenu": "",
            "cleanedPath": str(output_dir / "mobile_ui_extraction.json"),
            "renderedPath": str(output_dir / "mobile_screen_map.json"),
            "checksPath": str(output_dir / "mobile_interaction_results.json"),
            "mobileOutputDir": str(output_dir),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a GTM mobile audit from live Android extraction artifacts.")
    parser.add_argument("--input-dir", required=True, help="Mobile extraction artifact directory.")
    parser.add_argument("--app-label", default="Android App Audit")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    input_dir = to_path(args.input_dir, Path(args.input_dir))
    if not input_dir.exists():
        raise FileNotFoundError(f"Mobile artifact directory not found: {input_dir}")
    payload = build_payload(input_dir, app_label=args.app_label)
    output_path = to_path(args.output, DEFAULT_OUTPUT)
    save_json(output_path, payload)
    print(f"Mobile GTM audit written to: {output_path}")


if __name__ == "__main__":
    main()
