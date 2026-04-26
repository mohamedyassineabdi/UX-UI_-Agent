from __future__ import annotations

from typing import List

from .common import (
    AuditContext,
    CheckResult,
    FALSE,
    NA,
    TRUE,
    average,
    build_evidence_bundle,
    clean_text,
    comparable_label,
    component_evidence_target,
    looks_like_locale_picker,
    make_result,
    normalize_text,
    page_title_core,
    region_evidence_target,
)

SHEET = "Navigation"


def run(context: AuditContext) -> List[CheckResult]:
    results: List[CheckResult] = []

    nav_components = context.nav_components()
    nav_labels = context.meaningful_navigation_labels()
    active_labels = context.active_navigation_labels()
    breadcrumb_total = sum(len(page.html.get("navigation", {}).get("data", {}).get("breadcrumbs", [])) for page in context.pages)
    search_all_pages = context.has_search_on_every_page()
    search_widths = context.search_input_widths()

    location_signal = bool(active_labels or breadcrumb_total)
    results.append(make_result(
        SHEET, 4,
        "Navigation, page titling and breadcrumbs tell the user where she is, how she got here and where she can go.",
        TRUE if nav_components and context.pages and location_signal else FALSE if context.pages else NA,
        0.70,
        f"Rendered navigation components found on {len(nav_components)} page instances; active-location labels={len(active_labels)}; breadcrumbs found={breadcrumb_total}.",
        evidence=(active_labels[:4] or context.page_names()),
        decision_basis="direct",
    ))

    results.append(make_result(
        SHEET, 5,
        "The current location is clearly indicated (e.g. breadcrumb, highlighted menu item).",
        TRUE if location_signal else FALSE if context.pages else NA,
        0.78,
        "Current-location evidence is taken from extracted active menu items and breadcrumbs, excluding locale picker items.",
        evidence=active_labels[:6],
        decision_basis="direct",
    ))

    component_scores = [metric.get("componentConsistency") for metric in context.consistency_metrics() if isinstance(metric.get("componentConsistency"), (int, float))]
    avg_consistency = average(component_scores)
    results.append(make_result(
        SHEET, 6,
        "Navigation location and styling is consistent on every page.",
        TRUE if avg_consistency >= 80 else FALSE if component_scores else NA,
        0.83,
        f"Average component consistency score is {avg_consistency:.1f}.",
        evidence=[str(value) for value in component_scores[:6]],
        decision_basis="direct",
    ))

    page_nav_sets = []
    for page in context.pages:
        labels = []
        nav = page.html.get("navigation", {}).get("data", {})
        for key in ("primaryNav", "utilityNav", "footerNavUseful"):
            for item in nav.get(key, []):
                text = clean_text(item.get("text") or item.get("label"))
                if text and comparable_label(text):
                    labels.append(comparable_label(text))
        page_nav_sets.append(set(labels))
    shared_core = set.intersection(*page_nav_sets) if page_nav_sets else set()
    results.append(make_result(
        SHEET, 7,
        "Navigation menus are persistent (on every screen) and consistent (items don’t disappear/reappear).",
        TRUE if {"home", "catalog", "contact"}.issubset(shared_core) else FALSE if page_nav_sets else NA,
        0.82,
        f"Shared navigation labels across audited pages: {sorted(shared_core)[:8]}.",
        evidence=sorted(shared_core)[:8],
        decision_basis="direct",
    ))

    unclear_nav = []
    for label in nav_labels:
        norm = normalize_text(label)
        if len(label) <= 1 or norm in {"3afsa"}:
            unclear_nav.append(label)
        if "shopify" in norm:
            unclear_nav.append(label)
    results.append(make_result(
        SHEET, 8,
        "Navigation labels are clear and obvious, readily understood by the target audience.",
        TRUE if len(unclear_nav) == 0 and nav_labels else FALSE if nav_labels else NA,
        0.78,
        f"Meaningful navigation labels reviewed after excluding locale-picker and Shopify noise; unclear labels detected: {len(unclear_nav)}.",
        evidence=(unclear_nav[:6] or nav_labels[:6]),
        decision_basis="direct",
    ))

    nav_norm = {normalize_text(label) for label in nav_labels}
    has_browse = any(label in nav_norm for label in {"catalog", "contact", "home"})
    has_search = search_all_pages
    has_contact = context.has_contact_or_help_path()
    results.append(make_result(
        SHEET, 9,
        "Navigation structure addresses common user goals.",
        TRUE if has_browse and has_search and has_contact else FALSE if nav_labels else NA,
        0.74,
        f"Signals: browse={has_browse}, search={has_search}, contact={has_contact}.",
        evidence=nav_labels[:8],
        decision_basis="direct",
    ))

    results.append(make_result(
        SHEET, 10,
        "Navigation is flexible, allowing users to navigate by their desired means (e.g. searching, browse by type, browse by name, most recent etc.).",
        TRUE if has_search and has_browse else FALSE if context.pages else NA,
        0.78,
        "This relies on actual site search detection plus meaningful browse/navigation labels, not localization controls.",
        evidence=nav_labels[:6] + [clean_text(field.get("label") or field.get("placeholder") or "") for field in context.site_search_inputs()[:2]],
        decision_basis="direct",
    ))

    sorted_like = False
    if len(nav_labels) >= 4:
        nav_norm_list = [normalize_text(label) for label in nav_labels[:10]]
        sorted_like = nav_norm_list == sorted(nav_norm_list)
    results.append(make_result(
        SHEET, 11,
        "Alphabetical A-Z sorting is used only when there are no better alternatives, such as grouping items into descriptive, related groups.",
        TRUE if not sorted_like else NA,
        0.56,
        f"Meaningful navigation/category label set looks alphabetically sorted={sorted_like}.",
        evidence=nav_labels[:10],
        decision_basis="proxy",
    ))

    search_bundle = None
    if search_all_pages:
        first_search_input = context.site_search_inputs()[0] if context.site_search_inputs() else None
        if first_search_input:
            search_bundle = build_evidence_bundle(
                criterion="Search is available on every page, not just the homepage.",
                source="deterministic_check",
                target=component_evidence_target(
                    first_search_input,
                    reason="Detected site-search input on an audited page.",
                    issue_kind="presence",
                ),
                notes="Directly grounded in extracted search-input geometry.",
            )
    else:
        for page in context.pages:
            inputs = page.rendered.get("renderedUi", {}).get("components", {}).get("inputs", [])
            page_has_site_search = False
            for field in inputs:
                field_type = normalize_text(field.get("type"))
                if field_type != "search":
                    continue
                name = normalize_text(field.get("name"))
                fid = normalize_text(field.get("id"))
                cls = normalize_text(field.get("className"))
                label = clean_text(field.get("label") or field.get("placeholder") or field.get("name") or "")
                if (name == "q" or "search" in fid or "search" in cls) and not label:
                    page_has_site_search = True
                    break
                if looks_like_locale_picker(label):
                    continue
                if name == "q" or "search" in fid or "search" in cls:
                    page_has_site_search = True
                    break
            if page_has_site_search:
                continue

            provenance = context._page_provenance(page)
            nav_region_components = [
                component
                for component in context.nav_components()
                if clean_text(component.get("_pageUrl")) == provenance["_pageUrl"]
            ]
            search_bundle = build_evidence_bundle(
                criterion="Search is available on every page, not just the homepage.",
                source="deterministic_check",
                target=region_evidence_target(
                    nav_region_components,
                    page_name=provenance["_pageName"],
                    page_url=provenance["_pageUrl"],
                    final_url=provenance["_finalUrl"],
                    screenshot_path=provenance["_screenshotPath"],
                    reason="Header/navigation area highlighted because no site-search input was detected on this page.",
                    issue_kind="absence",
                    component_type="navigation-region",
                ),
                notes="For missing-search issues, the evidence target is the main navigation/header zone rather than an unrelated component.",
            )
            break

    results.append(make_result(
        SHEET, 13,
        "Search is available on every page, not just the homepage.",
        TRUE if search_all_pages else FALSE if context.pages else NA,
        0.94,
        f"Actual site-search inputs detected on every page={search_all_pages}.",
        evidence=context.page_names(),
        decision_basis="direct",
        evidence_bundle=search_bundle,
    ))

    avg_search_width = average(search_widths)
    results.append(make_result(
        SHEET, 14,
        "Search box is wide enough so that users can see what they’ve typed.",
        TRUE if search_widths and avg_search_width >= 180 else FALSE if search_widths else NA,
        0.76,
        f"Average extracted site-search input width is {avg_search_width:.1f}px.",
        evidence=[f"{value:.1f}px" for value in search_widths[:6]],
        decision_basis="direct",
    ))

    search_links = [button for button in context.user_buttons() if button.get("uxRole") == "search-trigger"]
    search_inputs = context.site_search_inputs()
    results.append(make_result(
        SHEET, 15,
        "Search is always the form itself, not a link to a form.",
        FALSE if search_links and search_inputs else TRUE if search_inputs and not search_links else NA,
        0.84,
        "A search trigger plus a separate modal/form suggests search is opened through a control rather than always exposed directly.",
        evidence=[clean_text(button.get("accessibleName") or button.get("label") or "") for button in search_links[:4]],
        decision_basis="direct",
    ))

    filter_like = []
    for field in context.user_form_fields():
        label = clean_text(field.get("label") or field.get("placeholder") or field.get("name") or "")
        norm = normalize_text(label)
        if not label or norm in {"recherche"}:
            continue
        if any(keyword in norm for keyword in ("prix", "price", "de", "a ")):
            filter_like.append(label)
    results.append(make_result(
        SHEET, 16,
        "The search interface is appropriate to meet user goals (e.g. multi-parameter, prioritized results, filtering search results).",
        NA,
        0.24,
        f"Detected search-adjacent filter fields ({len(filter_like)}), but static extraction is not enough to prove the quality or appropriateness of the search experience.",
        evidence=filter_like[:8],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 17,
        "The search facility deals well with common search queries (e.g. showing most popular results), misspellings and abbreviations.",
        NA,
        0.15,
        "This still requires runtime query testing.",
        evidence=[],
        decision_basis="interactive_required",
    ))
    results.append(make_result(
        SHEET, 18,
        "Search results are relevant, comprehensive, precise, and well displayed.",
        NA,
        0.15,
        "This still requires executed search-result inspection.",
        evidence=[],
        decision_basis="interactive_required",
    ))
    results.append(make_result(
        SHEET, 19,
        "Search results do not return broken links.",
        NA,
        0.10,
        "This still requires search execution plus link validation.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    multi_step_signals = [normalize_text(label) for label in context.button_labels() if any(keyword in normalize_text(label) for keyword in ("continuer", "checkout", "suivant", "next"))]
    results.append(make_result(
        SHEET, 21,
        "The flow of content and/or tasks follows progressive disclosure.",
        NA,
        0.22,
        "Continue/next-style actions alone are not enough to prove progressive disclosure.",
        evidence=multi_step_signals[:6],
        decision_basis="proxy",
    ))

    title_goal_matches = 0
    page_titles = context.page_titles()
    for page in context.pages:
        name = normalize_text(page.html.get("name"))
        core = page_title_core(page.html.get("pageMeta", {}).get("data", {}).get("title", ""))
        if name and (name in core or core in name):
            title_goal_matches += 1
    results.append(make_result(
        SHEET, 22,
        "All primary onscreen content is related to the user’s current task.",
        TRUE if page_titles and title_goal_matches >= max(1, len(page_titles) - 1) else NA,
        0.58,
        f"{title_goal_matches}/{len(page_titles)} pages had a close page-name/page-title alignment.",
        evidence=page_titles[:6],
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 23,
        "The flow of content matches the flow of the work the user is trying to accomplish.",
        NA,
        0.25,
        "This requires fuller user-task mapping and interaction-path review.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 24,
        "Workflows with multiple steps include an overview of those steps.",
        NA,
        0.20,
        "No reliable multi-step overview extraction was found.",
        evidence=[],
        decision_basis="interactive_required",
    ))
    results.append(make_result(
        SHEET, 25,
        "Workflows with multiple steps include a persistent progress indicator.",
        NA,
        0.20,
        "No reliable progress-indicator extraction was found.",
        evidence=[],
        decision_basis="interactive_required",
    ))
    results.append(make_result(
        SHEET, 26,
        "Illustrations are used to make instructions easier to understand (if applicable).",
        NA,
        0.20,
        "Instructional illustrations cannot be confirmed from the current extraction alone.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 27,
        "Similar operations and tasks are presented and performed in similar ways.",
        TRUE if component_scores and avg_consistency >= 80 else NA,
        0.50,
        f"Using overall component-consistency score as a proxy: {avg_consistency:.1f}.",
        evidence=[str(value) for value in component_scores[:4]],
        decision_basis="proxy",
    ))
    results.append(make_result(
        SHEET, 28,
        "Repetitive actions or frequent activities are made easier (e.g. option to use previously entered information).",
        NA,
        0.15,
        "This needs behavioral or interaction evidence beyond static extraction.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    return results
