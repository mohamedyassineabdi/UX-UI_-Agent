# UX/UI Auditor

UX/UI Auditor is a Python-first audit platform that crawls websites, captures UI evidence, runs structured UX/UI checks, and generates client-ready reports.

It supports 4 main operating modes:
- `Detailed website audit`: crawl a live site, audit discovered pages, export workbook results, and generate a static UX/UI report.
- `GTM audit`: reuse the website audit artifacts and synthesize them into a 7-axis go-to-market UX/UI report.
- `Screenshot audit`: upload screenshots only and generate a GTM-style report without crawling a live website.
- `Mobile app audit`: connect to Appium and extract Android screen, hierarchy, and safe-interaction artifacts.

## Product Screens

### Home page

![UX/UI Auditor home page](docs/screenshots/ui-home-page.png)

### Audit launcher

![UX/UI Auditor audit launcher](docs/screenshots/ui-launch-page.png)

## What The Project Does

- Crawls the homepage and extracts primary navigation, auth links, and menu structure.
- Visits each unique discovered page with Playwright.
- Captures full-page screenshots, scroll screenshots, and safe interaction evidence.
- Extracts structured HTML signals: headings, forms, labels, navigation, media, and text content.
- Extracts rendered UI signals: components, layout patterns, typography, consistency, CTA clarity, and accessibility-related cues.
- Runs rule-based checks across:
  - `Content`
  - `Labeling`
  - `Presentation`
  - `Navigation`
  - `Interaction`
  - `Feedback`
  - `Forms`
  - `Visual hierarchy`
- Exports workbook-ready results and static HTML reports.
- Optionally packages and deploys GTM reports to Vercel.

## Main Workflow

The default website pipeline runs in this order:

1. `navigator/crawler.py`
2. `python -m src.main`
3. `python -m src.audit.checks.run_sheet_checks`
4. Detailed mode:
   - `python -m src.audit.export.write_checks_to_workbook`
   - `python -m src.report.generate_audit_report`
5. GTM mode:
   - `python -m src.gtm_audit.generate_gtm_audit`
   - `python -m src.gtm_audit.generate_gtm_report`
   - `python -m src.gtm_audit.vercel_static_deploy`

## Generated Artifacts

Key outputs are written under `shared/generated` and `shared/output`.

- `shared/generated/website_menu.json`
- `shared/generated/html_extraction.json`
- `shared/generated/html_cleaned.json`
- `shared/generated/rendered_ui_extraction.json`
- `shared/generated/sheet_checks.json`
- `shared/generated/UX-Audit-Workbook-final.xlsx`
- `shared/generated/audit-report/index.html`
- `shared/generated/gtm_audit.json`
- `shared/generated/gtm-report/index.html`
- `shared/generated/vercel-gtm-report/index.html`
- `shared/output/results/audit-results_*.json`
- `shared/generated/mobile-audits/<job-id>/...`

## Requirements

- Python 3.11+
- Playwright Chromium
- Node.js and npm
- Optional for GTM multimodal review: Ollama or another compatible AI backend
- Optional for mobile audit: Appium, Android SDK platform-tools, emulator/device

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Environment Variables

The crawler, AI review layer, and GTM vision review read configuration from `.env`.

Common variables:

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_API_KEY=
```

Optional GTM vision overrides:

```bash
OLLAMA_VISION_MODEL=llama3.2-vision
GTM_VISION_MODEL=llama3.2-vision
GTM_VISION_BASE_URL=http://localhost:11434
GTM_VISION_API_KEY=
```

Optional automation flags:

```bash
CRAWLER_USE_AI_NAV=1
GTM_AUTO_DEPLOY=1
```

## Run

### Local UI

```bash
npm run ui
```

Open:

```text
http://127.0.0.1:8787
```

The local launcher can:
- run website audits
- run screenshot-only GTM audits
- run mobile extraction jobs
- stream logs and progress
- package and deploy reports to Vercel

On PowerShell systems where `npm.ps1` is blocked:

```bash
npm.cmd run ui
```

### Detailed website audit

```bash
npm run scan -- https://example.com
```

Equivalent:

```bash
python scripts/run_pipeline.py https://example.com
```

### GTM website audit

```bash
npm run scan -- https://example.com --mode gtm
```

Equivalent:

```bash
python scripts/run_pipeline.py https://example.com --mode gtm
```

### GTM audit with Vercel deployment

```bash
npm run scan:gtm:deploy -- https://example.com
```

Equivalent:

```bash
python scripts/run_pipeline.py https://example.com --mode gtm --deploy-vercel
```

### Screenshot-only GTM audit

```bash
npm run audit:screenshots -- --screenshots path/to/shot-1.png path/to/shot-2.png --site-name "Client Review"
```

### Mobile audit

```bash
npm run audit:mobile -- --app-package com.example.app --app-activity .MainActivity
```

## Useful Flags

```bash
python scripts/run_pipeline.py https://example.com --workbook-template "shared/generated/UX-Audit-Workbook-template.xlsx"
python scripts/run_pipeline.py https://example.com --skip-workbook
python scripts/run_pipeline.py https://example.com --report-out "shared/generated/audit-report"
python scripts/run_pipeline.py https://example.com --mode gtm --skip-vision
```

To regenerate the detailed report from existing artifacts:

```bash
npm run report -- --checks shared/generated/sheet_checks.json
```

## Vercel Deployment

The GTM report packager writes a static deployable folder to:

```text
shared/generated/vercel-gtm-report/index.html
```

If deployment fails because the CLI is missing or unauthenticated:

```bash
npm i -g vercel
vercel login
vercel link --yes
```

## Repo Structure

```text
src/
  audit/          Website page audit, extraction, and checks engine
  gtm_audit/      GTM synthesis, vision review, report generation, Vercel packaging
  mobile_audit/   Android/Appium extraction and bounded exploration
  report/         Detailed audit HTML report generation
  ui/             Local launcher server and static frontend
  utils/          Shared file and URL helpers
navigator/        Crawl and navigation discovery
scripts/          Pipeline orchestration
deploy/           VPS/systemd/nginx deployment assets
shared/           Generated artifacts, reports, screenshots, and results
```

## Deployment

For VPS deployment on Ubuntu, see:

- `deploy/README.md`
- `deploy/setup_ubuntu.sh`
- `deploy/ux-ui-auditor.service`
- `deploy/nginx-ux-ui-auditor.conf`

The Docker image starts the UI server directly:

```bash
docker build -t ux-ui-auditor .
docker run -p 10000:10000 ux-ui-auditor
```

## Notes

- The UI frontend is served as static assets by the Python server in `src/ui/server.py`.
- The screenshot and GTM paths depend on the configured AI backend when vision review is enabled.
- Mobile mode currently focuses on extraction and bounded safe exploration rather than a fully scored report pipeline.
