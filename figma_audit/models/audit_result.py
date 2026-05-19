from __future__ import annotations

from pydantic import BaseModel, Field

from figma_audit.models.issue import AuditIssue, Severity


class SeveritySummary(BaseModel):
    """
    Count of issues by severity level.
    """
    low: int = 0
    medium: int = 0
    high: int = 0


class AuditResult(BaseModel):
    """
    Final container for all issues produced by the audit engine.
    """
    issues: list[AuditIssue] = Field(default_factory=list)

    def add_issue(self, issue: AuditIssue) -> None:
        """
        Add one issue to the result set.
        """
        self.issues.append(issue)

    def total_issues(self) -> int:
        """
        Total number of issues found.
        """
        return len(self.issues)

    def severity_summary(self) -> SeveritySummary:
        """
        Compute counts by severity.
        """
        summary = SeveritySummary()

        for issue in self.issues:
            if issue.severity == Severity.LOW:
                summary.low += 1
            elif issue.severity == Severity.MEDIUM:
                summary.medium += 1
            elif issue.severity == Severity.HIGH:
                summary.high += 1

        return summary