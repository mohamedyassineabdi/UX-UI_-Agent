from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from appium.webdriver.webdriver import WebDriver

from .mobile_runner import MobileRunner
from .safe_actions import classify_tappables, is_defer_label, is_progression_label, rank_safe_tappables


@dataclass(slots=True)
class ExplorerConfig:
    max_screens: int = 80
    max_actions_total: int = 192
    max_actions_per_screen: int = 8
    max_scrolls_per_path: int = 5
    max_backtrack_steps: int = 4


@dataclass(slots=True)
class SingleInteractionResult:
    interaction: dict[str, Any]
    follow_up_capture: Optional[dict[str, Any]] = None
    discovered_screen: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class BoundedExplorationResult:
    captures: list[dict[str, Any]]
    screens: list[dict[str, Any]]
    interactions: list[dict[str, Any]]


class BoundedScreenExplorer:
    def __init__(self, driver: WebDriver, runner: MobileRunner, device_manager: Any, config: Optional[ExplorerConfig] = None):
        self.driver = driver
        self.runner = runner
        self.device_manager = device_manager
        self.config = config or ExplorerConfig()
        self._captures: list[dict[str, Any]] = []
        self._screens: list[dict[str, Any]] = []
        self._interactions: list[dict[str, Any]] = []
        self._screen_by_fingerprint: dict[str, dict[str, Any]] = {}
        self._completed_fingerprints: set[str] = set()
        self._active_fingerprints: set[str] = set()
        self._tested_action_signatures: set[tuple[Any, ...]] = set()
        self._screen_counter = 1
        self._interaction_counter = 1

    def _next_screen_id(self) -> str:
        screen_id = f"screen_{self._screen_counter:03d}"
        self._screen_counter += 1
        return screen_id

    def _next_interaction_id(self) -> str:
        interaction_id = f"act_{self._interaction_counter:03d}"
        self._interaction_counter += 1
        return interaction_id

    def _label(self, candidate: dict[str, Any]) -> str:
        return (
            candidate.get("label")
            or candidate.get("content_desc")
            or candidate.get("text")
            or candidate.get("element_id")
            or "unlabeled"
        )

    def _candidate_label_key(self, candidate: dict[str, Any]) -> str:
        label = str(self._label(candidate) or "").strip().lower()
        return " ".join(label.split())

    def _apply_screen_identity(self, capture: dict[str, Any], screen_id: str) -> None:
        capture["screen"]["screen_id"] = screen_id
        capture["screen"]["screenshot_path"] = f"screenshots/{screen_id}.png"
        capture["screen"]["hierarchy_path"] = f"hierarchies/{screen_id}.xml"

    def _register_capture(self, capture: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        fingerprint = str(capture["screen"].get("screen_fingerprint") or "").strip()
        if fingerprint in self._screen_by_fingerprint:
            existing = self._screen_by_fingerprint[fingerprint]
            return existing, False

        requested_screen_id = str(capture["screen"].get("screen_id") or "").strip()
        screen_id = requested_screen_id if requested_screen_id.startswith("screen_") else self._next_screen_id()
        self._apply_screen_identity(capture, screen_id)
        self._screen_by_fingerprint[fingerprint] = capture["screen"]
        self._screens.append(capture["screen"])
        self._captures.append(capture)
        return capture["screen"], True

    def _entry_context(self, screen: dict[str, Any]) -> tuple[dict[str, Any], str]:
        semantic_type = str(screen.get("semantic", {}).get("screen_type") or screen.get("meta", {}).get("screen_type") or "")
        if semantic_type in {"modal_surface", "menu_surface"} or screen.get("meta", {}).get("has_modal"):
            return (
                {"phase": "modal_followup"},
                "Entry modal",
            )
        return (
            {"phase": "initial"},
            "Entry screen",
        )

    def _screen_context(self, screen: dict[str, Any], context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        resolved = dict(context or {})
        labels = {
            str(tappable.get("label") or tappable.get("text") or tappable.get("content_desc") or "").strip().lower()
            for tappable in screen.get("tappables", [])
            if str(tappable.get("label") or tappable.get("text") or tappable.get("content_desc") or "").strip()
        }
        resolved["available_labels"] = sorted(labels)
        resolved["screen_type"] = screen.get("semantic", {}).get("screen_type") or screen.get("meta", {}).get("screen_type") or "unknown"
        resolved["surface_profile"] = resolved["screen_type"]
        resolved["visible_text_count"] = len(screen.get("visible_text", []))
        resolved["screen_bounds"] = list(screen.get("meta", {}).get("screen_bounds_union") or [0, 0, 0, 0])
        return resolved

    def _classify_screen_tappables(self, screen: dict[str, Any], context: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        resolved_context = self._screen_context(screen, context)
        screen["tappables"] = classify_tappables(screen.get("tappables", []), context=resolved_context)
        return screen["tappables"]

    def _log_candidate_ranking(self, screen: dict[str, Any], ranked: list[dict[str, Any]], stage_label: str) -> None:
        if not ranked:
            print(f"[mobile] {stage_label}: no safe candidates matched the current allowlist.")
            return

        print(f"[mobile] {stage_label} candidate ranking for {screen.get('screen_id')}:")
        for index, candidate in enumerate(ranked[:8], start=1):
            print(
                "[mobile]   "
                f"{index}. {self._label(candidate)} | "
                f"safety={candidate.get('safety_score', 0)} "
                f"exploration={candidate.get('exploration_score', 0)} "
                f"final={candidate.get('selection_score', 0)} | "
                f"{candidate.get('selection_reason') or candidate.get('safe_reason')}"
            )

    def _progression_candidates(self, screen: dict[str, Any], phase_context: dict[str, Any]) -> list[dict[str, Any]]:
        ranked = rank_safe_tappables(self._classify_screen_tappables(screen, context=phase_context))
        return [candidate for candidate in ranked if str(candidate.get("action_category") or "") == "progression"]

    def _tap_center(self, bounds: list[int]) -> None:
        if len(bounds) != 4:
            raise RuntimeError("Cannot tap element without valid bounds.")
        x = int((bounds[0] + bounds[2]) / 2)
        y = int((bounds[1] + bounds[3]) / 2)
        print(f"[mobile] Tapping at ({x}, {y}).")
        try:
            self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        except Exception as exc:
            raise RuntimeError(f"Unable to execute tap gesture at ({x}, {y}).") from exc

    def _perform_candidate_action(self, candidate: dict[str, Any]) -> str:
        action_category = str(candidate.get("action_category") or "")
        if action_category == "slider_adjustment":
            self.runner.adjust_slider(candidate.get("bounds") or [])
            return "adjust"
        self._tap_center(candidate.get("bounds") or [])
        return "tap"

    def _screen_type(self, screen: dict[str, Any]) -> str:
        return str(screen.get("semantic", {}).get("screen_type") or screen.get("meta", {}).get("screen_type") or "unknown")

    def _fingerprint(self, screen: dict[str, Any]) -> str:
        return str(screen.get("screen_fingerprint") or "").strip()

    def _is_active_ancestor(self, source_screen: dict[str, Any], target_screen: dict[str, Any]) -> bool:
        target_fingerprint = self._fingerprint(target_screen)
        return bool(target_fingerprint) and target_fingerprint != self._fingerprint(source_screen) and target_fingerprint in self._active_fingerprints

    def _is_access_gate(self, screen: dict[str, Any]) -> bool:
        screen_type = self._screen_type(screen)
        if screen_type in {"auth_gate", "auth_screen"}:
            return True
        text_blob = " ".join(str(value or "").lower() for value in screen.get("visible_text", []))
        has_auth_prompt = any(token in text_blob for token in ("connexion", "connectez", "sign in", "login", "log in"))
        has_guest_route = any(token in text_blob for token in ("mode invité", "mode invite", "guest"))
        return has_auth_prompt and has_guest_route

    def _recover_from_access_gate(self, gate_capture: dict[str, Any], expected_fingerprint: str) -> bool:
        gate_screen = gate_capture["screen"]
        if not self._is_access_gate(gate_screen):
            return False

        candidates = rank_safe_tappables(self._classify_screen_tappables(gate_screen, context={"phase": "initial"}))
        recovery_candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("action_category") or "") == "progression" or is_defer_label(self._label(candidate))
        ]
        if not recovery_candidates:
            return False

        candidate = recovery_candidates[0]
        try:
            print(
                "[mobile] Access gate detected; following safe guest/defer route "
                f"{self._label(candidate)} before continuing the parent branch."
            )
            action_type = self._perform_candidate_action(candidate)
            follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
            target_screen, _is_new = self._maybe_register_target(follow_up_capture)
            result = self._detect_result(gate_screen, follow_up_capture["screen"])
            target_screen_id = (
                target_screen.get("screen_id") or ""
                if result in {"navigation", "modal_open", "content_shift"}
                else gate_screen.get("screen_id") or ""
            )
            self._record_interaction(
                source_screen=gate_screen,
                action_type=action_type,
                result=result,
                notes=(
                    f"Recovered from access gate via '{self._label(candidate)}' "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)}) and observed {result.replace('_', ' ')}."
                ),
                candidate=candidate,
                target_screen_id=target_screen_id,
                target_screen=follow_up_capture["screen"],
            )
            target_fingerprint = self._fingerprint(follow_up_capture["screen"])
            return target_fingerprint == expected_fingerprint or target_fingerprint in self._active_fingerprints
        except Exception as exc:
            self._record_interaction(
                source_screen=gate_screen,
                action_type="tap",
                result="error",
                notes=f"Access-gate recovery failed for '{self._label(candidate)}': {exc}",
                candidate=candidate,
                target_screen_id="",
                target_screen=gate_screen,
            )
            return False

    def _progression_label(self, value: str) -> bool:
        return is_progression_label(value)

    def _element_label(self, element: dict[str, Any]) -> str:
        return (
            str(element.get("text") or "").strip()
            or str(element.get("content_desc") or "").strip()
            or str(element.get("hint_text") or "").strip()
            or str(element.get("label") or "").strip()
        )

    def _has_disabled_progression(self, screen: dict[str, Any]) -> bool:
        for element in screen.get("elements", []):
            if not element.get("visible"):
                continue
            if element.get("enabled"):
                continue
            if self._progression_label(self._element_label(element)):
                return True
        return False

    def _is_modal_surface(self, screen: dict[str, Any]) -> bool:
        screen_type = self._screen_type(screen)
        if screen_type in {"modal_surface", "menu_surface"}:
            return True
        meta = screen.get("meta", {})
        return bool(meta.get("has_modal")) and not bool(meta.get("is_page_like"))

    def _is_page_surface(self, screen: dict[str, Any]) -> bool:
        screen_type = self._screen_type(screen)
        if screen_type in {
            "webview_screen",
            "webview_page",
            "intro_landing",
            "auth_gate",
            "home_dashboard",
            "list_feed",
            "detail_screen",
            "content_feed",
            "scrollable_collection",
            "menu_list",
            "form_screen",
            "input_screen",
            "auth_screen",
            "onboarding_screen",
            "opaque_visual_surface",
            "scrollable_content",
            "navigation_shell",
            "home_feed",
            "program_overview_screen",
            "proof_interstitial",
            "prediction_interstitial",
            "coaching_interstitial",
            "result_summary",
        }:
            return True
        meta = screen.get("meta", {})
        if meta.get("is_page_like"):
            return True
        visible_text = screen.get("visible_text", [])
        return (
            not bool(meta.get("has_modal"))
            and (
                bool(meta.get("has_webview"))
                or bool(meta.get("has_address_bar"))
                or len(visible_text) >= 8
            )
        )

    def _result_details(
        self,
        source_screen: dict[str, Any],
        target_screen: dict[str, Any],
        result_type: str,
        action_type: str,
        candidate: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        source_type = self._screen_type(source_screen)
        target_type = self._screen_type(target_screen)
        details = {
            "type": result_type,
            "action_type": action_type,
            "source_screen_type": source_type,
            "target_screen_type": target_type,
            "source_fingerprint": str(source_screen.get("screen_fingerprint") or ""),
            "target_fingerprint": str(target_screen.get("screen_fingerprint") or ""),
            "trigger_label": self._label(candidate) if candidate else "",
            "trigger_resource_id": str((candidate or {}).get("resource_id") or ""),
            "is_overlay_transition": target_type in {"modal_surface", "menu_surface"},
            "contains_external_content": bool(target_screen.get("meta", {}).get("ux_signals", {}).get("contains_external_content")),
        }
        return details

    def _detect_result(self, source_screen: dict[str, Any], target_screen: dict[str, Any]) -> str:
        source_fingerprint = source_screen.get("screen_fingerprint")
        target_fingerprint = target_screen.get("screen_fingerprint")
        source_type = self._screen_type(source_screen)
        target_type = self._screen_type(target_screen)
        if source_fingerprint == target_fingerprint:
            return "no_change"

        if self._is_modal_surface(target_screen) and not self._is_modal_surface(source_screen):
            return "modal_open"

        if self._is_modal_surface(source_screen) and self._is_page_surface(target_screen):
            return "navigation"

        same_context = (
            source_screen.get("package_name") == target_screen.get("package_name")
            and source_screen.get("activity_name") == target_screen.get("activity_name")
        )
        if (
            same_context
            and source_screen.get("screen_title_guess") == target_screen.get("screen_title_guess")
            and source_type == target_type
            and target_type not in {"modal_surface", "menu_surface"}
        ):
            return "content_shift"
        if same_context and self._is_modal_surface(target_screen):
            return "modal_open"
        if same_context and self._is_page_surface(target_screen):
            return "navigation"
        if same_context and source_screen.get("screen_title_guess") != target_screen.get("screen_title_guess"):
            return "navigation"
        return "navigation"

    def _current_capture(self, screen_id_prefix: str = "probe") -> dict[str, Any]:
        return self.runner.inspect_current_screen(
            screen_id=f"{screen_id_prefix}_{self._interaction_counter:03d}",
            include_screenshot=False,
        )

    def _action_signature(self, source_screen: dict[str, Any], candidate: dict[str, Any], action_type: str) -> tuple[Any, ...]:
        return (
            str(source_screen.get("screen_fingerprint") or ""),
            action_type,
            str(candidate.get("resource_id") or ""),
            str(candidate.get("label") or candidate.get("text") or candidate.get("content_desc") or ""),
            tuple(candidate.get("bounds") or []),
        )

    def _record_interaction(
        self,
        source_screen: dict[str, Any],
        action_type: str,
        result: str,
        notes: str,
        candidate: Optional[dict[str, Any]] = None,
        target_screen_id: str = "",
        target_screen: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        interaction = {
            "interaction_id": self._next_interaction_id(),
            "source_screen_id": source_screen.get("screen_id") or "",
            "element_id": (candidate or {}).get("element_id") or "",
            "action_type": action_type,
            "action_safety": (candidate or {}).get("safe_action") or "safe",
            "result": result,
            "result_details": self._result_details(
                source_screen=source_screen,
                target_screen=target_screen or source_screen,
                result_type=result,
                action_type=action_type,
                candidate=candidate,
            ),
            "target_screen_id": target_screen_id,
            "notes": notes,
        }
        self._interactions.append(interaction)
        return interaction

    def _continue_after_onboarding_selection(self, updated_capture: dict[str, Any], phase_context: dict[str, Any]) -> bool:
        updated_screen = updated_capture["screen"]
        progression_candidates = self._progression_candidates(updated_screen, phase_context)
        if not progression_candidates:
            return False

        candidate = progression_candidates[0]
        try:
            print(
                "[mobile] In-place onboarding change detected; immediately following progression with "
                f"{self._label(candidate)} "
                f"(safety={candidate.get('safety_score', 0)}, "
                f"exploration={candidate.get('exploration_score', 0)}, "
                f"final={candidate.get('selection_score', 0)})."
            )
            action_type = self._perform_candidate_action(candidate)
            follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
            target_screen, is_new = self._maybe_register_target(follow_up_capture)
            result = self._detect_result(updated_screen, follow_up_capture["screen"])
            target_screen_id = (
                target_screen.get("screen_id") or ""
                if result in {"navigation", "modal_open", "content_shift"}
                else updated_screen.get("screen_id") or ""
            )
            self._record_interaction(
                source_screen=updated_screen,
                action_type=action_type,
                result=result,
                notes=(
                    f"Triggered immediate progression after onboarding selection via '{self._label(candidate)}' "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)}) and observed {result.replace('_', ' ')}."
                ),
                candidate=candidate,
                target_screen_id=target_screen_id,
                target_screen=follow_up_capture["screen"],
            )
            if is_new and not self._should_stop():
                self._explore_capture(follow_up_capture, scroll_depth=0)
            return True
        except Exception as exc:
            self._record_interaction(
                source_screen=updated_screen,
                action_type="tap",
                result="error",
                notes=f"Immediate progression after in-place change failed for '{self._label(candidate)}': {exc}",
                candidate=candidate,
                target_screen_id="",
                target_screen=updated_screen,
            )
            return False

    def _return_to_screen(self, expected_fingerprint: str) -> bool:
        if not expected_fingerprint:
            return False

        for attempt in range(self.config.max_backtrack_steps + 1):
            probe = self.runner.inspect_current_screen(screen_id="backtrack", include_screenshot=False)
            current_fingerprint = str(probe["screen"].get("screen_fingerprint") or "").strip()
            if current_fingerprint == expected_fingerprint:
                return True
            if attempt >= self.config.max_backtrack_steps:
                break
            print("[mobile] Backtracking to the previous exploration state.")
            self.device_manager.press_back()
        return False

    def _labels_for_screen_return(self, source_screen: dict[str, Any]) -> list[str]:
        context = self._screen_context(source_screen, {"phase": "initial"})
        classified = classify_tappables(source_screen.get("tappables", []), context=context)
        candidates: list[tuple[int, str]] = []
        for candidate in classified:
            if candidate.get("safe_action") != "safe":
                continue
            if str(candidate.get("action_category") or "") != "navigation":
                continue
            key = self._candidate_label_key(candidate)
            if not key:
                continue
            candidates.append((int(candidate.get("selection_score") or 0), key))

        ordered: list[str] = []
        for _score, key in sorted(candidates, reverse=True):
            if key not in ordered:
                ordered.append(key)
        return ordered[:8]

    def _tap_current_label(self, label_keys: list[str]) -> bool:
        if not label_keys:
            return False
        try:
            probe = self.runner.inspect_current_screen(screen_id="nav_recovery", include_screenshot=False)
            screen = probe["screen"]
            ranked = rank_safe_tappables(self._classify_screen_tappables(screen, context={"phase": "initial"}))
            for wanted in label_keys:
                for candidate in ranked:
                    if self._candidate_label_key(candidate) == wanted:
                        print(f"[mobile] Recovering via in-app navigation label '{self._label(candidate)}'.")
                        self._perform_candidate_action(candidate)
                        return True
        except Exception as exc:
            print(f"[mobile] In-app navigation recovery failed: {exc}")
        return False

    def _recover_to_source_screen(self, source_screen: dict[str, Any]) -> bool:
        expected_fingerprint = str(source_screen.get("screen_fingerprint") or "")
        if self._return_to_screen(expected_fingerprint):
            return True
        if self._tap_current_label(self._labels_for_screen_return(source_screen)):
            return self._return_to_screen(expected_fingerprint)
        return False

    def _should_stop(self) -> bool:
        return (
            len(self._interactions) >= self.config.max_actions_total
            or len(self._screens) >= self.config.max_screens
        )

    def _max_actions_for_screen(self, screen: dict[str, Any]) -> int:
        screen_type = self._screen_type(screen)
        tappable_count = len(screen.get("tappables") or [])
        dense_navigation_surface = screen_type in {
            "home_dashboard",
            "home_feed",
            "navigation_shell",
            "menu_list",
            "scrollable_collection",
            "content_feed",
            "list_feed",
            "program_overview_screen",
            "opaque_visual_surface",
        } or tappable_count >= 10
        if not dense_navigation_surface:
            return self.config.max_actions_per_screen
        return min(self.config.max_actions_total, max(self.config.max_actions_per_screen, 20))

    def _bounds_area(self, bounds: list[int]) -> int:
        if len(bounds) != 4:
            return 0
        return max(0, int(bounds[2]) - int(bounds[0])) * max(0, int(bounds[3]) - int(bounds[1]))

    def _screen_bounds(self, screen: dict[str, Any]) -> list[int]:
        bounds = list(screen.get("meta", {}).get("screen_bounds_union") or [0, 0, 0, 0])
        if len(bounds) == 4 and self._bounds_area(bounds) > 0:
            return [int(value) for value in bounds]
        return [0, 0, 1080, 2280]

    def _covers_most_of_screen(self, bounds: list[int], screen_bounds: list[int]) -> bool:
        screen_area = self._bounds_area(screen_bounds)
        return screen_area > 0 and self._bounds_area(bounds) >= int(screen_area * 0.75)

    def _is_opaque_visual_surface(self, screen: dict[str, Any]) -> bool:
        if self._screen_type(screen) == "opaque_visual_surface":
            return True
        meta = screen.get("meta", {})
        if int(meta.get("visible_text_count") or len(screen.get("visible_text") or [])) > 1:
            return False
        if not (1 <= int(meta.get("clickable_count") or 0) <= 3):
            return False
        screen_bounds = self._screen_bounds(screen)
        return any(
            tappable.get("visible")
            and tappable.get("enabled")
            and tappable.get("is_generic_label")
            and self._covers_most_of_screen(list(tappable.get("bounds") or []), screen_bounds)
            for tappable in screen.get("tappables", [])
        )

    def _opaque_visual_probe_candidates(self, screen: dict[str, Any]) -> list[dict[str, Any]]:
        left, top, right, bottom = self._screen_bounds(screen)
        width = max(1, right - left)
        height = max(1, bottom - top)
        target_half_size = max(8, min(width, height) // 80)
        probe_points = [
            ("Visual option area 1", 0.50, 0.31),
            ("Visual bottom progression area", 0.50, 0.89),
            ("Visual option area 2", 0.50, 0.43),
            ("Visual bottom progression area", 0.50, 0.89),
            ("Visual option area 3", 0.50, 0.55),
            ("Visual bottom progression area", 0.50, 0.89),
            ("Visual option area 4", 0.50, 0.68),
            ("Visual bottom progression area", 0.50, 0.89),
            ("Visual option area 5", 0.50, 0.80),
            ("Visual bottom progression area", 0.50, 0.89),
            ("Visual middle-left area", 0.28, 0.55),
            ("Visual middle-right area", 0.72, 0.55),
        ]

        candidates: list[dict[str, Any]] = []
        for index, (label, x_fraction, y_fraction) in enumerate(probe_points, start=1):
            x = left + int(width * x_fraction)
            y = top + int(height * y_fraction)
            candidates.append(
                {
                    "element_id": f"opaque_probe_{index:02d}",
                    "class_name": "visual_probe",
                    "resource_id": "",
                    "text": label,
                    "content_desc": label,
                    "hint_text": "",
                    "label": label,
                    "bounds": [
                        max(left, x - target_half_size),
                        max(top, y - target_half_size),
                        min(right, x + target_half_size),
                        min(bottom, y + target_half_size),
                    ],
                    "clickable": True,
                    "enabled": True,
                    "visible": True,
                    "focusable": False,
                    "scrollable": False,
                    "control_type": "action",
                    "control_role": "opaque_visual_probe",
                    "is_generic_label": False,
                    "action_category": "opaque_visual_probe",
                    "safe_action": "safe",
                    "safe_reason": "bounded probe for a visual-only app surface with missing accessibility labels",
                    "safety_score": 70,
                    "exploration_score": 72,
                    "selection_score": 142,
                    "selection_reason": "samples likely option and progression zones when the native hierarchy exposes only an unlabeled full-screen view",
                }
            )
        return candidates

    def _maybe_register_target(self, capture: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        screen, is_new = self._register_capture(capture)
        return screen, is_new

    def _explore_opaque_visual_surface(self, source_capture: dict[str, Any]) -> bool:
        current_capture = source_capture
        current_screen = current_capture["screen"]
        print("[mobile] Exploring visual-only surface with bounded coordinate probes because no accessible labels were exposed.")

        for candidate in self._opaque_visual_probe_candidates(current_screen):
            if self._should_stop():
                return False

            signature = self._action_signature(current_screen, candidate, "tap")
            if signature in self._tested_action_signatures:
                continue
            self._tested_action_signatures.add(signature)

            try:
                print(f"[mobile] Probing inaccessible visual target: {self._label(candidate)}.")
                self._perform_candidate_action(candidate)
                follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
                target_screen, is_new = self._maybe_register_target(follow_up_capture)
                result = self._detect_result(current_screen, follow_up_capture["screen"])
                target_screen_id = (
                    target_screen.get("screen_id") or ""
                    if result in {"navigation", "modal_open", "content_shift"}
                    else current_screen.get("screen_id") or ""
                )
                self._record_interaction(
                    source_screen=current_screen,
                    action_type="tap",
                    result=result,
                    notes=(
                        f"Tapped bounded visual probe '{self._label(candidate)}' on an unlabeled app surface "
                        f"and observed {result.replace('_', ' ')}."
                    ),
                    candidate=candidate,
                    target_screen_id=target_screen_id,
                    target_screen=follow_up_capture["screen"],
                )

                if result == "no_change":
                    continue

                if result == "content_shift":
                    current_capture = follow_up_capture
                    current_screen = current_capture["screen"]
                    continue

                if result in {"navigation", "modal_open"}:
                    if self._is_active_ancestor(current_screen, follow_up_capture["screen"]):
                        return False
                    if is_new and not self._should_stop():
                        self._explore_capture(follow_up_capture, scroll_depth=0)
                    return False
            except Exception as exc:
                self._record_interaction(
                    source_screen=current_screen,
                    action_type="tap",
                    result="error",
                    notes=f"Visual-only probe failed for '{self._label(candidate)}': {exc}",
                    candidate=candidate,
                    target_screen_id="",
                    target_screen=current_screen,
                )
                return False

        return True

    def _explore_taps(self, source_capture: dict[str, Any], phase_context: dict[str, Any]) -> bool:
        source_screen = source_capture["screen"]
        source_screen_type = self._screen_type(source_screen)
        ranked = rank_safe_tappables(self._classify_screen_tappables(source_screen, context=phase_context))
        self._log_candidate_ranking(source_screen, ranked, "Safe exploration")

        if not ranked and self._is_opaque_visual_surface(source_screen):
            return self._explore_opaque_visual_surface(source_capture)

        executed_on_screen = 0
        max_actions_for_screen = self._max_actions_for_screen(source_screen)
        executed_label_keys: set[tuple[str, str]] = set()
        onboarding_choice_attempts = 0
        onboarding_choice_no_change_count = 0
        inert_onboarding_choices = False
        progression_visible = any(str(candidate.get("action_category") or "") == "progression" for candidate in ranked)
        disabled_progression_visible = self._has_disabled_progression(source_screen)
        max_onboarding_choice_attempts = 5 if disabled_progression_visible else 2
        for candidate in ranked:
            if executed_on_screen >= max_actions_for_screen or self._should_stop():
                return True

            action_category = str(candidate.get("action_category") or "")
            label_key = self._candidate_label_key(candidate)
            duplicate_sensitive_categories = {
                "navigation",
                "content_card",
                "utility_entry",
                "auth_entry",
                "progression",
            }
            duplicate_key = (action_category, label_key)
            if action_category in duplicate_sensitive_categories and duplicate_key in executed_label_keys:
                continue
            if source_screen_type == "onboarding_screen":
                if action_category == "onboarding_choice":
                    if inert_onboarding_choices:
                        continue
                    if onboarding_choice_attempts >= max_onboarding_choice_attempts:
                        continue

            action_type = "adjust" if action_category == "slider_adjustment" else "tap"
            signature = self._action_signature(source_screen, candidate, action_type)
            if signature in self._tested_action_signatures:
                continue
            self._tested_action_signatures.add(signature)
            if action_category in duplicate_sensitive_categories:
                executed_label_keys.add(duplicate_key)
            executed_on_screen += 1

            try:
                print(
                    "[mobile] Selected safe tappable: "
                    f"{self._label(candidate)} "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)})"
                )
                action_type = self._perform_candidate_action(candidate)
                follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
                target_screen, is_new = self._maybe_register_target(follow_up_capture)
                result = self._detect_result(source_screen, follow_up_capture["screen"])
                target_screen_id = (
                    target_screen.get("screen_id") or ""
                    if result in {"navigation", "modal_open", "content_shift"}
                    else source_screen.get("screen_id") or ""
                )
                notes = (
                    f"{'Adjusted' if action_type == 'adjust' else 'Tapped'} '{self._label(candidate)}' "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)}) and observed {result.replace('_', ' ')}."
                )
                self._record_interaction(
                    source_screen=source_screen,
                    action_type=action_type,
                    result=result,
                    notes=notes,
                    candidate=candidate,
                    target_screen_id=target_screen_id,
                    target_screen=follow_up_capture["screen"],
                )
                if source_screen_type == "onboarding_screen" and action_category == "onboarding_choice":
                    onboarding_choice_attempts += 1
                    if result == "no_change":
                        onboarding_choice_no_change_count += 1
                        if onboarding_choice_no_change_count >= max_onboarding_choice_attempts:
                            inert_onboarding_choices = True
                            print(
                                f"[mobile] Onboarding option taps did not change state after {max_onboarding_choice_attempts} attempts; "
                                "skipping the remaining sibling choices and preferring progression controls."
                            )
                    else:
                        onboarding_choice_no_change_count = 0
                if (
                    source_screen_type == "onboarding_screen"
                    and action_category in {"onboarding_choice", "slider_adjustment"}
                    and self._continue_after_onboarding_selection(follow_up_capture, phase_context)
                ):
                    print("[mobile] Continued onboarding immediately after a valid selection state.")
                    return False
                if result == "content_shift":
                    if is_new and not self._should_stop():
                        self._explore_capture(follow_up_capture, scroll_depth=0)
                    if self._recover_to_source_screen(source_screen):
                        print("[mobile] Returned after in-place state change; continuing sibling exploration on the source screen.")
                        continue
                    print("[mobile] In-place screen state changed after tap and recovery did not reach the source state.")
                    return False
                if result in {"navigation", "modal_open"}:
                    if self._is_active_ancestor(source_screen, follow_up_capture["screen"]):
                        print("[mobile] Tap returned to an active parent screen; continuing the parent branch.")
                        return False
                    if is_new and not self._should_stop():
                        self._explore_capture(follow_up_capture, scroll_depth=0)
                    if source_screen_type == "onboarding_screen" and action_category == "progression":
                        print("[mobile] Onboarding progression succeeded; moving deeper instead of continuing sibling exploration on this step.")
                        return False
                    if (
                        not is_new
                        and self._is_access_gate(follow_up_capture["screen"])
                        and self._recover_from_access_gate(
                            follow_up_capture,
                            str(source_screen.get("screen_fingerprint") or ""),
                        )
                    ):
                        print("[mobile] Recovered from a known access gate; continuing sibling exploration.")
                        continue
                    if not self._recover_to_source_screen(source_screen):
                        print("[mobile] Unable to return to the previous screen after tap exploration. Stopping this branch.")
                        return False
            except Exception as exc:
                self._record_interaction(
                    source_screen=source_screen,
                    action_type="tap",
                    result="error",
                    notes=f"Safe tap failed for '{self._label(candidate)}': {exc}",
                    candidate=candidate,
                    target_screen_id="",
                    target_screen=source_screen,
                )
                if not self._recover_to_source_screen(source_screen):
                    print("[mobile] State recovery failed after tap error. Stopping this branch.")
                    return False

        if source_screen_type == "onboarding_screen" and not self._should_stop():
            if inert_onboarding_choices or not progression_visible:
                if not self._reveal_onboarding_progression(source_capture, phase_context):
                    return False

        return True

    def _reveal_onboarding_progression(self, source_capture: dict[str, Any], phase_context: dict[str, Any]) -> bool:
        source_screen = source_capture["screen"]
        source_fingerprint = str(source_screen.get("screen_fingerprint") or "")
        reveal_signature = (source_fingerprint, "onboarding_reveal_scroll")
        if reveal_signature in self._tested_action_signatures:
            return True
        self._tested_action_signatures.add(reveal_signature)

        print("[mobile] Attempting a bounded onboarding scroll to reveal a hidden progression control.")
        try:
            can_scroll_more = self.runner.scroll_forward(source_screen)
            revealed_capture = self.runner.capture_current_screen(screen_id="pending_screen")
            revealed_screen, is_new = self._maybe_register_target(revealed_capture)
            revealed_result = self._detect_result(source_screen, revealed_capture["screen"])

            if revealed_capture["screen"].get("screen_fingerprint") == source_screen.get("screen_fingerprint"):
                self._record_interaction(
                    source_screen=source_screen,
                    action_type="scroll",
                    result="no_change",
                    notes="Scrolled the onboarding screen to reveal a hidden progression CTA, but no new state appeared.",
                    candidate=None,
                    target_screen_id=source_screen.get("screen_id") or "",
                    target_screen=source_screen,
                )
                return True

            notes = "Scrolled the onboarding screen to reveal additional content or progression controls."
            if not can_scroll_more:
                notes += " Appium reported the end of the scrollable region."
            self._record_interaction(
                source_screen=source_screen,
                action_type="scroll",
                result=revealed_result,
                notes=notes,
                candidate=None,
                target_screen_id=revealed_screen.get("screen_id") or "",
                target_screen=revealed_capture["screen"],
            )

            revealed_progression = self._progression_candidates(revealed_screen, phase_context)
            if revealed_progression:
                candidate = revealed_progression[0]
                print(
                    "[mobile] Hidden onboarding progression revealed: "
                    f"{self._label(candidate)} "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)})"
                )
                self._tap_center(candidate.get("bounds") or [])
                follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
                target_screen, follow_up_is_new = self._maybe_register_target(follow_up_capture)
                result = self._detect_result(revealed_screen, follow_up_capture["screen"])
                target_screen_id = (
                    target_screen.get("screen_id") or ""
                    if result in {"navigation", "modal_open", "content_shift"}
                    else revealed_screen.get("screen_id") or ""
                )
                self._record_interaction(
                    source_screen=revealed_screen,
                    action_type="tap",
                    result=result,
                    notes=(
                        f"Tapped revealed onboarding progression control '{self._label(candidate)}' "
                        f"(safety={candidate.get('safety_score', 0)}, "
                        f"exploration={candidate.get('exploration_score', 0)}, "
                        f"final={candidate.get('selection_score', 0)}) and observed {result.replace('_', ' ')}."
                    ),
                    candidate=candidate,
                    target_screen_id=target_screen_id,
                    target_screen=follow_up_capture["screen"],
                )
                if follow_up_is_new and not self._should_stop():
                    self._explore_capture(follow_up_capture, scroll_depth=0)
                print("[mobile] Onboarding progression continued after reveal; moving deeper into the app flow.")
                return False

            if is_new and not self._should_stop():
                self._explore_capture(revealed_capture, scroll_depth=0)
                return False
        except Exception as exc:
            self._record_interaction(
                source_screen=source_screen,
                action_type="scroll",
                result="error",
                notes=f"Onboarding CTA reveal failed: {exc}",
                candidate=None,
                target_screen_id="",
                target_screen=source_screen,
            )

        return True

    def _explore_scroll(self, source_capture: dict[str, Any], scroll_depth: int) -> None:
        if self._should_stop():
            return
        if scroll_depth >= self.config.max_scrolls_per_path:
            return

        source_screen = source_capture["screen"]
        if not self.runner.can_scroll(source_screen):
            return

        scroll_signature = (
            str(source_screen.get("screen_fingerprint") or ""),
            "scroll",
            scroll_depth,
        )
        if scroll_signature in self._tested_action_signatures:
            return
        self._tested_action_signatures.add(scroll_signature)

        try:
            can_scroll_more = self.runner.scroll_forward(source_screen)
            follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
            target_screen, is_new = self._maybe_register_target(follow_up_capture)
            if follow_up_capture["screen"].get("screen_fingerprint") == source_screen.get("screen_fingerprint"):
                self._record_interaction(
                    source_screen=source_screen,
                    action_type="scroll",
                    result="no_change",
                    notes="Performed a bounded forward scroll but no new UI state was detected.",
                    candidate=None,
                    target_screen_id=source_screen.get("screen_id") or "",
                    target_screen=source_screen,
                )
                return

            notes = "Performed a bounded forward scroll and discovered additional content."
            if not can_scroll_more:
                notes += " Appium reported the end of the scrollable region."
            self._record_interaction(
                source_screen=source_screen,
                action_type="scroll",
                result="content_shift",
                notes=notes,
                candidate=None,
                target_screen_id=target_screen.get("screen_id") or "",
                target_screen=follow_up_capture["screen"],
            )
            if is_new and not self._should_stop():
                self._explore_capture(follow_up_capture, scroll_depth=scroll_depth + 1)
        except Exception as exc:
            self._record_interaction(
                source_screen=source_screen,
                action_type="scroll",
                result="error",
                notes=f"Scroll discovery failed: {exc}",
                candidate=None,
                target_screen_id="",
                target_screen=source_screen,
            )

    def _explore_capture(self, capture: dict[str, Any], scroll_depth: int) -> None:
        screen, _ = self._maybe_register_target(capture)
        fingerprint = str(screen.get("screen_fingerprint") or "").strip()
        if not fingerprint:
            return
        if fingerprint in self._completed_fingerprints or fingerprint in self._active_fingerprints:
            return
        if self._should_stop():
            return

        self._active_fingerprints.add(fingerprint)
        try:
            context, stage_label = self._entry_context(screen)
            if stage_label == "Entry modal":
                print("[mobile] Exploring a bounded modal/menu surface.")
            safe_branch_ok = self._explore_taps(capture, phase_context=context)
            if safe_branch_ok:
                self._explore_scroll(capture, scroll_depth=scroll_depth)
            self._completed_fingerprints.add(fingerprint)
        finally:
            self._active_fingerprints.discard(fingerprint)

    def run_bounded_exploration(self, first_capture: dict[str, Any]) -> BoundedExplorationResult:
        self._captures = []
        self._screens = []
        self._interactions = []
        self._screen_by_fingerprint = {}
        self._completed_fingerprints = set()
        self._active_fingerprints = set()
        self._tested_action_signatures = set()
        self._screen_counter = 1
        self._interaction_counter = 1

        self._apply_screen_identity(first_capture, "screen_001")
        self._screen_counter = 2
        self._register_capture(first_capture)
        self._explore_capture(first_capture, scroll_depth=0)

        return BoundedExplorationResult(
            captures=self._captures,
            screens=self._screens,
            interactions=self._interactions,
        )


SingleStepScreenExplorer = BoundedScreenExplorer
