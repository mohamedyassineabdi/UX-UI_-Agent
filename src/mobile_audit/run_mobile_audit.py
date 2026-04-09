from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.config.audit_config import AUDIT_CONFIG

from .device_manager import AndroidDeviceManager, AndroidSessionConfig
from .mobile_artifact_writer import create_mobile_audit_output_dir, write_mobile_block1_artifacts
from .mobile_runner import MobileRunner, MobileRunnerConfig


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _mobile_defaults() -> dict:
    return AUDIT_CONFIG["mobileAudit"]


def build_parser() -> argparse.ArgumentParser:
    defaults = _mobile_defaults()
    appium_defaults = defaults["appium"]
    capture_defaults = defaults["capture"]

    parser = argparse.ArgumentParser(description="Run Block 1 Android mobile extraction.")
    parser.add_argument("--app-package", required=True, help="Android application package name.")
    parser.add_argument("--app-activity", required=True, help="Android launch activity.")
    parser.add_argument("--appium-url", default=appium_defaults["url"], help="Appium server URL.")
    parser.add_argument("--device-name", default=appium_defaults["deviceName"], help="ADB/Appium device name.")
    parser.add_argument("--platform-version", default="", help="Optional Android platform version.")
    parser.add_argument("--udid", default="", help="Optional emulator/device UDID.")
    parser.add_argument("--job-id", default="", help="Optional output job id.")
    parser.add_argument("--output-root", default=defaults["paths"]["outputRoot"], help="Root directory for generated mobile audits.")
    parser.add_argument("--app-wait-activity", default="*", help="Appium appWaitActivity capability.")
    parser.add_argument(
        "--launch-timeout-ms",
        type=int,
        default=capture_defaults["launchTimeoutMs"],
        help="How long to wait for the app to reach a foreground activity.",
    )
    parser.add_argument(
        "--settle-delay-ms",
        type=int,
        default=capture_defaults["settleDelayMs"],
        help="Initial delay before reading the first hierarchy.",
    )
    parser.add_argument(
        "--stabilization-timeout-ms",
        type=int,
        default=capture_defaults["stabilizationTimeoutMs"],
        help="Maximum time to wait for a stable hierarchy.",
    )
    parser.add_argument(
        "--stabilization-poll-ms",
        type=int,
        default=capture_defaults["stabilizationPollMs"],
        help="Polling cadence while waiting for a stable hierarchy.",
    )
    parser.add_argument(
        "--no-reset",
        dest="no_reset",
        action="store_true",
        default=bool(appium_defaults["noReset"]),
        help="Preserve installed app state between runs.",
    )
    parser.add_argument(
        "--full-reset",
        dest="no_reset",
        action="store_false",
        help="Disable no-reset and let Appium start from a clean app state.",
    )
    return parser


def _build_session_config(args: argparse.Namespace) -> AndroidSessionConfig:
    appium_defaults = _mobile_defaults()["appium"]
    return AndroidSessionConfig(
        appium_url=args.appium_url,
        app_package=args.app_package,
        app_activity=args.app_activity,
        device_name=args.device_name,
        udid=args.udid or None,
        platform_version=args.platform_version or None,
        app_wait_activity=args.app_wait_activity,
        no_reset=args.no_reset,
        auto_grant_permissions=bool(appium_defaults["autoGrantPermissions"]),
        new_command_timeout_sec=int(appium_defaults["newCommandTimeoutSec"]),
        adb_exec_timeout_ms=int(appium_defaults["adbExecTimeoutMs"]),
        uiautomator2_server_install_timeout_ms=int(appium_defaults["uiautomator2ServerInstallTimeoutMs"]),
        uiautomator2_server_launch_timeout_ms=int(appium_defaults["uiautomator2ServerLaunchTimeoutMs"]),
        uiautomator2_server_read_timeout_ms=int(appium_defaults["uiautomator2ServerReadTimeoutMs"]),
        android_install_timeout_ms=int(appium_defaults["androidInstallTimeoutMs"]),
        app_wait_duration_ms=int(appium_defaults["appWaitDurationMs"]),
        skip_device_initialization=bool(appium_defaults["skipDeviceInitialization"]),
        disable_window_animation=bool(appium_defaults["disableWindowAnimation"]),
        device_ready_timeout_ms=int(appium_defaults["deviceReadyTimeoutMs"]),
        device_ready_poll_ms=int(appium_defaults["deviceReadyPollMs"]),
        launch_timeout_ms=args.launch_timeout_ms,
    )


def _build_runner_config(args: argparse.Namespace) -> MobileRunnerConfig:
    return MobileRunnerConfig(
        settle_delay_ms=args.settle_delay_ms,
        stabilization_timeout_ms=args.stabilization_timeout_ms,
        stabilization_poll_ms=args.stabilization_poll_ms,
    )


def run_block1(args: argparse.Namespace) -> Path:
    output_dir = create_mobile_audit_output_dir(job_id=args.job_id or None, output_root=args.output_root)
    session_config = _build_session_config(args)
    runner_config = _build_runner_config(args)

    print("[1/4] Connecting to Appium and launching the Android app.")
    manager = AndroidDeviceManager(session_config)
    driver = manager.connect()

    try:
        print("[2/4] Capturing first screen screenshot and hierarchy.")
        runner = MobileRunner(driver, runner_config)
        capture = runner.capture_current_screen(screen_id="screen_001")

        print("[3/4] Writing mobile extraction artifacts.")
        write_mobile_block1_artifacts(
            output_dir=output_dir,
            app_info=manager.build_app_info(),
            screen_record=capture["screen"],
            screenshot_png=capture["screenshot_png"],
            hierarchy_xml=capture["hierarchy_xml"],
        )
    finally:
        manager.close()

    print("[4/4] Mobile extraction complete.")
    print(f"Artifacts written to: {output_dir}")
    return output_dir


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_block1(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
