# One Page UX/UI Audit

Standalone home-page audit tool. It does not depend on the parent project.

It opens a given website home page, captures desktop and mobile screenshots, extracts visible UI signals, runs a focused UX/UI audit, and generates:

- `audit.json`
- `report.html`
- desktop/mobile screenshots
- evidence crops for important findings

## Setup

```powershell
cd one_page
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```powershell
python audit_homepage.py https://example.com
```

Optional output folder:

```powershell
python audit_homepage.py https://example.com --out output/example
```

Open the generated report:

```powershell
start output\latest\report.html
```

## What It Checks

- Responsive behavior between desktop and mobile
- Navigation/menu discoverability
- Primary CTA clarity
- Above-the-fold value proposition
- Accessibility basics: headings, alt text, labels, landmarks
- Form friction
- Visual hierarchy and text scale
- Trust/proof signals
- Content scanability
- Basic performance timing

## Output

By default, results are written to:

```text
one_page/output/latest/
```

The folder is self-contained and can be shared with the generated screenshots and report.
