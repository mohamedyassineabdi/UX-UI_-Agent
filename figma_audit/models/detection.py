from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from figma_audit.models.issue import AuditIssue


class DetectionConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CriterionDetectionStatus(BaseModel):
    criterion_id: str
    exists: bool = False
    issue_count: int = 0
    confidence: DetectionConfidence | None = None
    detector_ids: list[str] = Field(default_factory=list)


class DetectionRunSummary(BaseModel):
    criteria_total: int
    criteria_with_detected_problems: int
    draft_issue_count: int
    screenshot_count: int
    status: str = "draft_detections_not_final_audit"


class DetectionResult(BaseModel):
    """
    Binary criterion detection output.

    criterion_status answers: does this criterion currently have at least one
    detected problem? draft_issues keeps the evidence behind each positive.
    """

    criterion_status: list[CriterionDetectionStatus]
    draft_issues: list[AuditIssue] = Field(default_factory=list)
    summary: DetectionRunSummary

    @property
    def issues(self) -> list[AuditIssue]:
        """Expose draft issues through the same name used by annotation helpers."""
        return self.draft_issues

    def status_by_id(self) -> dict[str, CriterionDetectionStatus]:
        return {status.criterion_id: status for status in self.criterion_status}

    def refresh_summary(self) -> None:
        self.summary = DetectionRunSummary(
            criteria_total=len(self.criterion_status),
            criteria_with_detected_problems=sum(
                1 for status in self.criterion_status if status.exists
            ),
            draft_issue_count=len(self.draft_issues),
            screenshot_count=sum(len(issue.visual_evidence) for issue in self.draft_issues),
        )
