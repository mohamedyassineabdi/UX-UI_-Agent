from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from .common import AXIS_DEFINITIONS, clean_text


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_VISION_MODEL = "llama3.2-vision"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env", override=False)


def _normalize_base_url(raw_url: Optional[str]) -> str:
    value = clean_text(raw_url) or DEFAULT_OLLAMA_BASE_URL
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _extract_json(text: str) -> Dict[str, Any]:
    content = clean_text(text)
    if not content:
        raise ValueError("Vision model returned empty content.")

    try:
        return json.loads(content)
    except Exception:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise


def _resolved_vision_settings(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, str]:
    resolved_model = (
        model_name
        or os.getenv("GTM_VISION_MODEL")
        or os.getenv("OLLAMA_VISION_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or DEFAULT_VISION_MODEL
    )
    resolved_base_url = _normalize_base_url(
        base_url
        or os.getenv("GTM_VISION_BASE_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_HOST")
    )
    resolved_api_key = api_key or os.getenv("GTM_VISION_API_KEY") or os.getenv("OLLAMA_API_KEY") or ""
    return {
        "model": resolved_model,
        "base_url": resolved_base_url,
        "api_key": resolved_api_key,
    }


def _chat_json_with_images(
    *,
    prompt: str,
    image_paths: List[Path],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
    timeout: int = 180,
) -> Dict[str, Any]:
    settings = _resolved_vision_settings(api_key=api_key, base_url=base_url, model_name=model_name)
    headers = {"Content-Type": "application/json"}
    if settings["api_key"]:
        headers["Authorization"] = f"Bearer {settings['api_key']}"

    payload = {
        "model": settings["model"],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
        "messages": [
            {
                "role": "system",
                "content": "You are a precise multimodal UX strategy reviewer. Output JSON only.",
            },
            {
                "role": "user",
                "content": prompt,
                "images": [_image_to_base64(path) for path in image_paths],
            },
        ],
    }

    response = requests.post(
        f"{settings['base_url']}/api/chat",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    content = clean_text(((data.get("message") or {}).get("content") or ""))
    return {
        "model": settings["model"],
        "parsed": _extract_json(content),
    }


def _build_prompt(site_context: Dict[str, Any], screenshots: List[Dict[str, Any]]) -> str:
    schema = {
        "site_summary": "string",
        "axes": {
            axis["id"]: {
                "observation": "string",
                "score": 0,
                "severity": "low | medium | high",
                "confidence": 0.0,
            }
            for axis in AXIS_DEFINITIONS
        },
        "priority_issues": [
            {
                "axis_id": "string",
                "title": "string",
                "severity": "low | medium | high",
                "confidence": 0.0,
                "page_name": "string",
                "page_url": "string",
                "screenshot_index": 0,
                "reason": "string",
                "evidence": "string",
                "recommendation": "string",
                "visual_region": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 0.0,
                    "height": 0.0,
                    "coordinate_system": "normalized_0_1",
                    "description": "string",
                },
                "outside_workbook": True,
            }
        ],
        "criteria_discoveries": [
            {
                "axis_id": "string",
                "criterion": "string",
                "severity": "low | medium | high",
                "confidence": 0.0,
                "page_name": "string",
                "page_url": "string",
                "screenshot_index": 0,
                "evidence": "string",
                "why_it_matters": "string",
                "recommendation": "string",
                "visual_region": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 0.0,
                    "height": 0.0,
                    "coordinate_system": "normalized_0_1",
                    "description": "string",
                },
            }
        ],
        "visual_trust_findings": [
            {
                "axis_id": "trust_accessibility | visual_brand | market_alignment",
                "title": "string",
                "severity": "low | medium | high",
                "confidence": 0.0,
                "visual_signal_type": "ai_generated_looking_portraits | broken_images | generic_stock_imagery | brand_inconsistency | rendering_artifact | other",
                "page_name": "string",
                "page_url": "string",
                "screenshot_index": 0,
                "evidence": "string",
                "why_it_matters": "string",
                "recommendation": "string",
                "visual_region": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 0.0,
                    "height": 0.0,
                    "coordinate_system": "normalized_0_1",
                    "description": "string",
                },
                "outside_workbook": True,
            }
        ],
        "strengths": ["string"],
        "market_positioning": "string",
    }

    return f"""
You are a senior UX/UI strategist preparing a persuasive go-to-market audit.

You will review a small set of representative website screenshots and return a compact,
decision-oriented synthesis for a B2B sales context.

Rules:
- Focus on the seven axes below.
- Score every axis from 0 to 100, where 100 is launch-ready and 0 is commercially risky. Use the score field inside `axes`.
- Calibrate severity from commercial GTM risk: high means likely to hurt comprehension, trust, or conversion; medium means meaningful friction; low means polish or secondary risk.
- Do not limit yourself to the spreadsheet/workbook criteria. If screenshots reveal a more important GTM UX/UI issue, add it to `criteria_discoveries`.
- Actively inspect for visual trust risks that workbook criteria may miss, especially:
  - AI-generated-looking or heavily AI-enhanced people/team photos.
  - Generic stock imagery that weakens authenticity.
  - Broken images, partial renders, clipping, overlapped UI, corrupted text, or obvious screenshot/rendering artifacts.
  - Visual credibility problems in founder/team/proof/client sections.
  - Brand imagery that feels inconsistent with the product promise or target buyer.
- Put those visual credibility issues in `visual_trust_findings`.
- Do not claim a person is AI-generated as a fact. Use careful wording such as "appears heavily AI-enhanced" or "may read as synthetic" and explain the visible signals.
- Use `priority_issues` for the most important issues overall, whether they come from workbook logic or your own visual/strategic review.
- Return 3 to 8 `priority_issues` total. Prefer fewer precise, evidence-backed findings over many generic ones.
- For each axis, make `observation` specific to visible evidence. If an axis cannot be confidently assessed from the screenshots, say what is not visible and lower confidence.
- Stay grounded in what is visible in the screenshots and the provided site context.
- Prefer concise, specific observations over generic design language.
- Cite exact visible text, CTA labels, section names, or UI elements when relevant.
- Include page_name/page_url or screenshot_index when you can identify where the issue appears.
- Use zero-based `screenshot_index` matching the Screenshot metadata array order.
- Every issue must explain: what is visible, why it matters commercially, and what to change next.
- For every issue in `priority_issues`, `criteria_discoveries`, and `visual_trust_findings`, include `visual_region` when the affected area is visible.
- `visual_region` must be an approximate bounding box around the concerned visible area in normalized 0-1 screenshot coordinates: x, y, width, height.
- If the issue affects the whole screen, use a broad visual_region covering the primary affected area rather than omitting it.
- If a visual issue is high impact but not part of the workbook, still return it with `outside_workbook: true`.
- If uncertain, lower confidence instead of overstating.
- Return STRICT JSON ONLY matching this schema:

{json.dumps(schema, ensure_ascii=False, indent=2)}

Seven axes:
{json.dumps([{k: v for k, v in axis.items() if k in {'id', 'name', 'description'}} for axis in AXIS_DEFINITIONS], ensure_ascii=False, indent=2)}

Site context:
{json.dumps(site_context, ensure_ascii=False, indent=2)}

Screenshot metadata:
{json.dumps(screenshots, ensure_ascii=False, indent=2)}
""".strip()


def _build_spotlight_prompt(issue: Dict[str, Any], candidates: List[Dict[str, Any]]) -> str:
    schema = {
        "best_candidate": 0,
        "confidence": 0.0,
        "reason": "string",
    }
    return f"""
You are reviewing candidate spotlight screenshots for a UX/UI audit issue.

Goal:
- Choose the candidate image whose red-circled region best supports the issue context.
- If none of the candidates is a good visual match, return -1.
- Prefer a precise UI element over a broad decorative area.
- Use what is visible in the images, not guesses.

Return STRICT JSON ONLY:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Meaning:
- `best_candidate`: zero-based candidate index, or -1 if no candidate is visually relevant.
- `confidence`: 0.0 to 1.0
- `reason`: short explanation

Issue context:
{json.dumps(issue, ensure_ascii=False, indent=2)}

Candidate metadata:
{json.dumps(candidates, ensure_ascii=False, indent=2)}
""".strip()


def run_gtm_vision_review(
    *,
    site_context: Dict[str, Any],
    screenshots: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    usable_screenshots = []
    image_paths: List[Path] = []

    for item in screenshots:
        raw_path = clean_text(item.get("screenshot_path"))
        if not raw_path:
            continue

        candidate = Path(raw_path)
        absolute = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
        if not absolute.exists():
            continue

        usable_screenshots.append(
            {
                "page_name": clean_text(item.get("page_name")),
                "page_url": clean_text(item.get("page_url")),
                "title": clean_text(item.get("title")),
                "reason": clean_text(item.get("reason")),
                "source_type": clean_text(item.get("source_type")),
                "file_name": clean_text(item.get("file_name")),
                "image_width": item.get("image_width"),
                "image_height": item.get("image_height"),
                "aspect_ratio": item.get("aspect_ratio"),
            }
        )
        image_paths.append(absolute)

    settings = _resolved_vision_settings(api_key=api_key, base_url=base_url, model_name=model_name)

    if not image_paths:
        return {
            "enabled": False,
            "model": settings["model"],
            "used_images": 0,
            "error": "No usable screenshots were found for the GTM vision review.",
            "result": None,
        }

    try:
        reviewed = _chat_json_with_images(
            prompt=_build_prompt(site_context, usable_screenshots),
            image_paths=image_paths,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            timeout=180,
        )
        return {
            "enabled": True,
            "model": reviewed["model"],
            "used_images": len(image_paths),
            "error": "",
            "result": reviewed["parsed"],
        }
    except Exception as error:
        return {
            "enabled": False,
            "model": settings["model"],
            "used_images": len(image_paths),
            "error": str(error),
            "result": None,
        }


def run_spotlight_candidate_review(
    *,
    issue: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    settings = _resolved_vision_settings(api_key=api_key, base_url=base_url, model_name=model_name)
    image_paths = []
    usable_candidates = []
    for candidate in candidates:
        image_path = candidate.get("image_path")
        if not image_path:
            continue
        path = Path(image_path)
        absolute = path if path.is_absolute() else PROJECT_ROOT / clean_text(str(image_path))
        if not absolute.exists():
            continue
        image_paths.append(absolute)
        usable_candidates.append(
            {
                "index": len(usable_candidates),
                "label": clean_text(candidate.get("label")),
                "component_type": clean_text(candidate.get("component_type")),
                "component_text": clean_text(candidate.get("component_text"))[:180],
                "reason": clean_text(candidate.get("reason")),
            }
        )

    if not image_paths:
        return {
            "enabled": False,
            "model": settings["model"],
            "error": "No usable candidate images were available for spotlight review.",
            "result": None,
        }

    try:
        reviewed = _chat_json_with_images(
            prompt=_build_spotlight_prompt(issue, usable_candidates),
            image_paths=image_paths,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            timeout=120,
        )
        return {
            "enabled": True,
            "model": reviewed["model"],
            "error": "",
            "result": reviewed["parsed"],
        }
    except Exception as error:
        return {
            "enabled": False,
            "model": settings["model"],
            "error": str(error),
            "result": None,
        }
