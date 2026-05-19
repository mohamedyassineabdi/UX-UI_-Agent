from __future__ import annotations

from figma_audit.models.audit_result import AuditResult
from figma_audit.models.normalized_models import NormalizedFigmaFile


def run_audit(normalized_file: NormalizedFigmaFile) -> AuditResult:
    """
    Return an empty audit result.

    Criteria verification is intentionally disabled for now. The pipeline still
    fetches and normalizes Figma data so future rules can be added cleanly.
    """
    _ = normalized_file
    return AuditResult()
