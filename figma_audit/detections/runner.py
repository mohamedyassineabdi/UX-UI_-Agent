from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from figma_audit.config import DETECTION_WORKERS
from figma_audit.criteria_catalog import load_criteria_catalog
from figma_audit.detections.content_microcopy import detect_placeholder_or_generic_copy
from figma_audit.detections.flow_architecture import detect_generic_navigation_labels
from figma_audit.detections.task_execution import detect_destructive_action_without_recovery
from figma_audit.detections.trust_accessibility import detect_low_text_contrast
from figma_audit.detections.ui_consistency import detect_component_style_outlier
from figma_audit.detections.visual_brand import detect_flat_visual_hierarchy
from figma_audit.models.detection import (
    CriterionDetectionStatus,
    DetectionConfidence,
    DetectionResult,
    DetectionRunSummary,
)
from figma_audit.models.issue import AuditIssue
from figma_audit.models.normalized_models import NormalizedFigmaFile
from figma_audit.utils.progress import progress_bar


DetectorOutput = tuple[str, list[AuditIssue]]
Detector = tuple[str, Callable[[NormalizedFigmaFile], list[AuditIssue]]]


def _confidence_rank(value: DetectionConfidence | None) -> int:
    if value == DetectionConfidence.HIGH:
        return 3
    if value == DetectionConfidence.MEDIUM:
        return 2
    if value == DetectionConfidence.LOW:
        return 1
    return 0


def _issue_confidence(issue: AuditIssue) -> DetectionConfidence:
    raw_value = issue.evidence.get("confidence", DetectionConfidence.LOW.value)
    try:
        return DetectionConfidence(str(raw_value))
    except ValueError:
        return DetectionConfidence.LOW


def _run_detector(detector: Detector, normalized_file: NormalizedFigmaFile) -> DetectorOutput:
    detector_id, detector_fn = detector
    return detector_id, detector_fn(normalized_file)


def run_detections(
    normalized_file: NormalizedFigmaFile,
    *,
    workers: int | None = None,
    log: Callable[[str], None] | None = None,
) -> DetectionResult:
    """
    Run draft detectors and return binary criterion statuses.

    This is intentionally separate from final audit rules. Detections are
    evidence candidates, not final scored audit findings.
    """
    catalog = load_criteria_catalog()
    criterion_ids = catalog.criteria_ids()

    detectors: list[Detector] = [
        ("destructive_action_without_recovery", detect_destructive_action_without_recovery),
        ("generic_navigation_label", detect_generic_navigation_labels),
        ("low_text_contrast", detect_low_text_contrast),
        ("component_style_outlier", detect_component_style_outlier),
        ("flat_visual_hierarchy", detect_flat_visual_hierarchy),
        ("placeholder_or_generic_copy", detect_placeholder_or_generic_copy),
    ]
    worker_count = max(1, min(workers or DETECTION_WORKERS, len(detectors)))
    if log is not None:
        log(f"Running {len(detectors)} detectors with {worker_count} worker(s)...")

    if worker_count == 1:
        detector_outputs = []
        for index, detector in enumerate(detectors, start=1):
            detector_output = _run_detector(detector, normalized_file)
            detector_outputs.append(detector_output)
            if log is not None:
                detector_id, issues = detector_output
                log(f"Detectors {progress_bar(index, len(detectors))} {detector_id}: {len(issues)} issue(s)")
    else:
        detector_outputs_by_index: dict[int, DetectorOutput] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_run_detector, detector, normalized_file): index
                for index, detector in enumerate(detectors)
            }
            completed_count = 0
            for future in as_completed(futures):
                completed_count += 1
                detector_output = future.result()
                detector_outputs_by_index[futures[future]] = detector_output
                if log is not None:
                    detector_id, issues = detector_output
                    log(
                        f"Detectors {progress_bar(completed_count, len(detectors))} "
                        f"{detector_id}: {len(issues)} issue(s)"
                    )

        detector_outputs = [
            detector_outputs_by_index[index] for index in range(len(detectors))
        ]

    issues_by_criterion: dict[str, list[AuditIssue]] = defaultdict(list)
    detectors_by_criterion: dict[str, set[str]] = defaultdict(set)

    for detector_id, issues in detector_outputs:
        for issue in issues:
            if not issue.criterion:
                continue
            issues_by_criterion[issue.criterion].append(issue)
            issue_detector_id = str(issue.evidence.get("detector_id") or detector_id)
            detectors_by_criterion[issue.criterion].add(issue_detector_id)

    criterion_status: list[CriterionDetectionStatus] = []
    draft_issues: list[AuditIssue] = []

    for criterion_id in criterion_ids:
        issues = issues_by_criterion.get(criterion_id, [])
        draft_issues.extend(issues)

        best_confidence: DetectionConfidence | None = None
        for issue in issues:
            confidence = _issue_confidence(issue)
            if _confidence_rank(confidence) > _confidence_rank(best_confidence):
                best_confidence = confidence

        criterion_status.append(
            CriterionDetectionStatus(
                criterion_id=criterion_id,
                exists=bool(issues),
                issue_count=len(issues),
                confidence=best_confidence,
                detector_ids=sorted(detectors_by_criterion.get(criterion_id, set())),
            )
        )

    summary = DetectionRunSummary(
        criteria_total=len(criterion_status),
        criteria_with_detected_problems=sum(1 for status in criterion_status if status.exists),
        draft_issue_count=len(draft_issues),
        screenshot_count=sum(len(issue.visual_evidence) for issue in draft_issues),
    )

    return DetectionResult(
        criterion_status=criterion_status,
        draft_issues=draft_issues,
        summary=summary,
    )
