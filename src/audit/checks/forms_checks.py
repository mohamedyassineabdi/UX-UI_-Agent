from __future__ import annotations

import re
from typing import Any, Dict, List

from .common import (
    AuditContext,
    CHOICE_FIELD_TYPES,
    CheckResult,
    FALSE,
    FORMAT_SENSITIVE_FIELD_TYPES,
    NA,
    TRUE,
    average,
    button_display_label,
    clean_text,
    field_display_label,
    field_has_guidance_signal,
    field_has_required_indicator,
    field_semantic_type,
    make_result,
    normalize_text,
    safe_float,
    tokenize,
)

SHEET = "Forms"

PROGRESS_MARKERS = ("step", "etape", "étape", "progress", "suivant", "next", "back", "retour")
SUCCESS_MARKERS = (
    "article ajoute au panier",
    "article ajouté au panier",
    "added to cart",
    "success",
    "confirmation",
    "submitted",
    "sent",
    "merci",
    "thank you",
)
ERROR_MARKERS = ("error", "erreur", "invalid", "required", "obligatoire", "missing")
SUGGESTION_MARKERS = ("please", "enter", "must", "veuillez", "saisir", "corriger", "fix")
SECONDARY_ACTION_MARKERS = ("cancel", "annuler", "reset", "reinitialiser", "réinitialiser", "back", "retour", "clear", "effacer", "close", "fermer")
CHOICE_EXPECTATION_MARKERS = ("availability", "sort", "trier", "filter", "filtrer", "choose", "choisir", "category", "categorie", "catégorie")
COMMON_LABEL_STOPWORDS = {"field", "input", "value", "option", "submit", "select"}


def _collect_text_records(context: AuditContext) -> List[str]:
    texts: List[str] = []
    for page in context.pages:
        data = page.html.get("titlesAndHeadings", {}).get("data", {})
        for key in ("rawHeadings", "headings", "contentHeadings", "h1", "h2", "h3", "h4", "h5", "h6"):
            values = data.get(key, [])
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict):
                    text = clean_text(item.get("text") or item.get("label") or "")
                else:
                    text = clean_text(item)
                if text:
                    texts.append(text)

        components = page.rendered.get("renderedUi", {}).get("components", {})
        for bucket in ("headings", "textBlocks", "buttons", "links", "navLinks", "dialogs"):
            for item in components.get(bucket, []):
                text = clean_text(
                    item.get("accessibleName")
                    or item.get("label")
                    or item.get("text")
                    or ""
                )
                if text:
                    texts.append(text)

    out: List[str] = []
    seen = set()
    for text in texts:
        key = normalize_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _form_buttons(form: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [button for button in form.get("buttons", []) if button.get("visible") is not False]


def _form_fields(form: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [field for field in form.get("fields", []) if field.get("visible") is not False]


def _is_submit_button(button: Dict[str, Any]) -> bool:
    label = normalize_text(button_display_label(button))
    button_type = normalize_text(button.get("type"))
    return button_type == "submit" or any(token in label for token in ("submit", "envoyer", "send", "save", "search", "recherche", "s'inscrire"))


def _is_secondary_button(button: Dict[str, Any]) -> bool:
    label = normalize_text(button_display_label(button))
    return any(token in label for token in SECONDARY_ACTION_MARKERS)


def _button_style_signature(button: Dict[str, Any]) -> tuple[str, str, str]:
    styles = button.get("styles") or {}
    return (
        normalize_text(button.get("semanticType")),
        normalize_text(styles.get("backgroundColor")),
        normalize_text(styles.get("color")),
    )


def _field_count_signature(form: Dict[str, Any]) -> str:
    form_key = clean_text(form.get("_formKey") or form.get("formKey") or form.get("formId") or form.get("formName") or "form")
    page_name = clean_text(form.get("_pageName") or "")
    return f"{page_name} | {form_key} | fields={len(_form_fields(form))}"


def run(context: AuditContext) -> List[CheckResult]:
    user_forms = context.user_forms()
    task_forms = context.task_forms()
    task_fields = context.user_form_fields()
    all_user_fields = context.all_form_fields()
    page_texts = _collect_text_records(context)

    submit_buttons = []
    for form in task_forms:
        submit_buttons.extend([button for button in _form_buttons(form) if _is_submit_button(button)])

    field_counts = [len(_form_fields(form)) for form in task_forms]
    average_field_count = average(field_counts)
    max_field_count = max(field_counts) if field_counts else 0

    select_fields = [field for field in task_fields if field_semantic_type(field) == "select"]
    choice_fields = [field for field in all_user_fields if field_semantic_type(field) in CHOICE_FIELD_TYPES]
    text_like_fields = [
        field for field in task_fields
        if field_semantic_type(field) in {"input", "text", "textarea", "email", "tel", "number", "date", "password", "url"}
    ]

    required_marked_fields = [field for field in task_fields if field_has_required_indicator(field)]
    labeled_fields = [field for field in task_fields if field_display_label(field)]
    familiar_labels = []
    unfamiliar_labels = []
    for field in labeled_fields:
        label = field_display_label(field)
        norm = normalize_text(label)
        token_count = len(tokenize(label))
        if token_count <= 4 and norm not in COMMON_LABEL_STOPWORDS and "contact[" not in norm:
            familiar_labels.append(label)
        else:
            unfamiliar_labels.append(label)

    format_sensitive_fields = [
        field for field in task_fields
        if field_semantic_type(field) in FORMAT_SENSITIVE_FIELD_TYPES
        or any(token in normalize_text(field_display_label(field)) for token in ("email", "phone", "telephone", "téléphone", "date", "password", "url"))
    ]
    guidance_fields = [field for field in task_fields if field_has_guidance_signal(field)]

    contextual_help_signals = [
        field for field in task_fields
        if clean_text(field.get("ariaDescribedBy") or field.get("helperText"))
    ]

    choice_mismatches = [
        field for field in all_user_fields
        if field_semantic_type(field) not in CHOICE_FIELD_TYPES
        and any(marker in normalize_text(field_display_label(field)) for marker in CHOICE_EXPECTATION_MARKERS)
    ]

    progress_hits = [
        text for text in page_texts
        if any(marker in normalize_text(text) for marker in PROGRESS_MARKERS)
        and re.search(r"\bstep\b|\betape\b|\bétape\b|\b\d+\s*/\s*\d+\b", normalize_text(text))
    ]
    multi_step_signals = [
        label for label in context.button_labels()
        if any(marker in normalize_text(label) for marker in ("next", "suivant", "back", "retour"))
    ]
    has_complex_form = any(count >= 7 for count in field_counts)

    error_hits = [
        text for text in page_texts
        if any(marker in normalize_text(text) for marker in ERROR_MARKERS)
    ]
    suggestion_error_hits = [
        text for text in error_hits
        if any(marker in normalize_text(text) for marker in SUGGESTION_MARKERS)
    ]

    submit_by_form = {}
    secondary_by_form = {}
    distinct_action_forms = []
    indistinct_action_forms = []
    for form in user_forms:
        form_key = clean_text(form.get("_formKey") or form.get("formKey") or form.get("formId") or form.get("formName") or "")
        buttons = _form_buttons(form)
        submit = [button for button in buttons if _is_submit_button(button)]
        secondary = [button for button in buttons if _is_secondary_button(button)]
        if submit:
            submit_by_form[form_key] = submit
        if secondary:
            secondary_by_form[form_key] = secondary

        if submit and secondary:
            submit_signature = _button_style_signature(submit[0])
            if any(_button_style_signature(button) != submit_signature for button in secondary):
                distinct_action_forms.append(form_key)
            else:
                indistinct_action_forms.append(form_key)

    success_hits = [
        text for text in page_texts
        if any(marker in normalize_text(text) for marker in SUCCESS_MARKERS)
    ]

    images = context.all_media_images()
    missing_alt = sum(1 for image in images if not clean_text(image.get("alt")))

    media_blocks = []
    for page in context.pages:
        media = page.html.get("media", {}).get("data", {})
        videos = media.get("videos", []) if isinstance(media.get("videos"), list) else []
        audios = media.get("audios", []) if isinstance(media.get("audios"), list) else []
        has_caption_tracks = bool(media.get("hasCaptionTracks"))
        media_blocks.append({"videos": videos, "audios": audios, "hasCaptions": has_caption_tracks})

    has_media = any(block["videos"] or block["audios"] for block in media_blocks)
    has_captions = any(block["hasCaptions"] for block in media_blocks)

    unlabeled_controls = [
        field for field in task_fields if not field_display_label(field)
    ] + [
        button for button in submit_buttons if not button_display_label(button)
    ]

    checkbox_fields = [field for field in all_user_fields if field_semantic_type(field) in {"checkbox", "radio"}]
    checkbox_with_labels = [
        field for field in checkbox_fields
        if field.get("hasAssociatedLabel") or field.get("hasVisibleLabel")
    ]
    button_target_sizes = []
    for button in submit_buttons:
        rect = button.get("rect") or {}
        width = safe_float(rect.get("width"))
        height = safe_float(rect.get("height"))
        if width is not None and height is not None:
            button_target_sizes.append(min(width, height))

    results: List[CheckResult] = []

    row4_status = TRUE if task_forms and max_field_count <= 6 and average_field_count <= 4.5 else FALSE if max_field_count >= 10 else NA
    results.append(make_result(
        SHEET, 4,
        "Only absolutely necessary questions are asked in forms.",
        row4_status,
        0.68 if row4_status == TRUE else 0.56 if row4_status == FALSE else 0.28,
        f"Task-form field counts: avg={average_field_count:.1f}, max={max_field_count}.",
        evidence=[_field_count_signature(form) for form in task_forms[:6]],
        decision_basis="proxy",
    ))

    row5_status = TRUE if task_forms and not select_fields else NA
    results.append(make_result(
        SHEET, 5,
        "Long droplist menus are avoided when possible; instead users can input text, which is validated on the back end.",
        row5_status,
        0.58 if row5_status == TRUE else 0.24,
        "This is only upgraded when task forms avoid select menus entirely; option counts are not available in the current extraction.",
        evidence=[field_display_label(field) for field in select_fields[:6]],
        decision_basis="proxy",
    ))

    row6_status = TRUE if has_complex_form and (progress_hits or multi_step_signals) else FALSE if has_complex_form else NA
    results.append(make_result(
        SHEET, 6,
        "Complex processes are broken up into easily understood steps and sections.",
        row6_status,
        0.60 if row6_status == TRUE else 0.54 if row6_status == FALSE else 0.22,
        "Only large forms are judged here; smaller standalone forms stay not applicable.",
        evidence=progress_hits[:4] + multi_step_signals[:4] + [_field_count_signature(form) for form in task_forms[:2]],
        decision_basis="proxy" if row6_status != NA else "proxy",
    ))

    row7_status = TRUE if progress_hits else FALSE if multi_step_signals else NA
    results.append(make_result(
        SHEET, 7,
        "For multi-step workflows, a progress indicator is present with numbers or stages.",
        row7_status,
        0.62 if row7_status == TRUE else 0.50 if row7_status == FALSE else 0.20,
        "Explicit step or progress text is required to mark TRUE; next/back controls alone are only enough to mark a likely gap.",
        evidence=progress_hits[:6] + multi_step_signals[:4],
        decision_basis="proxy" if row7_status != NA else "proxy",
    ))

    row9_status = TRUE if required_marked_fields else NA
    results.append(make_result(
        SHEET, 9,
        "Required and optional form fields are clearly indicated.",
        row9_status,
        0.70 if row9_status == TRUE else 0.26,
        "Required-field clarity is inferred from explicit required attributes or visible required markers such as '*'.",
        evidence=[field_display_label(field) for field in required_marked_fields[:8]],
        decision_basis="proxy",
    ))

    row10_status = TRUE if labeled_fields and len(familiar_labels) >= max(1, int(len(labeled_fields) * 0.75)) else FALSE if task_fields else NA
    results.append(make_result(
        SHEET, 10,
        "Fields are labeled with common terms, e.g. Name, Address (supports auto-fill).",
        row10_status,
        0.72 if row10_status == TRUE else 0.58 if row10_status == FALSE else 0.24,
        f"Labeled task fields={len(labeled_fields)}; familiar labels={len(familiar_labels)}.",
        evidence=(familiar_labels[:6] or unfamiliar_labels[:6]),
        decision_basis="proxy",
    ))

    row11_status = TRUE if format_sensitive_fields and guidance_fields else FALSE if format_sensitive_fields else NA
    results.append(make_result(
        SHEET, 11,
        "Fields requiring specific formatting include guidance and examples (e.g. Password must be 6 characters minimum and must contain at least 1 symbol.).",
        row11_status,
        0.62 if row11_status == TRUE else 0.56 if row11_status == FALSE else 0.22,
        "Formatting guidance is inferred from field-level help text, example placeholders, or explanatory descriptors on formatting-sensitive inputs.",
        evidence=[field_display_label(field) for field in guidance_fields[:8]] or [field_display_label(field) for field in format_sensitive_fields[:8]],
        decision_basis="proxy",
    ))

    input_assistance_signals = [
        field for field in task_fields
        if field_semantic_type(field) in FORMAT_SENSITIVE_FIELD_TYPES
        or clean_text(field.get("autocomplete"))
        or field_semantic_type(field) in CHOICE_FIELD_TYPES
    ]
    row12_status = TRUE if input_assistance_signals else NA
    results.append(make_result(
        SHEET, 12,
        "Appropriate input assistance (e.g. calendar widget for date selection) is used and required formats are indicated.",
        row12_status,
        0.68 if row12_status == TRUE else 0.24,
        "Semantic HTML input types, autocomplete attributes, and explicit choice controls are used as evidence of input assistance.",
        evidence=[f"{field_semantic_type(field)} | {field_display_label(field)}" for field in input_assistance_signals[:10]],
        decision_basis="proxy",
    ))

    row13_status = TRUE if contextual_help_signals else FALSE if len(task_fields) >= 4 and not guidance_fields else NA
    results.append(make_result(
        SHEET, 13,
        "Help and instructions (e.g. examples, information required) are provided where necessary, in context with particular fields or sections.",
        row13_status,
        0.58 if row13_status == TRUE else 0.50 if row13_status == FALSE else 0.24,
        "Contextual help requires field-level helper text, described-by references, or distinct inline examples.",
        evidence=[field_display_label(field) for field in contextual_help_signals[:8]] or [field_display_label(field) for field in guidance_fields[:8]],
        decision_basis="proxy",
    ))

    row14_status = TRUE if choice_fields and not choice_mismatches else NA
    results.append(make_result(
        SHEET, 14,
        "Expected data selection conventions are followed (e.g. radio button when only one choice can be made, check box when multiple selections can be made).",
        row14_status,
        0.64 if row14_status == TRUE else 0.24,
        "Choice-like inputs are judged from actual checkbox, radio, and select usage; ambiguous text-entry substitutes remain outside this signal.",
        evidence=[f"{field_semantic_type(field)} | {field_display_label(field)}" for field in choice_fields[:10]],
        decision_basis="proxy",
    ))

    row16_status = TRUE if error_hits else NA
    results.append(make_result(
        SHEET, 16,
        "Form field error messages are displayed next to the related input field.",
        row16_status,
        0.46 if row16_status == TRUE else 0.18,
        "Only explicit error-state text counts here; the current extraction does not preserve enough spatial error placement evidence for a strong claim.",
        evidence=error_hits[:6],
        decision_basis="proxy" if row16_status == TRUE else "interactive_required",
    ))

    row17_status = TRUE if suggestion_error_hits else NA
    results.append(make_result(
        SHEET, 17,
        "Form field error messages include suggestions for correcting the error.",
        row17_status,
        0.46 if row17_status == TRUE else 0.18,
        "Only explicit error text with corrective language counts.",
        evidence=suggestion_error_hits[:6],
        decision_basis="proxy" if row17_status == TRUE else "interactive_required",
    ))

    row18_status = TRUE if distinct_action_forms else FALSE if indistinct_action_forms else NA
    results.append(make_result(
        SHEET, 18,
        "Primary action (e.g. Submit) is visually distinct  from secondary actions (e.g. Cancel).",
        row18_status,
        0.58 if row18_status == TRUE else 0.52 if row18_status == FALSE else 0.24,
        "This compares submit-action styling with reset, cancel, back, and clear actions within the same rendered form.",
        evidence=distinct_action_forms[:6] or indistinct_action_forms[:6],
        decision_basis="proxy",
    ))

    row19_status = TRUE if success_hits and submit_buttons else NA
    results.append(make_result(
        SHEET, 19,
        "Form submission is confirmed in a visually distinct manner (e.g. colored banner).",
        row19_status,
        0.64 if row19_status == TRUE else 0.24,
        "Submission confirmation is inferred from explicit success-message extraction alongside visible form submit actions.",
        evidence=success_hits[:6] + [button_display_label(button) for button in submit_buttons[:3]],
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 20,
        "Users are not automatically taken anywhere else in the site after submitting a form, except to a confirmation page (if desired or necessary).",
        NA,
        0.16,
        "This still requires interaction testing through real form submission paths.",
        evidence=[button_display_label(button) for button in submit_buttons[:6]],
        decision_basis="interactive_required",
    ))

    row22_status = TRUE if images and missing_alt == 0 else FALSE if images else NA
    results.append(make_result(
        SHEET, 22,
        "Alt attributes are provided for non-text elements, such as images and maps.",
        row22_status,
        0.78 if images else 0.20,
        f"Images inspected={len(images)}; images missing alt text={missing_alt}.",
        evidence=[clean_text(image.get("alt")) for image in images[:6] if clean_text(image.get("alt"))],
        decision_basis="direct" if images else "interactive_required",
    ))

    row23_status = TRUE if has_media and has_captions else FALSE if has_media else NA
    results.append(make_result(
        SHEET, 23,
        "Captions and transcriptions are used for audio and video.",
        row23_status,
        0.72 if has_media else 0.18,
        "Audio/video caption support is taken directly from the extracted media block.",
        evidence=[f"videos={len(block['videos'])}, audios={len(block['audios'])}, captions={block['hasCaptions']}" for block in media_blocks if block["videos"] or block["audios"]][:6],
        decision_basis="direct" if has_media else "interactive_required",
    ))

    row24_status = TRUE if submit_buttons and not unlabeled_controls else FALSE if unlabeled_controls else NA
    results.append(make_result(
        SHEET, 24,
        "Color alone is not used to convey hierarchy, content or functionality.",
        row24_status,
        0.54 if row24_status == TRUE else 0.50 if row24_status == FALSE else 0.22,
        "Visible text labels on task-form controls are used as a proxy that function is not communicated by color alone.",
        evidence=[field_display_label(field) for field in task_fields[:6]] + [button_display_label(button) for button in submit_buttons[:4]],
        decision_basis="proxy",
    ))

    row25_status = TRUE if (checkbox_fields and len(checkbox_with_labels) == len(checkbox_fields)) or (submit_buttons and button_target_sizes and min(button_target_sizes) >= 24) else NA
    results.append(make_result(
        SHEET, 25,
        "Links, buttons and check boxes are easily clickable (e.g. user can select a check box by clicking the text as well as the check box).",
        row25_status,
        0.60 if row25_status == TRUE else 0.24,
        "Clickable-form affordance is inferred from associated checkbox labels and minimum visible submit-button target sizes where available.",
        evidence=[field_display_label(field) for field in checkbox_with_labels[:6]] + [f"{size:.1f}px" for size in button_target_sizes[:4]],
        decision_basis="proxy",
    ))

    return results
