from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.audit.ai_review_client import AIReviewClient

from .common import AXIS_DEFINITIONS, clamp, clean_text, mean, score_to_severity
from .vision_client import run_gtm_vision_review


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
DEFAULT_OUTPUT = GENERATED_DIR / "screenshot_gtm_audit.json"


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


def _severity_score(severity: Any, confidence: Any) -> float:
    severity_text = clean_text(severity).lower()
    conf = clamp(_safe_float(confidence, 0.55), 0.1, 1.0)
    base = {"low": 80.0, "medium": 58.0, "high": 36.0}.get(severity_text, 58.0)
    return round(clamp(base + (conf - 0.5) * 12.0, 0.0, 100.0), 1)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _axis_score(axis_review: Dict[str, Any]) -> float:
    explicit = axis_review.get("score")
    if explicit is not None:
        score = _safe_float(explicit, -1.0)
        if 0.0 <= score <= 100.0:
            return round(score, 1)
    return _severity_score(axis_review.get("severity"), axis_review.get("confidence"))


def _screenshot_index(value: Any) -> Optional[int]:
    try:
        index = int(value)
    except Exception:
        return None
    return index if index >= 0 else None


def _screenshot_for_issue(issue: Dict[str, Any], screenshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    index = _screenshot_index(issue.get("screenshot_index"))
    if index is not None and index < len(screenshots):
        return screenshots[index]

    page_name = clean_text(issue.get("page_name")).lower()
    if page_name:
        for screenshot in screenshots:
            if clean_text(screenshot.get("page_name")).lower() == page_name:
                return screenshot

    return screenshots[0] if screenshots else {}


def _finding_from_issue(issue: Dict[str, Any], axis: Dict[str, Any], screenshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    screenshot = _screenshot_for_issue(issue, screenshots)
    title = clean_text(issue.get("title") or issue.get("criterion")) or f"{axis['short_name']} opportunity"
    page_name = clean_text(issue.get("page_name")) or clean_text(screenshot.get("page_name")) or "Uploaded screenshot"
    page_url = clean_text(issue.get("page_url")) or clean_text(screenshot.get("page_url"))
    evidence = clean_text(issue.get("evidence") or issue.get("reason"))
    recommendation = clean_text(issue.get("recommendation")) or "Review this screen and prioritize the change if it affects a primary commercial journey."
    why_it_matters = clean_text(issue.get("why_it_matters")) or (
        f"This matters because {AXIS_USER_IMPACT[axis['id']]}. In a GTM context, visible friction in {page_name} can reduce clarity, trust, or conversion readiness."
    )
    explanation = clean_text(issue.get("reason")) or evidence or "The screenshot review identified a potential GTM UX/UI issue on this screen."
    if title.lower() not in explanation.lower():
        explanation = f"On {page_name}, the review found that '{title}' may not be fully supported. {explanation}"

    return {
        "title": title,
        "axisId": axis["id"],
        "axisName": axis["short_name"],
        "pageName": page_name,
        "pageUrl": page_url,
        "sourceSheet": "Screenshot AI Review",
        "severity": clean_text(issue.get("severity")).lower() or "medium",
        "confidence": clamp(_safe_float(issue.get("confidence"), 0.55), 0.0, 1.0),
        "evidence": evidence[:240],
        "explanation": explanation,
        "whyItMatters": why_it_matters,
        "recommendation": recommendation,
        "screenshotPath": clean_text(screenshot.get("screenshot_path")),
        "visualRegion": issue.get("visual_region") if isinstance(issue.get("visual_region"), dict) else issue.get("visualRegion") if isinstance(issue.get("visualRegion"), dict) else None,
        "evidenceBundle": None,
        "aiDiscovered": True,
    }


def _vision_issues_for_axis(vision_result: Dict[str, Any], axis_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key in ("priority_issues", "criteria_discoveries", "visual_trust_findings"):
        for item in vision_result.get(key) or []:
            if isinstance(item, dict) and clean_text(item.get("axis_id")) == axis_id:
                items.append(item)
    return items


def _build_axes(vision_result: Dict[str, Any], screenshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    vision_axes = vision_result.get("axes") if isinstance(vision_result.get("axes"), dict) else {}
    axes: List[Dict[str, Any]] = []
    for axis in AXIS_DEFINITIONS:
        axis_review = vision_axes.get(axis["id"]) or {}
        score = _axis_score(axis_review)
        issues = _vision_issues_for_axis(vision_result, axis["id"])
        findings = [_finding_from_issue(issue, axis, screenshots) for issue in issues[:4]]
        strengths = [
            {
                "title": strength,
                "pageName": clean_text((screenshots[0] if screenshots else {}).get("page_name")) or "Uploaded screenshot",
                "pageUrl": "",
                "sourceSheet": "Screenshot AI Review",
                "severity": "low",
                "confidence": 0.6,
                "evidence": strength,
                "explanation": strength,
                "whyItMatters": f"This helps because {AXIS_USER_IMPACT[axis['id']]}.",
                "recommendation": "Preserve this strength while addressing higher-priority friction.",
                "screenshotPath": clean_text((screenshots[0] if screenshots else {}).get("screenshot_path")),
                "evidenceBundle": None,
            }
            for strength in (vision_result.get("strengths") or [])[:1]
            if clean_text(strength)
        ]
        axes.append(
            {
                "id": axis["id"],
                "name": axis["short_name"],
                "shortName": axis["short_name"],
                "description": axis["description"],
                "score": int(round(score)),
                "severity": score_to_severity(score),
                "confidence": round(clamp(_safe_float(axis_review.get("confidence"), 0.45), 0.25, 0.95), 2),
                "summary": clean_text(axis_review.get("observation")) or f"{axis['short_name']} was reviewed from the uploaded screenshots.",
                "businessImpact": AXIS_IMPACT[axis["id"]],
                "painPoints": findings,
                "strengths": strengths,
                "opportunities": [finding["recommendation"] for finding in findings[:3]] or [f"Use the screenshot evidence to refine {axis['short_name'].lower()} before launch."],
                "evidence": [finding["evidence"] for finding in findings if clean_text(finding.get("evidence"))][:6],
                "signals": {
                    "rowScore": None,
                    "heuristicScore": None,
                    "visionScore": round(score, 1),
                    "relevantChecks": len(findings),
                    "aiDiscoveredFindings": len(findings),
                },
                "visionObservation": clean_text(axis_review.get("observation")),
            }
        )
    return axes


def _top_priorities(axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for axis in axes:
        for point in axis.get("painPoints") or []:
            items.append({**point, "axisId": axis["id"], "axisName": axis["shortName"], "axisScore": axis["score"]})
    rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (rank.get(clean_text(item.get("severity")).lower(), 3), item.get("axisScore", 999), -_safe_float(item.get("confidence"), 0.0)))
    return items[:8]


def _recommendations(priorities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for item in priorities:
        title = clean_text(item.get("title"))
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        severity = clean_text(item.get("severity")).lower()
        out.append(
            {
                "priority": "Critical" if severity == "high" else "High" if severity == "medium" else "Medium",
                "title": title,
                "description": clean_text(item.get("recommendation")) or "Address this issue on the most commercial screenshot first.",
                "impact": f"Screen or area: {clean_text(item.get('pageName')) or 'Uploaded screenshot'}",
                "axis": clean_text(item.get("axisName")),
            }
        )
        if len(out) >= 5:
            break
    return out


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _screenshot_metadata(paths: List[Path], names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out = []
    for index, path in enumerate(paths, start=1):
        width, height = _image_dimensions(path)
        custom_name = clean_text((names or [])[index - 1] if names and index - 1 < len(names) else "")
        fallback_name = f"Screenshot {index}"
        display_name = custom_name or fallback_name
        title = custom_name or path.stem.replace("_", " ").replace("-", " ").strip() or fallback_name
        out.append(
            {
                "page_name": display_name,
                "page_url": "",
                "title": title,
                "source_type": "uploaded_screenshot",
                "file_name": path.name,
                "image_width": width,
                "image_height": height,
                "aspect_ratio": round(width / height, 3) if width and height else None,
                "reason": "User-uploaded screenshot for GTM UX/UI review",
                "screenshot_path": str(path),
            }
        )
    return out


def _text_refinement_enabled() -> bool:
    raw = clean_text(os.getenv("SCREENSHOT_AUDIT_LLM_REFINEMENT", "1")).lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _visual_region(issue: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("visual_region", "visualRegion", "region", "bounding_box", "boundingBox"):
        value = issue.get(key)
        if isinstance(value, dict):
            return value
    return None


def _copy_missing_issue_context(refined: Dict[str, Any], original: Dict[str, Any]) -> Dict[str, Any]:
    issue_keys = ("priority_issues", "criteria_discoveries", "visual_trust_findings")
    source_by_position: Dict[tuple[str, int], Dict[str, Any]] = {}
    source_by_context: Dict[tuple[str, int], List[Dict[str, Any]]] = {}

    for key in issue_keys:
        for index, issue in enumerate(original.get(key) or []):
            if not isinstance(issue, dict):
                continue
            screenshot_index = _screenshot_index(issue.get("screenshot_index"))
            context_key = (clean_text(issue.get("axis_id")), screenshot_index if screenshot_index is not None else -1)
            source_by_position[(key, index)] = issue
            source_by_context.setdefault(context_key, []).append(issue)

    for key in issue_keys:
        for index, issue in enumerate(refined.get(key) or []):
            if not isinstance(issue, dict):
                continue
            source_issue = source_by_position.get((key, index))
            if not source_issue:
                screenshot_index = _screenshot_index(issue.get("screenshot_index"))
                context_key = (clean_text(issue.get("axis_id")), screenshot_index if screenshot_index is not None else -1)
                matches = source_by_context.get(context_key) or []
                source_issue = matches[0] if matches else None
            if not source_issue:
                continue
            if _visual_region(source_issue) and not _visual_region(issue):
                issue["visual_region"] = _visual_region(source_issue)
            if _screenshot_index(issue.get("screenshot_index")) is None and _screenshot_index(source_issue.get("screenshot_index")) is not None:
                issue["screenshot_index"] = _screenshot_index(source_issue.get("screenshot_index"))
            if not clean_text(issue.get("page_name")) and clean_text(source_issue.get("page_name")):
                issue["page_name"] = clean_text(source_issue.get("page_name"))
            if not clean_text(issue.get("page_url")) and clean_text(source_issue.get("page_url")):
                issue["page_url"] = clean_text(source_issue.get("page_url"))
            if not clean_text(issue.get("title")) and clean_text(source_issue.get("title")):
                issue["title"] = clean_text(source_issue.get("title"))
            if not clean_text(issue.get("criterion")) and clean_text(source_issue.get("criterion")):
                issue["criterion"] = clean_text(source_issue.get("criterion"))

    return refined


def _run_text_refinement(
    *,
    site_context: Dict[str, Any],
    screenshots: List[Dict[str, Any]],
    vision_result: Dict[str, Any],
) -> Dict[str, Any]:
    if not _text_refinement_enabled():
        return {"enabled": False, "error": "Disabled by SCREENSHOT_AUDIT_LLM_REFINEMENT.", "result": None}
    if not vision_result:
        return {"enabled": False, "error": "No VLM result available to refine.", "result": None}

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
        "priority_issues": "array of the strongest 3-8 evidence-grounded issues, preserving screenshot_index and visual_region when present",
        "criteria_discoveries": "array of additional evidence-grounded issues, preserving screenshot_index and visual_region when present",
        "visual_trust_findings": "array of visual credibility findings, preserving screenshot_index and visual_region when present",
        "strengths": ["string"],
        "market_positioning": "string",
    }
    system_prompt = """
You are a senior GTM UX/UI audit editor. You refine a VLM screenshot audit into sharper, client-ready JSON.
You cannot see the images now, so do not invent visual facts. Use only the provided VLM result and screenshot metadata.
Preserve screenshot_index and visual_region values whenever they exist. If a visual_region is missing, leave it missing.
Improve prioritization, axis scoring, wording, why-it-matters reasoning, and recommendations.
Return strict JSON only matching the requested schema.
""".strip()
    user_payload = {
        "required_schema": schema,
        "axis_definitions": AXIS_DEFINITIONS,
        "site_context": site_context,
        "screenshots": [
            {key: value for key, value in screenshot.items() if key != "screenshot_path"}
            for screenshot in screenshots
        ],
        "vlm_result_to_refine": vision_result,
        "quality_rules": [
            "Keep only issues that cite visible evidence or clear VLM-observed UI signals.",
            "Prefer 3 to 8 priority issues across the whole set, ranked by commercial GTM impact.",
            "Avoid generic statements such as 'improve hierarchy' unless tied to a visible element, label, CTA, or section.",
            "Every recommendation should be a concrete next action, not a broad principle.",
            "Use lower confidence when the screenshot lacks enough context to judge the axis.",
        ],
    }

    try:
        client = AIReviewClient()
        client.config.timeout = min(client.config.timeout, 90)
        refined = client.review_json(system_prompt=system_prompt, user_payload=user_payload, temperature=0.05)
        if not isinstance(refined, dict):
            raise ValueError("Text refinement did not return a JSON object.")
        return {
            "enabled": True,
            "model": client.config.model,
            "backend": client.config.backend,
            "error": "",
            "result": _copy_missing_issue_context(refined, vision_result),
        }
    except Exception as error:
        return {
            "enabled": False,
            "error": str(error),
            "result": None,
        }


def build_payload(screenshot_paths: List[Path], site_name: str = "Screenshot Audit", screenshot_names: Optional[List[str]] = None) -> Dict[str, Any]:
    screenshots = _screenshot_metadata(screenshot_paths, screenshot_names)
    site_context = {
        "site": {
            "homepage": "",
            "domain": "uploaded-screenshots",
            "display_name": clean_text(site_name) or "Screenshot Audit",
            "language": "",
        },
        "counts": {
            "pages": len(screenshots),
            "topLevelNavigation": 0,
            "navigationItems": 0,
        },
        "source": "uploaded screenshots",
        "visual_review_goal": "Evaluate uploaded UI screenshots against the same seven GTM UX/UI audit axes used by the website audit mode.",
    }
    vision = run_gtm_vision_review(site_context=site_context, screenshots=screenshots)
    vision_result = vision.get("result") if isinstance(vision.get("result"), dict) else {}
    text_refinement = _run_text_refinement(
        site_context=site_context,
        screenshots=screenshots,
        vision_result=vision_result,
    )
    if isinstance(text_refinement.get("result"), dict):
        vision_result = text_refinement["result"]
        vision["result"] = vision_result
    vision["textRefinement"] = {
        "enabled": bool(text_refinement.get("enabled")),
        "model": clean_text(text_refinement.get("model")),
        "backend": clean_text(text_refinement.get("backend")),
        "error": clean_text(text_refinement.get("error")),
    }
    axes = _build_axes(vision_result, screenshots)
    overall_score = int(round(mean([axis["score"] for axis in axes], default=55.0)))
    strongest = max(axes, key=lambda axis: axis["score"], default=None)
    weakest = min(axes, key=lambda axis: axis["score"], default=None)
    priorities = _top_priorities(axes)
    summary = f"{clean_text(site_name) or 'Screenshot audit'} scores {overall_score}/100 on the screenshot-based GTM UX/UI audit pass."
    if weakest:
        summary += f" The biggest commercial risk sits in {weakest['shortName'].lower()} ({weakest['score']}/100)."
    if vision.get("error"):
        summary += f" Vision review note: {vision['error']}"

    return {
        "version": 1,
        "mode": "screenshot",
        "generator": "src.gtm_audit.generate_screenshot_gtm_audit",
        "site": site_context["site"],
        "context": {
            "siteType": "Screenshot audit",
            "pagesAudited": len(screenshots),
            "topLevelNavigation": "N/A",
            "auditAxes": len(AXIS_DEFINITIONS),
            "approach": "User-uploaded screenshots reviewed by the multimodal GTM synthesis layer against the same seven UX/UI audit axes.",
        },
        "methodology": [
            {"step": "Upload", "description": "Screenshots are uploaded directly by the user and preserved as report evidence."},
            {"step": "Vision Review", "description": "A multimodal model reviews visible UI evidence against seven GTM UX/UI axes."},
            {"step": "LLM Synthesis", "description": "A text model refines the visual review into sharper GTM priorities when available, without inventing new visual evidence."},
            {"step": "Prioritization", "description": "The highest-impact issues are translated into sales-facing recommendations and a static report."},
        ],
        "profile": site_context,
        "focusScreenshots": screenshots[:3],
        "scannedPages": screenshots,
        "visionReview": vision,
        "aiDiscoveredFindings": priorities,
        "axes": axes,
        "executiveSummary": {
            "overallScore": overall_score,
            "strongestAxis": strongest,
            "weakestAxis": weakest,
            "summary": summary,
            "positioningHook": clean_text(vision_result.get("market_positioning")) or "Use the uploaded screenshots to clarify the product story, trust signals, and conversion path before launch.",
            "topPriorities": priorities,
        },
        "recommendations": _recommendations(priorities),
        "artifacts": {
            "websiteMenu": "",
            "cleanedPath": "",
            "renderedPath": "",
            "checksPath": "",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a GTM-oriented audit from uploaded screenshots.")
    parser.add_argument("--screenshots", nargs="+", required=True, help="One or more screenshot image paths.")
    parser.add_argument("--screenshot-names", nargs="*", default=[], help="Optional display names matching the uploaded screenshots.")
    parser.add_argument("--screenshot-names-json", default="", help="Optional JSON array of display names matching the uploaded screenshots.")
    parser.add_argument("--site-name", default="Screenshot Audit")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    screenshot_paths = [to_path(raw, Path(raw)) for raw in args.screenshots]
    missing = [str(path) for path in screenshot_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Screenshot file(s) not found: {', '.join(missing)}")

    screenshot_names = args.screenshot_names
    if clean_text(args.screenshot_names_json):
        try:
            parsed_names = json.loads(args.screenshot_names_json)
            if isinstance(parsed_names, list):
                screenshot_names = [clean_text(item) for item in parsed_names]
        except Exception:
            screenshot_names = args.screenshot_names

    payload = build_payload(screenshot_paths, site_name=args.site_name, screenshot_names=screenshot_names)
    output_path = to_path(args.output, DEFAULT_OUTPUT)
    save_json(output_path, payload)
    print(f"Screenshot GTM audit written to: {output_path}")


if __name__ == "__main__":
    main()
