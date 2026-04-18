from __future__ import annotations

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


class MobileRunner:
    def __init__(self, driver: WebDriver, config: MobileRunnerConfig):
        self.driver = driver
        self.config = config

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

        screen_record = {
            "screen_id": screen_id,
            "package_name": package_name,
            "activity_name": activity_name,
            "screen_fingerprint": build_screen_fingerprint(
                package_name,
                activity_name,
                parsed["visible_text"],
                parsed["elements"],
            ),
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
        return bool(meta.get("has_webview") or meta.get("is_page_like"))

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

    def _screen_labels(self, screen: dict[str, Any]) -> set[str]:
        labels = {str(value or "").strip().lower() for value in screen.get("visible_text", []) if str(value or "").strip()}
        for tappable in screen.get("tappables", []):
            label = str(tappable.get("label") or tappable.get("text") or tappable.get("content_desc") or "").strip().lower()
            if label:
                labels.add(label)
        return labels

    def _compact_modal_labels(self, screen: dict[str, Any]) -> set[str]:
        labels = self._screen_labels(screen)
        return {label for label in labels if label}

    def _looks_like_compact_modal_menu(self, screen: dict[str, Any], expected_package: str) -> bool:
        if str(screen.get("package_name") or "").strip() != expected_package:
            return False
        semantic = screen.get("semantic", {})
        if semantic.get("screen_type") == "modal_menu":
            return True
        if not bool(screen.get("meta", {}).get("has_modal")):
            return False

        labels = self._compact_modal_labels(screen)
        if not labels:
            return False

        compact_menu_signals = {
            "learn more",
            "turn off",
            "menu",
        }
        tappable_count = len(screen.get("tappables", []))
        visible_count = len(screen.get("visible_text", []))
        return (
            len(labels.intersection(compact_menu_signals)) > 0
            or (visible_count <= 3 and tappable_count <= 3)
        )

    def _looks_like_stale_chrome_surface(self, screen: dict[str, Any], expected_package: str) -> bool:
        if str(screen.get("package_name") or "").strip() != expected_package:
            return False
        labels = self._screen_labels(screen)
        stale_indicators = (
            "support.google.com",
            "google chrome help",
            "help",
            "learn more",
            "turn off",
        )
        return any(indicator in label for label in labels for indicator in stale_indicators)

    def _address_bar_text(self, screen: dict[str, Any]) -> str:
        for element in screen.get("elements", []):
            resource_id = str(element.get("resource_id") or "").lower()
            if "url_bar" not in resource_id:
                continue
            text = str(element.get("text") or "").strip()
            hint_text = str(element.get("hint_text") or "").strip()
            if text:
                return text
            if hint_text:
                return hint_text
        return ""

    def _looks_like_help_or_support_destination(self, screen: dict[str, Any], expected_package: str) -> bool:
        if str(screen.get("package_name") or "").strip() != expected_package:
            return False

        semantic = screen.get("semantic", {})
        if semantic.get("screen_type") == "webview_page":
            return True

        meta = screen.get("meta", {})
        title = str(screen.get("screen_title_guess") or "").strip().lower()
        labels = self._screen_labels(screen)
        address_bar_text = self._address_bar_text(screen).strip().lower()
        has_support_url = (
            "support.google.com" in address_bar_text
            or ("http" in address_bar_text and "support" in address_bar_text)
            or ("chrome/answer" in address_bar_text)
        )
        title_looks_article = any(
            token in title
            for token in ("google chrome help", "help", "support", "customize your new tab page")
        )
        content_looks_article = any(
            token in label
            for label in labels
            for token in ("google chrome help", "search help center", "helpcenter sections", "support page navigation bar")
        )

        return bool(
            meta.get("is_page_like")
            and (
                has_support_url
                or (
                    bool(meta.get("has_webview"))
                    and (title_looks_article or content_looks_article or bool(meta.get("has_help_or_article_structure")))
                )
            )
        )

    def _looks_like_chrome_home_surface(self, screen: dict[str, Any], expected_package: str) -> bool:
        if str(screen.get("package_name") or "").strip() != expected_package:
            return False
        semantic = screen.get("semantic", {})
        if semantic.get("screen_type") == "home_feed":
            return True
        if bool(screen.get("meta", {}).get("has_modal")):
            return False
        if self._looks_like_help_or_support_destination(screen, expected_package):
            return False

        labels = self._screen_labels(screen)
        title = str(screen.get("screen_title_guess") or "").strip().lower()
        address_bar_text = self._address_bar_text(screen).strip().lower()
        is_search_hint = address_bar_text in {"", "search or type web address"}

        strong_positive_signals = [
            "search or type web address" in labels or title == "search or type web address" or is_search_hint,
            "start voice search" in labels,
            "discover" in labels,
            "options for discover" in labels,
            "update available. more options" in labels,
            "1 open tab, tap to switch tabs" in labels or any("open tab" in label for label in labels),
            "home" in labels,
        ]
        strong_signal_count = sum(1 for matched in strong_positive_signals if matched)
        has_search_surface = strong_positive_signals[0]
        has_chrome_controls = any(strong_positive_signals[1:])

        return (has_search_surface and has_chrome_controls) or strong_signal_count >= 4

    def _chrome_baseline_diagnostics(self, screen: dict[str, Any], expected_package: str) -> dict[str, Any]:
        package_name = str(screen.get("package_name") or "").strip()
        labels = self._screen_labels(screen)
        title = str(screen.get("screen_title_guess") or "").strip().lower()
        meta = screen.get("meta", {})
        address_bar_text = self._address_bar_text(screen).strip().lower()
        semantic = screen.get("semantic", {})

        positive_signals: list[str] = []
        ignored_noise: list[str] = []
        rejection_reasons: list[str] = []

        signal_checks = [
            ("search_box", "search or type web address" in labels or "search or type web address" in title or address_bar_text in {"", "search or type web address"}),
            ("voice_search", "start voice search" in labels),
            ("discover_feed_header", "discover" in labels),
            ("discover_menu", "options for discover" in labels),
            ("menu_button", "update available. more options" in labels),
            ("tab_switcher", "1 open tab, tap to switch tabs" in labels or any("open tab" in label for label in labels)),
            ("home_button", "home" in labels),
        ]

        for signal_name, matched in signal_checks:
            if matched:
                positive_signals.append(signal_name)

        noisy_indicators = (
            "support.google.com",
            "google chrome help",
            "help",
            "facebook",
            "youtube",
            "instagram",
            "article",
            "news",
            "story",
        )
        for label in sorted(labels):
            if any(indicator in label for indicator in noisy_indicators):
                ignored_noise.append(label)

        has_modal = bool(meta.get("has_modal"))
        is_expected_package = package_name == expected_package
        if not is_expected_package:
            rejection_reasons.append("package_mismatch")
        if has_modal:
            rejection_reasons.append("modal_surface")
        if self._looks_like_help_or_support_destination(screen, expected_package):
            rejection_reasons.append("help_or_support_destination")
        elif semantic.get("screen_type") == "browser_menu":
            rejection_reasons.append("browser_menu_surface")
        elif meta.get("is_page_like") and (meta.get("has_webview") or meta.get("has_address_bar")):
            rejection_reasons.append("page_like_destination")

        is_baseline = not rejection_reasons and self._looks_like_chrome_home_surface(screen, expected_package)

        return {
            "is_baseline": is_baseline,
            "package_matches": is_expected_package,
            "has_modal": has_modal,
            "positive_signals": positive_signals,
            "ignored_noise": ignored_noise,
            "title": title,
            "address_bar_text": address_bar_text,
            "screen_type": semantic.get("screen_type") or meta.get("screen_type") or "unknown",
            "rejection_reasons": rejection_reasons,
        }

    def is_probable_chrome_baseline(self, screen: dict[str, Any], expected_package: str) -> bool:
        diagnostics = self._chrome_baseline_diagnostics(screen, expected_package)
        return bool(diagnostics["is_baseline"])

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
            semantic_type = screen.get("semantic", {}).get("screen_type") or screen.get("meta", {}).get("screen_type") or "unknown"
            print(
                "[mobile] Normalization probe: "
                f"title={title}, type={semantic_type}, modal={screen.get('meta', {}).get('has_modal')}, "
                f"visible_text={screen.get('visible_text', [])[:4]}"
            )

            if str(screen.get("package_name") or "").strip() != expected_package:
                print("[mobile] Target app is not in foreground during normalization. Re-activating it.")
                device_manager.activate_target_app()
                continue

            diagnostics = self._chrome_baseline_diagnostics(screen, expected_package)
            if diagnostics["is_baseline"]:
                print(
                    "[mobile] Baseline Chrome surface confirmed. "
                    f"positive_signals={diagnostics['positive_signals']}"
                )
                if diagnostics["ignored_noise"]:
                    print(
                        "[mobile] Ignoring baseline noise: "
                        f"{diagnostics['ignored_noise'][:4]}"
                    )
                return

            if diagnostics["positive_signals"] or diagnostics["rejection_reasons"]:
                print(
                    "[mobile] Baseline rejected. "
                    f"type={diagnostics['screen_type']}, "
                    f"reasons={diagnostics['rejection_reasons']}, "
                    f"positive_signals={diagnostics['positive_signals']}, "
                    f"ignored_noise={diagnostics['ignored_noise'][:4]}"
                )

            if self._looks_like_compact_modal_menu(screen, expected_package) and back_presses_used < max_back_presses:
                print("[mobile] Detected stale modal/menu state, sending back.")
                device_manager.press_back()
                back_presses_used += 1
                time.sleep(post_back_delay_s)
                continue

            if back_presses_used < max_back_presses:
                print("[mobile] Detected non-baseline Chrome surface, retrying recovery with back.")
                device_manager.press_back()
                back_presses_used += 1
                time.sleep(post_back_delay_s)
                continue

            if relaunches_used < max_relaunches:
                print("[mobile] Back recovery exhausted. Re-launching Chrome main activity.")
                device_manager.start_target_activity()
                relaunches_used += 1
                time.sleep(post_relaunch_delay_s)
                continue

            break

        if last_screen:
            print(
                "[mobile] Baseline normalization failed. "
                f"Last observed title={last_screen.get('screen_title_guess') or '(untitled)'}, "
                f"type={last_screen.get('semantic', {}).get('screen_type') or 'unknown'}, "
                f"visible_text={last_screen.get('visible_text', [])[:4]}."
            )
        raise RuntimeError("Could not normalize Chrome to a baseline state before entry capture.")