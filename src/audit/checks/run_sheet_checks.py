import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


TARGET_SHEETS = ["Content", "Labeling", "Navigation", "Feedback"]


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
    """
    Index cleaned extraction pages by several useful keys:
    - name
    - pageId
    - url
    - finalUrl
    """
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
    """
    Try to attach the most relevant page(s) to a check result.

    Priority:
    1. explicit source_pages / pages / page_names already on the item
    2. evidence mentioning audited page names
    3. fallback to all audited pages
    """
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

    # Distinguish "unknown" from true "not applicable"
    basis = str(enriched.get("decision_basis", "") or "").strip().lower()
    if status == "N/A":
        if basis == "interactive_required":
            enriched["applicability"] = "unknown"
        elif basis == "proxy":
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
    parser.add_argument("--checks", required=True, help="Path to existing person_a_sheet_checks.json")
    parser.add_argument("--cleaned", required=True, help="Path to person_a_cleaned.json")
    parser.add_argument("--output", required=True, help="Path to enriched checks output json")
    args = parser.parse_args()

    checks_path = Path(args.checks)
    cleaned_path = Path(args.cleaned)
    output_path = Path(args.output)

    if not checks_path.exists():
        raise FileNotFoundError(f"Checks JSON not found: {checks_path}")
    if not cleaned_path.exists():
        raise FileNotFoundError(f"Cleaned JSON not found: {cleaned_path}")

    checks_data = load_json(checks_path)
    cleaned_data = load_json(cleaned_path)

    enriched = enrich_checks_schema(checks_data, cleaned_data)
    save_json(output_path, enriched)

    print(f"Enriched checks written to: {output_path}")


if __name__ == "__main__":
    main()