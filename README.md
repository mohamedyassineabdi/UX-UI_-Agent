# UX/UI Auditor

Current pipeline:
- runs the Python crawler in `navigator/crawler.py`
- writes and reads `shared/generated/website_menu.json`
- normalizes and deduplicates extracted pages
- visits each unique page once
- takes a full-page screenshot
- tests safe interactions
- writes a JSON audit results file

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

Direct Python usage also works:

```bash
python scripts/run_pipeline.py https://example.com
```

Running the crawler directly also writes to `shared/generated/website_menu.json` by default:

```bash
python navigator/crawler.py https://example.com
```
