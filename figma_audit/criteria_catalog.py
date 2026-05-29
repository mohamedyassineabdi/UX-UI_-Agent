from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from figma_audit.config import DEFAULT_CRITERIA_PATH
from figma_audit.models.criteria import CriteriaCatalog


ROOT_DIR = Path(__file__).resolve().parents[1]
EDITABLE_CRITERIA_PATH = Path(
    os.getenv("AUDIT_CRITERIA_CONFIG_PATH") or ROOT_DIR / "shared" / "config" / "audit_axes.json"
)


def _clean_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [" ".join(str(item or "").split()).strip() for item in value if " ".join(str(item or "").split()).strip()]


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _apply_editable_axis_overrides(data: dict[str, object]) -> dict[str, object]:
    if not EDITABLE_CRITERIA_PATH.exists():
        return data
    try:
        editable = json.loads(EDITABLE_CRITERIA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return data
    axes = editable.get("axes") if isinstance(editable, dict) else None
    criteria = data.get("criteria")
    if not isinstance(axes, list) or not isinstance(criteria, list):
        return data
    overrides = {str(axis.get("id") or ""): axis for axis in axes if isinstance(axis, dict)}
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        axis = overrides.get(str(criterion.get("id") or ""))
        if not axis:
            continue
        for source, target in (
            ("name", "name"),
            ("short_name", "short_name"),
            ("description", "validated_definition"),
            ("core_question", "core_question"),
            ("business_impact", "business_impact"),
            ("user_impact", "user_impact"),
            ("default_fix", "default_fix"),
        ):
            text = " ".join(str(axis.get(source) or "").split()).strip()
            if text:
                criterion[target] = text
        for source, target in (
            ("focus", "focus"),
            ("evidence_expectations", "evidence_expectations"),
            ("keywords", "keywords"),
        ):
            values = _clean_string_list(axis.get(source))
            if values:
                criterion[target] = values
        logic = criterion.get("validated_logic")
        if isinstance(logic, dict):
            healthy = _clean_string_list(axis.get("healthy_signals"))
            failures = _clean_string_list(axis.get("failure_modes"))
            out_of_scope = _clean_string_list(axis.get("out_of_scope"))
            if healthy:
                logic["healthy_signals"] = healthy
            if failures:
                logic["failure_modes"] = failures
            if out_of_scope:
                logic["do_not_flag_when"] = out_of_scope
        severity = axis.get("severity_ladder")
        if isinstance(severity, dict):
            current = criterion.get("severity_ladder") if isinstance(criterion.get("severity_ladder"), dict) else {}
            criterion["severity_ladder"] = {
                "high": " ".join(str(severity.get("high") or current.get("high") or "").split()).strip(),
                "medium": " ".join(str(severity.get("medium") or current.get("medium") or "").split()).strip(),
                "low": " ".join(str(severity.get("low") or current.get("low") or "").split()).strip(),
            }
    existing_ids = {str(criterion.get("id") or "") for criterion in criteria if isinstance(criterion, dict)}
    next_order = max([int(criterion.get("order") or 0) for criterion in criteria if isinstance(criterion, dict)] or [0]) + 1
    for axis in axes:
        if not isinstance(axis, dict):
            continue
        axis_id = _clean_text(axis.get("id"))
        if not axis_id or axis_id in existing_ids:
            continue
        severity = axis.get("severity_ladder") if isinstance(axis.get("severity_ladder"), dict) else {}
        criteria.append(
            {
                "id": axis_id,
                "order": next_order,
                "name": _clean_text(axis.get("name")) or f"Custom audit axis {next_order}",
                "short_name": _clean_text(axis.get("short_name")) or _clean_text(axis.get("name")) or f"Custom axis {next_order}",
                "focus": _clean_string_list(axis.get("focus")),
                "validated_definition": _clean_text(axis.get("description")) or "Custom UX/UI audit criterion.",
                "core_question": _clean_text(axis.get("core_question")) or "What should the audit decide for this custom axis?",
                "business_impact": _clean_text(axis.get("business_impact")) or "This custom axis affects the quality and credibility of the audited experience.",
                "user_impact": _clean_text(axis.get("user_impact")) or "This custom area may create avoidable user friction.",
                "default_fix": _clean_text(axis.get("default_fix")) or "Review the cited evidence and improve this custom axis before launch.",
                "validated_logic": {
                    "healthy_signals": _clean_string_list(axis.get("healthy_signals")),
                    "failure_modes": _clean_string_list(axis.get("failure_modes")),
                    "do_not_flag_when": _clean_string_list(axis.get("out_of_scope")),
                },
                "evidence_expectations": _clean_string_list(axis.get("evidence_expectations")),
                "figma_detection_support": {
                    "can_estimate": _clean_string_list(axis.get("look_for")),
                    "needs_human_review": ["Runtime behavior and business-specific interpretation may need human review."],
                },
                "severity_ladder": {
                    "high": _clean_text(severity.get("high")) or "The issue creates a major user or business risk.",
                    "medium": _clean_text(severity.get("medium")) or "The issue creates meaningful friction but does not block the journey.",
                    "low": _clean_text(severity.get("low")) or "The issue is localized or mostly polish-related.",
                },
                "primary_references": [],
                "keywords": _clean_string_list(axis.get("keywords")),
            }
        )
        existing_ids.add(axis_id)
        next_order += 1
    return data


def load_criteria_catalog(path: Path | str | None = None) -> CriteriaCatalog:
    """Load and validate the UX/UI criteria catalog JSON."""
    criteria_path = Path(path) if path is not None else DEFAULT_CRITERIA_PATH

    if not criteria_path.exists():
        raise FileNotFoundError(f"Criteria catalog not found: {criteria_path}")

    with criteria_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if path is None:
        data = _apply_editable_axis_overrides(data)

    try:
        catalog = CriteriaCatalog.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Criteria catalog is not valid: {criteria_path}") from exc

    reference_errors = catalog.validate_links()
    if reference_errors:
        details = "; ".join(reference_errors)
        raise ValueError(f"Criteria catalog has invalid references: {details}")

    return catalog
