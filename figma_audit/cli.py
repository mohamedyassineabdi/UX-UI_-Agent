from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from figma_audit.annotations import annotate_issue_screenshots
from figma_audit.browser_screenshots import capture_real_page_screenshots
from figma_audit.config import (
    ANNOTATIONS_OUTPUT_DIR,
    ANNOTATION_WORKERS,
    AUDIT_PARTS_OUTPUT_DIR,
    DETECTION_WORKERS,
    DETECTIONS_OUTPUT_DIR,
    EXTRACTED_OUTPUT_DIR,
    FINAL_RESULT_OUTPUT_PATH,
    NORMALIZED_OUTPUT_DIR,
    RAW_OUTPUT_DIR,
    REPORTS_OUTPUT_DIR,
    RUN_STATUS_OUTPUT_PATH,
    ensure_output_dirs,
)
from figma_audit.criteria_catalog import load_criteria_catalog
from figma_audit.detections import run_detections
from figma_audit.evidence_quality import attach_evidence_quality
from figma_audit.models.normalized_models import NormalizedFigmaFile
from figma_audit.pipeline import AuditPipelineOutputs, run_pipeline
from figma_audit.reports import build_detection_review_report
from figma_audit.utils.io import load_json, save_json


def print_summary(outputs: AuditPipelineOutputs) -> None:
    normalized_file = outputs.normalized_file
    audit_result = outputs.audit_result
    severity_summary = audit_result.severity_summary()

    print("\nDone successfully.\n")
    print(f"File key: {normalized_file.file_key}")
    print(f"File name: {normalized_file.file_name}")
    print(f"Pages: {len(normalized_file.pages)}")
    print(f"Frames: {len(normalized_file.frames)}")
    print(f"Nodes: {len(normalized_file.nodes)}")
    print(f"Components: {len(normalized_file.components)}")
    print(f"Tokens: {len(normalized_file.tokens)}")
    print(f"Warnings: {len(normalized_file.warnings)}")
    print("")
    print("Audit summary:")
    print(f"Total issues: {audit_result.total_issues()}")
    print(f"High: {severity_summary.high}")
    print(f"Medium: {severity_summary.medium}")
    print(f"Low: {severity_summary.low}")
    print("")
    print("Draft detections:")
    print(f"Criteria with problems: {outputs.detection_result.summary.criteria_with_detected_problems}")
    print(f"Draft issues: {outputs.detection_result.summary.draft_issue_count}")
    print(f"Detection screenshots: {outputs.detection_result.summary.screenshot_count}")
    print("")
    print(f"Raw output: {outputs.raw_output_path}")
    print(f"Normalized output: {outputs.normalized_output_path}")
    print(f"Audit extraction output: {outputs.audit_extraction_output_path}")
    print(f"Audit output: {outputs.audit_output_path}")
    print(f"Detections output: {outputs.detections_output_path}")
    print(f"Final result output: {outputs.final_result_output_path}")
    print(f"Run status output: {outputs.run_status_output_path}")
    print(f"Audit parts output: {outputs.audit_parts_output_dir}")
    print(f"Annotations output: {outputs.annotations_output_dir}")

    if normalized_file.warnings:
        print("\nWarnings:")
        for warning in normalized_file.warnings:
            print(f"- {warning}")


def run(figma_url: str) -> None:
    """Run the local CLI workflow and print a concise progress log."""
    print("1. Config validated")
    outputs = run_pipeline(figma_url, verbose=True)
    print("2. Pipeline completed")
    print(f"Total audit issues: {outputs.audit_result.total_issues()}")
    print_summary(outputs)


def validate_criteria(path: Path | None = None) -> None:
    """Validate the criteria catalog JSON and print a compact summary."""
    catalog = load_criteria_catalog(path)
    summary = catalog.summary()

    print("Criteria catalog is valid.")
    print(f"Status: {summary['status']}")
    print(f"Criteria: {summary['criteria_count']}")
    print(f"References: {summary['reference_count']}")
    print("IDs:")
    for criterion_id in summary["criteria_ids"]:
        print(f"- {criterion_id}")


def print_detection_summary(summary: object) -> None:
    print("Draft detections:")
    print(f"Criteria with problems: {summary.criteria_with_detected_problems}")
    print(f"Draft issues: {summary.draft_issue_count}")
    print(f"Detection screenshots: {summary.screenshot_count}")


def open_report(path: Path) -> None:
    """Open a generated HTML report on Windows, falling back to a copyable command."""
    try:
        os.startfile(path.resolve())  # type: ignore[attr-defined]
    except Exception:
        print(f'To open the report, run: start "" "{path}"')


def run_full_report(
    *,
    figma_url: str,
    max_annotations: int | None,
    annotation_workers: int | None,
    detection_workers: int | None,
    max_runtime_seconds: int | None,
    annotation_scope: str,
    allow_annotation_preview_fallback: bool,
    use_cached_fetch_on_error: bool,
    prefer_cached_fetch: bool,
    cache_only_fetch: bool,
    fetch_variables: bool,
    capture_real_pages: bool,
    strict_real_pages: bool,
    screenshot_width: int,
    screenshot_height: int,
    report_output: Path,
    polish_copy: bool,
    force_polish: bool,
    open_after: bool,
) -> Path:
    """Run the full audit, attach real evidence screenshots, and build the report."""
    print("1. Running Figma audit pipeline")
    use_fallback_annotations = max_annotations is None or max_annotations > 0
    if cache_only_fetch:
        print("Cache-only fetch is enabled; no live Figma API fetch will be attempted.")
    if not use_fallback_annotations and capture_real_pages:
        print("Fallback Figma Images API annotations are disabled; using real page screenshots for evidence.")
    elif not use_fallback_annotations:
        print("Fallback Figma Images API annotations are disabled; using available static/cached evidence.")
    outputs = run_pipeline(
        figma_url,
        save_outputs=True,
        split_audit_output=True,
        run_draft_detections=True,
        annotate_issues=use_fallback_annotations,
        max_annotations=max_annotations,
        annotation_workers=annotation_workers,
        detection_workers=detection_workers,
        annotation_render_scope=annotation_scope,
        allow_annotation_preview_fallback=allow_annotation_preview_fallback,
        use_cached_fetch_on_error=use_cached_fetch_on_error,
        prefer_cached_fetch=prefer_cached_fetch,
        cache_only_fetch=cache_only_fetch,
        fetch_variables=fetch_variables,
        max_runtime_seconds=max_runtime_seconds,
        verbose=True,
    )
    print("2. Audit pipeline completed")
    print_detection_summary(outputs.detection_result.summary)

    if capture_real_pages:
        print("3. Capturing real Figma page screenshots")
        try:
            screenshots = capture_real_page_screenshots(
                source_url=figma_url,
                detections_path=outputs.detections_output_path,
                extraction_path=outputs.audit_extraction_output_path,
                output_dir=REPORTS_OUTPUT_DIR / "real_pages",
                width=screenshot_width,
                height=screenshot_height,
                log=print,
            )
            print(f"Captured {len(screenshots)} real page screenshot(s).")
        except Exception as exc:
            message = f"Real page screenshot capture failed: {exc}"
            if strict_real_pages:
                raise RuntimeError(message) from exc
            print(f"Warning: {message}")
            print("The report will still be built with available fallback evidence.")
    else:
        print("3. Skipping real Figma page screenshots")

    evidence_quality_path = outputs.detections_output_path.parent / "evidence_quality.json"
    if outputs.final_result_output_path.exists():
        final_payload = load_json(outputs.final_result_output_path)
        updated_detections = load_json(outputs.detections_output_path)
        updated_quality = load_json(evidence_quality_path)
        if final_payload:
            if updated_detections:
                final_payload["draft_analysis"] = updated_detections
            if updated_quality:
                final_payload["validation_quality"] = updated_quality
            save_json(outputs.final_result_output_path, final_payload)

    print("4. Building executive HTML report")
    report_path = build_detection_review_report(
        detections_path=outputs.detections_output_path,
        output_path=report_output,
        polish_copy=polish_copy,
        ai_review=polish_copy,
        force_polish=force_polish,
        log=print,
    )
    print(f"Report saved: {report_path}")

    if open_after:
        print("5. Opening report")
        open_report(report_path)
    else:
        print(f'Open it with: start "" "{report_path}"')

    return report_path


def detect_normalized(
    *,
    normalized_path: Path,
    output_path: Path,
    annotations: bool = False,
    max_annotations: int | None = None,
    annotation_workers: int | None = None,
    detection_workers: int | None = None,
    annotations_output_dir: Path = ANNOTATIONS_OUTPUT_DIR,
) -> None:
    """Run draft detections against a saved normalized_file.json."""
    data = load_json(normalized_path)
    normalized_file = NormalizedFigmaFile.model_validate(data)
    detection_result = run_detections(normalized_file, workers=detection_workers, log=print)

    if annotations and detection_result.draft_issues:
        annotate_issue_screenshots(
            normalized_file=normalized_file,
            audit_result=detection_result,
            output_dir=annotations_output_dir,
            max_images=max_annotations,
            workers=annotation_workers,
            log=print,
        )
        detection_result.refresh_summary()

    validation_quality = attach_evidence_quality(
        normalized_file=normalized_file,
        detection_result=detection_result,
    )
    save_json(output_path, detection_result.model_dump(mode="json"))
    save_json(output_path.parent / "evidence_quality.json", validation_quality)
    print(f"Detections saved: {output_path}")
    print(f"Evidence quality saved: {output_path.parent / 'evidence_quality.json'}")
    print_detection_summary(detection_result.summary)


def cleanup_generated(*, yes: bool = False) -> None:
    """Remove generated output folders. Requires --yes to avoid accidents."""
    if not yes:
        raise ValueError("cleanup-generated requires --yes.")

    targets = [
        RAW_OUTPUT_DIR,
        NORMALIZED_OUTPUT_DIR,
        EXTRACTED_OUTPUT_DIR,
        AUDIT_PARTS_OUTPUT_DIR,
        DETECTIONS_OUTPUT_DIR,
        ANNOTATIONS_OUTPUT_DIR,
        REPORTS_OUTPUT_DIR,
        RUN_STATUS_OUTPUT_PATH,
        FINAL_RESULT_OUTPUT_PATH,
    ]

    for target in targets:
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            print(f"Removed {target}")

    ensure_output_dirs()
    print("Generated output folders recreated.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="figma-audit",
        description="Fetch and normalize Figma files. Criteria verification is disabled.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the Figma pipeline.")
    run_parser.add_argument("figma_url", help="Figma file, design, proto, or board URL.")
    run_parser.add_argument(
        "--no-save",
        action="store_true",
        help="Run without writing raw, normalized, or audit JSON files.",
    )
    run_parser.add_argument(
        "--no-split",
        action="store_true",
        help="Do not split the audit JSON into text parts.",
    )
    run_parser.add_argument(
        "--no-annotations",
        action="store_true",
        help="Skip annotated screenshot generation for detected issues.",
    )
    run_parser.add_argument(
        "--annotation-scope",
        choices=["context", "page"],
        default="context",
        help="Render issue evidence from the nearest useful context or the whole top-level page/section.",
    )
    run_parser.add_argument(
        "--real-screenshots-only",
        action="store_true",
        help="Fail instead of generating fallback preview images when Figma screenshots are unavailable.",
    )
    run_parser.add_argument(
        "--no-detections",
        action="store_true",
        help="Skip draft binary detections.",
    )
    run_parser.add_argument(
        "--no-cache-fallback",
        action="store_true",
        help="Do not continue from matching data/raw/raw_bundle.json if live Figma fetch fails.",
    )
    run_parser.add_argument(
        "--cache-first",
        action="store_true",
        help="Use matching data/raw/cache raw data before making a live Figma API request.",
    )
    run_parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Use only matching raw cache and fail without any live Figma API request if it is missing.",
    )
    run_parser.add_argument(
        "--offline",
        action="store_true",
        help="Quota-safe run mode: implies --cache-only and skips Figma Images API annotations.",
    )
    run_parser.add_argument(
        "--fetch-variables",
        action="store_true",
        help="Also fetch local Figma variables. Off by default to reduce API rate-limit pressure.",
    )
    run_parser.add_argument(
        "--max-annotations",
        type=int,
        default=None,
        help="Maximum number of screenshots to render for this run.",
    )
    run_parser.add_argument(
        "--annotation-workers",
        type=int,
        default=None,
        help=f"Concurrent image download workers. Default: ANNOTATION_WORKERS={ANNOTATION_WORKERS}.",
    )
    run_parser.add_argument(
        "--detection-workers",
        type=int,
        default=None,
        help=f"Concurrent detector workers. Default: DETECTION_WORKERS={DETECTION_WORKERS}.",
    )
    run_parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=None,
        help="Fail cleanly before starting a new stage if the run exceeds this many seconds.",
    )

    full_parser = subparsers.add_parser(
        "full-report",
        help="Run the full Figma audit, capture real page screenshots, build the HTML report, and open it.",
    )
    full_parser.add_argument("figma_url", help="Figma file, design, proto, or board URL.")
    full_parser.add_argument(
        "--max-annotations",
        type=int,
        default=0,
        help="Maximum number of fallback Figma Images API annotations to render. Default: 0; real page screenshots are used instead.",
    )
    full_parser.add_argument(
        "--annotation-workers",
        type=int,
        default=2,
        help="Concurrent annotation image workers. Default: 2.",
    )
    full_parser.add_argument(
        "--detection-workers",
        type=int,
        default=None,
        help=f"Concurrent detector workers. Default: DETECTION_WORKERS={DETECTION_WORKERS}.",
    )
    full_parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=1800,
        help="Fail cleanly before starting a new stage if the audit exceeds this many seconds. Default: 1800.",
    )
    full_parser.add_argument(
        "--annotation-scope",
        choices=["context", "page"],
        default="page",
        help="Render fallback evidence from nearest context or whole top-level page/section. Default: page.",
    )
    full_parser.add_argument(
        "--real-screenshots-only",
        action="store_true",
        help="Fail instead of generating fallback preview images when Figma rendered screenshots are unavailable.",
    )
    full_parser.add_argument(
        "--no-cache-fallback",
        action="store_true",
        help="Do not continue from matching data/raw/raw_bundle.json if live Figma fetch fails.",
    )
    full_parser.add_argument(
        "--cache-first",
        action="store_true",
        help="Use matching data/raw/cache raw data before making a live Figma API request.",
    )
    full_parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Use only matching raw cache and fail without any live Figma API request if it is missing.",
    )
    full_parser.add_argument(
        "--offline",
        action="store_true",
        help="Quota-safe report mode: implies --cache-only, --max-annotations 0, --no-real-pages, --no-polish, and --no-open.",
    )
    full_parser.add_argument(
        "--fetch-variables",
        action="store_true",
        help="Also fetch local Figma variables. Off by default to reduce API rate-limit pressure.",
    )
    full_parser.add_argument(
        "--no-real-pages",
        action="store_true",
        help="Skip browser capture of public Figma prototype pages.",
    )
    full_parser.add_argument(
        "--strict-real-pages",
        action="store_true",
        help="Fail the whole command if browser screenshot capture fails.",
    )
    full_parser.add_argument(
        "--width",
        type=int,
        default=1440,
        help="Browser screenshot viewport width. Default: 1440.",
    )
    full_parser.add_argument(
        "--height",
        type=int,
        default=5200,
        help="Browser screenshot viewport height. Default: 5200.",
    )
    full_parser.add_argument(
        "--report-output",
        type=Path,
        default=REPORTS_OUTPUT_DIR / "draft_detection_review.html",
        help="Where to write the final HTML report.",
    )
    full_parser.add_argument(
        "--no-polish",
        action="store_true",
        help="Skip Ollama Cloud client-facing copy polishing.",
    )
    full_parser.add_argument(
        "--force-polish",
        action="store_true",
        help="Ignore cached client copy and request a fresh Ollama Cloud rewrite.",
    )
    full_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the generated report automatically.",
    )

    criteria_parser = subparsers.add_parser(
        "validate-criteria",
        help="Validate the UX/UI criteria JSON without running an audit.",
    )
    criteria_parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Optional path to a criteria catalog JSON file.",
    )

    detect_parser = subparsers.add_parser(
        "detect-normalized",
        help="Run binary draft detections against a saved normalized JSON file.",
    )
    detect_parser.add_argument(
        "--input",
        type=Path,
        default=NORMALIZED_OUTPUT_DIR / "normalized_file.json",
        help="Path to normalized_file.json.",
    )
    detect_parser.add_argument(
        "--output",
        type=Path,
        default=DETECTIONS_OUTPUT_DIR / "draft_detections.json",
        help="Where to write draft detections JSON.",
    )
    detect_parser.add_argument(
        "--annotations",
        action="store_true",
        help="Also request Figma rendered images and attach visual evidence.",
    )
    detect_parser.add_argument(
        "--max-annotations",
        type=int,
        default=None,
        help="Maximum number of screenshots to render for this detection run.",
    )
    detect_parser.add_argument(
        "--annotation-workers",
        type=int,
        default=None,
        help=f"Concurrent image download workers. Default: ANNOTATION_WORKERS={ANNOTATION_WORKERS}.",
    )
    detect_parser.add_argument(
        "--detection-workers",
        type=int,
        default=None,
        help=f"Concurrent detector workers. Default: DETECTION_WORKERS={DETECTION_WORKERS}.",
    )

    report_parser = subparsers.add_parser(
        "build-report",
        help="Build a local HTML review report from draft detections JSON.",
    )
    report_parser.add_argument(
        "--detections",
        type=Path,
        default=DETECTIONS_OUTPUT_DIR / "draft_detections.json",
        help="Path to draft detections JSON.",
    )
    report_parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS_OUTPUT_DIR / "draft_detection_review.html",
        help="Where to write the HTML review report.",
    )
    report_parser.add_argument(
        "--no-polish",
        action="store_true",
        help="Skip Ollama Cloud client-facing copy polishing.",
    )
    report_parser.add_argument(
        "--force-polish",
        action="store_true",
        help="Ignore cached client copy and request a fresh Ollama Cloud rewrite.",
    )

    screenshot_parser = subparsers.add_parser(
        "capture-real-pages",
        help="Capture real public Figma prototype page screenshots and attach them to detections.",
    )
    screenshot_parser.add_argument("figma_url", help="Figma proto/design URL used for this run.")
    screenshot_parser.add_argument(
        "--detections",
        type=Path,
        default=DETECTIONS_OUTPUT_DIR / "draft_detections.json",
        help="Path to draft detections JSON to update.",
    )
    screenshot_parser.add_argument(
        "--extraction",
        type=Path,
        default=EXTRACTED_OUTPUT_DIR / "audit_info.json",
        help="Path to extracted audit info JSON.",
    )
    screenshot_parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORTS_OUTPUT_DIR / "real_pages",
        help="Where to save real page screenshots.",
    )
    screenshot_parser.add_argument(
        "--height",
        type=int,
        default=5200,
        help="Browser screenshot viewport height.",
    )
    screenshot_parser.add_argument(
        "--width",
        type=int,
        default=1440,
        help="Browser screenshot viewport width.",
    )

    cleanup_parser = subparsers.add_parser(
        "cleanup-generated",
        help="Remove generated raw, normalized, parts, detections, and annotations outputs.",
    )
    cleanup_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of generated output folders.",
    )

    return parser


def main() -> int:
    """
    CLI entry point.

    Usage:
        python main.py "https://www.figma.com/design/FILEKEY/YourFile?node-id=1-2"
        python main.py run "https://www.figma.com/design/FILEKEY/YourFile?node-id=1-2"
        python main.py validate-criteria

    Or:
        python main.py
        # then paste the URL interactively
    """
    try:
        if len(sys.argv) == 1:
            figma_url = input("Enter Figma URL: ").strip()
            if not figma_url:
                raise ValueError("No Figma URL provided.")
            run(figma_url)
            return 0

        known_commands = {
            "run",
            "full-report",
            "validate-criteria",
            "detect-normalized",
            "build-report",
            "capture-real-pages",
            "cleanup-generated",
            "-h",
            "--help",
        }
        if sys.argv[1] not in known_commands:
            figma_url = sys.argv[1].strip()
            if not figma_url:
                raise ValueError("No Figma URL provided.")
            run(figma_url)
            return 0

        parser = build_parser()
        args = parser.parse_args()

        if args.command == "run":
            print("1. Config validated")
            offline_run = args.offline
            outputs = run_pipeline(
                args.figma_url,
                save_outputs=not args.no_save,
                split_audit_output=not args.no_split,
                run_draft_detections=not args.no_detections,
                annotate_issues=not args.no_annotations and not offline_run,
                max_annotations=args.max_annotations,
                annotation_workers=args.annotation_workers,
                detection_workers=args.detection_workers,
                annotation_render_scope=args.annotation_scope,
                allow_annotation_preview_fallback=not args.real_screenshots_only,
                use_cached_fetch_on_error=not args.no_cache_fallback,
                prefer_cached_fetch=args.cache_first or args.cache_only or offline_run,
                cache_only_fetch=args.cache_only or offline_run,
                fetch_variables=args.fetch_variables,
                max_runtime_seconds=args.max_runtime_seconds
                if args.max_runtime_seconds is not None
                else None,
                verbose=True,
            )
            print("2. Pipeline completed")
            print(f"Total audit issues: {outputs.audit_result.total_issues()}")
            print_summary(outputs)
        elif args.command == "full-report":
            offline_report = args.offline
            run_full_report(
                figma_url=args.figma_url,
                max_annotations=0 if offline_report else args.max_annotations,
                annotation_workers=args.annotation_workers,
                detection_workers=args.detection_workers,
                max_runtime_seconds=args.max_runtime_seconds,
                annotation_scope=args.annotation_scope,
                allow_annotation_preview_fallback=not args.real_screenshots_only,
                use_cached_fetch_on_error=not args.no_cache_fallback,
                prefer_cached_fetch=args.cache_first or args.cache_only or offline_report,
                cache_only_fetch=args.cache_only or offline_report,
                fetch_variables=args.fetch_variables,
                capture_real_pages=not args.no_real_pages and not offline_report,
                strict_real_pages=args.strict_real_pages,
                screenshot_width=args.width,
                screenshot_height=args.height,
                report_output=args.report_output,
                polish_copy=not args.no_polish and not offline_report,
                force_polish=args.force_polish,
                open_after=not args.no_open and not offline_report,
            )
        elif args.command == "validate-criteria":
            validate_criteria(args.path)
        elif args.command == "detect-normalized":
            detect_normalized(
                normalized_path=args.input,
                output_path=args.output,
                annotations=args.annotations,
                max_annotations=args.max_annotations,
                annotation_workers=args.annotation_workers,
                detection_workers=args.detection_workers,
            )
        elif args.command == "build-report":
            report_path = build_detection_review_report(
                detections_path=args.detections,
                output_path=args.output,
                polish_copy=not args.no_polish,
                ai_review=not args.no_polish,
                force_polish=args.force_polish,
                log=print,
            )
            print(f"Report saved: {report_path}")
        elif args.command == "capture-real-pages":
            screenshots = capture_real_page_screenshots(
                source_url=args.figma_url,
                detections_path=args.detections,
                extraction_path=args.extraction,
                output_dir=args.output_dir,
                width=args.width,
                height=args.height,
                log=print,
            )
            print(f"Captured {len(screenshots)} real Figma page screenshot(s).")
            for screenshot in screenshots:
                print(f"- {screenshot}")
        elif args.command == "cleanup-generated":
            cleanup_generated(yes=args.yes)
        else:
            parser.print_help()

        return 0

    except KeyboardInterrupt:
        print("\nExecution cancelled by user.")
        return 1

    except Exception as exc:
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
