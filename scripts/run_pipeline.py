import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
NAVIGATOR_DIR = ROOT_DIR / "navigator"
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
RESULTS_DIR = ROOT_DIR / "shared" / "output" / "results"

WEBSITE_MENU_JSON = GENERATED_DIR / "website_menu.json"
HTML_CLEANED_JSON = GENERATED_DIR / "html_cleaned.json"
RENDERED_UI_JSON = GENERATED_DIR / "rendered_ui_extraction.json"
CHECKS_JSON = GENERATED_DIR / "sheet_checks.json"
WORKBOOK_OUTPUT = GENERATED_DIR / "UX-Audit-Workbook-final.xlsx"
GTM_AUDIT_JSON = GENERATED_DIR / "gtm_audit.json"
DETAILED_REPORT_OUTPUT_DIR = GENERATED_DIR / "audit-report"
GTM_REPORT_OUTPUT_DIR = GENERATED_DIR / "gtm-report"
GTM_VERCEL_OUTPUT_DIR = GENERATED_DIR / "vercel-gtm-report"
DEFAULT_TEMPLATE_CANDIDATE = GENERATED_DIR / "UX-Audit-Workbook-template.xlsx"

load_dotenv(ROOT_DIR / ".env")


def run_command(args, cwd: Optional[Path] = None) -> None:
    completed = subprocess.run(
        [str(arg) for arg in args],
        cwd=str(cwd) if cwd else None,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{args[0]} exited with code {completed.returncode}")


def run_command_capture(args, cwd: Optional[Path] = None) -> str:
    completed = subprocess.run(
        [str(arg) for arg in args],
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise RuntimeError(f"{args[0]} exited with code {completed.returncode}")
    return output


def ensure_dir(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)


def ensure_file_exists(file_path: Path) -> None:
    if not file_path.exists():
        raise RuntimeError(f"Expected file was not created: {file_path}")


def latest_audit_results_file() -> Optional[Path]:
    candidates = sorted(
        RESULTS_DIR.glob("audit-results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def read_json_file(file_path: Path):
    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_crawler_output(file_path: Path):
    data = read_json_file(file_path)

    if isinstance(data, dict):
        crawler_error = str(data.get("error") or "").strip()
        if crawler_error:
            raise RuntimeError(f"Crawler failed for this website: {crawler_error}")

    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full UX/UI auditor pipeline.")
    parser.add_argument("url", help="Website URL to crawl and audit")
    parser.add_argument(
        "--mode",
        choices=("detailed", "gtm"),
        default="detailed",
        help="Audit mode. 'detailed' runs the existing sheet-based audit. 'gtm' runs the 7-axis go-to-market audit.",
    )
    parser.add_argument(
        "--workbook-template",
        default="",
        help="Optional workbook template path. If omitted, the pipeline auto-discovers one.",
    )
    parser.add_argument(
        "--checks-out",
        default=str(CHECKS_JSON),
        help="Path for the generated checks JSON output",
    )
    parser.add_argument(
        "--workbook-out",
        default=str(WORKBOOK_OUTPUT),
        help="Path for the final workbook output",
    )
    parser.add_argument(
        "--skip-workbook",
        action="store_true",
        help="Generate checks JSON but skip workbook export",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="Directory for the generated report site. Defaults depend on the selected mode.",
    )
    parser.add_argument(
        "--gtm-out",
        default=str(GTM_AUDIT_JSON),
        help="Path for the GTM analysis JSON output when --mode gtm is used.",
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="When --mode gtm is used, skip the multimodal vision synthesis layer.",
    )
    parser.add_argument(
        "--vercel-out",
        default=str(GTM_VERCEL_OUTPUT_DIR),
        help="Directory for the packaged Vercel static GTM report.",
    )
    parser.add_argument(
        "--deploy-vercel",
        action="store_true",
        help="When --mode gtm is used, package and deploy the generated GTM report to Vercel.",
    )
    parser.add_argument(
        "--vercel-preview",
        action="store_true",
        help="Create a Vercel preview deployment instead of a production deployment.",
    )
    return parser.parse_args()


def resolve_workbook_template(explicit_path: str, workbook_output: Path) -> Path:
    candidates = []

    if explicit_path:
        candidates.append(Path(explicit_path))

    env_path = os.getenv("AUDIT_WORKBOOK_TEMPLATE", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    candidates.append(DEFAULT_TEMPLATE_CANDIDATE)

    for candidate in candidates:
        resolved = candidate if candidate.is_absolute() else ROOT_DIR / candidate
        if resolved.exists():
            return resolved

    workbook_files = []
    for candidate in GENERATED_DIR.glob("*.xlsx"):
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved == workbook_output.resolve():
            continue
        workbook_files.append(candidate)

    if workbook_files:
        workbook_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return workbook_files[0]

    raise FileNotFoundError(
        "No workbook template found. Pass --workbook-template or set AUDIT_WORKBOOK_TEMPLATE."
    )


def main() -> None:
    args = parse_args()

    checks_output = Path(args.checks_out)
    if not checks_output.is_absolute():
        checks_output = ROOT_DIR / checks_output

    workbook_output = Path(args.workbook_out)
    if not workbook_output.is_absolute():
        workbook_output = ROOT_DIR / workbook_output

    report_output_dir = Path(args.report_out or (
        GTM_REPORT_OUTPUT_DIR if args.mode == "gtm" else DETAILED_REPORT_OUTPUT_DIR
    ))
    if not report_output_dir.is_absolute():
        report_output_dir = ROOT_DIR / report_output_dir

    gtm_output = Path(args.gtm_out)
    if not gtm_output.is_absolute():
        gtm_output = ROOT_DIR / gtm_output

    vercel_output_dir = Path(args.vercel_out)
    if not vercel_output_dir.is_absolute():
        vercel_output_dir = ROOT_DIR / vercel_output_dir

    ensure_dir(GENERATED_DIR)
    ensure_dir(checks_output.parent)
    ensure_dir(report_output_dir)
    if args.mode == "gtm":
        ensure_dir(gtm_output.parent)
    if not args.skip_workbook:
        ensure_dir(workbook_output.parent)

    print("\n[1/5] Running crawler...\n")
    crawler_args = [
        sys.executable,
        NAVIGATOR_DIR / "crawler.py",
        args.url,
        "--json-out",
        WEBSITE_MENU_JSON,
    ]
    if os.getenv("OLLAMA_MODEL") or os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST"):
        crawler_args.append("--use-ai-nav")

    run_command(crawler_args, cwd=GENERATED_DIR)

    ensure_file_exists(WEBSITE_MENU_JSON)
    validate_crawler_output(WEBSITE_MENU_JSON)

    print("\n[2/5] Running page audit...\n")
    run_command([sys.executable, "-m", "src.main"], cwd=ROOT_DIR)

    ensure_file_exists(HTML_CLEANED_JSON)
    ensure_file_exists(RENDERED_UI_JSON)
    latest_results = latest_audit_results_file()

    print("\n[3/5] Generating checks JSON...\n")
    checks_args = [
        sys.executable,
        "-m",
        "src.audit.checks.run_sheet_checks",
        "--cleaned",
        HTML_CLEANED_JSON,
        "--rendered",
        RENDERED_UI_JSON,
        "--output",
        checks_output,
    ]
    if latest_results:
        checks_args.extend(["--results", latest_results])
    run_command(checks_args, cwd=ROOT_DIR)

    ensure_file_exists(checks_output)

    workbook_for_report = ""
    if args.mode == "detailed":
        if args.skip_workbook:
            print("\n[4/5] Workbook export skipped.\n")
        else:
            workbook_template = resolve_workbook_template(args.workbook_template, workbook_output)

            print("\n[4/5] Exporting workbook...\n")
            print(f"Using workbook template: {workbook_template}")
            run_command(
                [
                    sys.executable,
                    "-m",
                    "src.audit.export.write_checks_to_workbook",
                    "--template",
                    workbook_template,
                    "--checks",
                    checks_output,
                    "--output",
                    workbook_output,
                ],
                cwd=ROOT_DIR,
            )

            ensure_file_exists(workbook_output)
            workbook_for_report = str(workbook_output)

        print("\n[5/5] Generating audit report site...\n")
        report_args = [
            sys.executable,
            "-m",
            "src.report.generate_audit_report",
            "--website-menu",
            WEBSITE_MENU_JSON,
            "--cleaned",
            HTML_CLEANED_JSON,
            "--rendered",
            RENDERED_UI_JSON,
            "--checks",
            checks_output,
            "--output-dir",
            report_output_dir,
        ]
        if workbook_for_report:
            report_args.extend(["--workbook", workbook_for_report])
        run_command(report_args, cwd=ROOT_DIR)
        ensure_file_exists(report_output_dir / "index.html")
    else:
        print("\n[4/5] Generating GTM 7-axis audit...\n")
        gtm_args = [
            sys.executable,
            "-m",
            "src.gtm_audit.generate_gtm_audit",
            "--website-menu",
            WEBSITE_MENU_JSON,
            "--cleaned",
            HTML_CLEANED_JSON,
            "--rendered",
            RENDERED_UI_JSON,
            "--checks",
            checks_output,
            "--output",
            gtm_output,
        ]
        if latest_results:
            gtm_args.extend(["--results", latest_results])
        if args.skip_vision:
            gtm_args.append("--skip-vision")
        run_command(gtm_args, cwd=ROOT_DIR)
        ensure_file_exists(gtm_output)

        print("\n[5/5] Generating GTM report site...\n")
        run_command(
            [
                sys.executable,
                "-m",
                "src.gtm_audit.generate_gtm_report",
                "--input",
                gtm_output,
                "--output-dir",
                report_output_dir,
            ],
            cwd=ROOT_DIR,
        )
        ensure_file_exists(report_output_dir / "index.html")

        deploy_vercel = args.deploy_vercel or os.getenv("GTM_AUTO_DEPLOY", "").strip().lower() in {"1", "true", "yes", "on"}
        print("\n[6/6] Packaging GTM report for Vercel...\n")
        deploy_args = [
            sys.executable,
            "-m",
            "src.gtm_audit.vercel_static_deploy",
            "--report-dir",
            report_output_dir,
            "--output-dir",
            vercel_output_dir,
        ]
        if deploy_vercel:
            deploy_args.append("--deploy")
            if args.vercel_preview:
                deploy_args.append("--preview")
        deploy_output = run_command_capture(deploy_args, cwd=ROOT_DIR)
        deployment_urls = re.findall(r"https://[^\s]+", deploy_output)
        if deployment_urls:
            print(f"Final Vercel link: {deployment_urls[-1]}")
        elif not deploy_vercel:
            print(f"Vercel static package: {vercel_output_dir / 'index.html'}")

    print("\nPipeline completed successfully.")
    print(f"Navigation JSON: {WEBSITE_MENU_JSON}")
    print(f"Cleaned HTML JSON: {HTML_CLEANED_JSON}")
    print(f"Rendered UI JSON: {RENDERED_UI_JSON}")
    print(f"Checks JSON: {checks_output}")
    if workbook_for_report:
        print(f"Workbook: {workbook_output}")
    if args.mode == "gtm":
        print(f"GTM audit JSON: {gtm_output}")
        print(f"Vercel static package: {vercel_output_dir / 'index.html'}")
    print(f"Audit report: {report_output_dir / 'index.html'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nPipeline failed:", file=sys.stderr)
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
