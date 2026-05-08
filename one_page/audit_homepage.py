from __future__ import annotations

import argparse
import asyncio
import html
import json
import math
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


DESKTOP_VIEWPORT = {"width": 1440, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
DEFAULT_OUTPUT = Path("output") / "latest"


@dataclass
class Finding:
    title: str
    severity: str
    score: float
    issue: str
    why_it_matters: str
    recommendation: str
    evidence_image: str = ""


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("URL is required")
    parsed = urlparse(value)
    if not parsed.scheme:
        value = "https://" + value
    return value


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def score_from_issues(issues: int, base: float = 9.0, penalty: float = 1.4) -> float:
    return max(1.0, min(10.0, round(base - issues * penalty, 1)))


def severity_from_score(score: float) -> str:
    if score < 5:
        return "major"
    if score < 7:
        return "moderate"
    return "minor"


async def wait_ready(page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(700)


async def dismiss_common_banners(page) -> None:
    labels = [
        "accept",
        "agree",
        "allow all",
        "ok",
        "got it",
        "continue",
        "reject all",
        "close",
    ]
    for label in labels:
        try:
            locator = page.get_by_role("button", name=re.compile(label, re.I)).first
            if await locator.count():
                await locator.click(timeout=900)
                await page.wait_for_timeout(250)
                return
        except Exception:
            continue


async def scroll_probe(page) -> None:
    await page.evaluate(
        """async () => {
            const steps = [0.25, 0.5, 0.75, 1];
            const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
            for (const step of steps) {
                window.scrollTo(0, Math.round(maxY * step));
                await new Promise(resolve => setTimeout(resolve, 180));
            }
            window.scrollTo(0, 0);
            await new Promise(resolve => setTimeout(resolve, 220));
        }"""
    )


async def extract_page_signals(page) -> Dict[str, Any]:
    return await page.evaluate(
        """() => {
            const isVisible = (el) => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
            };
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const rectOf = (el) => {
                const r = el.getBoundingClientRect();
                return {x: r.x, y: r.y, width: r.width, height: r.height};
            };
            const pick = (selector, limit = 80) => Array.from(document.querySelectorAll(selector)).filter(isVisible).slice(0, limit);
            const buttons = pick('button, [role="button"], input[type="button"], input[type="submit"], a[href]');
            const inputs = pick('input, textarea, select');
            const images = pick('img', 120);
            const headings = pick('h1,h2,h3', 80).map(el => ({tag: el.tagName.toLowerCase(), text: textOf(el), rect: rectOf(el)}));
            const links = pick('a[href]', 140).map(el => ({text: textOf(el) || el.getAttribute('aria-label') || '', href: el.href, rect: rectOf(el)}));
            const controls = buttons.map(el => ({
                tag: el.tagName.toLowerCase(),
                text: textOf(el) || el.getAttribute('aria-label') || el.value || '',
                href: el.href || '',
                role: el.getAttribute('role') || '',
                rect: rectOf(el)
            }));
            const inputInfo = inputs.map(el => {
                const id = el.id;
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                return {
                    type: el.getAttribute('type') || el.tagName.toLowerCase(),
                    name: el.getAttribute('name') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    label: label ? textOf(label) : '',
                    rect: rectOf(el)
                };
            });
            const navText = pick('nav, header, [role="navigation"], [class*="nav"], [class*="menu"]', 20).map(textOf).filter(Boolean);
            const bodyText = textOf(document.body).slice(0, 12000);
            const viewportEls = Array.from(document.querySelectorAll('body *')).filter(isVisible).filter(el => {
                const r = el.getBoundingClientRect();
                return r.y < window.innerHeight && r.y + r.height > 0;
            });
            const fontSizes = viewportEls.slice(0, 220).map(el => parseFloat(getComputedStyle(el).fontSize || '0')).filter(n => n > 0);
            const oversized = Array.from(document.querySelectorAll('body *')).filter(isVisible).filter(el => {
                const r = el.getBoundingClientRect();
                return r.right > window.innerWidth + 2 || r.left < -2;
            }).slice(0, 20).map(el => ({text: textOf(el).slice(0, 120), rect: rectOf(el)}));
            const trustWords = ['client', 'clients', 'trusted', 'testimonial', 'case study', 'awards', 'certified', 'partners', 'reviews', 'portfolio', 'work'];
            const lowerBody = bodyText.toLowerCase();
            return {
                url: location.href,
                title: document.title || '',
                lang: document.documentElement.lang || '',
                metaDescription: document.querySelector('meta[name="description"]')?.content || '',
                bodyText,
                wordCount: bodyText.split(/\\s+/).filter(Boolean).length,
                h1: pick('h1', 12).map(el => ({text: textOf(el), rect: rectOf(el)})),
                headings,
                links,
                controls,
                inputs: inputInfo,
                images: images.map(el => ({src: el.currentSrc || el.src, alt: el.getAttribute('alt') || '', rect: rectOf(el)})),
                navText,
                hasHeader: !!document.querySelector('header'),
                hasMain: !!document.querySelector('main'),
                hasFooter: !!document.querySelector('footer'),
                hasNav: !!document.querySelector('nav, [role="navigation"]'),
                viewport: {width: window.innerWidth, height: window.innerHeight},
                documentSize: {width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight},
                horizontalOverflowPx: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
                oversized,
                fontSizes,
                trustSignalCount: trustWords.filter(word => lowerBody.includes(word)).length,
                perf: performance.getEntriesByType('navigation')[0] ? {
                    domContentLoaded: Math.round(performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd),
                    loadEventEnd: Math.round(performance.getEntriesByType('navigation')[0].loadEventEnd)
                } : {}
            };
        }"""
    )


def control_is_primary(control: Dict[str, Any]) -> bool:
    text = clean_text(control.get("text")).lower()
    href = clean_text(control.get("href")).lower()
    primary_terms = [
        "contact",
        "get in touch",
        "book",
        "start",
        "demo",
        "buy",
        "quote",
        "request",
        "sign up",
        "try",
        "work with",
        "let's talk",
    ]
    return any(term in text or term in href for term in primary_terms)


def first_rect_for(signals: Dict[str, Any], keywords: List[str]) -> Optional[Dict[str, float]]:
    candidates = []
    for collection in ("controls", "links", "headings", "h1"):
        for item in signals.get(collection, []):
            text = clean_text(item.get("text")).lower()
            if any(keyword in text for keyword in keywords):
                rect = item.get("rect") or {}
                if rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
                    candidates.append(rect)
    return candidates[0] if candidates else None


def crop_evidence(screenshot: Path, output: Path, rect: Optional[Dict[str, float]] = None) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(screenshot) as img:
        img = img.convert("RGB")
        width, height = img.size
        target_ratio = 16 / 9
        if rect:
            x = max(0, float(rect.get("x", 0)))
            y = max(0, float(rect.get("y", 0)))
            w = max(1, float(rect.get("width", width)))
            h = max(1, float(rect.get("height", height)))
            crop_w = min(width, max(900, int(w * 3.2)))
            crop_h = min(height, int(crop_w / target_ratio))
            if crop_h < h * 2.2:
                crop_h = min(height, int(h * 2.2))
                crop_w = min(width, int(crop_h * target_ratio))
            left = int(max(0, min(x + w / 2 - crop_w / 2, width - crop_w)))
            top = int(max(0, min(y + h / 2 - crop_h / 2, height - crop_h)))
        else:
            crop_w = width
            crop_h = min(height, int(width / target_ratio))
            left = 0
            top = 0
        crop = img.crop((left, top, left + crop_w, top + crop_h)).resize((1280, 720))
        if rect:
            sx = 1280 / crop_w
            sy = 720 / crop_h
            bounds = (
                int((x - left) * sx) - 18,
                int((y - top) * sy) - 18,
                int((x + w - left) * sx) + 18,
                int((y + h - top) * sy) + 18,
            )
            draw = ImageDraw.Draw(crop)
            draw.rounded_rectangle(bounds, radius=22, outline=(255, 52, 52), width=8)
        crop.save(output, "PNG", optimize=True)
    return output.name


def analyze(signals: Dict[str, Any], mobile: Dict[str, Any], output_dir: Path) -> List[Finding]:
    desktop_shot = output_dir / "screenshots" / "desktop.png"
    mobile_shot = output_dir / "screenshots" / "mobile.png"
    evidence_dir = output_dir / "evidence"
    findings: List[Finding] = []

    h1_texts = [clean_text(item.get("text")) for item in signals.get("h1", []) if clean_text(item.get("text"))]
    meta = clean_text(signals.get("metaDescription"))
    word_count = int(signals.get("wordCount") or 0)
    issues = 0
    if not h1_texts:
        issues += 1
    if h1_texts and len(h1_texts[0]) < 18:
        issues += 1
    if not meta:
        issues += 1
    if word_count < 120:
        issues += 1
    score = score_from_issues(issues)
    if issues:
        rect = (signals.get("h1") or [{}])[0].get("rect") if signals.get("h1") else None
        findings.append(
            Finding(
                title="Above-the-fold value proposition",
                severity=severity_from_score(score),
                score=score,
                issue="The homepage does not make the offer, audience, or value proposition clear enough in the first screen.",
                why_it_matters="Visitors need to understand what the company does and why it matters before they decide to explore.",
                recommendation="Use one specific H1, a short supporting paragraph, and a visible next action that names the outcome.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "value-proposition.png", rect),
            )
        )

    controls = signals.get("controls", [])
    primary_controls = [control for control in controls if control_is_primary(control)]
    visible_primary = [control for control in primary_controls if (control.get("rect") or {}).get("y", 9999) < DESKTOP_VIEWPORT["height"]]
    issues = 0
    if not primary_controls:
        issues += 2
    elif not visible_primary:
        issues += 1
    vague = [c for c in controls if clean_text(c.get("text")).lower() in {"learn more", "more", "click here", "read more", "submit"}]
    if len(vague) >= 3:
        issues += 1
    score = score_from_issues(issues)
    if issues:
        rect = (primary_controls[0].get("rect") if primary_controls else first_rect_for(signals, ["learn", "more", "read"]))
        findings.append(
            Finding(
                title="Primary action clarity",
                severity=severity_from_score(score),
                score=score,
                issue="The homepage does not expose a strong, clearly labeled primary CTA early enough.",
                why_it_matters="A weak or hidden CTA makes qualified visitors work harder to start a commercial journey.",
                recommendation="Place one primary CTA above the fold and label it with a verb plus outcome, such as 'Contact us', 'Book a call', or 'View work'.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "primary-cta.png", rect),
            )
        )

    nav_text = " ".join(signals.get("navText", []))
    link_count = len(signals.get("links", []))
    issues = 0
    if not signals.get("hasNav") and not nav_text:
        issues += 2
    if link_count < 4:
        issues += 1
    if len(nav_text) > 500:
        issues += 1
    score = score_from_issues(issues)
    if issues:
        findings.append(
            Finding(
                title="Navigation discoverability",
                severity=severity_from_score(score),
                score=score,
                issue="Navigation is missing, too sparse, or not structured as a recognizable navigation landmark.",
                why_it_matters="Clear navigation lets users validate scope, services, proof, and contact paths without guessing.",
                recommendation="Use a semantic nav/header with a small set of persistent links for the main user decisions.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "navigation.png", None),
            )
        )

    mobile_overflow = int(mobile.get("horizontalOverflowPx") or 0)
    desktop_overflow = int(signals.get("horizontalOverflowPx") or 0)
    mobile_h1 = clean_text(((mobile.get("h1") or [{}])[0] or {}).get("text"))
    issues = 0
    if mobile_overflow > 8:
        issues += 2
    if desktop_overflow > 8:
        issues += 1
    if h1_texts and not mobile_h1:
        issues += 1
    score = score_from_issues(issues)
    if issues:
        findings.append(
            Finding(
                title="Desktop-to-mobile responsiveness",
                severity=severity_from_score(score),
                score=score,
                issue=f"The page shows responsive risk. Desktop overflow: {desktop_overflow}px; mobile overflow: {mobile_overflow}px.",
                why_it_matters="Mobile visitors should be able to read and navigate without horizontal panning, clipping, or missing core content.",
                recommendation="Test at 390px, 430px, 768px, and desktop widths; remove fixed-width rows and make media/text blocks fluid.",
                evidence_image=crop_evidence(mobile_shot, evidence_dir / "responsive.png", None),
            )
        )

    images = signals.get("images", [])
    missing_alt = [img for img in images if not clean_text(img.get("alt"))]
    unlabeled_inputs = [
        item for item in signals.get("inputs", [])
        if not clean_text(item.get("label") or item.get("ariaLabel") or item.get("placeholder"))
    ]
    issues = 0
    if len(signals.get("h1", [])) != 1:
        issues += 1
    if images and len(missing_alt) / max(len(images), 1) > 0.35:
        issues += 1
    if unlabeled_inputs:
        issues += 2
    if not signals.get("hasMain"):
        issues += 1
    score = score_from_issues(issues)
    if issues:
        findings.append(
            Finding(
                title="Accessibility fundamentals",
                severity=severity_from_score(score),
                score=score,
                issue=f"Accessibility basics need review: H1 count={len(signals.get('h1', []))}, missing image alt={len(missing_alt)}, unlabeled inputs={len(unlabeled_inputs)}.",
                why_it_matters="Basic semantic structure and labels improve usability for assistive technology, search, and scanning.",
                recommendation="Keep one H1, add a main landmark, write meaningful alt text for content images, and label every form control.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "accessibility.png", None),
            )
        )

    inputs = signals.get("inputs", [])
    issues = 0
    if len(inputs) > 8:
        issues += 2
    elif len(inputs) > 5:
        issues += 1
    required_like = [item for item in inputs if clean_text(item.get("name") or item.get("placeholder") or item.get("label"))]
    if inputs and len(required_like) < len(inputs) * 0.7:
        issues += 1
    score = score_from_issues(issues)
    if issues:
        rect = inputs[0].get("rect") if inputs else None
        findings.append(
            Finding(
                title="Form friction",
                severity=severity_from_score(score),
                score=score,
                issue=f"The homepage has {len(inputs)} visible form controls, which may create avoidable effort.",
                why_it_matters="Long or unclear forms reduce completion, especially before the visitor understands the value exchange.",
                recommendation="Ask only for fields required for the next step and explain what happens after submission.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "forms.png", rect),
            )
        )

    font_sizes = [float(x) for x in signals.get("fontSizes", []) if x]
    small_text_ratio = 0.0
    if font_sizes:
        small_text_ratio = sum(1 for size in font_sizes if size < 13) / len(font_sizes)
    issues = 0
    if small_text_ratio > 0.35:
        issues += 1
    if len(set(round(size) for size in font_sizes)) > 18:
        issues += 1
    score = score_from_issues(issues)
    if issues:
        findings.append(
            Finding(
                title="Visual hierarchy and readability",
                severity=severity_from_score(score),
                score=score,
                issue="The page may rely on too many text scales or small text in the first screen.",
                why_it_matters="A strong hierarchy helps users scan the offer, proof, and next step quickly.",
                recommendation="Reduce type-scale variation, keep body text comfortably readable, and make the main message visually dominant.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "hierarchy.png", None),
            )
        )

    trust_count = int(signals.get("trustSignalCount") or 0)
    issues = 0
    if trust_count < 2:
        issues += 1
    if not any(term in signals.get("bodyText", "").lower() for term in ["case", "client", "portfolio", "work", "testimonial"]):
        issues += 1
    score = score_from_issues(issues)
    if issues:
        findings.append(
            Finding(
                title="Trust and proof",
                severity=severity_from_score(score),
                score=score,
                issue="The homepage does not surface enough proof signals such as clients, case studies, testimonials, or credibility markers.",
                why_it_matters="Visitors need evidence that the offer is credible before they spend time evaluating it.",
                recommendation="Bring proof closer to the first or second viewport: client logos, quantified outcomes, case studies, testimonials, or certifications.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "trust.png", None),
            )
        )

    perf = signals.get("perf") or {}
    load_ms = int(perf.get("loadEventEnd") or 0)
    issues = 1 if load_ms and load_ms > 3500 else 0
    score = score_from_issues(issues, base=9.0, penalty=2.0)
    if issues:
        findings.append(
            Finding(
                title="Basic loading performance",
                severity=severity_from_score(score),
                score=score,
                issue=f"The browser load event completed at roughly {load_ms}ms in this test run.",
                why_it_matters="Slow first impressions reduce patience and make the experience feel less polished.",
                recommendation="Compress large media, defer non-critical scripts, and prioritize above-the-fold assets.",
                evidence_image=crop_evidence(desktop_shot, evidence_dir / "performance.png", None),
            )
        )

    return findings[:10]


def overall_score(findings: List[Finding]) -> float:
    if not findings:
        return 9.2
    return round(sum(f.score for f in findings) / len(findings), 1)


def render_report(url: str, signals: Dict[str, Any], mobile: Dict[str, Any], findings: List[Finding], output_dir: Path) -> None:
    score = overall_score(findings)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    finding_cards = []
    for index, finding in enumerate(findings, 1):
        img = f"evidence/{finding.evidence_image}" if finding.evidence_image else "screenshots/desktop.png"
        finding_cards.append(
            f"""
            <article class="finding severity-{html.escape(finding.severity)}">
              <div class="shot"><img src="{html.escape(img)}" alt="{html.escape(finding.title)} evidence"></div>
              <div class="copy">
                <div class="meta"><span>{index:02d}</span><strong>{html.escape(finding.severity.title())}</strong></div>
                <h3>{html.escape(finding.title)}</h3>
                <p><b>Issue:</b> {html.escape(finding.issue)}</p>
                <p><b>Why it matters:</b> {html.escape(finding.why_it_matters)}</p>
                <p><b>Recommendation:</b> {html.escape(finding.recommendation)}</p>
                <div class="score">Score {finding.score:.1f}/10</div>
              </div>
            </article>
            """
        )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Homepage UX/UI Audit</title>
  <style>
    :root {{
      --ink: #141821;
      --muted: #687385;
      --line: #e8e2d7;
      --paper: #fbf7ef;
      --card: #fffdfa;
      --gold: #cda334;
      --red: #cf513f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background: var(--paper);
      line-height: 1.55;
    }}
    header {{
      padding: 54px min(7vw, 92px) 30px;
      border-bottom: 1px solid var(--line);
      background: #fffaf0;
    }}
    h1 {{
      max-width: 850px;
      margin: 0;
      font-size: clamp(2.3rem, 5vw, 5rem);
      line-height: 0.98;
      letter-spacing: 0;
    }}
    .sub {{
      max-width: 760px;
      color: var(--muted);
      font-size: 1.05rem;
      margin-top: 18px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 34px;
      max-width: 980px;
    }}
    .metric {{
      border: 1px solid var(--line);
      background: var(--card);
      padding: 18px;
      border-radius: 8px;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .metric strong {{
      display: block;
      margin-top: 6px;
      font-size: 1.7rem;
    }}
    main {{
      width: min(1180px, calc(100% - 34px));
      margin: 0 auto;
      padding: 34px 0 70px;
    }}
    .screens {{
      display: grid;
      grid-template-columns: 1.5fr 0.7fr;
      gap: 18px;
      margin: 20px 0 36px;
      align-items: start;
    }}
    .screen {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: white;
      box-shadow: 0 18px 38px rgba(30, 25, 15, 0.08);
    }}
    .screen img {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .finding {{
      display: grid;
      grid-template-columns: minmax(320px, 1fr) minmax(360px, 0.9fr);
      gap: 34px;
      align-items: center;
      padding: 34px 0;
      border-top: 1px solid var(--line);
    }}
    .shot {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: white;
      box-shadow: 0 18px 38px rgba(30, 25, 15, 0.08);
    }}
    .shot img {{
      display: block;
      width: 100%;
    }}
    .meta {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.78rem;
    }}
    .meta span {{
      display: inline-grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--ink);
      letter-spacing: 0;
      font-weight: 700;
    }}
    h2 {{
      font-size: 1.7rem;
      margin-top: 42px;
    }}
    h3 {{
      font-size: 1.45rem;
      line-height: 1.12;
      margin: 14px 0 14px;
    }}
    p {{
      color: var(--muted);
      margin: 10px 0;
    }}
    p b {{
      color: var(--ink);
    }}
    .score {{
      display: inline-block;
      margin-top: 12px;
      padding: 8px 12px;
      border: 1px solid rgba(205, 163, 52, 0.45);
      border-radius: 999px;
      background: rgba(205, 163, 52, 0.10);
      font-weight: 700;
    }}
    .empty {{
      padding: 34px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    footer {{
      color: var(--muted);
      padding-top: 28px;
      border-top: 1px solid var(--line);
      font-size: 0.9rem;
    }}
    @media (max-width: 860px) {{
      .summary, .screens, .finding {{
        grid-template-columns: 1fr;
      }}
      header {{
        padding-inline: 22px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Homepage UX/UI Audit</h1>
    <p class="sub">Audit for <b>{html.escape(url)}</b>. Generated {html.escape(generated)} from desktop and mobile browser captures.</p>
    <section class="summary">
      <div class="metric"><span>Overall Score</span><strong>{score:.1f}/10</strong></div>
      <div class="metric"><span>Findings</span><strong>{len(findings)}</strong></div>
      <div class="metric"><span>Desktop Size</span><strong>{DESKTOP_VIEWPORT["width"]}x{DESKTOP_VIEWPORT["height"]}</strong></div>
      <div class="metric"><span>Mobile Size</span><strong>{MOBILE_VIEWPORT["width"]}x{MOBILE_VIEWPORT["height"]}</strong></div>
    </section>
  </header>
  <main>
    <h2>Captured Screens</h2>
    <section class="screens">
      <div class="screen"><img src="screenshots/desktop.png" alt="Desktop homepage screenshot"></div>
      <div class="screen"><img src="screenshots/mobile.png" alt="Mobile homepage screenshot"></div>
    </section>
    <h2>Findings</h2>
    {''.join(finding_cards) if finding_cards else '<div class="empty">No major homepage UX/UI issues were detected by the automated checks.</div>'}
    <footer>
      <p>Generated by the standalone one-page audit tool. Review automated findings manually before making design decisions.</p>
    </footer>
  </main>
</body>
</html>"""
    (output_dir / "report.html").write_text(html_doc, encoding="utf-8")


async def run_audit(url: str, output_dir: Path) -> Dict[str, Any]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    (output_dir / "evidence").mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        desktop_context = await browser.new_context(viewport=DESKTOP_VIEWPORT, device_scale_factor=1)
        page = await desktop_context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await wait_ready(page)
        await dismiss_common_banners(page)
        await scroll_probe(page)
        await page.screenshot(path=output_dir / "screenshots" / "desktop.png", full_page=False)
        await page.screenshot(path=output_dir / "screenshots" / "desktop-full.png", full_page=True)
        desktop_signals = await extract_page_signals(page)
        await desktop_context.close()

        mobile_context = await browser.new_context(
            viewport=MOBILE_VIEWPORT,
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        mobile_page = await mobile_context.new_page()
        await mobile_page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await wait_ready(mobile_page)
        await dismiss_common_banners(mobile_page)
        await mobile_page.screenshot(path=output_dir / "screenshots" / "mobile.png", full_page=False)
        await mobile_page.screenshot(path=output_dir / "screenshots" / "mobile-full.png", full_page=True)
        mobile_signals = await extract_page_signals(mobile_page)
        await mobile_context.close()
        await browser.close()

    findings = analyze(desktop_signals, mobile_signals, output_dir)
    payload = {
        "url": url,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "overallScore": overall_score(findings),
        "desktopViewport": DESKTOP_VIEWPORT,
        "mobileViewport": MOBILE_VIEWPORT,
        "desktopSignals": desktop_signals,
        "mobileSignals": mobile_signals,
        "findings": [asdict(finding) for finding in findings],
    }
    (output_dir / "audit.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    render_report(url, desktop_signals, mobile_signals, findings, output_dir)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit only the home page of a website.")
    parser.add_argument("url", help="Website home page URL, for example https://example.com")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Output folder. Default: output/latest")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    url = normalize_url(args.url)
    output_dir = Path(args.out)
    payload = asyncio.run(run_audit(url, output_dir))
    print(f"Audit complete: {url}")
    print(f"Overall score: {payload['overallScore']}/10")
    print(f"Findings: {len(payload['findings'])}")
    print(f"Report: {(output_dir / 'report.html').resolve()}")
    print(f"JSON: {(output_dir / 'audit.json').resolve()}")


if __name__ == "__main__":
    main()
