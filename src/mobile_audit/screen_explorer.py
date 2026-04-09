from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from appium.webdriver.webdriver import WebDriver

from .mobile_runner import MobileRunner
from .safe_actions import choose_best_safe_tappable, classify_tappables, rank_safe_tappables


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


class SingleStepScreenExplorer:
    def __init__(self, driver: WebDriver, runner: MobileRunner):
        self.driver = driver
        self.runner = runner

    def _entry_context(self, screen: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if screen.get("meta", {}).get("has_modal"):
            return (
                {"phase": "modal_followup"},
                "Step 1 (entry modal)",
            )
        return (
            {"phase": "initial"},
            "Step 1",
        )

    def _label(self, candidate: dict[str, Any]) -> str:
        return (
            candidate.get("label")
            or candidate.get("content_desc")
            or candidate.get("text")
            or candidate.get("element_id")
            or "unlabeled"
        )

    def _classify_screen_tappables(self, screen: dict[str, Any], context: Optional[dict[str, Any]] = None) -> None:
        screen["tappables"] = classify_tappables(screen.get("tappables", []), context=context)

    def _log_candidate_ranking(
        self,
        classified_tappables: list[dict[str, Any]],
        chosen: Optional[dict[str, Any]],
        stage_label: str,
    ) -> None:
        ranked = rank_safe_tappables(classified_tappables)
        if not ranked:
            print(f"[mobile] {stage_label}: no safe candidates matched the current allowlist.")
            return

        print(f"[mobile] {stage_label} candidate ranking:")
        for index, candidate in enumerate(ranked[:5], start=1):
            print(
                "[mobile]   "
                f"{index}. {self._label(candidate)} | "
                f"safety={candidate.get('safety_score', 0)} "
                f"exploration={candidate.get('exploration_score', 0)} "
                f"final={candidate.get('selection_score', 0)} | "
                f"{candidate.get('selection_reason') or candidate.get('safe_reason')}"
            )

        home_candidate = next(
            (
                candidate
                for candidate in ranked
                if str(candidate.get("label") or candidate.get("content_desc") or candidate.get("text") or "").strip().lower() == "home"
            ),
            None,
        )
        if home_candidate and chosen and home_candidate.get("element_id") != chosen.get("element_id"):
            print(
                "[mobile]   Home was skipped because its exploration score is lower than the selected bounded control."
            )
        elif home_candidate and chosen and home_candidate.get("element_id") == chosen.get("element_id"):
            print("[mobile]   Home was chosen because no higher-value bounded safe control ranked above it.")

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

    def _detect_result(self, source_screen: dict[str, Any], target_screen: dict[str, Any]) -> str:
        source_fingerprint = source_screen.get("screen_fingerprint")
        target_fingerprint = target_screen.get("screen_fingerprint")
        if source_fingerprint == target_fingerprint:
            return "no_change"

        if target_screen.get("meta", {}).get("has_modal") and not source_screen.get("meta", {}).get("has_modal"):
            return "modal_open"

        if source_screen.get("meta", {}).get("has_modal") and not target_screen.get("meta", {}).get("has_modal"):
            return "navigation"

        same_context = (
            source_screen.get("package_name") == target_screen.get("package_name")
            and source_screen.get("activity_name") == target_screen.get("activity_name")
        )
        if same_context and target_screen.get("meta", {}).get("has_modal"):
            return "modal_open"
        if same_context and source_screen.get("screen_title_guess") != target_screen.get("screen_title_guess"):
            return "navigation"
        return "navigation"

    def _can_continue_from_modal(self, interaction: dict[str, Any], capture: Optional[dict[str, Any]]) -> bool:
        if not capture:
            return False
        if interaction.get("result") != "modal_open":
            return False
        return bool(capture["screen"].get("meta", {}).get("has_modal"))

    def _run_safe_interaction(
        self,
        source_capture: dict[str, Any],
        interaction_id: str,
        next_screen_id: str,
        context: Optional[dict[str, Any]],
        stage_label: str,
    ) -> SingleInteractionResult:
        source_screen = source_capture["screen"]
        self._classify_screen_tappables(source_screen, context=context)
        candidate = choose_best_safe_tappable(source_screen["tappables"])
        self._log_candidate_ranking(source_screen["tappables"], candidate, stage_label)

        if not candidate:
            print(f"[mobile] {stage_label}: no safe tappable matched the current allowlist.")
            return SingleInteractionResult(
                interaction={
                    "interaction_id": interaction_id,
                    "source_screen_id": source_screen["screen_id"],
                    "element_id": "",
                    "action_type": "tap",
                    "action_safety": "blocked",
                    "result": "error",
                    "target_screen_id": "",
                    "notes": f"{stage_label}: no safe tappable matched the current allowlist.",
                }
            )

        print(
            "[mobile] Selected safe tappable: "
            f"{self._label(candidate)} "
            f"(safety={candidate.get('safety_score', 0)}, "
            f"exploration={candidate.get('exploration_score', 0)}, "
            f"final={candidate.get('selection_score', 0)})"
        )

        try:
            self._tap_center(candidate.get("bounds") or [])
            follow_up_capture = self.runner.capture_current_screen(screen_id=next_screen_id)
            self._classify_screen_tappables(follow_up_capture["screen"])
            result = self._detect_result(source_screen, follow_up_capture["screen"])
            target_screen_id = (
                follow_up_capture["screen"]["screen_id"]
                if result in {"navigation", "modal_open"}
                else source_screen["screen_id"]
            )
            notes = (
                f"Tapped '{self._label(candidate)}' "
                f"(safety={candidate.get('safety_score', 0)}, "
                f"exploration={candidate.get('exploration_score', 0)}, "
                f"final={candidate.get('selection_score', 0)}) and observed {result.replace('_', ' ')}."
            )
            return SingleInteractionResult(
                interaction={
                    "interaction_id": interaction_id,
                    "source_screen_id": source_screen["screen_id"],
                    "element_id": candidate.get("element_id") or "",
                    "action_type": "tap",
                    "action_safety": "safe",
                    "result": result,
                    "target_screen_id": target_screen_id,
                    "notes": notes,
                },
                follow_up_capture=follow_up_capture,
                discovered_screen=follow_up_capture["screen"] if result in {"navigation", "modal_open"} else None,
            )
        except Exception as exc:
            return SingleInteractionResult(
                interaction={
                    "interaction_id": interaction_id,
                    "source_screen_id": source_screen["screen_id"],
                    "element_id": candidate.get("element_id") or "",
                    "action_type": "tap",
                    "action_safety": "safe",
                    "result": "error",
                    "target_screen_id": "",
                    "notes": f"Safe tap failed: {exc}",
                }
            )

    def run_one_safe_interaction(self, source_capture: dict[str, Any]) -> SingleInteractionResult:
        context, stage_label = self._entry_context(source_capture["screen"])
        return self._run_safe_interaction(
            source_capture=source_capture,
            interaction_id="act_001",
            next_screen_id="screen_002",
            context=context,
            stage_label=stage_label,
        )

    def run_bounded_two_step_flow(self, first_capture: dict[str, Any]) -> BoundedExplorationResult:
        captures = [first_capture]
        screens = [first_capture["screen"]]
        interactions: list[dict[str, Any]] = []

        first_context, first_stage_label = self._entry_context(first_capture["screen"])
        if first_context.get("phase") == "modal_followup":
            print("[mobile] Entry screen is already a bounded modal/menu state; using the modal follow-up allowlist for Step 1.")

        first_result = self._run_safe_interaction(
            source_capture=first_capture,
            interaction_id="act_001",
            next_screen_id="screen_002",
            context=first_context,
            stage_label=first_stage_label,
        )
        interactions.append(first_result.interaction)

        if first_result.follow_up_capture:
            captures.append(first_result.follow_up_capture)
        if first_result.discovered_screen:
            screens.append(first_result.discovered_screen)

        if not self._can_continue_from_modal(first_result.interaction, first_result.follow_up_capture):
            return BoundedExplorationResult(captures=captures, screens=screens, interactions=interactions)

        print("[mobile] Step 2 is enabled because Step 1 opened a bounded modal/menu state.")
        second_result = self._run_safe_interaction(
            source_capture=first_result.follow_up_capture,
            interaction_id="act_002",
            next_screen_id="screen_003",
            context={"phase": "modal_followup"},
            stage_label="Step 2",
        )
        interactions.append(second_result.interaction)

        if second_result.follow_up_capture:
            captures.append(second_result.follow_up_capture)
        if second_result.discovered_screen:
            screens.append(second_result.discovered_screen)

        return BoundedExplorationResult(captures=captures, screens=screens, interactions=interactions)
