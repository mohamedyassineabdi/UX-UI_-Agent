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

    def capture_current_screen(self, screen_id: str = "screen_001") -> dict[str, Any]:
        hierarchy_xml = self.wait_for_stabilization()
        screenshot_png = self.driver.get_screenshot_as_png()
        parsed = extract_hierarchy(hierarchy_xml)
        tappables = build_tappables(parsed["elements"])

        package_name = str(getattr(self.driver, "current_package", "") or "").strip()
        try:
            activity_name = str(getattr(self.driver, "current_activity", "") or "").strip()
        except Exception:
            activity_name = ""

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
        }

        return {
            "screen": screen_record,
            "screenshot_png": screenshot_png,
            "hierarchy_xml": hierarchy_xml,
        }
