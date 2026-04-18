from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple
from dotenv import load_dotenv

from src.audit.ai_review_client import AIReviewClient


SYSTEM_PROMPT = """
You are a strict UX/UI audit review assistant.

You must review structured evidence for one audit check.
You must stay grounded in the provided evidence only.
Do not invent UI elements, flows, or page behavior.
Do not overstate certainty.
If evidence is weak, keep uncertainty explicit.
If there is not enough evidence, return N/A and mark needs_human_review true.

Important evaluation guidance:
- Treat explicit measured signals as valid evidence when they are concrete and numeric.
- Examples of strong evidence include counts, ratios, widths, presence or absence detections,
  consistency scores, contrast values, alt-text counts, extracted component statistics,
  language-distribution signals, and repeated detections across pages.
- Incidental numbers such as item totals, filter counts, or typography summary counts are weak
  unless they directly measure the criterion being checked.
- Do not require screenshots or visual proof when reliable structured metrics are already present.
- If rationale or decision_basis contains measurable evidence and it directly supports the criterion,
  you may return True or False confidently even if evidence_text examples are short.
- Use N/A mainly when the check truly requires runtime behavior, user intent, hidden states,
  unavailable visual context, or unavailable interaction context.
- If the original decision appears unsupported by the measured evidence, you may set contradiction_flag true.

Return a JSON object with exactly these fields:
{
  "reviewed_status": "True" | "False" | "N/A",
  "reviewed_confidence": number,
  "evidence_summary": string,
  "key_insight": string,
  "ux_impact": string,
  "recommended_fix": string,
  "severity_hint": "low" | "medium" | "high",
  "needs_human_review": boolean,
  "contradiction_flag": boolean,
  "recurrence_candidate_label": string,
  "review_note": string
}

Rules:
- reviewed_confidence must be between 0 and 1.
- recurrence_candidate_label should be short and normalized, like:
  "inconsistent primary cta", "missing search in header", "weak form validation feedback".
- contradiction_flag should be true only when the original decision seems inconsistent with the evidence.
- If the issue is minor or very local, severity_hint should be "low".
- If it affects usability significantly or appears structural, use "medium" or "high".
- Evidence summary must be concise and evidence-grounded.
- Recommended fix must be practical and specific.
- Do not include markdown fences.
""".strip()


RECURRENCE_LABEL_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "e",
    "etc",
    "for",
    "from",
    "g",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "items",
    "of",
    "on",
    "or",
    "she",
    "so",
    "such",
    "that",
    "the",
    "their",
    "there",
    "this",
    "to",
    "user",
    "users",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def _load_project_dotenv() -> None:
    project_env = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(dotenv_path=project_env, override=False)
    load_dotenv(override=False)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        number = float(value)
        if math.isnan(number):
            return default
        return max(0.0, min(1.0, number))
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = _safe_str(value).lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n", ""}:
        return False

    return bool(value)


def _normalize_status(value: Any) -> str:
    text = _safe_str(value).lower()
    if text in {"true", "pass", "passed", "yes"}:
        return "True"
    if text in {"false", "fail", "failed", "no"}:
        return "False"
    return "N/A"


def _slugify(text: str) -> str:
    text = _safe_str(text).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_decision_basis(value: Any) -> str:
    text = _slugify(_safe_str(value))
    if not text:
        return ""
    if "direct" in text:
        return "direct"
    if any(marker in text for marker in {"proxy", "indirect", "inferred"}):
        return "proxy"
    if any(marker in text for marker in {"interactive", "runtime", "manual"}):
        return "interactive_required"
    return text


def _compact_recurrence_label(text: str, max_words: int = 6) -> str:
    tokens = [token for token in _slugify(text).split() if token]
    if not tokens:
        return ""

    if len(tokens) > max_words:
        filtered_tokens = [token for token in tokens if token not in RECURRENCE_LABEL_STOPWORDS]
        if filtered_tokens:
            tokens = filtered_tokens

    if len(tokens) > max_words:
        tokens = tokens[:max_words]

    return " ".join(tokens).strip()


def _normalize_recurrence_label(label: Any, criterion: Any) -> str:
    raw_label = _safe_str(label).replace("_", " ").replace("-", " ")
    fallback = _safe_str(criterion).replace("_", " ").replace("-", " ")

    normalized = _compact_recurrence_label(raw_label or fallback)
    if not normalized:
        normalized = _compact_recurrence_label(fallback)
    if not normalized:
        normalized = _slugify(fallback)[:80]

    return normalized[:80]


def _has_explicit_metric_signal(text: Any) -> bool:
    cleaned = _safe_str(text).lower()
    if not cleaned:
        return False

    patterns = [
        r"\b\d+(?:\.\d+)?\s*px\b",
        r"\b\d+(?:\.\d+)?\s*%\b",
        r"\bratio[:=\s]+\d+(?:\.\d+)?\b",
        r"\bscore[:=\s]+\d+(?:\.\d+)?\b",
        r"\bcontrast\b.*\b\d+(?:\.\d+)?\b",
        r"\baverage\b.*\b\d+(?:\.\d+)?\b",
        r"\b(?:present|missing|detected)\s*=\s*\d+(?:\.\d+)?\b",
        r"\b[a-z][a-z _-]{2,}\s*=\s*\d+(?:\.\d+)?\b",
        r"\b\d+\s*(?:issues|errors|fields|buttons|links|pages|images|headings|paragraphs|items|text blocks)\b",
        r"\bconsistency score\b",
        r"\bmissing alt text=\d+\b",
        r"\bimages inspected=\d+\b",
        r"\bevery page=true\b",
        r"\bon every page=true\b",
    ]

    return any(re.search(pattern, cleaned) for pattern in patterns)


def _pick_first_non_empty(row: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = row.get(key)

        if isinstance(value, str):
            value = value.strip()
            if value:
                return value

        if isinstance(value, list):
            parts = []
            for item in value:
                item_text = _safe_str(item)
                if item_text:
                    parts.append(item_text)
            if parts:
                return " | ".join(parts)

        if isinstance(value, dict):
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if serialized and serialized != "{}":
                return serialized

        if value not in ("", None):
            return _safe_str(value)

    return ""


def _normalize_row(sheet_name: str, row: Dict[str, Any], row_index: int) -> Dict[str, Any]:
    criterion = _pick_first_non_empty(
        row,
        [
            "criterion",
            "check_text",
            "check",
            "rule",
            "question",
            "label",
            "audit_check",
            "criterion_text",
            "name",
            "title",
        ],
    )

    evidence = _pick_first_non_empty(
        row,
        [
            "evidence",
            "decision_basis",
            "rationale",
            "source_pages",
            "notes",
            "details",
            "summary",
        ],
    )

    page_url = _pick_first_non_empty(row, ["page_url", "url", "checked_page_url"])
    final_url = _pick_first_non_empty(row, ["final_url"])
    screenshot_path = _pick_first_non_empty(row, ["screenshot_path", "screenshot", "image_path"])
    check_id = _pick_first_non_empty(row, ["check_id", "id", "code", "rule_id"])

    return {
        "_sheet_name": sheet_name,
        "_row_index": row_index,
        "_source_row": _safe_int(row.get("row"), row_index),
        "check_id": check_id,
        "criterion": criterion,
        "status": _normalize_status(
            row.get("status", row.get("answer", row.get("result", row.get("value"))))
        ),
        "confidence": _safe_float(row.get("confidence"), 0.0),
        "confidence_band": _safe_str(row.get("confidence_band")),
        "needs_review": bool(row.get("needs_review", False)),
        "rationale": _pick_first_non_empty(row, ["rationale", "reasoning", "explanation"]),
        "decision_basis": _pick_first_non_empty(row, ["decision_basis"]),
        "evidence": evidence,
        "source_pages": _pick_first_non_empty(row, ["source_pages"]),
        "page_url": page_url,
        "final_url": final_url,
        "page_id": _pick_first_non_empty(row, ["page_id"]),
        "screenshot_path": screenshot_path,
        "raw": row,
    }


def _looks_like_row_dict(candidate: Dict[str, Any]) -> bool:
    row_like_keys = {
        "criterion",
        "check_text",
        "check",
        "rule",
        "question",
        "label",
        "audit_check",
        "criterion_text",
        "status",
        "answer",
        "result",
        "confidence",
        "rationale",
        "decision_basis",
        "evidence",
        "page_url",
        "final_url",
        "source_pages",
    }
    return any(key in candidate for key in row_like_keys)


def _row_signature(normalized_row: Dict[str, Any]) -> Tuple[str, str, str, str, str, str]:
    return (
        _safe_str(normalized_row.get("_sheet_name")).lower(),
        _safe_str(normalized_row.get("_source_row")),
        _slugify(_safe_str(normalized_row.get("check_id"))),
        _slugify(_safe_str(normalized_row.get("criterion"))),
        _safe_str(normalized_row.get("page_url") or normalized_row.get("final_url")).lower(),
        _normalize_status(normalized_row.get("status")),
    )


def _extract_rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, str, str, str]] = set()

    common_row_keys = [
        "rows",
        "checks",
        "items",
        "records",
        "generated_evidence",
        "evidence",
        "results",
    ]

    def add_row(sheet_name: str, row_dict: Dict[str, Any], row_index: int) -> None:
        normalized = _normalize_row(sheet_name, row_dict, row_index)
        signature = _row_signature(normalized)
        if signature in seen:
            return
        seen.add(signature)
        rows.append(normalized)

    def add_rows_from_list(sheet_name: str, candidate_list: List[Any]) -> None:
        if not candidate_list:
            return
        if not all(isinstance(item, dict) for item in candidate_list):
            return
        if not all(_looks_like_row_dict(item) for item in candidate_list):
            return

        for i, row_dict in enumerate(candidate_list, start=1):
            add_row(sheet_name, row_dict, i)

    def walk(node: Any, sheet_name: str = "Generated Evidence") -> None:
        if isinstance(node, list):
            add_rows_from_list(sheet_name, node)
            return

        if not isinstance(node, dict):
            return

        handled_keys: Set[str] = set()

        for key in common_row_keys:
            value = node.get(key)
            if isinstance(value, list):
                add_rows_from_list(sheet_name, value)
                handled_keys.add(key)

        for key, value in node.items():
            if key in handled_keys:
                continue

            next_sheet_name = sheet_name
            if sheet_name == "Generated Evidence" and isinstance(value, (dict, list)):
                if key.lower() not in {"meta", "summary", "recurrence_summary"}:
                    next_sheet_name = key

            walk(value, next_sheet_name)

    if isinstance(payload, list):
        add_rows_from_list("Generated Evidence", payload)
        return rows

    if not isinstance(payload, dict):
        return rows

    if "rows" in payload and isinstance(payload["rows"], list) and all(isinstance(x, dict) for x in payload["rows"]):
        add_rows_from_list("Generated Evidence", payload["rows"])
        return rows

    if "sheets" in payload and isinstance(payload["sheets"], dict):
        for sheet_name, sheet_node in payload["sheets"].items():
            walk(sheet_node, sheet_name)
        return rows

    walk(payload, "Generated Evidence")
    return rows


def _extract_structured_signals(row: Dict[str, Any]) -> Dict[str, Any]:
    text = " | ".join(
        [
            _safe_str(row.get("criterion")),
            _safe_str(row.get("rationale")),
            _safe_str(row.get("decision_basis")),
            _safe_str(row.get("evidence")),
        ]
    ).lower()

    signals: Dict[str, Any] = {
        "mentions_contrast": "contrast" in text,
        "mentions_language_or_i18n": any(token in text for token in ["language", "locale", "translation", "i18n"]),
        "mentions_search": "search" in text,
        "mentions_header": "header" in text or "navbar" in text or "navigation" in text,
        "mentions_form": "form" in text or "input" in text or "field" in text,
        "mentions_validation": "validation" in text or "error message" in text or "required" in text,
        "mentions_cta": "cta" in text or "button" in text or "call to action" in text,
        "mentions_footer": "footer" in text,
        "mentions_abbreviation": "abbr" in text or "abbreviation" in text,
        "mentions_accessibility": "accessibility" in text or "aria" in text or "alt" in text,
        "has_page_url": bool(_safe_str(row.get("page_url") or row.get("final_url"))),
        "has_screenshot": bool(_safe_str(row.get("screenshot_path"))),
    }

    ratio_matches = re.findall(r"ratio[:\s]+([0-9]+(?:\.[0-9]+)?)", text)
    if ratio_matches:
        parsed_ratios = []
        for match in ratio_matches:
            try:
                parsed_ratios.append(float(match))
            except ValueError:
                continue
        if parsed_ratios:
            signals["detected_ratios"] = parsed_ratios

    width_matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*px", text)
    if width_matches:
        parsed_widths = []
        for match in width_matches:
            try:
                parsed_widths.append(float(match))
            except ValueError:
                continue
        if parsed_widths:
            signals["detected_px_values"] = parsed_widths

    float_matches = re.findall(r"\b([0-9]+(?:\.[0-9]+)?)\b", text)
    numeric_values = []
    for match in float_matches:
        try:
            numeric_values.append(float(match))
        except ValueError:
            continue
    if numeric_values:
        signals["numeric_value_count"] = len(numeric_values)
        signals["numeric_values_preview"] = numeric_values[:12]

    count_match = re.search(r"(\d+)\s*(issues|errors|fields|buttons|links|pages|images|headings|paragraphs|items)", text)
    if count_match:
        try:
            signals["detected_count"] = int(count_match.group(1))
            signals["detected_count_unit"] = count_match.group(2)
        except ValueError:
            pass

    bool_like_signals = {
        "detected_every_page_true": "every page=true" in text or "on every page=true" in text,
        "detected_missing_alt_zero": "missing alt text=0" in text or "images missing alt text=0" in text,
        "detected_consistency_score": "consistency score" in text,
        "detected_average_value": "average" in text,
        "detected_present_absent_signal": "present=" in text or "missing=" in text or "detected=" in text,
    }
    signals.update(bool_like_signals)

    return signals


def _has_metric_backed_evidence(row: Dict[str, Any]) -> bool:
    rationale_text = " ".join(
        [
            _safe_str(row.get("rationale")),
            _safe_str(row.get("decision_basis")),
        ]
    )
    if _has_explicit_metric_signal(rationale_text):
        return True

    evidence_text = _safe_str(row.get("evidence"))
    evidence_only_patterns = [
        r"\b\d+(?:\.\d+)?\s*px\b",
        r"\bratio[:=\s]+\d+(?:\.\d+)?\b",
        r"\bscore[:=\s]+\d+(?:\.\d+)?\b",
        r"\bcontrast\b",
        r"\baverage\b",
        r"\b(?:present|missing|detected)\s*=\s*\d+(?:\.\d+)?\b",
        r"\bconsistency score\b",
        r"\bmissing alt text=\d+\b",
        r"\bimages inspected=\d+\b",
        r"\bevery page=true\b",
        r"\bon every page=true\b",
    ]
    return any(re.search(pattern, evidence_text.lower()) for pattern in evidence_only_patterns)


def _build_evidence_strength(row: Dict[str, Any]) -> Dict[str, Any]:
    rationale = _safe_str(row.get("rationale"))
    decision_basis = _safe_str(row.get("decision_basis"))
    evidence = _safe_str(row.get("evidence"))
    combined = " ".join([rationale, decision_basis, evidence]).strip()

    metric_backed = _has_metric_backed_evidence(row)
    numeric_matches = re.findall(r"\b\d+(?:\.\d+)?\b", combined)

    return {
        "has_rationale": bool(rationale),
        "has_decision_basis": bool(decision_basis),
        "has_evidence_text": bool(evidence),
        "has_numeric_rationale": bool(re.search(r"\b\d+(?:\.\d+)?\b", rationale)),
        "has_numeric_decision_basis": bool(re.search(r"\b\d+(?:\.\d+)?\b", decision_basis)),
        "numeric_token_count": len(numeric_matches),
        "is_metric_backed": metric_backed,
        "derived_directness": "proxy_or_indirect" if _is_proxy_or_indirect(row) else "direct_or_supported",
        "has_page_context": bool(_safe_str(row.get("page_url") or row.get("final_url") or row.get("page_id"))),
        "has_visual_context": bool(_safe_str(row.get("screenshot_path"))),
    }


def _build_user_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sheet_name": row["_sheet_name"],
        "row_index": row["_row_index"],
        "check_id": row["check_id"],
        "criterion": row["criterion"],
        "original_assessment": {
            "status": row["status"],
            "confidence": row["confidence"],
            "confidence_band": row["confidence_band"],
            "needs_review": row["needs_review"],
        },
        "context": {
            "page_url": row["page_url"],
            "final_url": row["final_url"],
            "page_id": row["page_id"],
            "source_pages": row["source_pages"],
            "screenshot_path": row["screenshot_path"],
        },
        "evidence_bundle": {
            "rationale": row["rationale"],
            "decision_basis": row["decision_basis"],
            "evidence_text": row["evidence"],
        },
        "structured_signals": _extract_structured_signals(row),
        "evidence_strength": _build_evidence_strength(row),
        "instructions": {
            "stay_grounded": True,
            "do_not_invent_missing_ui": True,
            "prefer_na_when_evidence_is_too_weak": True,
            "accept_reliable_measured_signals_as_valid_evidence": True,
        },
    }


def _is_weak_text(text: str) -> bool:
    cleaned = _safe_str(text).lower()
    if not cleaned:
        return True

    weak_markers = [
        "interactive_required",
        "not enough evidence",
        "insufficient evidence",
        "unknown",
        "unclear",
        "could not determine",
        "needs manual review",
        "manual review",
        "n/a",
    ]

    if cleaned in weak_markers:
        return True

    if len(cleaned) < 20:
        return True

    weak_hits = 0
    for marker in weak_markers:
        if marker in cleaned:
            weak_hits += 1

    if weak_hits >= 2 and len(cleaned) < 120:
        return True

    return False


def _should_skip_ai_review(row: Dict[str, Any]) -> Tuple[bool, str]:
    evidence = _safe_str(row.get("evidence"))
    rationale = _safe_str(row.get("rationale"))
    decision_basis = _safe_str(row.get("decision_basis"))
    criterion = _safe_str(row.get("criterion"))

    evidence_like_parts = [evidence, rationale, decision_basis]
    non_weak_count = sum(0 if _is_weak_text(part) else 1 for part in evidence_like_parts)
    metric_backed = _has_metric_backed_evidence(row)

    if not criterion:
        return True, "Skipped AI review: missing criterion."

    if non_weak_count == 0 and not metric_backed:
        return True, "Skipped AI review: insufficient grounded evidence."

    return False, ""


def _default_ai_result(row: Dict[str, Any], error_message: str = "") -> Dict[str, Any]:
    summary_source = _safe_str(row.get("evidence")) or _safe_str(row.get("rationale")) or _safe_str(row.get("decision_basis"))
    return {
        "reviewed_status": row["status"],
        "reviewed_confidence": row["confidence"],
        "evidence_summary": summary_source[:300],
        "key_insight": "",
        "ux_impact": "",
        "recommended_fix": "",
        "severity_hint": "low",
        "needs_human_review": True,
        "contradiction_flag": False,
        "recurrence_candidate_label": _normalize_recurrence_label("", row["criterion"]),
        "review_note": error_message or "AI review fallback used.",
    }


def _sanitize_ai_result(result: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    status = _normalize_status(result.get("reviewed_status"))
    confidence = _safe_float(result.get("reviewed_confidence"), row["confidence"])

    severity = _safe_str(result.get("severity_hint")).lower()
    if severity not in {"low", "medium", "high"}:
        severity = "low"

    cleaned = {
        "reviewed_status": status,
        "reviewed_confidence": confidence,
        "evidence_summary": _safe_str(result.get("evidence_summary")),
        "key_insight": _safe_str(result.get("key_insight")),
        "ux_impact": _safe_str(result.get("ux_impact")),
        "recommended_fix": _safe_str(result.get("recommended_fix")),
        "severity_hint": severity,
        "needs_human_review": _safe_bool(result.get("needs_human_review", False)),
        "contradiction_flag": _safe_bool(result.get("contradiction_flag", False)),
        "recurrence_candidate_label": _normalize_recurrence_label(
            result.get("recurrence_candidate_label"),
            row["criterion"],
        ),
        "review_note": _safe_str(result.get("review_note")),
    }

    if not cleaned["evidence_summary"]:
        cleaned["evidence_summary"] = (_safe_str(row.get("evidence")) or _safe_str(row.get("rationale")))[:300]

    return cleaned


def _is_proxy_or_indirect(row: Dict[str, Any]) -> bool:
    decision_basis = _normalize_decision_basis(row.get("decision_basis"))
    if decision_basis and decision_basis != "direct":
        return True

    rationale = " ".join(
        [
            _safe_str(row.get("rationale")),
            _safe_str(row.get("decision_basis")),
        ]
    ).lower()
    proxy_markers = {
        "inferred from",
        "rather than exact",
        "suggests",
        "proxy",
        "indirect",
    }
    return any(marker in rationale for marker in proxy_markers)


def _has_weak_supporting_context(row: Dict[str, Any]) -> bool:
    evidence = _safe_str(row.get("evidence"))
    source_pages = _safe_str(row.get("source_pages"))
    has_page_context = bool(_safe_str(row.get("page_url") or row.get("final_url") or row.get("page_id")))
    has_visual_context = bool(_safe_str(row.get("screenshot_path")))

    return _is_weak_text(evidence) and _is_weak_text(source_pages) and not has_page_context and not has_visual_context


def _requires_global_coverage(criterion: Any) -> bool:
    text = _safe_str(criterion).lower()
    global_markers = {
        "always",
        "every page",
        "every screen",
        "on every page",
        "persistent",
    }
    return any(marker in text for marker in global_markers)


def _resolve_final_status(row: Dict[str, Any], ai: Dict[str, Any]) -> Tuple[str, float, str]:
    original_status = row["status"]
    original_conf = row["confidence"]
    reviewed_status = ai["reviewed_status"]
    reviewed_conf = ai["reviewed_confidence"]
    metric_backed = _has_metric_backed_evidence(row)
    weak_context = _has_weak_supporting_context(row)

    if original_status == reviewed_status:
        return reviewed_status, max(original_conf, reviewed_conf), "AI agreed with original decision."

    if (
        original_status == "True"
        and reviewed_status == "N/A"
        and ai["needs_human_review"]
        and _is_proxy_or_indirect(row)
        and not metric_backed
        and (original_conf <= 0.65 or weak_context)
    ):
        final_confidence = min(max(original_conf, reviewed_conf), 0.55)
        return (
            "N/A",
            final_confidence,
            "AI downgraded a low-confidence proxy decision to N/A pending human review.",
        )

    if (
        original_status == "True"
        and reviewed_status == "N/A"
        and ai["needs_human_review"]
        and not metric_backed
        and _requires_global_coverage(row.get("criterion"))
        and original_conf <= 0.85
    ):
        final_confidence = min(max(original_conf, reviewed_conf), 0.55)
        return (
            "N/A",
            final_confidence,
            "AI downgraded an unsupported global-coverage claim to N/A pending human review.",
        )

    if original_status == "N/A" and reviewed_status != "N/A" and reviewed_conf >= 0.55:
        return reviewed_status, reviewed_conf, "AI converted N/A into a concrete decision."

    if original_conf < 0.45 and reviewed_conf >= 0.60:
        return reviewed_status, reviewed_conf, "AI overrode a low-confidence original decision."

    if metric_backed and original_status == "N/A" and reviewed_status != "N/A" and reviewed_conf >= 0.50:
        return reviewed_status, reviewed_conf, "AI used metric-backed evidence to replace N/A."

    if ai["contradiction_flag"] and original_status != reviewed_status:
        if original_conf < 0.65 and reviewed_conf >= 0.50:
            return reviewed_status, reviewed_conf, "AI overrode due to contradiction on a moderate-confidence original decision."
        if reviewed_conf >= original_conf + 0.15:
            return reviewed_status, reviewed_conf, "AI overrode due to evidence contradiction."

    return original_status, max(original_conf, min(reviewed_conf, original_conf + 0.10)), "Original decision kept."


def _group_recurrence(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    for row in rows:
        final_status = _normalize_status(row.get("final_status"))
        if final_status != "False":
            continue

        sheet_name = _safe_str(row.get("_sheet_name"))
        label = _safe_str(row.get("recurrence_candidate_label"))
        if not label:
            label = _slugify(_safe_str(row.get("criterion")))[:80]

        key = (sheet_name, label)
        groups[key].append(row)

    summary: List[Dict[str, Any]] = []

    for (sheet_name, label), items in groups.items():
        urls = []
        severities = []
        insights = []
        fixes = []

        for item in items:
            url = _safe_str(item.get("page_url") or item.get("final_url"))
            if url:
                urls.append(url)
            severity = _safe_str(item.get("severity_hint")).lower()
            if severity:
                severities.append(severity)
            if _safe_str(item.get("key_insight")):
                insights.append(_safe_str(item.get("key_insight")))
            if _safe_str(item.get("recommended_fix")):
                fixes.append(_safe_str(item.get("recommended_fix")))

        unique_urls = sorted(set(urls))
        recurrence_count = len(unique_urls) if unique_urls else len(items)

        severity_rank = {"low": 1, "medium": 2, "high": 3}
        highest_severity = "low"
        for sev in severities:
            if severity_rank.get(sev, 1) > severity_rank.get(highest_severity, 1):
                highest_severity = sev

        systemic = recurrence_count >= 3

        summary.append(
            {
                "sheet_name": sheet_name,
                "pattern_label": label,
                "affected_rows": len(items),
                "affected_pages": recurrence_count,
                "systemic_issue": systemic,
                "highest_severity": highest_severity,
                "representative_insight": insights[0] if insights else "",
                "recommended_action": fixes[0] if fixes else "",
                "example_urls": unique_urls[:10],
            }
        )

        for item in items:
            item["recurrence_count"] = recurrence_count
            item["systemic_issue"] = systemic
            item["group_pattern_label"] = label

    summary.sort(
        key=lambda x: (
            {"high": 0, "medium": 1, "low": 2}.get(x["highest_severity"], 3),
            -x["affected_pages"],
            x["sheet_name"],
            x["pattern_label"],
        )
    )
    return summary


def enrich_checks(input_path: str, output_path: str) -> None:
    _load_project_dotenv()
    client = AIReviewClient()

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    print(f"[DEBUG] Input JSON type: {type(payload).__name__}")
    if isinstance(payload, dict):
        print(f"[DEBUG] Top-level keys: {list(payload.keys())[:20]}")
        if isinstance(payload.get('sheets'), dict):
            print(f"[DEBUG] Sheet names: {list(payload['sheets'].keys())[:20]}")

    base_rows = _extract_rows_from_payload(payload)
    print(f"[DEBUG] Extracted candidate rows after dedupe: {len(base_rows)}")

    if not base_rows:
        raise ValueError("No rows found in the input JSON. Check the structure of sheet_checks.json.")

    enriched_rows: List[Dict[str, Any]] = []

    for idx, row in enumerate(base_rows, start=1):
        criterion_preview = _safe_str(row["criterion"])[:80]
        print(f"[DEBUG] Reviewing row {idx}/{len(base_rows)} | sheet={row['_sheet_name']} | criterion={criterion_preview}")

        skip_ai, skip_reason = _should_skip_ai_review(row)
        if skip_ai:
            ai = _default_ai_result(row, error_message=skip_reason)
        else:
            user_payload = _build_user_payload(row)
            try:
                ai_raw = client.review_json(SYSTEM_PROMPT, user_payload, temperature=0.1)
                ai = _sanitize_ai_result(ai_raw, row)
            except Exception as exc:
                ai = _default_ai_result(row, error_message=f"AI review failed: {exc}")

        final_status, final_confidence, decision_source = _resolve_final_status(row, ai)

        merged = deepcopy(row)
        merged.update(ai)
        merged["final_status"] = final_status
        merged["final_confidence"] = final_confidence
        merged["decision_source"] = decision_source
        merged["recurrence_count"] = 1
        merged["systemic_issue"] = False
        merged["group_pattern_label"] = merged["recurrence_candidate_label"]

        enriched_rows.append(merged)

    recurrence_summary = _group_recurrence(enriched_rows)

    output_payload = {
        "meta": {
            "source_file": os.path.abspath(input_path),
            "row_count": len(enriched_rows),
            "ai_review_enabled": True,
        },
        "rows": enriched_rows,
        "recurrence_summary": recurrence_summary,
    }

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    print(f"[OK] AI-enriched checks written to: {output_path}")
    print(f"[OK] Reviewed rows: {len(enriched_rows)}")
    print(f"[OK] Recurrence groups: {len(recurrence_summary)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-enrich UX/UI audit check rows.")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to sheet_checks.json",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write sheet_checks_ai_enriched.json",
    )
    args = parser.parse_args()

    enrich_checks(args.input, args.output)


if __name__ == "__main__":
    main()
