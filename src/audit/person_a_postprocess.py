import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List


LOCALE_PICKER_PATTERNS = [
    r"pays/région",
    r"country/region",
    r"currency",
    r"devise",
    r"d[.]?t\b",
    r"usd\b",
    r"eur\b",
    r"gbp\b",
    r"cad\b",
    r"aud\b",
    r"jpy\b",
    r"tnd\b",
    r"afrique",
    r"arabie",
    r"barbuda",
    r"burkina",
    r"brazzaville",
    r"caiques",
]

SYSTEM_NOISE_PATTERNS = [
    r"article ajouté au panier",
    r"ajouter au panier",
    r"épuisé",
    r"fermer",
    r"close",
    r"ouvrir",
    r"open",
    r"menu",
    r"search",
    r"recherche",
]

PICKER_NOISE_RE = re.compile("|".join(LOCALE_PICKER_PATTERNS), re.IGNORECASE)
SYSTEM_NOISE_RE = re.compile("|".join(SYSTEM_NOISE_PATTERNS), re.IGNORECASE)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _is_picker_noise(text: str) -> bool:
    if not text:
        return False
    return bool(PICKER_NOISE_RE.search(text))


def _is_system_noise(text: str) -> bool:
    if not text:
        return False
    return bool(SYSTEM_NOISE_RE.search(text))


def _keep_meaningful_text(text: str) -> bool:
    if not text:
        return False

    stripped = str(text).strip()
    if not stripped:
        return False

    if _is_picker_noise(stripped):
        return False

    # Keep common UX labels even if they look short/system-like
    allowlist = {
        "contact",
        "panier",
        "catalog",
        "home",
        "accueil",
        "recherche",
        "e-mail",
        "numéro de téléphone",
        "destination",
        "de",
        "trier par :",
        "filtrer",
        "filtrer et trier",
    }
    if stripped.lower() in allowlist:
        return True

    if _is_system_noise(stripped):
        # still keep some operational UX terms for checks
        if stripped.lower() in {"recherche", "e-mail", "destination", "de", "trier par :", "filtrer"}:
            return True
        return False

    return True


def _filter_string_list(values: List[Any]) -> List[Any]:
    out = []
    seen = set()
    for item in values:
        s = str(item).strip()
        if not _keep_meaningful_text(s):
            continue
        key = s.lower()
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _filter_nav_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = []
    seen = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        label = str(item.get("text") or item.get("label") or "").strip()
        href = str(item.get("href") or "").strip()

        if not _keep_meaningful_text(label):
            continue

        # Exclude locale/currency switch targets
        if _is_picker_noise(label):
            continue

        key = (label.lower(), href.lower())
        if key in seen:
            continue

        filtered.append(item)
        seen.add(key)

    return filtered


def _filter_forms(forms_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(forms_payload, dict):
        return forms_payload

    forms_payload = deepcopy(forms_payload)
    data = forms_payload.get("data", {})
    if not isinstance(data, dict):
        data = {}

    forms = data.get("items")
    if not isinstance(forms, list):
        forms = data.get("forms")
    if not isinstance(forms, list):
        forms = forms_payload.get("forms", [])

    meaningful_forms = []

    for form in forms:
        if not isinstance(form, dict):
            continue

        action = str(form.get("action") or form.get("formAction") or "").strip().lower()
        if form.get("isLocalizationForm") is True or "localization" in action:
            continue

        raw_fields = (
            form.get("visibleFields")
            or form.get("userInputFields")
            or form.get("fields")
            or []
        )
        filtered_fields = []
        for field in raw_fields:
            if not isinstance(field, dict):
                continue
            field_type = str(field.get("type") or field.get("tag") or "").strip().lower()
            if field_type == "hidden":
                continue

            label = str(
                field.get("label")
                or field.get("placeholder")
                or field.get("name")
                or ""
            ).strip()
            if not label:
                continue
            if not _keep_meaningful_text(label):
                continue
            filtered_fields.append(field)

        form = deepcopy(form)
        form["fields"] = filtered_fields
        form["visibleFields"] = filtered_fields
        form["userInputFields"] = filtered_fields
        counts = form.get("counts", {})
        if isinstance(counts, dict):
            counts["userInputFields"] = len(filtered_fields)
            counts["visibleFields"] = len(filtered_fields)
            counts["fields"] = len(filtered_fields)
            form["counts"] = counts

        if filtered_fields:
            meaningful_forms.append(form)

    data["items"] = meaningful_forms
    data["forms"] = meaningful_forms
    data["meaningfulFormCount"] = len(meaningful_forms)

    total_user_input_fields = 0
    for form in meaningful_forms:
        total_user_input_fields += len(form.get("userInputFields", []))
    data["userInputFields"] = total_user_input_fields

    forms_payload["data"] = data

    return forms_payload


def refine_page(page: Dict[str, Any]) -> Dict[str, Any]:
    page = deepcopy(page)

    # Titles & headings
    tah = page.get("titlesAndHeadings", {})
    tah_data = tah.get("data", {})
    if isinstance(tah_data, dict):
        heading_items = tah_data.get("headings", [])
        raw_headings = []
        content_headings = []
        filtered_heading_items = []

        if isinstance(heading_items, list):
            for item in heading_items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                raw_headings.append(text)
                if _keep_meaningful_text(text):
                    filtered_heading_items.append(item)
                    content_headings.append({"text": text})

            tah_data["headings"] = filtered_heading_items

        if isinstance(tah_data.get("rawHeadings"), list):
            raw_headings.extend(str(item).strip() for item in tah_data.get("rawHeadings", []) if str(item).strip())
        if isinstance(tah_data.get("contentHeadings"), list):
            for item in tah_data.get("contentHeadings", []):
                text = str(item.get("text") if isinstance(item, dict) else item).strip()
                if text and _keep_meaningful_text(text):
                    content_headings.append({"text": text})

        if raw_headings:
            tah_data["rawHeadings"] = list(dict.fromkeys(raw_headings))
        if content_headings:
            deduped_content = []
            seen_content = set()
            for item in content_headings:
                text = str(item.get("text") or "").strip()
                key = text.lower()
                if text and key not in seen_content:
                    deduped_content.append(item)
                    seen_content.add(key)
            tah_data["contentHeadings"] = deduped_content

        tah["data"] = tah_data
        page["titlesAndHeadings"] = tah

    # Lists
    lists_payload = page.get("lists", {})
    lists_data = lists_payload.get("data", {})
    if isinstance(lists_data, dict):
        for key in ("items", "meaningfulItems", "meaningfulListItems"):
            if isinstance(lists_data.get(key), list):
                lists_data[key] = _filter_string_list(lists_data[key])
        lists_payload["data"] = lists_data
        page["lists"] = lists_payload

    # Navigation
    navigation = page.get("navigation", {})
    nav_data = navigation.get("data", {})
    if isinstance(nav_data, dict):
        primary_nav = _filter_nav_items(nav_data.get("primaryNav", []) or nav_data.get("primaryNavItems", []))
        footer_nav = _filter_nav_items(nav_data.get("footerNav", []) or nav_data.get("footerNavUseful", []))
        side_nav = _filter_nav_items(nav_data.get("sideNav", []) or nav_data.get("secondaryNavItems", []))
        breadcrumbs = _filter_nav_items(nav_data.get("breadcrumbs", [])) if isinstance(nav_data.get("breadcrumbs"), list) else []
        active_items = _filter_nav_items(nav_data.get("activeItems", [])) if isinstance(nav_data.get("activeItems"), list) else []

        nav_data["primaryNav"] = primary_nav
        nav_data["primaryNavItems"] = primary_nav
        nav_data["footerNav"] = footer_nav
        nav_data["footerNavUseful"] = footer_nav
        nav_data["sideNav"] = side_nav
        nav_data["secondaryNavItems"] = side_nav
        nav_data["breadcrumbs"] = breadcrumbs
        nav_data["activeItems"] = active_items
        nav_data["allNavItems"] = primary_nav + footer_nav + side_nav + breadcrumbs + active_items
        navigation["data"] = nav_data
        page["navigation"] = navigation

    # Links
    links_payload = page.get("links", {})
    links_data = links_payload.get("data", {})
    if isinstance(links_data, dict):
        for key in ("meaningfulLinks", "links"):
            if isinstance(links_data.get(key), list):
                filtered_links = []
                seen = set()
                for entry in links_data.get(key, []):
                    if isinstance(entry, dict):
                        text = str(entry.get("text") or "").strip()
                        href = str(entry.get("href") or "").strip()
                        if not _keep_meaningful_text(text):
                            continue
                        if _is_picker_noise(text):
                            continue
                        k = (text.lower(), href.lower())
                        if k in seen:
                            continue
                        seen.add(k)
                        filtered_links.append(entry)
                    else:
                        s = str(entry).strip()
                        if _keep_meaningful_text(s):
                            if s.lower() not in seen:
                                filtered_links.append(entry)
                                seen.add(s.lower())
                links_data[key] = filtered_links
        links_payload["data"] = links_data
        page["links"] = links_payload

    # Forms
    if "forms" in page:
        page["forms"] = _filter_forms(page["forms"])

    # Quality signals cleanup
    quality = page.get("qualitySignals", {})
    quality_summary = quality.get("summary", {})
    quality_flags = quality.get("flags", [])

    if isinstance(quality_flags, list):
        # Keep the flag because it is useful diagnostic info,
        # but add a second flag when the page was successfully refined.
        if "heavy_picker_or_locale_noise" in quality_flags:
            if "picker_noise_refined" not in quality_flags:
                quality_flags.append("picker_noise_refined")
        quality["flags"] = quality_flags

    if isinstance(quality_summary, dict):
        # Downweight noisy picker count for postprocessed usage
        locale_count = int(quality_summary.get("localeOrPickerLinkCount", 0) or 0)
        if locale_count > 0:
            quality_summary["localeOrPickerLinkCountOriginal"] = locale_count
            quality_summary["localeOrPickerLinkCountPostprocessed"] = max(0, min(locale_count, 5))
        quality["summary"] = quality_summary

    page["qualitySignals"] = quality
    return page


def postprocess(data: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(data)
    pages = out.get("pages", [])
    out["pages"] = [refine_page(page) for page in pages]
    out["source"] = "person_a_cleaned"
    out["generatedFrom"] = "src.audit.person_a_postprocess"
    return out


def clean_person_a_output(data: Dict[str, Any]) -> Dict[str, Any]:
    return postprocess(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to person_a_extraction.json")
    parser.add_argument("--output", required=True, help="Path to cleaned output json")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input extraction not found: {input_path}")

    raw = load_json(input_path)
    cleaned = postprocess(raw)
    save_json(output_path, cleaned)

    print(f"Cleaned extraction written to: {output_path}")


if __name__ == "__main__":
    main()
