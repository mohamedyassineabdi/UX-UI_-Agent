from __future__ import annotations

import argparse
import html
import json
import os
import math
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from .evidence import build_gtm_spotlight


ROOT_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT_DIR / "shared" / "generated"
DEFAULT_GTM_AUDIT = GENERATED_DIR / "gtm_audit.json"
DEFAULT_OUTPUT_DIR = GENERATED_DIR / "gtm-report"

AXIS_LABELS = {
    "task_execution": "Task Execution",
    "flow_architecture": "Flow & Architecture",
    "trust_accessibility": "Trust & Accessibility",
    "ui_consistency": "UI Consistency",
    "visual_brand": "Visual Brand",
    "content_microcopy": "Content & Microcopy",
    "market_alignment": "Market Alignment",
}


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


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


def render_score_ring(score_ten: float, *, label: str, accent: str = "#caa23b", size: int = 138) -> str:
    normalized = max(0.0, min(10.0, float(score_ten)))
    stroke = max(8.0, size * 0.075)
    radius = max(24.0, (size / 2.0) - (stroke / 2.0) - 6.0)
    circumference = 2 * math.pi * radius
    progress = circumference * (normalized / 10.0)
    offset = circumference - progress
    center = size / 2
    return f"""
    <div class="score-ring" style="--ring-size:{size}px; --ring-stroke:{accent}; --ring-width:{stroke:.1f}px;">
      <svg viewBox="0 0 {size} {size}" aria-hidden="true">
        <circle cx="{center}" cy="{center}" r="{radius}" class="ring-track"></circle>
        <circle cx="{center}" cy="{center}" r="{radius}" class="ring-progress" style="stroke-dasharray:{circumference:.2f};stroke-dashoffset:{offset:.2f};"></circle>
      </svg>
      <div class="score-ring-copy">
        <strong>{normalized:.1f}</strong>
        <span>{html.escape(label)}</span>
      </div>
    </div>
    """


def render_priority_story(item: Dict[str, Any], index: int, output_dir: Path) -> str:
    spotlight = clean_text(item.get("spotlightImage"))
    shot = spotlight or href_from_repo(item.get("screenshotPath", ""), output_dir)
    page_name = clean_text(item.get("pageName")) or "Main journey"
    page_url = clean_text(item.get("pageUrl"))
    axis_score = round(float(item.get("axisScore", 0)) / 10, 1)
    axis_name = axis_label(item.get("axisId"), item.get("axisName"))
    tone = severity_tone(item.get("severity"))
    return f"""
    <article class="story-row tone-{tone}">
      <div class="story-index">0{index}</div>
      <div class="story-media">
        <div class="story-visual-frame">
          <div class="desktop-screen">
            <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
            <div class="desktop-screen-body">
              {f'<img src="{shot}" alt="{html.escape(clean_text(item.get("title")))} evidence">' if shot else '<div class="story-visual-empty">No evidence crop available</div>'}
            </div>
          </div>
        </div>
        <p class="story-visual-meta">Evidence from {html.escape(page_name)}</p>
      </div>
      <div class="story-score-pane">
        {render_score_ring(axis_score, label="Axis score", accent="#caa23b", size=128)}
        <p class="story-score-axis">{html.escape(axis_name)}</p>
        <p class="story-score-severity">{html.escape(tone.title())} severity</p>
      </div>
      <div class="story-copy">
        <p class="story-meta">{html.escape(axis_name)} | {html.escape(page_name)}</p>
        <h3>{html.escape(clean_text(item.get("title")))}</h3>
        <p><strong>Issue:</strong> {html.escape(clean_text(item.get("explanation")))}</p>
        <p><strong>Why it matters:</strong> {html.escape(clean_text(item.get("whyItMatters")))}</p>
        <p><strong>Evidence:</strong> {html.escape(clean_text(item.get("evidence")))}</p>
        <p><strong>What to change:</strong> {html.escape(clean_text(item.get("recommendation")))}</p>
        {f'<p class="story-link"><strong>Page:</strong> <a href="{html.escape(page_url)}" target="_blank" rel="noreferrer">{html.escape(page_url)}</a></p>' if page_url else ''}
        <p class="story-confidence">Confidence {float(item.get("confidence", 0)):.2f}</p>
      </div>
    </article>
    """


def render_axis_tile(axis: Dict[str, Any], index: int) -> str:
    score = round(float(axis.get("score", 0)) / 10, 1)
    tone = severity_tone(axis.get("severity")).title()
    return f"""
    <article class="axis-tile tone-{severity_tone(axis.get("severity"))}">
      <span class="floating-step">{index}</span>
      <h4>{html.escape(clean_text(axis.get("shortName")) or clean_text(axis.get("name")))}</h4>
      <p>{html.escape(clean_text(axis.get("description")) or clean_text(axis.get("businessImpact")))}</p>
      <div class="axis-tile-meta">
        <strong>{score:.1f}/10</strong>
        <span>{tone} severity</span>
      </div>
    </article>
    """


def render_axis_section(axis: Dict[str, Any], index: int, output_dir: Path) -> str:
    lead_item = ((axis.get("painPoints") or [])[:1] or (axis.get("strengths") or [])[:1] or [{}])[0]
    shot = clean_text(lead_item.get("spotlightImage")) or href_from_repo(lead_item.get("screenshotPath", ""), output_dir)
    axis_score = round(float(axis.get("score", 0)) / 10, 1)
    signals = axis.get("signals") or {}
    tone = severity_tone(axis.get("severity"))
    evidence_items = [clean_text(item) for item in (axis.get("evidence") or []) if clean_text(item)][:4]
    evidence_html = "".join(f'<span class="signal-chip">{html.escape(item)}</span>' for item in evidence_items)
    return f"""
    <article class="axis-story tone-{tone}" id="axis-{index}">
      <div class="story-index">0{index}</div>
      <div class="axis-story-media">
        <div class="axis-story-frame">
          <div class="desktop-screen">
            <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
            <div class="desktop-screen-body">
              {f'<img src="{shot}" alt="{html.escape(clean_text(axis.get("shortName")) or clean_text(axis.get("name")))} visual evidence">' if shot else '<div class="story-visual-empty">No evidence crop available</div>'}
            </div>
          </div>
        </div>
      </div>
      <div class="axis-story-score">
        {render_score_ring(axis_score, label="Axis score", accent="#caa23b", size=154)}
        <p class="axis-story-title">{html.escape(clean_text(axis.get("shortName")))}</p>
        <p class="axis-story-metric">{html.escape(tone.title())} severity</p>
      </div>
      <div class="axis-story-copy">
        <p class="story-meta">{html.escape(clean_text(axis.get("shortName")) or clean_text(axis.get("name")))}</p>
        <h3>{html.escape(clean_text(axis.get("summary")))}</h3>
        <p><strong>Commercial impact:</strong> {html.escape(clean_text(axis.get("businessImpact")))}</p>
        {f'<p><strong>Lead issue:</strong> {html.escape(clean_text(lead_item.get("title")))}</p>' if clean_text(lead_item.get("title")) else ''}
        {f'<p><strong>Observed friction:</strong> {html.escape(clean_text(lead_item.get("explanation")))}</p>' if clean_text(lead_item.get("explanation")) else ''}
        {f'<p><strong>Recommended move:</strong> {html.escape(clean_text(lead_item.get("recommendation")))}</p>' if clean_text(lead_item.get("recommendation")) else ''}
        <div class="signal-chip-row">{evidence_html}</div>
      </div>
    </article>
    """


def render_scanned_page(item: Dict[str, Any], output_dir: Path) -> str:
    href = href_from_repo(item.get("screenshot_path", ""), output_dir)
    if not href:
        return ""
    page_name = clean_text(item.get("page_name")) or clean_text(item.get("pageName")) or "Page"
    page_url = clean_text(item.get("page_url")) or clean_text(item.get("pageUrl"))
    title = clean_text(item.get("title")) or clean_text(item.get("reason")) or page_name
    return f"""
    <a class="scan-card" href="{href}" target="_blank" rel="noreferrer">
      <div class="scan-screen">
        <div class="desktop-screen-bar"><span></span><span></span><span></span></div>
        <img src="{href}" alt="{html.escape(page_name)} screenshot">
      </div>
      <span>{html.escape(page_name)}</span>
      <strong>{html.escape(title)}</strong>
      {f'<em>{html.escape(page_url)}</em>' if page_url else ''}
    </a>
    """


def render_radar_chart(axes: list[Dict[str, Any]]) -> str:
    if not axes:
        return "<p class='empty'>No scoring data available.</p>"

    labels = [clean_text(axis.get("shortName") or axis.get("name") or "Axis") for axis in axes]
    values = [max(0.0, min(10.0, float(axis.get("score", 0)) / 10.0)) for axis in axes]
    count = len(labels)
    cx = 260
    cy = 250
    radius = 150
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
        label_x, label_y = polar_point(index, 1.23)
        anchor = "middle"
        if label_x < cx - 40:
            anchor = "end"
        elif label_x > cx + 40:
            anchor = "start"
        label_nodes.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor}" fill="#4d5868" font-size="15" font-weight="600">{html.escape(label)}</text>'
        )

    data_points = [polar_point(index, value / 10.0) for index, value in enumerate(values)]
    data_polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_points)
    point_nodes = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#caa23b" stroke="#ffffff" stroke-width="2"></circle>'
        for x, y in data_points
    )

    return f"""
    <div class="radar-card">
      <svg class="radar-chart" viewBox="0 0 520 500" role="img" aria-label="Seven-axis scoring radar chart">
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

    scanned_pages_data: List[Dict[str, Any]] = []
    seen_scanned_pages: set[str] = set()
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
    cleaned_path = to_path(clean_text(artifacts.get("cleanedPath")), ROOT_DIR / "shared" / "generated" / "person_a_cleaned.json")
    rendered_path = to_path(clean_text(artifacts.get("renderedPath")), ROOT_DIR / "shared" / "generated" / "rendered_ui_extraction.json")
    for index, item in enumerate(priorities_data, start=1):
        item["spotlightImage"] = build_gtm_spotlight(
            item=item,
            output_dir=output_dir,
            cleaned_path=cleaned_path,
            rendered_path=rendered_path,
            issue_index=index,
        )
        page_key = clean_text(item.get("pageUrl")) or clean_text(item.get("pageName")) or clean_text(item.get("screenshotPath"))
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
    priorities = "".join(render_priority_story(item, index, output_dir) for index, item in enumerate(priorities_data, start=1))
    axes_data = payload.get("axes") or []
    for index, axis in enumerate(axes_data, start=1):
        lead_item = ((axis.get("painPoints") or [])[:1] or (axis.get("strengths") or [])[:1] or [{}])[0]
        lead_item["spotlightImage"] = build_gtm_spotlight(
            item=lead_item,
            output_dir=output_dir,
            cleaned_path=cleaned_path,
            rendered_path=rendered_path,
            issue_index=100 + index,
        )
    axes_tiles_html = "".join(render_axis_tile(axis, index) for index, axis in enumerate(axes_data, start=1))
    axis_sections_html = "".join(render_axis_section(axis, index, output_dir) for index, axis in enumerate(axes_data, start=1))
    radar_html = render_radar_chart(axes_data)
    methodology_html = "".join(
        f"""
        <div class="method-card">
          <span class="floating-step">{index + 1}</span>
          <h4>{html.escape(clean_text(item.get("step")))}</h4>
          <p>{html.escape(clean_text(item.get("description")))}</p>
        </div>
        """
        for index, item in enumerate(methodology)
    )
    reco_html = "".join(
        f"""
        <article class="reco-card priority-{html.escape(clean_text(item.get('priority')).lower())}">
          <span class="reco-badge">{html.escape(clean_text(item.get("priority")))}</span>
          <h4>{html.escape(clean_text(item.get("title")))}</h4>
          <p>{html.escape(clean_text(item.get("description")))}</p>
          <p class="reco-meta">{html.escape(clean_text(item.get("impact")))} | {html.escape(clean_text(item.get("axis")))}</p>
        </article>
        """
        for item in recommendations[:5]
    )
    strongest_axis = summary.get("strongestAxis") or {}
    weakest_axis = summary.get("weakestAxis") or {}
    strongest = axis_label(strongest_axis.get("id"), strongest_axis.get("shortName") or strongest_axis.get("name")) if strongest_axis else ""
    weakest = axis_label(weakest_axis.get("id"), weakest_axis.get("shortName") or weakest_axis.get("name")) if weakest_axis else ""
    overall_ten = round(float(summary.get("overallScore", 0)) / 10, 1)
    hero_score = render_score_ring(overall_ten, label="Overall", accent="#caa23b", size=170)
    client_lockup = clean_text(site.get("display_name")) or clean_text(site.get("domain")) or "Client"
    scanned_pages_html = "".join(render_scanned_page(item, output_dir) for item in scanned_pages_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(clean_text(site.get("display_name")) or "GTM Audit")}</title>
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
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
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
      padding-top: 10px;
      color: var(--ink);
    }}
    .brand-primary::before {{
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      width: 44px;
      height: 8px;
      background: #ffd44d;
      transform: skewX(-22deg);
    }}
    .brand-ey {{
      display: inline-flex;
      align-items: flex-end;
      gap: 6px;
    }}
    .brand-ey strong {{
      font-size: 1.7rem;
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .brand-ey span {{
      font-size: 1rem;
      line-height: 1.1;
      padding-bottom: 3px;
      font-weight: 600;
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
      font-family: Georgia, "Times New Roman", serif;
      line-height: 1.06;
      letter-spacing: -0.02em;
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
    .scan-strip {{
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(280px, 340px);
      gap: 18px;
      overflow-x: auto;
      padding: 6px 0 8px;
      margin: 6px 0 18px;
      scrollbar-width: thin;
      scrollbar-color: rgba(32,39,51,0.22) transparent;
    }}
    .scan-card {{
      display: block;
      text-decoration: none;
      color: inherit;
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
    .context-grid,
    .method-grid,
    .reco-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
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
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
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
      grid-template-columns: repeat(16, minmax(0, 1fr));
      grid-auto-rows: 1fr;
      width: 100%;
      padding-top: 20px;
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
      grid-column: span 4;
    }}
    .axis-tile:nth-child(5) {{
      grid-column: 3 / span 4;
    }}
    .axis-tile h4 {{
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
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
      color: var(--ink);
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
      align-items: start;
      padding: 12px 0 0;
      border: none;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .story-index {{
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
      display: grid;
      gap: 10px;
      align-content: start;
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
      display: grid;
      gap: 12px;
      align-content: start;
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
    .tone-critical {{ border-left: 4px solid var(--red); }}
    .tone-high {{ border-left: 4px solid #d98e2f; }}
    .tone-medium {{ border-left: 4px solid var(--gold); }}
    .reco-card {{
      padding: 8px 0 0;
      border-top: 1px solid rgba(32,39,51,0.08);
    }}
    .reco-badge {{
      display: inline-block;
      margin-bottom: 10px;
      color: var(--ink);
      font-size: 0.78rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-weight: 700;
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
      .axis-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .axis-tile,
      .axis-tile:nth-child(5) {{
        grid-column: auto;
      }}
      .method-grid {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
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
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand-lockups">
        <div class="brand-primary">
          <div class="brand-ey">
            <strong>EY</strong>
            <span>Studio+</span>
          </div>
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
        <a href="#context">Context &amp; Methodology</a>
        <a href="#priorities">Findings</a>
        <a href="#scores">Scores</a>
        <a href="#recommendations">Recommendations</a>
      </nav>
    </header>

    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">GTM UX/UI Audit</p>
        <h1>Hello, {html.escape(clean_text(site.get("display_name")) or "Client site")}</h1>
        <p class="hero-lead">Take a look at your go-to-market experience score.</p>
        <p class="hero-subcopy">{html.escape(clean_text(summary.get("summary")))}</p>
      </div>
      <aside class="hero-side">
        <div class="hero-score-card">
          {hero_score}
          <p>This first-pass score is built from the GTM-focused UX/UI signals already collected by the audit pipeline.</p>
        </div>
      </aside>
    </section>

    <section class="section-panel">
      <div class="section-head">
        <div>
          <p class="eyebrow">Pages Scanned</p>
          <h2>Representative pages captured during the audit</h2>
        </div>
        <p>Scroll horizontally to review the pages used as visual evidence before reading the commercial synthesis.</p>
      </div>
      <div class="scan-strip">{scanned_pages_html or "<p class='empty'>No scanned-page screenshots were available for this run.</p>"}</div>
    </section>

    <section class="section-panel" id="context">
      <div class="section-head">
        <div>
          <p class="eyebrow">Context</p>
          <h2>Audit framing</h2>
        </div>
        <p>The GTM layer does not repeat the full detailed audit. It isolates the friction points most likely to affect first impressions, trust, clarity, and sales readiness.</p>
      </div>
      <div class="context-grid">
        <div class="context-card"><span>Pages audited</span><strong>{html.escape(str(context.get("pagesAudited", "")))}</strong><p>Representative pages used for the commercial synthesis.</p></div>
        <div class="context-card"><span>Navigation scope</span><strong>{html.escape(str(context.get("topLevelNavigation", "")))}</strong><p>Primary navigation areas considered for information architecture review.</p></div>
        <div class="context-card"><span>Axes reviewed</span><strong>{html.escape(str(context.get("auditAxes", "")))}</strong><p>The seven GTM-oriented UX/UI lenses used in this report.</p></div>
      </div>
    </section>

    <section class="section-panel methodology-section" id="methodology">
      <div class="section-head">
        <div>
          <p class="eyebrow">Methodology</p>
          <h2>Our structured 3-step approach to evaluating the user experience</h2>
        </div>
        <p>The model reads structured audit evidence first, then adds concise synthesis so the final output stays commercial, not overly technical.</p>
      </div>
      <div class="method-grid">{methodology_html}</div>
    </section>

    <section class="section-panel scoring-section" id="scores">
      <div class="section-head">
        <div>
          <p class="eyebrow">Scoring</p>
          <h2>Seven axes at a glance</h2>
        </div>
        <p>The chart shows where the product feels solid and where the experience starts to weaken before a prospect even engages with sales.</p>
      </div>
      <div class="score-overview">
        {radar_html}
        <div class="score-note">
          <span>Scoring logic</span>
          <strong>{html.escape(str(overall_ten))}/10</strong>
          <p>Each axis score is normalized on a ten-point scale from the detailed checks, rendered UI extraction, interaction traces, and optional vision review.</p>
        </div>
        <div class="axis-grid">{axes_tiles_html}</div>
      </div>
    </section>

    <section class="priority-panel" id="priorities">
      <div class="section-head">
        <div>
          <p class="eyebrow">Priority Issues</p>
          <h2>Only the pain points that matter most</h2>
        </div>
        <p>The evidence stays on the left so each issue can be read against the exact UI state that triggered the finding.</p>
      </div>
      <div class="stories">{priorities or "<p class='empty'>No major GTM priorities were identified in this first pass.</p>"}</div>
    </section>

    <section class="section-panel" id="axes">
      <div class="section-head">
        <div>
          <p class="eyebrow">Axis Deep Dive</p>
          <h2>One section per commercial lens</h2>
        </div>
        <p>Each axis keeps one lead visual, one score, and one practical interpretation so the report remains presentable in a business conversation.</p>
      </div>
      <div class="axis-stories">{axis_sections_html or "<p class='empty'>No axis breakdown was generated yet.</p>"}</div>
    </section>

    <section class="section-panel" id="recommendations">
      <div class="section-head">
        <div>
          <p class="eyebrow">Recommendations</p>
          <h2>Prioritized actions</h2>
        </div>
        <p>The action list is intentionally short. The goal is to frame the first commercial moves, not to exhaust every possible optimization.</p>
      </div>
      <div class="reco-grid">{reco_html or "<p class='empty'>No prioritized recommendation was generated yet.</p>"}</div>
    </section>

    <footer class="footer">
      <span>Generated from the automated GTM audit pipeline.</span>
      <span>{html.escape(clean_text(site.get("domain")) or clean_text(site.get("url")) or "Site")}</span>
    </footer>
  </div>
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
