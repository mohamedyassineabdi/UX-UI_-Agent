from __future__ import annotations

import argparse
import html
import json
import os
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from .evidence import build_gtm_spotlight


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
DEFAULT_GTM_AUDIT = GENERATED_DIR / "gtm_audit.json"
DEFAULT_OUTPUT_DIR = GENERATED_DIR / "gtm-report"

AXIS_LABELS = {
    "task_execution": "Performance & Task Execution",
    "flow_architecture": "Flow & Architecture",
    "trust_accessibility": "Trust & Accessibility",
    "ui_consistency": "Visual & UI Consistency",
    "content_microcopy": "Content & Microcopy",
}


def ey_studio_logo_svg(class_name: str = "ey-studio-logo") -> str:
    return f"""
    <svg class="{html.escape(class_name)}" viewBox="0 0 400 154" role="img" aria-label="EY Studio plus" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ey-plus-gradient" x1="350" y1="91" x2="389" y2="130" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#ffffff"/>
          <stop offset="0.22" stop-color="#28d7ff"/>
          <stop offset="0.48" stop-color="#8b35ff"/>
          <stop offset="0.72" stop-color="#ff2aa1"/>
          <stop offset="1" stop-color="#ffe600"/>
        </linearGradient>
      </defs>
      <polygon points="0,52 149,0 149,29 0,52" fill="#ffe600"/>
      <text x="0" y="132" fill="currentColor" font-family="Arial Black, Arial, Helvetica, sans-serif" font-size="79" font-weight="900" letter-spacing="-5">EY</text>
      <text x="146" y="132" fill="currentColor" font-family="Arial, Helvetica, sans-serif" font-size="63" font-weight="700" letter-spacing="-3">Studio</text>
      <path d="M363 91h13v13h13v13h-13v13h-13v-13h-13v-13h13V91Z" fill="url(#ey-plus-gradient)"/>
    </svg>
    """


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def html_attrs(attrs: Dict[str, Any]) -> str:
    parts = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        if value is True:
            parts.append(html.escape(str(key), quote=True))
        else:
            parts.append(f'{html.escape(str(key), quote=True)}="{html.escape(str(value), quote=True)}"')
    return f" {' '.join(parts)}" if parts else ""


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def to_path(raw: str, default: Path) -> Path:
    if not clean_text(raw):
        return default
    path = Path(raw)
    return path if path.is_absolute() else ROOT_DIR / path


def href_from_repo(raw_path: str, output_dir: Path) -> str:
    value = clean_text(raw_path)
    if not value:
        return ""
    path = Path(value)
    absolute = path if path.is_absolute() else ROOT_DIR / path
    if not absolute.exists():
        return ""
    relative = os.path.relpath(absolute, output_dir)
    return quote(Path(relative).as_posix(), safe="/:#?&=%")


def severity_tone(value: Any) -> str:
    return clean_text(value).lower() or "medium"


def axis_label(axis_id: Any, fallback: Any = "") -> str:
    return AXIS_LABELS.get(clean_text(axis_id), clean_text(fallback) or "Priority issue")


def display_copy(value: Any) -> str:
    text = clean_text(value)
    replacements = {
        "GTM-oriented ": "",
        "GTM-oriented": "",
        "GTM ": "",
        " GTM": "",
        "GTM": "",
        "Trust & WCAG 2.2 Accessibility": "Trust & Accessibility",
        "Trust & WCAG 2.2": "Trust & Accessibility",
        "Visual Brand & UI Consistency": "Visual & UI Consistency",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def score_accent(score_ten: float) -> str:
    score = max(0.0, min(10.0, float(score_ten)))
    if score < 5.0:
        return "#cf513f"
    if score >= 7.5:
        return "#11886e"
    return "#caa23b"


def score_tone(score_ten: float) -> str:
    score = max(0.0, min(10.0, float(score_ten)))
    if score < 5.0:
        return "bad"
    if score >= 7.5:
        return "good"
    return "medium"


def severity_label(value: Any) -> str:
    tone = severity_tone(value)
    if tone == "high":
        return "Major Issue"
    if tone == "low":
        return "Minor Issue"
    return "Medium Issue"


def render_score_ring(score_ten: float, *, label: str, accent: str = "", size: int = 138, attrs: Optional[Dict[str, Any]] = None) -> str:
    normalized = max(0.0, min(10.0, float(score_ten)))
    accent = clean_text(accent) or score_accent(normalized)
    stroke = max(8.0, size * 0.075)
    radius = max(24.0, (size / 2.0) - (stroke / 2.0) - 6.0)
    circumference = 2 * math.pi * radius
    progress = circumference * (normalized / 10.0)
    offset = circumference - progress
    center = size / 2
    return f"""
    <div class="score-ring"{html_attrs({"data-score": f"{normalized:.1f}", **(attrs or {})})} style="--ring-size:{size}px; --ring-stroke:{accent}; --ring-width:{stroke:.1f}px;">
      <svg viewBox="0 0 {size} {size}" aria-hidden="true">
        <circle cx="{center}" cy="{center}" r="{radius}" class="ring-track"></circle>
        <circle cx="{center}" cy="{center}" r="{radius}" class="ring-progress" style="stroke-dasharray:{circumference:.2f};stroke-dashoffset:{offset:.2f};"></circle>
      </svg>
      <div class="score-ring-copy">
        <strong data-score-text>{normalized:.1f}</strong>
        {f'<span>{html.escape(label)}</span>' if clean_text(label) else ''}
      </div>
    </div>
    """


def render_evidence_frame(
    shot: str,
    alt: str,
    *,
    is_mobile_visual: bool = False,
    empty_text: str = "No evidence image available",
) -> str:
    if is_mobile_visual:
        return f"""
          <div class="phone-device">
            <span class="phone-side phone-side-left"></span>
            <span class="phone-side phone-side-right"></span>
            <div class="phone-top">
              <span class="phone-speaker"></span>
              <span class="phone-camera"></span>
            </div>
            <div class="phone-screen">
              {f'<img src="{shot}" alt="{html.escape(alt)}">' if shot else f'<div class="story-visual-empty">{html.escape(empty_text)}</div>'}
            </div>
          </div>
        """
    return f"""
          <div class="desktop-screen">
            <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
            <div class="desktop-screen-body">
              {f'<img src="{shot}" alt="{html.escape(alt)}">' if shot else f'<div class="story-visual-empty">{html.escape(empty_text)}</div>'}
            </div>
          </div>
    """


def render_priority_story(
    item: Dict[str, Any],
    index: int,
    output_dir: Path,
    is_screenshot_audit: bool = False,
    is_mobile_visual: bool = False,
) -> str:
    spotlight = clean_text(item.get("spotlightImage"))
    shot = spotlight or href_from_repo(item.get("screenshotPath", ""), output_dir)
    axis_score = round(float(item.get("axisScore", 0)) / 10, 1)
    tone = severity_tone(item.get("severity"))
    severity = severity_label(item.get("severity"))
    _ = is_screenshot_audit
    visual = render_evidence_frame(
        shot,
        clean_text(item.get("title")) or "Audit evidence",
        is_mobile_visual=is_mobile_visual,
        empty_text="No evidence crop available",
    )
    return f"""
    <article class="story-row tone-{tone}">
      <div class="story-index">0{index}</div>
      <div class="story-media">
        <div class="story-visual-frame {'mobile-visual-frame' if is_mobile_visual else ''}">{visual}</div>
      </div>
      <div class="story-score-pane">
        {render_score_ring(axis_score, label="", accent="#caa23b", size=128)}
      </div>
      <div class="story-copy">
        <span class="severity-badge severity-{tone}"><span class="severity-icon">!</span>{html.escape(severity)}</span>
        <h3>{html.escape(clean_text(item.get("title")))}</h3>
        <p><strong>Issue:</strong> {html.escape(clean_text(item.get("explanation")))}</p>
        <p><strong>Why it matters:</strong> {html.escape(clean_text(item.get("whyItMatters")))}</p>
        <p><strong>Direction:</strong> {html.escape(clean_text(item.get("recommendation")))}</p>
      </div>
    </article>
    """


def _issue_key(item: Dict[str, Any]) -> tuple[str, str, str]:
    return (
        clean_text(item.get("axisId") or item.get("axis_id") or item.get("axis")),
        clean_text(item.get("pageUrl") or item.get("page_url") or item.get("pageName") or item.get("page_name")),
        clean_text(item.get("title")),
    )


def _safe_dom_id(*parts: Any) -> str:
    raw = "-".join(clean_text(part) for part in parts if clean_text(part))
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-").lower()[:96] or "item"


def render_issue_card(item: Dict[str, Any], index: int, output_dir: Path) -> str:
    issue_id = f"issue-{index:02d}-{_safe_dom_id(item.get('axisId') or item.get('axis_id'), item.get('pageName') or item.get('page_name'), item.get('title'))}"
    axis_id = clean_text(item.get("axisId") or item.get("axis_id"))
    axis_name = axis_label(axis_id, item.get("axisName") or item.get("axis") or "Issue")
    page_name = clean_text(item.get("pageName") or item.get("page_name")) or "Audited page"
    tone = severity_tone(item.get("severity"))
    shot = clean_text(item.get("spotlightImage")) or href_from_repo(item.get("screenshotPath", ""), output_dir)
    return f"""
    <article class="issue-card tone-{tone}" data-issue-id="{html.escape(issue_id)}">
      <div class="issue-media">
        <span class="issue-number">{index:02d}</span>
        <div class="issue-evidence-frame">
          <div class="desktop-screen">
            <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
            <div class="desktop-screen-body">
              {f'<img class="issue-thumb" src="{shot}" alt="{html.escape(clean_text(item.get("title")) or "Issue evidence")}" data-editable-image="{html.escape(issue_id)}">' if shot else '<div class="story-visual-empty">No evidence image available</div>'}
            </div>
          </div>
        </div>
        <input class="hidden-file-input" type="file" accept="image/*" data-screenshot-input data-issue-id="{html.escape(issue_id)}" data-local-edit-control>
      </div>
      <div class="issue-copy">
        <div class="issue-card-top">
          <span class="issue-axis">{html.escape(axis_name)}</span>
          <span class="severity-dot severity-{tone}">{html.escape(severity_label(item.get("severity")))}</span>
        </div>
        <div class="issue-title-row">
          <h3 data-editable-field="title">{html.escape(display_copy(item.get("title")) or "Untitled issue")}</h3>
          <div class="issue-title-actions" data-local-edit-control>
            <button class="mini-edit-button" type="button" data-edit-action="copy" data-issue-id="{html.escape(issue_id)}" aria-pressed="false">Edit text</button>
            <button class="mini-edit-button" type="button" data-edit-action="image" data-issue-id="{html.escape(issue_id)}">Edit screenshot</button>
          </div>
        </div>
        <p data-editable-field="explanation">{html.escape(display_copy(item.get("explanation")) or display_copy(item.get("evidence")))}</p>
        {f'<p class="issue-why"><strong>Why it matters:</strong> <span data-editable-field="whyItMatters">{html.escape(display_copy(item.get("whyItMatters")))}</span></p>' if clean_text(item.get("whyItMatters")) else ''}
        {f'<p class="issue-fix"><strong>Recommended move:</strong> <span data-editable-field="recommendation">{html.escape(display_copy(item.get("recommendation")))}</span></p>' if clean_text(item.get("recommendation")) else ''}
        <div class="issue-meta">
          <span>{html.escape(page_name)}</span>
        </div>
      </div>
    </article>
    """


def render_local_publish_panel() -> str:
    return """
    <section class="local-publish-panel" data-local-publish-panel>
      <div>
        <p class="eyebrow">Finalize</p>
        <h2>Review, edit, then deploy the final audit</h2>
        <p>Adjust text, screenshots, and scores locally. When the report is ready, deploy this edited version to Vercel.</p>
      </div>
      <div class="local-publish-actions">
        <button class="publish-button" type="button" data-deploy-edited-report>Deploy final audit to Vercel</button>
        <span class="publish-status" data-deploy-status>Local edits are not published until you deploy.</span>
      </div>
    </section>
    """


def render_local_edit_script() -> str:
    return """
  <script>
  (() => {
    const localHosts = new Set(["127.0.0.1", "localhost", "::1"]);
    const panel = document.querySelector("[data-local-publish-panel]");
    const isAuditPath = window.location.pathname.startsWith("/audits/");
    const isKnownStaticDeployment = /(?:^|\\.)vercel\\.app$/i.test(window.location.hostname) || /(?:^|\\.)vercel\\.com$/i.test(window.location.hostname);
    const isLocalEditable = !isKnownStaticDeployment && (isAuditPath || localHosts.has(window.location.hostname) || window.location.protocol === "file:");
    const canDeployFromHere = isAuditPath && !isKnownStaticDeployment && /^https?:$/.test(window.location.protocol);
    if (panel && !isLocalEditable) panel.hidden = true;
    document.querySelectorAll("[data-local-edit-control]").forEach((control) => {
      if (!isLocalEditable) control.hidden = true;
    });
    document.querySelectorAll("[data-axis-id] [data-editable-field]").forEach((field) => {
      field.contentEditable = isLocalEditable ? "true" : "false";
    });

    function setEditing(issueId, enabled) {
      document.querySelectorAll(`[data-issue-id="${CSS.escape(issueId)}"] [data-editable-field]`).forEach((field) => {
        field.contentEditable = enabled ? "true" : "false";
      });
      document.querySelectorAll(`[data-edit-action="copy"][data-issue-id="${CSS.escape(issueId)}"]`).forEach((button) => {
        button.setAttribute("aria-pressed", enabled ? "true" : "false");
        button.textContent = enabled ? "Done editing" : "Edit text";
      });
    }

    document.addEventListener("click", (event) => {
      const action = event.target.closest("[data-edit-action]");
      if (!action) return;
      const issueId = action.dataset.issueId || "";
      if (!issueId) return;
      if (action.dataset.editAction === "copy") {
        setEditing(issueId, action.getAttribute("aria-pressed") !== "true");
      }
      if (action.dataset.editAction === "image") {
        const input = document.querySelector(`[data-screenshot-input][data-issue-id="${CSS.escape(issueId)}"]`);
        if (input) input.click();
      }
    });

    document.addEventListener("change", (event) => {
      const input = event.target.closest("[data-screenshot-input]");
      if (!input || !input.files || !input.files[0]) return;
      const issueId = input.dataset.issueId || "";
      const reader = new FileReader();
      reader.addEventListener("load", () => {
        document.querySelectorAll(`[data-editable-image="${CSS.escape(issueId)}"]`).forEach((image) => {
          image.src = String(reader.result || "");
        });
      });
      reader.readAsDataURL(input.files[0]);
    });

    document.addEventListener("input", (event) => {
      const scoreInput = event.target.closest("[data-axis-score-input]");
      if (!scoreInput) return;
      const tile = scoreInput.closest("[data-axis-id]");
      if (!tile) return;
      const score = Math.max(0, Math.min(10, Number.parseFloat(scoreInput.value || "0") || 0));
      const text = tile.querySelector("[data-axis-score-text]");
      if (text) text.textContent = score.toFixed(1);
      tile.style.setProperty("--score-color", score < 5 ? "#cf513f" : score >= 7.5 ? "#11886e" : "#caa23b");
      updateOverallScore();
    });

    function scoreAccent(score) {
      if (score < 5) return "#cf513f";
      if (score >= 7.5) return "#11886e";
      return "#caa23b";
    }

    function updateScoreRing(ring, score) {
      if (!ring) return;
      const normalized = Math.max(0, Math.min(10, Number(score) || 0));
      ring.dataset.score = normalized.toFixed(1);
      ring.style.setProperty("--ring-stroke", scoreAccent(normalized));
      const text = ring.querySelector("[data-score-text]");
      if (text) text.textContent = normalized.toFixed(1);
      const progress = ring.querySelector(".ring-progress");
      if (progress) {
        const dashArray = Number.parseFloat(progress.style.strokeDasharray || progress.getAttribute("stroke-dasharray") || "0");
        if (dashArray) progress.style.strokeDashoffset = String(dashArray * (1 - normalized / 10));
      }
    }

    function updateOverallScore() {
      const scores = Array.from(document.querySelectorAll("[data-axis-score-input]"))
        .map((input) => Math.max(0, Math.min(10, Number.parseFloat(input.value || "0") || 0)));
      if (!scores.length) return;
      const average = scores.reduce((sum, value) => sum + value, 0) / scores.length;
      document.querySelectorAll('[data-score-role="overall"]').forEach((ring) => updateScoreRing(ring, average));
    }

    updateOverallScore();

    function cleanCloneForDeployment() {
      const clone = document.documentElement.cloneNode(true);
      clone.querySelectorAll("[contenteditable]").forEach((node) => node.removeAttribute("contenteditable"));
      clone.querySelectorAll("[data-editable-field]").forEach((node) => node.removeAttribute("data-editable-field"));
      clone.querySelectorAll("[data-editable-image]").forEach((node) => node.removeAttribute("data-editable-image"));
      clone.querySelectorAll("[data-local-edit-control], [data-local-publish-panel]").forEach((node) => node.remove());
      clone.querySelectorAll("[data-deploy-status]").forEach((node) => {
        node.textContent = "Published version generated from reviewed local edits.";
      });
      return "<!doctype html>\\n" + clone.outerHTML;
    }

    async function readDeployPayload(response) {
      const text = await response.text();
      if (!text.trim()) return {};
      try {
        return JSON.parse(text);
      } catch (_error) {
        const preview = text.replace(/\\s+/g, " ").trim().slice(0, 180);
        throw new Error(`Deployment endpoint did not return JSON (${response.status}). Open the /audits report from the local Python server, then retry. Response started with: ${preview}`);
      }
    }

    async function postEditedReport(body) {
      const params = new URLSearchParams(window.location.search);
      const configuredApiBase = String(params.get("apiBaseUrl") || params.get("backend") || params.get("api") || "").replace(/\\/+$/, "");
      const requestHeaders = {"Content-Type": "application/json"};
      if (configuredApiBase.includes("ngrok")) requestHeaders["ngrok-skip-browser-warning"] = "true";
      const endpoints = [
        ...(configuredApiBase ? [`${configuredApiBase}/api/reports/deploy`] : []),
        new URL("/api/reports/deploy", window.location.href).href,
        new URL("api/reports/deploy", window.location.href).href,
        new URL("../../api/reports/deploy", window.location.href).href
      ];
      let lastPayload = null;
      let lastResponse = null;
      let lastError = null;
      for (const endpoint of Array.from(new Set(endpoints))) {
        try {
          const response = await fetch(endpoint, {
            method: "POST",
            headers: requestHeaders,
            body
          });
          const payload = await readDeployPayload(response);
          if (response.ok) return payload;
          lastPayload = payload;
          lastResponse = response;
          if (response.status !== 404) break;
        } catch (error) {
          lastError = error;
        }
      }
      if (lastPayload && lastPayload.error) throw new Error(lastPayload.error);
      if (lastError instanceof Error) throw lastError;
      throw new Error(lastResponse ? `Deployment endpoint failed with status ${lastResponse.status}.` : "Deployment endpoint could not be reached.");
    }

    const deployButton = document.querySelector("[data-deploy-edited-report]");
    const status = document.querySelector("[data-deploy-status]");
    if (deployButton) {
      if (!canDeployFromHere) {
        deployButton.disabled = true;
        if (status && isLocalEditable) status.textContent = "Open this report from the local UI server to deploy it.";
      }
      deployButton.addEventListener("click", async () => {
        if (!canDeployFromHere) return;
        deployButton.disabled = true;
        if (status) status.textContent = "Saving edited audit and deploying to Vercel...";
        try {
          const payload = await postEditedReport(JSON.stringify({
              path: window.location.pathname,
              html: cleanCloneForDeployment()
            }));
          if (status) {
            status.innerHTML = `Deployed: <a href="${payload.url}" target="_blank" rel="noreferrer">${payload.url}</a>`;
          }
        } catch (error) {
          if (status) status.textContent = error instanceof Error ? error.message : "Deployment failed.";
          deployButton.disabled = false;
        }
      });
    }
  })();
  </script>
    """


def render_issue_tabs(
    *,
    priorities: List[Dict[str, Any]],
    axes: List[Dict[str, Any]],
    output_dir: Path,
) -> str:
    all_issues: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in priorities:
        key = _issue_key(item)
        if key not in seen:
            all_issues.append(item)
            seen.add(key)
    for axis in axes:
        axis_id = clean_text(axis.get("id"))
        axis_name = axis_label(axis_id, axis.get("shortName") or axis.get("name"))
        for item in axis.get("painPoints") or []:
            item.setdefault("axisId", axis_id)
            item.setdefault("axisName", axis_name)
            key = _issue_key(item)
            if key not in seen:
                all_issues.append(item)
                seen.add(key)

    tabs: List[tuple[str, str, List[Dict[str, Any]]]] = [
        ("priorities", "Priorities", priorities),
        ("all", "All", all_issues),
    ]
    for axis in axes:
        axis_id = clean_text(axis.get("id"))
        axis_name = axis_label(axis_id, axis.get("shortName") or axis.get("name"))
        axis_issues = []
        for item in all_issues:
            if clean_text(item.get("axisId") or item.get("axis_id")) == axis_id:
                axis_issues.append(item)
        tabs.append((axis_id, axis_name, axis_issues))

    inputs = []
    labels = []
    panels = []
    for index, (tab_id, label, items) in enumerate(tabs):
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", tab_id).strip("-") or f"tab-{index}"
        input_id = f"issue-tab-{safe_id}"
        inputs.append(
            f'<input class="issue-tab-input" type="radio" name="issue-filter" id="{input_id}" {"checked" if index == 0 else ""}>'
        )
        labels.append(
            f'<label class="issue-tab-label" for="{input_id}">{html.escape(label)}<span>{len(items)}</span></label>'
        )
        cards = "".join(render_issue_card(item, card_index, output_dir) for card_index, item in enumerate(items, start=1))
        panel_body = cards or '<p class="empty">No issues for this filter.</p>'
        panels.append(
            f'<div class="issue-panel issue-panel-{safe_id}">{panel_body}</div>'
        )

    style_rules = []
    for tab_id, _label, _items in tabs:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", tab_id).strip("-") or "tab"
        input_id = f"issue-tab-{safe_id}"
        style_rules.append(
            f"#{input_id}:checked ~ .issue-tab-bar label[for='{input_id}'] {{ background: var(--ink); color: #fff; border-color: var(--ink); }}"
        )
        style_rules.append(
            f"#{input_id}:checked ~ .issue-panels .issue-panel-{safe_id} {{ display: grid; }}"
        )

    return f"""
    <div class="issue-tabs">
      <style>{''.join(style_rules)}</style>
      {''.join(inputs)}
      <div class="issue-tab-bar">{''.join(labels)}</div>
      <div class="issue-panels">{''.join(panels)}</div>
    </div>
    """


def render_axis_tile(axis: Dict[str, Any], index: int) -> str:
    axis_id = _safe_dom_id("axis", axis.get("id") or axis.get("shortName") or index)
    score = round(float(axis.get("score", 0)) / 10, 1)
    tone = severity_tone(axis.get("severity")).title()
    return f"""
    <article class="axis-tile tone-{severity_tone(axis.get("severity"))} score-{score_tone(score)}" style="--score-color:{score_accent(score)};" data-axis-id="{html.escape(axis_id)}">
      <span class="floating-step">{index}</span>
      <h4>{html.escape(axis_label(axis.get("id"), axis.get("shortName") or axis.get("name")))}</h4>
      <p data-editable-field="axis-description">{html.escape(display_copy(axis.get("description")) or display_copy(axis.get("businessImpact")))}</p>
      <div class="axis-tile-meta">
        <strong><span data-axis-score-text>{score:.1f}</span>/10</strong>
        <span>{tone} severity</span>
      </div>
      <label class="axis-score-editor" data-local-edit-control>
        <span>Score</span>
        <input type="number" min="0" max="10" step="0.1" value="{score:.1f}" data-axis-score-input>
      </label>
    </article>
    """


def render_independent_review(payload: Dict[str, Any]) -> str:
    review = payload.get("independentReview")
    if not isinstance(review, dict):
        return ""

    context = review.get("review_context") if isinstance(review.get("review_context"), dict) else {}
    missed = [item for item in (review.get("missed_issues") or []) if isinstance(item, dict)]
    questioned = [item for item in (review.get("agent_findings_to_question") or []) if isinstance(item, dict)]
    kept = [item for item in (review.get("agent_findings_to_keep") or []) if isinstance(item, dict)]
    insertions = [item for item in (review.get("final_report_insertions") or []) if isinstance(item, dict)]

    if not (missed or questioned or kept or insertions):
        return ""

    def issue_card(item: Dict[str, Any], index: int) -> str:
        severity = severity_tone(item.get("severity"))
        title = clean_text(item.get("title")) or "Independent review issue"
        category = clean_text(item.get("category")) or "UX/UI"
        page = clean_text(item.get("page"))
        evidence = clean_text(item.get("evidence"))
        why = clean_text(item.get("why_it_matters"))
        recommendation = clean_text(item.get("recommendation"))
        confidence = clean_text(item.get("confidence")) or "Medium"
        return f"""
        <article class="qa-card tone-{html.escape(severity)}">
          <div class="qa-card-top">
            <span class="qa-index">{index:02d}</span>
            <span class="severity-dot severity-{html.escape(severity)}">{html.escape(severity_label(severity))}</span>
          </div>
          <h3>{html.escape(title)}</h3>
          <p class="qa-meta">{html.escape(category)}{f' · {html.escape(page)}' if page else ''} · Confidence: {html.escape(confidence)}</p>
          {f'<p><strong>Evidence:</strong> {html.escape(evidence)}</p>' if evidence else ''}
          {f'<p><strong>Why it matters:</strong> {html.escape(why)}</p>' if why else ''}
          {f'<p><strong>Recommended move:</strong> {html.escape(recommendation)}</p>' if recommendation else ''}
        </article>
        """

    def question_card(item: Dict[str, Any], index: int) -> str:
        title = clean_text(item.get("title_or_reference")) or "Finding to review"
        problem = clean_text(item.get("problem")) or "Needs review"
        reason = clean_text(item.get("reason"))
        action = clean_text(item.get("suggested_action")) or "Verify"
        return f"""
        <article class="qa-note-card">
          <span class="qa-index">{index:02d}</span>
          <h4>{html.escape(title)}</h4>
          <p class="qa-meta">{html.escape(problem)} · Suggested action: {html.escape(action)}</p>
          {f'<p>{html.escape(reason)}</p>' if reason else ''}
        </article>
        """

    def kept_card(item: Dict[str, Any], index: int) -> str:
        title = clean_text(item.get("title")) or "Finding to keep"
        reason = clean_text(item.get("reason"))
        strength = clean_text(item.get("evidence_strength")) or "Medium"
        return f"""
        <article class="qa-note-card">
          <span class="qa-index">{index:02d}</span>
          <h4>{html.escape(title)}</h4>
          <p class="qa-meta">Evidence strength: {html.escape(strength)}</p>
          {f'<p>{html.escape(reason)}</p>' if reason else ''}
        </article>
        """

    missed_html = "".join(issue_card(item, index) for index, item in enumerate(missed, start=1))
    questioned_html = "".join(question_card(item, index) for index, item in enumerate(questioned, start=1))
    kept_html = "".join(kept_card(item, index) for index, item in enumerate(kept, start=1))
    insertion_html = "".join(
        f"""
        <li>
          <strong>{html.escape(clean_text(item.get("section")) or "Report note")}:</strong>
          {html.escape(clean_text(item.get("text")))}
        </li>
        """
        for item in insertions
        if clean_text(item.get("text"))
    )
    used_files = ", ".join(clean_text(item) for item in (context.get("used_files") or []) if clean_text(item))
    notes = clean_text(context.get("notes"))

    return f"""
    <section class="section-panel independent-review-section" id="independent-review">
      <div class="section-head">
        <div>
          <p class="eyebrow">Independent QA Review</p>
          <h2>External review additions and corrections</h2>
          <p class="qa-lede">This section separates the external ChatGPT QA pass from the automated audit findings. Use it to add high-confidence missed issues and to downgrade findings that need manual verification.</p>
          {f'<p class="qa-source"><strong>Inputs reviewed:</strong> {html.escape(used_files)}</p>' if used_files else ''}
          {f'<p class="qa-source">{html.escape(notes)}</p>' if notes else ''}
        </div>
      </div>
      {f'<h3 class="qa-subhead">Missed issues to add</h3><div class="qa-grid">{missed_html}</div>' if missed_html else ''}
      {f'<h3 class="qa-subhead">Automated findings to question</h3><div class="qa-note-grid">{questioned_html}</div>' if questioned_html else ''}
      {f'<h3 class="qa-subhead">Automated findings to keep</h3><div class="qa-note-grid">{kept_html}</div>' if kept_html else ''}
      {f'<h3 class="qa-subhead">Final report insertion notes</h3><ul class="qa-insertion-list">{insertion_html}</ul>' if insertion_html else ''}
    </section>
    """


def render_axis_section(
    axis: Dict[str, Any],
    index: int,
    output_dir: Path,
    is_screenshot_audit: bool = False,
    is_mobile_visual: bool = False,
) -> str:
    lead_item = ((axis.get("painPoints") or [])[:1] or (axis.get("strengths") or [])[:1] or [{}])[0]
    shot = clean_text(lead_item.get("spotlightImage")) or href_from_repo(lead_item.get("screenshotPath", ""), output_dir)
    axis_score = round(float(axis.get("score", 0)) / 10, 1)
    tone = severity_tone(axis.get("severity"))
    _ = is_screenshot_audit
    visual = render_evidence_frame(
        shot,
        f"{clean_text(axis.get('shortName')) or clean_text(axis.get('name')) or 'Axis'} visual evidence",
        is_mobile_visual=is_mobile_visual,
        empty_text="No evidence crop available",
    )
    return f"""
    <article class="axis-story tone-{tone}" id="axis-{index}">
      <div class="story-index">0{index}</div>
      <div class="axis-story-media">
        <div class="axis-story-frame {'mobile-visual-frame' if is_mobile_visual else ''}">{visual}</div>
      </div>
      <div class="axis-story-score">
        {render_score_ring(axis_score, label="", accent="#caa23b", size=154)}
      </div>
      <div class="axis-story-copy">
        <h3>{html.escape(clean_text(axis.get("shortName")) or clean_text(axis.get("name")))}</h3>
        <p><strong>Commercial impact:</strong> {html.escape(clean_text(axis.get("businessImpact")))}</p>
        {f'<p><strong>Lead issue:</strong> {html.escape(clean_text(lead_item.get("title")))}</p>' if clean_text(lead_item.get("title")) else ''}
        {f'<p><strong>Observed friction:</strong> {html.escape(clean_text(lead_item.get("explanation")))}</p>' if clean_text(lead_item.get("explanation")) else ''}
        {f'<p><strong>Recommended move:</strong> {html.escape(clean_text(lead_item.get("recommendation")))}</p>' if clean_text(lead_item.get("recommendation")) else ''}
      </div>
    </article>
    """


def render_scanned_page(item: Dict[str, Any], output_dir: Path, is_mobile_visual: bool = False) -> str:
    href = href_from_repo(item.get("screenshot_path", ""), output_dir)
    if not href:
        return ""
    page_name = clean_text(item.get("page_name")) or clean_text(item.get("pageName")) or "Page"
    if is_mobile_visual:
        return f"""
    <a class="scan-card mobile-scan-card" href="{href}" target="_blank" rel="noreferrer">
      <div class="scan-phone-shell">
        <span class="phone-side phone-side-left"></span>
        <span class="phone-side phone-side-right"></span>
        <div class="phone-top"><span class="phone-speaker"></span><span class="phone-camera"></span></div>
        <div class="scan-phone-screen">
          <img src="{href}" alt="{html.escape(page_name)} screenshot">
        </div>
      </div>
      <strong class="scan-caption">{html.escape(page_name)}</strong>
    </a>
    """
    return f"""
    <a class="scan-card" href="{href}" target="_blank" rel="noreferrer">
      <div class="scan-screen">
        <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
        <img src="{href}" alt="{html.escape(page_name)} screenshot">
      </div>
      <strong class="scan-caption">{html.escape(page_name)}</strong>
    </a>
    """


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _visual_region_from_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("visualRegion", "visual_region", "region", "boundingBox", "bounding_box"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
    bundle = item.get("evidenceBundle")
    if isinstance(bundle, dict):
        target = bundle.get("target")
        if isinstance(target, dict) and isinstance(target.get("rect"), dict):
            return target["rect"]
    return None


SPECIFIC_REGION_TERMS = {
    "button",
    "cta",
    "link",
    "text",
    "copy",
    "label",
    "heading",
    "title",
    "photo",
    "image",
    "picture",
    "icon",
    "logo",
    "form",
    "field",
    "input",
    "card",
    "menu",
    "nav",
    "header",
    "control",
    "component",
    "section",
    "area",
}

GENERAL_REGION_TERMS = {
    "full page",
    "whole page",
    "full screen",
    "whole screen",
    "full viewport",
    "whole viewport",
    "entire page",
    "entire screen",
    "viewport",
    "website",
    "overall",
    "general",
    "responsive",
    "layout failure",
    "performance",
    "web vitals",
}


def _region_to_pixels(region: Optional[Dict[str, Any]], image_width: int, image_height: int) -> Tuple[float, float, float, float]:
    if not region:
        return image_width * 0.18, image_height * 0.16, image_width * 0.64, image_height * 0.58

    x = _safe_number(region.get("x"), 0.18)
    y = _safe_number(region.get("y"), 0.16)
    width = _safe_number(region.get("width"), 0.64)
    height = _safe_number(region.get("height"), 0.58)
    normalized_hint = clean_text(region.get("coordinate_system")).lower()
    values_look_normalized = max(abs(x), abs(y), abs(width), abs(height)) <= 1.5

    if "normalized" in normalized_hint or values_look_normalized:
        x *= image_width
        width *= image_width
        y *= image_height
        height *= image_height

    width = max(24.0, min(width, image_width))
    height = max(24.0, min(height, image_height))
    x = max(0.0, min(x, image_width - width))
    y = max(0.0, min(y, image_height - height))
    return x, y, width, height


def _region_text(item: Dict[str, Any], region: Dict[str, Any]) -> str:
    parts = [
        region.get("description"),
        region.get("semanticType"),
        region.get("uxRole"),
        item.get("title"),
        item.get("sourceSheet"),
        item.get("axisName"),
        item.get("evidence"),
    ]
    return clean_text(" ".join(clean_text(part) for part in parts if clean_text(part))).lower()


def _is_precise_region(item: Dict[str, Any], region: Optional[Dict[str, Any]], image_width: int, image_height: int) -> bool:
    if not region:
        return False
    if item.get("responsiveFailure"):
        return False
    bundle = item.get("evidenceBundle")
    bundle_source = clean_text((bundle or {}).get("source") if isinstance(bundle, dict) else "").lower()
    if bundle_source in {"playwright_performance_snapshot", "playwright_performance_kpi"}:
        return False

    x, y, width, height = _region_to_pixels(region, image_width, image_height)
    if width <= 0 or height <= 0:
        return False

    area_ratio = (width * height) / max(float(image_width * image_height), 1.0)
    width_ratio = width / max(float(image_width), 1.0)
    height_ratio = height / max(float(image_height), 1.0)
    is_full_view = x <= image_width * 0.03 and y <= image_height * 0.03 and width_ratio >= 0.92 and height_ratio >= 0.86
    if is_full_view or area_ratio >= 0.68:
        return False

    text = _region_text(item, region)
    has_general_signal = any(term in text for term in GENERAL_REGION_TERMS)
    has_specific_signal = any(term in text for term in SPECIFIC_REGION_TERMS)
    if has_general_signal and not has_specific_signal:
        return False
    if area_ratio > 0.42 and not has_specific_signal:
        return False
    return True


def _clamp_highlight_bounds(bounds: Tuple[float, float, float, float], image_width: int, image_height: int, inset: int) -> Tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    return (
        max(inset, min(left, image_width - inset)),
        max(inset, min(top, image_height - inset)),
        max(inset, min(right, image_width - inset)),
        max(inset, min(bottom, image_height - inset)),
    )


def _draw_red_highlight(
    draw: Any,
    bounds: Tuple[float, float, float, float],
    image_width: int,
    image_height: int,
    *,
    broad: bool = False,
) -> None:
    stroke_width = max(8, int(max(image_width, image_height) * 0.006))
    soft_stroke_width = max(12, int(max(image_width, image_height) * 0.009))
    inset = max(stroke_width, soft_stroke_width)
    bounds = _clamp_highlight_bounds(bounds, image_width, image_height, inset)
    soft_bounds = _clamp_highlight_bounds(
        (bounds[0] - 10, bounds[1] - 10, bounds[2] + 10, bounds[3] + 10),
        image_width,
        image_height,
        inset,
    )
    if broad:
        radius = max(26, int(min(bounds[2] - bounds[0], bounds[3] - bounds[1]) * 0.035))
        draw.rounded_rectangle(soft_bounds, radius=radius + 8, outline=(255, 52, 52, 105), width=soft_stroke_width)
        draw.rounded_rectangle(bounds, radius=radius, outline=(255, 52, 52, 245), width=stroke_width)
        return

    draw.ellipse(soft_bounds, outline=(255, 52, 52, 105), width=soft_stroke_width)
    draw.ellipse(bounds, outline=(255, 52, 52, 245), width=stroke_width)


def _desktop_crop_box(
    source_width: int,
    source_height: int,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    has_region: bool,
) -> Tuple[float, float, float, float]:
    target_aspect = 16 / 9
    source_aspect = source_width / max(source_height, 1)
    if source_aspect >= target_aspect:
        crop_height = float(source_height)
        crop_width = min(float(source_width), crop_height * target_aspect)
        center_x = source_width / 2 if not has_region or width / max(source_width, 1) > 0.55 else x + width / 2
        crop_left = max(0.0, min(center_x - crop_width / 2, source_width - crop_width))
        return crop_left, 0.0, crop_left + crop_width, crop_height

    crop_width = float(source_width)
    crop_height = min(float(source_height), crop_width / target_aspect)
    if not has_region:
        crop_top = 0.0
    elif y <= crop_height * 0.25:
        crop_top = 0.0
    else:
        center_y = y + height / 2
        crop_top = max(0.0, min(center_y - crop_height * 0.45, source_height - crop_height))
    return 0.0, crop_top, crop_width, crop_top + crop_height


def build_screenshot_spotlight(item: Dict[str, Any], output_dir: Path, issue_index: int, *, is_mobile_visual: bool = False) -> str:
    screenshot_path = clean_text(item.get("screenshotPath"))
    if not screenshot_path:
        return ""

    source = Path(screenshot_path)
    absolute = source if source.is_absolute() else ROOT_DIR / source
    if not absolute.exists():
        return ""

    try:
        from PIL import Image, ImageDraw
    except Exception:
        return ""

    try:
        with Image.open(absolute) as source_image:
            image = source_image.convert("RGBA")
    except Exception:
        return ""

    source_width, source_height = image.width, image.height
    visual_region = _visual_region_from_item(item)
    has_precise_region = _is_precise_region(item, visual_region, source_width, source_height)
    x, y, width, height = _region_to_pixels(visual_region, source_width, source_height)
    if is_mobile_visual:
        if has_precise_region:
            draw = ImageDraw.Draw(image, "RGBA")
            halo = max(18, int(max(width, height) * 0.08))
            stroke_width = max(6, int(max(source_width, source_height) * 0.004))
            soft_stroke_width = max(8, int(max(source_width, source_height) * 0.006))
            inset = max(stroke_width, soft_stroke_width)
            bounds = (
                max(inset, x - halo),
                max(inset, y - halo),
                min(source_width - inset, x + width + halo),
                min(source_height - inset, y + height + halo),
            )
            soft_bounds = (
                max(inset, bounds[0] - 8),
                max(inset, bounds[1] - 8),
                min(source_width - inset, bounds[2] + 8),
                min(source_height - inset, bounds[3] + 8),
            )
            draw.rounded_rectangle(
                bounds,
                radius=max(18, int(min(width, height) * 0.12)),
                outline=(255, 52, 52, 245),
                width=stroke_width,
            )
            draw.rounded_rectangle(
                soft_bounds,
                radius=max(22, int(min(width, height) * 0.14)),
                outline=(255, 52, 52, 90),
                width=soft_stroke_width,
            )
        evidence_dir = output_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        output_path = evidence_dir / f"screenshot-issue-{issue_index:02d}.png"
        image.convert("RGB").save(output_path, format="PNG", optimize=True)
        return quote(os.path.relpath(output_path, output_dir).replace(os.sep, "/"), safe="/:#?&=%")

    target_width = 1920
    target_height = 1080
    if source_width / max(float(source_height), 1.0) < 0.8:
        canvas = Image.new("RGBA", (target_width, target_height), (250, 246, 238, 255))
        scale = min((target_width - 120) / max(float(source_width), 1.0), (target_height - 80) / max(float(source_height), 1.0))
        scaled_width = max(1, int(round(source_width * scale)))
        scaled_height = max(1, int(round(source_height * scale)))
        offset_x = int(round((target_width - scaled_width) / 2))
        offset_y = int(round((target_height - scaled_height) / 2))
        scaled = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
        canvas.paste(scaled, (offset_x, offset_y))
        if has_precise_region:
            draw = ImageDraw.Draw(canvas, "RGBA")
            scaled_bounds = (
                offset_x + x * scale,
                offset_y + y * scale,
                offset_x + (x + width) * scale,
                offset_y + (y + height) * scale,
            )
            halo = max(18, int(max(scaled_bounds[2] - scaled_bounds[0], scaled_bounds[3] - scaled_bounds[1]) * 0.08))
            _draw_red_highlight(
                draw,
                (scaled_bounds[0] - halo, scaled_bounds[1] - halo, scaled_bounds[2] + halo, scaled_bounds[3] + halo),
                target_width,
                target_height,
                broad=True,
            )
        evidence_dir = output_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        output_path = evidence_dir / f"screenshot-issue-{issue_index:02d}.png"
        canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
        return quote(os.path.relpath(output_path, output_dir).replace(os.sep, "/"), safe="/:#?&=%")

    crop_left, crop_top, crop_right, crop_bottom = _desktop_crop_box(
        source_width,
        source_height,
        x,
        y,
        width,
        height,
        has_region=has_precise_region,
    )
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top

    image = image.crop(
        (
            int(round(crop_left)),
            int(round(crop_top)),
            int(round(crop_right)),
            int(round(crop_bottom)),
        )
    ).resize((target_width, target_height), Image.Resampling.LANCZOS)

    scale_x = target_width / max(crop_width, 1.0)
    scale_y = target_height / max(crop_height, 1.0)
    x = (x - crop_left) * scale_x
    y = (y - crop_top) * scale_y
    width *= scale_x
    height *= scale_y

    if has_precise_region:
        draw = ImageDraw.Draw(image, "RGBA")
        halo = max(20, int(max(width, height) * 0.12))
        highlight_bounds = (x - halo, y - halo, x + width + halo, y + height + halo)
        broad_region = (
            (width * height) / max(float(target_width * target_height), 1.0) > 0.34
            or width / max(float(target_width), 1.0) > 0.72
            or height / max(float(target_height), 1.0) > 0.62
        )
        _draw_red_highlight(draw, highlight_bounds, target_width, target_height, broad=broad_region)

    evidence_dir = output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output_path = evidence_dir / f"screenshot-issue-{issue_index:02d}.png"
    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return quote(os.path.relpath(output_path, output_dir).replace(os.sep, "/"), safe="/:#?&=%")


def render_radar_chart(axes: list[Dict[str, Any]]) -> str:
    if not axes:
        return "<p class='empty'>No scoring data available.</p>"

    labels = [axis_label(axis.get("id"), axis.get("shortName") or axis.get("name") or "Axis") for axis in axes]
    values = [max(0.0, min(10.0, float(axis.get("score", 0)) / 10.0)) for axis in axes]
    count = len(labels)
    cx = 310
    cy = 250
    radius = 132
    levels = 5

    def polar_point(index: int, scale: float) -> tuple[float, float]:
        angle = (-math.pi / 2) + (2 * math.pi * index / count)
        return (
            cx + math.cos(angle) * radius * scale,
            cy + math.sin(angle) * radius * scale,
        )

    grid_polygons = []
    for level in range(1, levels + 1):
        scale = level / levels
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (polar_point(index, scale) for index in range(count)))
        value = int(round(scale * 10))
        grid_polygons.append(
            f'<polygon points="{points}" fill="none" stroke="rgba(31,39,51,0.08)" stroke-width="1"></polygon>'
            f'<text x="{cx + 6}" y="{cy - radius * scale + 4:.1f}" fill="rgba(93,103,117,0.9)" font-size="12">{value}</text>'
        )

    axis_lines = []
    label_nodes = []
    for index, label in enumerate(labels):
        x, y = polar_point(index, 1.0)
        axis_lines.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="rgba(31,39,51,0.10)" stroke-width="1"></line>')
        label_x, label_y = polar_point(index, 1.45)
        anchor = "middle"
        if label_x < cx - 40:
            anchor = "end"
        elif label_x > cx + 40:
            anchor = "start"
        words = label.split()
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > 16 and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        tspans = "".join(
            f'<tspan x="{label_x:.1f}" dy="{0 if line_index == 0 else 15}">{html.escape(line)}</tspan>'
            for line_index, line in enumerate(lines[:3])
        )
        label_nodes.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor}" fill="#4d5868" font-size="12.5" font-weight="650">{tspans}</text>'
        )

    data_points = [polar_point(index, value / 10.0) for index, value in enumerate(values)]
    data_polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_points)
    point_nodes = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#caa23b" stroke="#ffffff" stroke-width="2"></circle>'
        for x, y in data_points
    )

    return f"""
    <div class="radar-card">
      <svg class="radar-chart" viewBox="0 0 620 520" role="img" aria-label="Audit scoring radar chart">
        <defs>
          <linearGradient id="radarFill" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="rgba(202,162,59,0.24)"></stop>
            <stop offset="100%" stop-color="rgba(202,162,59,0.10)"></stop>
          </linearGradient>
        </defs>
        {''.join(grid_polygons)}
        {''.join(axis_lines)}
        <polygon points="{data_polygon}" fill="url(#radarFill)" stroke="#caa23b" stroke-width="3"></polygon>
        {point_nodes}
        {''.join(label_nodes)}
      </svg>
    </div>
    """


def render_html(payload: Dict[str, Any], output_dir: Path) -> str:
    summary = payload.get("executiveSummary") or {}
    site = payload.get("site") or {}
    context = payload.get("context") or {}
    methodology = payload.get("methodology") or []
    recommendations = payload.get("recommendations") or []
    is_screenshot_audit = clean_text(payload.get("mode")).lower() == "screenshot"
    is_live_mobile_audit = clean_text(payload.get("generator")) == "src.mobile_audit.generate_mobile_audit"
    is_mobile_visual = is_live_mobile_audit or clean_text(payload.get("surfaceType")).lower() in {"mobile", "mobile_app", "mobile-app"}

    scanned_pages_data: List[Dict[str, Any]] = []
    seen_scanned_pages: set[str] = set()
    if is_mobile_visual:
        scanned_pages_data = [
            item
            for item in payload.get("scannedPages") or []
            if href_from_repo(item.get("screenshot_path", ""), output_dir)
        ]
        seen_scanned_pages = {
            clean_text(item.get("screenshot_path")) or clean_text(item.get("screen_id")) or str(index)
            for index, item in enumerate(scanned_pages_data)
        }
    else:
        for item in payload.get("scannedPages") or []:
            href = href_from_repo(item.get("screenshot_path", ""), output_dir)
            if not href:
                continue
            page_key = clean_text(item.get("page_url")) or clean_text(item.get("page_name")) or href
            if page_key in seen_scanned_pages:
                continue
            scanned_pages_data.append(item)
            seen_scanned_pages.add(page_key)

        for item in payload.get("focusScreenshots") or []:
            href = href_from_repo(item.get("screenshot_path", ""), output_dir)
            if not href:
                continue
            page_key = clean_text(item.get("page_url")) or clean_text(item.get("page_name")) or href
            if page_key not in seen_scanned_pages:
                scanned_pages_data.append(item)
                seen_scanned_pages.add(page_key)

    priorities_data = list((summary.get("topPriorities") or [])[:5])
    artifacts = payload.get("artifacts") or {}
    cleaned_path = to_path(clean_text(artifacts.get("cleanedPath")), ROOT_DIR / "shared" / "generated" / "html_cleaned.json")
    rendered_path = to_path(clean_text(artifacts.get("renderedPath")), ROOT_DIR / "shared" / "generated" / "rendered_ui_extraction.json")
    for index, item in enumerate(priorities_data, start=1):
        item["spotlightImage"] = build_screenshot_spotlight(item, output_dir, index, is_mobile_visual=is_mobile_visual) if is_screenshot_audit else build_gtm_spotlight(
            item=item,
            output_dir=output_dir,
            cleaned_path=cleaned_path,
            rendered_path=rendered_path,
            issue_index=index,
        )
        page_key = (
            clean_text(item.get("screenshotPath"))
            if is_mobile_visual
            else clean_text(item.get("pageUrl")) or clean_text(item.get("pageName")) or clean_text(item.get("screenshotPath"))
        )
        if page_key and page_key not in seen_scanned_pages and clean_text(item.get("screenshotPath")):
            scanned_pages_data.append(
                {
                    "page_name": clean_text(item.get("pageName")) or "Page",
                    "page_url": clean_text(item.get("pageUrl")),
                    "title": clean_text(item.get("title")) or clean_text(item.get("axisName")) or "Scanned page",
                    "screenshot_path": clean_text(item.get("screenshotPath")),
                }
            )
            seen_scanned_pages.add(page_key)
    priorities = "".join(
        render_priority_story(
            item,
            index,
            output_dir,
            is_screenshot_audit=is_screenshot_audit,
            is_mobile_visual=is_mobile_visual,
        )
        for index, item in enumerate(priorities_data, start=1)
    )
    axes_data = payload.get("axes") or []
    for index, axis in enumerate(axes_data, start=1):
        lead_item = ((axis.get("painPoints") or [])[:1] or (axis.get("strengths") or [])[:1] or [{}])[0]
        lead_item["spotlightImage"] = build_screenshot_spotlight(lead_item, output_dir, 100 + index, is_mobile_visual=is_mobile_visual) if is_screenshot_audit else build_gtm_spotlight(
            item=lead_item,
            output_dir=output_dir,
            cleaned_path=cleaned_path,
            rendered_path=rendered_path,
            issue_index=100 + index,
        )
    axes_tiles_html = "".join(render_axis_tile(axis, index) for index, axis in enumerate(axes_data, start=1))
    issue_tabs_html = render_issue_tabs(priorities=priorities_data, axes=axes_data, output_dir=output_dir)
    radar_html = render_radar_chart(axes_data)
    methodology_html = "".join(
        f"""
        <div class="method-card">
          <span class="floating-step">{index + 1}</span>
          <h4>{html.escape("Evidence Review" if display_copy(item.get("step")).lower() == "context" else display_copy(item.get("step")))}</h4>
          <p>{html.escape(display_copy(item.get("description")))}</p>
        </div>
        """
        for index, item in enumerate(methodology)
    )
    reco_items = recommendations[:5]
    reco_html = "".join(
        f"""
        <details class="reco-card priority-{html.escape(clean_text(item.get('priority')).lower().replace(' ', '-') or 'normal')}" {"open" if index == 0 else ""}>
          <summary>
            <span class="reco-orb">{index + 1:02d}</span>
            <span class="reco-summary-copy">
              <span class="reco-badge">{html.escape(clean_text(item.get("priority")) or "Action")}</span>
              <strong>{html.escape(display_copy(item.get("title")))}</strong>
            </span>
            <span class="reco-toggle" aria-hidden="true">+</span>
          </summary>
          <div class="reco-body">
            <p>{html.escape(display_copy(item.get("description")))}</p>
            {f'<p class="reco-impact">{html.escape(display_copy(item.get("impact")))}</p>' if clean_text(item.get("impact")) else ''}
            {f'<span class="reco-axis">{html.escape(display_copy(item.get("axis")))}</span>' if clean_text(item.get("axis")) else ''}
          </div>
        </details>
        """
        for index, item in enumerate(reco_items)
    )
    independent_review_html = render_independent_review(payload)
    strongest_axis = summary.get("strongestAxis") or {}
    weakest_axis = summary.get("weakestAxis") or {}
    strongest = axis_label(strongest_axis.get("id"), strongest_axis.get("shortName") or strongest_axis.get("name")) if strongest_axis else ""
    weakest = axis_label(weakest_axis.get("id"), weakest_axis.get("shortName") or weakest_axis.get("name")) if weakest_axis else ""
    overall_ten = round(float(summary.get("overallScore", 0)) / 10, 1)
    hero_score = render_score_ring(overall_ten, label="Overall", size=170, attrs={"data-score-role": "overall"})
    client_lockup = clean_text(site.get("display_name")) or clean_text(site.get("domain")) or "Client"
    scanned_pages_html = "".join(render_scanned_page(item, output_dir, is_mobile_visual=is_mobile_visual) for item in scanned_pages_data)
    scanned_pages_clone_html = scanned_pages_html.replace('<a class="scan-card', '<a tabindex="-1" class="scan-card')
    scan_duration_seconds = max(42, min(180, len(scanned_pages_data) * (2.6 if is_mobile_visual else 4.0)))
    scanned_pages_loop = (
        f'<div class="scan-static">{scanned_pages_html}</div>'
        if is_screenshot_audit and not is_mobile_visual
        else f"""
            <div class="scan-marquee">
              <div class="scan-strip" style="--scan-duration: {scan_duration_seconds:.0f}s;">
                <div class="scan-track">{scanned_pages_html}</div>
                <div class="scan-track" aria-hidden="true">{scanned_pages_clone_html}</div>
              </div>
            </div>
        """
        if scanned_pages_html
        else "<p class='empty'>No scanned-page screenshots were available for this run.</p>"
    )
    company_name = clean_text(site.get("display_name")) or "Client site"
    pages_count = clean_text(context.get("pagesAudited")) or str(len(scanned_pages_data) or "selected")
    generated_month = date.today().strftime("%B %Y")
    audit_subject = "captured mobile app screens" if is_live_mobile_audit else "uploaded screenshots" if is_screenshot_audit else f"{company_name} website"
    tested_url = clean_text(site.get("homepage") or site.get("url"))
    scan_eyebrow = "Screens Captured" if is_live_mobile_audit else "Screenshots Analyzed" if is_screenshot_audit else "Pages Scanned"
    scan_heading = "Representative mobile app screens reviewed during the audit" if is_live_mobile_audit else "Representative screenshots reviewed during the audit" if is_screenshot_audit else "Representative pages captured during the audit"
    nav_scope_label = "Input scope" if is_screenshot_audit else "Navigation scope"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(clean_text(site.get("display_name")) or "UX/UI Audit")}</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --paper: rgba(255,255,255,0.84);
      --card: rgba(255,255,255,0.92);
      --ink: #202733;
      --muted: #687386;
      --line: rgba(32,39,51,0.10);
      --gold: #c6a137;
      --gold-soft: rgba(198,161,55,0.14);
      --teal: #11886e;
      --red: #cf513f;
      --shadow: 0 20px 48px rgba(32,39,51,0.08);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(198,161,55,0.15), transparent 22rem),
        radial-gradient(circle at top right, rgba(202,162,59,0.10), transparent 24rem),
        linear-gradient(180deg, #fbf7f0 0%, #f6f1e8 100%);
      font-family: Aptos, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      line-height: 1.55;
    }}
    a {{ color: inherit; }}
    .shell {{ max-width: 1240px; margin: 0 auto; padding: 24px 20px 84px; }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 24px;
      width: 100vw;
      margin: -24px 0 24px calc(50% - 50vw);
      padding: 14px max(24px, calc((100vw - 1240px) / 2 + 20px));
      border-bottom: 1px solid rgba(32,39,51,0.08);
      background: rgba(255,255,255,0.95);
      backdrop-filter: blur(16px);
      box-shadow: 0 8px 22px rgba(32,39,51,0.04);
    }}
    .brand-lockups {{
      display: flex;
      align-items: center;
      gap: 20px;
      min-width: 0;
    }}
    .brand-primary,
    .brand-secondary {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-height: 48px;
    }}
    .brand-primary {{
      position: relative;
      flex: 0 0 auto;
    }}
    .ey-studio-logo {{
      display: block;
      width: clamp(118px, 11vw, 154px);
      height: auto;
      color: var(--ink);
    }}
    .brand-divider {{
      width: 1px;
      height: 40px;
      background: rgba(32,39,51,0.10);
    }}
    .brand-secondary strong {{
      display: block;
      font-size: 0.95rem;
      line-height: 1.1;
      max-width: 20ch;
    }}
    .brand-secondary span {{
      display: block;
      font-size: 0.75rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .topnav {{
      display: flex;
      gap: 28px;
      flex-wrap: wrap;
      justify-content: flex-end;
      font-size: 0.94rem;
      color: #4f5d6f;
      font-weight: 600;
    }}
    .topnav a {{
      text-decoration: none;
      padding-bottom: 2px;
      border-bottom: 1px solid transparent;
    }}
    .topnav a:hover {{ border-color: var(--gold); color: var(--ink); }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(260px, 0.36fr);
      gap: 42px;
      align-items: center;
      min-height: 360px;
      padding: 56px 0 46px;
    }}
    .hero-copy {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .eyebrow {{
      margin: 0;
      color: #6c7583;
      font-size: 0.82rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font-weight: 700;
    }}
    h1, h2, h3, h4 {{
      margin: 0;
      font-family: Aptos, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      line-height: 1.06;
      letter-spacing: -0.035em;
    }}
    h1 {{ font-size: clamp(2.1rem, 4vw, 3.2rem); }}
    h2 {{ font-size: clamp(1.35rem, 2.2vw, 1.9rem); margin-bottom: 10px; }}
    h3 {{ font-size: clamp(1.08rem, 1.6vw, 1.34rem); }}
    p {{ margin: 0; color: var(--muted); }}
    .hero-lead {{
      max-width: 40ch;
      font-size: clamp(1.35rem, 2.5vw, 2.2rem);
      line-height: 1.3;
      color: #66707b;
    }}
    .hero-stats {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 28px;
      margin-top: 10px;
    }}
    .hero-meta span {{
      display: block;
      color: #a3a9b3;
      font-size: 0.78rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .hero-meta strong {{
      display: block;
      margin-top: 4px;
      color: var(--ink);
      font-size: 1rem;
    }}
    .hero-visit-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: max-content;
      max-width: 100%;
      min-height: 44px;
      margin-top: 4px;
      padding: 12px 18px;
      border-radius: 4px;
      border: 1px solid rgba(32,39,51,0.16);
      background: var(--ink);
      color: #fff;
      text-decoration: none;
      font-weight: 750;
      box-shadow: 0 14px 28px rgba(32,39,51,0.12);
    }}
    .hero-visit-button:hover,
    .hero-visit-button:focus-visible {{
      background: #000;
      outline: 2px solid rgba(198,161,55,0.36);
      outline-offset: 3px;
    }}
    .hero-subcopy {{
      max-width: 48ch;
      color: var(--muted);
    }}
    .stat-card,
    .context-card,
    .method-card,
    .reco-card,
    .axis-tile,
    .score-note {{
      border: none;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .stat-card,
    .context-card,
    .method-card,
    .score-note {{
      padding: 18px;
    }}
    .stat-card span,
    .context-card span,
    .score-note span {{
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .stat-card strong,
    .context-card strong,
    .score-note strong {{
      display: block;
      font-size: 1.6rem;
      color: var(--ink);
      margin-bottom: 6px;
    }}
    .hero-side {{
      display: grid;
      gap: 14px;
      align-content: start;
    }}
    .hero-score-card {{
      display: grid;
      place-items: center;
      gap: 12px;
      padding: 10px 0 0;
      border: none;
      border-radius: 0;
      background: transparent;
    }}
    .hero-score-card p {{
      text-align: center;
      max-width: 28ch;
      font-size: 0.93rem;
    }}
    .score-ring {{
      position: relative;
      display: inline-grid;
      place-items: center;
      width: var(--ring-size);
      min-width: var(--ring-size);
      margin: 0 auto;
    }}
    .score-ring svg {{
      width: var(--ring-size);
      height: var(--ring-size);
      transform: rotate(-90deg);
    }}
    .ring-track {{
      fill: none;
      stroke: rgba(32,39,51,0.08);
      stroke-width: var(--ring-width);
    }}
    .ring-progress {{
      fill: none;
      stroke-width: var(--ring-width);
      stroke-linecap: round;
      stroke: var(--ring-stroke);
    }}
    .score-ring-copy {{
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 6px;
      text-align: center;
      pointer-events: none;
    }}
    .score-ring-copy strong {{
      display: block;
      font-size: clamp(1.7rem, 1.2rem + 1vw, 2.2rem);
      line-height: 0.95;
      color: var(--ink);
    }}
    .score-ring-copy span {{
      display: block;
      font-size: 0.82rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .scan-marquee {{
      position: relative;
      overflow: hidden;
      margin: 6px calc(50% - 50vw) 18px;
      padding: 8px 0 26px;
      mask-image: linear-gradient(90deg, transparent, #000 7%, #000 93%, transparent);
    }}
    .scan-strip {{
      display: flex;
      width: max-content;
      gap: 0;
      animation: scan-marquee var(--scan-duration, 38s) linear infinite;
      will-change: transform;
    }}
    .scan-marquee:hover .scan-strip,
    .scan-marquee:focus-within .scan-strip {{
      animation-play-state: paused;
    }}
    .scan-track {{
      display: flex;
      gap: 18px;
      padding-right: 18px;
    }}
    .scan-static {{
      display: flex;
      gap: 18px;
      overflow-x: auto;
      padding: 8px 0 22px;
      scrollbar-color: rgba(32,39,51,0.22) transparent;
    }}
    .scan-card {{
      display: block;
      flex: 0 0 clamp(280px, 25vw, 360px);
      text-decoration: none;
      color: inherit;
      transform: translateY(0) scale(1);
      transition: transform 220ms ease, filter 220ms ease;
    }}
    .scan-card:hover,
    .scan-card:focus-visible {{
      transform: translateY(-6px) scale(1.015);
      filter: saturate(1.04);
    }}
    .scan-screen {{
      overflow: hidden;
      border-radius: 18px;
      border: 1px solid rgba(32,39,51,0.10);
      background: #ffffff;
      box-shadow: 0 16px 28px rgba(32,39,51,0.10);
      margin-bottom: 10px;
    }}
    .scan-card img {{
      display: block;
      width: 100%;
      aspect-ratio: 16 / 9;
      object-fit: cover;
      object-position: center center;
    }}
    .mobile-scan-card {{
      flex-basis: clamp(210px, 18vw, 280px);
      padding: 6px 4px 14px;
    }}
    .scan-phone-shell,
    .phone-device {{
      position: relative;
      isolation: isolate;
      width: min(100%, 330px);
      aspect-ratio: 9 / 19.4;
      margin: 0 auto;
      padding: clamp(8px, 2.4%, 12px);
      border-radius: 44px;
      background:
        linear-gradient(145deg, #111318, #3a3d42 45%, #111318);
      box-shadow:
        inset 0 0 0 2px rgba(255,255,255,0.12),
        inset 0 0 0 5px rgba(0,0,0,0.76),
        0 24px 54px rgba(32,39,51,0.22);
    }}
    .scan-phone-shell::before,
    .phone-device::before {{
      content: "";
      position: absolute;
      inset: 5px;
      border-radius: 39px;
      border: 1px solid rgba(255,255,255,0.2);
      pointer-events: none;
      z-index: 2;
    }}
    .phone-top {{
      position: absolute;
      top: 16px;
      left: 50%;
      z-index: 4;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 32%;
      min-width: 84px;
      height: 25px;
      border-radius: 999px;
      background: #050608;
      transform: translateX(-50%);
      box-shadow: inset 0 1px 2px rgba(255,255,255,0.12);
    }}
    .phone-speaker {{
      width: 38px;
      height: 5px;
      border-radius: 999px;
      background: #151923;
    }}
    .phone-camera {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #111b2d;
      box-shadow: inset 0 0 0 2px #0a0d13;
    }}
    .phone-side {{
      position: absolute;
      z-index: 0;
      width: 4px;
      border-radius: 999px;
      background: linear-gradient(180deg, #2d3035, #0d0e10);
      opacity: 0.82;
    }}
    .phone-side-left {{
      left: -2px;
      top: 18%;
      height: 12%;
    }}
    .phone-side-right {{
      right: -2px;
      top: 29%;
      height: 16%;
    }}
    .scan-phone-screen,
    .phone-screen {{
      position: relative;
      z-index: 1;
      width: 100%;
      height: 100%;
      overflow: hidden;
      border-radius: 34px;
      background: #ffffff;
    }}
    .scan-phone-screen img,
    .phone-screen img {{
      display: block;
      width: 100%;
      height: 100%;
      aspect-ratio: auto;
      object-fit: contain;
      object-position: center center;
      background: #ffffff;
    }}
    .scan-card span,
    .scan-card strong,
    .scan-card em {{
      display: block;
    }}
    .scan-card span {{
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .scan-card strong {{
      font-size: 0.98rem;
      line-height: 1.35;
      color: var(--ink);
      margin-top: 4px;
    }}
    .scan-card em {{
      margin-top: 6px;
      font-style: normal;
      color: var(--muted);
      font-size: 0.82rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .scan-caption {{
      display: block;
      padding: 0 4px;
      color: var(--ink);
      font-size: 0.92rem;
      line-height: 1.25;
      text-align: center;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    @keyframes scan-marquee {{
      from {{ transform: translateX(0); }}
      to {{ transform: translateX(-50%); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .scan-strip {{
        animation: none;
      }}
      .scan-marquee {{
        overflow-x: auto;
        mask-image: none;
      }}
      .reco-card,
      .scan-card {{
        transition: none;
      }}
      .reco-card {{
        transform: none;
      }}
    }}
    .section-panel,
    .priority-panel {{
      margin-top: 28px;
      padding: 6px 0;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 18px;
      margin-bottom: 16px;
      padding-top: 12px;
      border-top: 1px solid rgba(32,39,51,0.08);
    }}
    .section-head p {{
      max-width: 46ch;
      font-size: 0.96rem;
    }}
    .independent-review-section {{
      margin-top: 44px;
    }}
    .qa-lede,
    .qa-source {{
      margin-top: 8px;
      max-width: 76ch;
    }}
    .qa-subhead {{
      margin: 28px 0 14px;
      font-size: 1.15rem;
      letter-spacing: 0;
    }}
    .qa-grid,
    .qa-note-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }}
    .qa-card,
    .qa-note-card {{
      display: grid;
      gap: 10px;
      min-width: 0;
      padding: 18px;
      border: 1px solid rgba(32,39,51,0.10);
      border-radius: 2px;
      background: rgba(255,255,255,0.86);
      box-shadow: 0 16px 32px rgba(32,39,51,0.05);
    }}
    .qa-card {{
      border-left: 4px solid var(--gold);
    }}
    .qa-card.tone-high {{
      border-left-color: var(--red);
    }}
    .qa-card.tone-medium {{
      border-left-color: #caa23b;
    }}
    .qa-card.tone-low {{
      border-left-color: var(--teal);
    }}
    .qa-card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    .qa-index {{
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 800;
      letter-spacing: 0.12em;
    }}
    .qa-card h3,
    .qa-note-card h4 {{
      font-size: 1rem;
      line-height: 1.28;
      letter-spacing: 0;
    }}
    .qa-card p,
    .qa-note-card p {{
      font-size: 0.9rem;
      line-height: 1.55;
    }}
    .qa-meta {{
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 650;
    }}
    .qa-insertion-list {{
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .qa-insertion-list li {{
      padding: 14px 16px;
      border: 1px solid rgba(32,39,51,0.09);
      background: rgba(255,255,255,0.72);
      color: var(--muted);
    }}
    .qa-insertion-list strong {{
      color: var(--ink);
    }}
    .context-grid,
    .method-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .recommendations-section {{
      margin-top: clamp(44px, 6vw, 84px);
    }}
    .reco-stage {{
      display: grid;
      grid-template-columns: minmax(240px, 0.72fr) minmax(0, 1.28fr);
      gap: clamp(28px, 6vw, 72px);
      align-items: start;
    }}
    .reco-copy {{
      position: sticky;
      top: 92px;
      display: grid;
      align-content: center;
      min-height: calc(100svh - 128px);
      margin-bottom: 0;
      padding-top: 18px;
      border-top: 1px solid rgba(32,39,51,0.08);
    }}
    .reco-copy h2 {{
      max-width: 11ch;
      font-size: clamp(2.1rem, 5vw, 4.8rem);
      line-height: 0.96;
    }}
    .reco-lede {{
      max-width: 34ch;
      margin-top: 12px;
      font-size: 1rem;
      line-height: 1.7;
    }}
    .reco-stack {{
      display: grid;
      gap: 16px;
      padding: 18px 0 0;
    }}
    .methodology-section {{
      margin-top: 44px;
    }}
    .methodology-section .section-head {{
      display: block;
      max-width: 760px;
      margin: 0 auto 34px;
      text-align: center;
      border-top: none;
    }}
    .methodology-section .section-head p {{
      max-width: 58ch;
      margin: 0 auto;
    }}
    .method-grid {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
      align-items: stretch;
      gap: 22px;
    }}
    .method-card {{
      position: relative;
      min-height: 180px;
      padding: 44px 28px 28px;
      border: 1px solid rgba(32,39,51,0.08);
      border-radius: 2px;
      background: rgba(255,255,255,0.84);
      box-shadow: 0 18px 36px rgba(32,39,51,0.05);
    }}
    .method-card h4 {{
      font-family: Aptos, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      font-size: 1.06rem;
      letter-spacing: 0;
      margin-bottom: 10px;
    }}
    .floating-step {{
      position: absolute;
      top: -18px;
      left: 28px;
      display: inline-grid;
      place-items: center;
      width: 40px;
      height: 40px;
      border-radius: 999px;
      background: #ffe100;
      color: #111820;
      font-size: 0.92rem;
      font-weight: 700;
      letter-spacing: 0.08em;
    }}
    .score-overview {{
      display: grid;
      gap: 22px;
      grid-template-columns: 1fr;
      justify-items: center;
      margin-bottom: 18px;
    }}
    .scoring-section .section-head {{
      display: block;
      max-width: 760px;
      margin: 0 auto 18px;
      text-align: center;
    }}
    .scoring-section .section-head p {{
      margin: 0 auto;
    }}
    .radar-card {{
      padding: 18px;
      border-radius: 0;
      border: none;
      background: transparent;
      width: min(100%, 680px);
    }}
    .radar-chart {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .axis-grid {{
      display: grid;
      gap: 24px 18px;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      grid-auto-rows: 1fr;
      width: min(100%, 980px);
      padding-top: 20px;
      margin: 0 auto;
    }}
    .axis-tile {{
      position: relative;
      display: grid;
      justify-items: start;
      align-content: start;
      gap: 12px;
      min-height: 100%;
      padding: 40px 24px 24px;
      text-align: left;
      border: 1px solid rgba(32,39,51,0.08);
      border-radius: 2px;
      background: rgba(255,255,255,0.84);
      box-shadow: 0 18px 36px rgba(32,39,51,0.05);
      grid-column: span 2;
    }}
    .axis-tile::before {{
      content: "";
      position: absolute;
      left: -1px;
      top: -1px;
      bottom: -1px;
      width: 4px;
      background: var(--score-color, var(--gold));
    }}
    .axis-tile:nth-child(4) {{
      grid-column: 2 / span 2;
    }}
    .axis-tile:nth-child(5) {{
      grid-column: 4 / span 2;
    }}
    .axis-tile h4 {{
      font-family: Aptos, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      font-size: 1rem;
      line-height: 1.3;
      letter-spacing: 0;
      color: var(--ink);
      font-weight: 700;
      margin: 0;
    }}
    .axis-tile > p {{
      max-width: none;
      margin: 0;
      text-align: left;
    }}
    .axis-tile-meta {{
      display: flex;
      align-items: baseline;
      gap: 8px;
      margin-top: auto;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .axis-tile-meta strong {{
      color: var(--score-color, var(--ink));
    }}
    .axis-score-editor {{
      display: grid;
      gap: 5px;
      width: 100%;
      max-width: 150px;
      margin-top: 4px;
      color: var(--muted);
      font-size: 0.74rem;
      font-weight: 750;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .axis-score-editor input {{
      width: 100%;
      border: 1px solid rgba(32,39,51,0.14);
      border-radius: 4px;
      padding: 7px 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      letter-spacing: 0;
      text-transform: none;
    }}
    .stories,
    .axis-stories {{
      display: grid;
      gap: 34px;
    }}
    .story-row,
    .axis-story {{
      display: grid;
      grid-template-columns: 42px minmax(420px, 1.18fr) minmax(155px, 0.34fr) minmax(0, 0.88fr);
      gap: 22px;
      align-items: center;
      padding: 12px 0 0;
      border: none;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .story-index {{
      align-self: start;
      display: inline-grid;
      place-items: center;
      width: 44px;
      height: 44px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.08em;
    }}
    .story-media,
    .axis-story-media {{
      align-self: center;
      display: grid;
      gap: 10px;
      align-content: center;
      justify-items: center;
    }}
    .story-visual-frame,
    .axis-story-frame {{
      width: 100%;
      max-width: 680px;
      overflow: hidden;
      border-radius: 22px;
      border: 1px solid rgba(32,39,51,0.08);
      background: #ffffff;
      box-shadow: 0 18px 36px rgba(32,39,51,0.08);
    }}
    .story-visual-frame.mobile-visual-frame,
    .axis-story-frame.mobile-visual-frame {{
      max-width: 390px;
      overflow: visible;
      border: none;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .desktop-screen {{
      overflow: hidden;
      background: #ffffff;
    }}
    .desktop-screen-bar {{
      display: flex;
      align-items: center;
      gap: 7px;
      height: 28px;
      padding: 0 12px;
      border-bottom: 1px solid rgba(32,39,51,0.08);
      background: linear-gradient(180deg, #f8f8f7, #eeeeec);
    }}
    .desktop-screen-bar span {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #c8c9c7;
    }}
    .desktop-screen-bar span:first-child {{ background: #e6cf67; }}
    .desktop-screen-body {{
      aspect-ratio: 16 / 9;
      background: #ffffff;
      display: grid;
      place-items: center;
      overflow: hidden;
    }}
    .story-visual-frame img,
    .axis-story-frame img {{
      display: block;
      width: 100%;
      height: 100%;
      aspect-ratio: 16 / 9;
      object-fit: cover;
      object-position: center center;
      background: #ffffff;
    }}
    .mobile-visual-frame img {{
      aspect-ratio: auto;
      object-fit: contain;
    }}
    .story-visual-empty {{
      display: grid;
      place-items: center;
      min-height: 240px;
      padding: 18px;
      color: var(--muted);
      text-align: center;
    }}
    .story-visual-meta {{
      font-size: 0.88rem;
      color: var(--muted);
    }}
    .story-score-pane,
    .axis-story-score {{
      align-self: center;
      display: grid;
      grid-auto-rows: min-content;
      gap: 12px;
      justify-items: center;
      align-content: center;
      min-height: 100%;
      width: min(100%, 210px);
      margin-inline: auto;
      padding: 10px 4px 12px;
      border: none;
      border-radius: 0;
      background: transparent;
      text-align: center;
    }}
    .story-score-axis,
    .story-score-severity,
    .axis-story-kicker,
    .axis-story-metric {{
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .axis-story-title {{
      color: var(--ink);
      font-size: 1.08rem;
      line-height: 1.4;
      font-weight: 700;
      max-width: 14ch;
      text-align: center;
    }}
    .axis-story-kicker {{
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }}
    .story-copy,
    .axis-story-copy {{
      align-self: center;
      display: grid;
      gap: 12px;
      align-content: start;
      position: relative;
    }}
    .severity-badge {{
      justify-self: start;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      width: max-content;
      padding: 8px 12px;
      border-radius: 3px;
      border: 1px solid #f0b47d;
      background: #fff3e6;
      color: #9a3b0b;
      font-size: 0.84rem;
      font-weight: 700;
      line-height: 1;
    }}
    .severity-icon {{
      display: inline-grid;
      place-items: center;
      width: 16px;
      height: 16px;
      border: 1.6px solid currentColor;
      border-radius: 999px;
      font-size: 0.72rem;
      line-height: 1;
    }}
    .severity-icon {{
      font-family: Aptos, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    }}
    .severity-medium {{
      border-color: #e5c453;
      background: #fff8d7;
      color: #7b5c00;
    }}
    .severity-low {{
      border-color: rgba(32,39,51,0.16);
      background: rgba(255,255,255,0.78);
      color: #4f5d6f;
    }}
    .story-meta {{
      color: var(--muted);
      font-size: 0.86rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .story-copy h3,
    .axis-story-copy h3 {{
      max-width: 28ch;
    }}
    .story-copy strong,
    .axis-story-copy strong {{
      color: var(--ink);
    }}
    .story-link a {{
      text-decoration: underline;
      text-underline-offset: 3px;
    }}
    .story-confidence {{
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .signal-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .signal-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(202,162,59,0.10);
      border: 1px solid rgba(202,162,59,0.18);
      color: var(--ink);
      font-size: 0.82rem;
    }}
    .issue-tabs {{
      display: grid;
      gap: 20px;
    }}
    .issue-tab-input {{
      position: absolute;
      inline-size: 1px;
      block-size: 1px;
      opacity: 0;
      pointer-events: none;
    }}
    .issue-tab-bar {{
      display: flex;
      gap: 10px;
      overflow-x: auto;
      padding: 4px 2px 10px;
      scrollbar-color: rgba(32,39,51,0.22) transparent;
    }}
    .issue-tab-label {{
      position: relative;
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 10px 18px;
      border: 1px solid rgba(32,39,51,0.12);
      border-radius: 4px;
      background: rgba(255,255,255,0.76);
      color: var(--ink);
      font-size: 0.9rem;
      font-weight: 760;
      cursor: pointer;
      transition: background 180ms ease, color 180ms ease, border-color 180ms ease;
    }}
    .issue-tab-label span {{
      position: absolute;
      top: -8px;
      right: -4px;
      display: inline-grid;
      place-items: center;
      min-width: 22px;
      height: 22px;
      padding: 0 6px;
      border-radius: 999px;
      background: #ffe100;
      color: #111820;
      border: 1px solid rgba(32,39,51,0.16);
      font-size: 0.72rem;
      line-height: 1;
      font-weight: 850;
    }}
    .issue-panels {{
      min-height: 260px;
    }}
    .issue-panel {{
      display: none;
      grid-template-columns: 1fr;
      gap: 28px;
      align-items: start;
    }}
    .issue-card {{
      display: grid;
      grid-template-columns: minmax(320px, 0.92fr) minmax(0, 1.08fr);
      align-items: center;
      align-content: start;
      gap: clamp(22px, 4vw, 52px);
      min-height: 100%;
      padding: 18px 0 28px;
      border: none;
      border-left: 3px solid var(--gold);
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .issue-card.tone-high,
    .issue-card.tone-critical {{
      border-left-color: var(--red);
    }}
    .issue-card.tone-low {{
      border-left-color: var(--teal);
    }}
    .issue-media {{
      position: relative;
      display: grid;
      align-content: center;
      padding-left: 22px;
    }}
    .issue-evidence-frame {{
      overflow: hidden;
      border-radius: 18px;
      border: 1px solid rgba(32,39,51,0.08);
      background: #ffffff;
      box-shadow: 0 20px 42px rgba(32,39,51,0.08);
    }}
    .issue-card-top {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }}
    .issue-number {{
      position: absolute;
      top: -16px;
      left: 0;
      z-index: 2;
      display: inline-grid;
      place-items: center;
      width: 44px;
      height: 44px;
      border-radius: 999px;
      background: #ffe100;
      color: #111820;
      font-weight: 850;
      font-size: 0.82rem;
      box-shadow: 0 0 0 10px rgba(255,225,0,0.16);
    }}
    .issue-axis,
    .severity-dot {{
      display: inline-flex;
      min-height: 28px;
      align-items: center;
      padding: 5px 9px;
      border-radius: 999px;
      background: rgba(32,39,51,0.05);
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 750;
    }}
    .severity-dot.severity-high,
    .severity-dot.severity-critical {{
      background: rgba(207,81,63,0.10);
      color: #9d2f23;
    }}
    .severity-dot.severity-low {{
      background: rgba(17,136,110,0.10);
      color: #0f6a58;
    }}
    .issue-thumb {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
      background: #fff;
    }}
    .issue-copy {{
      display: grid;
      gap: 12px;
      align-content: center;
    }}
    .issue-card h3 {{
      max-width: 32ch;
      font-size: clamp(1.1rem, 1.7vw, 1.42rem);
      line-height: 1.18;
      letter-spacing: 0;
    }}
    .issue-title-row {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }}
    .issue-title-actions {{
      display: flex;
      flex: 0 0 auto;
      gap: 6px;
      align-items: center;
      padding-top: 2px;
    }}
    .mini-edit-button {{
      border: 1px solid rgba(32,39,51,0.18);
      border-radius: 3px;
      padding: 5px 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-size: 0.72rem;
      font-weight: 800;
      line-height: 1;
      cursor: pointer;
      white-space: nowrap;
    }}
    .mini-edit-button:hover,
    .mini-edit-button[aria-pressed="true"] {{
      background: var(--ink);
      border-color: var(--ink);
      color: #fff;
    }}
    .issue-card p {{
      max-width: 62ch;
      font-size: 0.98rem;
      line-height: 1.65;
    }}
    .issue-why,
    .issue-fix {{
      padding-top: 8px;
      border-top: 1px solid rgba(32,39,51,0.08);
    }}
    .issue-card strong {{
      color: var(--ink);
    }}
    .issue-meta {{
      display: flex;
      justify-content: flex-start;
      align-items: center;
      gap: 10px;
      margin-top: 4px;
      color: var(--muted);
      font-size: 0.82rem;
    }}
    .hidden-file-input {{
      display: none;
    }}
    .edit-chip {{
      width: max-content;
      border: 1px solid rgba(32,39,51,0.16);
      border-radius: 4px;
      padding: 7px 10px;
      background: rgba(255,255,255,0.74);
      color: var(--ink);
      font: inherit;
      font-size: 0.78rem;
      font-weight: 750;
      cursor: pointer;
    }}
    .edit-chip:hover,
    .edit-chip[aria-pressed="true"] {{
      background: var(--ink);
      border-color: var(--ink);
      color: #fff;
    }}
    [data-editable-field][contenteditable="true"] {{
      outline: 2px solid rgba(17,136,110,0.34);
      border-radius: 4px;
      background: rgba(17,136,110,0.08);
      padding: 2px 4px;
    }}
    @media (max-width: 760px) {{
      .issue-title-row {{
        display: grid;
      }}
      .issue-title-actions {{
        justify-content: flex-start;
      }}
    }}
    .local-publish-panel {{
      display: grid;
      gap: 14px;
      margin-top: 42px;
      padding: 24px;
      border: 1px solid rgba(198,161,55,0.24);
      border-radius: 8px;
      background: rgba(255,255,255,0.76);
      box-shadow: 0 18px 36px rgba(32,39,51,0.05);
    }}
    .local-publish-panel h2 {{
      font-size: clamp(1.35rem, 2vw, 1.8rem);
      line-height: 1.12;
    }}
    .local-publish-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    .publish-button {{
      border: 1px solid var(--ink);
      border-radius: 4px;
      padding: 12px 16px;
      background: var(--ink);
      color: #fff;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    .publish-button:disabled {{
      cursor: wait;
      opacity: 0.62;
    }}
    .publish-status {{
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .publish-status a {{
      color: var(--ink);
      font-weight: 800;
      text-underline-offset: 3px;
    }}
    .tone-critical {{ border-left: 4px solid var(--red); }}
    .tone-high {{ border-left: 4px solid #d98e2f; }}
    .tone-medium {{ border-left: 4px solid var(--gold); }}
    .reco-card {{
      width: 100%;
      overflow: hidden;
      border: 1px solid rgba(198,161,55,0.22);
      border-radius: 8px;
      background:
        radial-gradient(circle at 10% 0%, rgba(255,225,0,0.22), transparent 34%),
        linear-gradient(145deg, #fffefa, #fbf8f0);
      box-shadow: 0 18px 36px rgba(32,39,51,0.07);
    }}
    .reco-card[open] {{
      border-color: rgba(198,161,55,0.48);
      box-shadow: 0 24px 54px rgba(32,39,51,0.10);
    }}
    .reco-card summary {{
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr) 34px;
      gap: 14px;
      align-items: center;
      padding: 18px 20px;
      cursor: pointer;
      list-style: none;
    }}
    .reco-card summary::-webkit-details-marker {{
      display: none;
    }}
    .reco-card summary:focus-visible {{
      outline: none;
      box-shadow: inset 0 0 0 2px rgba(198,161,55,0.42);
    }}
    .reco-orb {{
      display: inline-grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border-radius: 999px;
      background: #ffe100;
      color: var(--ink);
      font-size: 0.9rem;
      font-weight: 800;
      box-shadow: 0 0 0 10px rgba(255,225,0,0.12), 0 18px 30px rgba(198,161,55,0.18);
    }}
    .reco-summary-copy {{
      display: grid;
      gap: 5px;
      min-width: 0;
    }}
    .reco-badge {{
      display: inline-flex;
      width: max-content;
      color: var(--ink);
      font-size: 0.78rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-weight: 700;
    }}
    .reco-summary-copy strong {{
      color: var(--ink);
      font-size: clamp(1.05rem, 1.6vw, 1.35rem);
      line-height: 1.18;
    }}
    .reco-toggle {{
      display: inline-grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border-radius: 999px;
      border: 1px solid rgba(32,39,51,0.16);
      color: var(--ink);
      font-size: 1.25rem;
      font-weight: 650;
      transition: transform 180ms ease;
    }}
    .reco-card[open] .reco-toggle {{
      transform: rotate(45deg);
    }}
    .reco-body {{
      display: grid;
      gap: 12px;
      padding: 0 20px 22px 78px;
    }}
    .reco-body p {{
      max-width: 58ch;
      font-size: 0.98rem;
      line-height: 1.65;
    }}
    .reco-card .reco-impact {{
      max-width: 44ch;
      padding: 12px 14px;
      border-left: 3px solid var(--gold);
      background: rgba(255,255,255,0.52);
      color: var(--ink);
      font-size: 0.96rem;
      font-weight: 650;
    }}
    .reco-axis {{
      display: inline-flex;
      width: max-content;
      max-width: 100%;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(32,39,51,0.12);
      background: rgba(32,39,51,0.04);
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 750;
    }}
    .reco-meta {{
      margin-top: 10px;
      font-size: 0.88rem;
    }}
    .footer {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      margin-top: 22px;
      padding: 22px 6px 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 1120px) {{
      .hero,
      .story-row,
      .axis-story {{
        grid-template-columns: 1fr;
      }}
      .story-index {{
        width: 40px;
        height: 40px;
      }}
      .scan-strip {{
        grid-template-columns: 1fr;
      }}
      .scan-static {{
        display: grid;
        grid-template-columns: 1fr;
      }}
      .axis-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .axis-tile,
      .axis-tile:nth-child(4),
      .axis-tile:nth-child(5) {{
        grid-column: auto;
      }}
      .issue-card {{
        grid-template-columns: 1fr;
        gap: 18px;
      }}
      .issue-media {{
        padding-left: 18px;
      }}
      .method-grid {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}
      .reco-stage {{
        grid-template-columns: 1fr;
        gap: 20px;
      }}
      .reco-copy {{
        position: relative;
        top: auto;
        min-height: auto;
        align-content: start;
      }}
      .reco-copy h2 {{
        max-width: 14ch;
      }}
      .reco-stack {{
        min-height: auto;
        padding-top: 4px;
      }}
      .reco-card {{
        margin-bottom: 26px;
      }}
      .topbar {{
        gap: 16px;
        padding-inline: 20px;
      }}
    }}
    @media (max-width: 720px) {{
      .shell {{ padding-inline: 14px; }}
      .hero,
      .section-panel,
      .priority-panel {{
        padding: 22px;
        border-radius: 24px;
      }}
      .topbar {{
        position: static;
        width: auto;
        margin: -24px -14px 20px;
        padding: 16px 14px;
        flex-direction: column;
        align-items: flex-start;
      }}
      .topnav {{
        gap: 10px;
        font-size: 0.84rem;
        justify-content: flex-start;
      }}
      .method-grid,
      .axis-grid {{
        grid-template-columns: 1fr;
      }}
      .reco-copy h2 {{
        font-size: clamp(2rem, 13vw, 3.2rem);
      }}
      .reco-stack {{
        padding-bottom: 0;
      }}
      .reco-card {{
        margin-bottom: 18px;
      }}
      .reco-card summary {{
        grid-template-columns: 38px minmax(0, 1fr) 30px;
        padding: 16px;
      }}
      .reco-body {{
        padding: 0 16px 18px;
      }}
      .reco-card .reco-impact {{
        margin-top: 4px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand-lockups">
        <div class="brand-primary">
          {ey_studio_logo_svg()}
        </div>
        <span class="brand-divider" aria-hidden="true"></span>
        <div class="brand-secondary">
          <div>
            <span>Audit for</span>
            <strong>{html.escape(client_lockup)}</strong>
          </div>
        </div>
      </div>
      <nav class="topnav">
        <a href="#methodology">Methodology</a>
        <a href="#priorities">Findings</a>
        <a href="#scores">Scores</a>
        {f'<a href="#independent-review">QA Review</a>' if independent_review_html else ''}
        <a href="#recommendations">Recommendations</a>
      </nav>
    </header>

    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">UX/UI Audit</p>
        <h1>{html.escape(company_name)}</h1>
        <p class="hero-lead">Comprehensive evaluation of the user experience and interface of {html.escape(audit_subject)} through the active UX/UI axes on {html.escape(pages_count)} main screen(s).</p>
        {f'<a class="hero-visit-button" href="{html.escape(tested_url)}" target="_blank" rel="noreferrer">Open tested website</a>' if tested_url.startswith(("http://", "https://")) else ''}
        <div class="hero-meta">
          <div><span>Date</span><strong>{html.escape(generated_month)}</strong></div>
          <div><span>Pages analyzed</span><strong>{html.escape(str(context.get("pagesAudited", "")))}</strong></div>
          <div><span>Audit axes</span><strong>{html.escape(str(context.get("auditAxes", "")))}</strong></div>
        </div>
      </div>
      <aside class="hero-side">
        <div class="hero-score-card">
          {hero_score}
        </div>
      </aside>
    </section>

    <section class="section-panel">
      <div class="section-head">
        <div>
          <p class="eyebrow">{html.escape(scan_eyebrow)}</p>
          <h2>{html.escape(scan_heading)}</h2>
        </div>
      </div>
      {scanned_pages_loop}
    </section>

    <section class="section-panel methodology-section" id="methodology">
      <div class="section-head">
        <div>
          <p class="eyebrow">Methodology</p>
          <h2>Our structured 3-step approach to evaluating the user experience</h2>
        </div>
      </div>
      <div class="method-grid">{methodology_html}</div>
    </section>

    <section class="section-panel scoring-section" id="scores">
      <div class="section-head">
        <div>
          <p class="eyebrow">Scoring</p>
          <h2>Five axes at a glance</h2>
        </div>
      </div>
      <div class="score-overview">
        {radar_html}
        <div class="axis-grid">{axes_tiles_html}</div>
      </div>
    </section>

    <section class="priority-panel issues-section" id="priorities">
      <div class="section-head">
        <div>
          <p class="eyebrow">Issues</p>
          <h2>Filter findings by priority or audit axis</h2>
        </div>
      </div>
      {issue_tabs_html}
    </section>

    {independent_review_html}

    <section class="section-panel recommendations-section" id="recommendations">
      <div class="reco-stage">
        <div class="section-head reco-copy">
          <div>
          <p class="eyebrow">Recommendations</p>
          <h2>Prioritized actions</h2>
          <p class="reco-lede">The highest-impact fixes are ordered for execution, with acceptance targets and business risk kept with each action.</p>
          </div>
        </div>
        <div class="reco-stack">{reco_html or "<p class='empty'>No prioritized recommendation was generated yet.</p>"}</div>
      </div>
    </section>

    {render_local_publish_panel()}

    <footer class="footer">
      <span>Generated from the automated UX/UI audit pipeline.</span>
      <span>{html.escape(clean_text(site.get("domain")) or clean_text(site.get("url")) or "Site")}</span>
    </footer>
  </div>
  {render_local_edit_script()}
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the GTM audit landing page.")
    parser.add_argument("--input", default=str(DEFAULT_GTM_AUDIT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    input_path = to_path(args.input, DEFAULT_GTM_AUDIT)
    output_dir = to_path(args.output_dir, DEFAULT_OUTPUT_DIR)
    if not input_path.exists():
        raise FileNotFoundError(f"GTM audit JSON not found: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = load_json(input_path)
    (output_dir / "index.html").write_text(render_html(payload, output_dir), encoding="utf-8")
    print(f"GTM report generated at: {output_dir / 'index.html'}")


if __name__ == "__main__":
    main()
