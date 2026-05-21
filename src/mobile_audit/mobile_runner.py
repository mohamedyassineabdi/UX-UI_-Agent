from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from typing import Any

from appium.webdriver.webdriver import WebDriver

from .hierarchy_extractor import build_screen_fingerprint, extract_hierarchy
from .tappable_extractor import build_tappables


@dataclass(slots=True)
class MobileRunnerConfig:
    settle_delay_ms: int = 1200
    stabilization_timeout_ms: int = 10000
    stabilization_poll_ms: int = 700
    initialization_max_back_presses: int = 2
    initialization_post_back_delay_ms: int = 900
    initialization_max_relaunches: int = 1
    initialization_post_relaunch_delay_ms: int = 1400
    scroll_post_delay_ms: int = 900
    scroll_percent: float = 0.82
    slider_post_delay_ms: int = 900
    slider_drag_duration_ms: int = 500


class MobileRunner:
    def __init__(self, driver: WebDriver, config: MobileRunnerConfig):
        self.driver = driver
        self.config = config

    def _visual_screen_signature(self, screenshot_png: bytes) -> str:
        if not screenshot_png:
            return ""
        try:
            from PIL import Image

            with Image.open(io.BytesIO(screenshot_png)) as image:
                width, height = image.size
                top = int(height * 0.10)
                bottom = int(height * 0.92)
                cropped = image.convert("L").crop((0, top, width, max(top + 1, bottom)))
                sampled = cropped.resize((32, 64))
                return hashlib.sha1(sampled.tobytes()).hexdigest()[:16]
        except Exception:
            return hashlib.sha1(screenshot_png).hexdigest()[:16]

    def _needs_visual_fingerprint(self, parsed: dict[str, Any], tappables: list[dict[str, Any]]) -> bool:
        semantic_type = str(parsed.get("semantic", {}).get("screen_type") or "")
        if semantic_type == "opaque_visual_surface":
            return True
        meta = parsed.get("meta", {})
        visible_text_count = int(meta.get("visible_text_count") or 0)
        clickable_count = int(meta.get("clickable_count") or 0)
        has_fullscreen_generic_target = any(
            tappable.get("visible")
            and tappable.get("enabled")
            and tappable.get("is_generic_label")
            and self._covers_most_of_screen(tappable.get("bounds") or [], meta.get("screen_bounds_union") or [])
            for tappable in tappables
        )
        return visible_text_count <= 1 and 1 <= clickable_count <= 3 and has_fullscreen_generic_target

    def _covers_most_of_screen(self, bounds: list[int], screen_bounds: list[int]) -> bool:
        if len(bounds) != 4 or len(screen_bounds) != 4:
            return False
        target_area = max(0, int(bounds[2]) - int(bounds[0])) * max(0, int(bounds[3]) - int(bounds[1]))
        screen_area = max(0, int(screen_bounds[2]) - int(screen_bounds[0])) * max(0, int(screen_bounds[3]) - int(screen_bounds[1]))
        return screen_area > 0 and target_area >= int(screen_area * 0.75)

    def wait_for_stabilization(self) -> str:
        time.sleep(max(0, self.config.settle_delay_ms) / 1000.0)

        deadline = time.time() + (self.config.stabilization_timeout_ms / 1000.0)
        previous_source = ""
        while time.time() < deadline:
            current_source = self.driver.page_source or ""
            if current_source and current_source == previous_source:
                return current_source
            previous_source = current_source
            time.sleep(max(100, self.config.stabilization_poll_ms) / 1000.0)

        return previous_source or (self.driver.page_source or "")

    def inspect_current_screen(self, screen_id: str = "probe", include_screenshot: bool = True) -> dict[str, Any]:
        hierarchy_xml = self.wait_for_stabilization()
        screenshot_png = self.driver.get_screenshot_as_png() if include_screenshot else b""

        package_name = str(getattr(self.driver, "current_package", "") or "").strip()
        try:
            activity_name = str(getattr(self.driver, "current_activity", "") or "").strip()
        except Exception:
            activity_name = ""

        parsed = extract_hierarchy(
            hierarchy_xml,
            package_name=package_name,
            activity_name=activity_name,
        )
        tappables = build_tappables(parsed["elements"])
        needs_visual_fingerprint = self._needs_visual_fingerprint(parsed, tappables)
        if needs_visual_fingerprint and not screenshot_png:
            screenshot_png = self.driver.get_screenshot_as_png()

        screen_fingerprint = build_screen_fingerprint(
            package_name,
            activity_name,
            parsed["visible_text"],
            parsed["elements"],
        )
        visual_signature = self._visual_screen_signature(screenshot_png) if needs_visual_fingerprint else ""
        if visual_signature:
            screen_fingerprint = f"{screen_fingerprint}:{visual_signature}"
            parsed["meta"]["uses_visual_fingerprint"] = True

        screen_record = {
            "screen_id": screen_id,
            "package_name": package_name,
            "activity_name": activity_name,
            "screen_fingerprint": screen_fingerprint,
            "screen_title_guess": parsed["screen_title_guess"],
            "screenshot_path": f"screenshots/{screen_id}.png",
            "hierarchy_path": f"hierarchies/{screen_id}.xml",
            "visible_text": parsed["visible_text"],
            "elements": parsed["elements"],
            "tappables": tappables,
            "meta": parsed["meta"],
            "semantic": parsed["semantic"],
        }

        return {
            "screen": screen_record,
            "screenshot_png": screenshot_png,
            "hierarchy_xml": hierarchy_xml,
        }

    def capture_current_screen(self, screen_id: str = "screen_001") -> dict[str, Any]:
        return self.inspect_current_screen(screen_id=screen_id, include_screenshot=True)

    def _largest_scrollable_bounds(self, screen: dict[str, Any]) -> list[int]:
        scrollables = [
            element
            for element in screen.get("elements", [])
            if element.get("visible") and element.get("scrollable") and len(element.get("bounds") or []) == 4
        ]
        if not scrollables:
            return []
        scrollables.sort(key=lambda item: (item.get("width", 0) * item.get("height", 0)), reverse=True)
        return list(scrollables[0].get("bounds") or [])

    def can_scroll(self, screen: dict[str, Any]) -> bool:
        if any(bool(element.get("scrollable")) for element in screen.get("elements", [])):
            return True
        meta = screen.get("meta", {})
        semantic_type = self._screen_type(screen)
        if semantic_type in {
            "list_feed",
            "detail_screen",
            "scrollable_content",
            "webview_screen",
            "webview_page",
            "intro_landing",
            "auth_gate",
            "home_dashboard",
            "home_feed",
            "navigation_shell",
            "scrollable_collection",
            "content_feed",
            "menu_list",
            "form_screen",
            "auth_screen",
            "onboarding_screen",
            "opaque_visual_surface",
            "program_overview_screen",
            "proof_interstitial",
            "prediction_interstitial",
            "coaching_interstitial",
            "result_summary",
            "blocked_no_actions",
        }:
            return True
        return bool(meta.get("is_page_like")) or int(meta.get("visible_text_count") or 0) >= 10

    def scroll_forward(self, screen: dict[str, Any]) -> bool:
        bounds = self._largest_scrollable_bounds(screen)
        if len(bounds) != 4:
            size = self.driver.get_window_size()
            width = int(size.get("width", 1080))
            height = int(size.get("height", 2148))
            bounds = [
                int(width * 0.08),
                int(height * 0.22),
                int(width * 0.92),
                int(height * 0.86),
            ]

        left = int(bounds[0])
        top = int(bounds[1])
        width = max(1, int(bounds[2] - bounds[0]))
        height = max(1, int(bounds[3] - bounds[1]))

        print(
            "[mobile] Scrolling forward within bounds "
            f"[{left},{top},{left + width},{top + height}]"
        )
        try:
            can_scroll_more = bool(
                self.driver.execute_script(
                    "mobile: scrollGesture",
                    {
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                        "direction": "down",
                        "percent": float(self.config.scroll_percent),
                    },
                )
            )
        except Exception:
            can_scroll_more = False
            self.driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "direction": "up",
                    "percent": float(self.config.scroll_percent),
                },
            )

        time.sleep(max(0, self.config.scroll_post_delay_ms) / 1000.0)
        return can_scroll_more

    def adjust_slider(self, bounds: list[int], *, target_fraction: float = 0.72) -> None:
        if len(bounds) != 4:
            raise RuntimeError("Cannot adjust a slider without valid bounds.")

        x1, y1, x2, y2 = [int(value) for value in bounds]
        width = max(1, x2 - x1)
        height = max(1, y2 - y1)
        start_x = x1 + max(4, int(width * 0.35))
        end_x = x1 + max(8, int(width * max(0.45, min(target_fraction, 0.9))))
        center_y = y1 + int(height / 2)

        print(f"[mobile] Adjusting slider from ({start_x}, {center_y}) to ({end_x}, {center_y}).")
        self.driver.execute_script(
            "mobile: dragGesture",
            {
                "startX": start_x,
                "startY": center_y,
                "endX": end_x,
                "endY": center_y,
                "speed": 900,
            },
        )
        time.sleep(max(0, self.config.slider_post_delay_ms) / 1000.0)

    def _screen_type(self, screen: dict[str, Any]) -> str:
        return str(screen.get("semantic", {}).get("screen_type") or screen.get("meta", {}).get("screen_type") or "unknown")

    def _screen_labels(self, screen: dict[str, Any]) -> set[str]:
        labels = {str(value or "").strip().lower() for value in screen.get("visible_text", []) if str(value or "").strip()}
        for tappable in screen.get("tappables", []):
            label = str(tappable.get("label") or tappable.get("text") or tappable.get("content_desc") or "").strip().lower()
            if label:
                labels.add(label)
        return labels

    def _is_overlay_surface(self, screen: dict[str, Any]) -> bool:
        screen_type = self._screen_type(screen)
        if screen_type in {"modal_surface", "menu_surface"}:
            return True
        meta = screen.get("meta", {})
        return bool(meta.get("has_modal")) and not bool(meta.get("is_page_like"))

    def _baseline_diagnostics(self, screen: dict[str, Any], expected_package: str) -> dict[str, Any]:
        package_name = str(screen.get("package_name") or "").strip()
        meta = screen.get("meta", {})
        semantic = screen.get("semantic", {})
        labels = self._screen_labels(screen)
        screen_type = semantic.get("screen_type") or meta.get("screen_type") or "unknown"
        visible_count = len(screen.get("visible_text", []))
        tappable_count = len(screen.get("tappables", []))
        scrollable_count = int(meta.get("scrollable_count") or 0)
        title = str(screen.get("screen_title_guess") or "").strip()

        positive_signals: list[str] = []
        rejection_reasons: list[str] = []

        package_matches = package_name == expected_package
        if not package_matches:
            rejection_reasons.append("package_mismatch")
        if self._is_overlay_surface(screen):
            rejection_reasons.append("overlay_surface")
        if visible_count <= 1 and tappable_count <= 1 and not scrollable_count and not meta.get("is_page_like"):
            rejection_reasons.append("empty_or_transient_surface")

        if title:
            positive_signals.append("has_title")
        if visible_count >= 3:
            positive_signals.append("has_visible_content")
        if tappable_count >= 2:
            positive_signals.append("has_actions")
        if scrollable_count >= 1 or semantic.get("ux_signals", {}).get("is_scrollable"):
            positive_signals.append("scrollable_surface")
        if screen_type != "unknown":
            positive_signals.append(f"classified:{screen_type}")
        if meta.get("has_bottom_nav"):
            positive_signals.append("bottom_navigation")

        is_baseline = package_matches and not rejection_reasons and bool(positive_signals)
        return {
            "is_baseline": is_baseline,
            "package_matches": package_matches,
            "screen_type": screen_type,
            "title": title,
            "positive_signals": positive_signals,
            "rejection_reasons": rejection_reasons,
            "visible_count": visible_count,
            "tappable_count": tappable_count,
        }

    def normalize_to_baseline(self, device_manager: Any, expected_package: str) -> None:
        print("[mobile] Normalizing app state before entry capture.")
        device_manager.activate_target_app()

        max_back_presses = max(0, int(self.config.initialization_max_back_presses))
        post_back_delay_s = max(0, self.config.initialization_post_back_delay_ms) / 1000.0
        max_relaunches = max(0, int(self.config.initialization_max_relaunches))
        post_relaunch_delay_s = max(0, self.config.initialization_post_relaunch_delay_ms) / 1000.0
        last_screen: dict[str, Any] | None = None
        back_presses_used = 0
        relaunches_used = 0

        for attempt in range(max_back_presses + max_relaunches + 3):
            probe = self.inspect_current_screen(screen_id=f"normalize_{attempt + 1}", include_screenshot=False)
            screen = probe["screen"]
            last_screen = screen
            title = screen.get("screen_title_guess") or "(untitled)"
            semantic_type = self._screen_type(screen)
            print(
                "[mobile] Normalization probe: "
                f"title={title}, type={semantic_type}, modal={screen.get('meta', {}).get('has_modal')}, "
                f"visible_text={screen.get('visible_text', [])[:4]}"
            )

            if str(screen.get("package_name") or "").strip() != expected_package:
                print("[mobile] Target app is not in foreground during normalization. Re-activating it.")
                device_manager.activate_target_app()
                continue

            diagnostics = self._baseline_diagnostics(screen, expected_package)
            if diagnostics["is_baseline"]:
                print(
                    "[mobile] Baseline app surface confirmed. "
                    f"type={diagnostics['screen_type']}, positive_signals={diagnostics['positive_signals']}"
                )
                return

            print(
                "[mobile] Baseline rejected. "
                f"type={diagnostics['screen_type']}, "
                f"reasons={diagnostics['rejection_reasons']}, "
                f"positive_signals={diagnostics['positive_signals']}"
            )

            if self._is_overlay_surface(screen) and back_presses_used < max_back_presses:
                print("[mobile] Detected transient overlay state, sending back.")
                device_manager.press_back()
                back_presses_used += 1
                time.sleep(post_back_delay_s)
                continue

            if back_presses_used < max_back_presses:
                print("[mobile] Attempting generic recovery with back navigation.")
                device_manager.press_back()
                back_presses_used += 1
                time.sleep(post_back_delay_s)
                continue

            if relaunches_used < max_relaunches:
                print("[mobile] Recovery via back exhausted. Re-launching the target activity.")
                device_manager.start_target_activity()
                relaunches_used += 1
                time.sleep(post_relaunch_delay_s)
                continue

            break

        if last_screen and str(last_screen.get("package_name") or "").strip() == expected_package and not self._is_overlay_surface(last_screen):
            print(
                "[mobile] Using the last stable in-app surface as a fallback baseline. "
                f"title={last_screen.get('screen_title_guess') or '(untitled)'}, "
                f"type={self._screen_type(last_screen)}"
            )
            return

        if last_screen:
            print(
                "[mobile] Baseline normalization failed. "
                f"Last observed title={last_screen.get('screen_title_guess') or '(untitled)'}, "
                f"type={self._screen_type(last_screen)}, "
                f"visible_text={last_screen.get('visible_text', [])[:4]}."
            )
        raise RuntimeError("Could not normalize the app to a stable in-app baseline before entry capture.")
