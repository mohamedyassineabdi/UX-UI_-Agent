from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


@dataclass
class AIReviewConfig:
    backend: str
    base_url: str
    api_key: str
    model: str
    timeout: int = 120
    max_retries: int = 5
    retry_base_delay: float = 2.0
    retry_max_delay: float = 20.0
    request_spacing_seconds: float = 0.6


def _strip_trailing_slash(url: str) -> str:
    return url[:-1] if url.endswith("/") else url


def _normalize_base_url(url: str) -> str:
    value = _strip_trailing_slash((url or "").strip())
    if value and not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value


def _load_project_dotenv() -> None:
    project_env = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(dotenv_path=project_env, override=False)


def _first_non_empty_env(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return default


def _detect_backend() -> str:
    explicit_backend = _first_non_empty_env("AI_REVIEW_BACKEND").lower()
    if explicit_backend:
        return explicit_backend

    if _first_non_empty_env("OLLAMA_HOST", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "OLLAMA_API_KEY"):
        return "ollama"
    if _first_non_empty_env("GROQ_API_KEY", "GROQ_MODEL", "GROQ_BASE_URL"):
        return "groq"
    if _first_non_empty_env("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        return "openai"

    return "ollama"


def load_ai_review_config() -> AIReviewConfig:
    _load_project_dotenv()

    backend = _detect_backend()

    timeout_raw = _first_non_empty_env("AI_REVIEW_TIMEOUT", default="120")
    max_retries_raw = _first_non_empty_env("AI_REVIEW_MAX_RETRIES", default="5")
    retry_base_delay_raw = _first_non_empty_env("AI_REVIEW_RETRY_BASE_DELAY", default="2.0")
    retry_max_delay_raw = _first_non_empty_env("AI_REVIEW_RETRY_MAX_DELAY", default="20.0")
    request_spacing_raw = _first_non_empty_env("AI_REVIEW_REQUEST_SPACING_SECONDS", default="0.6")

    if backend == "groq":
        base_url = _normalize_base_url(
            _first_non_empty_env("AI_REVIEW_BASE_URL", "GROQ_BASE_URL", default=DEFAULT_GROQ_BASE_URL)
        )
        api_key = _first_non_empty_env("AI_REVIEW_API_KEY", "GROQ_API_KEY")
        model = _first_non_empty_env("AI_REVIEW_MODEL", "GROQ_MODEL", default=DEFAULT_GROQ_MODEL)
        if not api_key:
            raise ValueError("Missing GROQ_API_KEY (or AI_REVIEW_API_KEY) in environment.")
    elif backend == "ollama":
        base_url = _normalize_base_url(
            _first_non_empty_env("AI_REVIEW_BASE_URL", "OLLAMA_BASE_URL", "OLLAMA_HOST", default=DEFAULT_OLLAMA_BASE_URL)
        )
        api_key = _first_non_empty_env("AI_REVIEW_API_KEY", "OLLAMA_API_KEY")
        model = _first_non_empty_env("AI_REVIEW_MODEL", "OLLAMA_MODEL", default=DEFAULT_OLLAMA_MODEL)
    elif backend == "openai":
        base_url = _normalize_base_url(
            _first_non_empty_env("AI_REVIEW_BASE_URL", "OPENAI_BASE_URL", default=DEFAULT_OPENAI_BASE_URL)
        )
        api_key = _first_non_empty_env("AI_REVIEW_API_KEY", "OPENAI_API_KEY")
        model = _first_non_empty_env("AI_REVIEW_MODEL", "OPENAI_MODEL")
        if not api_key and base_url == DEFAULT_OPENAI_BASE_URL:
            raise ValueError("Missing OPENAI_API_KEY (or AI_REVIEW_API_KEY) in environment.")
    else:
        raise ValueError(f"Unsupported AI_REVIEW_BACKEND: {backend}")

    if not base_url:
        raise ValueError("Missing AI review base URL in environment.")
    if not model:
        raise ValueError("Missing AI review model in environment.")

    try:
        timeout = int(timeout_raw)
    except ValueError:
        timeout = 120

    try:
        max_retries = int(max_retries_raw)
    except ValueError:
        max_retries = 5

    try:
        retry_base_delay = float(retry_base_delay_raw)
    except ValueError:
        retry_base_delay = 2.0

    try:
        retry_max_delay = float(retry_max_delay_raw)
    except ValueError:
        retry_max_delay = 20.0

    try:
        request_spacing_seconds = float(request_spacing_raw)
    except ValueError:
        request_spacing_seconds = 0.6

    return AIReviewConfig(
        backend=backend,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
        request_spacing_seconds=request_spacing_seconds,
    )


class AIReviewClient:
    def __init__(self, config: Optional[AIReviewConfig] = None) -> None:
        self.config = config or load_ai_review_config()
        self.session = requests.Session()
        self._last_request_ts = 0.0

    def review_json(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        if self.config.request_spacing_seconds > 0:
            self._respect_request_spacing()

        if self.config.backend == "ollama":
            text = self._call_ollama(system_prompt, user_payload, temperature)
        elif self.config.backend in {"openai", "groq"}:
            text = self._call_openai_compatible(system_prompt, user_payload, temperature)
        else:
            raise ValueError(f"Unsupported AI_REVIEW_BACKEND: {self.config.backend}")

        parsed = self._extract_json(text)
        if not isinstance(parsed, dict):
            raise ValueError("Model did not return a JSON object.")
        return parsed

    def _respect_request_spacing(self) -> None:
        now = time.time()
        elapsed = now - self._last_request_ts
        wait_for = self.config.request_spacing_seconds - elapsed
        if wait_for > 0:
            time.sleep(wait_for)

    def _mark_request_done(self) -> None:
        self._last_request_ts = time.time()

    def _call_openai_compatible(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        temperature: float,
    ) -> str:
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        body = {
            "model": self.config.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, indent=2),
                },
            ],
        }

        response = self._post_with_retry(url=url, headers=headers, body=body)
        data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ValueError(f"Unexpected OpenAI-compatible response format: {data}") from exc

        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Empty model response content: {data}")

        return content

    def _call_ollama(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        temperature: float,
    ) -> str:
        url = f"{self.config.base_url}/api/chat"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        body = {
            "model": self.config.model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temperature,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, indent=2),
                },
            ],
        }

        response = self._post_with_retry(url=url, headers=headers, body=body)
        data = response.json()

        try:
            content = data["message"]["content"]
        except Exception as exc:
            raise ValueError(f"Unexpected Ollama response format: {data}") from exc

        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Empty model response content: {data}")

        return content

    def _post_with_retry(
        self,
        url: str,
        headers: Dict[str, str],
        body: Dict[str, Any],
    ) -> requests.Response:
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.session.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=self.config.timeout,
                )
                self._mark_request_done()

                if response.status_code in {429, 500, 502, 503, 504}:
                    if attempt >= self.config.max_retries:
                        response.raise_for_status()
                    self._sleep_before_retry(attempt, response)
                    continue

                response.raise_for_status()
                return response

            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self._sleep_before_retry(attempt, None)

        raise ValueError(f"AI request failed after retries: {last_error}") from last_error

    def _sleep_before_retry(
        self,
        attempt: int,
        response: Optional[requests.Response],
    ) -> None:
        retry_after = None
        if response is not None:
            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header:
                try:
                    retry_after = float(retry_after_header)
                except ValueError:
                    retry_after = None

        if retry_after is None:
            retry_after = min(
                self.config.retry_base_delay * (2 ** attempt),
                self.config.retry_max_delay,
            )
            retry_after += random.uniform(0.0, 0.5)

        time.sleep(max(0.0, retry_after))

    @staticmethod
    def _extract_json(text: str) -> Any:
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        fenced = re.search(r"```json\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            return json.loads(fenced.group(1))

        first_obj = re.search(r"(\{.*\})", text, re.DOTALL)
        if first_obj:
            return json.loads(first_obj.group(1))

        first_array = re.search(r"(\[.*\])", text, re.DOTALL)
        if first_array:
            return json.loads(first_array.group(1))

        raise ValueError(f"Could not parse JSON from model output: {text[:500]}")
