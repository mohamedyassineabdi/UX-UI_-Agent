# UX/UI Auditor — task 1

step 1 does the following:
- reads `input/pages.json`
- normalizes URLs
- removes duplicates
- visits each unique page once
- takes a full-page screenshot
- writes a JSON results file

## Install

```bash
npm install
npx playwright install