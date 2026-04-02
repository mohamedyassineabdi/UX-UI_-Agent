from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

DEFAULT_OLLAMA_MODEL = "llama3.2-vision"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


def _image_bytes_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _compact_candidates(candidates: List[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in candidates[:limit]:
        out.append(
            {
                "candidate_id": item.get("candidate_id"),
                "tag": item.get("tag"),
                "text": _safe_text(item.get("text")),
                "aria_label": _safe_text(item.get("aria_label")),
                "title": _safe_text(item.get("title")),
                "href": item.get("href"),
                "role": item.get("role"),
                "top": item.get("top"),
                "left": item.get("left"),
                "width": item.get("width"),
                "height": item.get("height"),
                "icon_like": bool(item.get("icon_like")),
                "near_top": bool(item.get("near_top")),
            }
        )
    return out


def build_navigation_prompt(
    homepage: str,
    mobile: bool,
    current_nav_items: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> str:
    schema = {
        "navigation_present": True,
        "navigation_style": "topbar | hamburger | sidebar | mega_menu | drawer | unknown",
        "interaction_style": "hover | click | mixed | unknown",
        "menu_trigger": {
            "exists": True,
            "candidate_id": "string or null",
            "reason": "string",
            "confidence": 0.0,
        },
        "top_categories": [
            {
                "name": "string",
                "confidence": 0.0,
            }
        ],
        "utility_items": {
            "signin": {
                "visible": True,
                "candidate_id": "string or null",
                "label": "string or null",
                "confidence": 0.0,
            },
            "signup": {
                "visible": True,
                "candidate_id": "string or null",
                "label": "string or null",
                "confidence": 0.0,
            },
            "search": {
                "visible": True,
                "candidate_id": "string or null",
                "label": "string or null",
                "confidence": 0.0,
            },
        },
        "notes": "string",
    }

    return f"""
You are a strict website navigation recovery assistant helping a crawler.

You are given:
1) a screenshot of the homepage
2) clickable candidate elements extracted from the DOM
3) the crawler's current weak navigation result

Goal:
- identify the MAIN site navigation
- identify whether a button/icon should be clicked first to reveal the menu
- identify likely top-level categories visible or implied by the menu area
- identify likely sign-in, sign-up, and search locations
- infer whether this navbar behaves more like hover, click, or mixed interaction

Important rules:
- Focus only on PRIMARY SITE NAVIGATION and utility actions.
- Ignore cookie banners, floating widgets, chat buttons, ads, carousels, product cards, body links, and footer links.
- If the main navigation is hidden, pick the single best candidate_id to click first.
- Do not invent URLs.
- Do not invent labels not visible or strongly implied.
- If uncertain, keep confidence low.
- Return STRICT JSON ONLY matching this schema:

{json.dumps(schema, indent=2, ensure_ascii=False)}

Context:
- homepage: {homepage}
- viewport_mode: {"mobile" if mobile else "desktop"}

Current crawler navigation result:
{json.dumps(current_nav_items, indent=2, ensure_ascii=False)}

Clickable candidates:
{json.dumps(_compact_candidates(candidates), indent=2, ensure_ascii=False)}
""".strip()


def _parse_json_response(content: str) -> Dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise ValueError("LLM response was empty")

    try:
        return json.loads(content)
    except Exception:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except Exception:
                pass
        raise


def _looks_like_image_unsupported_error(message: str) -> bool:
    m = (message or "").lower()
    return (
        "image" in m
        or "vision" in m
        or "multimodal" in m
        or "does not support" in m
        or "unsupported" in m
    )


def _normalize_ollama_base_url(raw_url: Optional[str]) -> str:
    value = (raw_url or DEFAULT_OLLAMA_BASE_URL).strip()
    if not value:
        return DEFAULT_OLLAMA_BASE_URL
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _build_ollama_payload(
    model_name: str,
    prompt: str,
    screenshot_bytes: bytes,
    include_image: bool,
) -> Dict[str, Any]:
    user_message: Dict[str, Any] = {
        "role": "user",
        "content": prompt,
    }
    if include_image:
        user_message["images"] = [_image_bytes_to_base64(screenshot_bytes)]

    return {
        "model": model_name,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
        "messages": [
            {
                "role": "system",
                "content": "You are a precise UI navigation analysis assistant. Output valid JSON only.",
            },
            user_message,
        ],
    }


def _post_ollama_chat(base_url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    with httpx.Client(timeout=90.0) as client:
        response = client.post(
            f"{base_url}/api/chat",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def call_llama_navigation_detector(
    screenshot_bytes: bytes,
    homepage: str,
    mobile: bool,
    current_nav_items: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    api_key = api_key or os.getenv("OLLAMA_API_KEY")
    base_url = _normalize_ollama_base_url(
        base_url or os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST")
    )
    model_name = model_name or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    prompt = build_navigation_prompt(
        homepage=homepage,
        mobile=mobile,
        current_nav_items=current_nav_items,
        candidates=candidates,
    )

    try:
        data = _post_ollama_chat(
            base_url,
            headers,
            _build_ollama_payload(model_name, prompt, screenshot_bytes, include_image=True),
        )
    except Exception as exc:
        if not _looks_like_image_unsupported_error(str(exc)):
            raise ValueError(f"Ollama request failed: {exc}") from exc
        try:
            data = _post_ollama_chat(
                base_url,
                headers,
                _build_ollama_payload(model_name, prompt, screenshot_bytes, include_image=False),
            )
        except Exception as retry_exc:
            raise ValueError(f"Ollama request failed: {retry_exc}") from retry_exc

    content = ((data.get("message") or {}).get("content") or "").strip()
    try:
        return _parse_json_response(content)
    except Exception as exc:
        raise ValueError(f"Ollama response was not valid JSON: {exc}\nRaw content:\n{content}") from exc


def call_groq_navigation_detector(*args, **kwargs) -> Dict[str, Any]:
    return call_llama_navigation_detector(*args, **kwargs)
