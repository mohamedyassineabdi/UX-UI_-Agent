from __future__ import annotations

import argparse
import json
import os
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
HISTORY_STATIC_DIR = GENERATED_DIR / "vercel-audit-history"
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
    resolved.mkdir(parents=True, exist_ok=True)
    for child in resolved.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


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


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return slug[:80] or "audit"


def _copy_report_with_assets(report_dir: Path, target_dir: Path) -> Path:
    report_dir = report_dir if report_dir.is_absolute() else ROOT_DIR / report_dir
    target_dir = target_dir if target_dir.is_absolute() else ROOT_DIR / target_dir
    index_path = report_dir / "index.html"
    if not index_path.exists():
        raise FileNotFoundError(f"Report index.html not found: {index_path}")

    _safe_clear_dir(target_dir)
    shutil.copytree(report_dir, target_dir, dirs_exist_ok=True)

    output_index = target_dir / "index.html"
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
        target = target_dir / unquote(asset_href)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        rewrites[href] = asset_href

    for old, new in rewrites.items():
        html = html.replace(f'"{old}"', f'"{new}"')
    output_index.write_text(html, encoding="utf-8")
    return output_index


def _write_history_index(history_dir: Path, audit_slug: str) -> None:
    audits_dir = history_dir / "audits"
    links = []
    for index_path in sorted(audits_dir.glob("*/index.html"), key=lambda path: path.stat().st_mtime, reverse=True):
        slug = index_path.parent.name
        label = slug.replace("-", " ")
        links.append(f'<li><a href="/audits/{slug}/">{label}</a></li>')
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url=/audits/{audit_slug}/">
  <title>Audit Reports</title>
</head>
<body>
  <main>
    <h1>Audit Reports</h1>
    <p><a href="/audits/{audit_slug}/">Open latest audit report</a></p>
    <ul>{''.join(links)}</ul>
  </main>
</body>
</html>"""
    (history_dir / "index.html").write_text(html, encoding="utf-8")


def package_report_for_vercel(report_dir: Path, static_dir: Path, audit_slug: str = "") -> Path:
    static_dir = static_dir if static_dir.is_absolute() else ROOT_DIR / static_dir
    slug = _safe_slug(audit_slug) if audit_slug else ""
    if not slug:
        return _copy_report_with_assets(report_dir, static_dir)

    history_dir = HISTORY_STATIC_DIR
    current_report_dir = history_dir / "audits" / slug
    output_index = _copy_report_with_assets(report_dir, current_report_dir)
    _write_history_index(history_dir, slug)

    _safe_clear_dir(static_dir)
    shutil.copytree(history_dir, static_dir, dirs_exist_ok=True)
    return static_dir / "audits" / slug / "index.html"


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


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _ensure_vercel_project_link(static_dir: Path) -> None:
    org_id = _env("VERCEL_ORG_ID")
    project_id = _env("VERCEL_PROJECT_ID")
    if not org_id or not project_id:
        return

    vercel_dir = static_dir / ".vercel"
    vercel_dir.mkdir(parents=True, exist_ok=True)
    project_json = vercel_dir / "project.json"
    project_json.write_text(
        json.dumps({"orgId": org_id, "projectId": project_id}, indent=2),
        encoding="utf-8",
    )


def deploy_to_vercel(static_dir: Path, *, production: bool = True, public_path: str = "", prefer_alias: bool = False) -> str:
    executable = _vercel_executable()
    static_dir = static_dir if static_dir.is_absolute() else ROOT_DIR / static_dir
    _ensure_vercel_project_link(static_dir)

    command = [executable, "deploy", ".", "--yes"]
    if production:
        command.append("--prod")
    vercel_token = _env("VERCEL_TOKEN")
    if vercel_token:
        command.extend(["--token", vercel_token])
    vercel_scope = _env("VERCEL_SCOPE")
    if vercel_scope:
        command.extend(["--scope", vercel_scope])
    completed = subprocess.run(
        command,
        cwd=str(static_dir),
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
        output_tail = "\n".join(output.splitlines()[-12:]).strip()
        detail = f"\n\nVercel output:\n{output_tail}" if output_tail else ""
        raise RuntimeError(f"Vercel deployment failed with exit code {completed.returncode}.{detail}")
    url = _public_deployment_url(output, prefer_alias=prefer_alias)
    if public_path and url:
        return f"{url.rstrip('/')}/{public_path.strip('/')}/"
    return url


def _clean_cli_url(value: str) -> str:
    return value.strip().strip('",.)')


def _public_deployment_url(output: str, *, prefer_alias: bool = False) -> str:
    labeled_candidates: list[tuple[str, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith(("preview:", "production:", "ready:", "deployed to:")):
            labeled_candidates.extend((lowered.split(":", 1)[0], _clean_cli_url(url)) for url in re.findall(r"https://[^\s]+", stripped))
        elif lowered.startswith("aliased:"):
            labeled_candidates.extend(("aliased", _clean_cli_url(url)) for url in re.findall(r"https://[^\s]+", stripped))

    candidates = [url for _label, url in labeled_candidates]
    if not candidates:
        candidates = [_clean_cli_url(url) for url in re.findall(r"https://[^\s]+", output)]

    public_labeled_candidates = [
        (label, url)
        for label, url in labeled_candidates
        if ".vercel.app" in url
        and "api.vercel.com" not in url
        and "vercel.com/" not in url.replace(".vercel.app", "")
    ]
    if prefer_alias:
        for label, url in public_labeled_candidates:
            if label == "aliased":
                return url
    for label, url in public_labeled_candidates:
        if label != "aliased":
            return url

    public_candidates = [
        url
        for url in candidates
        if ".vercel.app" in url
        and "api.vercel.com" not in url
        and "vercel.com/" not in url.replace(".vercel.app", "")
    ]
    if public_candidates:
        return public_candidates[0]

    fallback_candidates = [
        url
        for url in candidates
        if "api.vercel.com" not in url and not url.startswith("https://vercel.com/")
    ]
    return fallback_candidates[-1] if fallback_candidates else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Package and optionally deploy an audit report as a static Vercel site.")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_STATIC_DIR))
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--prod", action="store_true", help="Create a production deployment and update the production alias.")
    parser.add_argument("--preview", action="store_true", help="Create a preview deployment instead of production.")
    parser.add_argument("--audit-slug", default="", help="Optional stable slug for a public /audits/<slug>/ report URL.")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    static_dir = Path(args.output_dir)
    audit_slug = _safe_slug(args.audit_slug) if args.audit_slug else ""
    output_index = package_report_for_vercel(report_dir, static_dir, audit_slug=audit_slug)
    print(f"Vercel static report packaged at: {output_index}")

    if args.deploy:
        production = args.prod or not args.preview
        url = deploy_to_vercel(
            static_dir,
            production=production,
            public_path=f"audits/{audit_slug}" if audit_slug else "",
            prefer_alias=bool(audit_slug and production),
        )
        if not url:
            raise RuntimeError("Vercel deployment completed but no deployment URL was found in CLI output.")
        print(f"Vercel deployment URL: {url}")


if __name__ == "__main__":
    main()
