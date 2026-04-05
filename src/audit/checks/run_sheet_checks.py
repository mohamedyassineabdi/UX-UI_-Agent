from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import AuditContext, clean_text
from .content_checks import run as run_content_checks
from .feedback_checks import run as run_feedback_checks
from .forms_checks import run as run_forms_checks
from .interaction_controls_checks import run_interaction_controls_checks
from .labeling_checks import run as run_labeling_checks
from .navigation_checks import run as run_navigation_checks
from .presentation_checks import run_presentation_checks
from .visual_hierarchy_checks import run_visual_hierarchy_checks


RESULTS_DIR = Path(__file__).resolve().parents[3] / "shared" / "output" / "results"

TARGET_SHEETS = [
    "Content",
    "Labeling",
    "Presentation",
    "Navigation",
    "Interaction",
    "Feedback",
    "Forms",
    "Visual hierarchy",
]

SHEET_RUNNERS = [
    ("Content", run_content_checks),
    ("Labeling", run_labeling_checks),
    ("Navigation", run_navigation_checks),
    ("Feedback", run_feedback_checks),
    ("Forms", run_forms_checks),
]

PARTNER_SHEET_SPECS = {
    "Presentation": [
        {"row": 4, "criterion_ids": ["tested-viewport-support"], "criterion": "Most common devices, browsers and screen resolutions are supported."},
        {"row": 5, "criterion_ids": ["no-horizontal-scrolling"], "criterion": "There is no horizontal scrolling on any device, browser or screen resolution."},
        {"row": 6, "criterion_ids": ["layout-consistency"], "criterion": "Page layouts are consistent across the whole website."},
        {"row": 7, "criterion_ids": ["negative-space-scanning"], "criterion": "Negative space supports scanning and quickly determining what items are related."},
        {"row": 8, "criterion_ids": ["information-order-expectation"], "criterion": "The order of information matches user expectation."},
        {"row": 9, "criterion_ids": ["modal-focus-appropriateness"], "criterion": "Modal or pop-up windows are used only when strict focus is necessary for the user."},
        {"row": 10, "criterion_ids": ["no-distracting-animation", "no-distracting-animation-runtime"], "criterion": "There is no distracting blinking, flashing, or animation."},
        {"row": 11, "criterion_ids": ["visual-style-consistency"], "criterion": "Visual styles are consistent throughout the application or site."},
        {"row": 12, "criterion_ids": ["visual-metaphor-clarity"], "criterion": "Visual metaphors used will be understood by both casual and expert users."},
    ],
    "Interaction": [
        {"row": 4, "criterion_ids": ["cta-clearly-labeled-and-clickable"], "criterion": "Calls to action (e.g. Register, Add, Submit) are clearly labeled and appear clickable."},
        {"row": 5, "criterion_ids": ["verbs-used-for-actions"], "criterion": "Verbs are used for all actions (e.g. Save, Go, Submit, Continue)."},
        {"row": 7, "criterion_ids": ["interactive-labeling-familiar-not-system-oriented"], "criterion": "Labeling of Interactive elements is familiar to users and does not use system-oriented terminology."},
        {"row": 8, "criterion_ids": ["users-have-control-over-interactive-workflows"], "criterion": "Users have control over interactive content, experiences or workflows — where they’re going, how they get there, and how easily they can stop and start."},
        {"row": 9, "criterion_ids": ["ui-responds-consistently-to-user-actions"], "criterion": "The UI (and all buttons or controls) responds consistently to user actions in terms of visual display, appropriate context and data functionality."},
        {"row": 10, "criterion_ids": ["frequently-used-features-readily-available"], "criterion": "Frequently used features are readily available."},
        {"row": 11, "criterion_ids": ["default-primary-actions-not-destructive"], "criterion": "Default actions — or actions that visually appear as primary actions — are not destructive (e.g. Delete)."},
        {"row": 12, "criterion_ids": ["destructive-actions-confirmed-before-execution"], "criterion": "Destructive actions are highlighted and does not execute directly as a confirmation screen is displayed to confirm the action"},
        {"row": 13, "criterion_ids": ["red-reserved-for-destructive-actions"], "criterion": "Red color is reserved for destructive actions"},
        {"row": 14, "criterion_ids": ["standard-browser-functions-supported"], "criterion": "Standard browser functions (e.g. Back, Forward, Copy, Paste) are supported."},
        {"row": 16, "criterion_ids": ["controls-placed-consistently"], "criterion": "Buttons or other controls are placed consistently in every screen/page."},
        {"row": 17, "criterion_ids": ["controls-related-to-surrounding-information"], "criterion": "All controls are clearly related to the information around them."},
        {"row": 18, "criterion_ids": ["interactive-elements-not-abstracted"], "criterion": "Interactive elements are not abstracted (e.g. buttons clearly look like buttons)."},
        {"row": 19, "criterion_ids": ["editable-droplists-where-applicable"], "criterion": "Droplists are editable where applicable, providing suggestions as the user types."},
        {"row": 20, "criterion_ids": ["controls-provide-hints-help-tooltips-where-applicable"], "criterion": "UI Controls provide rich hints, help, or tool tip text where applicable."},
        {"row": 21, "criterion_ids": ["primary-secondary-tertiary-controls-visually-distinct"], "criterion": "Primary, secondary and tertiary controls are visually distinct from one another."},
        {"row": 22, "criterion_ids": ["secondary-actions-displayed-as-links"], "criterion": "Secondary actions are displayed as links (e.g. Cancel, Hide, Close)."},
    ],
    "Visual hierarchy": [
        {"row": 4, "criterion_ids": ["information-order-importance"], "criterion": "Information is visually organized and presented in order of importance to the user."},
        {"row": 5, "criterion_ids": ["visual-hierarchy-reflects-priority"], "criterion": "The visual hierarchy on the screen reflects the user’s information priority."},
        {"row": 6, "criterion_ids": ["required-action-direction"], "criterion": "Visual hierarchy clearly directs the user to the first (or next) required action."},
        {"row": 7, "criterion_ids": ["cta-primary-visual-element"], "criterion": "Calls to action serve as the primary visual content element (when applicable)."},
        {"row": 8, "criterion_ids": ["visual-grouping-proximity-alignment"], "criterion": "Items that are functionally or contextually connected are grouped together visually (proximity & alignment)."},
        {"row": 9, "criterion_ids": ["negative-space-purpose"], "criterion": "Negative space is used purposefully to help the user scan, identify grouped/ related content and separate unrelated items."},
        {"row": 10, "criterion_ids": ["similar-information-consistency"], "criterion": "Similar types of information are presented in similar, consistent ways."},
        {"row": 12, "criterion_ids": ["ui-uses-no-more-than-3-primary-colors"], "criterion": "The UI uses no more than 3 primary colors."},
        {"row": 13, "criterion_ids": ["chrome-desaturated-colors"], "criterion": "The UI “chrome” is made up of de-saturated colors that recede visually so content comes immediately into focus."},
        {"row": 14, "criterion_ids": ["colors-reinforce-hierarchy"], "criterion": "Colors help establish reinforce the hierarchy of content and interactive elements."},
        {"row": 15, "criterion_ids": ["color-scheme-consistency"], "criterion": "The color scheme is used consistently throughout the application or web site."},
        {"row": 16, "criterion_ids": ["no-oversaturated-colors"], "criterion": "Colors are not over-saturated and don’t vibrate or fatigue the eye."},
        {"row": 18, "criterion_ids": ["most-important-items-have-most-contrast"], "criterion": "Items with the most contrast are also the most important items on the screen, both to the user and/or the business."},
        {"row": 19, "criterion_ids": ["contrast-primary-mechanism-for-hierarchy"], "criterion": "Contrast is the primary mechanism for establishing visual priority/hierarchy."},
        {"row": 20, "criterion_ids": ["contrast-separates-content-from-controls"], "criterion": "Contrast is the primary mechanism for visually separating content from controls (e.g. buttons, links, menus)"},
        {"row": 21, "criterion_ids": ["contrast-separates-labels-from-content"], "criterion": "Contrast is the primary mechanism used to separate labels from the content or data they describe."},
        {"row": 22, "criterion_ids": ["foreground-distinguished-from-background"], "criterion": "Foreground elements (content and controls) are easily distinguished from the background."},
        {"row": 24, "criterion_ids": ["no-more-than-two-font-families"], "criterion": "No more than two (2) distinct font families are used (e.g. Helvetica & Times)."},
        {"row": 25, "criterion_ids": ["content-fonts-at-least-12px"], "criterion": "Fonts used for content are at least 12 pixels in size."},
        {"row": 26, "criterion_ids": ["font-size-weight-differentiate-content-types"], "criterion": "Font size and weight is used to differentiate between content types (e.g. Headings, subheadings, paragraphs)."},
        {"row": 27, "criterion_ids": ["font-consistency-across-screens"], "criterion": "Font styles, sizes and weights are used consistently throughout every screen."},
        {"row": 28, "criterion_ids": ["fonts-reinforce-hierarchy"], "criterion": "Font styles, sizes and weights establish and reinforce the hierarchy of content."},
        {"row": 29, "criterion_ids": ["fonts-separate-labels-from-content"], "criterion": "Different font styles (or families) are used to separate labels from content."},
        {"row": 30, "criterion_ids": ["fonts-separate-content-from-controls"], "criterion": "Different font styles (or families) are used to separate content from controls."},
    ],
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_status(raw_status: Any) -> str:
    if raw_status is None:
        return "N/A"

    if isinstance(raw_status, bool):
        return "TRUE" if raw_status else "FALSE"

    value = str(raw_status).strip().upper()

    true_values = {"TRUE", "T", "YES", "Y", "OK", "VALID", "PASS", "PASSED", "1"}
    false_values = {"FALSE", "F", "NO", "N", "X", "FAIL", "FAILED", "0"}
    na_values = {"", "N/A", "NA", "NOT APPLICABLE", "NONE", "NULL", "UNKNOWN"}

    if value in true_values:
        return "TRUE"
    if value in false_values:
        return "FALSE"
    if value in na_values:
        return "N/A"

    return "N/A"


def confidence_band(confidence: Any) -> str:
    try:
        c = float(confidence)
    except Exception:
        return "Unknown"

    if c >= 0.75:
        return "High"
    if c >= 0.45:
        return "Medium"
    return "Low"


def needs_review(item: Dict[str, Any]) -> bool:
    c = item.get("confidence", 0)
    try:
        c = float(c)
    except Exception:
        c = 0.0

    basis = str(item.get("decision_basis", "") or "").strip().lower()
    status = normalize_status(item.get("status"))

    if basis == "interactive_required":
        return True
    if c < 0.45:
        return True
    if status == "N/A" and basis in {"proxy", "interactive_required"}:
        return True
    return False


def build_page_index(cleaned: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}

    for page in cleaned.get("pages", []):
        page_meta = page.get("pageMeta", {}).get("data", {})
        page_name = page.get("name")
        page_id = page.get("pageId") or page_meta.get("pageId")
        url = page.get("url") or page_meta.get("url")
        final_url = page.get("finalUrl") or page_meta.get("finalUrl")

        keys = [page_name, page_id, url, final_url]
        for key in keys:
            if key:
                index[str(key).strip().lower()] = page

    return index


def page_record_from_name(page_index: Dict[str, Dict[str, Any]], name_or_id: str) -> Optional[Dict[str, Any]]:
    if not name_or_id:
        return None
    return page_index.get(str(name_or_id).strip().lower())


def extract_page_provenance(page: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not page:
        return {
            "page_name": "",
            "page_id": "",
            "page_url": "",
            "final_url": "",
            "screenshot_path": "",
        }

    page_meta = page.get("pageMeta", {}).get("data", {})
    screenshot_paths = page_meta.get("screenshotPaths", {}) or {}

    return {
        "page_name": page.get("name", "") or page_meta.get("name", ""),
        "page_id": page.get("pageId", "") or page_meta.get("pageId", ""),
        "page_url": page.get("url", "") or page_meta.get("url", ""),
        "final_url": page.get("finalUrl", "") or page_meta.get("finalUrl", ""),
        "screenshot_path": screenshot_paths.get("page", "") or "",
    }


def infer_source_pages_from_result(
    item: Dict[str, Any],
    pages_audited: List[str],
    page_index: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    explicit_candidates: List[str] = []

    for key in ("source_pages", "pages", "page_names"):
        value = item.get(key)
        if isinstance(value, list):
            explicit_candidates.extend([str(x) for x in value if x])

    evidence = item.get("evidence", [])
    evidence_strings: List[str] = []
    if isinstance(evidence, list):
        evidence_strings = [str(x) for x in evidence]
    elif evidence:
        evidence_strings = [str(evidence)]

    matched_names: List[str] = []
    for audited_name in pages_audited:
        audited_lower = str(audited_name).strip().lower()
        for ev in evidence_strings:
            if audited_lower == str(ev).strip().lower():
                matched_names.append(audited_name)
                break

    chosen_names = explicit_candidates or matched_names or list(pages_audited)

    seen = set()
    pages: List[Dict[str, Any]] = []
    for name in chosen_names:
        page = page_record_from_name(page_index, name)
        if page:
            page_name = (page.get("name") or "").strip().lower()
            if page_name not in seen:
                pages.append(page)
                seen.add(page_name)

    primary = pages[0] if pages else None
    return pages, primary


def enrich_result_with_provenance(
    item: Dict[str, Any],
    pages_audited: List[str],
    page_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    enriched = dict(item)

    status = normalize_status(enriched.get("status"))
    enriched["status"] = status
    enriched["confidence_band"] = confidence_band(enriched.get("confidence"))
    enriched["needs_review"] = needs_review(enriched)

    source_pages, primary_page = infer_source_pages_from_result(enriched, pages_audited, page_index)

    primary = extract_page_provenance(primary_page)
    enriched.update(primary)

    source_page_records = []
    for page in source_pages:
        source_page_records.append(extract_page_provenance(page))

    enriched["source_pages"] = source_page_records

    basis = str(enriched.get("decision_basis", "") or "").strip().lower()
    if status == "N/A":
        if basis in {"proxy", "interactive_required"}:
            enriched["applicability"] = "unknown"
        else:
            enriched["applicability"] = "not_applicable"
    else:
        enriched["applicability"] = "applicable"

    return enriched


def summarize_sheet(results: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"TRUE": 0, "FALSE": 0, "N/A": 0, "total": 0}
    for item in results:
        status = normalize_status(item.get("status"))
        counts[status] = counts.get(status, 0) + 1
        counts["total"] += 1
    return counts


def load_latest_results(results_dir: Path) -> Optional[Dict[str, Any]]:
    candidates = sorted(results_dir.glob("audit-results_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return load_json(candidates[0])


def partner_status_to_sheet_status(raw_status: Any) -> str:
    normalized = str(raw_status or "").strip().lower()
    if normalized == "pass":
        return "TRUE"
    if normalized in {"fail", "warning"}:
        return "FALSE"
    return "N/A"


def partner_confidence_to_float(raw_confidence: Any, fallback_score: Any = None) -> float:
    if isinstance(raw_confidence, (int, float)):
        return max(0.0, min(1.0, float(raw_confidence)))

    normalized = str(raw_confidence or "").strip().lower()
    mapping = {
        "high": 0.86,
        "medium": 0.62,
        "low": 0.38,
    }
    if normalized in mapping:
        return mapping[normalized]

    try:
        score = float(fallback_score)
    except Exception:
        return 0.45
    return max(0.25, min(0.95, score / 100.0))


def normalize_page_names(raw_pages: Any) -> List[str]:
    names: List[str] = []
    for page in raw_pages or []:
        if isinstance(page, dict):
            name = clean_text(page.get("name") or page.get("page_name"))
            if name:
                names.append(name)
        elif clean_text(page):
            names.append(clean_text(page))
    seen = set()
    ordered: List[str] = []
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(name)
    return ordered


def flatten_partner_evidence(value: Any, prefix: str = "") -> List[str]:
    out: List[str] = []

    if value is None:
        return out

    if isinstance(value, dict):
        for key, nested in value.items():
            next_prefix = f"{prefix}{clean_text(key)}: " if clean_text(key) else prefix
            out.extend(flatten_partner_evidence(nested, next_prefix))
        return out

    if isinstance(value, list):
        for item in value[:10]:
            out.extend(flatten_partner_evidence(item, prefix))
        return out

    cleaned = clean_text(value)
    if cleaned:
        out.append(f"{prefix}{cleaned}" if prefix else cleaned)
    return out


def partner_decision_basis(raw_items: Sequence[Dict[str, Any]]) -> str:
    methods = []
    for item in raw_items:
        method = item.get("method") or []
        if isinstance(method, list):
            methods.extend(str(entry).strip().lower() for entry in method if str(entry).strip())

    if any("runtime" in method for method in methods):
        return "direct"
    if any("document-metrics" in method for method in methods):
        return "direct"
    if any("rendered" in method for method in methods):
        return "direct"
    return "proxy"


def rank_partner_status(raw_status: Any) -> int:
    normalized = str(raw_status or "").strip().lower()
    return {
        "fail": 3,
        "warning": 2,
        "pass": 1,
        "not_applicable": 0,
    }.get(normalized, 0)


def synthesize_partner_result(
    sheet_name: str,
    spec: Dict[str, Any],
    raw_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not raw_items:
        return {
            "sheet": sheet_name,
            "row": spec["row"],
            "criterion": spec["criterion"],
            "status": "N/A",
            "confidence": 0.25,
            "rationale": "No result was generated for this criterion by the partner check module.",
            "evidence": [],
            "decision_basis": "proxy",
            "page_names": [],
            "machine_criterion": ",".join(spec["criterion_ids"]),
        }

    worst_item = max(raw_items, key=lambda item: rank_partner_status(item.get("status")))
    page_names: List[str] = []
    evidence: List[str] = []
    rationales: List[str] = []
    confidences: List[float] = []

    for item in raw_items:
        page_names.extend(normalize_page_names(item.get("pages")))
        evidence.extend(flatten_partner_evidence(item.get("evidence")))
        title = clean_text(item.get("title"))
        description = clean_text(item.get("description"))
        recommendation = clean_text(item.get("recommendation"))

        rationale_parts = [part for part in (title, description) if part]
        if recommendation:
            rationale_parts.append(f"Recommendation: {recommendation}")
        if rationale_parts:
            rationales.append(" ".join(rationale_parts))

        confidences.append(partner_confidence_to_float(item.get("confidence"), item.get("score")))

    deduped_evidence: List[str] = []
    seen_evidence = set()
    for entry in evidence:
        key = entry.lower()
        if key not in seen_evidence:
            seen_evidence.add(key)
            deduped_evidence.append(entry)

    deduped_pages: List[str] = []
    seen_pages = set()
    for page_name in page_names:
        key = page_name.lower()
        if key not in seen_pages:
            seen_pages.add(key)
            deduped_pages.append(page_name)

    rationale = " ".join(rationales) or "Partner module returned no descriptive rationale."

    return {
        "sheet": sheet_name,
        "row": spec["row"],
        "criterion": spec["criterion"],
        "status": partner_status_to_sheet_status(worst_item.get("status")),
        "confidence": min(confidences) if confidences else 0.45,
        "rationale": rationale,
        "evidence": deduped_evidence[:12],
        "decision_basis": partner_decision_basis(raw_items),
        "page_names": deduped_pages,
        "machine_criterion": ",".join(spec["criterion_ids"]),
        "partner_status": clean_text(worst_item.get("status")),
        "partner_category": clean_text(worst_item.get("category")),
    }


def build_partner_sheet_results(
    sheet_name: str,
    raw_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    criteria_map: Dict[str, List[Dict[str, Any]]] = {}
    for item in raw_results:
        key = clean_text(item.get("criterion"))
        if not key:
            continue
        criteria_map.setdefault(key, []).append(item)

    results: List[Dict[str, Any]] = []
    for spec in PARTNER_SHEET_SPECS[sheet_name]:
        matching_items: List[Dict[str, Any]] = []
        for criterion_id in spec["criterion_ids"]:
            matching_items.extend(criteria_map.get(criterion_id, []))
        results.append(synthesize_partner_result(sheet_name, spec, matching_items))
    return results


def generate_checks_schema(
    cleaned_path: Path,
    rendered_path: Path,
    results_path: Optional[Path] = None,
) -> Dict[str, Any]:
    context = AuditContext.from_files(cleaned_path, rendered_path)
    cleaned_data = load_json(cleaned_path)
    rendered_data = load_json(rendered_path)

    page_results = None
    if results_path and results_path.exists():
        results_data = load_json(results_path)
        if isinstance(results_data, dict):
            page_results = results_data.get("pages")

    sheets: Dict[str, Dict[str, Any]] = {}
    for sheet_name, runner in SHEET_RUNNERS:
        raw_results = [item.to_dict() for item in runner(context)]
        sheets[sheet_name] = {
            "summary": summarize_sheet(raw_results),
            "results": raw_results,
        }

    presentation_results = build_partner_sheet_results(
        "Presentation",
        run_presentation_checks(cleaned_data, rendered_data, page_results=page_results),
    )
    interaction_results = build_partner_sheet_results(
        "Interaction",
        run_interaction_controls_checks(cleaned_data, rendered_data),
    )
    visual_hierarchy_results = build_partner_sheet_results(
        "Visual hierarchy",
        run_visual_hierarchy_checks(cleaned_data, rendered_data),
    )

    for sheet_name, raw_results in (
        ("Presentation", presentation_results),
        ("Interaction", interaction_results),
        ("Visual hierarchy", visual_hierarchy_results),
    ):
        sheets[sheet_name] = {
            "summary": summarize_sheet(raw_results),
            "results": raw_results,
        }

    return {
        "version": 1,
        "generator": "src.audit.checks.run_sheet_checks",
        "inputs": {
            "person_a": str(cleaned_path),
            "rendered": str(rendered_path),
            "results": str(results_path) if results_path else "",
        },
        "pagesAudited": context.page_names(),
        "sheets": sheets,
    }


def enrich_checks_schema(checks_data: Dict[str, Any], cleaned_data: Dict[str, Any]) -> Dict[str, Any]:
    page_index = build_page_index(cleaned_data)
    pages_audited = checks_data.get("pagesAudited", [])

    out = dict(checks_data)
    out["version"] = max(int(out.get("version", 1)), 2)
    out["schema"] = {
        "result_fields": [
            "sheet",
            "row",
            "criterion",
            "status",
            "confidence",
            "confidence_band",
            "needs_review",
            "applicability",
            "page_name",
            "page_id",
            "page_url",
            "final_url",
            "screenshot_path",
            "source_pages",
            "rationale",
            "evidence",
            "decision_basis",
        ]
    }

    sheets = out.get("sheets", {})
    for sheet_name, sheet_payload in sheets.items():
        raw_results = sheet_payload.get("results", [])
        enriched_results = [
            enrich_result_with_provenance(item, pages_audited, page_index)
            for item in raw_results
        ]
        sheet_payload["results"] = enriched_results
        sheet_payload["summary"] = summarize_sheet(enriched_results)

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks", help="Path to an existing person_a_sheet_checks.json to enrich")
    parser.add_argument("--cleaned", required=True, help="Path to person_a_cleaned.json")
    parser.add_argument("--rendered", help="Path to rendered_ui_extraction.json. When provided, checks are generated before enrichment.")
    parser.add_argument("--results", help="Optional path to audit-results_*.json used by runtime-oriented partner checks.")
    parser.add_argument("--output", required=True, help="Path to enriched checks output json")
    args = parser.parse_args()

    cleaned_path = Path(args.cleaned)
    output_path = Path(args.output)

    if not cleaned_path.exists():
        raise FileNotFoundError(f"Cleaned JSON not found: {cleaned_path}")

    cleaned_data = load_json(cleaned_path)

    if args.rendered:
        rendered_path = Path(args.rendered)
        if not rendered_path.exists():
            raise FileNotFoundError(f"Rendered JSON not found: {rendered_path}")

        results_path = Path(args.results) if args.results else None
        if results_path is None:
            latest_results = load_latest_results(RESULTS_DIR)
            if latest_results is not None:
                latest_candidates = sorted(RESULTS_DIR.glob("audit-results_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
                results_path = latest_candidates[0] if latest_candidates else None

        checks_data = generate_checks_schema(cleaned_path, rendered_path, results_path=results_path)
    elif args.checks:
        checks_path = Path(args.checks)
        if not checks_path.exists():
            raise FileNotFoundError(f"Checks JSON not found: {checks_path}")
        checks_data = load_json(checks_path)
    else:
        raise ValueError("Provide either --rendered to generate checks or --checks to enrich an existing checks JSON.")

    enriched = enrich_checks_schema(checks_data, cleaned_data)
    save_json(output_path, enriched)

    print(f"Enriched checks written to: {output_path}")


if __name__ == "__main__":
    main()
