# UX/UI Auditor

Current pipeline:
- runs the Python crawler in `navigator/crawler.py`
- writes and reads `shared/generated/website_menu.json`
- normalizes and deduplicates extracted pages
- visits each unique page once
- takes a full-page screenshot
- tests safe interactions
- writes `shared/generated/person_a_cleaned.json`
- writes `shared/generated/rendered_ui_extraction.json`
- generates `shared/generated/person_a_sheet_checks_v2.json`
- exports a final workbook `.xlsx`

The crawler and AI review code use Ollama configuration from the repo `.env` file.
Typical variables are `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and optional `OLLAMA_API_KEY`.

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

The full pipeline now runs these stages in order:
1. `navigator/crawler.py`
2. `python -m src.main`
3. `python -m src.audit.checks.run_sheet_checks`
4. `python -m src.audit.export.write_checks_to_workbook`

Direct Python usage also works:

```bash
python scripts/run_pipeline.py https://example.com
```

Optional flags:

```bash
python scripts/run_pipeline.py https://example.com --workbook-template "shared/generated/UX-Audit-Workbook-template.xlsx"
python scripts/run_pipeline.py https://example.com --skip-workbook
```

Running the crawler directly also writes to `shared/generated/website_menu.json` by default:

```bash
python navigator/crawler.py https://example.com
```
