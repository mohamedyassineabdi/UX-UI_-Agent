import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
NAVIGATOR_DIR = ROOT_DIR / "navigator"
GENERATED_DIR = ROOT_DIR / "shared" / "generated"

WEBSITE_MENU_JSON = GENERATED_DIR / "website_menu.json"
PERSON_A_CLEANED_JSON = GENERATED_DIR / "person_a_cleaned.json"
RENDERED_UI_JSON = GENERATED_DIR / "rendered_ui_extraction.json"
CHECKS_JSON = GENERATED_DIR / "person_a_sheet_checks_v2.json"
WORKBOOK_OUTPUT = GENERATED_DIR / "UX-Audit-Workbook-final.xlsx"
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


def ensure_dir(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)


def ensure_file_exists(file_path: Path) -> None:
    if not file_path.exists():
        raise RuntimeError(f"Expected file was not created: {file_path}")


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

    ensure_dir(GENERATED_DIR)
    ensure_dir(checks_output.parent)
    if not args.skip_workbook:
        ensure_dir(workbook_output.parent)

    print("\n[1/4] Running crawler...\n")
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

    print("\n[2/4] Running page audit...\n")
    run_command([sys.executable, "-m", "src.main"], cwd=ROOT_DIR)

    ensure_file_exists(PERSON_A_CLEANED_JSON)
    ensure_file_exists(RENDERED_UI_JSON)

    print("\n[3/4] Generating checks JSON...\n")
    run_command(
        [
            sys.executable,
            "-m",
            "src.audit.checks.run_sheet_checks",
            "--cleaned",
            PERSON_A_CLEANED_JSON,
            "--rendered",
            RENDERED_UI_JSON,
            "--output",
            checks_output,
        ],
        cwd=ROOT_DIR,
    )

    ensure_file_exists(checks_output)

    if args.skip_workbook:
        print("\n[4/4] Workbook export skipped.\n")
        print("Pipeline completed successfully.")
        print(f"Navigation JSON: {WEBSITE_MENU_JSON}")
        print(f"Cleaned content JSON: {PERSON_A_CLEANED_JSON}")
        print(f"Rendered UI JSON: {RENDERED_UI_JSON}")
        print(f"Checks JSON: {checks_output}")
        return

    workbook_template = resolve_workbook_template(args.workbook_template, workbook_output)

    print("\n[4/4] Exporting workbook...\n")
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

    print("\nPipeline completed successfully.")
    print(f"Navigation JSON: {WEBSITE_MENU_JSON}")
    print(f"Cleaned content JSON: {PERSON_A_CLEANED_JSON}")
    print(f"Rendered UI JSON: {RENDERED_UI_JSON}")
    print(f"Checks JSON: {checks_output}")
    print(f"Workbook: {workbook_output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nPipeline failed:", file=sys.stderr)
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
