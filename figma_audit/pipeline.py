from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from figma_audit.annotations import annotate_issue_screenshots
from figma_audit.audit.audit_runner import run_audit
from figma_audit.config import (
    ANNOTATIONS_OUTPUT_DIR,
    AUDIT_PARTS_OUTPUT_DIR,
    DETECTIONS_OUTPUT_DIR,
    EXTRACTED_OUTPUT_DIR,
    FINAL_RESULT_OUTPUT_PATH,
    MAX_RUNTIME_SECONDS,
    NORMALIZED_OUTPUT_DIR,
    RAW_OUTPUT_DIR,
    RUN_STATUS_OUTPUT_PATH,
    ensure_output_dirs,
    validate_config,
)
from figma_audit.detections import run_detections
from figma_audit.evidence_quality import attach_evidence_quality
from figma_audit.extraction import build_audit_extraction, normalized_file_from_audit_extraction
from figma_audit.ingestion.figma_client import FigmaClient, FigmaRateLimitError
from figma_audit.ingestion.fetch_service import fetch_figma_bundle
from figma_audit.ingestion.url_parser import parse_figma_url
from figma_audit.models.audit_result import AuditResult
from figma_audit.models.detection import DetectionResult
from figma_audit.models.normalized_models import NormalizedFigmaFile
from figma_audit.models.raw_bundle import RawFigmaBundle
from figma_audit.normalization.normalizer import normalize_figma_bundle
from figma_audit.run_artifacts import RunArtifacts
from figma_audit.utils.io import (
    load_json,
    save_json,
    save_normalized_file,
    save_raw_bundle,
    split_file_by_lines,
)


@dataclass(frozen=True)
class AuditPipelineOutputs:
    raw_bundle: RawFigmaBundle
    normalized_file: NormalizedFigmaFile
    audit_result: AuditResult
    detection_result: DetectionResult
    raw_output_path: Path
    normalized_output_path: Path
    audit_extraction_output_path: Path
    audit_output_path: Path
    detections_output_path: Path
    final_result_output_path: Path
    run_status_output_path: Path
    audit_parts_output_dir: Path
    annotations_output_dir: Path


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{int(minutes)}m {remaining_seconds:.0f}s"


def _emit(log: object, message: str) -> None:
    if callable(log):
        log(message)


def _ensure_runtime_budget(started_at: float, max_runtime_seconds: int, next_stage: str) -> None:
    elapsed = perf_counter() - started_at
    if elapsed > max_runtime_seconds:
        raise TimeoutError(
            f"Maximum runtime of {max_runtime_seconds}s was exceeded before {next_stage}. "
            "Partial output files were written; rerun with a smaller node selection, "
            "--no-annotations, --max-annotations, or a higher MAX_RUNTIME_SECONDS value."
        )


def _raw_extraction_summary(raw_bundle: RawFigmaBundle) -> dict[str, object]:
    document = raw_bundle.raw_file.get("document", {})
    return {
        "source_url": raw_bundle.source_url,
        "file_key": raw_bundle.file_key,
        "node_id": raw_bundle.node_id,
        "file_name": raw_bundle.raw_file.get("name"),
        "root_node_id": document.get("id") if isinstance(document, dict) else None,
        "root_node_name": document.get("name") if isinstance(document, dict) else None,
        "root_node_type": document.get("type") if isinstance(document, dict) else None,
        "has_variables": raw_bundle.has_variables(),
        "warnings": raw_bundle.warnings,
    }


def _load_cached_raw_bundle(
    *,
    figma_url: str,
    raw_output_path: Path,
    fetch_error: Exception | None,
    cache_reason: str = "Cache-first requested",
    log: object,
) -> RawFigmaBundle | None:
    try:
        parsed = parse_figma_url(figma_url)
    except Exception as exc:
        _emit(log, f"Could not parse Figma URL for cache lookup: {exc}")
        return None

    checked_any_cache = False
    for cache_path in _raw_cache_candidates(
        raw_output_path=raw_output_path,
        file_key=str(parsed["file_key"]),
        node_id=parsed["node_id"],
    ):
        if not cache_path.exists():
            continue
        checked_any_cache = True
        try:
            cached_bundle = RawFigmaBundle.model_validate(load_json(cache_path))
        except Exception as exc:
            _emit(log, f"Cached raw output is not usable: {cache_path}: {exc}")
            continue

        if (
            cached_bundle.file_key != parsed["file_key"]
            or cached_bundle.node_id != parsed["node_id"]
        ):
            _emit(log, f"Cached raw output does not match requested file/node: {cache_path}")
            continue

        if fetch_error is None:
            warning = f"{cache_reason}; reused cached raw output from {cache_path}."
        else:
            warning = (
                f"Live Figma fetch failed; reused cached raw output from {cache_path}: "
                f"{fetch_error}"
            )
        if warning not in cached_bundle.warnings:
            cached_bundle.warnings.append(warning)
        _emit(log, warning)

        return cached_bundle

    if not checked_any_cache:
        return None
    _emit(log, "No matching cached raw output was found for the requested file/node.")
    return None


def _format_wait(seconds: float | None) -> str:
    if seconds is None:
        return "the retry window Figma returned"
    if seconds < 3600:
        return f"{seconds / 60:.0f} minute(s)"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} hour(s)"
    return f"{seconds / 86400:.1f} day(s)"


def _missing_cache_rate_limit_error(
    *,
    figma_url: str,
    raw_output_path: Path,
    fetch_error: FigmaRateLimitError,
) -> RuntimeError:
    try:
        parsed = parse_figma_url(figma_url)
        file_key = str(parsed["file_key"])
        node_id = parsed["node_id"]
        expected_cache_path = _raw_bundle_cache_path(
            raw_output_path=raw_output_path,
            file_key=file_key,
            node_id=node_id,
        )
    except Exception:
        file_key = "unknown"
        node_id = None
        expected_cache_path = raw_output_path.parent / "cache"

    return RuntimeError(
        "Figma is rate-limiting this request and no matching raw cache exists yet. "
        f"Requested file_key={file_key}, node_id={node_id or 'full-file'}. "
        f"Figma asked to wait about {_format_wait(fetch_error.retry_after_seconds)}. "
        f"Expected cache file: {expected_cache_path}. "
        "To audit this link accurately, either wait for the Figma API quota to reset, "
        "set FIGMA_TOKEN or FIGMA_TOKENS to another authorized token with access/quota, "
        "or place a matching raw_bundle.json at that cache path. "
        "The audit cannot safely reuse a cache from another Figma file."
    )


def _missing_cache_only_error(*, figma_url: str, raw_output_path: Path) -> RuntimeError:
    try:
        parsed = parse_figma_url(figma_url)
        file_key = str(parsed["file_key"])
        node_id = parsed["node_id"]
        expected_cache_path = _raw_bundle_cache_path(
            raw_output_path=raw_output_path,
            file_key=file_key,
            node_id=node_id,
        )
    except Exception:
        file_key = "unknown"
        node_id = None
        expected_cache_path = raw_output_path.parent / "cache"

    return RuntimeError(
        "Cache-only mode requested, but no matching raw cache exists. "
        f"Requested file_key={file_key}, node_id={node_id or 'full-file'}. "
        f"Expected cache file: {expected_cache_path}. "
        "No live Figma API request was made. Run once later without --cache-only "
        "when quota is available, or place the matching raw_bundle.json at that cache path."
    )


def _safe_cache_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _raw_bundle_cache_path(*, raw_output_path: Path, file_key: str, node_id: str | None) -> Path:
    node_label = _safe_cache_name(node_id or "full-file")
    return raw_output_path.parent / "cache" / f"{_safe_cache_name(file_key)}__node-{node_label}.json"


def _raw_cache_candidates(
    *,
    raw_output_path: Path,
    file_key: str,
    node_id: str | None,
) -> list[Path]:
    indexed_path = _raw_bundle_cache_path(
        raw_output_path=raw_output_path,
        file_key=file_key,
        node_id=node_id,
    )
    if indexed_path == raw_output_path:
        return [raw_output_path]
    return [indexed_path, raw_output_path]


def _normalized_extraction_summary(normalized_file: NormalizedFigmaFile) -> dict[str, object]:
    return {
        "file_key": normalized_file.file_key,
        "file_name": normalized_file.file_name,
        "last_modified": normalized_file.last_modified,
        "version": normalized_file.version,
        "editor_type": normalized_file.editor_type,
        "pages": len(normalized_file.pages),
        "frames": len(normalized_file.frames),
        "nodes": len(normalized_file.nodes),
        "components": len(normalized_file.components),
        "tokens": len(normalized_file.tokens),
        "warnings": normalized_file.warnings,
    }


def _audit_summary(audit_result: AuditResult) -> dict[str, object]:
    severity_summary = audit_result.severity_summary()
    return {
        "total_issues": audit_result.total_issues(),
        "severity_summary": severity_summary.model_dump(mode="json"),
    }


def _final_result_payload(
    *,
    raw_bundle: RawFigmaBundle,
    normalized_file: NormalizedFigmaFile,
    audit_extraction: dict[str, object],
    audit_result: AuditResult,
    detection_result: DetectionResult,
    validation_quality: dict[str, object],
    output_paths: dict[str, Path],
) -> dict[str, object]:
    return {
        "status": "completed",
        "source_url": raw_bundle.source_url,
        "file_key": normalized_file.file_key,
        "node_id": raw_bundle.node_id,
        "extraction": _normalized_extraction_summary(normalized_file),
        "audit_extraction": {
            "schema_version": audit_extraction.get("schema_version"),
            "counts": audit_extraction.get("counts", {}),
            "completeness_note": audit_extraction.get("completeness_note"),
        },
        "audit": {
            **_audit_summary(audit_result),
            "issues": audit_result.model_dump(mode="json").get("issues", []),
        },
        "draft_analysis": detection_result.model_dump(mode="json"),
        "validation_quality": validation_quality,
        "warnings": [*raw_bundle.warnings, *normalized_file.warnings],
        "outputs": {key: str(value) for key, value in output_paths.items()},
    }


def run_pipeline(
    figma_url: str,
    *,
    save_outputs: bool = True,
    split_audit_output: bool = True,
    run_draft_detections: bool = True,
    annotate_issues: bool = True,
    max_annotations: int | None = None,
    annotation_workers: int | None = None,
    detection_workers: int | None = None,
    annotation_render_scope: str = "context",
    allow_annotation_preview_fallback: bool = True,
    use_cached_fetch_on_error: bool = True,
    prefer_cached_fetch: bool = False,
    cache_only_fetch: bool = False,
    fetch_variables: bool | None = None,
    max_runtime_seconds: int | None = MAX_RUNTIME_SECONDS,
    verbose: bool = False,
    raw_output_path: Path | None = None,
    normalized_output_path: Path | None = None,
    audit_extraction_output_path: Path | None = None,
    audit_output_path: Path | None = None,
    detections_output_path: Path | None = None,
    audit_parts_output_dir: Path | None = None,
    annotations_output_dir: Path | None = None,
    final_result_output_path: Path | None = None,
    run_status_output_path: Path | None = None,
) -> AuditPipelineOutputs:
    """
    Run the complete Figma audit pipeline.

    This function is the integration-friendly API for larger projects. Callers
    can use the returned models directly, disable disk writes, or provide their
    own output paths.
    """
    pipeline_started_at = perf_counter()
    max_runtime_seconds = max_runtime_seconds or MAX_RUNTIME_SECONDS
    validate_config(require_figma_token=(not cache_only_fetch or annotate_issues))

    log = print if verbose else None

    raw_output_path = raw_output_path or RAW_OUTPUT_DIR / "raw_bundle.json"
    normalized_output_path = normalized_output_path or NORMALIZED_OUTPUT_DIR / "normalized_file.json"
    audit_extraction_output_path = audit_extraction_output_path or EXTRACTED_OUTPUT_DIR / "audit_info.json"
    audit_output_path = audit_output_path or NORMALIZED_OUTPUT_DIR / "audit_result.json"
    detections_output_path = detections_output_path or DETECTIONS_OUTPUT_DIR / "draft_detections.json"
    final_result_output_path = final_result_output_path or FINAL_RESULT_OUTPUT_PATH
    run_status_output_path = run_status_output_path or RUN_STATUS_OUTPUT_PATH
    audit_parts_output_dir = audit_parts_output_dir or AUDIT_PARTS_OUTPUT_DIR
    annotations_output_dir = annotations_output_dir or ANNOTATIONS_OUTPUT_DIR

    tracker = RunArtifacts(
        source_url=figma_url,
        status_path=run_status_output_path,
        final_result_path=final_result_output_path,
    )
    if save_outputs:
        ensure_output_dirs()
        tracker.write_status()

    current_stage: str | None = None

    try:
        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "fetch")
        current_stage = "fetch"
        tracker.start_stage(current_stage)
        stage_started_at = perf_counter()
        cache_used = False
        live_fetch_error: Exception | None = None
        raw_bundle: RawFigmaBundle | None = None

        if prefer_cached_fetch or cache_only_fetch:
            cached_bundle = _load_cached_raw_bundle(
                figma_url=figma_url,
                raw_output_path=raw_output_path,
                fetch_error=None,
                cache_reason=(
                    "Cache-only requested" if cache_only_fetch else "Cache-first requested"
                ),
                log=log,
            )
            if cached_bundle is not None:
                raw_bundle = cached_bundle
                cache_used = True
            elif cache_only_fetch:
                raise _missing_cache_only_error(
                    figma_url=figma_url,
                    raw_output_path=raw_output_path,
                )

        if raw_bundle is None:
            try:
                raw_bundle = fetch_figma_bundle(
                    figma_url,
                    fetch_variables=fetch_variables,
                    log=log,
                )
            except Exception as exc:
                live_fetch_error = exc
                cached_bundle = (
                    _load_cached_raw_bundle(
                        figma_url=figma_url,
                        raw_output_path=raw_output_path,
                        fetch_error=exc,
                        log=log,
                    )
                    if use_cached_fetch_on_error
                    else None
                )
                if cached_bundle is None:
                    if isinstance(exc, FigmaRateLimitError):
                        raise _missing_cache_rate_limit_error(
                            figma_url=figma_url,
                            raw_output_path=raw_output_path,
                            fetch_error=exc,
                        ) from exc
                    raise
                raw_bundle = cached_bundle
                cache_used = True

        if raw_bundle is None:
            raise RuntimeError("Figma fetch did not return data.")
        raw_summary_path = raw_output_path.parent / "extraction_summary.json"
        raw_summary = _raw_extraction_summary(raw_bundle)
        indexed_raw_cache_path = _raw_bundle_cache_path(
            raw_output_path=raw_output_path,
            file_key=raw_bundle.file_key,
            node_id=raw_bundle.node_id,
        )
        raw_summary["indexed_cache_path"] = str(indexed_raw_cache_path)
        if cache_used:
            raw_summary["cache_used"] = True
            if cache_only_fetch:
                raw_summary["cache_mode"] = "cache_only"
            else:
                raw_summary["cache_mode"] = "cache_first" if live_fetch_error is None else "fallback_after_live_fetch_failure"
            if live_fetch_error is not None:
                raw_summary["live_fetch_error"] = str(live_fetch_error)
        if save_outputs:
            save_raw_bundle(raw_output_path, raw_bundle)
            save_raw_bundle(indexed_raw_cache_path, raw_bundle)
            save_json(raw_summary_path, raw_summary)
        tracker.complete_stage(
            "fetch",
            output_files=[raw_output_path, indexed_raw_cache_path, raw_summary_path]
            if save_outputs
            else [],
            details=raw_summary,
        )
        _emit(log, f"Fetch completed in {_format_duration(perf_counter() - stage_started_at)}")

        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "normalization")
        current_stage = "normalization"
        tracker.start_stage(current_stage)
        stage_started_at = perf_counter()
        _emit(log, "Normalizing Figma file...")
        normalized_file = normalize_figma_bundle(raw_bundle)
        normalized_summary_path = normalized_output_path.parent / "extraction_summary.json"
        normalized_summary = _normalized_extraction_summary(normalized_file)
        if save_outputs:
            save_normalized_file(normalized_output_path, normalized_file)
            save_json(normalized_summary_path, normalized_summary)
        tracker.complete_stage(
            "normalization",
            output_files=[normalized_output_path, normalized_summary_path] if save_outputs else [],
            details=normalized_summary,
        )
        _emit(
            log,
            "Normalization completed in "
            f"{_format_duration(perf_counter() - stage_started_at)} "
            f"({len(normalized_file.nodes)} nodes, {len(normalized_file.frames)} frames)",
        )

        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "audit info extraction")
        current_stage = "audit_extraction"
        tracker.start_stage(current_stage)
        stage_started_at = perf_counter()
        _emit(log, "Extracting complete audit input from normalized data...")
        audit_extraction = build_audit_extraction(normalized_file)
        analysis_input_file = normalized_file_from_audit_extraction(audit_extraction)
        if save_outputs:
            save_json(audit_extraction_output_path, audit_extraction)
        tracker.complete_stage(
            "audit_extraction",
            output_files=[audit_extraction_output_path] if save_outputs else [],
            details={
                "analysis_input": str(audit_extraction_output_path),
                "counts": audit_extraction.get("counts", {}),
            },
        )
        _emit(
            log,
            "Audit input extraction completed in "
            f"{_format_duration(perf_counter() - stage_started_at)} "
            f"({audit_extraction.get('counts', {}).get('nodes', 0)} nodes indexed)",
        )

        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "audit")
        current_stage = "audit"
        tracker.start_stage(current_stage)
        stage_started_at = perf_counter()
        _emit(log, "Running audit rules from extracted audit input...")
        audit_result = run_audit(analysis_input_file)
        audit_summary_path = audit_output_path.parent / "audit_summary.json"
        if save_outputs:
            save_json(audit_output_path, audit_result.model_dump(mode="json"))
            save_json(audit_summary_path, _audit_summary(audit_result))
        tracker.complete_stage(
            "audit",
            output_files=[audit_output_path, audit_summary_path] if save_outputs else [],
            details=_audit_summary(audit_result),
        )
        _emit(log, f"Audit completed in {_format_duration(perf_counter() - stage_started_at)}")

        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "draft analysis")
        current_stage = "draft_analysis"
        tracker.start_stage(current_stage)
        stage_started_at = perf_counter()
        detection_result = (
            run_detections(analysis_input_file, workers=detection_workers, log=log)
            if run_draft_detections
            else run_detections(
                NormalizedFigmaFile(file_key=analysis_input_file.file_key),
                workers=detection_workers,
                log=log,
            )
        )
        analysis_summary_path = detections_output_path.parent / "analysis_summary.json"
        if save_outputs:
            save_json(detections_output_path, detection_result.model_dump(mode="json"))
            save_json(analysis_summary_path, detection_result.summary.model_dump(mode="json"))
        tracker.complete_stage(
            "draft_analysis",
            output_files=[detections_output_path, analysis_summary_path] if save_outputs else [],
            details=detection_result.summary.model_dump(mode="json"),
        )
        _emit(log, f"Detections completed in {_format_duration(perf_counter() - stage_started_at)}")

        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "annotations")
        current_stage = "annotations"
        tracker.start_stage(
            current_stage,
            details={
                "enabled": annotate_issues,
                "max_annotations": max_annotations,
                "annotation_workers": annotation_workers,
            },
        )
        stage_started_at = perf_counter()
        annotation_client = (
            FigmaClient(log=log)
            if annotate_issues and (detection_result.draft_issues or audit_result.issues)
            else None
        )
        if annotate_issues and detection_result.draft_issues:
            _emit(log, f"Annotating {len(detection_result.draft_issues)} draft detection issue(s)...")
            annotate_issue_screenshots(
                normalized_file=analysis_input_file,
                audit_result=detection_result,
                output_dir=annotations_output_dir,
                client=annotation_client,
                max_images=max_annotations,
                workers=annotation_workers,
                render_scope=annotation_render_scope,
                allow_preview_fallback=allow_annotation_preview_fallback,
                log=log,
            )
            detection_result.refresh_summary()
            if save_outputs:
                save_json(detections_output_path, detection_result.model_dump(mode="json"))
        if annotate_issues and audit_result.issues:
            _emit(log, f"Annotating {len(audit_result.issues)} audit issue(s)...")
            annotate_issue_screenshots(
                normalized_file=analysis_input_file,
                audit_result=audit_result,
                output_dir=annotations_output_dir,
                client=annotation_client,
                max_images=max_annotations,
                workers=annotation_workers,
                render_scope=annotation_render_scope,
                allow_preview_fallback=allow_annotation_preview_fallback,
                log=log,
            )
            if save_outputs:
                save_json(audit_output_path, audit_result.model_dump(mode="json"))
        evidence_quality_path = detections_output_path.parent / "evidence_quality.json"
        validation_quality = attach_evidence_quality(
            normalized_file=analysis_input_file,
            detection_result=detection_result,
            source_url=figma_url,
            node_id=raw_bundle.node_id,
        )
        if save_outputs:
            save_json(detections_output_path, detection_result.model_dump(mode="json"))
            save_json(evidence_quality_path, validation_quality)
        tracker.complete_stage(
            "annotations",
            output_files=[annotations_output_dir, detections_output_path, audit_output_path, evidence_quality_path] if save_outputs else [],
            details={
                "screenshot_count": detection_result.summary.screenshot_count,
                "strong_issue_count": validation_quality.get("evidence_summary", {}).get("strong_issue_count")
                if isinstance(validation_quality.get("evidence_summary"), dict)
                else None,
                "duration": _format_duration(perf_counter() - stage_started_at),
            },
        )
        _emit(log, f"Annotations completed in {_format_duration(perf_counter() - stage_started_at)}")

        _ensure_runtime_budget(pipeline_started_at, max_runtime_seconds, "finalization")
        current_stage = "finalization"
        tracker.start_stage(current_stage)
        stage_started_at = perf_counter()
        if split_audit_output:
            split_file_by_lines(
                input_path=audit_output_path,
                output_dir=audit_parts_output_dir,
                parts=10,
                output_prefix="audit_lines_part",
                log=log,
            )
        final_payload = _final_result_payload(
            raw_bundle=raw_bundle,
            normalized_file=analysis_input_file,
            audit_extraction=audit_extraction,
            audit_result=audit_result,
            detection_result=detection_result,
            validation_quality=validation_quality,
            output_paths={
                "raw_bundle": raw_output_path,
                "raw_extraction_summary": raw_summary_path,
                "normalized_file": normalized_output_path,
                "normalized_extraction_summary": normalized_summary_path,
                "audit_extraction": audit_extraction_output_path,
                "audit_result": audit_output_path,
                "audit_summary": audit_summary_path,
                "draft_detections": detections_output_path,
                "analysis_summary": analysis_summary_path,
                "evidence_quality": evidence_quality_path,
                "annotations_dir": annotations_output_dir,
                "audit_parts_dir": audit_parts_output_dir,
                "run_status": run_status_output_path,
                "final_result": final_result_output_path,
            },
        )
        if save_outputs:
            save_json(final_result_output_path, final_payload)
        tracker.complete_stage(
            "finalization",
            output_files=[final_result_output_path, audit_parts_output_dir] if save_outputs else [],
            details={"final_result_path": str(final_result_output_path)},
        )

        tracker.complete_run(
            details={
                "final_result_path": str(final_result_output_path),
                "draft_issue_count": detection_result.summary.draft_issue_count,
                "audit_issue_count": audit_result.total_issues(),
            }
        )
        _emit(log, f"Outputs finalized in {_format_duration(perf_counter() - stage_started_at)}")
        _emit(log, f"Pipeline completed in {_format_duration(perf_counter() - pipeline_started_at)}")

    except Exception as exc:
        tracker.fail_stage(current_stage or "pipeline", str(exc))
        raise

    return AuditPipelineOutputs(
        raw_bundle=raw_bundle,
        normalized_file=normalized_file,
        audit_result=audit_result,
        detection_result=detection_result,
        raw_output_path=raw_output_path,
        normalized_output_path=normalized_output_path,
        audit_extraction_output_path=audit_extraction_output_path,
        audit_output_path=audit_output_path,
        detections_output_path=detections_output_path,
        final_result_output_path=final_result_output_path,
        run_status_output_path=run_status_output_path,
        audit_parts_output_dir=audit_parts_output_dir,
        annotations_output_dir=annotations_output_dir,
    )
