from __future__ import annotations

import argparse
import cgi
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
DETAILED_REPORT_DIR = GENERATED_DIR / "audit-report"
GTM_REPORT_DIR = GENERATED_DIR / "gtm-report"
DETAILED_VERCEL_DIR = GENERATED_DIR / "vercel-audit-report"
GTM_VERCEL_DIR = GENERATED_DIR / "vercel-gtm-report"
SCREENSHOT_AUDIT_DIR = GENERATED_DIR / "screenshot-audits"
MOBILE_AUDIT_DIR = GENERATED_DIR / "mobile-audits"

URL_RE = re.compile(r"https://[^\s]+")
STAGE_RE = re.compile(r"\[(?P<current>\d+)/(?P<total>\d+)\]\s*(?P<label>.+)")
SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

load_dotenv(ROOT_DIR / ".env")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_PROCESSES: dict[str, subprocess.Popen[str]] = {}
JOB_PROCESSES_LOCK = threading.Lock()
CANCELLED_RETURN_CODE = -999


def _now() -> float:
    return time.time()


def _new_job(url: str, mode: str) -> dict[str, Any]:
    job = {
        "id": uuid.uuid4().hex[:12],
        "type": "website",
        "url": url,
        "mode": mode,
        "status": "queued",
        "stage": "Queued",
        "progress": 0,
        "logs": [],
        "resultUrl": "",
        "error": "",
        "cancelRequested": False,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    return job


def _new_screenshot_job(site_name: str, screenshot_paths: list[Path], screenshot_labels: list[str]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "type": "screenshot",
        "url": "",
        "mode": "gtm",
        "siteName": site_name,
        "screenshotPaths": [str(path) for path in screenshot_paths],
        "screenshotLabels": screenshot_labels,
        "status": "queued",
        "stage": "Queued",
        "progress": 0,
        "logs": [],
        "resultUrl": "",
        "error": "",
        "cancelRequested": False,
        "createdAt": _now(),
        "updatedAt": _now(),
    }


def _new_mobile_job(
    app_label: str,
    app_package: str,
    app_activity: str,
    appium_url: str,
    device_name: str,
    platform_version: str,
    udid: str,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "type": "mobile",
        "url": "",
        "mode": "interactive",
        "appLabel": app_label,
        "appPackage": app_package,
        "appActivity": app_activity,
        "appiumUrl": appium_url,
        "deviceName": device_name,
        "platformVersion": platform_version,
        "udid": udid,
        "status": "queued",
        "stage": "Queued",
        "progress": 0,
        "logs": [],
        "resultUrl": "",
        "outputDir": "",
        "error": "",
        "cancelRequested": False,
        "createdAt": _now(),
        "updatedAt": _now(),
    }


def _snapshot_job(job: dict[str, Any]) -> dict[str, Any]:
    safe = dict(job)
    safe["logs"] = list(job.get("logs", []))[-200:]
    return safe


def _append_log(job_id: str, line: str) -> None:
    clean_line = line.rstrip()
    if not clean_line:
        return
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["logs"].append(clean_line)
        job["updatedAt"] = _now()
        match = STAGE_RE.search(clean_line)
        if match:
            current = int(match.group("current"))
            total = int(match.group("total"))
            label = match.group("label").strip().strip(".")
            job["stage"] = label
            job["progress"] = max(job.get("progress", 0), round((current - 1) / max(total, 1) * 85))
        urls = URL_RE.findall(clean_line)
        if urls:
            job["resultUrl"] = urls[-1].rstrip(".,)")


def _set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updatedAt"] = _now()


def _get_job_status(job_id: str) -> str:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return ""
        return str(job.get("status") or "")


def _is_cancel_requested(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancelRequested"))


def _mark_job_cancelled(job_id: str) -> None:
    _set_job(
        job_id,
        status="cancelled",
        stage="Audit stopped",
        progress=100,
        error="Audit stopped by user.",
        cancelRequested=True,
    )


def _finish_if_cancelled(job_id: str) -> bool:
    if _is_cancel_requested(job_id) or _get_job_status(job_id) == "cancelled":
        _mark_job_cancelled(job_id)
        return True
    return False


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=5)
            return
        except Exception:
            pass

    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return

    try:
        process.kill()
    except Exception:
        pass


def _cancel_job(job_id: str) -> tuple[dict[str, Any] | None, bool]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None, False
        status = str(job.get("status") or "")
        if status not in {"queued", "running"}:
            return _snapshot_job(job), False
        job["cancelRequested"] = True
        job["status"] = "cancelled"
        job["stage"] = "Stopping audit"
        job["progress"] = 100
        job["error"] = "Audit stopped by user."
        job["updatedAt"] = _now()

    with JOB_PROCESSES_LOCK:
        process = JOB_PROCESSES.get(job_id)
    if process:
        _terminate_process(process)

    with JOBS_LOCK:
        return _snapshot_job(JOBS[job_id]), True


def _validate_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("Website URL is required.")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid website URL, for example https://example.com.")
    return url


def _validate_required_text(value: str, field_label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field_label} is required.")
    return clean


def _safe_upload_name(raw_name: str, index: int, suffix_source: str = "") -> str:
    suffix = Path(raw_name or "").suffix.lower() or Path(suffix_source or "").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        suffix = ".png"
    stem = Path(raw_name or f"screenshot-{index}").stem or f"screenshot-{index}"
    cleaned = SAFE_FILENAME_RE.sub("-", stem).strip(".-_") or f"screenshot-{index}"
    return f"{index:03d}-{cleaned}{suffix}"


def _read_multipart_form(handler: BaseHTTPRequestHandler) -> cgi.FieldStorage:
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
        "CONTENT_LENGTH": handler.headers.get("Content-Length", "0"),
    }
    return cgi.FieldStorage(fp=handler.rfile, headers=handler.headers, environ=environ)


def _field_value(form: cgi.FieldStorage, name: str, default: str = "") -> str:
    value = form.getfirst(name, default)
    return str(value or default).strip()


def _field_json_array(form: cgi.FieldStorage, name: str) -> list[str]:
    raw = _field_value(form, name, "[]")
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item or "").strip() for item in parsed]


def _field_items(form: cgi.FieldStorage, name: str) -> list[cgi.FieldStorage]:
    value = form[name] if name in form else []
    items = value if isinstance(value, list) else [value]
    return [item for item in items if getattr(item, "filename", "")]


def _save_screenshot_uploads(form: cgi.FieldStorage, job_id: str, labels: list[str]) -> list[Path]:
    upload_dir = SCREENSHOT_AUDIT_DIR / job_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for index, item in enumerate(_field_items(form, "screenshots"), start=1):
        label = labels[index - 1] if index - 1 < len(labels) else ""
        filename = _safe_upload_name(label or item.filename, index, suffix_source=item.filename)
        target = upload_dir / filename
        with target.open("wb") as output:
            shutil.copyfileobj(item.file, output)
        if target.stat().st_size <= 0:
            target.unlink(missing_ok=True)
            continue
        saved_paths.append(target)

    if not saved_paths:
        raise ValueError("Upload at least one screenshot image.")

    return saved_paths


def _run_command(job_id: str, command: list[str], *, stage: str, progress: int) -> int:
    if _finish_if_cancelled(job_id):
        return CANCELLED_RETURN_CODE

    _set_job(job_id, stage=stage, progress=progress)
    _append_log(job_id, f"$ {' '.join(command)}")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    with JOB_PROCESSES_LOCK:
        JOB_PROCESSES[job_id] = process
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if _finish_if_cancelled(job_id):
                _terminate_process(process)
                return CANCELLED_RETURN_CODE
            _append_log(job_id, line)
        return_code = process.wait()
        if _finish_if_cancelled(job_id):
            return CANCELLED_RETURN_CODE
        return return_code
    finally:
        with JOB_PROCESSES_LOCK:
            if JOB_PROCESSES.get(job_id) is process:
                JOB_PROCESSES.pop(job_id, None)


def _report_paths_for_mode(mode: str) -> tuple[Path, Path]:
    if mode == "gtm":
        return GTM_REPORT_DIR, GTM_VERCEL_DIR
    return DETAILED_REPORT_DIR, DETAILED_VERCEL_DIR


def _run_audit_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        url = job["url"]
        mode = job["mode"]

    _set_job(job_id, status="running", stage="Starting audit", progress=2)
    pipeline_command = [
        sys.executable,
        "scripts/run_pipeline.py",
        url,
        "--mode",
        mode,
    ]
    pipeline_code = _run_command(job_id, pipeline_command, stage="Running audit pipeline", progress=5)
    if pipeline_code == CANCELLED_RETURN_CODE or _finish_if_cancelled(job_id):
        return
    if pipeline_code != 0:
        _set_job(
            job_id,
            status="failed",
            error=f"Audit pipeline failed with exit code {pipeline_code}.",
        )
        return

    if _finish_if_cancelled(job_id):
        return

    report_dir, vercel_dir = _report_paths_for_mode(mode)
    deploy_command = [
        sys.executable,
        "-m",
        "src.gtm_audit.vercel_static_deploy",
        "--report-dir",
        str(report_dir),
        "--output-dir",
        str(vercel_dir),
        "--deploy",
    ]
    deploy_code = _run_command(job_id, deploy_command, stage="Deploying report to Vercel", progress=90)
    if deploy_code == CANCELLED_RETURN_CODE or _finish_if_cancelled(job_id):
        return
    with JOBS_LOCK:
        result_url = JOBS[job_id].get("resultUrl", "")
    if deploy_code != 0:
        _set_job(
            job_id,
            status="failed",
            error=(
                "Vercel deployment failed. Run `vercel login` and `vercel link --yes` "
                "if authentication or project linking is missing."
            ),
        )
        return
    if not result_url:
        _set_job(
            job_id,
            status="failed",
            error="Vercel deployment completed but no deployment URL was detected in the CLI output.",
        )
        return

    _set_job(job_id, status="completed", stage="Completed", progress=100, resultUrl=result_url)


def _run_screenshot_audit_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        screenshot_paths = [Path(path) for path in job.get("screenshotPaths", [])]
        site_name = str(job.get("siteName") or "Screenshot Audit")
        screenshot_labels = [str(label).strip() for label in job.get("screenshotLabels", []) if str(label).strip()]

    job_dir = SCREENSHOT_AUDIT_DIR / job_id
    audit_json = job_dir / "screenshot_gtm_audit.json"
    report_dir = job_dir / "gtm-report"
    vercel_dir = job_dir / "vercel-gtm-report"

    _set_job(job_id, status="running", stage="Analyzing uploaded screenshots", progress=5)
    analysis_command = [
        sys.executable,
        "-m",
        "src.gtm_audit.generate_screenshot_gtm_audit",
        "--output",
        str(audit_json),
        "--site-name",
        site_name,
        "--screenshots",
        *[str(path) for path in screenshot_paths],
    ]
    if screenshot_labels:
        analysis_command.extend(["--screenshot-names-json", json.dumps(screenshot_labels, ensure_ascii=False)])
    analysis_code = _run_command(job_id, analysis_command, stage="Running screenshot GTM audit", progress=10)
    if analysis_code == CANCELLED_RETURN_CODE or _finish_if_cancelled(job_id):
        return
    if analysis_code != 0:
        _set_job(
            job_id,
            status="failed",
            error=f"Screenshot audit failed with exit code {analysis_code}.",
        )
        return

    report_code = _run_command(
        job_id,
        [
            sys.executable,
            "-m",
            "src.gtm_audit.generate_gtm_report",
            "--input",
            str(audit_json),
            "--output-dir",
            str(report_dir),
        ],
        stage="Generating screenshot audit report",
        progress=70,
    )
    if report_code == CANCELLED_RETURN_CODE or _finish_if_cancelled(job_id):
        return
    if report_code != 0:
        _set_job(job_id, status="failed", error=f"Report generation failed with exit code {report_code}.")
        return

    deploy_code = _run_command(
        job_id,
        [
            sys.executable,
            "-m",
            "src.gtm_audit.vercel_static_deploy",
            "--report-dir",
            str(report_dir),
            "--output-dir",
            str(vercel_dir),
            "--deploy",
        ],
        stage="Deploying screenshot audit to Vercel",
        progress=90,
    )
    if deploy_code == CANCELLED_RETURN_CODE or _finish_if_cancelled(job_id):
        return
    with JOBS_LOCK:
        result_url = JOBS[job_id].get("resultUrl", "")
    if deploy_code != 0:
        _set_job(
            job_id,
            status="failed",
            error=(
                "Vercel deployment failed. Run `vercel login` and `vercel link --yes` "
                "if authentication or project linking is missing."
            ),
        )
        return
    if not result_url:
        _set_job(
            job_id,
            status="failed",
            error="Vercel deployment completed but no deployment URL was detected in the CLI output.",
        )
        return

    _set_job(job_id, status="completed", stage="Completed", progress=100, resultUrl=result_url)


def _run_mobile_audit_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        app_label = str(job.get("appLabel") or "Android App Audit").strip() or "Android App Audit"
        app_package = str(job.get("appPackage") or "").strip()
        app_activity = str(job.get("appActivity") or "").strip()
        appium_url = str(job.get("appiumUrl") or "http://127.0.0.1:4723").strip() or "http://127.0.0.1:4723"
        device_name = str(job.get("deviceName") or "Android Emulator").strip() or "Android Emulator"
        platform_version = str(job.get("platformVersion") or "").strip()
        udid = str(job.get("udid") or "").strip()

    output_dir = MOBILE_AUDIT_DIR / job_id
    _set_job(job_id, status="running", stage="Launching Android extraction", progress=5)
    command = [
        sys.executable,
        "-m",
        "src.mobile_audit.run_mobile_audit",
        "--job-id",
        job_id,
        "--output-root",
        str(MOBILE_AUDIT_DIR),
        "--app-package",
        app_package,
        "--app-activity",
        app_activity,
        "--appium-url",
        appium_url,
        "--device-name",
        device_name,
    ]
    if platform_version:
        command.extend(["--platform-version", platform_version])
    if udid:
        command.extend(["--udid", udid])

    _append_log(job_id, f"Preparing mobile audit for: {app_label}")
    exit_code = _run_command(job_id, command, stage="Running Android Block 1 extraction", progress=15)
    if exit_code == CANCELLED_RETURN_CODE or _finish_if_cancelled(job_id):
        return
    if exit_code != 0:
        _set_job(
            job_id,
            status="failed",
            error=(
                f"Mobile app audit failed with exit code {exit_code}. "
                "Check that Appium is running, the Android emulator/device is available, "
                "and the target app package/activity are correct."
            ),
        )
        return

    if not output_dir.exists():
        _set_job(
            job_id,
            status="failed",
            error="Mobile extraction finished but the expected artifact directory was not created.",
        )
        return

    _set_job(
        job_id,
        status="completed",
        stage="Mobile extraction artifacts ready",
        progress=100,
        outputDir=str(output_dir),
    )


class AuditRequestHandler(BaseHTTPRequestHandler):
    server_version = "UXUIAuditUI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content_type = "text/html; charset=utf-8" if file_path.suffix == ".html" else "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return
        if parsed.path.startswith("/api/audits/"):
            job_id = unquote(parsed.path.rsplit("/", 1)[-1])
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = _snapshot_job(job) if job else None
            if not payload:
                self._send_json({"error": "Audit job not found."}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
            return
        if parsed.path.startswith("/static/"):
            rel = unquote(parsed.path.removeprefix("/static/"))
            target = (STATIC_DIR / rel).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            self._send_file(target)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/audits/") and parsed.path.endswith("/cancel"):
            job_id = unquote(parsed.path.removeprefix("/api/audits/").removesuffix("/cancel").strip("/"))
            payload, _cancelled = _cancel_job(job_id)
            if not payload:
                self._send_json({"error": "Audit job not found."}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
            return

        if parsed.path != "/api/audits":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                form = _read_multipart_form(self)
                audit_type = _field_value(form, "auditType", "screenshot")
                if audit_type != "screenshot":
                    raise ValueError("Multipart upload is only supported for screenshot audits.")
                site_name = _field_value(form, "siteName", "Screenshot Audit") or "Screenshot Audit"
                screenshot_labels = _field_json_array(form, "screenshotLabels")
                pending_job_id = uuid.uuid4().hex[:12]
                with JOBS_LOCK:
                    running = [job for job in JOBS.values() if job.get("status") in {"queued", "running"}]
                    if running:
                        self._send_json(
                            {"error": "Another audit is already running. Wait for it to finish before starting a new one."},
                            HTTPStatus.CONFLICT,
                        )
                        return
                screenshot_paths = _save_screenshot_uploads(form, pending_job_id, screenshot_labels)
                screenshot_labels = [
                    (screenshot_labels[index] if index < len(screenshot_labels) and screenshot_labels[index] else path.stem)
                    for index, path in enumerate(screenshot_paths)
                ]
                with JOBS_LOCK:
                    job = _new_screenshot_job(site_name, screenshot_paths, screenshot_labels)
                    job["id"] = pending_job_id
                    JOBS[job["id"]] = job
                worker = threading.Thread(target=_run_screenshot_audit_job, args=(job["id"],), daemon=True)
                worker.start()
                self._send_json(_snapshot_job(job), HTTPStatus.ACCEPTED)
                return

            data = self._read_json_body()
            audit_type = str(data.get("auditType") or "website")
            mode = str(data.get("mode") or "gtm").lower()
            with JOBS_LOCK:
                running = [job for job in JOBS.values() if job.get("status") in {"queued", "running"}]
                if running:
                    self._send_json(
                        {"error": "Another audit is already running. Wait for it to finish before starting a new one."},
                        HTTPStatus.CONFLICT,
                    )
                    return
                if audit_type == "website":
                    if mode not in {"detailed", "gtm"}:
                        raise ValueError("Audit mode must be either detailed or gtm.")
                    url = _validate_url(str(data.get("url") or ""))
                    job = _new_job(url, mode)
                    JOBS[job["id"]] = job
                    worker = threading.Thread(target=_run_audit_job, args=(job["id"],), daemon=True)
                elif audit_type == "mobile":
                    app_label = str(data.get("appLabel") or "Android App Audit").strip() or "Android App Audit"
                    app_package = _validate_required_text(str(data.get("appPackage") or ""), "Android app package")
                    app_activity = _validate_required_text(str(data.get("appActivity") or ""), "Android app activity")
                    appium_url = str(data.get("appiumUrl") or "http://127.0.0.1:4723").strip() or "http://127.0.0.1:4723"
                    device_name = str(data.get("deviceName") or "Android Emulator").strip() or "Android Emulator"
                    platform_version = str(data.get("platformVersion") or "").strip()
                    udid = str(data.get("udid") or "").strip()
                    job = _new_mobile_job(
                        app_label=app_label,
                        app_package=app_package,
                        app_activity=app_activity,
                        appium_url=appium_url,
                        device_name=device_name,
                        platform_version=platform_version,
                        udid=udid,
                    )
                    JOBS[job["id"]] = job
                    worker = threading.Thread(target=_run_mobile_audit_job, args=(job["id"],), daemon=True)
                else:
                    raise ValueError("Use multipart upload for screenshot audits. Supported JSON audit types are website and mobile.")
            worker.start()
            self._send_json(_snapshot_job(job), HTTPStatus.ACCEPTED)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local React audit launcher UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AuditRequestHandler)
    print(f"Audit launcher UI: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping audit launcher UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
