from __future__ import annotations

import argparse
from pathlib import Path

from figma_audit.browser_screenshots import capture_real_page_screenshots
from figma_audit.pipeline import run_pipeline
from figma_audit.reports import build_detection_review_report
from figma_audit.utils.io import load_json, save_json


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "shared" / "generated" / "figma-audits"


def _job_dir(output_root: Path, job_id: str) -> Path:
    safe_job_id = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in job_id).strip("-_")
    return output_root / (safe_job_id or "figma-audit")


def _refresh_final_payload(final_result_path: Path, detections_path: Path) -> None:
    final_payload = load_json(final_result_path)
    if not final_payload:
        return

    detections = load_json(detections_path)
    evidence_quality = load_json(detections_path.parent / "evidence_quality.json")
    if detections:
        final_payload["draft_analysis"] = detections
    if evidence_quality:
        final_payload["validation_quality"] = evidence_quality
    save_json(final_result_path, final_payload)


def run_figma_audit(
    *,
    figma_url: str,
    job_id: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    capture_real_pages: bool = True,
    strict_real_pages: bool = False,
    screenshot_width: int = 1440,
    screenshot_height: int = 5200,
    max_annotations: int = 0,
    polish_copy: bool = False,
) -> Path:
    job_dir = _job_dir(output_root, job_id)
    data_dir = job_dir / "data"
    report_dir = job_dir / "figma-report"
    report_path = report_dir / "index.html"

    raw_output_path = data_dir / "raw" / "raw_bundle.json"
    normalized_output_path = data_dir / "normalized" / "normalized_file.json"
    audit_extraction_output_path = data_dir / "extracted" / "audit_info.json"
    audit_output_path = data_dir / "normalized" / "audit_result.json"
    detections_output_path = data_dir / "detections" / "draft_detections.json"
    final_result_output_path = data_dir / "final_result.json"
    run_status_output_path = data_dir / "run_status.json"
    audit_parts_output_dir = data_dir / "parts"
    annotations_output_dir = report_dir / "annotations"

    print("[1/5] Running Figma audit pipeline")
    outputs = run_pipeline(
        figma_url,
        save_outputs=True,
        split_audit_output=True,
        run_draft_detections=True,
        annotate_issues=max_annotations > 0,
        max_annotations=max_annotations if max_annotations > 0 else None,
        annotation_workers=2,
        detection_workers=None,
        annotation_render_scope="page",
        allow_annotation_preview_fallback=True,
        use_cached_fetch_on_error=True,
        prefer_cached_fetch=False,
        cache_only_fetch=False,
        fetch_variables=False,
        max_runtime_seconds=1800,
        verbose=True,
        raw_output_path=raw_output_path,
        normalized_output_path=normalized_output_path,
        audit_extraction_output_path=audit_extraction_output_path,
        audit_output_path=audit_output_path,
        detections_output_path=detections_output_path,
        audit_parts_output_dir=audit_parts_output_dir,
        annotations_output_dir=annotations_output_dir,
        final_result_output_path=final_result_output_path,
        run_status_output_path=run_status_output_path,
    )

    if capture_real_pages:
        print("[2/5] Capturing real Figma screenshots")
        try:
            screenshots = capture_real_page_screenshots(
                source_url=figma_url,
                detections_path=outputs.detections_output_path,
                extraction_path=outputs.audit_extraction_output_path,
                output_dir=report_dir / "real_pages",
                width=screenshot_width,
                height=screenshot_height,
                log=print,
            )
            print(f"Captured {len(screenshots)} real Figma screenshot(s).")
        except Exception as exc:
            if strict_real_pages:
                raise
            print(f"Warning: real Figma screenshot capture failed: {exc}")
    else:
        print("[2/5] Skipping real Figma screenshots")

    print("[3/5] Refreshing Figma evidence")
    _refresh_final_payload(outputs.final_result_output_path, outputs.detections_output_path)

    print("[4/5] Generating editable Figma audit report")
    report_path = build_detection_review_report(
        detections_path=outputs.detections_output_path,
        output_path=report_path,
        polish_copy=polish_copy,
        ai_review=True,
        force_polish=False,
        log=print,
    )

    print("[5/5] Figma audit report ready")
    print(f"Figma report saved: {report_path}")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Figma UX/UI audit and generate an editable report.")
    parser.add_argument("figma_url", help="Figma file, design, proto, or board URL.")
    parser.add_argument("--job-id", default="figma-audit")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-real-pages", action="store_true")
    parser.add_argument("--strict-real-pages", action="store_true")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=5200)
    parser.add_argument("--max-annotations", type=int, default=0)
    parser.add_argument("--polish", action="store_true", help="Use configured Ollama Cloud polishing for report copy.")
    args = parser.parse_args()

    try:
        run_figma_audit(
            figma_url=args.figma_url,
            job_id=args.job_id,
            output_root=args.output_root,
            capture_real_pages=not args.no_real_pages,
            strict_real_pages=args.strict_real_pages,
            screenshot_width=args.width,
            screenshot_height=args.height,
            max_annotations=args.max_annotations,
            polish_copy=args.polish,
        )
        return 0
    except KeyboardInterrupt:
        print("\nExecution cancelled by user.")
        return 1
    except Exception as exc:
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
