"""Draft issue detection layer, separate from final audit rules."""

from figma_audit.detections.runner import run_detections

__all__ = ["run_detections"]
