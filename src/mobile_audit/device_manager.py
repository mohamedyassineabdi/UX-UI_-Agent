from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.common.exceptions import WebDriverException


@dataclass(slots=True)
class AndroidSessionConfig:
    appium_url: str
    app_package: str
    app_activity: str
    platform_name: str = "Android"
    automation_name: str = "UiAutomator2"
    device_name: str = "Android Emulator"
    adb_path: Optional[str] = None
    android_sdk_root: Optional[str] = None
    udid: Optional[str] = None
    platform_version: Optional[str] = None
    app_wait_activity: str = "*"
    auto_grant_permissions: bool = True
    no_reset: bool = True
    new_command_timeout_sec: int = 120
    adb_exec_timeout_ms: int = 120000
    uiautomator2_server_install_timeout_ms: int = 120000
    uiautomator2_server_launch_timeout_ms: int = 120000
    uiautomator2_server_read_timeout_ms: int = 120000
    android_install_timeout_ms: int = 120000
    app_wait_duration_ms: int = 120000
    skip_device_initialization: bool = False
    disable_window_animation: bool = True
    device_ready_timeout_ms: int = 180000
    device_ready_poll_ms: int = 2000
    launch_timeout_ms: int = 15000


class AndroidDeviceManager:
    def __init__(self, config: AndroidSessionConfig):
        self.config = config
        self.driver: Optional[webdriver.Remote] = None
        self._resolved_adb_path: Optional[str] = None

    def _resolve_adb_path(self) -> str:
        if self._resolved_adb_path:
            return self._resolved_adb_path

        candidates: list[Path] = []
        configured_adb_path = str(self.config.adb_path or "").strip()
        configured_sdk_root = str(self.config.android_sdk_root or "").strip()
        env_sdk_root = str(os.getenv("ANDROID_SDK_ROOT") or "").strip()
        env_android_home = str(os.getenv("ANDROID_HOME") or "").strip()

        if configured_adb_path:
            candidates.append(Path(configured_adb_path))
        if configured_sdk_root:
            candidates.append(Path(configured_sdk_root) / "platform-tools" / "adb.exe")
            candidates.append(Path(configured_sdk_root) / "platform-tools" / "adb")
        if env_sdk_root:
            candidates.append(Path(env_sdk_root) / "platform-tools" / "adb.exe")
            candidates.append(Path(env_sdk_root) / "platform-tools" / "adb")
        if env_android_home:
            candidates.append(Path(env_android_home) / "platform-tools" / "adb.exe")
            candidates.append(Path(env_android_home) / "platform-tools" / "adb")

        for candidate in candidates:
            if candidate.exists():
                self._resolved_adb_path = str(candidate.resolve())
                print(f"[mobile] Using adb executable: {self._resolved_adb_path}")
                return self._resolved_adb_path

        path_fallback = shutil.which("adb")
        if path_fallback:
            self._resolved_adb_path = str(Path(path_fallback).resolve())
            print(f"[mobile] Using adb executable from PATH: {self._resolved_adb_path}")
            return self._resolved_adb_path

        raise RuntimeError(
            "adb could not be resolved. Configure mobileAudit.appium.adbPath, "
            "mobileAudit.appium.androidSdkRoot, ANDROID_SDK_ROOT, or ANDROID_HOME."
        )

    def _adb_base_command(self) -> list[str]:
        command = [self._resolve_adb_path()]
        if self.config.udid:
            command.extend(["-s", self.config.udid])
        return command

    def _run_adb(self, *args: str, timeout_ms: int = 15000) -> subprocess.CompletedProcess[str]:
        command = [*self._adb_base_command(), *args]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_ms) / 1000.0,
            check=False,
        )

    def _adb_stdout(self, *args: str, timeout_ms: int = 15000) -> str:
        try:
            result = self._run_adb(*args, timeout_ms=timeout_ms)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "adb was not found on PATH. Install Android platform-tools and ensure adb is available."
            ) from exc
        except subprocess.TimeoutExpired:
            return ""
        return (result.stdout or "").strip()

    def _log_adb_devices(self) -> None:
        try:
            result = self._run_adb("devices", timeout_ms=10000)
        except Exception as exc:
            print(f"[mobile] Unable to read adb devices output: {exc}")
            return
        output = (result.stdout or "").strip() or "(no adb devices output)"
        print("[mobile] adb devices:")
        for line in output.splitlines():
            print(f"[mobile]   {line}")

    def _wait_for_emulator_ready(self) -> None:
        print("[mobile] Checking emulator/device readiness before creating Appium session.")
        self._log_adb_devices()

        try:
            wait_result = self._run_adb("wait-for-device", timeout_ms=min(self.config.device_ready_timeout_ms, 30000))
            if wait_result.returncode != 0 and (wait_result.stderr or "").strip():
                print(f"[mobile] adb wait-for-device returned: {(wait_result.stderr or '').strip()}")
        except FileNotFoundError as exc:
            raise RuntimeError(
                "adb was not found on PATH. Install Android platform-tools and ensure adb is available."
            ) from exc
        except subprocess.TimeoutExpired:
            print("[mobile] adb wait-for-device did not complete immediately; continuing to poll boot properties.")

        deadline = time.time() + (self.config.device_ready_timeout_ms / 1000.0)
        last_state = ""
        last_boot_completed = ""
        last_boot_anim = ""

        while time.time() < deadline:
            state = self._adb_stdout("get-state", timeout_ms=8000)
            boot_completed = self._adb_stdout("shell", "getprop", "sys.boot_completed", timeout_ms=8000)
            boot_anim = self._adb_stdout("shell", "getprop", "init.svc.bootanim", timeout_ms=8000)

            if state != last_state or boot_completed != last_boot_completed or boot_anim != last_boot_anim:
                print(
                    "[mobile] Emulator state: "
                    f"adb={state or '(unknown)'}, "
                    f"sys.boot_completed={boot_completed or '(empty)'}, "
                    f"init.svc.bootanim={boot_anim or '(empty)'}"
                )
                last_state = state
                last_boot_completed = boot_completed
                last_boot_anim = boot_anim

            if state == "device" and boot_completed == "1" and boot_anim in {"", "stopped"}:
                print("[mobile] Emulator/device is fully booted and ready for Appium.")
                return

            time.sleep(max(200, self.config.device_ready_poll_ms) / 1000.0)

        raise RuntimeError(
            "Emulator/device did not become ready before Appium session creation. "
            f"Last observed state: adb={last_state or '(unknown)'}, "
            f"sys.boot_completed={last_boot_completed or '(empty)'}, "
            f"init.svc.bootanim={last_boot_anim or '(empty)'}."
        )

    def _build_options(self) -> UiAutomator2Options:
        options = UiAutomator2Options()
        capabilities = {
            "platformName": self.config.platform_name,
            "appium:automationName": self.config.automation_name,
            "appium:deviceName": self.config.device_name,
            "appium:appPackage": self.config.app_package,
            "appium:appActivity": self.config.app_activity,
            "appium:appWaitActivity": self.config.app_wait_activity,
            "appium:appWaitDuration": self.config.app_wait_duration_ms,
            "appium:autoGrantPermissions": self.config.auto_grant_permissions,
            "appium:noReset": self.config.no_reset,
            "appium:newCommandTimeout": self.config.new_command_timeout_sec,
            "appium:adbExecTimeout": self.config.adb_exec_timeout_ms,
            "appium:uiautomator2ServerInstallTimeout": self.config.uiautomator2_server_install_timeout_ms,
            "appium:uiautomator2ServerLaunchTimeout": self.config.uiautomator2_server_launch_timeout_ms,
            "appium:uiautomator2ServerReadTimeout": self.config.uiautomator2_server_read_timeout_ms,
            "appium:androidInstallTimeout": self.config.android_install_timeout_ms,
            "appium:skipDeviceInitialization": self.config.skip_device_initialization,
            "appium:disableWindowAnimation": self.config.disable_window_animation,
        }
        if self.config.udid:
            capabilities["appium:udid"] = self.config.udid
        if self.config.platform_version:
            capabilities["platformVersion"] = self.config.platform_version

        for name, value in capabilities.items():
            options.set_capability(name, value)
        return options

    def _log_session_request(self) -> None:
        print("[mobile] Creating Appium session with:")
        print(f"[mobile]   Appium URL: {self.config.appium_url}")
        print(f"[mobile]   adb executable: {self._resolve_adb_path()}")
        print(f"[mobile]   Device name: {self.config.device_name}")
        print(
            "[mobile]   Android SDK root: "
            f"{self.config.android_sdk_root or os.getenv('ANDROID_SDK_ROOT') or os.getenv('ANDROID_HOME') or '(not set)'}"
        )
        print(f"[mobile]   Platform version: {self.config.platform_version or '(default)'}")
        print(f"[mobile]   UDID: {self.config.udid or '(not set)'}")
        print(f"[mobile]   App package: {self.config.app_package}")
        print(f"[mobile]   App activity: {self.config.app_activity}")
        print(f"[mobile]   appWaitActivity: {self.config.app_wait_activity}")
        print(f"[mobile]   adbExecTimeout: {self.config.adb_exec_timeout_ms}ms")
        print(
            f"[mobile]   uiautomator2ServerInstallTimeout: "
            f"{self.config.uiautomator2_server_install_timeout_ms}ms"
        )
        print(
            f"[mobile]   uiautomator2ServerLaunchTimeout: "
            f"{self.config.uiautomator2_server_launch_timeout_ms}ms"
        )
        print(
            f"[mobile]   uiautomator2ServerReadTimeout: "
            f"{self.config.uiautomator2_server_read_timeout_ms}ms"
        )
        print(f"[mobile]   androidInstallTimeout: {self.config.android_install_timeout_ms}ms")
        print(f"[mobile]   appWaitDuration: {self.config.app_wait_duration_ms}ms")
        print(f"[mobile]   newCommandTimeout: {self.config.new_command_timeout_sec}s")
        print(f"[mobile]   skipDeviceInitialization: {self.config.skip_device_initialization}")
        print(f"[mobile]   disableWindowAnimation: {self.config.disable_window_animation}")
        print(f"[mobile]   noReset: {self.config.no_reset}")

    def connect(self) -> webdriver.Remote:
        options = self._build_options()
        self._wait_for_emulator_ready()
        self._log_session_request()
        try:
            self.driver = webdriver.Remote(self.config.appium_url, options=options)
        except WebDriverException as exc:
            raise RuntimeError(
                "Appium session creation failed. Check that the emulator is fully booted, "
                "Appium UIAutomator2 is installed, and the app package/activity are correct. "
                f"Requested adbExecTimeout={self.config.adb_exec_timeout_ms}ms and "
                f"uiautomator2ServerLaunchTimeout={self.config.uiautomator2_server_launch_timeout_ms}ms."
            ) from exc
        self.driver.implicitly_wait(0)
        self.wait_for_app_focus(self.config.launch_timeout_ms)
        print(
            "[mobile] Session ready: "
            f"package={self.current_package() or self.config.app_package}, "
            f"activity={self.current_activity() or self.config.app_activity}"
        )
        return self.driver

    def wait_for_app_focus(self, timeout_ms: int) -> None:
        if not self.driver:
            raise RuntimeError("Android driver session is not initialized.")

        deadline = time.time() + (timeout_ms / 1000.0)
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                package_name = self.current_package()
                activity_name = self.current_activity()
                if package_name and activity_name:
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.4)

        if last_error:
            raise RuntimeError(
                f"App did not reach a stable foreground activity within {timeout_ms}ms."
            ) from last_error
        raise RuntimeError(f"App did not reach a stable foreground activity within {timeout_ms}ms.")

    def current_package(self) -> str:
        if not self.driver:
            return ""
        try:
            return str(self.driver.current_package or "").strip()
        except Exception:
            return ""

    def current_activity(self) -> str:
        if not self.driver:
            return ""
        try:
            return str(self.driver.current_activity or "").strip()
        except Exception:
            return ""

    def build_app_info(self) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Android driver session is not initialized.")

        capabilities = dict(getattr(self.driver, "capabilities", {}) or {})
        return {
            "platform": "android",
            "automation": self.config.automation_name,
            "appiumUrl": self.config.appium_url,
            "sessionId": getattr(self.driver, "session_id", ""),
            "deviceName": capabilities.get("deviceName") or self.config.device_name,
            "platformVersion": capabilities.get("platformVersion") or self.config.platform_version or "",
            "udid": capabilities.get("udid") or self.config.udid or "",
            "appPackage": self.current_package() or self.config.app_package,
            "appActivity": self.current_activity() or self.config.app_activity,
            "capabilities": {
                "platformName": capabilities.get("platformName") or self.config.platform_name,
                "automationName": capabilities.get("appium:automationName")
                or capabilities.get("automationName")
                or self.config.automation_name,
                "newCommandTimeout": capabilities.get("appium:newCommandTimeout")
                or capabilities.get("newCommandTimeout")
                or self.config.new_command_timeout_sec,
                "adbExecTimeout": capabilities.get("appium:adbExecTimeout") or self.config.adb_exec_timeout_ms,
                "uiautomator2ServerInstallTimeout": capabilities.get("appium:uiautomator2ServerInstallTimeout")
                or self.config.uiautomator2_server_install_timeout_ms,
                "uiautomator2ServerLaunchTimeout": capabilities.get("appium:uiautomator2ServerLaunchTimeout")
                or self.config.uiautomator2_server_launch_timeout_ms,
                "uiautomator2ServerReadTimeout": capabilities.get("appium:uiautomator2ServerReadTimeout")
                or self.config.uiautomator2_server_read_timeout_ms,
                "androidInstallTimeout": capabilities.get("appium:androidInstallTimeout")
                or self.config.android_install_timeout_ms,
                "appWaitDuration": capabilities.get("appium:appWaitDuration") or self.config.app_wait_duration_ms,
                "skipDeviceInitialization": capabilities.get("appium:skipDeviceInitialization")
                if capabilities.get("appium:skipDeviceInitialization") is not None
                else self.config.skip_device_initialization,
                "disableWindowAnimation": capabilities.get("appium:disableWindowAnimation")
                if capabilities.get("appium:disableWindowAnimation") is not None
                else self.config.disable_window_animation,
                "noReset": capabilities.get("appium:noReset")
                if capabilities.get("appium:noReset") is not None
                else capabilities.get("noReset"),
                "autoGrantPermissions": capabilities.get("appium:autoGrantPermissions")
                if capabilities.get("appium:autoGrantPermissions") is not None
                else capabilities.get("autoGrantPermissions"),
            },
        }

    def close(self) -> None:
        if not self.driver:
            return
        try:
            self.driver.quit()
        finally:
            self.driver = None
