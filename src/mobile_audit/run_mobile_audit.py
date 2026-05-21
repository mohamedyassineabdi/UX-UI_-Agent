from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.config.audit_config import AUDIT_CONFIG

from .device_manager import AndroidDeviceManager, AndroidSessionConfig
from .mobile_artifact_writer import create_mobile_audit_output_dir, write_mobile_audit_artifacts
from .mobile_runner import MobileRunner, MobileRunnerConfig
from .screen_explorer import BoundedScreenExplorer, ExplorerConfig


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
    parser.add_argument("--adb-path", default=appium_defaults.get("adbPath", ""), help="Optional absolute adb executable path.")
    parser.add_argument("--android-sdk-root", default=appium_defaults.get("androidSdkRoot", ""), help="Optional Android SDK root used to resolve adb.")
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
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only write extraction artifacts. By default the command also writes mobile_gtm_audit.json and gtm-report/.",
    )
    parser.add_argument(
        "--deploy-vercel",
        action="store_true",
        help="Package and deploy the generated mobile report to Vercel after report generation.",
    )
    parser.add_argument(
        "--vercel-output-dir",
        default="",
        help="Optional directory for the packaged Vercel static report. Defaults to <output-dir>/vercel-gtm-report.",
    )
    parser.add_argument(
        "--vercel-preview",
        action="store_true",
        help="Create a Vercel preview deployment instead of a production deployment.",
    )
    parser.add_argument(
        "--vercel-prod",
        action="store_true",
        help="Create a production Vercel deployment and update the production alias.",
    )
    return parser


def _build_session_config(args: argparse.Namespace) -> AndroidSessionConfig:
    appium_defaults = _mobile_defaults()["appium"]
    return AndroidSessionConfig(
        appium_url=args.appium_url,
        app_package=args.app_package,
        app_activity=args.app_activity,
        device_name=args.device_name,
        adb_path=args.adb_path or None,
        android_sdk_root=args.android_sdk_root or None,
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
    defaults = _mobile_defaults()
    initialization_defaults = defaults.get("initialization", {})
    exploration_defaults = defaults.get("exploration", {})
    return MobileRunnerConfig(
        settle_delay_ms=args.settle_delay_ms,
        stabilization_timeout_ms=args.stabilization_timeout_ms,
        stabilization_poll_ms=args.stabilization_poll_ms,
        initialization_max_back_presses=int(initialization_defaults.get("maxBackPresses", 2)),
        initialization_post_back_delay_ms=int(initialization_defaults.get("postBackDelayMs", 900)),
        initialization_max_relaunches=int(initialization_defaults.get("maxRelaunches", 1)),
        initialization_post_relaunch_delay_ms=int(initialization_defaults.get("postRelaunchDelayMs", 1400)),
        scroll_post_delay_ms=int(exploration_defaults.get("scrollPostDelayMs", 900)),
        scroll_percent=float(exploration_defaults.get("scrollPercent", 0.82)),
    )


def _build_explorer_config() -> ExplorerConfig:
    exploration_defaults = _mobile_defaults().get("exploration", {})
    return ExplorerConfig(
        max_screens=int(exploration_defaults.get("maxScreens", 80)),
        max_actions_total=int(exploration_defaults.get("maxActionsTotal", 192)),
        max_actions_per_screen=int(exploration_defaults.get("maxActionsPerScreen", 8)),
        max_scrolls_per_path=int(exploration_defaults.get("maxScrollsPerPath", 5)),
        max_backtrack_steps=int(exploration_defaults.get("maxBacktrackSteps", 4)),
    )


def run_block1(args: argparse.Namespace) -> Path:
    output_dir = create_mobile_audit_output_dir(job_id=args.job_id or None, output_root=args.output_root)
    session_config = _build_session_config(args)
    runner_config = _build_runner_config(args)
    explorer_config = _build_explorer_config()

    print("[1/5] Connecting to Appium and launching the Android app.")
    manager = AndroidDeviceManager(session_config)
    driver = manager.connect()

    try:
        print("[2/5] Normalizing baseline state and capturing the first screen.")
        runner = MobileRunner(driver, runner_config)
        runner.normalize_to_baseline(manager, expected_package=args.app_package)
        first_capture = runner.capture_current_screen(screen_id="screen_001")

        print("[3/5] Running bounded all-safe exploration plus scroll discovery.")
        explorer = BoundedScreenExplorer(driver, runner, manager, config=explorer_config)
        exploration_result = explorer.run_bounded_exploration(first_capture)

        print("[4/5] Writing mobile extraction artifacts.")
        write_mobile_audit_artifacts(
            output_dir=output_dir,
            app_info=manager.build_app_info(),
            captures=exploration_result.captures,
            screens=exploration_result.screens,
            interactions=exploration_result.interactions,
        )
    finally:
        manager.close()

    print("[5/5] Mobile extraction complete.")
    print(f"Artifacts written to: {output_dir}")
    return output_dir


def generate_mobile_outputs(output_dir: Path, app_label: str = "Android App Audit") -> None:
    audit_json = output_dir / "mobile_gtm_audit.json"
    report_dir = output_dir / "gtm-report"

    print("[mobile] Generating mobile GTM audit JSON.")
    from .generate_mobile_audit import build_payload, save_json

    save_json(audit_json, build_payload(output_dir, app_label=app_label))
    print(f"[mobile] Mobile GTM audit written to: {audit_json}")

    print("[mobile] Generating mobile GTM HTML report.")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.gtm_audit.generate_gtm_report",
            "--input",
            str(audit_json),
            "--output-dir",
            str(report_dir),
        ],
        cwd=str(ROOT_DIR),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Mobile GTM report generation failed with exit code {result.returncode}.")
    print(f"[mobile] Mobile GTM report written to: {report_dir / 'index.html'}")


def deploy_mobile_report(output_dir: Path, vercel_output_dir: str = "", *, production: bool = True) -> str:
    report_dir = output_dir / "gtm-report"
    static_dir = Path(vercel_output_dir) if vercel_output_dir else output_dir / "vercel-gtm-report"
    if not static_dir.is_absolute():
        static_dir = ROOT_DIR / static_dir

    print("[mobile] Packaging mobile GTM report for Vercel.")
    from src.gtm_audit.vercel_static_deploy import deploy_to_vercel, package_report_for_vercel

    audit_slug = output_dir.name
    output_index = package_report_for_vercel(report_dir, static_dir, audit_slug=audit_slug)
    print(f"[mobile] Vercel static report packaged at: {output_index}")
    print("[mobile] Deploying mobile GTM report to Vercel.")
    url = deploy_to_vercel(
        static_dir,
        production=production,
        public_path=f"audits/{audit_slug}",
        prefer_alias=production,
    )
    if not url:
        raise RuntimeError("Vercel deployment completed but no deployment URL was found in CLI output.")
    print(f"Vercel deployment URL: {url}")
    return url


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_dir = run_block1(args)
    if not args.extract_only:
        generate_mobile_outputs(output_dir)
        if args.deploy_vercel:
            deploy_mobile_report(output_dir, args.vercel_output_dir, production=args.vercel_prod or not args.vercel_preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
