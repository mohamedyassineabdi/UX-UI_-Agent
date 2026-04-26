from __future__ import annotations

from typing import Any, Dict

from src.audit.ai_review_client import AIReviewClient


_AI_CLIENT: AIReviewClient | None = None


def _truncate_text(value: str, limit: int = 8000) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _build_system_prompt() -> str:
    return """
You are a senior UX/UI audit adjudicator.
You are not the primary calculator.
You review deterministic findings for one page and one criterion.

Rules:
- Never invent facts.
- Be conservative.
- If metrics look suspicious, say so.
- If evidence is weak, reduce confidence.
- Suspicious metric patterns should usually become warning, not fail.
- Return only valid JSON.

Return exactly:
{
  "criterion": "<string>",
  "final_verdict": "pass|warning|fail|not_applicable",
  "confidence": "low|medium|high",
  "agree_with_deterministic": true,
  "reason": "<short explanation>",
  "key_signals": ["..."],
  "recommended_adjustment": "<short suggestion or empty string>",
  "suspicious_metrics": ["..."],
  "evidence_quality": "low|medium|high",
  "needs_manual_review": true
}
""".strip()


def _build_user_payload(
    *,
    criterion: str,
    page_name: str,
    page_url: str,
    page_type: str,
    deterministic_result: Dict[str, Any],
    page_metrics: Dict[str, Any],
    extracted_summary: Dict[str, Any],
) -> Dict[str, Any]:
    rubric = {
        "visual_hierarchy": [
            "Use size, spacing, position, contrast, grouping, and emphasis together.",
            "Do not treat contrast alone as hierarchy.",
        ],
        "contrast": [
            "Contrast alone does not determine hierarchy.",
            "Uniform contrast across all element families is suspicious.",
        ],
        "typography": [
            "Judge typography by separation between headings, body, controls, and labels.",
            "Tiny numeric gaps may not be visually meaningful.",
        ],
    }

    return {
        "page": {
            "name": page_name,
            "url": page_url,
            "page_type": page_type,
        },
        "criterion": criterion,
        "rubric": rubric,
        "deterministic_result": deterministic_result,
        "page_metrics": page_metrics,
        "extracted_summary": extracted_summary,
        "notes": _truncate_text(str(extracted_summary), 12000),
    }


def _client() -> AIReviewClient:
    global _AI_CLIENT
    if _AI_CLIENT is None:
        _AI_CLIENT = AIReviewClient()
    return _AI_CLIENT


def review_page_criterion_with_ai(
    *,
    criterion: str,
    page_name: str,
    page_url: str,
    page_type: str,
    deterministic_result: Dict[str, Any],
    page_metrics: Dict[str, Any],
    extracted_summary: Dict[str, Any],
    api_key: str | None = None,
) -> Dict[str, Any]:
    del api_key

    fallback_status = deterministic_result.get("status", "warning")

    try:
        response = _client().review_json(
            system_prompt=_build_system_prompt(),
            user_payload=_build_user_payload(
                criterion=criterion,
                page_name=page_name,
                page_url=page_url,
                page_type=page_type,
                deterministic_result=deterministic_result,
                page_metrics=page_metrics,
                extracted_summary=extracted_summary,
            ),
            temperature=0.1,
        )
    except Exception as exc:
        return {
            "criterion": criterion,
            "final_verdict": fallback_status,
            "confidence": "low",
            "agree_with_deterministic": True,
            "reason": f"AI review failed: {exc}",
            "key_signals": [],
            "recommended_adjustment": "",
            "suspicious_metrics": ["request_failed"],
            "evidence_quality": "low",
            "needs_manual_review": True,
            "ai_error": "request_failed",
        }

    if not isinstance(response, dict):
        return {
            "criterion": criterion,
            "final_verdict": fallback_status,
            "confidence": "low",
            "agree_with_deterministic": True,
            "reason": "AI response was not a JSON object.",
            "key_signals": [],
            "recommended_adjustment": "",
            "suspicious_metrics": ["invalid_json_response"],
            "evidence_quality": "low",
            "needs_manual_review": True,
            "ai_error": "invalid_json",
        }

    response.setdefault("criterion", criterion)
    response.setdefault("final_verdict", fallback_status)
    response.setdefault("confidence", "low")
    response.setdefault("agree_with_deterministic", True)
    response.setdefault("reason", "")
    response.setdefault("key_signals", [])
    response.setdefault("recommended_adjustment", "")
    response.setdefault("suspicious_metrics", [])
    response.setdefault("evidence_quality", "low")
    response.setdefault("needs_manual_review", True)
    return response
