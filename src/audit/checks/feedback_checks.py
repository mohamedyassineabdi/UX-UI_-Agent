from __future__ import annotations

from typing import List

from .common import (
    AuditContext,
    CheckResult,
    FALSE,
    NA,
    TRUE,
    TOOLTIP_TOKENS,
    clean_text,
    make_result,
    normalize_text,
)

SHEET = "Feedback"


def run(context: AuditContext) -> List[CheckResult]:
    headings = context.all_feedback_headings()
    heading_norm = [normalize_text(text) for text in headings]
    button_labels = context.button_labels()
    fields = context.user_form_fields()

    results: List[CheckResult] = []

    action_feedback_markers = [
        "article ajoute au panier", "article ajouté au panier", "added to cart",
        "success", "confirmation", "saved", "sent", "merci",
    ]
    feedback_hits = [text for text in heading_norm if any(marker in text for marker in action_feedback_markers)]
    results.append(make_result(
        SHEET, 4,
        "The UI responds to a user’s actions or requests visually (onscreen message).",
        TRUE if feedback_hits else FALSE if context.pages else NA,
        0.85,
        f"Detected feedback or success message markers: {len(feedback_hits)}.",
        evidence=feedback_hits[:6],
        decision_basis="direct",
    ))

    loading_hits = []
    for block in context.meaningful_text_blocks():
        text = normalize_text(block.get("accessibleName") or block.get("text"))
        if any(keyword in text for keyword in ("loading", "chargement", "please wait", "patientez", "processing")):
            loading_hits.append(clean_text(block.get("accessibleName") or block.get("text")))
    results.append(make_result(
        SHEET, 5,
        "The UI provides feedback to let the user know his/her request is being processed.",
        TRUE if loading_hits else NA,
        0.34,
        "Only marked TRUE when explicit loading or processing indicators are extracted.",
        evidence=loading_hits[:6],
        decision_basis="direct" if loading_hits else "proxy",
    ))

    results.append(make_result(
        SHEET, 6,
        "Messages and alerts appear consistently, in the same location and visual style.",
        NA,
        0.22,
        "This still requires repeated message-state extraction across comparable actions or pages.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    dialogish = [button for button in context.user_buttons() if button.get("uxRole") in {"modal-close"}]
    results.append(make_result(
        SHEET, 7,
        "Alert messages are visually distinct, easily distinguished from screen content or other interactive elements.",
        TRUE if feedback_hits or dialogish else NA,
        0.56,
        "Presence of explicit feedback headings or close controls suggests visually separated feedback states.",
        evidence=[clean_text(button.get("accessibleName") or button.get("label") or "") for button in dialogish[:4]] + feedback_hits[:3],
        decision_basis="proxy",
    ))

    cancel_labels = [
        label for label in button_labels
        if any(word in normalize_text(label) for word in ("cancel", "annuler", "fermer", "close", "back", "retour"))
    ]
    results.append(make_result(
        SHEET, 8,
        "Users can easily undo, go back and change or cancel actions — or are given the chance to confirm an action before committing (e.g. before placing an order).",
        NA,
        0.24,
        "Visible close/back controls alone are not enough to prove undo, cancel, or pre-commit confirmation quality.",
        evidence=cancel_labels[:8],
        decision_basis="interactive_required",
    ))

    destructive_signals = [
        label for label in button_labels
        if any(word in normalize_text(label) for word in ("delete", "remove", "supprimer", "vider", "tout supprimer"))
    ]
    results.append(make_result(
        SHEET, 9,
        "Confirmation is required when an action is destructive (e.g. Delete).",
        NA,
        0.20,
        "Potentially destructive controls may exist, but confirmation cannot be verified from static extraction alone.",
        evidence=destructive_signals[:6],
        decision_basis="interactive_required",
    ))

    hard_to_undo_signals = [
        label for label in button_labels
        if any(word in normalize_text(label) for word in ("order", "checkout", "pay", "purchase", "acheter", "payer"))
    ]
    results.append(make_result(
        SHEET, 10,
        "Confirmation is required when an action is difficult or impossible to undo.",
        NA,
        0.18,
        "This needs post-action confirmation or irreversible-step evidence, which static extraction cannot guarantee.",
        evidence=hard_to_undo_signals[:6],
        decision_basis="interactive_required",
    ))

    primary_ctas = [button for button in context.user_buttons() if button.get("uxRole") in {"primary-cta", "purchase-cta"}]
    results.append(make_result(
        SHEET, 12,
        "The user always knows what to do first/next.",
        NA,
        0.24,
        "Primary CTAs are useful signals, but they are not enough on their own to prove that the next step is always clear.",
        evidence=[clean_text(button.get("accessibleName") or button.get("text") or "") for button in primary_ctas[:6]],
        decision_basis="proxy",
    ))

    instruction_markers = ["how", "comment", "instructions", "steps", "etapes", "étapes", "please", "veuillez"]
    instruction_hits = [text for text in headings if any(marker in normalize_text(text) for marker in instruction_markers)]
    results.append(make_result(
        SHEET, 13,
        "Instructions specific to the overall process are given at the start of the process.",
        TRUE if instruction_hits else NA,
        0.24,
        "Only judgeable when explicit process-instruction headings are extracted.",
        evidence=instruction_hits[:6],
        decision_basis="proxy",
    ))

    results.append(make_result(
        SHEET, 14,
        "Instructions specific to a particular step are given at the start of that step.",
        NA,
        0.18,
        "Step-level instructional ordering is not recoverable reliably from the current extraction.",
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
        SHEET, 15,
        "Instructions obvious to expert users are hidden but available (e.g. Tool tip).",
        TRUE if tooltip_like else NA,
        0.35,
        "This is only marked TRUE when explicit help/info or tooltip controls are extracted.",
        evidence=tooltip_like[:6],
        decision_basis="direct" if tooltip_like else "proxy",
    ))

    error_fields = [field for field in fields if field.get("required") or field.get("type") in {"email", "tel", "number"}]
    results.append(make_result(
        SHEET, 17,
        "Errors are clear, visually distinct from labels/data and appear in an appropriate location  (e.g. adjacent to data entry field, adjacent to form, etc.).",
        NA,
        0.20,
        "Error-state visibility and location require triggered validation extraction, not only form structure.",
        evidence=[clean_text(field.get("label") or field.get("placeholder") or "") for field in error_fields[:6]],
        decision_basis="interactive_required",
    ))

    results.append(make_result(
        SHEET, 18,
        "Error messages are concise, written in easy to understand language and describe what’s occurred and what action is necessary.",
        NA,
        0.20,
        "No triggered error-message strings were extracted.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    validation_ready = any(field.get("required") for field in fields) or any(field.get("type") in {"email", "tel", "number"} for field in fields)
    results.append(make_result(
        SHEET, 19,
        "Common user errors (e.g. missing fields, invalid formats, invalid selections) have been taken into consideration and where possible prevented.",
        TRUE if validation_ready else NA,
        0.64 if validation_ready else 0.24,
        "Preventive evidence is inferred from required fields and semantic field types on actual user-facing form fields.",
        evidence=[f"{field.get('type')} | {clean_text(field.get('label') or field.get('placeholder') or field.get('name'))}" for field in fields[:10]],
        decision_basis="proxy" if validation_ready else "interactive_required",
    ))

    results.append(make_result(
        SHEET, 21,
        "Help is written in easy to understand language, with terms users will recognize.",
        NA,
        0.18,
        "No dedicated help content was extracted.",
        evidence=[],
        decision_basis="interactive_required",
    ))
    results.append(make_result(
        SHEET, 22,
        "Accessing online help does not stop user progress; they can resume work where they left off after accessing help).",
        NA,
        0.12,
        "This requires interaction-state testing.",
        evidence=[],
        decision_basis="interactive_required",
    ))

    help_evidence = [label for label in context.meaningful_navigation_labels() if "contact" in normalize_text(label)]
    results.append(make_result(
        SHEET, 23,
        "Users can easily get further help (e.g. phone number, live chat, email support).",
        TRUE if context.has_contact_or_help_path() else FALSE if context.pages else NA,
        0.78,
        "Contact/help availability is inferred from meaningful navigation and action labels.",
        evidence=help_evidence[:6],
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

    results.append(make_result(
        SHEET, 26,
        "The system keeps users informed about what has happened, what is happening or what will happen during workflows or upon taking action (visibility of system status).",
        TRUE if feedback_hits or loading_hits else NA,
        0.72,
        "Visibility of system status is inferred from explicit action-feedback or loading/processing signals.",
        evidence=feedback_hits[:4] + loading_hits[:2],
        decision_basis="direct" if feedback_hits or loading_hits else "proxy",
    ))

    return results