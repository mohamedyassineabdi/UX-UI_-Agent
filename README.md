# UX/UI Auditor

Current pipeline:
- runs the Python crawler in `navigator/crawler.py`
- writes and reads `shared/generated/website_menu.json`
- normalizes and deduplicates extracted pages
- visits each unique page once
- takes a full-page screenshot
- tests safe interactions
- writes `shared/generated/html_cleaned.json`
- writes `shared/generated/rendered_ui_extraction.json`
- generates `shared/generated/sheet_checks.json`
- exports a final workbook `.xlsx`
- generates a static audit landing page in `shared/generated/audit-report/index.html`

The crawler and AI review code use Ollama configuration from the repo `.env` file.
Typical variables are `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and optional `OLLAMA_API_KEY`.

For the GTM audit mode, you can also set a dedicated multimodal model with:
- `OLLAMA_VISION_MODEL`
- or `GTM_VISION_MODEL`

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

Keep the existing usage:

```bash
npm run scan -- https://example.com
```

Run the local React launcher UI:

```bash
npm run ui
```

Then open `http://127.0.0.1:8787`. The UI can run the existing website-audit pipeline or accept multiple uploaded screenshots for a screenshot-only GTM audit. It shows process progress, packages the generated report with its assets, deploys it to Vercel, and shows the final Vercel link.

On PowerShell setups where `npm.ps1` is blocked by execution policy, use:

```bash
npm.cmd run ui
```

Run the new GTM-oriented 7-axis audit mode:

```bash
npm run scan -- https://example.com --mode gtm
```

The full pipeline now runs these stages in order:
1. `navigator/crawler.py`
2. `python -m src.main`
3. `python -m src.audit.checks.run_sheet_checks`
4. detailed mode: `python -m src.audit.export.write_checks_to_workbook`
5. detailed mode: `python -m src.report.generate_audit_report`

In GTM mode, stages 4 and 5 become:
4. `python -m src.gtm_audit.generate_gtm_audit`
5. `python -m src.gtm_audit.generate_gtm_report`
6. `python -m src.gtm_audit.vercel_static_deploy` packages the GTM report into a static Vercel-ready folder

Direct Python usage also works:

```bash
python scripts/run_pipeline.py https://example.com
```

Optional flags:

```bash
python scripts/run_pipeline.py https://example.com --workbook-template "shared/generated/UX-Audit-Workbook-template.xlsx"
python scripts/run_pipeline.py https://example.com --skip-workbook
python scripts/run_pipeline.py https://example.com --report-out "shared/generated/audit-report"
```

To package and deploy the GTM report to Vercel after generation:

```bash
npm run scan:gtm:deploy -- https://example.com
```

Equivalent direct usage:

```bash
python scripts/run_pipeline.py https://example.com --mode gtm --deploy-vercel
```

If you want every GTM run to attempt deployment, set:

```bash
GTM_AUTO_DEPLOY=1
```

The static package is written to `shared/generated/vercel-gtm-report/index.html`. If Vercel deployment fails because the CLI is missing or unauthenticated, install or login with:

```bash
npm i -g vercel
vercel login
```

Running the crawler directly also writes to `shared/generated/website_menu.json` by default:

```bash
python navigator/crawler.py https://example.com
```

To regenerate only the audit landing page from existing artifacts:

```bash
npm run report -- --checks shared/generated/sheet_checks.json
```
