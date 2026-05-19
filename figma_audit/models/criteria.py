from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CriteriaReference(BaseModel):
    id: str
    name: str
    url: str
    used_for: str


class CriteriaMetadata(BaseModel):
    source_file: str
    generated_on: str
    purpose: str
    status: str
    principles: list[str] = Field(default_factory=list)
    reference_sources: list[CriteriaReference] = Field(default_factory=list)


class ValidatedLogic(BaseModel):
    healthy_signals: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    do_not_flag_when: list[str] = Field(default_factory=list)


class FigmaDetectionSupport(BaseModel):
    can_estimate: list[str] = Field(default_factory=list)
    needs_human_review: list[str] = Field(default_factory=list)


class SeverityLadder(BaseModel):
    high: str
    medium: str
    low: str


class UxUiCriterion(BaseModel):
    id: str
    order: int
    name: str
    short_name: str
    focus: list[str] = Field(default_factory=list)
    validated_definition: str
    core_question: str
    business_impact: str
    user_impact: str
    default_fix: str
    validated_logic: ValidatedLogic
    evidence_expectations: list[str] = Field(default_factory=list)
    figma_detection_support: FigmaDetectionSupport
    severity_ladder: SeverityLadder
    primary_references: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class CrossAxisRules(BaseModel):
    deduplication_priority: list[dict[str, str]] = Field(default_factory=list)
    confidence_levels: dict[str, str] = Field(default_factory=dict)
    formal_accessibility_note: str


class CriteriaCatalog(BaseModel):
    metadata: CriteriaMetadata
    criteria: list[UxUiCriterion]
    cross_axis_rules: CrossAxisRules

    def get(self, criterion_id: str) -> UxUiCriterion:
        for criterion in self.criteria:
            if criterion.id == criterion_id:
                return criterion
        raise KeyError(f"Unknown criterion id: {criterion_id}")

    def criteria_ids(self) -> list[str]:
        return [criterion.id for criterion in self.criteria]

    def reference_ids(self) -> set[str]:
        return {reference.id for reference in self.metadata.reference_sources}

    def validate_links(self) -> list[str]:
        """
        Validate internal references inside the criteria catalog.

        This does not fetch external URLs; it only checks that every referenced
        source id exists in metadata.reference_sources.
        """
        errors: list[str] = []
        reference_ids = self.reference_ids()

        for criterion in self.criteria:
            for reference_id in criterion.primary_references:
                if reference_id not in reference_ids:
                    errors.append(
                        f"{criterion.id} references unknown source '{reference_id}'."
                    )

        return errors

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.metadata.status,
            "criteria_count": len(self.criteria),
            "criteria_ids": self.criteria_ids(),
            "reference_count": len(self.metadata.reference_sources),
        }
