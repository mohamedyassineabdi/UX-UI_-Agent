# Figma Audit

Small Python pipeline for fetching and normalizing a Figma file or Figma node.
Criteria verification is currently disabled.

## Project Structure

```text
figma_audit/
  cli.py                 Command-line wrapper for local runs
  config.py              Environment variables and output paths
  pipeline.py            Reusable orchestration API for larger projects
  ingestion/             Figma URL parsing and API fetching
  normalization/         Raw Figma JSON to stable internal models
  audit/                 Empty audit runner placeholder for future rules
  models/                Pydantic data contracts
  resources/             Packaged criteria JSON for integrations
  utils/                 File IO helpers

data/
  annotations/           Red-rectangle screenshots linked from issue JSON
  annotations/_render_cache/
                         Versioned Figma render cache to avoid repeat Images API calls
  criteria/              Editable criteria JSON copy for local work
  detections/            Binary draft detections and draft issue evidence
  raw/                   Raw Figma API snapshots
  raw/cache/             Per-file/per-node raw cache used after rate limits
  normalized/            Normalized file and empty audit JSON output
  parts/                 Split audit JSON text chunks, if output splitting is enabled

main.py                  Backward-compatible CLI entry point
config.py                Backward-compatible config import shim
```

## Run Locally

Create a `.env` file with:

```env
FIGMA_TOKEN=your_figma_token
```

For legitimate quota fallback, you can provide multiple authorized tokens. The
client will try the next token only when the current token is rate-limited, and
it will not retry a blocked token before Figma's `Retry-After` window. Do not
use this to bypass Figma's terms; personal access tokens from the same account
usually share the same user/plan quota.

```env
FIGMA_TOKENS=token_from_authorized_user_1,token_from_authorized_user_2
```

Optional client-facing copy polishing uses Ollama Cloud. When this key is set,
the report asks `gpt-oss:120b` to rewrite detector findings into clearer
stakeholder language. If the key is missing or the request fails, the command
still finishes with the built-in deterministic report copy.

```env
OLLAMA_API_KEY=your_ollama_cloud_api_key
OLLAMA_REPORT_MODEL=gpt-oss:120b
```

Defaults are tuned for large/community Figma files:

```env
FIGMA_FETCH_VARIABLES=false
FIGMA_RATE_LIMIT_RETRIES=8
FIGMA_MAX_RETRY_SLEEP_SECONDS=90
FIGMA_MAX_RETRY_AFTER_SECONDS=3600
FIGMA_MIN_REQUEST_INTERVAL_SECONDS=1
```

This keeps API usage low, waits when Figma returns `429`, and reuses matching cached raw data from `data/raw/cache/` if a live request is temporarily blocked.
If Figma asks the tool to wait longer than `FIGMA_MAX_RETRY_AFTER_SECONDS`, the command stops live retries immediately because the token quota is effectively exhausted for that window.
When Figma Images API annotations are enabled, rendered images are cached under `data/annotations/_render_cache/` by file key, Figma version, render node, format, and scale. Re-running the same version can reuse those pixels without another render URL request or image download.
For maximum speed on a file/node that has already been fetched, use `--cache-first` to skip the live Figma fetch entirely and audit the matching raw snapshot.

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the pipeline:

```bash
python main.py "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2"
```

Run the whole audit-to-report flow with one CMD command:

```bat
audit "https://www.figma.com/proto/FILEKEY/FileName?node-id=1-2"
```

That shortcut runs `python main.py full-report`, skips extra Figma Images API annotation calls by default, captures real Figma page screenshots when possible, rewrites the report copy with Ollama Cloud when configured, rebuilds `data/reports/draft_detection_review.html`, and opens it.

For big files, this is the recommended command:

```bat
audit "https://www.figma.com/proto/FILEKEY/FileName?node-id=1-2" --no-polish
```

For the fastest repeat run after a successful fetch:

```bat
python main.py full-report "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2" --cache-first --no-polish --no-open
```

For a quota-safe repeat run that never attempts a live Figma API fetch, use
offline mode. It requires a matching raw cache in `data/raw/cache/`, disables
fallback Figma Images API annotations, skips browser page capture, skips Ollama
polishing, and does not auto-open the report:

```bat
python main.py full-report "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2" --offline
```

If the cache is missing, the command fails with the exact expected cache path
and no live Figma request is made. Use `--cache-only` when you only want that
fetch protection but still want to choose the other report options yourself.

If you want Ollama rewriting too:

```bat
audit "https://www.figma.com/proto/FILEKEY/FileName?node-id=1-2" --force-polish
```

Only enable fallback Figma Images API screenshots when real browser screenshots are not enough:

```bat
audit "https://www.figma.com/proto/FILEKEY/FileName?node-id=1-2" --max-annotations 10
```

Force a fresh Ollama rewrite instead of using the cached client copy:

```bat
audit "https://www.figma.com/proto/FILEKEY/FileName?node-id=1-2" --force-polish
```

Skip Ollama rewriting for a fully offline/deterministic run:

```bat
audit "https://www.figma.com/proto/FILEKEY/FileName?node-id=1-2" --offline
```

Or use the explicit subcommand with integration-friendly flags:

```bash
python main.py run "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2" --no-split
python main.py run "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2" --no-save
python main.py run "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2" --no-annotations
python main.py run "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2" --no-detections
```

Use more workers without skipping pages or issues:

```bash
python main.py run "https://www.figma.com/design/FILEKEY/FileName" --detection-workers 4 --annotation-workers 4 --max-runtime-seconds 1800
```

The same settings can be placed in `.env`:

```env
DETECTION_WORKERS=4
ANNOTATION_WORKERS=4
```

For a Ryzen 7 4000 laptop with a GTX 1650 Ti, start with `4`. If Figma returns rate-limit errors or the laptop becomes unresponsive, use `2`. If it stays cool and stable, try `6`; going much higher usually helps less because Figma API/network time becomes the bottleneck.

During a run, every major stage writes its own output so progress is visible and recoverable:

```text
data/run_status.json                         Live run status and stage timings
data/raw/raw_bundle.json                     Raw Figma API extraction
data/raw/extraction_summary.json             Raw extraction summary
data/normalized/normalized_file.json         Normalized extracted nodes/frames/components
data/normalized/extraction_summary.json      Normalized extraction counts
data/extracted/audit_info.json               Complete static audit input used by analysis
data/normalized/audit_result.json            Final audit-rule output
data/normalized/audit_summary.json           Audit count summary
data/detections/draft_detections.json        Draft analysis/issues
data/detections/analysis_summary.json        Draft analysis count summary
data/detections/evidence_quality.json        Scope, applicability, and evidence-strength summary
data/annotations/                            Annotated screenshots when enabled
data/final_result.json                       Client-ready final JSON payload
```

If a stage fails, `data/run_status.json` is still updated with the failed stage and error message. `data/final_result.json` is written only after all enabled stages complete.

Run detections against an existing normalized file without refetching Figma:

```bash
python main.py detect-normalized --input data/normalized/normalized_file.json --output data/detections/draft_detections.json
```

Smoke-test live screenshot rendering for one draft issue:

```bash
python main.py detect-normalized --input data/normalized/normalized_file.json --output data/detections/draft_detections_annotated_smoke.json --annotations --max-annotations 1
```

Build the local review report:

```bash
python main.py build-report --detections data/detections/draft_detections_annotated_smoke.json --output data/reports/draft_detection_review.html
```

Validate the criteria catalog:

```bash
python main.py validate-criteria
```

## Integrate In Another Project

Use `run_pipeline` as the stable API:

```python
from figma_audit import load_criteria_catalog, run_pipeline

criteria = load_criteria_catalog()

outputs = run_pipeline(
    "https://www.figma.com/design/FILEKEY/FileName?node-id=1-2",
    save_outputs=False,
)

normalized_nodes = outputs.normalized_file.nodes
issues = outputs.audit_result.issues  # Empty while criteria are disabled
```

Use `FIGMA_AUDIT_CRITERIA_PATH` if a larger project wants to load a different criteria JSON file.

## Binary Draft Detections

The criteria system is binary at the draft detection layer: each criterion has `exists: true` when at least one draft problem is detected, otherwise `exists: false`.

Draft detections are separate from final audit rules and are saved to:

```text
data/detections/draft_detections.json
```

Current detector coverage is intentionally conservative:

- `task_execution`: destructive action visible without nearby recovery/confirmation wording
- `flow_architecture`: generic labels inside navigation-like structures
- `trust_accessibility`: measurable low text contrast for solid text/background colors
- `ui_consistency`: clear numeric style outlier in repeated control-like component families
- `visual_brand`: low-confidence flat text hierarchy signal in large frames
- `content_microcopy`: obvious placeholder or generic visible copy
- `market_alignment`: low-confidence generic offer wording without proof or commercial CTA cues

High-confidence detectors use objective geometry, color, text, or repeated style evidence. Low-confidence detectors are intentionally labeled because visual brand and market alignment still require human judgment before becoming final audit issues.

Example detection JSON shape:

```json
{
  "criterion_status": [
    {
      "criterion_id": "trust_accessibility",
      "exists": true,
      "issue_count": 1,
      "confidence": "high",
      "detector_ids": ["low_text_contrast"]
    }
  ],
  "draft_issues": [
    {
      "criterion": "trust_accessibility",
      "evidence": {
        "detector_id": "low_text_contrast",
        "binary_exists": true,
        "confidence": "high"
      },
      "visual_evidence": []
    }
  ]
}
```

If screenshots are enabled and a draft issue has `location.node_id`, `visual_evidence` is filled automatically with an annotated image link.

## Accuracy And Evidence Contract

The audit is designed to work with any valid Figma file, design, prototype, or board URL. It can run on a full file or a selected `node-id`.

To keep results accurate, every run now writes an evidence-quality layer to:

```text
data/detections/evidence_quality.json
```

That file records:

- the actual scope audited: full file or selected node
- how many pages, frames, nodes, text nodes, geometry nodes, and mobile viewport candidates were available
- criterion-by-criterion coverage: detected with evidence, no static issue detected, or manual review required
- a portable check-by-check evaluation grid in `criteria_evaluations`
- issue evidence strength: `strong`, `moderate`, or `weak`
- how many issues are backed by real prototype screenshots, Figma rendered annotations, or geometry fallback previews

Check statuses are intentionally conservative:

- `needs_improvement`: a visible static finding matched the check
- `needs_review_with_evidence`: evidence exists, but the check is judgment-heavy or AI-assisted
- `no_static_issue_detected`: no visible issue was found by the static detector
- `manual_review_required`: runtime behavior, hidden states, business logic, or broad judgment are needed
- `not_evaluated`: the framework check is currently disabled for automation

Findings are strongest when measured detector evidence is paired with real or rendered visual evidence. Static Figma data is used for geometry, text, color, layout, and component checks. Runtime behavior, hidden states, business logic, formal accessibility conformance, and broad market claims remain human-review items unless visible evidence supports them.

## Visual Evidence

When future audit rules create issues with `location.node_id`, the pipeline can attach annotated screenshots automatically. The screenshot system:

- renders the containing Figma frame when possible
- maps the issue node `absoluteRenderBounds` into screenshot pixel coordinates when available
- falls back to `absoluteBoundingBox` when render bounds are unavailable
- draws a red rectangle around the exact problem area
- saves images in `data/annotations/`
- writes the link and rectangle data into `issue.visual_evidence`

Example issue JSON shape:

```json
{
  "id": "issue-1",
  "location": {
    "node_id": "12:34"
  },
  "visual_evidence": [
    {
      "type": "annotated_screenshot",
      "image_path": "data/annotations/issue-1__node-12-34.png",
      "target_node_id": "12:34",
      "render_node_id": "1:2",
      "rectangle_px": {
        "x": 120,
        "y": 80,
        "width": 240,
        "height": 48
      },
      "accuracy": "high_static_geometry"
    }
  ]
}
```

Accuracy note: rectangles are computed from Figma absolute coordinates and the actual rendered image dimensions. `absoluteRenderBounds` is preferred because it better represents rendered pixels for strokes and effects. This is the highest-accuracy static-Figma approach, but final implemented UI may still differ because of runtime states, CSS, browser layout, or code-only behavior.

## Review Report

`build-report` creates a static client-facing HTML page for draft detections. It includes:

- executive summary and readiness scoring
- real page screenshots when available
- circled visual callouts that show where the problem appears
- clear explanations of what is wrong, why it matters, and what to fix
- criterion-by-criterion stories and prioritized recommendations
- optional Ollama Cloud rewriting for more polished client language

The report does not change the source detection JSON. Ollama-polished wording is cached separately in `data/reports/client_copy.json` so the HTML can be rebuilt without repeating the model call.

The pipeline is ordered as:

1. Ingestion fetches raw Figma data.
2. Normalization converts raw JSON into Pydantic models.
3. Audit runner returns an empty result while criteria are disabled.
4. Optional output saving writes JSON files into `data/`.

## Main Extension Points

- Add future criteria in `figma_audit/audit/` when verification is needed again.
- Register future rule groups in `figma_audit/audit/audit_runner.py`.
- Extend normalized fields in `figma_audit/models/normalized_models.py`.
- Change API behavior in `figma_audit/ingestion/figma_client.py`.

## Quality Checks

```bash
python main.py validate-criteria
python main.py detect-normalized --input data/normalized/normalized_file.json --output data/detections/draft_detections.json
python main.py build-report --detections data/detections/draft_detections.json --output data/reports/draft_detection_review.html
python -m unittest discover -s tests
python -m compileall figma_audit main.py config.py tests
```

Current behavior is intentional: the audit runner returns zero issues until criteria verification is explicitly re-enabled.

To remove generated outputs and recreate empty output folders:

```bash
python main.py cleanup-generated --yes
```
