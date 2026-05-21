from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from figma_audit.config import (
    OLLAMA_AI_REVIEW_MODEL,
    OLLAMA_API_HOST,
    OLLAMA_API_KEY,
    OLLAMA_REQUEST_TIMEOUT,
)
from figma_audit.models.criteria import UxUiCriterion
from figma_audit.report_polisher import ReportPolishError, _extract_json_object, _safe_text
from figma_audit.utils.io import load_json, save_json


AI_REVIEW_AXES = {
    "flow_architecture",
    "ui_consistency",
    "content_microcopy",
}
AI_REVIEW_DECISIONS = {"support", "soften", "reject"}


def _subdetector(evidence: dict[str, object]) -> object:
    for key in (
        "flow_subdetector",
        "ui_consistency_subdetector",
        "visual_brand_subdetector",
        "content_microcopy_subdetector",
    ):
        value = evidence.get(key)
        if value:
            return value
    return None


def issue_needs_ai_review(issue: dict[str, object]) -> bool:
    criterion = str(issue.get("criterion") or issue.get("axis") or "")
    if criterion not in AI_REVIEW_AXES:
        return False
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    if str(evidence.get("client_visibility") or ""):
        return False
    return True


def _compact_issue(
    issue: dict[str, object],
    criterion: UxUiCriterion | None,
) -> dict[str, object]:
    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    visual_evidence = issue.get("visual_evidence") if isinstance(issue.get("visual_evidence"), list) else []
    return {
        "id": issue.get("id"),
        "axis": issue.get("criterion") or issue.get("axis"),
        "axis_name": criterion.short_name if criterion else None,
        "axis_question": criterion.core_question if criterion else None,
        "severity": issue.get("severity"),
        "message": issue.get("message"),
        "screen_or_frame": location.get("frame_name") or location.get("node_name"),
        "node_name": location.get("node_name"),
        "detector": evidence.get("detector_id"),
        "subdetector": _subdetector(evidence),
        "confidence": evidence.get("confidence"),
        "client_visibility": evidence.get("client_visibility"),
        "text_sample": evidence.get("text_sample") or evidence.get("matched_text"),
        "plain_language_checks": evidence.get("plain_language_checks"),
        "value_communication_checks": evidence.get("value_communication_checks"),
        "visual_search_checks": evidence.get("visual_search_checks"),
        "ui_consistency_checks": evidence.get("ui_consistency_checks"),
        "visual_brand_checks": evidence.get("visual_brand_checks"),
        "has_real_client_evidence": any(
            isinstance(artifact, dict)
            and artifact.get("type") == "real_page_geometry_screenshot"
            and artifact.get("image_path")
            for artifact in visual_evidence
        ),
    }


def _normalize_reviews(
    *,
    issues: list[dict[str, object]],
    parsed: dict[str, Any],
) -> dict[str, dict[str, object]]:
    raw_items = parsed.get("reviews")
    if not isinstance(raw_items, list):
        raise ValueError("AI review response must contain a 'reviews' list.")

    issue_ids = {str(issue.get("id")) for issue in issues}
    reviews: dict[str, dict[str, object]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("id") or "")
        if issue_id not in issue_ids:
            continue
        decision = str(item.get("decision") or "").strip().lower()
        if decision not in AI_REVIEW_DECISIONS:
            continue
        review = {
            "decision": decision,
            "reason": _safe_text(item.get("reason"), 420),
            "client_reframe": _safe_text(item.get("client_reframe"), 700),
            "recommended_focus": _safe_text(item.get("recommended_focus"), 420),
        }
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)):
            review["confidence"] = max(0.0, min(1.0, round(float(confidence), 2)))
        reviews[issue_id] = review
    return reviews


def review_issues_with_ollama(
    *,
    issues: list[dict[str, object]],
    criteria_by_id: dict[str, UxUiCriterion],
    cache_path: Path | None = None,
    force: bool = False,
    log: object = print,
) -> dict[str, dict[str, object]]:
    review_candidates = [issue for issue in issues if issue_needs_ai_review(issue)]
    if not review_candidates:
        return {}

    if cache_path and cache_path.exists() and not force:
        cached = load_json(cache_path)
        if isinstance(cached, dict) and cached.get("model") == OLLAMA_AI_REVIEW_MODEL:
            reviews = cached.get("reviews")
            if isinstance(reviews, dict):
                return {
                    str(issue_id): review
                    for issue_id, review in reviews.items()
                    if isinstance(review, dict)
                    and str(review.get("decision") or "") in AI_REVIEW_DECISIONS
                }

    if not OLLAMA_API_KEY:
        raise ReportPolishError("OLLAMA_API_KEY is not set, so AI criterion review was skipped.")

    compact_issues = []
    for issue in review_candidates:
        criterion_id = str(issue.get("criterion") or issue.get("axis") or "")
        compact_issues.append(_compact_issue(issue, criteria_by_id.get(criterion_id)))

    system_prompt = (
        "You are a senior UX audit reviewer. Review only evidence-supported, visible UI findings. "
        "You must not invent hidden pages, runtime behavior, or business facts. "
        "Your job is to decide whether broad/judgment-heavy findings are client-logical. "
        "For content and microcopy, judge whether the visible words are meaningful, concrete, and understandable in context."
    )
    user_prompt = {
        "task": "Review each issue and decide if it should stay in the client report.",
        "axes_that_need_ai_review": sorted(AI_REVIEW_AXES),
        "decision_rules": {
            "support": "The issue is logical from the visible evidence and should stay as-is.",
            "soften": "The issue is plausible but should be worded as a lower-confidence improvement, not a hard failure.",
            "reject": "The issue is not supported enough by visible evidence or does not belong to this axis.",
        },
        "hard_rules": [
            "Do not approve a finding just because a detector says so.",
            "Reject or soften when the issue is broad and the evidence does not explain what a client can see.",
            "Never create new issues.",
            "Never override screenshot visibility gates.",
            "Use simple client language.",
        ],
        "output_contract": {
            "reviews": [
                {
                    "id": "same id as input",
                    "decision": "support | soften | reject",
                    "confidence": "number from 0 to 1",
                    "reason": "short evidence-based reason",
                    "client_reframe": "client-friendly explanation if support or soften",
                    "recommended_focus": "what the designer should inspect or improve",
                }
            ]
        },
        "issues": compact_issues,
    }

    if callable(log):
        log(f"Reviewing judgment-heavy criteria with Ollama model {OLLAMA_AI_REVIEW_MODEL}...")

    try:
        response = requests.post(
            f"{OLLAMA_API_HOST}/api/chat",
            headers={
                "Authorization": f"Bearer {OLLAMA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OLLAMA_AI_REVIEW_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ReportPolishError(f"Ollama AI review request failed: {exc}") from exc

    if response.status_code >= 400:
        raise ReportPolishError(
            f"Ollama AI review request failed with HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ReportPolishError("Ollama AI review response was not valid JSON.") from exc

    message = payload.get("message") if isinstance(payload, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ReportPolishError("Ollama AI review response did not contain message content.")

    try:
        parsed = _extract_json_object(content)
        reviews = _normalize_reviews(issues=review_candidates, parsed=parsed)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ReportPolishError(f"Ollama AI review response had an unusable format: {exc}") from exc

    if cache_path:
        save_json(
            cache_path,
            {
                "model": OLLAMA_AI_REVIEW_MODEL,
                "reviews": reviews,
            },
        )
    return reviews
