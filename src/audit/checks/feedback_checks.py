from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from .common import (
    AuditContext,
    CHOICE_FIELD_TYPES,
    CheckResult,
    FALSE,
    FORMAT_SENSITIVE_FIELD_TYPES,
    NA,
    TOOLTIP_TOKENS,
    TRUE,
    button_display_label,
    clean_text,
    field_display_label,
    field_has_guidance_signal,
    field_has_required_indicator,
    field_semantic_type,
    make_result,
    normalize_text,
    tokenize,
)

SHEET = "Feedback"

ACTION_FEEDBACK_MARKERS = (
    "article ajoute au panier",
    "article ajouté au panier",
    "added to cart",
    "success",
    "confirmation",
    "saved",
    "sent",
    "submitted",
    "merci",
    "thank you",
)

LOADING_MARKERS = (
    "loading",
    "chargement",
    "please wait",
    "patientez",
    "processing",
    "traitement",
    "mise a jour",
    "mise à jour",
)

CONFIRM_MARKERS = (
    "confirm",
    "confirmation",
    "confirmer",
    "are you sure",
    "etes vous sur",
    "êtes vous sûr",
)

INSTRUCTION_MARKERS = (
    "how",
    "comment",
    "instructions",
    "steps",
    "etapes",
    "étapes",
    "please",
    "veuillez",
)

ERROR_MARKERS = (
    "error",
    "erreur",
    "invalid",
    "incorrect",
    "required",
    "obligatoire",
    "missing",
)

def _contains_help_marker(text: str) -> bool:
    norm = normalize_text(text)
    if not norm:
        return False

    words = set(tokenize(norm))
    if words.intersection({"contact", "support", "help", "aide", "assistance", "email", "phone", "telephone"}):
        return True
    return "live chat" in norm


def _page_heading_texts(page: Any) -> List[str]:
    out: List[str] = []
    data = page.html.get("titlesAndHeadings", {}).get("data", {})
    for key in ("rawHeadings", "headings", "contentHeadings", "h1", "h2", "h3", "h4", "h5", "h6"):
        values = data.get(key, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                txt = clean_text(item.get("text") or item.get("label") or "")
            else:
                txt = clean_text(item)
            if txt:
                out.append(txt)
    return out


def _collect_text_records(context: AuditContext) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []

    for page in context.pages:
        page_name = clean_text(page.html.get("name"))

        for text in _page_heading_texts(page):
            records.append({"page": page_name, "source": "heading", "text": text})

        components = page.rendered.get("renderedUi", {}).get("components", {})

        for item in components.get("headings", []):
            text = clean_text(item.get("accessibleName") or item.get("text") or "")
            if text:
                records.append({"page": page_name, "source": "rendered_heading", "text": text})

        for item in components.get("textBlocks", []):
            text = clean_text(item.get("accessibleName") or item.get("text") or "")
            if text:
                records.append({"page": page_name, "source": "text", "text": text})

        for item in components.get("buttons", []):
            text = button_display_label(item)
            if text:
                records.append({"page": page_name, "source": "button", "text": text})

        for bucket in ("links", "navLinks"):
            for item in components.get(bucket, []):
                text = clean_text(item.get("accessibleName") or item.get("label") or item.get("text") or "")
                if text:
                    records.append({"page": page_name, "source": bucket, "text": text})

        for item in components.get("dialogs", []):
            text = clean_text(item.get("accessibleName") or item.get("label") or item.get("text") or "")
            if text:
                records.append({"page": page_name, "source": "dialog", "text": text})

    deduped: List[Dict[str, str]] = []
    seen = set()
    for record in records:
        key = (record["page"], record["source"], normalize_text(record["text"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _match_records(records: List[Dict[str, str]], markers: tuple[str, ...]) -> List[Dict[str, str]]:
    return [record for record in records if any(marker in normalize_text(record["text"]) for marker in markers)]


def _record_texts(records: List[Dict[str, str]], limit: int = 8) -> List[str]:
    out: List[str] = []
    seen = set()
    for record in records:
        text = clean_text(record.get("text"))
        key = normalize_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _button_like_labels(context: AuditContext) -> List[str]:
    return context.button_labels() + context.link_labels()


def run(context: AuditContext) -> List[CheckResult]:
    records = _collect_text_records(context)
    button_labels = _button_like_labels(context)
    task_forms = context.task_forms()
    fields = context.user_form_fields()
    submit_buttons = context.form_submit_buttons()

    feedback_hits = _match_records(records, ACTION_FEEDBACK_MARKERS)
    loading_hits = _match_records(records, LOADING_MARKERS)
    confirm_hits = _match_records(records, CONFIRM_MARKERS)
    instruction_hits = _match_records(records, INSTRUCTION_MARKERS)
    error_hits = _match_records(records, ERROR_MARKERS)

    feedback_pattern_counts = Counter(normalize_text(record["text"]) for record in feedback_hits)
    repeated_feedback_patterns = [
        pattern for pattern, count in feedback_pattern_counts.items()
        if pattern and count >= 2
    ]

    dialogish = [button for button in context.user_buttons() if button.get("uxRole") in {"modal-close"}]
    dialog_labels = [button_display_label(button) for button in dialogish if button_display_label(button)]

    cancel_or_change_hits = [
        label for label in button_labels
        if any(
            token in normalize_text(label)
            for token in (
                "cancel",
                "annuler",
                "close",
                "fermer",
                "back",
                "retour",
                "reset",
                "reinitialiser",
                "réinitialiser",
                "effacer",
                "continuer les achats",
                "continue shopping",
            )
        )
    ]

    destructive_signals = [
        label for label in button_labels
        if any(token in normalize_text(label) for token in ("delete", "remove", "supprimer", "vider", "tout supprimer"))
    ]

    hard_to_undo_signals = [
        label for label in button_labels
        if any(token in normalize_text(label) for token in ("order", "checkout", "pay", "purchase", "acheter", "payer", "commander"))
    ]

    primary_ctas = [
        button for button in context.user_buttons()
        if button.get("uxRole") in {"primary-cta", "purchase-cta", "secondary-action"}
    ]
    flow_signals = [
        button_display_label(button) for button in primary_ctas if button_display_label(button)
    ] + [
        button_display_label(button) for button in submit_buttons if button_display_label(button)
    ]

    tooltip_like = []
    for button in context.user_buttons():
        text = button_display_label(button)
        norm = normalize_text(text)
        class_name = normalize_text(button.get("className"))
        if norm in TOOLTIP_TOKENS or class_name.endswith("tooltip") or " tooltip" in class_name:
            tooltip_like.append(text)

    validation_evidence = []
    for field in fields:
        signals = []
        if field_has_required_indicator(field):
            signals.append("required")
        semantic_type = field_semantic_type(field)
        if semantic_type in FORMAT_SENSITIVE_FIELD_TYPES or semantic_type in CHOICE_FIELD_TYPES:
            signals.append(semantic_type)
        if clean_text(field.get("autocomplete")):
            signals.append("autocomplete")
        if field_has_guidance_signal(field):
            signals.append("guidance")
        if signals:
            validation_evidence.append(f"{'/'.join(signals)} | {field_display_label(field)}")

    field_level_error_support = [
        field_display_label(field) for field in fields
        if clean_text(field.get("ariaDescribedBy") or field.get("helperText"))
    ]

    help_evidence = [
        label for label in button_labels
        if _contains_help_marker(label)
    ]

    simple_help_labels = [
        label for label in help_evidence
        if len(tokenize(label)) <= 4
    ]

    results: List[CheckResult] = []

    feedback_status = TRUE if feedback_hits else FALSE if (context.pages and (submit_buttons or task_forms)) else NA
    results.append(make_result(
        SHEET, 4,
        "The UI responds to a user's actions or requests visually (onscreen message).",
        feedback_status,
        0.86 if feedback_hits else 0.48 if feedback_status == FALSE else 0.30,
        "Uses extracted status-message text from headings, rendered text blocks, dialogs, and controls instead of relying on one heading bucket only.",
        evidence=_record_texts(feedback_hits, limit=6),
        decision_basis="direct" if feedback_hits else "proxy" if feedback_status == FALSE else "proxy",
    ))

    results.append(make_result(
        SHEET, 5,
        "The UI provides feedback to let the user know his/her request is being processed.",
        TRUE if loading_hits else NA,
        0.72 if loading_hits else 0.30,
        "Only marked TRUE when explicit loading or processing indicators are extracted from rendered text or headings.",
        evidence=_record_texts(loading_hits, limit=6),
        decision_basis="direct" if loading_hits else "proxy",
    ))

    row6_status = TRUE if repeated_feedback_patterns else NA
    results.append(make_result(
        SHEET, 6,
        "Messages and alerts appear consistently, in the same location and visual style.",
        row6_status,
        0.62 if row6_status == TRUE else 0.26,
        f"Repeated feedback message patterns detected across audited pages: {len(repeated_feedback_patterns)}.",
        evidence=_record_texts(feedback_hits, limit=6),
        decision_basis="proxy",
    ))

    row7_status = TRUE if feedback_hits or dialogish else NA
    results.append(make_result(
        SHEET, 7,
        "Alert messages are visually distinct, easily distinguished from screen content or other interactive elements.",
        row7_status,
        0.64 if row7_status == TRUE else 0.28,
        "Explicit status messages or modal-close controls suggest feedback is separated from surrounding page content.",
        evidence=dialog_labels[:4] + _record_texts(feedback_hits, limit=3),
        decision_basis="proxy",
    ))

    row8_status = TRUE if len(cancel_or_change_hits) >= 2 or (cancel_or_change_hits and confirm_hits) else NA
    results.append(make_result(
        SHEET, 8,
        "Users can easily undo, go back and change or cancel actions - or are given the chance to confirm an action before committing (e.g. before placing an order).",
        row8_status,
        0.60 if row8_status == TRUE else 0.28,
        "Cancelable or reversible paths are inferred from explicit back, close, reset, or continue-shopping controls.",
        evidence=cancel_or_change_hits[:8],
        decision_basis="proxy",
    ))

    row9_status = TRUE if destructive_signals and confirm_hits else NA
    results.append(make_result(
        SHEET, 9,
        "Confirmation is required when an action is destructive (e.g. Delete).",
        row9_status,
        0.56 if row9_status == TRUE else 0.24,
        "This is only upgraded when destructive actions and confirmation-language signals are both present in extraction evidence.",
        evidence=destructive_signals[:6] + _record_texts(confirm_hits, limit=3),
        decision_basis="proxy" if row9_status == TRUE else "interactive_required",
    ))

    row10_status = TRUE if hard_to_undo_signals and confirm_hits else NA
    results.append(make_result(
        SHEET, 10,
        "Confirmation is required when an action is difficult or impossible to undo.",
        row10_status,
        0.54 if row10_status == TRUE else 0.22,
        "This remains conservative unless irreversible-action controls and confirmation cues appear together.",
        evidence=hard_to_undo_signals[:6] + _record_texts(confirm_hits, limit=3),
        decision_basis="proxy" if row10_status == TRUE else "interactive_required",
    ))

    row12_status = TRUE if flow_signals and task_forms else NA
    results.append(make_result(
        SHEET, 12,
        "The user always knows what to do first/next.",
        row12_status,
        0.60 if row12_status == TRUE else 0.26,
        "A single visible submit or primary action per task form is used as a proxy for next-step clarity.",
        evidence=flow_signals[:8],
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 13,
        "Instructions specific to the overall process are given at the start of the process.",
        TRUE if instruction_hits else NA,
        0.54 if instruction_hits else 0.24,
        "Only explicit instruction-like headings or text blocks count here.",
        evidence=_record_texts(instruction_hits, limit=6),
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 14,
        "Instructions specific to a particular step are given at the start of that step.",
        NA,
        0.18,
        "Step-level instructional ordering still requires richer sequence data than the current extraction guarantees.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 15,
        "Instructions obvious to expert users are hidden but available (e.g. Tool tip).",
        TRUE if tooltip_like else NA,
        0.48 if tooltip_like else 0.24,
        "Only explicit tooltip-like controls or help/info affordances count as evidence.",
        evidence=tooltip_like[:6],
        decision_basis="direct" if tooltip_like else "proxy",
    ))

    row17_status = TRUE if error_hits and field_level_error_support else NA
    results.append(make_result(
        SHEET, 17,
        "Errors are clear, visually distinct from labels/data and appear in an appropriate location  (e.g. adjacent to data entry field, adjacent to form, etc.).",
        row17_status,
        0.52 if row17_status == TRUE else 0.22,
        "Requires either explicit error text plus field-level descriptors, or richer interaction evidence.",
        evidence=_record_texts(error_hits, limit=4) + field_level_error_support[:4],
        decision_basis="proxy" if row17_status == TRUE else "interactive_required",
    ))

    concise_error_hits = [
        record for record in error_hits
        if len(tokenize(record["text"])) <= 24 and any(token in normalize_text(record["text"]) for token in ("please", "enter", "must", "veuillez", "saisir"))
    ]
    results.append(make_result(
        SHEET, 18,
        "Error messages are concise, written in easy to understand language and describe what's occurred and what action is necessary.",
        TRUE if concise_error_hits else NA,
        0.50 if concise_error_hits else 0.20,
        "Only explicit field or status error messages with corrective language count.",
        evidence=_record_texts(concise_error_hits, limit=6),
        decision_basis="proxy" if concise_error_hits else "interactive_required",
    ))

    results.append(make_result(
        SHEET, 19,
        "Common user errors (e.g. missing fields, invalid formats, invalid selections) have been taken into consideration and where possible prevented.",
        TRUE if validation_evidence else NA,
        0.72 if validation_evidence else 0.24,
        "Preventive evidence is inferred from required indicators, semantic input types, autocomplete, and field-level guidance on real task-form fields.",
        evidence=validation_evidence[:10],
        decision_basis="proxy" if validation_evidence else "interactive_required",
    ))

    row21_status = TRUE if simple_help_labels else NA
    results.append(make_result(
        SHEET, 21,
        "Help is written in easy to understand language, with terms users will recognize.",
        row21_status,
        0.56 if row21_status == TRUE else 0.18,
        "Simple support labels such as contact, help, phone, or email are used as lightweight evidence for understandable help language.",
        evidence=simple_help_labels[:6],
        decision_basis="proxy" if row21_status == TRUE else "interactive_required",
    ))

    results.append(make_result(
        SHEET, 22,
        "Accessing online help does not stop user progress; they can resume work where they left off after accessing help).",
        NA,
        0.12,
        "This still requires interaction-state testing.",
        evidence=help_evidence[:6],
        decision_basis="interactive_required",
    ))

    row23_status = TRUE if help_evidence else FALSE if context.pages else NA
    results.append(make_result(
        SHEET, 23,
        "Users can easily get further help (e.g. phone number, live chat, email support).",
        row23_status,
        0.80 if row23_status == TRUE else 0.52 if row23_status == FALSE else 0.22,
        "Contact/help availability is inferred from visible navigation, link, and button labels rather than one navigation subset only.",
        evidence=help_evidence[:8],
        decision_basis="direct",
    ))

    results.append(make_result(
        SHEET, 24,
        "Workflows requiring rapid or time-limited responses provide visual alerts explaining this at the beginning of the process.",
        NA,
        0.12,
        "No time-limited workflow evidence was extracted.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 25,
        "Users are alerted to pending time-out and are allowed to request more time.",
        NA,
        0.10,
        "Timeout handling cannot be evaluated from the current extraction.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    status_signals = feedback_hits or loading_hits or concise_error_hits
    results.append(make_result(
        SHEET, 26,
        "The system keeps users informed about what has happened, what is happening or what will happen during workflows or upon taking action (visibility of system status).",
        TRUE if status_signals else FALSE if (context.pages and (submit_buttons or task_forms)) else NA,
        0.76 if status_signals else 0.46 if context.pages and (submit_buttons or task_forms) else 0.24,
        "Visibility of system status is based on explicit feedback, processing, or concise error-state signals across extracted pages.",
        evidence=_record_texts(feedback_hits, limit=4) + _record_texts(loading_hits, limit=2) + _record_texts(concise_error_hits, limit=2),
        decision_basis="direct" if status_signals else "proxy",
    ))

    return results
