import json
import os
import subprocess
import sys


ROOT_DIR = os.getcwd()
NAVIGATOR_DIR = os.path.join(ROOT_DIR, "navigator")
GENERATED_DIR = os.path.join(ROOT_DIR, "shared", "generated")
OUTPUT_JSON = os.path.join(GENERATED_DIR, "website_menu.json")


def run_command(args, cwd=None):
    completed = subprocess.run(args, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{args[0]} exited with code {completed.returncode}")


def ensure_dir(dir_path):
    os.makedirs(dir_path, exist_ok=True)


def ensure_file_exists(file_path):
    if not os.path.exists(file_path):
        raise RuntimeError(f"Expected file was not created: {file_path}")


def read_json_file(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def validate_crawler_output(file_path):
    data = read_json_file(file_path)

    if isinstance(data, dict):
        crawler_error = str(data.get("error") or "").strip()
        if crawler_error:
            raise RuntimeError(f"Crawler failed for this website: {crawler_error}")

    return data


def main():
    if len(sys.argv) < 2:
        print("Usage: npm run scan -- <website-url>", file=sys.stderr)
        raise SystemExit(1)

    target_url = sys.argv[1]

    ensure_dir(GENERATED_DIR)

    print("\n[1/2] Running partner crawler...\n")
    run_command(
        [sys.executable, os.path.join(NAVIGATOR_DIR, "crawler.py"), target_url],
        cwd=GENERATED_DIR,
    )

    ensure_file_exists(OUTPUT_JSON)
    validate_crawler_output(OUTPUT_JSON)

    print("\n[2/2] Running auditor...\n")
    run_command([sys.executable, "-m", "src.main"], cwd=ROOT_DIR)

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nPipeline failed:", file=sys.stderr)
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
