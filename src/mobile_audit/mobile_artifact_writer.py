from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.config.audit_config import AUDIT_CONFIG
from src.utils.file_utils import ensure_dir, write_json_file, write_text_file


ROOT_DIR = Path(__file__).resolve().parents[2]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_mobile_audit_output_dir(job_id: Optional[str] = None, output_root: Optional[str] = None) -> Path:
    configured_root = Path(output_root or AUDIT_CONFIG["mobileAudit"]["paths"]["outputRoot"])
    root = configured_root if configured_root.is_absolute() else ROOT_DIR / configured_root
    resolved_job_id = job_id or uuid.uuid4().hex[:12]
    output_dir = root / resolved_job_id
    ensure_dir(str(output_dir))
    ensure_dir(str(output_dir / AUDIT_CONFIG["mobileAudit"]["paths"]["screenshotDirName"]))
    ensure_dir(str(output_dir / AUDIT_CONFIG["mobileAudit"]["paths"]["hierarchyDirName"]))
    return output_dir


def write_mobile_audit_artifacts(
    output_dir: Path,
    app_info: dict[str, Any],
    captures: list[dict[str, Any]],
    screens: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
) -> dict[str, str]:
    screenshots_dir = output_dir / AUDIT_CONFIG["mobileAudit"]["paths"]["screenshotDirName"]
    hierarchies_dir = output_dir / AUDIT_CONFIG["mobileAudit"]["paths"]["hierarchyDirName"]

    app_info_path = output_dir / "app_info.json"
    mobile_screen_map_path = output_dir / "mobile_screen_map.json"
    mobile_ui_extraction_path = output_dir / "mobile_ui_extraction.json"
    mobile_interaction_results_path = output_dir / "mobile_interaction_results.json"

    for capture in captures:
        screen = capture["screen"]
        screenshot_path = screenshots_dir / f"{screen['screen_id']}.png"
        hierarchy_path = hierarchies_dir / f"{screen['screen_id']}.xml"
        screenshot_path.write_bytes(capture["screenshot_png"])
        write_text_file(str(hierarchy_path), capture["hierarchy_xml"])

    entry_screen_id = screens[0]["screen_id"] if screens else ""

    write_json_file(
        str(app_info_path),
        {
            **app_info,
            "capturedAt": _now_iso(),
            "entryScreenId": entry_screen_id,
        },
    )
    write_json_file(
        str(mobile_screen_map_path),
        {
            "generatedAt": _now_iso(),
            "entry_screen_id": entry_screen_id,
            "screens": [
                {
                    "screen_id": screen["screen_id"],
                    "package_name": screen["package_name"],
                    "activity_name": screen["activity_name"],
                    "screen_fingerprint": screen["screen_fingerprint"],
                    "screen_title_guess": screen["screen_title_guess"],
                    "screenshot_path": screen["screenshot_path"],
                    "hierarchy_path": screen["hierarchy_path"],
                    "meta": screen["meta"],
                }
                for screen in screens
            ],
            "interactions": interactions,
        },
    )
    write_json_file(
        str(mobile_ui_extraction_path),
        {
            "generatedAt": _now_iso(),
            "screenCount": len(screens),
            "interactionCount": len(interactions),
            "screens": screens,
            "interactions": interactions,
        },
    )
    write_json_file(
        str(mobile_interaction_results_path),
        {
            "generatedAt": _now_iso(),
            "interactionCount": len(interactions),
            "interactions": interactions,
        },
    )

    return {
        "output_dir": str(output_dir),
        "app_info_path": str(app_info_path),
        "mobile_screen_map_path": str(mobile_screen_map_path),
        "mobile_ui_extraction_path": str(mobile_ui_extraction_path),
        "mobile_interaction_results_path": str(mobile_interaction_results_path),
    }


def write_mobile_block1_artifacts(
    output_dir: Path,
    app_info: dict[str, Any],
    screen_record: dict[str, Any],
    screenshot_png: bytes,
    hierarchy_xml: str,
) -> dict[str, str]:
    return write_mobile_audit_artifacts(
        output_dir=output_dir,
        app_info=app_info,
        captures=[
            {
                "screen": screen_record,
                "screenshot_png": screenshot_png,
                "hierarchy_xml": hierarchy_xml,
            }
        ],
        screens=[screen_record],
        interactions=[],
    )
