from __future__ import annotations

from typing import Any, Dict


AI_ELIGIBLE_CRITERIA = {
    # existing
    "information-order-importance",
    "visual-hierarchy-reflects-priority",
    "required-action-direction",
    "cta-primary-visual-element",
    "visual-grouping-proximity-alignment",
    "negative-space-purpose",
    "similar-information-consistency",
    "colors-reinforce-hierarchy",

    # newly added weak / perception-heavy criteria
    "most-important-items-have-most-contrast",
    "contrast-primary-mechanism-for-hierarchy",
    "contrast-separates-content-from-controls",
    "contrast-separates-labels-from-content",
    "font-size-weight-differentiate-content-types",
    "fonts-reinforce-hierarchy",
    "fonts-separate-content-from-controls",
}

HIGH_VALUE_PAGE_TYPES = {"home", "task", "catalog", "conversion"}

SUSPICIOUS_METRIC_CRITERIA = {
    "most-important-items-have-most-contrast",
    "contrast-primary-mechanism-for-hierarchy",
    "contrast-separates-content-from-controls",
    "contrast-separates-labels-from-content",
    "colors-reinforce-hierarchy",
    "font-size-weight-differentiate-content-types",
    "fonts-reinforce-hierarchy",
    "fonts-separate-content-from-controls",
}


def has_suspicious_metrics(page_result: Dict[str, Any]) -> bool:
    criterion = page_result.get("criterion")
    metrics = page_result.get("metrics") or {}

    if criterion not in SUSPICIOUS_METRIC_CRITERIA:
        return False

    suspicious = False

    # Uniform contrast families: strong red flag
    contrast_keys = [
        "priorityMedianContrast",
        "bodyMedianContrast",
        "headingMedianContrast",
        "contentMedianContrast",
        "labelMedianContrast",
        "medianControlContrast",
        "ctaMedianContrast",
    ]
    contrast_values = [
        metrics.get(k) for k in contrast_keys
        if isinstance(metrics.get(k), (int, float))
    ]
    unique_contrast_values = {round(v, 2) for v in contrast_values}
    if len(contrast_values) >= 3 and len(unique_contrast_values) == 1:
        suspicious = True

    # Zero deltas everywhere
    for key in ("contrastGap", "medianDelta", "p75Delta"):
        value = metrics.get(key)
        if isinstance(value, (int, float)) and abs(value) < 0.01:
            suspicious = True

    # Very low sample counts
    sample_keys = [
        "prioritySampleCount",
        "bodySampleCount",
        "headingSampleCount",
        "contentSampleCount",
        "labelContrastSampleCount",
        "controlContrastSampleCount",
    ]
    sample_values = [
        metrics.get(k) for k in sample_keys
        if isinstance(metrics.get(k), (int, float))
    ]
    if sample_values and min(sample_values) < 3:
        suspicious = True

    return suspicious


def should_run_ai_review(page_result: Dict[str, Any]) -> bool:
    criterion = page_result.get("criterion")
    status = page_result.get("status")
    score = page_result.get("score")
    archetype = page_result.get("archetype")
    details = page_result.get("details") or []
    metrics = page_result.get("metrics") or {}

    if criterion not in AI_ELIGIBLE_CRITERIA:
        return False

    if status == "not_applicable":
        return False

    if has_suspicious_metrics(page_result):
        return True

    if archetype in HIGH_VALUE_PAGE_TYPES and status in {"warning", "fail"}:
        return True

    if isinstance(score, (int, float)) and 45 <= score <= 85:
        return True

    if details:
        return True

    if metrics.get("reason"):
        return True

    return False


def reconcile_deterministic_and_ai(
    deterministic_status: str,
    ai_verdict: str,
    deterministic_score: float | None,
    *,
    ai_confidence: str = "low",
    suspicious_metrics: bool = False,
) -> Dict[str, Any]:
    if ai_verdict == "not_applicable":
        return {
            "final_status": deterministic_status,
            "used_ai_override": False,
            "reconciliation_reason": "AI review not applicable.",
        }

    # If deterministic fail is based on suspicious metrics, allow downgrade to warning
    if deterministic_status == "fail":
        if suspicious_metrics and ai_verdict in {"warning", "pass"} and ai_confidence in {"medium", "high"}:
            return {
                "final_status": "warning",
                "used_ai_override": True,
                "reconciliation_reason": "AI softened deterministic fail because supporting metrics looked suspicious.",
            }
        return {
            "final_status": "fail",
            "used_ai_override": False,
            "reconciliation_reason": "Deterministic hard fail retained.",
        }

    if deterministic_status == "pass" and ai_verdict in {"warning", "fail"}:
        return {
            "final_status": ai_verdict,
            "used_ai_override": True,
            "reconciliation_reason": "AI downgraded a soft deterministic pass.",
        }

    if deterministic_status == "warning" and ai_verdict == "fail":
        return {
            "final_status": "fail",
            "used_ai_override": True,
            "reconciliation_reason": "AI escalated warning to fail.",
        }

    if deterministic_status == "warning" and ai_verdict == "pass" and suspicious_metrics and ai_confidence == "high":
        return {
            "final_status": "warning",
            "used_ai_override": False,
            "reconciliation_reason": "Warning retained because metrics were suspicious, not conclusive.",
        }

    return {
        "final_status": deterministic_status,
        "used_ai_override": False,
        "reconciliation_reason": "Deterministic result retained.",
    }