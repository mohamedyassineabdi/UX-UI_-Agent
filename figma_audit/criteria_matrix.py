from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AxisCheck:
    axis_id: str
    name: str
    priority: str
    visible_rule: str
    detector_ids: tuple[str, ...]
    analysis_method: str = "rule"
    active: bool = True


def _check(
    axis_id: str,
    name: str,
    priority: str,
    visible_rule: str,
    detector_ids: tuple[str, ...] = (),
    *,
    analysis_method: str = "rule",
    active: bool = True,
) -> AxisCheck:
    return AxisCheck(
        axis_id=axis_id,
        name=name,
        priority=priority,
        visible_rule=visible_rule,
        detector_ids=detector_ids,
        analysis_method=analysis_method,
        active=active,
    )


AXIS_CHECKS: tuple[AxisCheck, ...] = (
    # Axis 1: Performance and task execution.
    _check("task_execution", "Core Web Vitals readiness", "important", "Runtime web audits should include LCP, INP, and CLS evidence when a live page is available.", (), active=False, analysis_method="runtime_web"),
    _check("task_execution", "Lighthouse performance opportunities", "important", "Runtime web audits should identify measurable page speed opportunities such as render blocking, image weight, and unused code.", (), active=False, analysis_method="runtime_web"),
    _check("task_execution", "Clear completion path", "important", "A visible task or form must show how the user finishes it.", ("form_without_completion_action",)),
    _check("task_execution", "Outcome-specific action labels", "important", "Primary actions must say what will happen, not only Continue, Done, or Submit.", ("ambiguous_completion_action",)),
    _check("task_execution", "Recovery for risky actions", "important", "Visible destructive actions need nearby cancel, undo, or confirmation cues.", ("destructive_action_without_recovery",)),
    _check("task_execution", "Required field marking", "important", "Required fields must have a visible required marker or label when the screen shows a form.", ("form_without_completion_action",), active=False, analysis_method="human_review"),
    _check("task_execution", "Input expectation clarity", "important", "Input fields should visibly communicate the expected information through labels, examples, or helper text.", ("form_without_completion_action",)),
    _check("task_execution", "State feedback visibility", "important", "Success, error, loading, and empty states should be visible near the task they affect.", (), active=False, analysis_method="human_review"),
    _check("task_execution", "Related field grouping", "secondary", "Related inputs should be visually grouped so users understand one task section at a time.", ("form_without_completion_action",), active=False, analysis_method="human_review"),
    _check("task_execution", "Validation message proximity", "secondary", "Validation or helper messages should sit close to the field or action they explain.", (), active=False, analysis_method="human_review"),
    _check("task_execution", "Risk consequence clarity", "secondary", "Risky actions should visibly say what will be changed, removed, or submitted.", ("destructive_action_without_recovery",)),
    _check("task_execution", "Multi-step progress cue", "secondary", "Multi-step tasks should show a visible progress, step, or location cue when the current screen implies a sequence.", ("generic_navigation_label",), active=False, analysis_method="human_review"),

    # Axis 2: Navigation and flow.
    _check("flow_architecture", "Specific destination labels", "important", "Visible navigation items must describe different destinations clearly.", ("generic_navigation_label",)),
    _check("flow_architecture", "Primary navigation coding", "important", "Bottom tabs or primary navigation must use visible labels or clearly distinct icons.", ("generic_navigation_label",)),
    _check("flow_architecture", "Back or close affordance", "important", "Detail, modal, and edit-like screens should show an obvious back, close, cancel, or exit control.", ("generic_navigation_label",), active=False, analysis_method="human_review"),
    _check("flow_architecture", "Active location visibility", "important", "Tabs, menus, or step controls should visibly show where the user is.", ("generic_navigation_label",)),
    _check("flow_architecture", "Obvious next step", "important", "The next navigation or task step should be visually clear from the screen.", ("ambiguous_completion_action",)),
    _check("flow_architecture", "Navigation placement consistency", "secondary", "Repeated navigation controls should appear in consistent visible positions across similar screens.", ("component_style_outlier",), active=False, analysis_method="human_review"),
    _check("flow_architecture", "Icon navigation clarity", "secondary", "Icon navigation should include a label or use an unmistakable icon when it controls movement.", ("icon_only_unlabeled_control", "generic_navigation_label")),
    _check("flow_architecture", "Progress or step indicator", "secondary", "Multi-step flows should expose a visible progress cue when the page title or layout implies steps.", ("generic_navigation_label",), active=False, analysis_method="human_review"),
    _check("flow_architecture", "Page grouping and scan order", "secondary", "Visible sections should be grouped and ordered so users can predict the next area.", ("flat_visual_hierarchy",), analysis_method="ai_assisted"),
    _check("flow_architecture", "Repeated labels ambiguity", "important", "Repeated visible labels such as Label or Item should not make navigation choices indistinguishable.", ("generic_navigation_label", "placeholder_or_generic_copy")),

    # Axis 3: Trust and WCAG 2.2 accessibility.
    _check("trust_accessibility", "WCAG 2.2 contrast minimum", "important", "Visible text should meet WCAG contrast thresholds: 4.5:1 for normal text and 3:1 for large text.", ("low_text_contrast",)),
    _check("trust_accessibility", "WCAG 2.2 non-text and status contrast", "important", "Visible option labels, selected values, status text, and timing cues must stay distinguishable when they affect a user decision.", ("low_text_contrast",)),
    _check("trust_accessibility", "WCAG 2.2 text over complex backgrounds", "important", "Text over images, gradients, or visually busy surfaces must remain readable in the captured screenshot.", ("low_text_contrast",), active=False, analysis_method="human_review"),
    _check("trust_accessibility", "Readable important text", "important", "Important labels, buttons, prices, status, and instructions should not be too small to read on mobile.", ("small_text_readability",)),
    _check("trust_accessibility", "Disabled-looking active controls", "important", "Clickable controls should not look faded, disabled, or inactive when they are intended to be used.", ("low_text_contrast",), active=False, analysis_method="human_review"),
    _check("trust_accessibility", "WCAG 2.2 target size", "important", "Visible controls should be large enough to tap reliably and should respect WCAG 2.2 Target Size guidance.", ("small_touch_target",)),
    _check("trust_accessibility", "WCAG 2.2 target spacing", "important", "Interactive controls should not sit so close together that accidental taps become likely.", ("crowded_touch_target",)),
    _check("trust_accessibility", "Edge-safe controls", "secondary", "Important controls should not be placed too close to the screen edge or system gesture areas.", ("crowded_touch_target",)),
    _check("trust_accessibility", "Accessible name clarity", "important", "Icon-only controls need visible labels, unmistakable symbols, or accessible-name evidence when the meaning is not obvious.", ("icon_only_unlabeled_control",)),
    _check("trust_accessibility", "Color-only status", "secondary", "Important status, warning, success, or error information should not rely on color alone.", ("icon_only_unlabeled_control",), active=False, analysis_method="human_review"),
    _check("trust_accessibility", "WCAG 2.2 focus appearance", "secondary", "Selected, active, or focused states should be visually clear enough to identify.", ("component_style_outlier",), active=False, analysis_method="human_review"),
    _check("trust_accessibility", "Dense text spacing", "secondary", "Dense text blocks should have enough line spacing and structure to scan on mobile.", ("small_text_readability", "placeholder_or_generic_copy")),

    # Axis 4: Visual brand, UI consistency, and component behavior.
    _check("ui_consistency", "Repeated control style consistency", "important", "Equivalent visible controls should not have one unexplained style outlier.", ("component_style_outlier",)),
    _check("ui_consistency", "Repeated action wording consistency", "important", "Equivalent visible actions should use the same wording unless meaning changes.", ("component_style_outlier",)),
    _check("ui_consistency", "Icon style consistency", "important", "Icons with the same purpose should use consistent size, stroke, fill, and container treatment.", ("component_style_outlier",), active=False, analysis_method="human_review"),
    _check("ui_consistency", "Repeated spacing consistency", "important", "Repeated controls, cards, and list rows should keep consistent visible spacing.", ("component_style_outlier",)),
    _check("ui_consistency", "Repeated layout consistency", "important", "Cards or list items with the same role should not unexpectedly change layout.", ("component_style_outlier",)),
    _check("ui_consistency", "State variation clarity", "secondary", "Selected, disabled, danger, and default states should be visually distinguishable and intentional.", ("component_style_outlier",), active=False, analysis_method="human_review"),
    _check("ui_consistency", "Color meaning consistency", "secondary", "The same color should not represent different meanings in the same visible screen.", ("component_style_outlier",), active=False, analysis_method="human_review"),
    _check("ui_consistency", "Mixed design-system treatment", "secondary", "Components from different visual systems should not appear mixed without a visible reason.", ("component_style_outlier",), analysis_method="ai_assisted"),
    _check("ui_consistency", "Repeated radius and shadow consistency", "secondary", "Repeated components should keep consistent radius, shadow, border, and elevation treatment.", ("component_style_outlier",)),
    _check("ui_consistency", "Alignment consistency", "secondary", "Repeated items should align consistently unless a visible state or hierarchy explains the difference.", ("component_style_outlier",), active=False, analysis_method="human_review"),
    _check("ui_consistency", "Clear visual hierarchy", "important", "Visible screens need a clear focal point and readable priority order.", ("flat_visual_hierarchy",)),
    _check("ui_consistency", "Primary action dominance", "important", "A primary action should be visually dominant when the screen expects the user to act.", ("flat_visual_hierarchy",), active=False, analysis_method="human_review"),
    _check("ui_consistency", "Competing element control", "important", "Too many similarly strong elements should not compete for first attention.", ("flat_visual_hierarchy",)),
    _check("ui_consistency", "Visible starting point", "important", "The screen should have a clear first element to read or act on.", ("flat_visual_hierarchy",)),
    _check("ui_consistency", "Heading size contrast", "important", "Headings, subheadings, and body text should have visibly different priority.", ("flat_visual_hierarchy",)),
    _check("ui_consistency", "Balanced first-glance composition", "secondary", "Large visible surfaces should not feel accidentally lopsided or visually heavy.", ("flat_visual_hierarchy",), analysis_method="ai_assisted"),

    # Axis 5: Content clarity and microcopy.
    _check("content_microcopy", "No placeholder copy", "important", "Client-facing screens should not show template words like Heading, Label, Description, Menu Item, or App Name.", ("placeholder_or_generic_copy",)),
    _check("content_microcopy", "Action copy explains outcome", "important", "Buttons and CTAs should explain the result of tapping them.", ("placeholder_or_generic_copy", "ambiguous_completion_action")),
    _check("content_microcopy", "Specific page title", "important", "The visible page title should explain the current screen instead of using Title, Heading, or a vague label.", ("placeholder_or_generic_copy",)),
    _check("content_microcopy", "Clear form field labels", "important", "Visible fields should use understandable labels rather than icons or placeholders alone.", ("placeholder_or_generic_copy", "form_without_completion_action")),
    _check("content_microcopy", "Truncated or clipped content", "important", "Important text should not be visibly cut off, ellipsized, or clipped.", ("placeholder_or_generic_copy",)),
    _check("content_microcopy", "Screen purpose copy", "important", "The visible text should explain the product, service, or screen purpose when the screen depends on it.", ("placeholder_or_generic_copy",)),
    _check("content_microcopy", "Repeated label ambiguity", "important", "Repeated visible labels such as Label or Item should not make content choices ambiguous.", ("placeholder_or_generic_copy",)),
    _check("content_microcopy", "Semantic plain-language meaning", "secondary", "The LLM should judge whether visible text is meaningful, concrete, and understandable in context, not only whether it matches placeholder words.", ("placeholder_or_generic_copy",), analysis_method="ai_assisted"),
    _check("content_microcopy", "Dense copy structure", "secondary", "Long text blocks should be broken into readable headings, sentences, or bullets.", ("placeholder_or_generic_copy",)),
    _check("content_microcopy", "Text hierarchy clarity", "secondary", "Copy styling should make the label, value, and explanation roles visually clear.", ("flat_visual_hierarchy",), active=False, analysis_method="human_review"),

)


def checks_by_axis() -> dict[str, list[AxisCheck]]:
    grouped: dict[str, list[AxisCheck]] = {}
    for check in AXIS_CHECKS:
        grouped.setdefault(check.axis_id, []).append(check)
    return grouped
