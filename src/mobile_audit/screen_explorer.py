from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from appium.webdriver.webdriver import WebDriver

from .mobile_runner import MobileRunner
from .safe_actions import classify_tappables, rank_safe_tappables


@dataclass(slots=True)
class ExplorerConfig:
    max_screens: int = 12
    max_actions_total: int = 24
    max_actions_per_screen: int = 6
    max_scrolls_per_path: int = 3
    max_backtrack_steps: int = 2


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
        if screen.get("meta", {}).get("has_modal"):
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
        if (
            resolved.get("phase", "initial") == "initial"
            and not screen.get("meta", {}).get("has_modal")
            and any(label in labels for label in ("search or type web address", "discover", "options for discover"))
        ):
            resolved["surface_profile"] = "chrome_home"
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

    def _is_modal_surface(self, screen: dict[str, Any]) -> bool:
        meta = screen.get("meta", {})
        return bool(meta.get("has_modal")) and not bool(meta.get("is_page_like"))

    def _is_page_surface(self, screen: dict[str, Any]) -> bool:
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

    def _detect_result(self, source_screen: dict[str, Any], target_screen: dict[str, Any]) -> str:
        source_fingerprint = source_screen.get("screen_fingerprint")
        target_fingerprint = target_screen.get("screen_fingerprint")
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
    ) -> dict[str, Any]:
        interaction = {
            "interaction_id": self._next_interaction_id(),
            "source_screen_id": source_screen.get("screen_id") or "",
            "element_id": (candidate or {}).get("element_id") or "",
            "action_type": action_type,
            "action_safety": (candidate or {}).get("safe_action") or "safe",
            "result": result,
            "target_screen_id": target_screen_id,
            "notes": notes,
        }
        self._interactions.append(interaction)
        return interaction

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

    def _should_stop(self) -> bool:
        return (
            len(self._interactions) >= self.config.max_actions_total
            or len(self._screens) >= self.config.max_screens
        )

    def _maybe_register_target(self, capture: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        screen, is_new = self._register_capture(capture)
        return screen, is_new

    def _explore_taps(self, source_capture: dict[str, Any], phase_context: dict[str, Any]) -> bool:
        source_screen = source_capture["screen"]
        ranked = rank_safe_tappables(self._classify_screen_tappables(source_screen, context=phase_context))
        self._log_candidate_ranking(source_screen, ranked, "Safe exploration")

        executed_on_screen = 0
        for candidate in ranked:
            if executed_on_screen >= self.config.max_actions_per_screen or self._should_stop():
                return True

            signature = self._action_signature(source_screen, candidate, "tap")
            if signature in self._tested_action_signatures:
                continue
            self._tested_action_signatures.add(signature)
            executed_on_screen += 1

            try:
                print(
                    "[mobile] Selected safe tappable: "
                    f"{self._label(candidate)} "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)})"
                )
                self._tap_center(candidate.get("bounds") or [])
                follow_up_capture = self.runner.capture_current_screen(screen_id="pending_screen")
                target_screen, is_new = self._maybe_register_target(follow_up_capture)
                result = self._detect_result(source_screen, follow_up_capture["screen"])
                target_screen_id = (
                    target_screen.get("screen_id") or ""
                    if result in {"navigation", "modal_open"}
                    else source_screen.get("screen_id") or ""
                )
                notes = (
                    f"Tapped '{self._label(candidate)}' "
                    f"(safety={candidate.get('safety_score', 0)}, "
                    f"exploration={candidate.get('exploration_score', 0)}, "
                    f"final={candidate.get('selection_score', 0)}) and observed {result.replace('_', ' ')}."
                )
                self._record_interaction(
                    source_screen=source_screen,
                    action_type="tap",
                    result=result,
                    notes=notes,
                    candidate=candidate,
                    target_screen_id=target_screen_id,
                )
                if result in {"navigation", "modal_open"}:
                    if is_new and not self._should_stop():
                        self._explore_capture(follow_up_capture, scroll_depth=0)
                    if not self._return_to_screen(str(source_screen.get("screen_fingerprint") or "")):
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
                )
                if not self._return_to_screen(str(source_screen.get("screen_fingerprint") or "")):
                    print("[mobile] State recovery failed after tap error. Stopping this branch.")
                    return False

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
