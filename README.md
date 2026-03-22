# UX/UI Auditor

Current pipeline:
- runs the Python crawler in `navigator/crawler.py`
- reads `shared/generated/website_menu.json`
- normalizes and deduplicates extracted pages
- visits each unique page once
- takes a full-page screenshot
- tests safe interactions
- writes a JSON audit results file

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

Direct Python usage also works:

```bash
python scripts/run_pipeline.py https://example.com
```
