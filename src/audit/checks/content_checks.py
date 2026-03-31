from __future__ import annotations

from typing import List

from .common import (
    AuditContext,
    CheckResult,
    FALSE,
    NA,
    TRUE,
    abbreviation_ratio,
    average,
    average_words_per_paragraph,
    clean_text,
    looks_like_marketing_banner,
    make_result,
    normalize_text,
    tokenize,
    uppercase_token_ratio,
)

SHEET = "Content"


def run(context: AuditContext) -> List[CheckResult]:
    paragraphs = context.meaningful_paragraphs()
    headings = context.content_headings()
    list_items = context.meaningful_list_items()
    flags = set(context.all_quality_flags())
    images = context.all_media_images()
    button_labels = context.button_labels()
    link_labels = context.link_labels()
    all_texts = paragraphs + headings + button_labels + link_labels + list_items

    results: List[CheckResult] = []

    avg_words = average_words_per_paragraph(paragraphs)
    long_ratio = sum(1 for text in paragraphs if len(tokenize(text)) > 30) / len(paragraphs) if paragraphs else 0.0

    results.append(make_result(
        SHEET, 4,
        "Language is plain, clear and simple.",
        TRUE if paragraphs and avg_words <= 24 and long_ratio <= 0.35 else FALSE if paragraphs else NA,
        0.80,
        f"Using filtered editorial paragraphs only: average paragraph length is {avg_words:.1f} words and long-paragraph ratio is {long_ratio:.0%}.",
        evidence=paragraphs[:3],
        decision_basis="direct",
    ))

    jargon_terms = {"api", "sdk", "oauth", "graphql", "metadata", "ux", "ui", "cms"}
    jargon_hits = []
    for text in all_texts:
        token_set = {normalize_text(token) for token in tokenize(text)}
        if jargon_terms.intersection(token_set):
            jargon_hits.append(text)
    common_language_ok = len(jargon_hits) <= max(1, int(len(all_texts) * 0.02))
    results.append(make_result(
        SHEET, 5,
        "Content is written with common language that users easily understand.",
        TRUE if common_language_ok else FALSE if all_texts else NA,
        0.78,
        f"Potentially technical content items detected after filtering noise: {len(jargon_hits)}.",
        evidence=jargon_hits[:5],
        decision_basis="direct",
    ))

    languages = sorted(set(context.page_languages()))
    results.append(make_result(
        SHEET, 6,
        "Terms, language and tone used are consistent throughout the site.",
        TRUE if len(languages) == 1 and languages else FALSE if len(languages) > 1 else NA,
        0.78,
        f"Detected page languages across audited pages: {languages or ['unknown']}.",
        evidence=context.page_names(),
        decision_basis="direct",
    ))

    audience_signals = context.meaningful_navigation_labels()[:6] + headings[:6]
    results.append(make_result(
        SHEET, 7,
        "Language, terminology and tone used is understood by the target audience.",
        NA,
        0.22,
        "This remains too subjective to judge safely from static extraction alone, even after noise filtering.",
        evidence=audience_signals[:8],
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 8,
        "Content is useful and up-to-date, providing answers to common questions.",
        NA,
        0.25,
        "Usefulness and freshness still require domain context, dates, or task-based validation beyond the current extraction.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    happy_talk = [text for text in paragraphs if looks_like_marketing_banner(text)]
    results.append(make_result(
        SHEET, 9,
        "There is no “happy talk” — long upfront instructions or “welcome” text.",
        FALSE if happy_talk else TRUE if paragraphs else NA,
        0.86,
        "Front-loaded welcome or promotional copy was checked using filtered editorial paragraphs.",
        evidence=happy_talk[:4],
        decision_basis="direct",
    ))

    missing_h1 = "missing_meaningful_h1" in flags
    descriptive_heading_count = len(headings)
    enough_heading_evidence = descriptive_heading_count >= max(1, len(context.pages) - 1)
    row10_status = TRUE if enough_heading_evidence else NA
    row10_confidence = 0.74 if row10_status == TRUE else 0.35
    results.append(make_result(
        SHEET, 10,
        "Titles and Headings clearly describe the content of the page.",
        row10_status,
        row10_confidence,
        f"Meaningful content headings found: {descriptive_heading_count}; missing meaningful H1 flag={missing_h1}. Missing H1 alone no longer forces FALSE.",
        evidence=headings[:8],
        decision_basis="direct" if row10_status == TRUE else "proxy",
    ))

    results.append(make_result(
        SHEET, 11,
        "Headings precede related paragraphs.",
        NA,
        0.20,
        "The current extracted JSON still does not preserve enough reliable reading order between headings and paragraphs.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 12,
        "Lists are used for related sub-points or sub-navigation links.",
        TRUE if len(list_items) >= 3 else FALSE if context.all_list_items() else NA,
        0.72,
        f"Detected {len(list_items)} meaningful list items after excluding locale picker and system noise.",
        evidence=list_items[:8],
        decision_basis="direct",
    ))

    results.append(make_result(
        SHEET, 13,
        "Links in text are contextually related to what the user is currently doing or reading.",
        NA,
        0.24,
        "This still needs richer region-level context than the current extraction provides.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    left_aligned = []
    centered = []
    for block in context.meaningful_text_blocks():
        text = clean_text(block.get("accessibleName") or block.get("text") or "")
        if not text or looks_like_marketing_banner(text):
            continue
        align = normalize_text((block.get("styles") or {}).get("textAlign"))
        if align == "left":
            left_aligned.append(text)
        elif align == "center":
            centered.append(text)
    results.append(make_result(
        SHEET, 14,
        "All sentences, paragraphs, titles and headlines are left-aligned.",
        NA,
        0.24,
        f"Found left-aligned={len(left_aligned)} and centered={len(centered)} meaningful text blocks, but centered hero or card text is not enough to fail this universally.",
        evidence=(centered[:6] or left_aligned[:6]),
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 15,
        "Content is scannable — short paragraphs, descriptive headings, lists and images.",
        TRUE if paragraphs and avg_words <= 24 and headings and list_items and images else FALSE if paragraphs or headings else NA,
        0.84,
        f"Paragraphs={len(paragraphs)}, meaningful headings={len(headings)}, meaningful list items={len(list_items)}, images={len(images)}, avg_words={avg_words:.1f}.",
        evidence=paragraphs[:2] + headings[:2] + list_items[:2],
        decision_basis="direct",
    ))

    contrasts = []
    for block in context.meaningful_text_blocks():
        value = block.get("contrastAgainstEffectiveBackground")
        if isinstance(value, (int, float)):
            contrasts.append(float(value))
    avg_contrast = average(contrasts)
    results.append(make_result(
        SHEET, 16,
        "There is adequate contrast between the text content and background.",
        TRUE if contrasts and avg_contrast >= 4.5 else FALSE if contrasts else NA,
        0.88,
        f"Average extracted text-block contrast against effective background is {avg_contrast:.2f}.",
        evidence=[f"{value:.2f}" for value in contrasts[:6]],
        decision_basis="direct",
    ))

    strong_emphasis = 0
    for block in context.meaningful_text_blocks():
        styles = block.get("styles") or {}
        font_weight = normalize_text(styles.get("fontWeight"))
        color = normalize_text(styles.get("color"))
        if font_weight in {"500", "600", "700", "800", "900"} and color:
            strong_emphasis += 1
    results.append(make_result(
        SHEET, 17,
        "Words and sentences, when applicable, are emphasized by both color and weight.",
        TRUE if strong_emphasis > 0 else NA,
        0.50,
        f"Detected {strong_emphasis} meaningful text blocks with stronger font weight and explicit color styling.",
        evidence=[],
        decision_basis="proxy" if strong_emphasis > 0 else "proxy",
    ))

    chart_like = []
    for image in images:
        alt = normalize_text(image.get("alt"))
        if any(keyword in alt for keyword in ("chart", "graph", "diagram", "infographic", "schema")):
            chart_like.append(clean_text(image.get("alt")))
    results.append(make_result(
        SHEET, 18,
        "Visual content (e.g. infographic, chart) is used to illustrate complex concepts.",
        TRUE if chart_like else NA,
        0.35,
        "This is only marked TRUE when chart or diagram evidence is explicit in image metadata.",
        evidence=chart_like[:6],
        decision_basis="direct" if chart_like else "proxy",
    ))

    results.append(make_result(
        SHEET, 19,
        "Separate ideas are kept in separate sentences and paragraphs.",
        NA,
        0.22,
        "This still needs fuller editorial sequence and semantic segmentation than the extraction currently guarantees.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    abbr_ratio = abbreviation_ratio(all_texts)
    results.append(make_result(
        SHEET, 20,
        "Full words are used instead of cryptic abbreviations.",
        TRUE if abbr_ratio <= 0.05 else FALSE if all_texts else NA,
        0.78,
        f"Estimated abbreviation ratio after excluding units, currency, and product-variant noise is {abbr_ratio:.2%}.",
        evidence=[],
        decision_basis="direct",
    ))

    upper_ratio = uppercase_token_ratio(all_texts)
    upper_status = TRUE if upper_ratio <= 0.08 else NA if upper_ratio <= 0.15 else FALSE
    results.append(make_result(
        SHEET, 21,
        "Uppercase words are used only for labels or acronyms.",
        upper_status if all_texts else NA,
        0.72 if upper_status == TRUE else 0.34 if upper_status == NA else 0.68,
        f"Estimated uppercase-token ratio after excluding noise tokens is {upper_ratio:.2%}.",
        evidence=[],
        decision_basis="direct" if upper_status != NA else "proxy",
    ))

    results.append(make_result(
        SHEET, 22,
        "Acronyms (e.g. UX) are used sparingly.",
        TRUE if abbr_ratio <= 0.05 else NA if abbr_ratio <= 0.10 else FALSE if all_texts else NA,
        0.74,
        f"Estimated acronym or abbreviation ratio after smarter filtering is {abbr_ratio:.2%}.",
        evidence=[],
        decision_basis="direct" if abbr_ratio <= 0.05 or abbr_ratio > 0.10 else "proxy",
    ))

    results.append(make_result(
        SHEET, 23,
        "Acronyms are explained (e.g. User Experience) in the first instance of use.",
        NA,
        0.21,
        "This can only be judged reliably when acronym usage and first-use sequence are both clearly captured.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    missing_alt = sum(1 for image in images if not clean_text(image.get("alt")))
    results.append(make_result(
        SHEET, 24,
        "Meaningful images have appropriate text alternatives.",
        TRUE if images and missing_alt == 0 else FALSE if images else NA,
        0.82,
        f"Images inspected={len(images)}; images missing alt text={missing_alt}.",
        evidence=[clean_text(image.get("alt")) for image in images[:6] if clean_text(image.get("alt"))],
        decision_basis="direct",
    ))

    duplicate_alt = len([clean_text(image.get("alt")) for image in images if clean_text(image.get("alt"))]) != len({normalize_text(clean_text(image.get("alt"))) for image in images if clean_text(image.get("alt"))})
    results.append(make_result(
        SHEET, 25,
        "Alternative text does not simply repeat adjacent visible text unnecessarily.",
        NA if not images else FALSE if duplicate_alt else TRUE,
        0.48,
        "This is only a lightweight duplicate-alt heuristic; it does not yet compare full local visual context.",
        evidence=[clean_text(image.get("alt")) for image in images[:6] if clean_text(image.get("alt"))],
        decision_basis="proxy",
    ))

    return results