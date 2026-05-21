from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections.abc import Callable
import threading
import time
from typing import Any

import requests

from figma_audit.config import (
    FIGMA_API_BASE,
    FIGMA_CA_BUNDLE,
    FIGMA_IMAGE_MAX_RETRIES,
    FIGMA_IMAGE_REQUEST_TIMEOUT,
    FIGMA_MIN_REQUEST_INTERVAL_SECONDS,
    FIGMA_MAX_RETRY_AFTER_SECONDS,
    FIGMA_MAX_RETRY_SLEEP_SECONDS,
    FIGMA_RATE_LIMIT_RETRIES,
    FIGMA_TRUST_ENV_PROXY,
    FIGMA_VERIFY_SSL,
    FIGMA_TOKEN,
    FIGMA_TOKENS,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
)


class FigmaApiError(Exception):
    """Raised when a Figma API request fails."""


class FigmaRateLimitError(FigmaApiError):
    """Raised when Figma keeps returning 429 after the configured retries."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        plan_tier: str | None = None,
        rate_limit_type: str | None = None,
        upgrade_link: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.plan_tier = plan_tier
        self.rate_limit_type = rate_limit_type
        self.upgrade_link = upgrade_link


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None

    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _bounded_retry_sleep(seconds: float) -> float:
    return min(max(0.0, seconds), FIGMA_MAX_RETRY_SLEEP_SECONDS)


def _cache_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(
            (str(key), _cache_value(item))
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_cache_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_cache_value(item) for item in value), key=repr))
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _request_cache_key(
    method: str,
    path: str,
    params: dict[str, Any] | None,
) -> tuple[str, str, tuple[tuple[str, Any], ...]] | None:
    if method.upper() != "GET":
        return None

    frozen_params: tuple[tuple[str, Any], ...] = ()
    if params:
        frozen_params = tuple(
            (str(key), _cache_value(value))
            for key, value in sorted(params.items(), key=lambda entry: str(entry[0]))
        )
    return method.upper(), path, frozen_params


def _normalize_tokens(tokens: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if tokens is None:
        return []

    raw_tokens: list[str]
    if isinstance(tokens, str):
        raw_tokens = [
            item.strip()
            for item in tokens.replace(";", ",").replace("\n", ",").split(",")
            if item.strip()
        ]
    else:
        raw_tokens = [str(item).strip() for item in tokens if str(item).strip()]

    seen: set[str] = set()
    result: list[str] = []
    for item in raw_tokens:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _verify_setting() -> bool | str:
    return FIGMA_CA_BUNDLE or FIGMA_VERIFY_SSL


class FigmaClient:
    """
    Small, focused client for the Figma REST API.

    Responsibilities:
    - authenticate requests
    - send HTTP requests
    - retry on transient failures
    - handle rate limits
    - expose clean helper methods for the endpoints we need
    """

    _token_pool_lock = threading.Lock()
    _global_token_cursor = 0
    _global_token_cooldowns: dict[str, float] = {}

    def __init__(
        self,
        token: str | None = None,
        tokens: str | list[str] | tuple[str, ...] | None = None,
        base_url: str | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        configured_tokens = _normalize_tokens(tokens)
        if token is not None:
            configured_tokens = _normalize_tokens([token, *configured_tokens])
        if not configured_tokens:
            configured_tokens = _normalize_tokens(FIGMA_TOKENS or FIGMA_TOKEN)

        self.tokens = configured_tokens
        self.token = self.tokens[0] if self.tokens else ""
        self.base_url = (base_url or FIGMA_API_BASE).rstrip("/")
        self.log = log
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        with self._token_pool_lock:
            self._next_token_position = self._global_token_cursor % max(len(self.tokens), 1)
            FigmaClient._global_token_cursor += 1
        self._cache_lock = threading.Lock()
        self._response_cache: dict[
            tuple[str, str, tuple[tuple[str, Any], ...]],
            dict[str, Any] | None,
        ] = {}
        self._image_url_cache: dict[tuple[str, str, float, str], str] = {}
        self._binary_cache_lock = threading.Lock()
        self._binary_cache: dict[str, bytes] = {}

        if not self.tokens:
            raise ValueError(
                "FIGMA_TOKEN or FIGMA_TOKENS is missing. Set it in your environment or .env file."
            )

        self.session = requests.Session()
        self.session.trust_env = FIGMA_TRUST_ENV_PROXY
        self.session.headers.update(
            {
                "X-Figma-Token": self.token,
                "Accept": "application/json",
            }
        )
        self.binary_session = requests.Session()
        self.binary_session.trust_env = FIGMA_TRUST_ENV_PROXY

    def _token_label(self, token: str) -> str:
        try:
            index = self.tokens.index(token) + 1
        except ValueError:
            index = 1
        return f"token {index}/{len(self.tokens)}"

    def _request_headers(self, token: str) -> dict[str, str]:
        return {
            "X-Figma-Token": token,
            "Accept": "application/json",
        }

    def _select_token(self) -> tuple[str | None, float | None]:
        now = time.monotonic()
        soonest_wait: float | None = None

        with self._token_pool_lock:
            for _ in range(len(self.tokens)):
                index = self._next_token_position % len(self.tokens)
                token = self.tokens[index]
                self._next_token_position = (index + 1) % len(self.tokens)
                FigmaClient._global_token_cursor = self._next_token_position

                cooldown_until = self._global_token_cooldowns.get(token, 0.0)
                if cooldown_until <= now:
                    self._global_token_cooldowns.pop(token, None)
                    return token, None

                wait_seconds = cooldown_until - now
                soonest_wait = (
                    wait_seconds
                    if soonest_wait is None
                    else min(soonest_wait, wait_seconds)
                )

        return None, soonest_wait

    def _has_available_token(self) -> bool:
        now = time.monotonic()
        with self._token_pool_lock:
            for token in self.tokens:
                if self._global_token_cooldowns.get(token, 0.0) <= now:
                    return True
        return False

    def _mark_token_rate_limited(self, token: str, retry_after: float | None) -> None:
        cooldown_seconds = retry_after if retry_after is not None else RETRY_BACKOFF_SECONDS
        if cooldown_seconds <= 0:
            return

        cooldown_until = time.monotonic() + cooldown_seconds
        with self._token_pool_lock:
            current = self._global_token_cooldowns.get(token, 0.0)
            self._global_token_cooldowns[token] = max(current, cooldown_until)

    def _pace_request(self) -> None:
        if FIGMA_MIN_REQUEST_INTERVAL_SECONDS <= 0:
            return

        with self._request_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            wait_seconds = FIGMA_MIN_REQUEST_INTERVAL_SECONDS - elapsed
            if wait_seconds > 0:
                time.sleep(_bounded_retry_sleep(wait_seconds))
            self._last_request_at = time.monotonic()

    def _rate_limit_error(self, path: str, response: requests.Response) -> FigmaRateLimitError:
        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        plan_tier = response.headers.get("X-Figma-Plan-Tier")
        rate_limit_type = response.headers.get("X-Figma-Rate-Limit-Type")
        upgrade_link = response.headers.get("X-Figma-Upgrade-Link")
        details: list[str] = [f"Figma API rate limit for {path}: {response.text}"]
        if retry_after is not None:
            details.append(f"Retry-After={retry_after:.0f}s")
        if plan_tier:
            details.append(f"plan={plan_tier}")
        if rate_limit_type:
            details.append(f"limit_type={rate_limit_type}")
        if upgrade_link:
            details.append(f"upgrade={upgrade_link}")
        return FigmaRateLimitError(
            "; ".join(details),
            retry_after_seconds=retry_after,
            plan_tier=plan_tier,
            rate_limit_type=rate_limit_type,
            upgrade_link=upgrade_link,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Internal request helper.

        Features:
        - retry on rate limits and transient request failures
        - optional 404 suppression for endpoints like variables
        - clear error messages

        Returns:
        - parsed JSON dict on success
        - None when allow_404=True and endpoint returns 404
        """
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        request_timeout = timeout if timeout is not None else REQUEST_TIMEOUT
        request_retries = max(
            max_retries if max_retries is not None else MAX_RETRIES,
            FIGMA_RATE_LIMIT_RETRIES,
            len(self.tokens),
        )
        cache_key = _request_cache_key(method, path, params)
        if cache_key is not None:
            with self._cache_lock:
                if cache_key in self._response_cache:
                    return self._response_cache[cache_key]

        for attempt in range(1, request_retries + 1):
            token, token_wait_seconds = self._select_token()
            if token is None:
                last_error = FigmaRateLimitError(
                    f"All configured Figma tokens are cooling down for {path}.",
                    retry_after_seconds=token_wait_seconds,
                )
                if (
                    token_wait_seconds is not None
                    and token_wait_seconds > FIGMA_MAX_RETRY_AFTER_SECONDS
                ):
                    _emit(
                        self.log,
                        f"All configured Figma tokens are cooling down for {path}; "
                        f"next retry is in about {token_wait_seconds:.0f}s.",
                    )
                    break

                sleep_seconds = _bounded_retry_sleep(
                    token_wait_seconds
                    if token_wait_seconds is not None
                    else RETRY_BACKOFF_SECONDS * attempt
                )
                _emit(
                    self.log,
                    f"All configured Figma tokens are cooling down for {path}; "
                    f"waiting {sleep_seconds:.1f}s before retry {attempt}/{request_retries}.",
                )
                time.sleep(sleep_seconds)
                continue

            try:
                self._pace_request()
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=self._request_headers(token),
                    verify=_verify_setting(),
                    timeout=request_timeout,
                )

                if response.status_code == 404 and allow_404:
                    if cache_key is not None:
                        with self._cache_lock:
                            self._response_cache[cache_key] = None
                    return None

                if response.status_code == 429:
                    last_error = self._rate_limit_error(path, response)
                    retry_after = (
                        last_error.retry_after_seconds
                        if isinstance(last_error, FigmaRateLimitError)
                        else None
                    )
                    should_cool_down_token = len(self.tokens) > 1 or (
                        retry_after is not None
                        and retry_after > FIGMA_MAX_RETRY_AFTER_SECONDS
                    )
                    if should_cool_down_token:
                        self._mark_token_rate_limited(token, retry_after)

                    if attempt == request_retries:
                        break

                    if len(self.tokens) > 1 and self._has_available_token():
                        _emit(
                            self.log,
                            f"Figma rate limit hit for {path} on {self._token_label(token)}; "
                            "trying another configured token.",
                        )
                        continue

                    if (
                        retry_after is not None
                        and retry_after > FIGMA_MAX_RETRY_AFTER_SECONDS
                    ):
                        _emit(
                            self.log,
                            f"Figma rate limit for {path} will not clear soon "
                            f"({retry_after:.0f}s). Stopping live retries and using cache if available.",
                        )
                        break

                    sleep_seconds = _bounded_retry_sleep(
                        retry_after
                        if retry_after is not None
                        else RETRY_BACKOFF_SECONDS * attempt
                    )
                    retry_note = (
                        f" Figma asked for {retry_after:.0f}s."
                        if retry_after is not None and retry_after > sleep_seconds
                        else ""
                    )
                    _emit(
                        self.log,
                        f"Figma rate limit hit for {path}; waiting {sleep_seconds:.1f}s "
                        f"before retry {attempt + 1}/{request_retries}.{retry_note}",
                    )
                    time.sleep(sleep_seconds)
                    continue

                if 500 <= response.status_code < 600:
                    raise FigmaApiError(
                        f"Figma server error {response.status_code} for {path}: "
                        f"{response.text}"
                    )

                if response.status_code >= 400:
                    raise FigmaApiError(
                        f"Figma API error {response.status_code} for {path}: "
                        f"{response.text}"
                    )

                try:
                    result = response.json()
                except ValueError as exc:
                    raise FigmaApiError(
                        f"Invalid JSON response from Figma for {path}."
                    ) from exc

                if cache_key is not None:
                    with self._cache_lock:
                        self._response_cache[cache_key] = result
                return result

            except (requests.RequestException, FigmaApiError) as exc:
                last_error = exc

                if attempt == request_retries:
                    break

                sleep_seconds = _bounded_retry_sleep(RETRY_BACKOFF_SECONDS * attempt)
                time.sleep(sleep_seconds)

        if isinstance(last_error, FigmaRateLimitError):
            raise last_error
        raise FigmaApiError(f"Request failed for {path}: {last_error}")

    def get_file(self, file_key: str) -> dict[str, Any]:
        """
        Fetch the full file JSON.

        GET /v1/files/{file_key}
        """
        result = self._request("GET", f"/v1/files/{file_key}")
        return result or {}

    def get_file_nodes(
        self,
        file_key: str,
        node_ids: list[str],
        depth: int | None = None,
    ) -> dict[str, Any]:
        """
        Fetch specific nodes from a file.

        GET /v1/files/{file_key}/nodes?ids=...
        """
        if not node_ids:
            raise ValueError("node_ids cannot be empty.")

        params: dict[str, Any] = {
            "ids": ",".join(node_ids),
        }

        if depth is not None:
            params["depth"] = depth

        result = self._request("GET", f"/v1/files/{file_key}/nodes", params=params)
        return result or {}

    def get_local_variables(self, file_key: str) -> dict[str, Any] | None:
        """
        Fetch local variables for the file if available.

        This endpoint may return 404 depending on:
        - token scope
        - workspace plan
        - file access

        In that case we return None instead of failing the whole pipeline.
        """
        return self._request(
            "GET",
            f"/v1/files/{file_key}/variables/local",
            allow_404=True,
        )

    def get_image_urls(
        self,
        file_key: str,
        node_ids: list[str],
        *,
        image_format: str = "png",
        scale: float = 2.0,
    ) -> dict[str, str]:
        """
        Ask Figma to render nodes and return temporary image URLs.

        The returned URLs are short-lived. Download them immediately if the
        artifact must be kept.
        """
        if not node_ids:
            raise ValueError("node_ids cannot be empty.")

        unique_node_ids = list(dict.fromkeys(node_ids))
        cached_images: dict[str, str] = {}
        missing_node_ids: list[str] = []
        scale_key = float(scale)

        with self._cache_lock:
            for node_id in unique_node_ids:
                cached_url = self._image_url_cache.get(
                    (file_key, image_format, scale_key, node_id)
                )
                if cached_url:
                    cached_images[node_id] = cached_url
                else:
                    missing_node_ids.append(node_id)

        if not missing_node_ids:
            return {
                node_id: cached_images[node_id]
                for node_id in unique_node_ids
                if node_id in cached_images
            }

        params: dict[str, Any] = {
            "ids": ",".join(missing_node_ids),
            "format": image_format,
            "scale": scale,
        }
        result = self._request(
            "GET",
            f"/v1/images/{file_key}",
            params=params,
            timeout=FIGMA_IMAGE_REQUEST_TIMEOUT,
            max_retries=FIGMA_IMAGE_MAX_RETRIES,
        ) or {}
        images = result.get("images", {})

        if not isinstance(images, dict):
            images = {}

        fetched_images = {
            node_id: image_url
            for node_id, image_url in images.items()
            if isinstance(image_url, str) and image_url
        }
        with self._cache_lock:
            for node_id, image_url in fetched_images.items():
                self._image_url_cache[(file_key, image_format, scale_key, node_id)] = image_url

        combined_images = {**cached_images, **fetched_images}
        return {
            node_id: combined_images[node_id]
            for node_id in unique_node_ids
            if node_id in combined_images
        }

    def download_binary(self, url: str) -> bytes:
        """Download a rendered image URL returned by the Figma Images API."""
        with self._binary_cache_lock:
            cached = self._binary_cache.get(url)
            if cached is not None:
                return cached

        last_error: Exception | None = None

        for attempt in range(1, FIGMA_IMAGE_MAX_RETRIES + 1):
            try:
                with requests.Session() as binary_session:
                    binary_session.trust_env = FIGMA_TRUST_ENV_PROXY
                    response = binary_session.get(
                        url,
                        verify=_verify_setting(),
                        timeout=FIGMA_IMAGE_REQUEST_TIMEOUT,
                    )
                response.raise_for_status()
                content = response.content
                with self._binary_cache_lock:
                    self._binary_cache[url] = content
                return content
            except requests.RequestException as exc:
                last_error = exc
                if attempt == FIGMA_IMAGE_MAX_RETRIES:
                    break
                time.sleep(_bounded_retry_sleep(RETRY_BACKOFF_SECONDS * attempt))

        raise FigmaApiError(f"Image download failed: {last_error}")

    def get_comments(self, file_key: str) -> dict[str, Any] | None:
        """
        Optional helper for future extensions.
        """
        return self._request(
            "GET",
            f"/v1/files/{file_key}/comments",
            allow_404=True,
        )
