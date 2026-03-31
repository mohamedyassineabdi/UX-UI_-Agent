from __future__ import annotations

from typing import List

from .common import (
    AuditContext,
    CheckResult,
    FALSE,
    NA,
    TRUE,
    TOOLTIP_TOKENS,
    abbreviation_ratio,
    clean_text,
    comparable_label,
    make_result,
    normalize_text,
    page_title_core,
    tokenize,
)

SHEET = "Labeling"


def run(context: AuditContext) -> List[CheckResult]:
    nav_labels = context.meaningful_navigation_labels()
    button_labels = context.button_labels()
    link_labels = context.link_labels()
    field_labels = [
        clean_text(field.get("label") or field.get("placeholder") or field.get("name") or "")
        for field in context.user_form_fields()
    ]
    all_labels = [label for label in (nav_labels + button_labels + link_labels + field_labels) if label]
    design_summaries = context.design_summaries()

    results: List[CheckResult] = []

    systemish_terms = ["submit", "modal", "field", "component", "audit", "utf8", "return_to"]
    visible_systemish = [label for label in all_labels if any(term in normalize_text(label) for term in systemish_terms)]
    results.append(make_result(
        SHEET, 4,
        "Label terms are familiar to the intended user and are not system-oriented terms.",
        TRUE if len(visible_systemish) <= 1 else FALSE if all_labels else NA,
        0.80,
        f"Meaningful label set inspected after stronger noise filtering; visible system-oriented labels detected: {len(visible_systemish)}.",
        evidence=visible_systemish[:6],
        decision_basis="direct",
    ))

    value_terms = (
        "free", "gratuit", "voir", "decouvrir", "découvrir", "continuer",
        "continuer les achats", "tout afficher", "choisir des options", "ajouter au panier",
    )
    generic_terms = ("submit", "go", "ok", "envoyer")
    value_oriented = [label for label in button_labels if any(term in normalize_text(label) for term in value_terms)]
    generic_actions = [label for label in button_labels if normalize_text(label) in generic_terms]
    status = TRUE if value_oriented and len(value_oriented) >= max(1, len(generic_actions)) else NA if button_labels else NA
    results.append(make_result(
        SHEET, 5,
        "Labeling describes value when possible (e.g.  “Free Trial” vs. “Register”).",
        status,
        0.58 if status == TRUE else 0.26,
        f"Value-oriented CTAs={len(value_oriented)}; generic-action CTAs={len(generic_actions)}. This check now stays conservative unless positive evidence is visible.",
        evidence=(value_oriented + generic_actions)[:8],
        decision_basis="proxy",
    ))

    languages = sorted(set(context.page_languages()))
    results.append(make_result(
        SHEET, 6,
        "Language is consistent across label types (e.g. verb/noun, tense, tone, word count).",
        TRUE if len(languages) == 1 and all_labels else FALSE if len(languages) > 1 and all_labels else NA,
        0.74,
        f"Page languages detected for label context: {languages or ['unknown']}.",
        evidence=all_labels[:10],
        decision_basis="direct",
    ))

    abbr_ratio = abbreviation_ratio(all_labels)
    results.append(make_result(
        SHEET, 7,
        "Full words are used instead of cryptic abbreviations.",
        TRUE if abbr_ratio <= 0.08 else FALSE if abbr_ratio > 0.15 and all_labels else NA,
        0.78 if abbr_ratio <= 0.08 else 0.34 if abbr_ratio <= 0.15 else 0.72,
        f"Estimated abbreviation ratio in filtered labels is {abbr_ratio:.2%}. Units, currency, and product-variant tokens are excluded.",
        evidence=[],
        decision_basis="direct" if abbr_ratio <= 0.08 or abbr_ratio > 0.15 else "proxy",
    ))

    typographic_variety = []
    for summary in design_summaries:
        typography = summary.get("typography", {}).get("counts", {})
        if typography:
            typographic_variety.append(str(typography))
    results.append(make_result(
        SHEET, 8,
        "Labels are visually distinct from content and/or data.",
        TRUE if typographic_variety else NA,
        0.55,
        "Still inferred from rendered typography summaries rather than exact label-vs-content pairing.",
        evidence=typographic_variety[:4],
        decision_basis="proxy",
    ))

    page_titles = context.page_titles()
    nav_core = {comparable_label(label) for label in nav_labels}
    exact_title_matches = 0
    title_evidence = []
    for title in page_titles:
        core = comparable_label(title)
        title_evidence.append(f"{title} -> {core}")
        if core and core in nav_core:
            exact_title_matches += 1
    results.append(make_result(
        SHEET, 10,
        "Each page title exactly matches the wording of the related navigation menu link.",
        TRUE if page_titles and exact_title_matches >= max(1, len(page_titles) - 1) else FALSE if page_titles and exact_title_matches == 0 else NA,
        0.82 if page_titles and exact_title_matches >= max(1, len(page_titles) - 1) else 0.36 if exact_title_matches else 0.52,
        f"{exact_title_matches}/{len(page_titles)} page titles matched navigation labels after normalization that removes site-brand suffixes and title framing.",
        evidence=title_evidence[:6],
        decision_basis="direct" if exact_title_matches >= max(1, len(page_titles) - 1) else "proxy",
    ))

    breadcrumb_total = sum(len(page.person_a.get("navigation", {}).get("data", {}).get("breadcrumbs", [])) for page in context.pages)
    results.append(make_result(
        SHEET, 11,
        "Each page title exactly matches the wording of the related breadcrumb link.",
        NA if breadcrumb_total == 0 else TRUE,
        0.40,
        f"Detected breadcrumb items across the audit set: {breadcrumb_total}.",
        evidence=[],
        decision_basis="proxy",
    ))

    descriptive_titles = [title for title in page_titles if len(tokenize(page_title_core(title))) >= 1]
    results.append(make_result(
        SHEET, 12,
        "Each page title gives the user a clear idea of the page’s content and purpose.",
        TRUE if len(descriptive_titles) == len(page_titles) and page_titles else FALSE if page_titles else NA,
        0.70,
        f"{len(descriptive_titles)}/{len(page_titles)} titles look descriptive by normalized token heuristics.",
        evidence=page_titles[:6],
        decision_basis="direct",
    ))

    results.append(make_result(
        SHEET, 14,
        "Breadcrumb paths match established navigation paths.",
        NA if breadcrumb_total == 0 else TRUE,
        0.30,
        "No breadcrumbs were extracted on the audited pages, so this criterion remains not applicable for now." if breadcrumb_total == 0 else "Breadcrumbs exist and need deeper path comparison in the next iteration.",
        evidence=[],
        decision_basis="proxy",
    ))
    results.append(make_result(
        SHEET, 15,
        "Every breadcrumb has a counterpart in a navigation menu.",
        NA if breadcrumb_total == 0 else TRUE,
        0.30,
        "No breadcrumbs were extracted on the audited pages, so this criterion remains not applicable for now." if breadcrumb_total == 0 else "Breadcrumb counterparts need a deeper path comparison in the next iteration.",
        evidence=[],
        decision_basis="proxy",
    ))
    results.append(make_result(
        SHEET, 16,
        "The full navigation path is shown in the breadcrumb (e.g. “Home > Services > Annual Reports > File an Annual Report” instead of “Home > File an Annual Report”).",
        NA,
        0.20,
        "This needs full breadcrumb path parsing and page hierarchy validation.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 18,
        "Form labels have less color and contrast than the content they describe.",
        NA,
        0.20,
        "The current extraction still lacks exact paired label/content styling for each form field.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    user_fields = context.user_form_fields()
    labeled_fields = [field for field in user_fields if clean_text(field.get("label") or field.get("placeholder"))]
    results.append(make_result(
        SHEET, 19,
        "Each form label is in close proximity to the item it describes.",
        TRUE if labeled_fields else NA,
        0.55,
        "This is inferred from visible user-facing labels or placeholders on actual user-input fields, excluding localization controls.",
        evidence=[clean_text(field.get("label") or field.get("placeholder") or "") for field in labeled_fields[:8]],
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 21,
        "Table labels have less color and contrast than the content they describe.",
        NA,
        0.20,
        "No rendered tables were extracted.",
        evidence=[],
        decision_basis="interactive_required",
    ))
    results.append(make_result(
        SHEET, 22,
        "Each table label is in close proximity to the item it describes.",
        NA,
        0.20,
        "No rendered tables were extracted.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    tooltip_like = []
    for button in context.user_buttons():
        text = clean_text(button.get("accessibleName") or button.get("label") or button.get("text"))
        norm = normalize_text(text)
        class_name = normalize_text(button.get("className"))
        if norm in TOOLTIP_TOKENS or class_name.endswith("tooltip") or " tooltip" in class_name:
            tooltip_like.append(text)
    results.append(make_result(
        SHEET, 24,
        "Tool tip icons are universally recognizable (e.g. “?”).",
        TRUE if tooltip_like else NA,
        0.45,
        "Tooltip recognition is strict: only explicit help/info or tooltip-like controls count.",
        evidence=tooltip_like[:6],
        decision_basis="direct" if tooltip_like else "proxy",
    ))
    results.append(make_result(
        SHEET, 25,
        "Tool tip icons are visually distinct from content and/or data.",
        TRUE if tooltip_like else NA,
        0.40,
        "Only evaluated when explicit tooltip/help controls are actually present.",
        evidence=tooltip_like[:6],
        decision_basis="proxy",
    ))
    results.append(make_result(
        SHEET, 26,
        "Tool tip icons have less color and contrast than the content they describe.",
        NA,
        0.20,
        "Tooltip/icon contrast relationship is not reliably recoverable from the current extraction.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    return results