from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import re
from collections import Counter, defaultdict


# ============================================================
# Generic helpers
# ============================================================

def _safe_get(d: Dict[str, Any], *keys: str, default=None):
    current: Any = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _page_ref(page: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": page.get("name", ""),
        "url": page.get("url", ""),
        "finalUrl": page.get("finalUrl", page.get("url", "")),
    }


def _make_result(
    *,
    criterion: str,
    status: str,  # pass | warning | fail
    title: str,
    description: str,
    pages: List[Dict[str, Any]],
    severity: Optional[str] = None,
    recommendation: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    confidence: Optional[str] = None,
    method: Optional[List[str]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "category": "presentation",
        "criterion": criterion,
        "status": status,
        "severity": severity,
        "title": title,
        "description": description,
        "pages": pages,
        "recommendation": recommendation,
    }
    if evidence is not None:
        result["evidence"] = evidence
    if confidence is not None:
        result["confidence"] = confidence
    if method is not None:
        result["method"] = method
    return result


def _parse_px(value: Any) -> Optional[float]:
    text = _normalize_text(value).lower()
    if not text:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)px", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _parse_time_seconds(value: str) -> Optional[float]:
    text = _normalize_text(value).lower()
    if not text:
        return None
    matches = re.findall(r"(\d*\.?\d+)\s*(ms|s)\b", text)
    if not matches:
        return None
    values: List[float] = []
    for raw_num, unit in matches:
        num = float(raw_num)
        values.append(num / 1000.0 if unit == "ms" else num)
    return min(values) if values else None


def _contains_any(text: str, needles: List[str]) -> bool:
    lowered = _normalize_text(text).lower()
    return any(needle in lowered for needle in needles)


def _viewport_bucket(width: int) -> str:
    if width <= 480:
        return "mobile"
    if width <= 1024:
        return "tablet"
    return "desktop"


def _get_all_rendered_elements(rendered_page: Dict[str, Any]) -> List[Dict[str, Any]]:
    rendered_ui = rendered_page.get("renderedUi") or {}
    components = rendered_ui.get("components") or {}

    all_elements: List[Dict[str, Any]] = []
    for value in components.values():
        if isinstance(value, list):
            all_elements.extend(item for item in value if isinstance(item, dict))
    return all_elements


def _rendered_page_map(rendered_ui_data: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    mapping: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for page in rendered_ui_data.get("pages", []):
        mapping[(page.get("name", ""), page.get("url", ""))] = page
    return mapping


def _persona_page_map(person_a_data: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    mapping: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for page in person_a_data.get("pages", []):
        mapping[(page.get("name", ""), page.get("url", ""))] = page
    return mapping


def _page_type_hint(page: Dict[str, Any]) -> str:
    """
    Infer rough page type from URL/name/page meta.
    """
    name = _normalize_text(page.get("name")).lower()
    url = _normalize_text(page.get("url")).lower()
    final_url = _normalize_text(page.get("finalUrl")).lower()
    combined = f"{name} {url} {final_url}"

    if "contact" in combined:
        return "contact"
    if "/cart" in combined or "panier" in combined or "cart" in name:
        return "cart"
    if "/collections/" in combined or "/collections" in combined or "catalog" in combined:
        return "catalog"
    if name == "home" or final_url.rstrip("/") == url.rstrip("/") and final_url.endswith(".com/"):
        return "home"
    return "generic"


# ============================================================
# Criterion 1
# Most common devices, browsers and screen resolutions are supported
# Proxy: audited viewport coverage + stability
# ============================================================

def check_tested_viewport_support(person_a_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages = person_a_data.get("pages", [])
    checked_pages: List[Dict[str, Any]] = []
    viewport_profiles: Counter = Counter()
    viewport_buckets = set()
    overflow_pages: List[Dict[str, Any]] = []
    tolerance_px = 8

    for page in pages:
        viewport_width = int(_safe_get(page, "pageMeta", "data", "viewport", "width", default=0) or 0)
        viewport_height = int(_safe_get(page, "pageMeta", "data", "viewport", "height", default=0) or 0)
        scroll_width = int(_safe_get(page, "pageMeta", "data", "documentMetrics", "scrollWidth", default=0) or 0)

        if not viewport_width or not viewport_height:
            continue

        checked_pages.append(_page_ref(page))
        viewport_profiles[(viewport_width, viewport_height)] += 1
        viewport_buckets.add(_viewport_bucket(viewport_width))

        overflow_px = max(0, scroll_width - viewport_width)
        if overflow_px > tolerance_px:
            overflow_pages.append(
                {
                    **_page_ref(page),
                    "viewportWidth": viewport_width,
                    "viewportHeight": viewport_height,
                    "scrollWidth": scroll_width,
                    "overflowPx": overflow_px,
                }
            )

    if not checked_pages:
        return [
            _make_result(
                criterion="tested-viewport-support",
                status="warning",
                severity="warning",
                title="Viewport support could not be evaluated",
                description="No audited page viewport data was available.",
                pages=[],
                recommendation="Ensure page metadata extraction completes successfully before evaluating viewport support.",
                evidence={"checkedPages": 0},
                confidence="low",
                method=["viewport-profile-analysis"],
            )
        ]

    if overflow_pages:
        return [
            _make_result(
                criterion="tested-viewport-support",
                status="fail",
                severity="high",
                title="Audited viewport support issue detected",
                description=(
                    "At least one audited page shows layout instability in a tested viewport, which indicates that "
                    "responsive support is not reliable for all audited profiles."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in overflow_pages],
                recommendation=(
                    "Fix responsive overflow and layout stability issues before claiming support across the audited viewport profiles."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "viewportProfiles": [
                        {"width": w, "height": h, "count": c}
                        for (w, h), c in viewport_profiles.items()
                    ],
                    "viewportBuckets": sorted(viewport_buckets),
                    "overflowPages": overflow_pages,
                    "tolerancePx": tolerance_px,
                },
                confidence="high",
                method=["viewport-profile-analysis", "document-metrics"],
            )
        ]

    if len(viewport_profiles) == 1:
        only_profile = next(iter(viewport_profiles.keys()))
        return [
            _make_result(
                criterion="tested-viewport-support",
                status="warning",
                severity="warning",
                title="Viewport support is only partially evidenced",
                description=(
                    "The audited pages appear stable in the single tested viewport, but broader device and "
                    "screen-resolution support cannot be confirmed from one viewport profile alone."
                ),
                pages=checked_pages,
                recommendation="Run the audit on at least mobile, tablet, and desktop viewport profiles to validate support more reliably.",
                evidence={
                    "checkedPages": len(checked_pages),
                    "viewportProfiles": [
                        {
                            "width": only_profile[0],
                            "height": only_profile[1],
                            "count": viewport_profiles[only_profile],
                        }
                    ],
                    "viewportBuckets": sorted(viewport_buckets),
                },
                confidence="high",
                method=["viewport-profile-analysis"],
            )
        ]

    if len(viewport_buckets) < 3:
        return [
            _make_result(
                criterion="tested-viewport-support",
                status="warning",
                severity="warning",
                title="Viewport support is only partially covered",
                description=(
                    "The audited pages appear stable across the tested viewport profiles, but not all common "
                    "viewport categories were covered."
                ),
                pages=checked_pages,
                recommendation="Include at least one mobile, tablet, and desktop profile in the audit run.",
                evidence={
                    "checkedPages": len(checked_pages),
                    "viewportProfiles": [
                        {"width": w, "height": h, "count": c}
                        for (w, h), c in viewport_profiles.items()
                    ],
                    "viewportBuckets": sorted(viewport_buckets),
                },
                confidence="medium",
                method=["viewport-profile-analysis"],
            )
        ]

    return [
        _make_result(
            criterion="tested-viewport-support",
            status="pass",
            severity=None,
            title="Audited viewport profiles appear supported",
            description=(
                "The audited pages appear stable across the tested viewport profiles, with no strong signals "
                "of responsive breakage in the audited coverage."
            ),
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "viewportProfiles": [
                    {"width": w, "height": h, "count": c}
                    for (w, h), c in viewport_profiles.items()
                ],
                "viewportBuckets": sorted(viewport_buckets),
            },
            confidence="medium",
            method=["viewport-profile-analysis", "document-metrics"],
        )
    ]


# ============================================================
# Criterion 2
# There is no horizontal scrolling on any device, browser or screen resolution
# ============================================================

def check_horizontal_scrolling(person_a_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages = person_a_data.get("pages", [])
    tolerance_px = 8

    checked_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    for page in pages:
        viewport_width = int(_safe_get(page, "pageMeta", "data", "viewport", "width", default=0) or 0)
        scroll_width = int(_safe_get(page, "pageMeta", "data", "documentMetrics", "scrollWidth", default=0) or 0)

        if not viewport_width or not scroll_width:
            continue

        checked_pages.append(_page_ref(page))

        overflow_px = max(0, scroll_width - viewport_width)
        if overflow_px > tolerance_px:
            failing_pages.append(
                {
                    **_page_ref(page),
                    "viewportWidth": viewport_width,
                    "scrollWidth": scroll_width,
                    "overflowPx": overflow_px,
                }
            )

    if failing_pages:
        max_overflow = max(page["overflowPx"] for page in failing_pages)
        severity = "high" if max_overflow >= 40 else "medium"

        return [
            _make_result(
                criterion="no-horizontal-scrolling",
                status="fail",
                severity=severity,
                title="Horizontal overflow detected",
                description="One or more audited pages exceed the viewport width and may require horizontal scrolling.",
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Review fixed-width elements, large media, absolute positioning, and overflowing containers "
                    "to keep content inside the viewport."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "failingPages": len(failing_pages),
                    "maxOverflowPx": max_overflow,
                    "failures": failing_pages,
                    "tolerancePx": tolerance_px,
                },
                confidence="high",
                method=["document-metrics"],
            )
        ]

    return [
        _make_result(
            criterion="no-horizontal-scrolling",
            status="pass",
            severity=None,
            title="No horizontal scrolling detected",
            description="No horizontal overflow was detected on the audited pages for the tested viewport.",
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "failingPages": 0,
                "tolerancePx": tolerance_px,
            },
            confidence="high",
            method=["document-metrics"],
        )
    ]


# ============================================================
# Criterion 3
# Page layouts are consistent across the whole website
# Corrected: compare broad scaffold, not page-purpose details
# ============================================================

def _layout_signature(
    persona_page: Optional[Dict[str, Any]],
    rendered_page: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    utility_nav_count = 0
    footer_useful_count = 0
    has_search = False
    has_localization = False
    has_dialog = False
    has_nav_landmark = False
    has_form_landmark = False
    has_section_landmark = False

    if persona_page:
        utility_nav = _safe_get(persona_page, "navigation", "data", "utilityNav", default=[]) or []
        footer_useful = _safe_get(persona_page, "navigation", "data", "footerNavUseful", default=[]) or []
        utility_nav_count = len(utility_nav)
        footer_useful_count = len(footer_useful)

    if rendered_page:
        rendered_ui = rendered_page.get("renderedUi") or {}
        landmarks = _safe_get(rendered_ui, "structure", "landmarks", default=[]) or []

        for lm in landmarks:
            tag = _normalize_text(lm.get("tag")).lower()
            if tag == "nav":
                has_nav_landmark = True
            elif tag == "form":
                has_form_landmark = True
            elif tag == "section":
                has_section_landmark = True

        for el in _get_all_rendered_elements(rendered_page):
            ux_role = _normalize_text(el.get("uxRole")).lower()
            closest_landmark = el.get("closestLandmark") or {}
            landmark_role = _normalize_text(closest_landmark.get("role")).lower()
            landmark_class = _normalize_text(closest_landmark.get("className")).lower()
            landmark_xpath = _normalize_text(closest_landmark.get("xpathHint")).lower()

            if ux_role in {"search-trigger", "search-submit", "search-field"}:
                has_search = True
            if ux_role == "localization-control":
                has_localization = True
            if landmark_role == "dialog" or "modal" in landmark_class or "modal" in landmark_xpath:
                has_dialog = True

    return {
        "hasUtilityNav": utility_nav_count > 0,
        "hasFooterUsefulLinks": footer_useful_count > 0,
        "hasSearch": has_search,
        "hasLocalization": has_localization,
        "hasDialog": has_dialog,
        "hasNavLandmark": has_nav_landmark,
        "hasFormLandmark": has_form_landmark,
        "hasSectionLandmark": has_section_landmark,
    }


def _simplified_layout_signature(signature: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        signature["hasUtilityNav"],
        signature["hasFooterUsefulLinks"],
        signature["hasSearch"],
        signature["hasLocalization"],
        signature["hasDialog"],
        signature["hasNavLandmark"],
        signature["hasFormLandmark"],
        signature["hasSectionLandmark"],
    )


def check_layout_consistency(
    person_a_data: Dict[str, Any],
    rendered_ui_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    persona_map = _persona_page_map(person_a_data)
    rendered_map = _rendered_page_map(rendered_ui_data)

    checked_pages: List[Dict[str, Any]] = []
    signatures: List[Dict[str, Any]] = []

    keys = sorted(set(persona_map.keys()) | set(rendered_map.keys()))
    for key in keys:
        name, url = key
        persona_page = persona_map.get(key)
        rendered_page = rendered_map.get(key)

        if not persona_page and not rendered_page:
            continue

        page_ref = {
            "name": name,
            "url": url,
            "finalUrl": (rendered_page or persona_page or {}).get("finalUrl", url),
        }
        checked_pages.append(page_ref)

        signature = _layout_signature(persona_page, rendered_page)
        signatures.append(
            {
                **page_ref,
                "pageType": _page_type_hint(page_ref),
                "signature": signature,
                "simpleSignature": _simplified_layout_signature(signature),
            }
        )

    if not checked_pages:
        return [
            _make_result(
                criterion="layout-consistency",
                status="warning",
                severity="warning",
                title="Layout consistency could not be evaluated",
                description="No page layout evidence was available.",
                pages=[],
                recommendation="Ensure both HTML-derived and rendered UI extraction complete successfully.",
                evidence={"checkedPages": 0},
                confidence="low",
                method=["page-scaffold-comparison"],
            )
        ]

    signature_counter = Counter(item["simpleSignature"] for item in signatures)
    dominant_signature, dominant_count = signature_counter.most_common(1)[0]
    off_pattern_pages = [item for item in signatures if item["simpleSignature"] != dominant_signature]

    if not off_pattern_pages:
        return [
            _make_result(
                criterion="layout-consistency",
                status="pass",
                severity=None,
                title="Page layouts appear consistent",
                description=(
                    "The audited pages share a stable broad page scaffold with no strong sign of cross-page "
                    "layout inconsistency."
                ),
                pages=checked_pages,
                recommendation=None,
                evidence={
                    "checkedPages": len(checked_pages),
                    "dominantSignaturePages": dominant_count,
                    "offPatternPages": 0,
                },
                confidence="high",
                method=["page-scaffold-comparison"],
            )
        ]

    off_ratio = len(off_pattern_pages) / max(len(checked_pages), 1)

    # More conservative thresholds than before
    special_types = {"contact", "cart"}
    if len(off_pattern_pages) == 1 and off_pattern_pages[0].get("pageType") in special_types:
        return [
            _make_result(
                criterion="layout-consistency",
                status="pass",
                severity=None,
                title="Page layouts appear broadly consistent",
                description=(
                    "The audited pages share a stable broad scaffold. The only variation detected is on a page type "
                    "that commonly uses a specialized layout."
                ),
                pages=checked_pages,
                recommendation=None,
                evidence={
                    "checkedPages": len(checked_pages),
                    "dominantSignaturePages": dominant_count,
                    "specializedOutlier": off_pattern_pages,
                },
                confidence="high",
                method=["page-scaffold-comparison"],
            )
        ]
    
    if off_ratio <= 0.40:
        return [
            _make_result(
                criterion="layout-consistency",
                status="warning",
                severity="warning",
                title="Some page-layout variation detected",
                description=(
                    "Most audited pages share the same broad scaffold, but one or more pages deviate from "
                    "the dominant layout structure."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in off_pattern_pages],
                recommendation=(
                    "Review outlier pages and confirm that layout differences are intentional and aligned "
                    "with the site's page-template strategy."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "dominantSignaturePages": dominant_count,
                    "offPatternPages": len(off_pattern_pages),
                    "outliers": off_pattern_pages,
                },
                confidence="high",
                method=["page-scaffold-comparison"],
            )
        ]

    return [
        _make_result(
            criterion="layout-consistency",
            status="fail",
            severity="medium",
            title="Page layouts are inconsistent across the website",
            description=(
                "A large share of the audited pages deviate from the dominant broad page scaffold, "
                "which reduces predictability across the site."
            ),
            pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in off_pattern_pages],
            recommendation=(
                "Standardize header, footer, navigation, and broad page-template structure so that the site "
                "feels more predictable across key pages."
            ),
            evidence={
                "checkedPages": len(checked_pages),
                "dominantSignaturePages": dominant_count,
                "offPatternPages": len(off_pattern_pages),
                "outliers": off_pattern_pages,
            },
            confidence="high",
            method=["page-scaffold-comparison"],
        )
    ]


# ============================================================
# Criterion 4
# Negative space supports scanning and quickly determining what items are related
# ============================================================

def _spacing_quality_for_page(
    persona_page: Optional[Dict[str, Any]],
    rendered_page: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    scroll_height = int(_safe_get(persona_page or {}, "pageMeta", "data", "documentMetrics", "scrollHeight", default=0) or 0)
    spacing_values = _safe_get(rendered_page or {}, "renderedUi", "designSummary", "spacing", "values", default=[]) or []
    components = (_safe_get(rendered_page or {}, "renderedUi", "components", default={}) or {})

    parsed_spacing = sorted({round(px) for px in (_parse_px(v) for v in spacing_values) if px is not None})
    spacing_count = len(parsed_spacing)
    has_medium_spacing = any(v >= 16 for v in parsed_spacing)
    has_large_spacing = any(v >= 24 for v in parsed_spacing)

    visible_component_count = 0
    for value in components.values():
        if isinstance(value, list):
            visible_component_count += len(value)

    density = 0.0
    if scroll_height > 0:
        density = visible_component_count / max(scroll_height / 1000.0, 1.0)

    return {
        "scrollHeight": scroll_height,
        "parsedSpacing": parsed_spacing,
        "spacingCount": spacing_count,
        "hasMediumSpacing": has_medium_spacing,
        "hasLargeSpacing": has_large_spacing,
        "visibleComponentCount": visible_component_count,
        "componentDensityPer1000px": round(density, 2),
    }


def check_negative_space_scanning(
    person_a_data: Dict[str, Any],
    rendered_ui_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    persona_map = _persona_page_map(person_a_data)
    rendered_map = _rendered_page_map(rendered_ui_data)

    checked_pages: List[Dict[str, Any]] = []
    warning_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    keys = sorted(set(persona_map.keys()) | set(rendered_map.keys()))
    for key in keys:
        name, url = key
        persona_page = persona_map.get(key)
        rendered_page = rendered_map.get(key)

        if not persona_page and not rendered_page:
            continue

        page_ref = {
            "name": name,
            "url": url,
            "finalUrl": (rendered_page or persona_page or {}).get("finalUrl", url),
        }
        checked_pages.append(page_ref)

        quality = _spacing_quality_for_page(persona_page, rendered_page)

        spacing_count = quality["spacingCount"]
        has_large_spacing = quality["hasLargeSpacing"]
        density = quality["componentDensityPer1000px"]

        reasons: List[str] = []
        severity: Optional[str] = None

        if spacing_count == 0:
            reasons.append("no-spacing-evidence")
            severity = "warning"

        if density >= 70 and not has_large_spacing:
            reasons.append("high-visual-density-without-strong-separation")
            severity = "medium"

        elif density >= 55 and spacing_count <= 3:
            reasons.append("dense-layout-with-limited-spacing-range")
            severity = "warning"

        elif density >= 45 and not quality["hasMediumSpacing"]:
            reasons.append("limited-separation-signals")
            severity = "warning"

        if not reasons:
            continue

        item = {
            **page_ref,
            "severity": severity,
            "reasons": reasons,
            "quality": quality,
        }

        if severity == "medium":
            failing_pages.append(item)
        else:
            warning_pages.append(item)

    if failing_pages:
        return [
            _make_result(
                criterion="negative-space-scanning",
                status="fail",
                severity="medium",
                title="Negative space may be insufficient for scanning",
                description=(
                    "One or more pages appear visually dense without enough strong spacing separation, "
                    "which may reduce scanability and make relationships between items harder to perceive."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Increase separation between major content groups, ensure stronger large-spacing tokens are used, "
                    "and reduce visual crowding in dense sections."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "failingPages": len(failing_pages),
                    "failures": failing_pages,
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["spacing-token-analysis", "component-density-analysis"],
            )
        ]

    if warning_pages:
        return [
            _make_result(
                criterion="negative-space-scanning",
                status="warning",
                severity="warning",
                title="Negative space may need review",
                description=(
                    "Some pages show limited spacing evidence or moderate visual density patterns that may deserve "
                    "manual review for scanability."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in warning_pages],
                recommendation=(
                    "Review spacing between related groups and confirm that important content blocks are visually "
                    "separated enough to support quick scanning."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["spacing-token-analysis", "component-density-analysis"],
            )
        ]

    return [
        _make_result(
            criterion="negative-space-scanning",
            status="pass",
            severity=None,
            title="Negative space appears adequate for scanning",
            description="The audited pages show no strong spacing-density signals suggesting that scanability is being harmed.",
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "warningPages": 0,
                "failingPages": 0,
            },
            confidence="medium",
            method=["spacing-token-analysis", "component-density-analysis"],
        )
    ]


# ============================================================
# Criterion 5
# The order of information matches user expectation
# Proxy: broad structural expectation by page type
# ============================================================

def _order_expectation_for_page(persona_page: Dict[str, Any]) -> Dict[str, Any]:
    page_type = _page_type_hint(persona_page)
    headings_data = _safe_get(persona_page, "titlesAndHeadings", "data", default={}) or {}
    navigation_data = _safe_get(persona_page, "navigation", "data", default={}) or {}

    h1_count = len(headings_data.get("h1") or [])
    h2_count = len(headings_data.get("h2") or [])
    h3_count = len(headings_data.get("h3") or [])
    utility_nav_count = len(navigation_data.get("utilityNav") or [])
    breadcrumbs_count = len(navigation_data.get("breadcrumbs") or [])

    return {
        "pageType": page_type,
        "h1Count": h1_count,
        "h2Count": h2_count,
        "h3Count": h3_count,
        "utilityNavCount": utility_nav_count,
        "breadcrumbsCount": breadcrumbs_count,
    }


def check_information_order_expectation(person_a_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    checked_pages: List[Dict[str, Any]] = []
    warning_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    for page in person_a_data.get("pages", []):
        page_ref = _page_ref(page)
        checked_pages.append(page_ref)

        info = _order_expectation_for_page(page)
        page_type = info["pageType"]
        reasons: List[str] = []
        severity: Optional[str] = None

               # More realistic, less aggressive scoring
        strong_issues = 0
        light_issues = 0

        if page_type in {"contact", "cart", "catalog"} and info["h1Count"] == 0:
            reasons.append("missing-primary-page-heading")
            light_issues += 1

        elif page_type == "home" and info["h2Count"] == 0 and info["h3Count"] == 0:
            reasons.append("homepage-lacks-structured-content-headings")
            light_issues += 1

        if page_type == "catalog" and info["utilityNavCount"] == 0:
            reasons.append("catalog-page-lacks-top-level-navigation-context")
            strong_issues += 1

        if page_type == "catalog" and info["breadcrumbsCount"] == 0:
            # Useful, but not mandatory
            reasons.append("no-breadcrumb-trail-on-browse-page")
            light_issues += 1

        if strong_issues >= 2:
            severity = "medium"
        elif strong_issues >= 1 or light_issues >= 1:
            severity = "warning"
        else:
            severity = None

        if not reasons:
            continue

        item = {
            **page_ref,
            "severity": severity,
            "reasons": reasons,
            "pageType": page_type,
            "structure": info,
        }

        if severity == "medium":
            failing_pages.append(item)
        else:
            warning_pages.append(item)

    if failing_pages:
        return [
            _make_result(
                criterion="information-order-expectation",
                status="fail",
                severity="medium",
                title="Information order may not match user expectations",
                description=(
                    "One or more audited pages are missing structural cues that users typically expect for that page type, "
                    "which may weaken clarity and predictability."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Ensure each page type has a clear primary heading and expected structural cues, especially "
                    "for contact, catalog, and cart flows."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "failingPages": len(failing_pages),
                    "failures": failing_pages,
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["page-type-structure-analysis", "heading-analysis"],
            )
        ]

    if warning_pages:
        return [
            _make_result(
                criterion="information-order-expectation",
                status="warning",
                severity="warning",
                title="Information order may deserve review",
                description=(
                    "Some pages are missing structural cues that often support user expectation, but the signal "
                    "is not strong enough to classify as a failure."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in warning_pages],
                recommendation=(
                    "Review page-type structure and confirm that headings, navigation cues, and content ordering "
                    "match the purpose of each page."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["page-type-structure-analysis", "heading-analysis"],
            )
        ]

    return [
        _make_result(
            criterion="information-order-expectation",
            status="pass",
            severity=None,
            title="Information order appears broadly aligned with expectation",
            description=(
                "The audited pages do not show strong structural signals suggesting that the broad information order "
                "is misaligned with page purpose."
            ),
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "warningPages": 0,
                "failingPages": 0,
            },
            confidence="medium",
            method=["page-type-structure-analysis", "heading-analysis"],
        )
    ]


# ============================================================
# Criterion 6
# Modal or pop-up windows are used only when strict focus is necessary for the user
# ============================================================

_TASK_MODAL_KEYWORDS = [
    "search", "recherche", "cart", "panier", "login", "connexion", "account",
    "filter", "filtre", "country", "région", "region", "localization", "currency",
]

_NON_ESSENTIAL_MODAL_KEYWORDS = [
    "newsletter", "subscribe", "s'inscrire", "promo", "promotion", "discount",
    "offer", "offre", "sale", "welcome", "inscription",
]


def _dialog_evidence_for_page(rendered_page: Dict[str, Any]) -> Dict[str, Any]:
    rendered_ui = rendered_page.get("renderedUi") or {}
    landmarks = _safe_get(rendered_ui, "structure", "landmarks", default=[]) or []
    elements = _get_all_rendered_elements(rendered_page)

    visible_dialog_landmarks = []
    related_elements = []

    for lm in landmarks:
        if _normalize_text(lm.get("role")).lower() == "dialog":
            visible_dialog_landmarks.append(
                {
                    "tag": lm.get("tag"),
                    "role": lm.get("role"),
                    "text": lm.get("text"),
                    "className": lm.get("className"),
                    "xpathHint": lm.get("xpathHint"),
                }
            )

    for el in elements:
        closest_landmark = el.get("closestLandmark") or {}
        lm_role = _normalize_text(closest_landmark.get("role")).lower()
        lm_class = _normalize_text(closest_landmark.get("className")).lower()
        lm_xpath = _normalize_text(closest_landmark.get("xpathHint")).lower()

        if lm_role == "dialog" or "modal" in lm_class or "modal" in lm_xpath:
            related_elements.append(
                {
                    "xpathHint": el.get("xpathHint"),
                    "tag": el.get("tag"),
                    "uxRole": el.get("uxRole"),
                    "text": el.get("text"),
                    "label": el.get("label"),
                    "className": el.get("className"),
                }
            )

    combined_text = " ".join(
        [_normalize_text(item.get("text")) for item in visible_dialog_landmarks]
        + [_normalize_text(item.get("text")) + " " + _normalize_text(item.get("label")) for item in related_elements]
    ).lower()

    return {
        "visibleDialogLandmarks": visible_dialog_landmarks,
        "relatedElements": related_elements[:20],
        "combinedText": combined_text,
    }


def check_modal_focus_appropriateness(
    rendered_ui_data: Dict[str, Any],
    page_results: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    checked_pages: List[Dict[str, Any]] = []
    warning_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    runtime_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for page in page_results or []:
        runtime_map[(page.get("name", ""), page.get("originalUrl", ""))] = page

    for rendered_page in rendered_ui_data.get("pages", []):
        page_ref = _page_ref(rendered_page)
        checked_pages.append(page_ref)

        dialog_info = _dialog_evidence_for_page(rendered_page)
        runtime_page = runtime_map.get((page_ref["name"], page_ref["url"]))
        safe_interactions = (runtime_page or {}).get("safeInteractionResults") or []

        dialog_count = len(dialog_info["visibleDialogLandmarks"])
        popup_interactions = [item for item in safe_interactions if item.get("outcomeType") in {"popup", "dialog"}]
        combined_text = dialog_info["combinedText"]

        if dialog_count == 0 and not popup_interactions:
            continue

        looks_task_related = _contains_any(combined_text, _TASK_MODAL_KEYWORDS)
        looks_non_essential = _contains_any(combined_text, _NON_ESSENTIAL_MODAL_KEYWORDS)

        evidence = {
            "dialogInfo": dialog_info,
            "popupInteractions": popup_interactions[:10],
        }

        if looks_non_essential and not looks_task_related:
            failing_pages.append(
                {
                    **page_ref,
                    "severity": "medium",
                    "reason": "non-essential-modal-pattern",
                    "evidence": evidence,
                }
            )
            continue

        warning_pages.append(
            {
                **page_ref,
                "severity": "warning",
                "reason": "task-related-modal-pattern",
                "evidence": evidence,
            }
        )

    if failing_pages:
        return [
            _make_result(
                criterion="modal-focus-appropriateness",
                status="fail",
                severity="medium",
                title="Potentially unnecessary modal or popup pattern detected",
                description=(
                    "One or more pages appear to use modal or popup patterns for content that does not "
                    "clearly require strict user focus."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Reserve modal or popup patterns for essential tasks that require focused interaction, "
                    "and avoid using them for low-priority promotional or newsletter content."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "failingPages": len(failing_pages),
                    "failures": failing_pages,
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["dialog-structure-analysis", "interaction-outcome-analysis"],
            )
        ]

    if warning_pages:
        return [
            _make_result(
                criterion="modal-focus-appropriateness",
                status="warning",
                severity="warning",
                title="Modal or popup usage deserves review",
                description=(
                    "Modal or popup-like patterns are present, but they appear related to task-oriented workflows. "
                    "They should be reviewed to confirm they are justified and not overused."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in warning_pages],
                recommendation=(
                    "Confirm that modal usage is limited to search, account, cart, localization, or similarly "
                    "focus-dependent workflows."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["dialog-structure-analysis", "interaction-outcome-analysis"],
            )
        ]

    return [
        _make_result(
            criterion="modal-focus-appropriateness",
            status="pass",
            severity=None,
            title="No problematic modal or popup usage detected",
            description=(
                "No strong evidence was found suggesting that modal or popup patterns are being used "
                "inappropriately on the audited pages."
            ),
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "warningPages": 0,
                "failingPages": 0,
            },
            confidence="medium",
            method=["dialog-structure-analysis", "interaction-outcome-analysis"],
        )
    ]


# ============================================================
# Criterion 7
# There is no distracting blinking, flashing, or animation
# CSS layer
# ============================================================

def _classify_animation_risk(styles: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    animation = _normalize_text(styles.get("animation")).lower()
    transition = _normalize_text(styles.get("transition")).lower()
    opacity = _normalize_text(styles.get("opacity")).lower()
    transform = _normalize_text(styles.get("transform")).lower()
    filter_value = _normalize_text(styles.get("filter")).lower()

    if not animation or animation == "none":
        return None

    duration_s = _parse_time_seconds(animation)
    reasons: List[str] = []
    severity = "warning"

    is_infinite = "infinite" in animation
    has_transform_motion = _contains_any(animation, ["translate", "scale", "rotate", "slide"]) or (
        is_infinite and transform not in {"", "none"}
    )
    has_flash_words = _contains_any(animation, ["blink", "blinking", "flash", "flicker", "strobe"])
    has_marquee_words = _contains_any(animation, ["marquee", "scroll", "ticker"])
    has_opacity_motion = _contains_any(animation, ["opacity", "fade"]) and is_infinite
    has_filter_motion = _contains_any(animation, ["blur", "brightness", "contrast"]) or (
        is_infinite and filter_value not in {"", "none"}
    )

    if has_flash_words:
        reasons.append("flashing-or-blinking-animation")
        severity = "high"

    if is_infinite and duration_s is not None and duration_s <= 0.5:
        reasons.append("fast-infinite-animation")
        severity = "high"

    if has_opacity_motion:
        reasons.append("repeated-opacity-animation")
        severity = "high"

    if is_infinite and has_transform_motion:
        reasons.append("repeated-motion-animation")
        if severity != "high":
            severity = "medium"

    if is_infinite and has_marquee_words:
        reasons.append("continuous-scrolling-animation")
        if severity != "high":
            severity = "medium"

    if is_infinite and has_filter_motion:
        reasons.append("continuous-visual-effect-animation")
        if severity != "high":
            severity = "medium"

    if is_infinite and not reasons:
        reasons.append("generic-infinite-animation")
        if severity not in {"high", "medium"}:
            severity = "warning"

    if not reasons:
        return None

    return {
        "reasons": reasons,
        "severity": severity,
        "animation": animation,
        "transition": transition,
        "durationSeconds": duration_s,
        "opacity": opacity,
        "transform": transform,
        "filter": filter_value,
    }


def check_animation_distraction(rendered_ui_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages = rendered_ui_data.get("pages", [])
    checked_pages: List[Dict[str, Any]] = []
    warning_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    for page in pages:
        checked_pages.append(_page_ref(page))
        elements = _get_all_rendered_elements(page)

        suspicious_elements: List[Dict[str, Any]] = []

        for element in elements:
            styles = element.get("styles") or {}
            risk = _classify_animation_risk(styles)
            if not risk:
                continue

            suspicious_elements.append(
                {
                    "xpathHint": element.get("xpathHint"),
                    "tag": element.get("tag"),
                    "semanticType": element.get("semanticType"),
                    "componentVariant": element.get("componentVariant"),
                    "text": element.get("text"),
                    "label": element.get("label"),
                    "reasons": risk["reasons"],
                    "severity": risk["severity"],
                    "animation": risk["animation"],
                    "transition": risk["transition"],
                    "durationSeconds": risk["durationSeconds"],
                    "opacity": risk["opacity"],
                    "transform": risk["transform"],
                    "filter": risk["filter"],
                }
            )

        if suspicious_elements:
            highest = "high" if any(e["severity"] == "high" for e in suspicious_elements) else (
                "medium" if any(e["severity"] == "medium" for e in suspicious_elements) else "warning"
            )
            bucket = failing_pages if highest in {"high", "medium"} else warning_pages
            bucket.append(
                {
                    **_page_ref(page),
                    "severity": highest,
                    "suspiciousElements": suspicious_elements,
                }
            )

    if failing_pages:
        overall_severity = "high" if any(p["severity"] == "high" for p in failing_pages) else "medium"
        return [
            _make_result(
                criterion="no-distracting-animation",
                status="fail",
                severity=overall_severity,
                title="Potentially distracting animation detected",
                description=(
                    "One or more pages contain repeated or suspicious animation patterns that may distract users "
                    "or draw unnecessary attention."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Reduce repeated motion, avoid flashing effects, and remove unnecessary infinite animations "
                    "unless they are essential to comprehension or user feedback."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "failingPages": len(failing_pages),
                    "failures": failing_pages,
                },
                confidence="medium",
                method=["css-analysis"],
            )
        ]

    if warning_pages:
        return [
            _make_result(
                criterion="no-distracting-animation",
                status="warning",
                severity="warning",
                title="Possible attention-grabbing animation detected",
                description=(
                    "Some pages include repeated or infinite animation patterns that may attract attention, "
                    "but they are not clearly severe enough to classify as a failure."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in warning_pages],
                recommendation=(
                    "Review infinite or decorative animations and confirm they are necessary, subtle, and "
                    "supportive of the user experience."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["css-analysis"],
            )
        ]

    return [
        _make_result(
            criterion="no-distracting-animation",
            status="pass",
            severity=None,
            title="No distracting animation detected",
            description=(
                "No obvious suspicious blinking, flashing, or repeated attention-grabbing animation patterns "
                "were detected in the audited pages."
            ),
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "warningPages": 0,
                "failingPages": 0,
            },
            confidence="medium",
            method=["css-analysis"],
        )
    ]


# ============================================================
# Criterion 7 companion
# Runtime motion layer
# ============================================================

def check_animation_distraction_runtime(page_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    checked_pages: List[Dict[str, Any]] = []
    warning_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    for page in page_results:
        if page.get("status") != "success":
            continue

        checked_pages.append(
            {
                "name": page.get("name", ""),
                "url": page.get("originalUrl", ""),
                "finalUrl": page.get("finalUrl", page.get("originalUrl", "")),
            }
        )

        runtime_motion = page.get("runtimeMotion") or {}
        suspicious = runtime_motion.get("suspiciousElements") or []

        if suspicious:
            highest = "high" if any(item.get("severity") == "high" for item in suspicious) else (
                "medium" if any(item.get("severity") == "medium" for item in suspicious) else "warning"
            )
            bucket = failing_pages if highest in {"high", "medium"} else warning_pages
            bucket.append(
                {
                    "name": page.get("name", ""),
                    "url": page.get("originalUrl", ""),
                    "finalUrl": page.get("finalUrl", page.get("originalUrl", "")),
                    "severity": highest,
                    "suspiciousElements": suspicious,
                    "summary": runtime_motion.get("summary") or {},
                }
            )

    if failing_pages:
        overall_severity = "high" if any(p["severity"] == "high" for p in failing_pages) else "medium"
        return [
            _make_result(
                criterion="no-distracting-animation-runtime",
                status="fail",
                severity=overall_severity,
                title="Potentially distracting runtime motion detected",
                description=(
                    "One or more pages contain repeated movement or visual changes over time, which may "
                    "indicate distracting animation or auto-updating motion."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Reduce unnecessary auto-playing motion, repeated movement, and continuous visual changes "
                    "unless they are essential to user understanding."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "failingPages": len(failing_pages),
                    "failures": failing_pages,
                },
                confidence="medium",
                method=["runtime-observation"],
            )
        ]

    if warning_pages:
        return [
            _make_result(
                criterion="no-distracting-animation-runtime",
                status="warning",
                severity="warning",
                title="Possible runtime motion pattern detected",
                description=(
                    "Some pages contain repeated motion or visual changes over time that may deserve manual review, "
                    "but they are not clearly severe enough to classify as a failure."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in warning_pages],
                recommendation=(
                    "Review repeating motion patterns and verify they are subtle, justified, and not competing "
                    "with core content."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["runtime-observation"],
            )
        ]

    return [
        _make_result(
            criterion="no-distracting-animation-runtime",
            status="pass",
            severity=None,
            title="No distracting runtime motion detected",
            description="No suspicious repeated movement or runtime visual motion was detected during page observation.",
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "warningPages": 0,
                "failingPages": 0,
            },
            confidence="medium",
            method=["runtime-observation"],
        )
    ]


# ============================================================
# Criterion 8
# Visual styles are consistent throughout the application or site
# Corrected: compare only true families by role/context
# ============================================================

def _component_family_key(element: Dict[str, Any]) -> Optional[str]:
    semantic_type = _normalize_text(element.get("semanticType")).lower()
    ux_role = _normalize_text(element.get("uxRole")).lower()
    component_variant = _normalize_text(element.get("componentVariant")).lower()
    tag = _normalize_text(element.get("tag")).lower()
    xpath_hint = _normalize_text(element.get("xpathHint")).lower()
    text = _normalize_text(element.get("text")).lower()
    label = _normalize_text(element.get("label")).lower()
    class_name = _normalize_text(element.get("className")).lower()

    if ux_role in {"primary-cta", "secondary-cta"}:
        return f"cta::{ux_role}"

    if ux_role in {"search-trigger", "search-submit", "modal-close", "localization-control"}:
        return f"ux::{ux_role}"

    if semantic_type in {"button", "button-ghost"}:
        if "newsletter" in xpath_hint or "subscribe" in label or "s'inscrire" in label:
            return f"button::{semantic_type}::newsletter-action"
        if "search" in xpath_hint or ux_role in {"search-trigger", "search-submit"}:
            return f"button::{semantic_type}::search-action"
        if "facet" in xpath_hint or "filter" in xpath_hint:
            return f"button::{semantic_type}::filter-action"
        if "localization" in class_name or ux_role == "localization-control":
            return f"button::{semantic_type}::localization"
        if "modal" in class_name or ux_role == "modal-close":
            return f"button::{semantic_type}::modal-action"
        if "quick-add" in xpath_hint or "cart" in xpath_hint:
            return f"button::{semantic_type}::commerce-action"
        return f"button::{semantic_type}::generic"

    if semantic_type == "cta-link":
        return f"link::cta::{component_variant or 'default'}"

    if semantic_type == "link":
        if ux_role == "catalog-link":
            return "link::catalog"
        if "social" in class_name or any(x in text for x in ["facebook", "instagram", "tiktok"]):
            return "link::social"
        if "footer" in xpath_hint or text in {"3afsa", "commerce électronique propulsé par shopify"}:
            return "link::footer"
        if "underlined-link" in class_name or "reset" in text or "connectez-vous" in text:
            return "link::utility"
        return "link::content"

    if semantic_type == "nav-link":
        if ux_role:
            return f"nav-link::{ux_role}"
        if "menu" in class_name:
            return "nav-link::global-navigation"
        if "account" in class_name:
            return "nav-link::account-navigation"
        if "cart" in class_name:
            return "nav-link::cart-navigation"
        if "heading-link" in class_name:
            return "nav-link::brand-navigation"
        return "nav-link::generic"

    if semantic_type == "form":
        if "search" in xpath_hint:
            return "form::search"
        if "contact" in xpath_hint:
            return "form::contact"
        if "newsletter" in xpath_hint:
            return "form::newsletter"
        if "quick-add" in xpath_hint or "cart" in xpath_hint:
            return "form::commerce"
        return "form::generic"

    if semantic_type == "input":
        if ux_role == "search-field" or "search" in xpath_hint:
            return "input::search-field"
        if "newsletter" in xpath_hint or "newsletter" in class_name:
            return "input::newsletter-field"
        if "contactform" in xpath_hint or "contact" in xpath_hint:
            return "input::contact-form-field"
        if "filter-price" in xpath_hint or "facet" in xpath_hint or "filter" in xpath_hint:
            return "input::filter-field"
        if "cart" in xpath_hint or "quantity" in xpath_hint:
            return "input::commerce-field"
        return "input::generic-field"

    if semantic_type == "select":
        if "localization" in class_name or "country" in xpath_hint:
            return "select::localization"
        if "filter" in xpath_hint or "facet" in xpath_hint:
            return "select::filter"
        return "select::generic"

    if semantic_type == "textarea":
        if "contact" in xpath_hint:
            return "textarea::contact-form"
        return "textarea::generic"

    if semantic_type == "heading":
        if "footer" in xpath_hint:
            if "country" in xpath_hint or "région" in text or "region" in text or "pays" in text:
                return "heading::footer-localization-heading"
            if "subscribe" in text or "emails" in text or "newsletter" in xpath_hint:
                return "heading::footer-newsletter-heading"
            return "heading::footer-heading"
        if "card__heading" in class_name or "card" in xpath_hint:
            return "heading::card-title"
        if "facet" in xpath_hint or "filter" in xpath_hint:
            return "heading::filter-heading"
        if tag == "h1":
            return "heading::page-title"
        if tag == "h2":
            return "heading::section-heading::h2"
        if tag == "h3":
            return "heading::section-heading::h3"
        return f"heading::generic::{tag or 'heading'}"

    return None


def _style_variant_signature(element: Dict[str, Any]) -> str:
    tokens = element.get("tokens") or {}
    styles = element.get("styles") or {}

    font_size = _normalize_text(tokens.get("fontSize") or styles.get("fontSize"))
    font_weight = _normalize_text(tokens.get("fontWeight") or styles.get("fontWeight"))
    text_color = _normalize_text(tokens.get("textColor") or styles.get("color"))
    bg_color = _normalize_text(tokens.get("backgroundColor") or styles.get("backgroundColor"))
    padding = _normalize_text(tokens.get("padding"))
    radius = _normalize_text(tokens.get("radius") or styles.get("borderTopLeftRadius"))
    border = _normalize_text(tokens.get("border"))
    shadow = _normalize_text(tokens.get("shadow") or styles.get("boxShadow"))
    layout_mode = _normalize_text(element.get("layoutMode") or styles.get("display"))

    return " | ".join(
        [
            f"fs={font_size}",
            f"fw={font_weight}",
            f"tc={text_color}",
            f"bg={bg_color}",
            f"pad={padding}",
            f"rad={radius}",
            f"border={border}",
            f"shadow={shadow}",
            f"display={layout_mode}",
        ]
    )


def _collect_component_style_families(rendered_ui_data: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, List[Dict[str, Any]]]], List[Dict[str, Any]]]:
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    checked_pages: List[Dict[str, Any]] = []

    for page in rendered_ui_data.get("pages", []):
        checked_pages.append(_page_ref(page))
        for element in _get_all_rendered_elements(page):
            family = _component_family_key(element)
            if not family:
                continue

            signature = _style_variant_signature(element)
            grouped[family][signature].append(
                {
                    "page": page.get("name"),
                    "url": page.get("url"),
                    "finalUrl": page.get("finalUrl", page.get("url")),
                    "xpathHint": element.get("xpathHint"),
                    "tag": element.get("tag"),
                    "semanticType": element.get("semanticType"),
                    "uxRole": element.get("uxRole"),
                    "componentVariant": element.get("componentVariant"),
                    "text": element.get("text"),
                    "label": element.get("label"),
                }
            )

    return grouped, checked_pages


def check_visual_style_consistency(rendered_ui_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped, checked_pages = _collect_component_style_families(rendered_ui_data)

    if not checked_pages:
        return [
            _make_result(
                criterion="visual-style-consistency",
                status="warning",
                severity="warning",
                title="Visual style consistency could not be evaluated",
                description="No rendered pages were available for style consistency comparison.",
                pages=[],
                recommendation="Ensure rendered UI extraction completes successfully before running this check.",
                evidence={"checkedPages": 0},
                confidence="low",
                method=["component-family-comparison"],
            )
        ]

    family_issues: List[Dict[str, Any]] = []

    for family, variants in grouped.items():
        total_instances = sum(len(items) for items in variants.values())

        if total_instances < 3:
            continue

        pages_in_family = set()
        for occurrences in variants.values():
            for item in occurrences:
                if item.get("page"):
                    pages_in_family.add(item["page"])

        if len(pages_in_family) < 2 and total_instances < 6:
            continue

        variant_count = len(variants)
        dominant_count = max(len(items) for items in variants.values())
        dominant_ratio = dominant_count / total_instances if total_instances else 1.0

        severity = None
        if family in {"link::catalog", "heading::card-title", "nav-link::global-navigation"}:
            if variant_count >= 3 and dominant_ratio < 0.60:
                severity = "warning"
            else:
                severity = None
        else:
            if variant_count >= 4:
                severity = "high"
            elif variant_count == 3 and dominant_ratio < 0.75:
                severity = "medium"
            elif variant_count == 2 and dominant_ratio < 0.65:
                severity = "warning"
            elif variant_count == 3:
                severity = "warning"

        if not severity:
            continue

        example_variants = []
        involved_pages = set()

        for signature, occurrences in variants.items():
            pages_for_variant = sorted({item["page"] for item in occurrences if item.get("page")})
            involved_pages.update((item["page"], item["url"], item["finalUrl"]) for item in occurrences)
            example_variants.append(
                {
                    "signature": signature,
                    "count": len(occurrences),
                    "pages": pages_for_variant[:5],
                    "samples": occurrences[:3],
                }
            )

        family_issues.append(
            {
                "family": family,
                "severity": severity,
                "variantCount": variant_count,
                "totalInstances": total_instances,
                "dominantRatio": round(dominant_ratio, 3),
                "variants": sorted(example_variants, key=lambda x: x["count"], reverse=True),
                "pages": [
                    {"name": name, "url": url, "finalUrl": final_url}
                    for name, url, final_url in sorted(involved_pages)
                ],
            }
        )

    if not family_issues:
        return [
            _make_result(
                criterion="visual-style-consistency",
                status="pass",
                severity=None,
                title="Visual styles appear consistent",
                description=(
                    "Repeated component families appear visually consistent across the audited pages, "
                    "with no strong signs of design-system drift."
                ),
                pages=checked_pages,
                recommendation=None,
                evidence={
                    "checkedPages": len(checked_pages),
                    "familiesChecked": len(grouped),
                    "flaggedFamilies": 0,
                },
                confidence="high",
                method=["component-family-comparison", "rendered-style-signatures"],
            )
        ]

    high_families = [f for f in family_issues if f["severity"] == "high"]
    medium_families = [f for f in family_issues if f["severity"] == "medium"]
    warning_families = [f for f in family_issues if f["severity"] == "warning"]

    involved_pages_map = {}
    for family_issue in family_issues:
        for page in family_issue["pages"]:
            key = (page["name"], page["url"], page["finalUrl"])
            involved_pages_map[key] = page

    involved_pages = list(involved_pages_map.values())

    if high_families or medium_families:
        severity = "high" if high_families else "medium"
        return [
            _make_result(
                criterion="visual-style-consistency",
                status="fail",
                severity=severity,
                title="Visual style inconsistency detected across repeated components",
                description=(
                    "Repeated component families use too many visual variants across the audited pages, "
                    "which suggests design-system drift or inconsistent implementation."
                ),
                pages=involved_pages,
                recommendation=(
                    "Standardize repeated components such as buttons, links, form controls, headings, and CTA patterns "
                    "using shared design tokens and clearly defined visual variants."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "familiesChecked": len(grouped),
                    "flaggedFamilies": len(family_issues),
                    "highFamilies": len(high_families),
                    "mediumFamilies": len(medium_families),
                    "warningFamilies": len(warning_families),
                    "families": family_issues,
                },
                confidence="high",
                method=["component-family-comparison", "rendered-style-signatures"],
            )
        ]

    return [
        _make_result(
            criterion="visual-style-consistency",
            status="warning",
            severity="warning",
            title="Minor visual style variation detected across repeated components",
            description=(
                "Some repeated component families show moderate visual variation. This may be acceptable in part, "
                "but it should be reviewed to ensure variants are intentional and system-driven."
            ),
            pages=involved_pages,
            recommendation=(
                "Review component variants and confirm that differences are intentional, documented, "
                "and aligned with a shared design system."
            ),
            evidence={
                "checkedPages": len(checked_pages),
                "familiesChecked": len(grouped),
                "flaggedFamilies": len(family_issues),
                "warningFamilies": len(warning_families),
                "families": family_issues,
            },
            confidence="high",
            method=["component-family-comparison", "rendered-style-signatures"],
        )
    ]


# ============================================================
# Criterion 9
# Visual metaphors used will be understood by both casual and expert users
# Proxy: icon-only / ambiguous control clarity
# ============================================================

def check_visual_metaphor_clarity(rendered_ui_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    checked_pages: List[Dict[str, Any]] = []
    warning_pages: List[Dict[str, Any]] = []
    failing_pages: List[Dict[str, Any]] = []

    ambiguous_text_tokens = {"", ">", "»", "+", "-", "x", "×", "..."}

    for page in rendered_ui_data.get("pages", []):
        checked_pages.append(_page_ref(page))
        elements = _get_all_rendered_elements(page)

        ambiguous_controls: List[Dict[str, Any]] = []

        for el in elements:
            tag = _normalize_text(el.get("tag")).lower()
            semantic_type = _normalize_text(el.get("semanticType")).lower()
            ux_role = _normalize_text(el.get("uxRole")).lower()
            text = _normalize_text(el.get("text"))
            label = _normalize_text(el.get("label"))
            accessible_name = _normalize_text(el.get("accessibleName"))
            xpath_hint = _normalize_text(el.get("xpathHint")).lower()

            interactive = semantic_type in {
                "button", "button-ghost", "cta-link", "link", "nav-link", "input", "select"
            } or tag in {"button", "a", "summary"}

            if not interactive:
                continue

            class_name = _normalize_text(el.get("className")).lower()

            visible_text = text.strip()
            resolved_name = accessible_name or label or visible_text

            is_icon_like = visible_text.lower() in ambiguous_text_tokens or visible_text == ""
            has_no_clear_name = not resolved_name
            is_symbol_only = visible_text in ambiguous_text_tokens and not label and not accessible_name

            # Ignore known acceptable patterns with clear roles
            if ux_role in {"search-trigger", "search-submit", "modal-close"} and resolved_name:
                continue

            # Ignore probable logo / brand / home links in header
            is_probable_brand_link = (
                "header__heading-link" in xpath_hint
                or "header__heading-link" in class_name
                or "header__heading" in xpath_hint
                or "header__heading" in class_name
                or "logo" in xpath_hint
                or "logo" in class_name
                or "brand" in ux_role
            )

            if is_probable_brand_link:
                continue

            if (is_icon_like and has_no_clear_name) or is_symbol_only:
                ambiguous_controls.append(
                    {
                        "xpathHint": el.get("xpathHint"),
                        "tag": tag,
                        "semanticType": semantic_type,
                        "uxRole": ux_role,
                        "text": text,
                        "label": label,
                        "accessibleName": accessible_name,
                    }
                )

        if ambiguous_controls:
            severity = "medium" if len(ambiguous_controls) >= 3 else "warning"
            item = {
                **_page_ref(page),
                "severity": severity,
                "ambiguousControls": ambiguous_controls[:20],
            }
            if severity == "medium":
                failing_pages.append(item)
            else:
                warning_pages.append(item)

    if failing_pages:
        return [
            _make_result(
                criterion="visual-metaphor-clarity",
                status="fail",
                severity="medium",
                title="Some visual controls may rely on unclear metaphors",
                description=(
                    "One or more pages contain interactive controls whose meaning may not be obvious to users "
                    "because they rely on icons or symbols without clear labels."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in failing_pages],
                recommendation=(
                    "Provide clearer labels, accessible names, or supporting text for icon-only and symbolic controls."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "failingPages": len(failing_pages),
                    "failures": failing_pages,
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["interactive-control-clarity-analysis"],
            )
        ]

    if warning_pages:
        return [
            _make_result(
                criterion="visual-metaphor-clarity",
                status="warning",
                severity="warning",
                title="Some visual controls may deserve metaphor-clarity review",
                description=(
                    "A small number of controls may rely on icon-only or symbolic presentation without enough "
                    "clarifying text."
                ),
                pages=[{k: p[k] for k in ("name", "url", "finalUrl")} for p in warning_pages],
                recommendation=(
                    "Review icon-only and symbolic controls and confirm that their purpose is obvious without prior familiarity."
                ),
                evidence={
                    "checkedPages": len(checked_pages),
                    "warningPages": len(warning_pages),
                    "warnings": warning_pages,
                },
                confidence="medium",
                method=["interactive-control-clarity-analysis"],
            )
        ]

    return [
        _make_result(
            criterion="visual-metaphor-clarity",
            status="pass",
            severity=None,
            title="No strong metaphor-clarity issues detected",
            description=(
                "No strong evidence was found suggesting that core interactive controls rely on unclear visual metaphors."
            ),
            pages=checked_pages,
            recommendation=None,
            evidence={
                "checkedPages": len(checked_pages),
                "warningPages": 0,
                "failingPages": 0,
            },
            confidence="medium",
            method=["interactive-control-clarity-analysis"],
        )
    ]


# ============================================================
# Runner
# ============================================================

def run_presentation_checks(
    person_a_data: Dict[str, Any],
    rendered_ui_data: Optional[Dict[str, Any]] = None,
    page_results: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    # 1
    results += check_tested_viewport_support(person_a_data)

    # 2
    results += check_horizontal_scrolling(person_a_data)

    if rendered_ui_data:
        # 3
        results += check_layout_consistency(person_a_data, rendered_ui_data)

        # 4
        results += check_negative_space_scanning(person_a_data, rendered_ui_data)

        # 6
        results += check_modal_focus_appropriateness(rendered_ui_data, page_results)

        # 7
        results += check_animation_distraction(rendered_ui_data)

        # 8
        results += check_visual_style_consistency(rendered_ui_data)

        # 9
        results += check_visual_metaphor_clarity(rendered_ui_data)

    if page_results:
        # 7 companion
        results += check_animation_distraction_runtime(page_results)

    # 5
    results += check_information_order_expectation(person_a_data)

    return results