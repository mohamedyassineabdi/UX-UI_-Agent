from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .common import (
    AXIS_DEFINITIONS,
    AXIS_KEYWORDS,
    clamp,
    clean_text,
    contains_keyword,
    dedupe_strings,
    mean,
    normalize_status,
    safe_float,
    safe_int,
    score_to_severity,
)
from .vision_client import run_gtm_vision_review


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
RESULTS_DIR = ROOT_DIR / "shared" / "output" / "results"
DEFAULT_WEBSITE_MENU = GENERATED_DIR / "website_menu.json"
DEFAULT_CLEANED = GENERATED_DIR / "html_cleaned.json"
DEFAULT_RENDERED = GENERATED_DIR / "rendered_ui_extraction.json"
DEFAULT_CHECKS = GENERATED_DIR / "sheet_checks.json"
DEFAULT_OUTPUT = GENERATED_DIR / "gtm_audit.json"

ACTION_WORDS = {"contact", "demander", "demo", "discover", "en savoir plus", "learn", "planifier", "request", "start", "talk", "try"}
AUDIENCE_WORDS = {"b2b", "e-commerce", "enterprise", "equipes", "fabricants", "grossistes", "professionnel", "teams"}
TRUST_WORDS = {"bpi", "client", "clients", "french tech", "partner", "partenaire", "partners", "testimonial", "vision"}
VISION_TRUST_PAGE_WORDS = {
    "about",
    "apropos",
    "a-propos",
    "equipe",
    "équipe",
    "founder",
    "founders",
    "leader",
    "leadership",
    "notre equipe",
    "qui sommes nous",
    "team",
    "trust",
    "vision",
}
PROOF_PATTERNS = [r"\b\d+\s*%\b", r"\b\d+\s*(minutes|min|jours|days|hours|heures)\b", r"\b-\d+\s*%\b"]
LOCALE_NOISE = {"deutsch", "english", "espanol", "español", "francais", "français", "français▼", "italiano", "nederlands", "portugues", "português"}
AXIS_IMPACT = {
    "task_execution": "Friction in key tasks increases drop-off and weakens the product story in a live sales context.",
    "flow_architecture": "Weak architecture slows comprehension and makes the offer feel less mature.",
    "trust_accessibility": "Trust and accessibility gaps create perceived risk and narrow the reachable audience.",
    "ui_consistency": "Inconsistent UI signals reduce perceived product maturity and scalability.",
    "visual_brand": "A weak visual narrative lowers memorability and product differentiation.",
    "content_microcopy": "Unclear messaging makes the value proposition harder to understand and repeat.",
    "market_alignment": "Weak GTM alignment makes it harder for prospects to see why this product fits them now.",
}
AXIS_USER_IMPACT = {
    "task_execution": "core tasks demand more effort than they should",
    "flow_architecture": "people struggle to understand where they are, what comes next, or how the product is organized",
    "trust_accessibility": "parts of the experience may not feel safe, inclusive, or reliable enough",
    "ui_consistency": "recurring patterns do not behave or look consistently, which makes the product feel less mature",
    "visual_brand": "the interface does not project enough confidence, polish, or brand distinctiveness at first glance",
    "content_microcopy": "the value proposition and interaction cues are harder to understand than they should be",
    "market_alignment": "prospects may not immediately understand why this product is relevant for their context or market",
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def to_path(raw: str, default: Path) -> Path:
    if not clean_text(raw):
        return default
    path = Path(raw)
    return path if path.is_absolute() else ROOT_DIR / path


def load_latest_results() -> Optional[Dict[str, Any]]:
    candidates = sorted(RESULTS_DIR.glob("audit-results_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return load_json(candidates[0])


def collect_numbers(node: Any, key: str) -> List[float]:
    values: List[float] = []
    if isinstance(node, dict):
        for nested_key, nested in node.items():
            if nested_key == key and isinstance(nested, (int, float)):
                values.append(float(nested))
            values.extend(collect_numbers(nested, key))
    elif isinstance(node, list):
        for nested in node:
            values.extend(collect_numbers(nested, key))
    return values


def nav_count(items: Iterable[Dict[str, Any]]) -> int:
    total = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        total += 1
        total += nav_count(item.get("children") or [])
    return total


def page_meta(page: Dict[str, Any]) -> Dict[str, Any]:
    return ((page.get("pageMeta") or {}).get("data") or {})


def homepage_page(cleaned_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pages = cleaned_data.get("pages") or []
    for page in pages:
        if clean_text(page_meta(page).get("sourceType")).lower() == "homepage":
            return page
    return pages[0] if pages else None


def page_texts(page: Dict[str, Any], bucket: str) -> List[str]:
    data = ((page.get("textContent") or {}).get("data") or {})
    values = data.get(bucket) or []
    if bucket == "ctaTexts":
        return dedupe_strings(item.get("text") for item in values if isinstance(item, dict) and clean_text(item.get("text")))
    return dedupe_strings(item.get("text") for item in values if isinstance(item, dict))


def headings(page: Dict[str, Any]) -> List[str]:
    items = (((page.get("titlesAndHeadings") or {}).get("data") or {}).get("headings") or [])
    return dedupe_strings(item.get("text") for item in items if isinstance(item, dict))


def flatten_checks(checks_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for sheet_name, payload in (checks_data.get("sheets") or {}).items():
        for item in payload.get("results") or []:
            evidence = item.get("evidence")
            rows.append(
                {
                    "sheet": clean_text(item.get("sheet") or sheet_name),
                    "row": safe_int(item.get("row")),
                    "criterion": clean_text(item.get("criterion")),
                    "status": normalize_status(item.get("status")),
                    "confidence": clamp(safe_float(item.get("confidence"), 0.5), 0.0, 1.0),
                    "decision_basis": clean_text(item.get("decision_basis")).lower(),
                    "rationale": clean_text(item.get("rationale")),
                    "evidence": evidence if isinstance(evidence, list) else [clean_text(evidence)] if clean_text(evidence) else [],
                    "page_name": clean_text(item.get("page_name")),
                    "page_url": clean_text(item.get("page_url") or item.get("final_url")),
                    "screenshot_path": clean_text(item.get("screenshot_path")),
                    "evidence_bundle": item.get("evidence_bundle") if isinstance(item.get("evidence_bundle"), dict) else None,
                }
            )
    return rows


def sheet_score(summary: Dict[str, Any]) -> float:
    passed = safe_int(summary.get("TRUE"))
    failed = safe_int(summary.get("FALSE"))
    total = passed + failed
    return round((passed / total) * 100.0, 1) if total else 55.0


def count_matches(texts: Iterable[str], words: Iterable[str]) -> int:
    haystack = " ".join(clean_text(text) for text in texts).lower()
    return sum(1 for word in words if word.lower() in haystack)


def count_proof_points(texts: Iterable[str]) -> int:
    haystack = " ".join(clean_text(text) for text in texts)
    return sum(len(re.findall(pattern, haystack, flags=re.IGNORECASE)) for pattern in PROOF_PATTERNS)


def build_profile(website_menu: Dict[str, Any], cleaned_data: Dict[str, Any], rendered_data: Dict[str, Any], checks_data: Dict[str, Any], results_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned_pages = cleaned_data.get("pages") or []
    rendered_pages = rendered_data.get("pages") or []
    homepage = homepage_page(cleaned_data) or {}
    meta = page_meta(homepage)
    home_headings = headings(homepage)[:5]
    home_paragraphs = page_texts(homepage, "paragraphs")[:6]
    home_ctas = [
        text
        for text in page_texts(homepage, "ctaTexts")
        if text.lower() not in LOCALE_NOISE
        and text.lower() not in {"menu", "search"}
        and any(word in text.lower() for word in ACTION_WORDS)
    ][:8]
    text_pool = dedupe_strings(home_headings + home_paragraphs + home_ctas, limit=24)
    summary = (results_data or {}).get("summary") or {}
    interactions_tested = safe_int(summary.get("testedInteractions"))
    interactions_ok = safe_int(summary.get("successfulInteractions"))
    interaction_success_rate = (interactions_ok / interactions_tested) * 100.0 if interactions_tested else 52.0
    host = urlparse(clean_text(website_menu.get("homepage"))).netloc or clean_text(website_menu.get("homepage"))
    if host.startswith("www."):
        host = host[4:]
    display_name = host.split(".")[0].replace("-", " ").replace("_", " ").title() if host else "Client site"
    return {
        "site": {
            "homepage": clean_text(website_menu.get("homepage")),
            "domain": host,
            "display_name": display_name,
            "language": clean_text(website_menu.get("language") or meta.get("language")),
        },
        "counts": {
            "pages": len(cleaned_pages),
            "topLevelNavigation": len(website_menu.get("navigation") or []),
            "navigationItems": nav_count(website_menu.get("navigation") or []),
        },
        "messaging": {
            "heroHeadings": home_headings,
            "heroParagraphs": home_paragraphs,
            "heroCtas": home_ctas,
            "textPool": text_pool,
            "audienceSignals": count_matches(text_pool, AUDIENCE_WORDS),
            "trustSignals": count_matches(text_pool, TRUST_WORDS),
            "proofSignals": count_proof_points(text_pool),
        },
        "metrics": {
            "interactionSuccessRate": interaction_success_rate,
            "designHealth": mean([mean(collect_numbers(page.get("renderedUi") or {}, "overallDesignSystemHealth"), default=0.0) for page in rendered_pages], default=0.0),
            "componentConsistency": mean([mean(collect_numbers(page.get("renderedUi") or {}, "componentConsistency"), default=0.0) for page in rendered_pages], default=0.0),
            "navigationClarity": mean([mean(collect_numbers(page.get("renderedUi") or {}, "navigationClarity"), default=0.0) for page in rendered_pages], default=0.0),
            "contentHierarchy": mean([mean(collect_numbers(page.get("renderedUi") or {}, "contentHierarchy"), default=0.0) for page in rendered_pages], default=0.0),
            "ctaClarity": mean([mean(collect_numbers(page.get("renderedUi") or {}, "ctaClarity"), default=0.0) for page in rendered_pages], default=0.0),
            "formUsability": mean([mean(collect_numbers(page.get("renderedUi") or {}, "formUsability"), default=0.0) for page in rendered_pages], default=0.0),
            "accessibilityReadiness": mean([mean(collect_numbers(page.get("renderedUi") or {}, "accessibilityReadiness"), default=0.0) for page in rendered_pages], default=0.0),
            "interactionFeedback": mean([mean(collect_numbers(page.get("renderedUi") or {}, "interactionFeedback"), default=0.0) for page in rendered_pages], default=0.0),
            "conversionReadiness": mean([mean(collect_numbers(page.get("renderedUi") or {}, "conversionReadiness"), default=0.0) for page in rendered_pages], default=0.0),
        },
        "sheetScores": {sheet_name: sheet_score((payload or {}).get("summary") or {}) for sheet_name, payload in (checks_data.get("sheets") or {}).items()},
        "homepageScreenshot": clean_text((meta.get("screenshotPaths") or {}).get("page")),
    }


def select_focus_screenshots(cleaned_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = []
    for page in cleaned_data.get("pages") or []:
        meta = page_meta(page)
        shot = clean_text((meta.get("screenshotPaths") or {}).get("page"))
        if not shot:
            continue
        name = clean_text(page.get("name"))
        url = clean_text(page.get("finalUrl") or page.get("url"))
        score = 100 if clean_text(meta.get("sourceType")).lower() == "homepage" else 0
        if "contact" in name.lower() or "contact" in url.lower():
            score += 30
        if "solution" in name.lower() or "commerce" in " ".join(clean_text(item) for item in meta.get("pageTypeClues") or []).lower():
            score += 20
        candidates.append((score, {"page_name": name, "page_url": url, "title": clean_text(meta.get("title")), "reason": "Representative page", "screenshot_path": shot}))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:3]]


def select_scanned_pages(cleaned_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for page in cleaned_data.get("pages") or []:
        meta = page_meta(page)
        shot = clean_text((meta.get("screenshotPaths") or {}).get("page"))
        if not shot:
            continue
        name = clean_text(page.get("name")) or "Page"
        url = clean_text(page.get("finalUrl") or page.get("url"))
        key = url or name or shot
        if key in seen:
            continue
        seen.add(key)
        pages.append(
            {
                "page_name": name,
                "page_url": url,
                "title": clean_text(meta.get("title")),
                "source_type": clean_text(meta.get("sourceType")),
                "screenshot_path": shot,
            }
        )
    return pages


def select_vision_screenshots(scanned_pages: List[Dict[str, Any]], focus_screenshots: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    candidates: List[tuple[int, int, Dict[str, Any]]] = []
    seen: set[str] = set()
    combined = list(scanned_pages or []) + list(focus_screenshots or [])
    for order, item in enumerate(combined):
        shot = clean_text(item.get("screenshot_path"))
        if not shot:
            continue
        key = clean_text(item.get("page_url")) or clean_text(item.get("page_name")) or shot
        if key in seen:
            continue
        seen.add(key)
        text = " ".join(
            [
                clean_text(item.get("page_name")),
                clean_text(item.get("page_url")),
                clean_text(item.get("title")),
                clean_text(item.get("source_type")),
                clean_text(item.get("reason")),
            ]
        ).lower()
        score = 0
        if order == 0 or "homepage" in text or "accueil" in text or "home" in text:
            score += 100
        if any(word in text for word in VISION_TRUST_PAGE_WORDS):
            score += 90
        if "contact" in text:
            score += 35
        if any(word in text for word in ("solution", "product", "produit", "fashion", "pro")):
            score += 30
        candidates.append((score, -order, item))
    candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return [item for _, _, item in candidates[: max(1, limit)]]


def row_weight(row: Dict[str, Any]) -> float:
    basis = row.get("decision_basis") or ""
    return 1.0 if basis == "direct" else 0.75 if basis == "proxy" else 0.55 if basis == "interactive_required" else 0.7


def axis_rows(flat_rows: List[Dict[str, Any]], axis: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = {sheet.lower() for sheet in axis.get("focus") or []}
    keywords = AXIS_KEYWORDS.get(axis["id"], [])
    out = []
    for row in flat_rows:
        texts = [row.get("criterion"), row.get("rationale")] + (row.get("evidence") or [])
        if clean_text(row.get("sheet")).lower() in focus or contains_keyword(texts, keywords):
            out.append(row)
    return out


def axis_row_score(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    if not rows:
        return {"score": 55.0, "confidence": 0.25}
    total = 0.0
    points = 0.0
    confs = []
    for row in rows:
        weight = row_weight(row)
        confidence = clamp(safe_float(row.get("confidence"), 0.5), 0.15, 1.0)
        confs.append(confidence)
        status = normalize_status(row.get("status"))
        status_score = 1.0 if status == "TRUE" else 0.0 if status == "FALSE" else 0.5
        factor = 0.5 + 0.5 * confidence
        total += weight * factor
        points += weight * factor * status_score
    return {"score": round((points / total) * 100.0, 1) if total else 55.0, "confidence": round(clamp(mean(confs, default=0.35) * min(1.0, len(rows) / 7.0), 0.2, 0.95), 2)}


def metric_score(axis_id: str, profile: Dict[str, Any]) -> float:
    metrics = profile["metrics"]
    sheets = profile["sheetScores"]
    messaging = profile["messaging"]
    if axis_id == "task_execution":
        return mean([metrics.get("interactionSuccessRate", 52.0), metrics.get("formUsability", 55.0), metrics.get("interactionFeedback", 55.0), metrics.get("ctaClarity", 55.0)], default=55.0)
    if axis_id == "flow_architecture":
        nav_structure = 82.0 if profile["counts"]["topLevelNavigation"] >= 3 and profile["counts"]["navigationItems"] >= 5 else 52.0
        return mean([metrics.get("navigationClarity", 55.0), metrics.get("contentHierarchy", 55.0), nav_structure, sheets.get("Navigation", 55.0)], default=55.0)
    if axis_id == "trust_accessibility":
        trust_score = clamp(40.0 + messaging.get("trustSignals", 0) * 12.0, 0.0, 100.0)
        return mean([metrics.get("accessibilityReadiness", 55.0), sheets.get("Content", 55.0), sheets.get("Labeling", 55.0), sheets.get("Forms", 55.0), trust_score], default=55.0)
    if axis_id == "ui_consistency":
        return mean([metrics.get("designHealth", 55.0), metrics.get("componentConsistency", 55.0), sheets.get("Presentation", 55.0), sheets.get("Visual hierarchy", 55.0)], default=55.0)
    if axis_id == "visual_brand":
        brand_score = clamp(45.0 + messaging.get("trustSignals", 0) * 8.0 + min(len(profile["messaging"]["heroHeadings"]), 3) * 6.0, 0.0, 100.0)
        return mean([metrics.get("designHealth", 55.0), metrics.get("contentHierarchy", 55.0), metrics.get("ctaClarity", 55.0), brand_score], default=55.0)
    if axis_id == "content_microcopy":
        copy_score = clamp(35.0 + min(len(profile["messaging"]["heroCtas"]), 4) * 10.0, 0.0, 100.0)
        return mean([sheets.get("Content", 55.0), sheets.get("Labeling", 55.0), copy_score], default=55.0)
    audience_score = clamp(35.0 + profile["messaging"].get("audienceSignals", 0) * 15.0, 0.0, 100.0)
    proof_score = clamp(30.0 + profile["messaging"].get("proofSignals", 0) * 18.0, 0.0, 100.0)
    return mean([metrics.get("conversionReadiness", 55.0), metrics.get("ctaClarity", 55.0), audience_score, proof_score], default=55.0)


def vision_axis_score(payload: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    severity = clean_text(payload.get("severity")).lower()
    confidence = clamp(safe_float(payload.get("confidence"), 0.5), 0.1, 1.0)
    base = {"low": 80.0, "medium": 58.0, "high": 36.0}.get(severity)
    return None if base is None else clamp(base + (confidence - 0.5) * 12.0, 0.0, 100.0)


def _detail_sentence(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."


def _is_low_signal_evidence(text: str, row: Dict[str, Any]) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return True
    if re.match(r"^(siteScore|checkedPages)\s*:", cleaned, flags=re.IGNORECASE):
        return True
    page_values = {
        clean_text(row.get("page_name")).lower(),
        clean_text(row.get("page_url")).lower(),
    }
    if cleaned.lower() in {value for value in page_values if value}:
        return True
    return len(cleaned.split()) <= 2 and cleaned.replace(".", "").isdigit() is False


def _split_rationale_and_recommendation(text: Any) -> tuple[str, str]:
    cleaned = clean_text(text)
    if not cleaned:
        return "", ""
    parts = re.split(r"\bRecommendation:\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)
    rationale = clean_text(parts[0])
    recommendation = clean_text(parts[1]) if len(parts) > 1 else ""
    return rationale, recommendation


def _short_evidence(row: Dict[str, Any]) -> str:
    evidence_values = [clean_text(item) for item in (row.get("evidence") or []) if clean_text(item)]
    if evidence_values:
        first = evidence_values[0]
        if not _is_low_signal_evidence(first, row):
            return first[:240]
    rationale, _ = _split_rationale_and_recommendation(row.get("rationale"))
    return rationale[:240]


def _page_label(row: Dict[str, Any]) -> str:
    return clean_text(row.get("page_name")) or clean_text(row.get("page_url")) or "the audited journey"


def _polish_issue_text(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    cleaned = re.sub(
        r"(?<=[a-z0-9])\s+(?=(At least|The available|The visible|Some |Average |Actual |No |Frequently |Interactive |Calls |Control |Expose |Create |Front-loaded |Most |Primary ))",
        ". ",
        cleaned,
    )
    cleaned = cleaned.replace("=False", " was not found")
    cleaned = cleaned.replace("=True", " was found")
    return _detail_sentence(cleaned)


def finding_from_row(row: Dict[str, Any], axis: Dict[str, Any]) -> Dict[str, Any]:
    severity = "high" if row["confidence"] >= 0.8 else "medium" if row["confidence"] >= 0.55 else "low"
    page_label = _page_label(row)
    evidence = _short_evidence(row)
    rationale, extracted_recommendation = _split_rationale_and_recommendation(row.get("rationale"))
    rationale_sentence = _polish_issue_text(rationale)
    evidence_sentence = _polish_issue_text(evidence) if evidence and evidence.lower() not in rationale.lower() else ""
    explanation = (
        f"On {page_label}, the audit found that '{row['criterion']}' is not fully met. "
        f"{rationale_sentence or 'The current interface does not give users enough support around this part of the experience.'}"
        f"{f' Observed signal: {evidence_sentence}' if evidence_sentence else ''}"
    ).strip()
    why_it_matters = (
        f"This matters because {AXIS_USER_IMPACT[axis['id']]}. "
        f"In a GTM context, visible friction on {page_label} can make the product feel harder to understand, trust, or adopt during a first review."
    )
    recommendation = _detail_sentence(extracted_recommendation) or (
        f"Fix this first on {page_label} by clarifying the interaction, tightening the label or feedback, "
        f"and making the intended next step more obvious."
    )
    return {
        "title": row["criterion"],
        "pageName": row["page_name"],
        "pageUrl": row["page_url"],
        "sourceSheet": row["sheet"],
        "severity": severity,
        "confidence": row["confidence"],
        "evidence": clean_text(evidence)[:240],
        "explanation": explanation,
        "whyItMatters": why_it_matters,
        "recommendation": recommendation,
        "screenshotPath": row["screenshot_path"],
        "evidenceBundle": row.get("evidence_bundle"),
    }


def ai_discovered_findings(vision: Dict[str, Any], screenshots: List[Dict[str, Any]], axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = (vision or {}).get("result")
    if not isinstance(result, dict):
        return []

    axis_by_id = {axis["id"]: axis for axis in axes}
    raw_items = []
    for key in ("visual_trust_findings", "criteria_discoveries", "priority_issues"):
        items = result.get(key)
        if isinstance(items, list):
            raw_items.extend(item for item in items if isinstance(item, dict))

    findings: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_items:
        axis_id = clean_text(item.get("axis_id"))
        axis = axis_by_id.get(axis_id)
        if not axis:
            continue
        title = clean_text(item.get("criterion") or item.get("title"))
        if not title:
            continue
        key = (axis_id, title.lower())
        if key in seen:
            continue
        seen.add(key)

        screenshot = {}
        if item.get("screenshot_index") not in (None, ""):
            screenshot_index = safe_int(item.get("screenshot_index"))
            if 0 <= screenshot_index < len(screenshots):
                screenshot = screenshots[screenshot_index]
        if not screenshot:
            page_name = clean_text(item.get("page_name"))
            page_url = clean_text(item.get("page_url"))
            for candidate in screenshots:
                if page_url and page_url == clean_text(candidate.get("page_url")):
                    screenshot = candidate
                    break
                if page_name and page_name.lower() == clean_text(candidate.get("page_name")).lower():
                    screenshot = candidate
                    break

        page_name = clean_text(item.get("page_name")) or clean_text(screenshot.get("page_name")) or "AI-reviewed screen"
        page_url = clean_text(item.get("page_url")) or clean_text(screenshot.get("page_url"))
        evidence = _detail_sentence(clean_text(item.get("evidence") or item.get("reason"))) or "The AI review identified this issue from the reviewed screenshots."
        explanation = f"On {page_name}, the AI review identified a GTM issue that is not necessarily covered by the workbook criteria: {evidence}"
        why_it_matters = _detail_sentence(clean_text(item.get("why_it_matters"))) or (
            f"This matters because {AXIS_USER_IMPACT[axis_id]}. In a GTM context, it can reduce clarity, trust, or sales readiness during a first review."
        )
        recommendation = _detail_sentence(clean_text(item.get("recommendation"))) or "Review this screen manually and prioritize the change if it affects a primary commercial journey."
        findings.append(
            {
                "title": title,
                "axisId": axis_id,
                "axisName": axis["short_name"],
                "pageName": page_name,
                "pageUrl": page_url,
                "sourceSheet": "AI Discovery",
                "severity": clean_text(item.get("severity")).lower() or "medium",
                "confidence": clamp(safe_float(item.get("confidence"), 0.65), 0.0, 1.0),
                "evidence": evidence[:240],
                "explanation": explanation,
                "whyItMatters": why_it_matters,
                "recommendation": recommendation,
                "screenshotPath": clean_text(screenshot.get("screenshot_path")),
                "evidenceBundle": None,
                "aiDiscovered": True,
            }
        )
    return findings[:6]


def attach_ai_findings_to_axes(axes: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> None:
    if not findings:
        return
    axes_by_id = {axis["id"]: axis for axis in axes}
    for finding in findings:
        axis_id = clean_text(finding.get("axisId"))
        axis = axes_by_id.get(axis_id)
        if not axis:
            continue
        current = axis.get("painPoints") or []
        duplicate = any(clean_text(item.get("title")).lower() == clean_text(finding.get("title")).lower() for item in current)
        if duplicate:
            continue
        axis["painPoints"] = [finding, *current][:4]
        axis["evidence"] = dedupe_strings([finding.get("evidence")] + (axis.get("evidence") or []), limit=6)
        axis["signals"]["aiDiscoveredFindings"] = safe_int(axis["signals"].get("aiDiscoveredFindings")) + 1


def build_axis(axis: Dict[str, Any], flat_rows: List[Dict[str, Any]], profile: Dict[str, Any], vision_axes: Dict[str, Any]) -> Dict[str, Any]:
    rows = axis_rows(flat_rows, axis)
    rows_scored = axis_row_score(rows)
    heuristic = metric_score(axis["id"], profile)
    vscore = vision_axis_score((vision_axes or {}).get(axis["id"]))
    weighted = [(rows_scored["score"], 0.55), (heuristic, 0.35)] + ([(vscore, 0.10)] if vscore is not None else [])
    score = round(sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted), 1)
    failed = sorted([row for row in rows if row["status"] == "FALSE"], key=lambda row: (-row["confidence"], row["sheet"], row["row"]))
    passed = sorted([row for row in rows if row["status"] == "TRUE"], key=lambda row: (-row["confidence"], row["sheet"], row["row"]))
    pain_points = [finding_from_row(row, axis) for row in failed[:3]]
    strengths = [finding_from_row(row, axis) for row in passed[:2]]
    vision_observation = clean_text((((vision_axes or {}).get(axis["id"]) or {}).get("observation") or ""))
    summary = f"{axis['short_name']} scores {int(round(score))}/100 in this GTM view. Structured evidence surfaced {len(failed)} pain point(s) and {len(passed)} positive signal(s)." + (f" Vision review: {vision_observation}" if vision_observation else "")
    return {
        "id": axis["id"],
        "name": axis["short_name"],
        "shortName": axis["short_name"],
        "description": axis["description"],
        "score": int(round(score)),
        "severity": score_to_severity(score),
        "confidence": round(clamp(mean([rows_scored["confidence"], 0.65 if heuristic > 0 else 0.25, safe_float((((vision_axes or {}).get(axis["id"]) or {}).get("confidence")), 0.0)], default=0.4), 0.25, 0.95), 2),
        "summary": summary,
        "businessImpact": AXIS_IMPACT[axis["id"]],
        "painPoints": pain_points,
        "strengths": strengths,
        "opportunities": dedupe_strings(([f"Resolve '{item['title']}' on the main commercial pages first." for item in pain_points[:2]] + [f"Raise this axis on homepage and primary conversion journeys before broader refinements."]), limit=3),
        "evidence": dedupe_strings([item["evidence"] for item in pain_points + strengths if clean_text(item.get("evidence"))] + profile["messaging"]["heroHeadings"][:2] + profile["messaging"]["heroCtas"][:2], limit=6),
        "signals": {"rowScore": round(rows_scored["score"], 1), "heuristicScore": round(heuristic, 1), "visionScore": round(vscore, 1) if vscore is not None else None, "relevantChecks": len(rows)},
        "visionObservation": vision_observation,
    }


def top_priorities(axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for axis in axes:
        for point in axis.get("painPoints") or []:
            items.append({**point, "axisId": axis["id"], "axisName": axis["shortName"], "axisScore": axis["score"]})
    rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (rank.get(clean_text(item.get("severity")).lower(), 3), item.get("axisScore", 999), -safe_float(item.get("confidence"), 0.0)))
    deduped = []
    seen = set()
    for item in items:
        key = (
            clean_text(item.get("title")).lower(),
            clean_text(item.get("pageUrl") or item.get("pageName")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 8:
            break
    return deduped


def build_recommendations(priorities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    recommendations = []
    seen = set()
    for item in priorities:
        title = clean_text(item.get("title"))
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        severity = clean_text(item.get("severity")).lower()
        priority = "Critical" if severity == "high" else "High" if severity == "medium" else "Medium"
        page_name = clean_text(item.get("pageName")) or "Core journey"
        axis_name = clean_text(item.get("axisName"))
        recommendations.append(
            {
                "priority": priority,
                "title": title,
                "description": clean_text(item.get("recommendation")) or "Address this issue on the most commercial flow first.",
                "impact": f"Screen or area: {page_name}",
                "axis": axis_name,
            }
        )
        if len(recommendations) >= 5:
            break
    return recommendations


def build_payload(website_menu: Dict[str, Any], cleaned_data: Dict[str, Any], rendered_data: Dict[str, Any], checks_data: Dict[str, Any], results_data: Optional[Dict[str, Any]], include_vision: bool) -> Dict[str, Any]:
    flat_rows = flatten_checks(checks_data)
    profile = build_profile(website_menu, cleaned_data, rendered_data, checks_data, results_data)
    focus_screenshots = select_focus_screenshots(cleaned_data)
    scanned_pages = select_scanned_pages(cleaned_data)
    vision_limit = max(1, safe_int(os.getenv("GTM_VISION_MAX_SCREENSHOTS"), 12))
    vision_screenshots = select_vision_screenshots(scanned_pages, focus_screenshots, limit=vision_limit)
    vision = {"enabled": False, "model": "", "used_images": 0, "error": "Vision review disabled for this run.", "result": None}
    if include_vision:
        vision = run_gtm_vision_review(
            site_context={
                "site": profile["site"],
                "hero_headings": profile["messaging"]["heroHeadings"][:3],
                "hero_ctas": profile["messaging"]["heroCtas"][:4],
                "metrics": profile["metrics"],
                "sheet_scores": profile["sheetScores"],
                "visual_trust_review": {
                    "enabled": True,
                    "goal": "Detect GTM trust risks visible in screenshots, including AI-enhanced-looking team imagery, generic stock visuals, broken images, clipping, rendering artifacts, and credibility gaps.",
                    "preferred_axes": ["trust_accessibility", "visual_brand", "market_alignment"],
                },
            },
            screenshots=vision_screenshots,
        )
    vision_axes = ((vision.get("result") or {}).get("axes") or {}) if isinstance(vision, dict) else {}
    axes = [build_axis(axis, flat_rows, profile, vision_axes) for axis in AXIS_DEFINITIONS]
    ai_findings = ai_discovered_findings(vision, vision_screenshots, AXIS_DEFINITIONS)
    attach_ai_findings_to_axes(axes, ai_findings)
    overall_score = int(round(mean([axis["score"] for axis in axes], default=0.0)))
    strongest = max(axes, key=lambda axis: axis["score"], default=None)
    weakest = min(axes, key=lambda axis: axis["score"], default=None)
    priorities = top_priorities(axes)
    position = clean_text(((vision.get("result") or {}).get("market_positioning") or ""))
    if not position and profile["messaging"]["heroHeadings"]:
        lead = profile["messaging"]["heroHeadings"][0]
        cta = clean_text((profile["messaging"]["heroCtas"] or [""])[0])
        position = f"Lead with '{lead}' and support it with a clearer commercial CTA like '{cta}'." if cta else f"Lead with '{lead}' as the commercial narrative anchor."
    summary = f"{profile['site']['display_name']} scores {overall_score}/100 on the first GTM-oriented UX/UI audit pass."
    if weakest:
        summary += f" The biggest commercial risk sits in {weakest['shortName'].lower()} ({weakest['score']}/100)."
    if strongest:
        summary += f" The strongest current signal is {strongest['shortName'].lower()} ({strongest['score']}/100)."
    context = {
        "siteType": "Website audit",
        "pagesAudited": profile["counts"]["pages"],
        "topLevelNavigation": profile["counts"]["topLevelNavigation"],
        "auditAxes": len(AXIS_DEFINITIONS),
        "approach": "Shared crawl and extraction pipeline, then a GTM synthesis that keeps only the highest-impact UX/UI pain points.",
    }
    methodology = [
        {
            "step": "Context",
            "description": "We isolate the homepage, core conversion pages, and the strongest commercial story signals before scoring.",
        },
        {
            "step": "Axis Review",
            "description": "The product is reviewed through 7 GTM-oriented UX/UI axes with rule-based evidence from the detailed audit.",
        },
        {
            "step": "Prioritization",
            "description": "Only the highest-impact friction points are kept, then converted into sales-facing recommendations.",
        },
    ]
    return {
        "version": 1,
        "mode": "gtm",
        "generator": "src.gtm_audit.generate_gtm_audit",
        "site": profile["site"],
        "context": context,
        "methodology": methodology,
        "profile": profile,
        "focusScreenshots": focus_screenshots,
        "scannedPages": scanned_pages,
        "visionReview": vision,
        "aiDiscoveredFindings": ai_findings,
        "axes": axes,
        "executiveSummary": {"overallScore": overall_score, "strongestAxis": strongest, "weakestAxis": weakest, "summary": summary, "positioningHook": position, "topPriorities": priorities},
        "recommendations": build_recommendations(priorities),
        "artifacts": {
            "websiteMenu": str(website_menu.get("homepage") or ""),
            "cleanedPath": str(DEFAULT_CLEANED),
            "renderedPath": str(DEFAULT_RENDERED),
            "checksPath": str(DEFAULT_CHECKS),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the GTM-oriented 7-axis UX/UI audit.")
    parser.add_argument("--website-menu", default=str(DEFAULT_WEBSITE_MENU))
    parser.add_argument("--cleaned", default=str(DEFAULT_CLEANED))
    parser.add_argument("--rendered", default=str(DEFAULT_RENDERED))
    parser.add_argument("--checks", default=str(DEFAULT_CHECKS))
    parser.add_argument("--results", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--skip-vision", action="store_true")
    args = parser.parse_args()

    website_menu_path = to_path(args.website_menu, DEFAULT_WEBSITE_MENU)
    cleaned_path = to_path(args.cleaned, DEFAULT_CLEANED)
    rendered_path = to_path(args.rendered, DEFAULT_RENDERED)
    checks_path = to_path(args.checks, DEFAULT_CHECKS)
    output_path = to_path(args.output, DEFAULT_OUTPUT)
    results_path = to_path(args.results, RESULTS_DIR) if clean_text(args.results) else None

    for required in (website_menu_path, cleaned_path, rendered_path, checks_path):
        if not required.exists():
            raise FileNotFoundError(f"Required input file not found: {required}")

    results_data = load_latest_results() if results_path is None else load_json(results_path)
    payload = build_payload(
        load_json(website_menu_path),
        load_json(cleaned_path),
        load_json(rendered_path),
        load_json(checks_path),
        results_data,
        include_vision=not args.skip_vision,
    )
    payload["artifacts"]["cleanedPath"] = str(cleaned_path)
    payload["artifacts"]["renderedPath"] = str(rendered_path)
    payload["artifacts"]["checksPath"] = str(checks_path)
    save_json(output_path, payload)
    print(f"GTM audit written to: {output_path}")


if __name__ == "__main__":
    main()
