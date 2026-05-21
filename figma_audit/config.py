from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} must be a boolean.")


def _env_list(name: str) -> list[str]:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return []

    normalized = raw_value.replace(";", ",").replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
RESOURCES_DIR = PACKAGE_DIR / "resources"

DATA_DIR = PROJECT_ROOT / "data"
RAW_OUTPUT_DIR = DATA_DIR / "raw"
NORMALIZED_OUTPUT_DIR = DATA_DIR / "normalized"
EXTRACTED_OUTPUT_DIR = DATA_DIR / "extracted"
AUDIT_PARTS_OUTPUT_DIR = DATA_DIR / "parts"
CRITERIA_DIR = DATA_DIR / "criteria"
ANNOTATIONS_OUTPUT_DIR = DATA_DIR / "annotations"
DETECTIONS_OUTPUT_DIR = DATA_DIR / "detections"
REPORTS_OUTPUT_DIR = DATA_DIR / "reports"
RUN_STATUS_OUTPUT_PATH = DATA_DIR / "run_status.json"
FINAL_RESULT_OUTPUT_PATH = DATA_DIR / "final_result.json"
DEFAULT_CRITERIA_FILENAME = "ux_ui_criteria_validated.json"
PACKAGED_CRITERIA_PATH = RESOURCES_DIR / DEFAULT_CRITERIA_FILENAME
DEFAULT_CRITERIA_PATH = Path(
    os.getenv("FIGMA_AUDIT_CRITERIA_PATH", str(PACKAGED_CRITERIA_PATH))
)

FIGMA_API_BASE = os.getenv("FIGMA_API_BASE", "https://api.figma.com").rstrip("/")
FIGMA_TOKEN = os.getenv("FIGMA_TOKEN", "").strip()
FIGMA_TOKENS = _dedupe([token for token in [FIGMA_TOKEN, *_env_list("FIGMA_TOKENS")] if token])

REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 30)
MAX_RETRIES = _env_int("MAX_RETRIES", 6)
RETRY_BACKOFF_SECONDS = _env_float("RETRY_BACKOFF_SECONDS", 1.5)
FIGMA_IMAGE_REQUEST_TIMEOUT = _env_int("FIGMA_IMAGE_REQUEST_TIMEOUT", 15)
FIGMA_IMAGE_MAX_RETRIES = _env_int("FIGMA_IMAGE_MAX_RETRIES", 1)
FIGMA_MAX_RETRY_SLEEP_SECONDS = _env_float("FIGMA_MAX_RETRY_SLEEP_SECONDS", 90.0)
FIGMA_MAX_RETRY_AFTER_SECONDS = _env_float("FIGMA_MAX_RETRY_AFTER_SECONDS", 3600.0)
FIGMA_RATE_LIMIT_RETRIES = _env_int("FIGMA_RATE_LIMIT_RETRIES", 8)
FIGMA_MIN_REQUEST_INTERVAL_SECONDS = _env_float("FIGMA_MIN_REQUEST_INTERVAL_SECONDS", 1.0)
FIGMA_TRUST_ENV_PROXY = _env_bool("FIGMA_TRUST_ENV_PROXY", False)
FIGMA_VERIFY_SSL = _env_bool("FIGMA_VERIFY_SSL", True)
FIGMA_CA_BUNDLE = os.getenv("FIGMA_CA_BUNDLE", "").strip()
FIGMA_FETCH_VARIABLES = _env_bool("FIGMA_FETCH_VARIABLES", False)
ANNOTATION_IMAGE_SCALE = _env_float("ANNOTATION_IMAGE_SCALE", 2.0)
ANNOTATION_MAX_IMAGES = _env_int("ANNOTATION_MAX_IMAGES", 100)
ANNOTATION_WORKERS = _env_int("ANNOTATION_WORKERS", 4)
DETECTION_MAX_ISSUES_PER_CHECK = _env_int("DETECTION_MAX_ISSUES_PER_CHECK", 8)
DETECTION_WORKERS = _env_int("DETECTION_WORKERS", 4)
MAX_RUNTIME_SECONDS = _env_int("MAX_RUNTIME_SECONDS", 1800)

OLLAMA_API_HOST = os.getenv("OLLAMA_API_HOST", "https://ollama.com").rstrip("/")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
OLLAMA_REPORT_MODEL = os.getenv("OLLAMA_REPORT_MODEL", "gpt-oss:120b").strip()
OLLAMA_REPORT_POLISH = _env_bool("OLLAMA_REPORT_POLISH", True)
OLLAMA_AI_REVIEW = _env_bool("OLLAMA_AI_REVIEW", True)
OLLAMA_AI_REVIEW_MODEL = os.getenv("OLLAMA_AI_REVIEW_MODEL", OLLAMA_REPORT_MODEL).strip()
OLLAMA_REQUEST_TIMEOUT = _env_int("OLLAMA_REQUEST_TIMEOUT", 90)


def ensure_output_dirs() -> None:
    """Create the default output directories used by the local CLI."""
    RAW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_PARTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CRITERIA_DIR.mkdir(parents=True, exist_ok=True)
    ANNOTATIONS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DETECTIONS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def validate_config(*, require_figma_token: bool = True) -> None:
    """Validate the minimum required configuration before the app runs."""
    if require_figma_token and not FIGMA_TOKENS:
        raise ValueError(
            "FIGMA_TOKEN or FIGMA_TOKENS is missing. Set one in your environment or .env file."
        )

    if REQUEST_TIMEOUT <= 0:
        raise ValueError("REQUEST_TIMEOUT must be greater than 0.")

    if MAX_RETRIES <= 0:
        raise ValueError("MAX_RETRIES must be greater than 0.")

    if RETRY_BACKOFF_SECONDS < 0:
        raise ValueError("RETRY_BACKOFF_SECONDS cannot be negative.")

    if FIGMA_IMAGE_REQUEST_TIMEOUT <= 0:
        raise ValueError("FIGMA_IMAGE_REQUEST_TIMEOUT must be greater than 0.")

    if FIGMA_IMAGE_MAX_RETRIES <= 0:
        raise ValueError("FIGMA_IMAGE_MAX_RETRIES must be greater than 0.")

    if FIGMA_MAX_RETRY_SLEEP_SECONDS < 0:
        raise ValueError("FIGMA_MAX_RETRY_SLEEP_SECONDS cannot be negative.")

    if FIGMA_MAX_RETRY_AFTER_SECONDS < 0:
        raise ValueError("FIGMA_MAX_RETRY_AFTER_SECONDS cannot be negative.")

    if FIGMA_RATE_LIMIT_RETRIES <= 0:
        raise ValueError("FIGMA_RATE_LIMIT_RETRIES must be greater than 0.")

    if FIGMA_MIN_REQUEST_INTERVAL_SECONDS < 0:
        raise ValueError("FIGMA_MIN_REQUEST_INTERVAL_SECONDS cannot be negative.")

    if ANNOTATION_IMAGE_SCALE <= 0:
        raise ValueError("ANNOTATION_IMAGE_SCALE must be greater than 0.")

    if ANNOTATION_MAX_IMAGES <= 0:
        raise ValueError("ANNOTATION_MAX_IMAGES must be greater than 0.")

    if ANNOTATION_WORKERS <= 0:
        raise ValueError("ANNOTATION_WORKERS must be greater than 0.")

    if DETECTION_MAX_ISSUES_PER_CHECK <= 0:
        raise ValueError("DETECTION_MAX_ISSUES_PER_CHECK must be greater than 0.")

    if DETECTION_WORKERS <= 0:
        raise ValueError("DETECTION_WORKERS must be greater than 0.")

    if MAX_RUNTIME_SECONDS <= 0:
        raise ValueError("MAX_RUNTIME_SECONDS must be greater than 0.")
