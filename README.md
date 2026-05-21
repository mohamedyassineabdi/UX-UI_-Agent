# UX/UI Auditor

Local audit tool for websites, Android mobile apps, uploaded screenshots, and Figma files. The launcher can crawl pages, capture evidence, run UX/UI checks, score the experience, and generate static reports that can be reviewed locally or deployed.

## What It Audits

- Website URLs: crawls representative pages, captures screenshots and page evidence, measures performance KPIs, checks accessibility, and generates an audit report.
- Uploaded screenshots: builds an audit from provided visual evidence when live crawling is not available.
- Android apps: connects through Appium, captures visible screens, explores safe navigation paths, and produces a mobile audit.
- Figma links: fetches the Figma file through the Figma API, analyzes frames/components, captures design evidence, and generates an editable audit report.

The current UX/UI scoring model uses five axes:

- Performance & Task Execution
- Flow & Architecture
- Trust & Accessibility
- Visual & UI Consistency
- Content & Microcopy

## Requirements

- Python 3.11+
- Node.js if you want to use the npm script shortcuts
- Chromium for Playwright
- Appium server and Android emulator/device for mobile audits
- Figma personal access token for Figma audits

Install Python dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

The project uses Python modules directly, so `npm install` is only needed if you later add Node dependencies. The existing npm scripts are convenience wrappers around Python commands.

## Environment

Create a local `.env` file. It is ignored by git.

```env
# Required for Figma audits
FIGMA_TOKEN=figd_your_personal_access_token

# Optional: rotate across several Figma tokens for larger files
FIGMA_TOKENS=figd_token_1,figd_token_2

# Optional Figma network settings
FIGMA_VERIFY_SSL=true
FIGMA_CA_BUNDLE=
FIGMA_TRUST_ENV_PROXY=false

# Optional AI review/enrichment
AI_REVIEW_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_API_KEY=
OLLAMA_VISION_MODEL=llama3.2-vision

# Optional report deployment
VERCEL_TOKEN=
```

If your company proxy or antivirus breaks TLS validation for the Figma API, set `FIGMA_CA_BUNDLE` to the local CA certificate bundle path instead of disabling SSL verification.

## Run The Local UI

```bash
npm run ui
```

Then open the URL printed by the server, usually:

```text
http://127.0.0.1:8787/
```

From the launcher you can start website, mobile, screenshot, or Figma audits depending on the available inputs and environment variables.

## CLI Commands

Website audit:

```bash
python scripts/run_pipeline.py https://example.com --mode gtm
```

Website audit with Vercel deployment:

```bash
python scripts/run_pipeline.py https://example.com --mode gtm --deploy-vercel
```

Figma audit:

```bash
python -m src.figma_audit_runner "https://www.figma.com/design/FILE_KEY/Project?node-id=0-1" --job-id figma-test
```

Equivalent npm shortcut:

```bash
npm run audit:figma -- "https://www.figma.com/design/FILE_KEY/Project?node-id=0-1" --job-id figma-test
```

Mobile audit:

```bash
python -m src.mobile_audit.run_mobile_audit --app-package your.package.name --app-activity your.MainActivity
```

Screenshot-based audit:

```bash
python -m src.gtm_audit.generate_screenshot_gtm_audit
```

Some module and output names still contain legacy internal naming. They are technical names only and do not need to be used as client-facing audit language.

## Figma Audit Notes

Figma audits require `FIGMA_TOKEN` or `FIGMA_TOKENS`. The token must have access to the target file.

Supported Figma URL forms include:

- `https://www.figma.com/file/...`
- `https://www.figma.com/design/...`
- `https://www.figma.com/proto/...`
- `https://www.figma.com/board/...`

The Figma report keeps the editable report feature from the imported audit package. In the generated report, users can edit scoring, issue text, recommendations, and screenshot evidence before using the final result.

## Generated Output

Generated artifacts are written under `shared/generated/` and are ignored by git:

- `shared/generated/audit-report/`
- `shared/generated/gtm-report/`
- `shared/generated/vercel-gtm-report/`
- `shared/generated/vercel-audit-history/`
- `shared/generated/mobile-audits/`
- `shared/generated/screenshot-audits/`
- `shared/generated/figma-audits/`

Figma audit output for a job is usually under:

```text
shared/generated/figma-audits/<job-id>/
```

## Project Structure

```text
figma_audit/                 Figma audit pipeline, checks, evidence, and report builder
scripts/run_pipeline.py      Main website audit pipeline launcher
src/audit/                   Website extraction and check logic
src/figma_audit_runner.py    Figma audit runner used by the UI and CLI
src/gtm_audit/               UX/UI report generation modules
src/mobile_audit/            Android/Appium extraction and mobile report logic
src/report/                  Static detailed report generator
src/ui/                      Local audit launcher server and frontend
shared/generated/            Local generated reports and audit artifacts
```

## Git / Push Hygiene

The `.gitignore` excludes local secrets, virtual environments, caches, Playwright artifacts, generated reports, generated screenshots, Figma audit outputs, Vercel local state, and imported archive files.

Ignored rules do not untrack files that were already committed. If git still shows old generated artifacts, remove them from the index once with `git rm --cached <path>` and then commit the updated ignore rules.

Before pushing, check:

```bash
git status --short
git diff -- .gitignore requirements.txt README.md
```
