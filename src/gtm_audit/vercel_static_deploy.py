from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict
from urllib.parse import quote, unquote


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
DEFAULT_REPORT_DIR = GENERATED_DIR / "gtm-report"
DEFAULT_STATIC_DIR = GENERATED_DIR / "vercel-gtm-report"
LOCAL_REF_RE = re.compile(r'(?P<attr>src|href)="(?P<href>[^"]+)"')


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_clear_dir(path: Path) -> None:
    resolved = path.resolve()
    if not _inside(resolved, GENERATED_DIR):
        raise RuntimeError(f"Refusing to clear non-generated directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _is_external_or_special(href: str) -> bool:
    lowered = href.lower()
    return (
        not href
        or href.startswith("#")
        or lowered.startswith(("http://", "https://", "mailto:", "tel:", "data:", "javascript:"))
    )


def _asset_href_for_source(source: Path) -> str:
    try:
        rel = source.resolve().relative_to(ROOT_DIR.resolve())
    except ValueError:
        rel = Path(source.name)
    return quote((Path("assets") / rel).as_posix(), safe="/:#?&=%")


def package_report_for_vercel(report_dir: Path, static_dir: Path) -> Path:
    report_dir = report_dir if report_dir.is_absolute() else ROOT_DIR / report_dir
    static_dir = static_dir if static_dir.is_absolute() else ROOT_DIR / static_dir
    index_path = report_dir / "index.html"
    if not index_path.exists():
        raise FileNotFoundError(f"Report index.html not found: {index_path}")

    _safe_clear_dir(static_dir)
    shutil.copytree(report_dir, static_dir, dirs_exist_ok=True)

    output_index = static_dir / "index.html"
    html = output_index.read_text(encoding="utf-8")
    rewrites: Dict[str, str] = {}

    for match in LOCAL_REF_RE.finditer(html):
        href = match.group("href")
        if _is_external_or_special(href):
            continue
        decoded_href = unquote(href)
        source = (report_dir / decoded_href).resolve()
        if not source.exists() or not source.is_file():
            continue
        if _inside(source, report_dir):
            continue
        asset_href = _asset_href_for_source(source)
        target = static_dir / unquote(asset_href)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        rewrites[href] = asset_href

    for old, new in rewrites.items():
        html = html.replace(f'"{old}"', f'"{new}"')
    output_index.write_text(html, encoding="utf-8")
    return output_index


def _vercel_executable() -> str:
    executable = shutil.which("vercel") or shutil.which("vercel.cmd")
    if not executable:
        npm_executable = shutil.which("npm.cmd") or shutil.which("npm")
        if npm_executable:
            completed = subprocess.run(
                [npm_executable, "config", "get", "prefix"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            prefix = (completed.stdout or "").strip()
            candidates = [
                Path(prefix) / "vercel.cmd",
                Path(prefix) / "vercel",
                Path(prefix) / "node_modules" / ".bin" / "vercel.cmd",
                Path(prefix) / "node_modules" / ".bin" / "vercel",
            ] if prefix else []
            for candidate in candidates:
                try:
                    if candidate.exists():
                        return str(candidate)
                except OSError:
                    continue
        raise RuntimeError("Vercel CLI not found. Install it with: npm i -g vercel")
    return executable


def deploy_to_vercel(static_dir: Path, *, production: bool = True) -> str:
    executable = _vercel_executable()
    command = [executable, "deploy", str(static_dir), "--yes"]
    if production:
        command.append("--prod")
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    output = completed.stdout or ""
    print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode != 0:
        lowered = output.lower()
        if "login" in lowered or "auth" in lowered or "not authenticated" in lowered:
            raise RuntimeError("Vercel deployment failed because the CLI is not authenticated. Run: vercel login")
        raise RuntimeError(f"Vercel deployment failed with exit code {completed.returncode}.")
    urls = re.findall(r"https://[^\s]+", output)
    return urls[-1] if urls else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Package and optionally deploy an audit report as a static Vercel site.")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_STATIC_DIR))
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--preview", action="store_true", help="Create a preview deployment instead of production.")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    static_dir = Path(args.output_dir)
    output_index = package_report_for_vercel(report_dir, static_dir)
    print(f"Vercel static report packaged at: {output_index}")

    if args.deploy:
        url = deploy_to_vercel(output_index.parent, production=not args.preview)
        if not url:
            raise RuntimeError("Vercel deployment completed but no deployment URL was found in CLI output.")
        print(f"Vercel deployment URL: {url}")


if __name__ == "__main__":
    main()
