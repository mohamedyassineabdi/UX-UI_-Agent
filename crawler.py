from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

DEFAULT_OUTPUT_FILE = "website_menu.json"

# ============================================================
# Output safety
# ============================================================


def force_utf8_output() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass

    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        try:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def print_json(data: dict) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=True) + "\n")


# ============================================================
# Config
# ============================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

DEFAULT_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
}

WEAK_AUTH_HINTS = {
    "sign in",
    "signin",
    "login",
    "log in",
    "sign up",
    "signup",
    "register",
    "create account",
}

WEAK_CTA_HINTS = {
    "get started",
    "start now",
    "contact sales",
    "book demo",
    "request demo",
    "talk to sales",
}

WEAK_UI_HINTS = {
    "menu",
    "toggle navigation menu",
    "open menu",
    "close menu",
    "search",
    "open search",
    "close search",
}

LIKELY_SIGNIN_PATHS = [
    "/login",
    "/signin",
    "/sign-in",
    "/account/login",
    "/auth/login",
    "/account",
]

LIKELY_SIGNUP_PATHS = [
    "/register",
    "/signup",
    "/sign-up",
    "/join",
    "/create-account",
]

NAV_CONTAINER_SELECTORS = [
    "header nav",
    "nav",
    '[role="navigation"]',
    "header",
    ".header",
    ".site-header",
    ".navbar",
    ".nav",
    ".menu",
    ".main-menu",
    ".topbar",
    ".navigation",
    ".site-nav",
    ".main-nav",
]

COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button#onetrust-accept-btn-handler",
    "button[aria-label*='accept' i]",
    "button[title*='accept' i]",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
    "button:has-text('Allow all')",
    "button:has-text('Tout accepter')",
    "button:has-text('Accepter')",
]

WHITELIST_EXTERNAL_SUBDOMAINS = (
    "docs.",
    "support.",
    "dashboard.",
)

BLOCKLIST_URL_PARTS_FOR_MENU = [
    "/privacy",
    "/cookie",
    "/legal",
    "/licenses",
    "/sitemap",
    "/terms",
]

# ============================================================
# Data classes
# ============================================================


@dataclass
class CrawlOptions:
    timeout: int
    debug: bool


@dataclass
class RobotsInfo:
    robots_url: str
    sitemap_url: Optional[str]
    allowed: bool


# ============================================================
# Helpers
# ============================================================

def weak_is_auth(name: str, url: Optional[str]) -> bool:
    n = normalize_menu_label(name).lower()
    u = (url or "").lower()

    if n in WEAK_AUTH_HINTS:
        return True

    if any(x in n for x in WEAK_AUTH_HINTS):
        return True

    return any(
        path in u
        for path in [
            "/login",
            "/signin",
            "/sign-in",
            "/register",
            "/signup",
            "/sign-up",
            "/create-account",
        ]
    )


def weak_is_cta(name: str, url: Optional[str]) -> bool:
    n = normalize_menu_label(name).lower()
    u = (url or "").lower()

    if n in WEAK_CTA_HINTS:
        return True

    if any(x in n for x in WEAK_CTA_HINTS):
        return True

    return any(
        path in u
        for path in [
            "/contact/sales",
            "/book-demo",
            "/request-demo",
        ]
    )
def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[DEBUG] {message}", file=sys.stderr)


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    path = parsed.path or "/"

    if not netloc and parsed.path:
        netloc = parsed.path
        path = "/"

    normalized = urlunparse((scheme, netloc, path, "", "", ""))
    return normalized.rstrip("/") if path == "/" else normalized

def force_english_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url

    parsed = urlparse(url)
    path = parsed.path or "/"

    # Remove leading locale segment like /de/, /fr/, /es/, /it/, etc.
    parts = path.split("/")

    if len(parts) > 1:
        first = parts[1].lower()

        # /de or /de/
        if re.fullmatch(r"[a-z]{2}", first):
            parts = [""] + parts[2:]
            path = "/".join(parts) or "/"

        # /de-de or /en-us
        elif re.fullmatch(r"[a-z]{2}-[a-z]{2}", first):
            parts = [""] + parts[2:]
            path = "/".join(parts) or "/"

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        path,
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))
def absolute_url(base_url: str, href: str) -> Optional[str]:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return urljoin(base_url, href)


def normalize_host(host: str) -> str:
    host = (host or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def same_domain(base_url: str, other_url: str) -> bool:
    a = normalize_host(urlparse(base_url).netloc)
    b = normalize_host(urlparse(other_url).netloc)

    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith("." + b) or b.endswith("." + a)


def allowed_external_for_nav(base_url: str, other_url: str) -> bool:
    if same_domain(base_url, other_url):
        return True
    host = normalize_host(urlparse(other_url).netloc)
    return host.startswith(WHITELIST_EXTERNAL_SUBDOMAINS)


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_menu_label(text: str) -> str:
    t = clean_text(text)
    if not t:
        return ""

    parts = t.split()
    if len(parts) % 2 == 0 and len(parts) >= 2:
        half = len(parts) // 2
        if [x.lower() for x in parts[:half]] == [x.lower() for x in parts[half:]]:
            t = " ".join(parts[:half])

    return t.strip()


def is_homepage_url(base_url: str, url: str) -> bool:
    if not url:
        return False
    b = urlparse(base_url)
    u = urlparse(url)
    return normalize_host(b.netloc) == normalize_host(u.netloc) and (u.path.rstrip("/") or "/") == "/"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def looks_like_bad_menu_url(url: Optional[str]) -> bool:
    u = (url or "").lower()
    return any(part in u for part in BLOCKLIST_URL_PARTS_FOR_MENU)


def dedupe_by_key(items: Iterable[Dict[str, Any]], key_fn) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def dedupe_links_prefer_shorter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_url: Dict[str, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []

    for item in items:
        url = (item.get("url") or "").rstrip("/")
        name = normalize_menu_label(item.get("name", ""))
        if not url:
            continue

        if url not in best_by_url:
            best_by_url[url] = item
            continue

        current = best_by_url[url]
        current_name = normalize_menu_label(current.get("name", ""))
        if len(name) < len(current_name):
            best_by_url[url] = item

    for item in best_by_url.values():
        extras.append(item)

    extras.sort(key=lambda x: (normalize_menu_label(x.get("name", "")).lower(), x.get("url", "")))
    return extras


def weak_is_ui_control(name: str) -> bool:
    n = normalize_menu_label(name).lower()
    return n in WEAK_UI_HINTS or n.startswith("toggle ") or n.endswith(" menu")

def classify_item_type(name: str, url: Optional[str], has_popup: bool, is_button_like: bool) -> str:
    if weak_is_cta(name, url):
        return "cta"

    if weak_is_auth(name, url):
        return "auth"

    if has_popup or is_button_like or not url:
        return "menu"

    return "link"

def score_top_candidate(item: Dict[str, Any]) -> int:
    score = 0

    if item.get("top_row"):
        score += 3
    if item.get("in_header"):
        score += 3
    if item.get("has_popup"):
        score += 4
    if item.get("is_button_like"):
        score += 2
    if item.get("visible"):
        score += 1
    if weak_is_ui_control(item.get("name", "")):
        score -= 5
    if is_homepage_url(item.get("base_url", ""), item.get("url") or ""):
        score -= 4

    return score


# ============================================================
# HTTP / robots
# ============================================================


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    debug: bool = False,
) -> Tuple[Optional[str], Optional[int]]:
    try:
        debug_log(debug, f"HTTP GET {url}")
        resp = await client.get(url, follow_redirects=True)
        return resp.text, resp.status_code
    except Exception as exc:
        debug_log(debug, f"HTTP GET failed for {url}: {exc}")
        return None, None


async def get_robots_info(
    client: httpx.AsyncClient,
    homepage: str,
    user_agent: str,
    debug: bool,
) -> RobotsInfo:
    parsed = urlparse(homepage)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    sitemap_url = None
    allowed = True

    text, status = await fetch_text(client, robots_url, debug=debug)
    if text and status == 200:
        rp = robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        allowed = rp.can_fetch(user_agent, homepage)
        for line in text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                break
    else:
        debug_log(debug, f"No robots.txt found or unreadable: {robots_url}")

    return RobotsInfo(
        robots_url=robots_url,
        sitemap_url=sitemap_url,
        allowed=allowed,
    )


async def guess_sitemap(
    client: httpx.AsyncClient,
    homepage: str,
    robots_info: RobotsInfo,
    debug: bool,
) -> Optional[str]:
    if robots_info.sitemap_url:
        return robots_info.sitemap_url

    parsed = urlparse(homepage)
    candidate = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    _, status = await fetch_text(client, candidate, debug=debug)
    if status == 200:
        return candidate
    return None


# ============================================================
# Playwright helpers
# ============================================================

async def get_page_language(page: Page) -> str:
    try:
        lang = await page.evaluate(
            "() => document.documentElement.lang || navigator.language || 'unknown'"
        )
        return clean_text(lang) or "unknown"
    except Exception:
        return "unknown"
async def wait_for_settle(page: Page, timeout_ms: int, debug: bool) -> None:
    debug_log(debug, "Waiting for DOM content loaded")
    await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

    try:
        debug_log(debug, "Waiting for network idle")
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
    except Exception:
        debug_log(debug, "Network idle wait skipped/timed out")

    await page.wait_for_timeout(800)


async def try_accept_cookies(page: Page, debug: bool) -> None:
    for selector in COOKIE_SELECTORS:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                await loc.click(timeout=1000)
                debug_log(debug, f"Cookie banner accepted with selector: {selector}")
                await page.wait_for_timeout(300)
                return
        except Exception:
            continue


async def get_page_html(page: Page) -> str:
    return await page.content()


async def get_page_metrics(page: Page) -> Dict[str, Any]:
    return await page.evaluate(
        """
        () => ({
          images: Array.from(document.images || []).length,
          forms: Array.from(document.querySelectorAll("form")).length,
          title: document.title || ""
        })
        """
    )


async def count_internal_external_links(page: Page, homepage: str) -> Tuple[int, int]:
    links = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll("a[href]")).map(a => a.href).filter(Boolean)
        """
    )
    internal = 0
    external = 0
    for href in links or []:
        if same_domain(homepage, href):
            internal += 1
        else:
            external += 1
    return internal, external


async def click_expandable_menu_buttons(page: Page, debug: bool) -> None:
    debug_log(debug, "Searching for expandable menu buttons")

    script = """
    (async () => {
      function visible(el) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0' &&
               r.width > 0 && r.height > 0;
      }

      function textOf(el) {
        return ((el.innerText || el.textContent || '') + ' ' +
                (el.getAttribute('aria-label') || '') + ' ' +
                (el.getAttribute('title') || '') + ' ' +
                (el.className || '')).replace(/\\s+/g, ' ').trim();
      }

      function visibleLinks() {
        return Array.from(document.querySelectorAll('a[href]')).filter(visible).length;
      }

      function score(el) {
        let s = 0;
        const txt = textOf(el).toLowerCase();
        const r = el.getBoundingClientRect();

        if (txt.includes('menu') || txt.includes('navigation') || txt.includes('hamburger')) s += 3;
        if (el.getAttribute('aria-expanded') !== null) s += 2;
        if (el.getAttribute('aria-haspopup') !== null) s += 1;
        if (r.top < 250) s += 1;
        return s;
      }

      const candidates = Array.from(document.querySelectorAll(
        'button, [role="button"], summary, .menu-toggle, .navbar-toggle, .hamburger, .drawer-toggle, [aria-label*="menu" i]'
      ))
        .filter(visible)
        .map(el => ({el, score: score(el), txt: textOf(el)}))
        .filter(x => x.score >= 3)
        .sort((a, b) => b.score - a.score)
        .slice(0, 6);

      for (const c of candidates) {
        const before = visibleLinks();
        try {
          c.el.click();
          await new Promise(r => setTimeout(r, 600));
          const after = visibleLinks();
          if (after <= before && c.el.getAttribute('aria-expanded') !== 'true') {
            try { c.el.click(); } catch (e) {}
          }
        } catch (e) {}
      }

      return true;
    })()
    """
    try:
        await page.evaluate(script)
        await page.wait_for_timeout(600)
    except Exception as exc:
        debug_log(debug, f"Expandable menu detection failed: {exc}")


# ============================================================
# Auth detection
# ============================================================


async def detect_auth_on_current_page(page: Page, current_url: str, debug: bool) -> Dict[str, Optional[Dict[str, str]]]:
    debug_log(debug, f"Detecting auth on page: {current_url}")

    script = """
    () => {
      function textOf(el) {
        return (
          (el.innerText || el.textContent || '') + ' ' +
          (el.getAttribute('aria-label') || '') + ' ' +
          (el.getAttribute('title') || '')
        ).replace(/\\s+/g, ' ').trim();
      }

      const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
        href: a.getAttribute('href') || '',
        text: textOf(a)
      }));

      const forms = Array.from(document.querySelectorAll('form')).map(f => ({
        text: textOf(f),
        hasPassword: !!f.querySelector('input[type="password"]'),
        hasEmail: !!f.querySelector('input[type="email"], input[name*="email" i], input[id*="email" i]')
      }));

      const pageText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 5000);
      return { links, forms, pageText };
    }
    """
    data = await page.evaluate(script)

    signin = None
    signup = None

    for item in data.get("links", []):
        text = normalize_menu_label(item.get("text", ""))
        href = item.get("href", "")
        abs_href = absolute_url(current_url, href)
        if not abs_href:
            continue

        lower = f"{text} {href}".lower()

        if any(k in abs_href.lower() for k in ["/search", "/cart", "/checkout", "/wishlist"]):
            continue

        if any(x in lower for x in ["sign in", "signin", "login", "log in"]):
            if not signin:
                signin = {"name": text or "Sign in", "url": abs_href}

        if any(x in lower for x in ["sign up", "signup", "register", "create account", "get started", "start now"]):
            if not signup:
                signup = {"name": text or "Sign up", "url": abs_href}

    page_text = (data.get("pageText", "") or "").lower()
    forms = data.get("forms", [])

    if not signin:
        if any(f.get("hasPassword") for f in forms) and "password" in page_text:
            signin = {"name": "Sign in", "url": current_url}

    if not signup:
        if any(f.get("hasEmail") for f in forms) and any(x in page_text for x in ["register", "sign up", "get started"]):
            signup = {"name": "Sign up", "url": current_url}

    return {"signin": signin, "signup": signup}


async def verify_auth_candidate(
    context: BrowserContext,
    homepage: str,
    path: str,
    timeout: int,
    debug: bool,
) -> Dict[str, Optional[Dict[str, str]]]:
    url = urljoin(homepage, path)
    page = None
    try:
        debug_log(debug, f"Verifying auth candidate: {url}")
        page = await context.new_page()
        await page.add_init_script("""
        Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)
        page.set_default_timeout(timeout * 1000)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        await wait_for_settle(page, timeout * 1000, debug)
        return await detect_auth_on_current_page(page, url, debug)
    except Exception as exc:
        debug_log(debug, f"Auth verification failed for {url}: {exc}")
        return {"signin": None, "signup": None}
    finally:
        if page:
            await page.close()


async def detect_auth(
    context: BrowserContext,
    page: Page,
    homepage: str,
    timeout: int,
    debug: bool,
) -> Dict[str, Optional[Dict[str, str]]]:
    result = await detect_auth_on_current_page(page, homepage, debug)

    if not result["signin"]:
        for path in LIKELY_SIGNIN_PATHS:
            checked = await verify_auth_candidate(context, homepage, path, timeout, debug)
            if checked["signin"]:
                result["signin"] = checked["signin"]
                break

    if not result["signup"]:
        for path in LIKELY_SIGNUP_PATHS:
            checked = await verify_auth_candidate(context, homepage, path, timeout, debug)
            if checked["signup"]:
                result["signup"] = checked["signup"]
                break

    return result


# ============================================================
# DOM / interaction extraction
# ============================================================


async def extract_navigation_candidates(page: Page, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Extracting navigation candidates")

    selectors_json = json.dumps(NAV_CONTAINER_SELECTORS)

    script = """
    (selectors) => {
      function visible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' &&
               style.visibility !== 'hidden' &&
               style.opacity !== '0' &&
               rect.width > 10 &&
               rect.height > 10;
      }

      function cleanText(s) {
        return (s || '').replace(/\\s+/g, ' ').trim();
      }

      function selectorish(el) {
        if (!el) return '';
        const tag = (el.tagName || '').toLowerCase();
        const id = el.id ? '#' + el.id : '';
        const classes = Array.from(el.classList || []).slice(0, 4).map(c => '.' + c).join('');
        return tag + id + classes;
      }

      function uniqBy(items, keyFn) {
        const seen = new Set();
        const out = [];
        for (const item of items) {
          const k = keyFn(item);
          if (seen.has(k)) continue;
          seen.add(k);
          out.push(item);
        }
        return out;
      }

      function collectRoots() {
        const roots = [document];
        const seen = new Set();

        function walk(node) {
          if (!node || seen.has(node)) return;
          seen.add(node);
          const elements = node.querySelectorAll ? node.querySelectorAll('*') : [];
          for (const el of elements) {
            if (el.shadowRoot) {
              roots.push(el.shadowRoot);
              walk(el.shadowRoot);
            }
          }
        }

        walk(document);
        return roots;
      }

      function queryAllAcrossRoots(selector) {
        const out = [];
        const roots = collectRoots();
        for (const root of roots) {
          try {
            out.push(...Array.from(root.querySelectorAll(selector)));
          } catch (e) {}
        }
        return out;
      }

      function scoreContainer(el, links, topLevelItems) {
        let score = 0;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const cls = (el.className || '').toLowerCase();
        const tag = (el.tagName || '').toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();

        if (links.length >= 2) score += 2;
        if (topLevelItems.length >= 3) score += 3;
        if (rect.top >= 0 && rect.top < 260) score += 2;
        if (style.display.includes('flex') || style.display.includes('grid')) score += 1;
        if (tag === 'nav' || tag === 'header') score += 2;
        if (role === 'navigation') score += 2;
        if (['nav', 'navbar', 'menu', 'header'].some(k => cls.includes(k))) score += 1;

        if (topLevelItems.length >= 3) {
          const rows = topLevelItems.map(x => x.top);
          const spread = Math.max(...rows) - Math.min(...rows);
          if (spread <= 40) score += 3;
        }

        return score;
      }

      function isFlatMenuItem(el, container) {
        let node = el.parentElement;
        let levels = 0;
        while (node && node !== container && levels < 5) {
          const tag = (node.tagName || '').toLowerCase();
          if (tag === 'li') return true;
          node = node.parentElement;
          levels++;
        }
        return false;
      }

      function getAnchorInfo(a, containerRect) {
        const r = a.getBoundingClientRect();
        const text = cleanText(a.innerText || a.textContent || '');
        const aria = cleanText(a.getAttribute('aria-label') || '');
        const title = cleanText(a.getAttribute('title') || '');
        const href = a.getAttribute('href') || '';
        const className = (a.className || '').toString();

        const imgCount = a.querySelectorAll('img').length;
        const svgCount = a.querySelectorAll('svg').length;
        const iconCount = a.querySelectorAll('svg, img, i').length;

        const firstLevelLike =
          r.top >= containerRect.top - 8 &&
          r.top <= containerRect.top + Math.min(160, containerRect.height + 50) &&
          r.height >= 18 &&
          r.width >= 18;

        const iconLike =
          text.length === 0 && (svgCount > 0 || imgCount > 0 || iconCount > 0);

        const logoLike =
          (imgCount > 0 || svgCount > 0) &&
          text.length <= 2 &&
          r.left < 260 &&
          r.top < 220;

        return {
          name: text,
          aria_label: aria,
          title: title,
          href: href,
          class_name: className,
          flat: isFlatMenuItem(a, a.closest('nav, header, [role="navigation"]') || a.parentElement),
          first_level_like: firstLevelLike,
          icon_like: iconLike,
          logo_like: logoLike,
          top: Math.round(r.top),
          left: Math.round(r.left),
          width: Math.round(r.width),
          height: Math.round(r.height)
        };
      }

      function getTopLevelItemInfo(el, containerRect) {
        const r = el.getBoundingClientRect();
        const text = cleanText(el.innerText || el.textContent || '');
        const aria = cleanText(el.getAttribute('aria-label') || '');
        const title = cleanText(el.getAttribute('title') || '');
        const href = el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : '';
        const name = text || aria || title;

        const isButtonLike =
          el.tagName.toLowerCase() === 'button' ||
          el.getAttribute('role') === 'button' ||
          el.getAttribute('aria-haspopup') !== null ||
          el.getAttribute('aria-expanded') !== null;

        const firstRow =
          r.top >= containerRect.top - 8 &&
          r.top <= containerRect.top + 120 &&
          r.height >= 18 &&
          r.width >= 18;

        return {
          name,
          href,
          aria_label: aria,
          title,
          top: Math.round(r.top),
          left: Math.round(r.left),
          width: Math.round(r.width),
          height: Math.round(r.height),
          first_level_like: firstRow,
          has_popup: el.getAttribute('aria-haspopup') !== null || el.getAttribute('aria-expanded') !== null,
          is_button_like: isButtonLike
        };
      }

      const rawContainers = uniqBy(
        queryAllAcrossRoots(selectors.join(',')),
        x => x
      ).filter(visible);

      const out = [];

      for (const el of rawContainers) {
        const rect = el.getBoundingClientRect();

        const anchors = Array.from(el.querySelectorAll('a[href]'))
          .filter(a => visible(a))
          .map(a => getAnchorInfo(a, rect))
          .filter(x => x.href);

        const uniqueAnchors = uniqBy(
          anchors,
          x => JSON.stringify([x.name, x.href, x.left, x.top])
        );

        if (uniqueAnchors.length < 1) continue;

        const topLevelItems = Array.from(el.querySelectorAll('a[href], button, [role="button"], summary'))
          .filter(node => visible(node))
          .map(node => getTopLevelItemInfo(node, rect))
          .filter(x => x.name || x.href)
          .filter(x => x.first_level_like)
          .filter(x => x.width > 18 && x.height > 18);

        const uniqueTopLevelItems = uniqBy(
          topLevelItems,
          x => JSON.stringify([x.name, x.href, x.left, x.top, x.has_popup, x.is_button_like])
        ).sort((a, b) => {
          if (a.top !== b.top) return a.top - b.top;
          return a.left - b.left;
        });

        out.push({
          container_selector: selectorish(el),
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
          },
          navbar_score: scoreContainer(el, uniqueAnchors, uniqueTopLevelItems),
          links: uniqueAnchors,
          top_level_items: uniqueTopLevelItems
        });
      }

      return out;
    }
    """

    return await page.evaluate(script, json.loads(selectors_json))


def build_top_level_items(base_url: str, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = candidate.get("top_level_items", []) or []
    rect = candidate.get("rect", {}) or {}
    selector = candidate.get("container_selector", "")

    out: List[Dict[str, Any]] = []
    for item in raw:
        name = normalize_menu_label(item.get("name", "") or item.get("aria_label", "") or item.get("title", ""))
        url = absolute_url(base_url, item.get("href", "")) if item.get("href") else None
        url = force_english_url(url)
        if not name:
            continue
        if weak_is_ui_control(name):
            continue
        if is_homepage_url(base_url, url or "") and name.lower() != "home":
            continue
        if not url and not item.get("has_popup") and not item.get("is_button_like"):
            continue
        if url and not allowed_external_for_nav(base_url, url):
            continue

        node = {
            "name": name,
            "url": url,
            "type": classify_item_type(
                name,
                url,
                bool(item.get("has_popup")),
                bool(item.get("is_button_like")),
            ),
            "children": [],
            "top_row": bool(item.get("first_level_like")),
            "in_header": safe_int(rect.get("y", 9999)) < 260 or "header" in selector.lower() or "nav" in selector.lower(),
            "has_popup": bool(item.get("has_popup")),
            "is_button_like": bool(item.get("is_button_like")),
            "visible": True,
            "base_url": base_url,
            "_top": safe_int(item.get("top", 9999)),
            "_left": safe_int(item.get("left", 9999)),
        }

        node["_score"] = score_top_candidate(node)
        out.append(node)

    out = [x for x in out if x["_score"] >= 2]
    out = dedupe_by_key(out, lambda x: (normalize_menu_label(x.get("name", "")).lower(), (x.get("url") or "").rstrip("/")))
    out.sort(key=lambda x: (x["_top"], x["_left"]))

    cleaned: List[Dict[str, Any]] = []
    for item in out:
        item.pop("_top", None)
        item.pop("_left", None)
        item.pop("_score", None)
        item.pop("top_row", None)
        item.pop("in_header", None)
        item.pop("has_popup", None)
        item.pop("is_button_like", None)
        item.pop("visible", None)
        item.pop("base_url", None)
        cleaned.append(item)

    return cleaned
def build_children_from_sections_and_links(
    sections: List[Dict[str, Any]],
    submenus: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []

    for section in sections:
        section_title = normalize_menu_label(section.get("title", "") or "General")
        section_links = []

        for u in section.get("urls", []) or []:
            name = normalize_menu_label(u.get("name", ""))
            url = u.get("url")
            description = clean_text(u.get("description", ""))

            if not name or not url:
                continue

            node = {
                "name": name,
                "type": "link",
                "url": url,
            }
            if description and description.lower() != name.lower():
                node["description"] = description

            section_links.append(node)

        if section_links:
            children.append({
                "name": section_title,
                "type": "section",
                "children": dedupe_links_prefer_shorter(section_links),
            })

    if submenus:
        loose_links = []
        for u in submenus:
            name = normalize_menu_label(u.get("name", ""))
            url = u.get("url")
            description = clean_text(u.get("description", ""))

            if not name or not url:
                continue

            node = {
                "name": name,
                "type": "link",
                "url": url,
            }
            if description and description.lower() != name.lower():
                node["description"] = description

            loose_links.append(node)

        if loose_links:
            children.append({
                "name": "General",
                "type": "section",
                "children": dedupe_links_prefer_shorter(loose_links),
            })

    return children
async def extract_submenus_from_top_nav(page: Page, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Extracting structured submenus from top navigation")

    script = r"""
    async () => {
      function visible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' &&
               style.visibility !== 'hidden' &&
               style.opacity !== '0' &&
               rect.width > 10 &&
               rect.height > 10;
      }

      function cleanText(s) {
        return (s || '').replace(/\s+/g, ' ').trim();
      }

      function uniqBy(items, keyFn) {
        const seen = new Set();
        const out = [];
        for (const item of items) {
          const k = keyFn(item);
          if (seen.has(k)) continue;
          seen.add(k);
          out.push(item);
        }
        return out;
      }

      function collectRoots() {
        const roots = [document];
        const seen = new Set();

        function walk(node) {
          if (!node || seen.has(node)) return;
          seen.add(node);
          const elements = node.querySelectorAll ? node.querySelectorAll('*') : [];
          for (const el of elements) {
            if (el.shadowRoot) {
              roots.push(el.shadowRoot);
              walk(el.shadowRoot);
            }
          }
        }

        walk(document);
        return roots;
      }

      function queryAllAcrossRoots(selector) {
        const out = [];
        for (const root of collectRoots()) {
          try {
            out.push(...Array.from(root.querySelectorAll(selector)));
          } catch (e) {}
        }
        return out;
      }

      function parseLinkTextFromElement(a) {
        function uniqueTexts(values) {
          const out = [];
          const seen = new Set();

          for (const value of values) {
            const text = cleanText(value);
            const key = text.toLowerCase();
            if (!text || seen.has(key)) continue;
            seen.add(key);
            out.push(text);
          }

          return out;
        }

        const fullText = cleanText(a.innerText || a.textContent || '');
        if (!fullText) {
          return { name: '', description: '' };
        }

        const childTexts = uniqueTexts(
          Array.from(a.querySelectorAll('span, div, p, strong, b, small, label'))
            .map(el => el.innerText || el.textContent || '')
        );

        if (childTexts.length >= 2) {
          const name = childTexts[0];
          const description = cleanText(childTexts.slice(1).join(' '));

          if (name && description && description.toLowerCase() !== name.toLowerCase()) {
            return { name, description };
          }
        }

        const lines = uniqueTexts(
          (a.innerText || a.textContent || '')
            .split(/\n+/)
            .map(x => cleanText(x))
            .filter(Boolean)
        );

        if (lines.length >= 2) {
          const name = lines[0];
          const description = cleanText(lines.slice(1).join(' '));

          if (name && description) {
            return { name, description };
          }
        }

        return {
          name: fullText,
          description: ''
        };
      }

      function getVisibleLinkElements() {
        return uniqBy(
          queryAllAcrossRoots('a[href]')
            .filter(a => visible(a))
            .map(a => {
              const r = a.getBoundingClientRect();
              const parsed = parseLinkTextFromElement(a);

              return {
                element: a,
                name: parsed.name,
                description: parsed.description,
                href: a.getAttribute('href') || '',
                top: Math.round(r.top),
                left: Math.round(r.left),
                width: Math.round(r.width),
                height: Math.round(r.height)
              };
            })
            .filter(x => x.href && x.name),
          x => JSON.stringify([x.name, x.href, x.left, x.top])
        );
      }

      function diffNewLinks(beforeLinks, afterLinks) {
        const beforeSet = new Set(
          beforeLinks.map(x => JSON.stringify([x.name, x.href, x.left, x.top]))
        );

        return afterLinks.filter(
          x => !beforeSet.has(JSON.stringify([x.name, x.href, x.left, x.top]))
        );
      }

      let popupCandidateCounter = 0;

      function getElementKey(el) {
        if (!el) return '';

        if (!el.dataset.menuCandidateId) {
          popupCandidateCounter += 1;
          el.dataset.menuCandidateId = 'menu-candidate-' + popupCandidateCounter;
        }

        return el.dataset.menuCandidateId;
      }

      function isReasonableContainer(el) {
        if (!el) return false;
        if (el === document.body || el === document.documentElement) return false;
        if (!visible(el)) return false;

        const r = el.getBoundingClientRect();

        if (r.width < 80 || r.height < 40) return false;

        if (r.width > window.innerWidth * 0.98 && r.height > window.innerHeight * 0.98) {
          return false;
        }

        return true;
      }

      function collectAncestorCandidates(el, maxDepth = 10) {
        const out = [];
        let node = el ? el.parentElement : null;
        let depth = 0;

        while (node && depth < maxDepth) {
          if (isReasonableContainer(node)) {
            out.push(node);
          }
          node = node.parentElement;
          depth += 1;
        }

        return out;
      }

      function scorePopupCandidate(container, triggerRect, newLinks) {
        const r = container.getBoundingClientRect();

        let containedLinks = 0;
        let minTop = Infinity;
        let maxTop = -Infinity;
        let minLeft = Infinity;
        let maxLeft = -Infinity;

        for (const link of newLinks) {
          const lr = link.element.getBoundingClientRect();

          const inside =
            lr.left >= r.left - 2 &&
            lr.right <= r.right + 2 &&
            lr.top >= r.top - 2 &&
            lr.bottom <= r.bottom + 2;

          if (inside) {
            containedLinks += 1;
            minTop = Math.min(minTop, lr.top);
            maxTop = Math.max(maxTop, lr.bottom);
            minLeft = Math.min(minLeft, lr.left);
            maxLeft = Math.max(maxLeft, lr.right);
          }
        }

        if (containedLinks === 0) {
          return null;
        }

        const distanceX = Math.abs(r.left - triggerRect.left);
        const distanceY = Math.abs(r.top - triggerRect.bottom);

        const belowTrigger = r.top >= triggerRect.top - 80;
        const nearTrigger = distanceX < 900 && distanceY < 700;

        const area = r.width * r.height;
        const viewportArea = window.innerWidth * window.innerHeight;

        const compactnessWidth =
          isFinite(minLeft) && isFinite(maxLeft) ? (maxLeft - minLeft) : r.width;
        const compactnessHeight =
          isFinite(minTop) && isFinite(maxTop) ? (maxTop - minTop) : r.height;
        const compactnessArea = compactnessWidth * compactnessHeight;

        let score = 0;

        score += containedLinks * 10;

        if (belowTrigger) score += 8;
        if (nearTrigger) score += 6;

        if (r.width >= 220) score += 4;
        if (r.height >= 120) score += 4;

        if (area < viewportArea * 0.85) score += 4;
        if (area > viewportArea * 0.92) score -= 20;

        if (compactnessArea > 0 && area / compactnessArea < 6) score += 4;

        if (r.top < 0) score -= 4;
        if (r.left < -20) score -= 4;

        const role = (container.getAttribute('role') || '').toLowerCase();
        const cls = (container.className || '').toString().toLowerCase();

        if (role === 'menu' || role === 'dialog') score += 6;

        if (
          cls.includes('menu') ||
          cls.includes('dropdown') ||
          cls.includes('popover') ||
          cls.includes('drawer') ||
          cls.includes('panel') ||
          cls.includes('nav')
        ) {
          score += 4;
        }

        return {
          element: container,
          rect: {
            top: r.top,
            left: r.left,
            width: r.width,
            height: r.height
          },
          containedLinks,
          score
        };
      }

      function findBestPopupRoot(triggerElement, beforeLinks, afterLinks) {
        const newLinks = diffNewLinks(beforeLinks, afterLinks);

        if (!newLinks.length) {
          return {
            popupRoot: null,
            newLinks: []
          };
        }

        const triggerRect = triggerElement.getBoundingClientRect();
        const candidateMap = new Map();

        for (const link of newLinks) {
          const ancestors = collectAncestorCandidates(link.element, 10);

          for (const ancestor of ancestors) {
            const key = getElementKey(ancestor);
            if (!candidateMap.has(key)) {
              candidateMap.set(key, ancestor);
            }
          }
        }

        const scored = [];

        for (const container of candidateMap.values()) {
          const result = scorePopupCandidate(container, triggerRect, newLinks);
          if (result) {
            scored.push(result);
          }
        }

        scored.sort((a, b) => {
          if (b.score !== a.score) return b.score - a.score;
          if (b.containedLinks !== a.containedLinks) return b.containedLinks - a.containedLinks;
          return (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height);
        });

        return {
          popupRoot: scored.length ? scored[0].element : null,
          newLinks
        };
      }

      function groupLinksIntoSectionsFromRoot(popupRoot, newLinks) {
        if (!popupRoot) return [];

        function isLikelyHeading(el) {
          if (!el || !visible(el)) return false;

          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const text = cleanText(el.innerText || el.textContent || '');

          if (!text) return false;
          if (text.length < 2 || text.length > 80) return false;
          if (rect.width < 20 || rect.height < 10) return false;

          const tag = (el.tagName || '').toLowerCase();
          if (['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b'].includes(tag)) {
            return true;
          }

          const fontWeight = parseInt(style.fontWeight || '400', 10);
          const fontSize = parseFloat(style.fontSize || '16');
          const className = (el.className || '').toString().toLowerCase();
          const fewLinksInside = el.querySelectorAll('a').length === 0;

          if ((fontWeight >= 500 || fontSize >= 15) && fewLinksInside) {
            return true;
          }

          if (
            className.includes('title') ||
            className.includes('heading') ||
            className.includes('header') ||
            className.includes('category') ||
            className.includes('section')
          ) {
            return true;
          }

          return false;
        }

        function getHeadingCandidates(root) {
          return Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6,strong,b,p,div,span'))
            .filter(el => isLikelyHeading(el))
            .map(el => {
              const r = el.getBoundingClientRect();
              return {
                element: el,
                text: cleanText(el.innerText || el.textContent || ''),
                left: Math.round(r.left),
                right: Math.round(r.right),
                top: Math.round(r.top),
                bottom: Math.round(r.bottom),
                width: Math.round(r.width),
                height: Math.round(r.height)
              };
            })
            .filter(x => x.text);
        }

        function chooseHeadingForColumn(col, headings) {
          if (!col.links.length) {
            return { title: '', confidence: 0 };
          }

          const colLeft = Math.min(...col.links.map(x => x.left));
          const colRight = Math.max(...col.links.map(x => x.left + x.width));
          const firstLinkTop = Math.min(...col.links.map(x => x.top));

          let best = null;

          for (const h of headings) {
            const overlapsHorizontally =
              h.right >= colLeft - 40 &&
              h.left <= colRight + 40;

            const aboveColumn =
              h.bottom <= firstLinkTop + 8 &&
              firstLinkTop - h.bottom <= 140;

            if (!overlapsHorizontally || !aboveColumn) {
              continue;
            }

            const verticalGap = Math.max(0, firstLinkTop - h.bottom);
            const colCenter = (colLeft + colRight) / 2;
            const headingCenter = (h.left + h.right) / 2;
            const horizontalOffset = Math.abs(headingCenter - colCenter);

            let score = 100;
            score -= verticalGap * 1.2;
            score -= horizontalOffset * 0.2;

            if (h.width <= (colRight - colLeft) + 100) score += 8;
            if (h.text.length <= 40) score += 5;
            if (h.text.length >= 2 && h.text.length <= 30) score += 4;

            const confidence = Math.max(0, Math.min(1, score / 100));

            if (!best || confidence > best.confidence) {
              best = {
                title: h.text,
                confidence
              };
            }
          }

          return best || { title: '', confidence: 0 };
        }

        const rootRect = popupRoot.getBoundingClientRect();

        const popupLinks = newLinks.filter(link => {
          const r = link.element.getBoundingClientRect();
          return (
            r.left >= rootRect.left - 2 &&
            r.right <= rootRect.right + 2 &&
            r.top >= rootRect.top - 2 &&
            r.bottom <= rootRect.bottom + 2
          );
        });

        if (popupLinks.length < 2) return [];

        const columns = [];
        const sorted = [...popupLinks].sort((a, b) => {
          if (a.left !== b.left) return a.left - b.left;
          return a.top - b.top;
        });

        for (const link of sorted) {
          let col = null;

          for (const existing of columns) {
            if (Math.abs(existing.x - link.left) < 140) {
              col = existing;
              break;
            }
          }

          if (!col) {
            col = { x: link.left, links: [] };
            columns.push(col);
          }

          col.links.push(link);
        }

        const normalizedColumns = columns
          .map(col => ({
            ...col,
            links: col.links.sort((a, b) => {
              if (a.top !== b.top) return a.top - b.top;
              return a.left - b.left;
            })
          }))
          .filter(col => col.links.length > 0)
          .sort((a, b) => a.x - b.x);

        if (!normalizedColumns.length) return [];

        const headings = getHeadingCandidates(popupRoot);

        const builtSections = normalizedColumns.map(col => {
          const heading = chooseHeadingForColumn(col, headings);

          const urls = uniqBy(
            col.links.map(x => {
              const node = {
                name: x.name,
                url: x.href
              };

              if (x.description && x.description.toLowerCase() !== x.name.toLowerCase()) {
                node.description = x.description;
              }

              return node;
            }),
            x => JSON.stringify([x.name, x.url])
          );

          return {
            title: heading.confidence >= 0.55 ? heading.title : 'General',
            headingConfidence: heading.confidence,
            urls
          };
        }).filter(section => section.urls.length > 0);

        if (!builtSections.length) return [];

        const totalLinks = builtSections.reduce((sum, s) => sum + s.urls.length, 0);
        const sectionCount = builtSections.length;
        const nonGeneralCount = builtSections.filter(s => s.title !== 'General').length;
        const tinySections = builtSections.filter(s => s.urls.length <= 1).length;

        const headingConfidenceAvg =
          builtSections.reduce((sum, s) => sum + (s.headingConfidence || 0), 0) / builtSections.length;

        const repeatedNonGeneralTitles =
          builtSections
            .filter(s => s.title !== 'General')
            .length !==
          new Set(
            builtSections
              .filter(s => s.title !== 'General')
              .map(s => cleanText(s.title).toLowerCase())
          ).size;

        if (totalLinks < 3) return [];
        if (sectionCount >= 3 && tinySections >= Math.ceil(sectionCount * 0.6)) return [];
        if (repeatedNonGeneralTitles) return [];
        if (sectionCount >= 3 && nonGeneralCount === 0) return [];
        if (sectionCount >= 3 && headingConfidenceAvg < 0.35 && nonGeneralCount <= 1) return [];
        if (sectionCount > 0 && totalLinks / sectionCount < 1.5) return [];

        return builtSections.map(section => ({
          title: section.title,
          urls: section.urls
        }));
      }

      function getTopCandidates() {
        const containers = uniqBy(
          queryAllAcrossRoots('header, nav, [role="navigation"], .header, .navbar, .nav, .menu, .topbar, .navigation, .site-nav, .main-nav'),
          x => x
        ).filter(visible);

        const items = [];

        for (const container of containers) {
          const cRect = container.getBoundingClientRect();

          const nodes = Array.from(container.querySelectorAll('a[href], button, [role="button"], summary'))
            .filter(el => visible(el))
            .map(el => {
              const r = el.getBoundingClientRect();
              const txt = cleanText(el.innerText || el.textContent || '');
              const aria = cleanText(el.getAttribute('aria-label') || '');
              const title = cleanText(el.getAttribute('title') || '');
              const href = el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : '';
              const name = txt || aria || title;
              const firstRow =
                r.top >= cRect.top - 8 &&
                r.top <= cRect.top + 120 &&
                r.height >= 18 &&
                r.width >= 18;

              return {
                name,
                href,
                top: Math.round(r.top),
                left: Math.round(r.left),
                width: Math.round(r.width),
                height: Math.round(r.height),
                has_popup: el.getAttribute('aria-haspopup') !== null || el.getAttribute('aria-expanded') !== null,
                is_button_like: el.tagName.toLowerCase() === 'button' || el.getAttribute('role') === 'button',
                element: el,
                first_row: firstRow
              };
            })
            .filter(x => x.first_row)
            .sort((a, b) => a.left - b.left);

          items.push(...nodes);
        }

        return uniqBy(items, x => JSON.stringify([x.name, x.href, x.left, x.top]));
      }

      function closeOpenUI() {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      }

      const results = [];
      const topItems = getTopCandidates();

      for (const item of topItems) {
        closeOpenUI();
        await new Promise(r => setTimeout(r, 250));

        const beforeLinks = getVisibleLinkElements();
        let interaction = null;
        let sections = [];
        let submenus = [];
        let popupRoot = null;
        let newLinks = [];

        try {
          item.element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
          item.element.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
          await new Promise(r => setTimeout(r, 450));

          let afterHover = getVisibleLinkElements();
          let popupResult = findBestPopupRoot(item.element, beforeLinks, afterHover);
          popupRoot = popupResult.popupRoot;
          newLinks = popupResult.newLinks;

          if (popupRoot && newLinks.length > 0) {
            interaction = 'hover';
          } else if (item.has_popup || item.is_button_like || !item.href) {
            try {
              item.element.click();
              await new Promise(r => setTimeout(r, 650));

              const afterClick = getVisibleLinkElements();
              popupResult = findBestPopupRoot(item.element, beforeLinks, afterClick);
              popupRoot = popupResult.popupRoot;
              newLinks = popupResult.newLinks;

              if (popupRoot && newLinks.length > 0) {
                interaction = 'click';
              }
            } catch (e) {}
          }

          if (popupRoot && newLinks.length > 0) {
            sections = groupLinksIntoSectionsFromRoot(popupRoot, newLinks);

            if (!sections.length) {
              submenus = uniqBy(
                newLinks.map(x => ({
                  name: x.name,
                  description: x.description || '',
                  href: x.href,
                  top: x.top,
                  left: x.left
                })),
                x => JSON.stringify([x.name, x.href])
              );
            }
          }

          results.push({
            name: item.name || '',
            href: item.href || '',
            interaction,
            sections,
            submenus
          });
        } catch (e) {
          results.push({
            name: item.name || '',
            href: item.href || '',
            interaction: null,
            sections: [],
            submenus: []
          });
        }
      }

      closeOpenUI();
      return results;
    }
    """

    raw = await page.evaluate(script)

    output: List[Dict[str, Any]] = []

    for item in raw or []:
        parent_name = normalize_menu_label(item.get("name", ""))
        parent_url = absolute_url(page.url, item.get("href", "")) if item.get("href") else None
        parent_url = force_english_url(parent_url)
        if not parent_name:
            continue
        if weak_is_ui_control(parent_name):
            continue

        sections: List[Dict[str, Any]] = []
        for section in item.get("sections", []) or []:
            title = normalize_menu_label(section.get("title", ""))
            urls: List[Dict[str, Any]] = []

            for u in section.get("urls", []) or []:
                name = normalize_menu_label(u.get("name", ""))
                url = absolute_url(page.url, u.get("url", "") or u.get("href", ""))
                url = force_english_url(url)
                description = clean_text(u.get("description", ""))

                if not name or not url:
                    continue
                if weak_is_ui_control(name):
                    continue
                if weak_is_auth(name, url) or weak_is_cta(name, url):
                    continue
                if looks_like_bad_menu_url(url):
                    continue
                if parent_url and url.rstrip("/") == parent_url.rstrip("/"):
                    continue
                if not allowed_external_for_nav(page.url, url):
                    continue

                node = {"name": name, "url": url}
                if description and description.lower() != name.lower():
                    node["description"] = description
                urls.append(node)

            urls = dedupe_links_prefer_shorter(urls)
            if urls:
                sections.append({
                    "title": title if title else "General",
                    "urls": urls
                })

        submenus: List[Dict[str, Any]] = []
        for u in item.get("submenus", []) or []:
            name = normalize_menu_label(u.get("name", ""))
            url = absolute_url(page.url, u.get("url", "") or u.get("href", ""))
            url = force_english_url(url)
            description = clean_text(u.get("description", ""))

            if not name or not url:
                continue
            if weak_is_ui_control(name):
                continue
            if weak_is_auth(name, url) or weak_is_cta(name, url):
                continue
            if looks_like_bad_menu_url(url):
                continue
            if parent_url and url.rstrip("/") == parent_url.rstrip("/"):
                continue
            if not allowed_external_for_nav(page.url, url):
                continue

            node = {"name": name, "url": url}
            if description and description.lower() != name.lower():
                node["description"] = description
            submenus.append(node)

        submenus = dedupe_links_prefer_shorter(submenus)

        children = build_children_from_sections_and_links(sections, submenus)

        output.append({
            "name": parent_name,
            "url": parent_url,
            "interaction": item.get("interaction"),
            "children": children,
        })

    return output


def dedupe_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(
        candidates,
        key=lambda c: (
            -float(c.get("navbar_score", 0)),
            -len(c.get("filtered_links", c.get("urls", []))),
            -(1 if "header" in (c.get("container_selector", "") or "").lower() else 0),
            -(1 if "nav" in (c.get("container_selector", "") or "").lower() else 0),
        )
    )

    kept: List[Dict[str, Any]] = []
    signatures: List[Tuple[str, ...]] = []

    for cand in ordered:
        links = cand.get("filtered_links", cand.get("urls", [])) or []
        urls = tuple(
            sorted(
                (item.get("url") or "").rstrip("/")
                for item in links
                if item.get("url")
            )
        )

        if not urls:
            continue

        duplicate = False
        for prev_urls in signatures:
            inter = len(set(urls) & set(prev_urls))
            union = len(set(urls) | set(prev_urls))
            if union and (inter / union) > 0.65:
                duplicate = True
                break

        if not duplicate:
            kept.append(cand)
            signatures.append(urls)

    return kept


def choose_best_navbars(base_url: str, raw_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prepared = []

    for cand in raw_candidates:
        selector = cand.get("container_selector", "")
        selector_lower = selector.lower()

        top_items = build_top_level_items(base_url, cand)
        if len(top_items) < 2:
            continue

        score = float(cand.get("navbar_score", 0))
        if "header" in selector_lower:
            score += 2
        if "nav" in selector_lower:
            score += 2
        if any(x in selector_lower for x in ["dialog", "popup", "popover", "drawer"]):
            score -= 3

        prepared.append({
            "container_selector": selector,
            "rect": cand.get("rect", {}),
            "navbar_score": round(score, 2),
            "urls": top_items,
        })

    prepared = sorted(
        dedupe_candidates([{
            "container_selector": x["container_selector"],
            "navbar_score": x["navbar_score"],
            "filtered_links": x["urls"],
            "rect": x["rect"],
        } for x in prepared]),
        key=lambda x: (-float(x.get("navbar_score", 0)), -len(x.get("filtered_links", [])))
    )

    output: List[Dict[str, Any]] = []
    for idx, cand in enumerate(prepared[:2], start=1):
        output.append({
            "id": idx,
            "name": "Main Navigation" if idx == 1 else "Secondary Navigation",
            "menu_count": len(cand.get("filtered_links", [])),
            "navbar_score": round(float(cand.get("navbar_score", 0)), 2),
            "container_selector": cand.get("container_selector", ""),
            "urls": cand.get("filtered_links", []),
        })

    return output


# ============================================================
# Main crawl
# ============================================================


async def crawl_single_view(
    context: BrowserContext,
    homepage: str,
    options: CrawlOptions,
    mobile: bool,
) -> Dict[str, Any]:
    page = None
    try:
        page = await context.new_page()

        if mobile:
            await page.set_viewport_size({"width": 390, "height": 844})
        else:
            await page.set_viewport_size({"width": 1440, "height": 1200})

        page.set_default_timeout(options.timeout * 1000)
        await page.goto(homepage, wait_until="domcontentloaded", timeout=options.timeout * 1000)
        await wait_for_settle(page, options.timeout * 1000, options.debug)

        await try_accept_cookies(page, options.debug)
        await click_expandable_menu_buttons(page, options.debug)
        page_language = await get_page_language(page)
        html = await get_page_html(page)
        page_metrics = await get_page_metrics(page)
        internal_links, external_links = await count_internal_external_links(page, homepage)

        raw_candidates = await extract_navigation_candidates(page, options.debug)
        navbars = choose_best_navbars(homepage, raw_candidates)
        submenu_data = await extract_submenus_from_top_nav(page, options.debug)

        for nav in navbars:
            nav["urls"] = merge_top_nav_with_submenus(nav["urls"], submenu_data)

        auth = await detect_auth(context, page, homepage, options.timeout, options.debug)

        soup = BeautifulSoup(html, "lxml")
        title = clean_text(page_metrics.get("title")) or clean_text(soup.title.string if soup.title else "")

        return {
            "auth": {
                "signin": auth.get("signin"),
                "signup": auth.get("signup"),
            },
            "navbars": navbars,
            "extra": {
                "forms_detected": safe_int(page_metrics.get("forms", 0)),
                "total_internal_links": internal_links,
                "external_links": external_links,
                "title": title,
                "page_size_kb": round(len(html.encode("utf-8")) / 1024, 2),
                "images_found": safe_int(page_metrics.get("images", 0)),
                "mode": "mobile" if mobile else "desktop",
                "page_language": page_language,
            },
        }
    finally:
        if page:
            await page.close()


def merge_top_nav_with_submenus(
    top_items: List[Dict[str, Any]],
    submenu_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []

    submenu_map: Dict[str, Dict[str, Any]] = {}
    for s in submenu_items:
        name = normalize_menu_label(s.get("name", "")).lower()
        if not name:
            continue
        submenu_map[name] = s

    for item in top_items:
        name = normalize_menu_label(item.get("name", ""))
        key = name.lower()

        children = item.get("children", []) or []

        if key in submenu_map:
            extracted = submenu_map[key]
            extracted_children = extracted.get("children", []) or []
            if extracted_children:
                children = extracted_children

        merged.append({
            "name": name,
            "url": item.get("url"),
            "type": item.get("type", "link"),
            "children": children,
        })

    seen = set()
    cleaned = []

    for item in merged:
        key = (
            normalize_menu_label(item.get("name", "")).lower(),
            (item.get("url") or "").rstrip("/"),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)

    return cleaned

def merge_children(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for item in (primary or []) + (secondary or []):
        name = normalize_menu_label(item.get("name", ""))
        item_type = item.get("type", "link")
        url = (item.get("url") or "").rstrip("/")
        url = url.rstrip("/")
        if not name:
            continue

        key = (name.lower(), item_type, url)

        if key not in by_key:
            by_key[key] = {
                "name": name,
                "type": item_type,
            }
            if item.get("url"):
                by_key[key]["url"] = item.get("url")
            if item.get("description"):
                by_key[key]["description"] = clean_text(item.get("description", ""))
            if item.get("children"):
                by_key[key]["children"] = item.get("children", [])
            continue

        existing = by_key[key]

        if not existing.get("url") and item.get("url"):
            existing["url"] = item.get("url")

        if not existing.get("description") and item.get("description"):
            existing["description"] = clean_text(item.get("description", ""))

        existing["children"] = merge_children(
            existing.get("children", []) or [],
            item.get("children", []) or [],
        )

    merged = list(by_key.values())
    merged.sort(key=lambda x: (x.get("type", ""), x.get("name", "").lower()))
    return merged
def choose_type(type_a: str, type_b: str) -> str:
    priority = {
        "menu": 4,
        "auth": 3,
        "cta": 2,
        "link": 1,
    }
    return type_a if priority.get(type_a, 0) >= priority.get(type_b, 0) else type_b

def merge_menu_lists(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for item in (primary or []) + (secondary or []):
        name = normalize_menu_label(item.get("name", ""))
        url = (item.get("url") or "").rstrip("/")
        item_type = item.get("type", "link")
        children = item.get("children", []) or []

        if not name:
            continue

        key = (name.lower(), url)

        if key not in by_key:
            by_key[key] = {
                "name": name,
                "url": item.get("url"),
                "type": item_type,
                "children": children,
            }
            continue

        existing = by_key[key]
        existing["type"] = choose_type(existing.get("type", "link"), item_type)

        if not existing.get("url") and item.get("url"):
            existing["url"] = item.get("url")

        existing["children"] = merge_children(
            existing.get("children", []) or [],
            children,
        )

    merged = list(by_key.values())
    merged.sort(key=lambda x: normalize_menu_label(x.get("name", "")).lower())
    return merged

def merge_nav_results(
    homepage: str,
    desktop_result: Dict[str, Any],
    mobile_result: Dict[str, Any],
) -> Dict[str, Any]:
    desktop_navbars = desktop_result.get("navbars", []) or []
    mobile_navbars = mobile_result.get("navbars", []) or []

    desktop_main = desktop_navbars[0]["urls"] if desktop_navbars else []
    mobile_main = mobile_navbars[0]["urls"] if mobile_navbars else []

    merged_main = merge_menu_lists(desktop_main, mobile_main)

    cleaned_main: List[Dict[str, Any]] = []
    for item in merged_main:
        name = normalize_menu_label(item.get("name", ""))
        url = item.get("url")
        item_type = item.get("type", "link")

        if not name:
            continue
        if weak_is_ui_control(name):
            continue
        if is_homepage_url(homepage, url or "") and name.lower() != "home":
            continue
        if item_type == "menu" and not item.get("children"):
            continue

        if item_type in {"auth", "cta", "link"}:
            item["children"] = []

        cleaned_main.append(item)

    final_main = []
    seen = set()

    for item in cleaned_main:
        key = (
            normalize_menu_label(item.get("name", "")).lower(),
            (item.get("url") or "").rstrip("/"),
        )
        if key in seen:
            continue
        seen.add(key)
        final_main.append(item)

    main_nav_score = 0.0
    if desktop_navbars:
        main_nav_score = max(main_nav_score, float(desktop_navbars[0].get("navbar_score", 0)))
    if mobile_navbars:
        main_nav_score = max(main_nav_score, float(mobile_navbars[0].get("navbar_score", 0)))

    signin = desktop_result.get("auth", {}).get("signin") or mobile_result.get("auth", {}).get("signin")
    signup = desktop_result.get("auth", {}).get("signup") or mobile_result.get("auth", {}).get("signup")


    return {
        "homepage": homepage,
        "language": (
            desktop_result.get("extra", {}).get("page_language")
            or mobile_result.get("extra", {}).get("page_language")
            or "unknown"
        ),
        "auth": {
            "signin": signin,
            "signup": signup,
        },
        "navigation": final_main,
        "extra": {
            "desktop": desktop_result.get("extra", {}),
            "mobile": mobile_result.get("extra", {}),
            "menu_count": len(final_main),
            "navbar_score": round(main_nav_score, 2),
            "source_container": "merged(desktop+mobile)",
        },
    }
    

async def crawl_site(homepage: str, options: CrawlOptions) -> Dict[str, Any]:
   
    start_time = time.perf_counter()
    homepage = normalize_url(homepage)
    homepage = force_english_url(homepage) or homepage
    parsed = urlparse(homepage)
    homepage = f"{parsed.scheme}://{parsed.netloc}/"
    user_agent = random.choice(USER_AGENTS)

    debug_log(options.debug, f"Normalized homepage: {homepage}")
    debug_log(options.debug, f"Selected User-Agent: {user_agent}")

    async with httpx.AsyncClient(
        timeout=options.timeout,
        headers={**DEFAULT_HEADERS, "User-Agent": user_agent},
    ) as client:
        robots_info = await get_robots_info(client, homepage, user_agent, options.debug)
        sitemap_url = await guess_sitemap(client, homepage, robots_info, options.debug)

        async with async_playwright() as pw:
            debug_log(options.debug, "Launching Chromium")
            browser: Browser = await pw.chromium.launch(headless=True)

            desktop_context = await browser.new_context(
            user_agent=user_agent,
            locale="en-US",
            extra_http_headers={
                **DEFAULT_HEADERS,
                "Accept-Language": "en-US,en;q=1",
            },
            viewport={"width": 1440, "height": 1200},
            java_script_enabled=True,
            is_mobile=False,
             )

            mobile_context = await browser.new_context(
            user_agent=user_agent,
            locale="en-US",
            extra_http_headers={
                **DEFAULT_HEADERS,
                "Accept-Language": "en-US,en;q=1",
            },
            viewport={"width": 390, "height": 844},
            java_script_enabled=True,
            is_mobile=True,
            has_touch=True,
            )

            try:
                desktop_result = await crawl_single_view(desktop_context, homepage, options, mobile=False)
                mobile_result = await crawl_single_view(mobile_context, homepage, options, mobile=True)

                merged = merge_nav_results(homepage, desktop_result, mobile_result)
                requested_language = "en-US"
                merged["requested_language"] = requested_language
                detected_language = merged.get("language", "unknown")

                if detected_language.lower() not in {"en", "en-us", "en-gb"}:
                    merged["language_warning"] = (
                        f"Requested English, but detected page language was {detected_language}."
                )
                merged["extra"].update({
                "sitemap_found": sitemap_url,
                "robots_txt": robots_info.robots_url,
                "crawl_time_ms": int((time.perf_counter() - start_time) * 1000),
                })
                return merged
            finally:
                await desktop_context.close()
                await mobile_context.close()
                await browser.close()


# ============================================================
# CLI
# ============================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract structured navigation menus and submenus from a website."
    )
    parser.add_argument("url", help="Website URL, example: https://example.com")
    parser.add_argument("--timeout", type=int, default=25, help="Per-page timeout in seconds")
    parser.add_argument("--debug", action="store_true", help="Show debug logs on stderr")
    parser.add_argument("--json-out", default="", help="Optional output path")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    options = CrawlOptions(
        timeout=max(5, args.timeout),
        debug=args.debug,
    )

    output_file = args.json_out or DEFAULT_OUTPUT_FILE

    try:
        result = await crawl_site(args.url, options)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print_json(result)
        print(f"\nSaved to {output_file}")

    except PlaywrightTimeoutError:
        error = {"error": "Failed to crawl: timeout"}
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(error, f, indent=2, ensure_ascii=False)
        print_json(error)

    except PlaywrightError as exc:
        error = {"error": f"Failed to crawl: browser error: {str(exc)}"}
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(error, f, indent=2, ensure_ascii=False)
        print_json(error)

    except Exception as exc:
        error = {"error": f"Failed to crawl: {str(exc)}"}
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(error, f, indent=2, ensure_ascii=False)
        print_json(error)


def main() -> None:
    force_utf8_output()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
