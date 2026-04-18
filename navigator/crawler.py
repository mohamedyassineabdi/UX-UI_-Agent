from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
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

try:
    from ai_navigation_helper import call_llama_navigation_detector
except ImportError:
    from navigator.ai_navigation_helper import call_llama_navigation_detector

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "shared" / "generated" / "website_menu.json"

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

OVERLAY_SELECTORS = [
    '[role="menu"]',
    '[role="dialog"]',
    '[aria-modal="true"]',
    '.hds-navigation-menu__positioner',
    '.hds-navigation-menu__popup',
    '.hds-navigation-menu__viewport',
    '.hds-navigation-menu__content',
    '.navigation__content',
    '.navigation__section',
    '.menu',
    '.dropdown',
    '.submenu',
    '.mega-menu',
    '.megamenu',
    '.drawer',
    '.popover',
    '.sub-menu',
    '.menu-dropdown',
    '.nav-dropdown',
    '.menu-drawer',
    '.mobile-menu',
    '.offcanvas',
    '.sidebar',
    '.HeaderMenu',
    '.header__submenu',
    '.header__dropdown',
]


@dataclass
class CrawlOptions:
    timeout: int
    debug: bool
    use_ai_nav: bool = False
    ai_debug_dir: str = "ai_nav_debug"


@dataclass
class RobotsInfo:
    robots_url: str
    sitemap_url: Optional[str]
    allowed: bool


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


def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[DEBUG] {message}", file=sys.stderr)


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
    parts = path.split("/")
    if len(parts) > 1:
        first = parts[1].lower()
        if re.fullmatch(r"[a-z]{2}", first):
            parts = [""] + parts[2:]
            path = "/".join(parts) or "/"
        elif re.fullmatch(r"[a-z]{2}-[a-z]{2}", first):
            parts = [""] + parts[2:]
            path = "/".join(parts) or "/"
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


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
    return any(path in u for path in ["/contact/sales", "/book-demo", "/request-demo"])


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

    extras.extend(best_by_url.values())
    extras.sort(key=lambda x: (normalize_menu_label(x.get("name", "")).lower(), x.get("url", "")))
    return extras


def merge_children(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for item in (primary or []) + (secondary or []):
        name = normalize_menu_label(item.get("name", ""))
        item_type = item.get("type", "link")
        url = (item.get("url") or "").rstrip("/")
        if not name:
            continue

        key = (name.lower(), item_type, url)
        if key not in by_key:
            by_key[key] = {"name": name, "type": item_type}
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
        existing["children"] = merge_children(existing.get("children", []) or [], item.get("children", []) or [])

    merged = list(by_key.values())
    merged.sort(key=lambda x: (x.get("type", ""), x.get("name", "").lower()))
    return merged


def choose_type(type_a: str, type_b: str) -> str:
    priority = {"menu": 4, "auth": 3, "cta": 2, "link": 1}
    return type_a if priority.get(type_a, 0) >= priority.get(type_b, 0) else type_b


def merge_menu_lists(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        existing["children"] = merge_children(existing.get("children", []) or [], children)

    merged = list(by_key.values())
    merged.sort(key=lambda x: normalize_menu_label(x.get("name", "")).lower())
    return merged


async def fetch_text(client: httpx.AsyncClient, url: str, debug: bool = False) -> Tuple[Optional[str], Optional[int]]:
    try:
        debug_log(debug, f"HTTP GET {url}")
        resp = await client.get(url, follow_redirects=True)
        return resp.text, resp.status_code
    except Exception as exc:
        debug_log(debug, f"HTTP GET failed for {url}: {exc}")
        return None, None


async def get_robots_info(client: httpx.AsyncClient, homepage: str, user_agent: str, debug: bool) -> RobotsInfo:
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

    return RobotsInfo(robots_url=robots_url, sitemap_url=sitemap_url, allowed=allowed)


async def guess_sitemap(client: httpx.AsyncClient, homepage: str, robots_info: RobotsInfo, debug: bool) -> Optional[str]:
    if robots_info.sitemap_url:
        return robots_info.sitemap_url
    parsed = urlparse(homepage)
    candidate = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    _, status = await fetch_text(client, candidate, debug=debug)
    if status == 200:
        return candidate
    return None


async def get_page_language(page: Page) -> str:
    try:
        lang = await page.evaluate("() => document.documentElement.lang || navigator.language || 'unknown'")
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
    await page.wait_for_timeout(700)


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
        const cls = String(el.className || '').toLowerCase();
        const r = el.getBoundingClientRect();
        const menuHint =
          txt.includes('menu') ||
          txt.includes('navigation') ||
          txt.includes('hamburger') ||
          txt.includes('open menu') ||
          txt.includes('close menu') ||
          cls.includes('menu-toggle') ||
          cls.includes('navbar-toggle') ||
          cls.includes('hamburger') ||
          cls.includes('drawer-toggle');
        const iconLike = r.width <= 72 && r.height <= 72;
        const looksLikeTopCategory =
          txt &&
          txt.split(/\\s+/).filter(Boolean).length <= 3 &&
          txt.length <= 32 &&
          !menuHint &&
          !iconLike;

        if (menuHint) s += 4;
        if (el.getAttribute('aria-expanded') !== null && (window.innerWidth < 768 || menuHint || iconLike)) s += 2;
        if (el.getAttribute('aria-haspopup') !== null && (window.innerWidth < 768 || menuHint || iconLike)) s += 1;
        if (iconLike) s += 1;
        if (window.innerWidth < 768 && r.top < 250) s += 1;
        if (looksLikeTopCategory) s -= 4;
        return s;
      }

      const candidates = Array.from(document.querySelectorAll(
        'button, [role="button"], summary, .menu-toggle, .navbar-toggle, .hamburger, .drawer-toggle, [aria-label*="menu" i]'
      ))
        .filter(visible)
        .map(el => ({el, score: score(el)}))
        .filter(x => x.score >= 4)
        .sort((a, b) => b.score - a.score)
        .slice(0, 6);

      for (const c of candidates) {
        const before = visibleLinks();
        try {
          c.el.click();
          await new Promise(r => setTimeout(r, 500));
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
        await page.wait_for_timeout(500)
    except Exception as exc:
        debug_log(debug, f"Expandable menu detection failed: {exc}")


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

    if not signin and any(f.get("hasPassword") for f in forms) and "password" in page_text:
        signin = {"name": "Sign in", "url": current_url}

    if not signup and any(f.get("hasEmail") for f in forms) and any(x in page_text for x in ["register", "sign up", "get started"]):
        signup = {"name": "Sign up", "url": current_url}

    return {"signin": signin, "signup": signup}


async def verify_auth_candidate(context: BrowserContext, homepage: str, path: str, timeout: int, debug: bool) -> Dict[str, Optional[Dict[str, str]]]:
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


async def detect_auth(context: BrowserContext, page: Page, homepage: str, timeout: int, debug: bool) -> Dict[str, Optional[Dict[str, str]]]:
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


async def detect_search_on_current_page(page: Page, homepage: str, debug: bool) -> Optional[Dict[str, Any]]:
    debug_log(debug, f"Detecting search on page: {homepage}")

    script = """
    () => {
      function textOf(el) {
        return (
          (el.innerText || el.textContent || '') + ' ' +
          (el.getAttribute('aria-label') || '') + ' ' +
          (el.getAttribute('title') || '') + ' ' +
          (el.getAttribute('placeholder') || '')
        ).replace(/\\s+/g, ' ').trim();
      }

      const input = Array.from(document.querySelectorAll('input, textarea')).find(el => {
        const t = (el.getAttribute('type') || '').toLowerCase();
        const txt = textOf(el).toLowerCase();
        return t === 'search' || txt.includes('search') || txt.includes('recherche') || txt.includes('chercher');
      });

      if (input) {
        return { kind: 'input', label: textOf(input) || 'Search', href: null };
      }

      const trigger = Array.from(document.querySelectorAll('a[href], button, [role="button"]')).find(el => {
        const txt = textOf(el).toLowerCase();
        const href = (el.getAttribute('href') || '').toLowerCase();
        return txt.includes('search') || txt.includes('recherche') || txt.includes('chercher') ||
               href.includes('/search') || href.includes('?q=');
      });

      if (trigger) {
        return {
          kind: 'trigger',
          label: textOf(trigger) || 'Search',
          href: trigger.getAttribute('href') || null
        };
      }

      return null;
    }
    """
    result = await page.evaluate(script)
    if not result:
        return None

    href = result.get("href")
    url = absolute_url(homepage, href) if href else None
    return {"name": normalize_menu_label(result.get("label", "") or "Search"), "url": url, "type": result.get("kind", "trigger")}


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

      function scoreContainer(el, topLevelItems) {
        let score = 0;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const cls = (el.className || '').toLowerCase();
        const tag = (el.tagName || '').toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();

        if (topLevelItems.length >= 3) score += 3;
        if (rect.top >= 0 && rect.top < 260) score += 2;
        if (style.display.includes('flex') || style.display.includes('grid')) score += 1;
        if (tag === 'nav' || tag === 'header') score += 2;
        if (role === 'navigation') score += 2;
        if (['nav', 'navbar', 'menu', 'header'].some(k => cls.includes(k))) score += 1;
        return score;
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
          r.top <= containerRect.top + 140 &&
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

        if (uniqueTopLevelItems.length < 1) continue;

        out.push({
          container_selector: selectorish(el),
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
          },
          navbar_score: scoreContainer(el, uniqueTopLevelItems),
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
        if url and looks_like_bad_menu_url(url):
            continue

        score = 0
        if item.get("first_level_like"):
            score += 3
        if safe_int(rect.get("y", 9999)) < 260 or "header" in selector.lower() or "nav" in selector.lower():
            score += 3
        if item.get("has_popup"):
            score += 4
        if item.get("is_button_like"):
            score += 2
        if weak_is_ui_control(name):
            score -= 5
        if is_homepage_url(base_url, url or ""):
            score -= 4

        node = {
            "name": name,
            "url": url,
            "type": classify_item_type(name, url, bool(item.get("has_popup")), bool(item.get("is_button_like"))),
            "children": [],
            "_score": score,
            "_top": safe_int(item.get("top", 9999)),
            "_left": safe_int(item.get("left", 9999)),
        }
        out.append(node)

    out = [x for x in out if x["_score"] >= 2]
    out = dedupe_by_key(out, lambda x: (normalize_menu_label(x.get("name", "")).lower(), (x.get("url") or "").rstrip("/")))
    out.sort(key=lambda x: (x["_top"], x["_left"]))

    cleaned: List[Dict[str, Any]] = []
    for item in out:
        item.pop("_score", None)
        item.pop("_top", None)
        item.pop("_left", None)
        cleaned.append(item)
    return cleaned


def dedupe_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        key = (
            item.get("container_selector", ""),
            tuple((x.get("name"), (x.get("url") or "").rstrip("/")) for x in item.get("filtered_links", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


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
        dedupe_candidates([
            {
                "container_selector": x["container_selector"],
                "navbar_score": x["navbar_score"],
                "filtered_links": x["urls"],
                "rect": x["rect"],
            }
            for x in prepared
        ]),
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


def build_children_from_sections_and_links(sections: List[Dict[str, Any]], submenus: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []
    section_urls = set()

    for section in sections:
        section_title = normalize_menu_label(section.get("title", "") or "General")
        section_links = []

        for u in section.get("urls", []) or []:
            name = normalize_menu_label(u.get("name", ""))
            url = u.get("url")
            description = clean_text(u.get("description", ""))
            if not name or not url:
                continue
            node = {"name": name, "type": "link", "url": url}
            if description and description.lower() != name.lower():
                node["description"] = description
            section_links.append(node)
            section_urls.add((url or "").rstrip("/"))

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
            if (url or "").rstrip("/") in section_urls:
                continue
            node = {"name": name, "type": "link", "url": url}
            if description and description.lower() != name.lower():
                node["description"] = description
            loose_links.append(node)

        if loose_links:
            children.append({
                "name": "General",
                "type": "section",
                "children": dedupe_links_prefer_shorter(loose_links),
            })

    titled_urls = {
        (child.get("url") or "").rstrip("/")
        for section in children
        if normalize_menu_label(section.get("name", "")) and normalize_menu_label(section.get("name", "")).lower() != "general"
        for child in (section.get("children") or [])
        if child.get("url")
    }

    if titled_urls:
        filtered_children = []
        for section in children:
            section_name = normalize_menu_label(section.get("name", ""))
            section_links = section.get("children") or []
            section_link_urls = {(child.get("url") or "").rstrip("/") for child in section_links if child.get("url")}
            if (
                section_name.lower() == "general"
                and section_link_urls
                and section_link_urls.issubset(titled_urls)
            ):
                continue
            filtered_children.append(section)
        children = filtered_children

    return children


def count_links_in_children(children: List[Dict[str, Any]]) -> int:
    total = 0
    for child in children or []:
        nested = child.get("children") or []
        if nested:
            total += count_links_in_children(nested)
        elif child.get("url"):
            total += 1
    return total


def overlay_signal_text(overlay: Dict[str, Any]) -> str:
    return clean_text(
        " ".join(
            [
                str(overlay.get("id", "") or ""),
                str(overlay.get("name", "") or ""),
                str(overlay.get("class_name", "") or ""),
                str(overlay.get("role", "") or ""),
            ]
        )
    ).lower()


def overlay_is_menu_like(overlay: Dict[str, Any]) -> bool:
    text = overlay_signal_text(overlay)
    link_count = count_links_in_children(overlay.get("children", []) or [])
    section_count = sum(
        1
        for child in (overlay.get("children", []) or [])
        if child.get("type") == "section" and (child.get("children") or [])
    )
    top = safe_int(overlay.get("top", 9999))
    height = safe_int(overlay.get("height", 0))

    keyword_match = any(
        token in text
        for token in ["navigation", "menu", "submenu", "dropdown", "popover", "popup", "positioner", "mega", "drawer"]
    )
    dialog_only = "dialog" in text and not keyword_match
    if dialog_only:
        return False

    if keyword_match:
        return True

    return top < 400 and height < 1400 and (section_count >= 3 or link_count >= 10)


def score_overlay_candidate(overlay: Dict[str, Any]) -> int:
    children = overlay.get("children", []) or []
    link_count = count_links_in_children(children)
    section_count = sum(
        1
        for child in children
        if child.get("type") == "section" and (child.get("children") or [])
    )
    if link_count == 0:
        return -999

    text = overlay_signal_text(overlay)
    height = safe_int(overlay.get("height", 0))
    menu_like = overlay_is_menu_like(overlay)

    if height > 1600 and not menu_like:
        return -999

    score = link_count + section_count * 4

    if menu_like:
        score += 8
    if safe_int(overlay.get("top", 9999)) < 260:
        score += 3
    if height > 1800:
        score -= 4
    if "dialog" in text and not menu_like:
        score -= 5

    return score


def select_relevant_overlay_children(
    overlays: List[Dict[str, Any]],
    controlled_id: Optional[str] = None,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    candidates = list(overlays or [])
    controlled_id = clean_text(controlled_id)

    if controlled_id:
        exact_matches = [overlay for overlay in candidates if clean_text(overlay.get("id", "")) == controlled_id]
        if exact_matches:
            candidates = exact_matches
        else:
            candidates = [overlay for overlay in candidates if overlay_is_menu_like(overlay)]
    else:
        menu_like_candidates = [overlay for overlay in candidates if overlay_is_menu_like(overlay)]
        if menu_like_candidates:
            candidates = menu_like_candidates

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for overlay in candidates:
        score = score_overlay_candidate(overlay)
        if score <= -999:
            continue
        scored.append((score, overlay))

    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score = scored[0][0]
    threshold = max(6, best_score - 3)

    selected = [overlay for score, overlay in scored if score >= threshold]
    if debug:
        debug_log(
            debug,
            "Overlay candidates: "
            + ", ".join(
                f"{clean_text(overlay.get('class_name') or overlay.get('name') or 'overlay')}={score}"
                for score, overlay in scored[:4]
            ),
        )

    merged: List[Dict[str, Any]] = []
    for overlay in selected:
        merged = merge_children(merged, overlay.get("children", []) or [])
    return merged


async def reset_page_for_nav_probe(page: Page, homepage: str, debug: bool) -> None:
    await page.goto(homepage, wait_until="domcontentloaded", timeout=25000)
    await wait_for_settle(page, 8000, debug)
    await try_accept_cookies(page, debug)
    try:
        viewport_width = await page.evaluate("() => window.innerWidth || 0")
    except Exception:
        viewport_width = 0
    if safe_int(viewport_width, 0) < 768:
        await click_expandable_menu_buttons(page, debug)
    await page.evaluate("() => window.scrollTo(0, 0)")
    await page.wait_for_timeout(250)


async def collect_top_nav_targets(page: Page, homepage: str, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Collecting top nav hover targets")

    script = """
    () => {
      function visible(el) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' &&
               s.visibility !== 'hidden' &&
               s.opacity !== '0' &&
               r.bottom > 0 &&
               r.right > 0 &&
               r.top < window.innerHeight &&
               r.left < window.innerWidth &&
               r.width > 10 &&
               r.height > 10;
      }

      function cleanText(s) {
        return (s || '').replace(/\\s+/g, ' ').trim();
      }

      const nodes = Array.from(document.querySelectorAll('header a, header button, nav a, nav button, [role="navigation"] a, [role="navigation"] button'))
        .filter(visible);

      const out = [];
      let count = 0;

      for (const el of nodes) {
        const rect = el.getBoundingClientRect();
        if (rect.top < -20 || rect.bottom < 10) continue;
        if (rect.top > 220) continue;

        const text = cleanText(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
        if (!text) continue;

        const href = el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : '';
        const candidateId = `topnav_${count++}`;
        el.setAttribute('data-top-nav-id', candidateId);

        out.push({
          nav_id: candidateId,
          name: text,
          href,
          role: el.getAttribute('role'),
          tag: el.tagName.toLowerCase(),
          top: Math.round(rect.top),
          left: Math.round(rect.left),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          has_popup: el.getAttribute('aria-haspopup') !== null || el.getAttribute('aria-expanded') !== null
        });
      }

      out.sort((a, b) => {
        if (a.top !== b.top) return a.top - b.top;
        return a.left - b.left;
      });

      return out;
    }
    """
    raw = await page.evaluate(script)

    cleaned = []
    for item in raw or []:
        name = normalize_menu_label(item.get("name", ""))
        href = item.get("href", "")
        url = absolute_url(homepage, href) if href else None
        url = force_english_url(url)

        if not name:
            continue
        if weak_is_ui_control(name):
            continue
        if url and looks_like_bad_menu_url(url):
            continue

        cleaned.append({
            "nav_id": item.get("nav_id"),
            "name": name,
            "url": url,
            "tag": item.get("tag"),
            "role": item.get("role"),
            "has_popup": bool(item.get("has_popup")),
            "top": safe_int(item.get("top", 9999)),
            "left": safe_int(item.get("left", 9999)),
        })

    cleaned = dedupe_by_key(cleaned, lambda x: (x["name"].lower(), (x.get("url") or "").rstrip("/")))
    cleaned.sort(key=lambda x: (x["top"], x["left"]))
    return cleaned[:20]


async def extract_visible_overlay_content(page: Page, homepage: str, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Extracting visible overlay content")

    script = f"""
    () => {{
      const overlaySelectors = {json.dumps(", ".join(OVERLAY_SELECTORS))};

      function visible(el, minWidth = 1, minHeight = 1) {{
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' &&
               style.visibility !== 'hidden' &&
               style.opacity !== '0' &&
               rect.width > minWidth &&
               rect.height > minHeight;
      }}

      function cleanText(s) {{
        return (s || '').replace(/\\s+/g, ' ').trim();
      }}

      function uniqBy(items, keyFn) {{
        const seen = new Set();
        const out = [];
        for (const item of items) {{
          const k = keyFn(item);
          if (seen.has(k)) continue;
          seen.add(k);
          out.push(item);
        }}
        return out;
      }}

      function absHref(href) {{
        try {{
          return new URL(href, window.location.href).href;
        }} catch (e) {{
          return null;
        }}
      }}

      function parseLinkTextFromElement(a) {{
        function uniqueTexts(values) {{
          const out = [];
          const seen = new Set();
          for (const value of values) {{
            const text = cleanText(value);
            const key = text.toLowerCase();
            if (!text || seen.has(key)) continue;
            seen.add(key);
            out.push(text);
          }}
          return out;
        }}

        const fullText = cleanText(a.innerText || a.textContent || '');
        if (!fullText) return {{ name: '', description: '' }};

        const childTexts = uniqueTexts(
          Array.from(a.querySelectorAll('span, div, p, strong, b, small, label'))
            .map(el => el.innerText || el.textContent || '')
        );

        if (childTexts.length >= 2) {{
          const name = childTexts[0];
          const description = cleanText(childTexts.slice(1).join(' '));
          if (name && description && description.toLowerCase() !== name.toLowerCase()) {{
            return {{ name, description }};
          }}
        }}

        const lines = uniqueTexts(
          (a.innerText || a.textContent || '')
            .split(/\\n+/)
            .map(x => cleanText(x))
            .filter(Boolean)
        );

        if (lines.length >= 2) {{
          const name = lines[0];
          const description = cleanText(lines.slice(1).join(' '));
          if (name && description) {{
            return {{ name, description }};
          }}
        }}

        return {{ name: fullText, description: '' }};
      }}

      const overlays = Array.from(document.querySelectorAll(overlaySelectors))
        .filter(el => visible(el, 120, 60));

      const result = [];

      for (const overlay of overlays.slice(0, 8)) {{
        const rect = overlay.getBoundingClientRect();
        const groups = Array.from(
          overlay.querySelectorAll(
            'nav, section, ul, .column, .group, .menu-group, .submenu-group, .mega-menu__column, .css-grid, .Grid, .grid, [class*="column"], [class*="section"]'
          )
        ).filter(el => visible(el, 20, 14));

        const sections = [];
        const looseLinks = [];

        if (groups.length > 0) {{
          for (const group of groups) {{
            const titleEl =
              group.querySelector('h1, h2, h3, h4, h5, h6, strong, .title, .heading, .menu-title, [class*="title"], [class*="heading"], [class*="label"]');
            const title = cleanText(titleEl ? (titleEl.innerText || titleEl.textContent || '') : '');

            const links = Array.from(group.querySelectorAll('a[href]'))
              .filter(a => visible(a, 8, 8))
              .map(a => {{
                const parsed = parseLinkTextFromElement(a);
                const href = absHref(a.getAttribute('href') || '');
                return {{
                  name: parsed.name,
                  description: parsed.description,
                  url: href
                }};
              }})
              .filter(x => x.name && x.url);

            if (links.length > 0) {{
              sections.push({{
                title: title || 'General',
                urls: uniqBy(links, x => JSON.stringify([x.name.toLowerCase(), x.url]))
              }});
            }}
          }}
        }}

        const directLinks = Array.from(overlay.querySelectorAll('a[href]'))
          .filter(a => visible(a, 8, 8))
          .map(a => {{
            const parsed = parseLinkTextFromElement(a);
            const href = absHref(a.getAttribute('href') || '');
            return {{
              name: parsed.name,
              description: parsed.description,
              url: href
            }};
          }})
          .filter(x => x.name && x.url);

        for (const link of directLinks) {{
          looseLinks.push(link);
        }}

        result.push({{
          id: overlay.id || '',
          name: cleanText(
            overlay.getAttribute('aria-label') ||
            overlay.getAttribute('title') ||
            overlay.className ||
            'Overlay Menu'
          ) || 'Overlay Menu',
          class_name: cleanText(String(overlay.className || '')),
          role: cleanText(overlay.getAttribute('role') || ''),
          top: Math.round(rect.top),
          left: Math.round(rect.left),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          extracted_sections: sections,
          extracted_links: uniqBy(looseLinks, x => JSON.stringify([x.name.toLowerCase(), x.url]))
        }});
      }}

      return result;
    }}
    """
    raw = await page.evaluate(script)

    structured = []
    for item in raw or []:
        children = build_children_from_sections_and_links(
            item.get("extracted_sections", []) or [],
            item.get("extracted_links", []) or [],
        )
        if children:
            structured.append({
                "id": clean_text(item.get("id", "")),
                "name": normalize_menu_label(item.get("name", "")) or "Overlay Menu",
                "class_name": clean_text(item.get("class_name", "")),
                "role": clean_text(item.get("role", "")),
                "top": safe_int(item.get("top", 9999)),
                "left": safe_int(item.get("left", 9999)),
                "width": safe_int(item.get("width", 0)),
                "height": safe_int(item.get("height", 0)),
                "children": children,
            })

    return structured


def likely_utility_name(name: str) -> bool:
    n = normalize_menu_label(name).lower()
    return (
        weak_is_auth(n, None)
        or weak_is_cta(n, None)
        or n in {"pricing", "contact", "contact sales", "search", "cart", "panier", "wishlist"}
    )


async def enrich_with_hover_mega_menus(page: Page, homepage: str, top_items: List[Dict[str, Any]], debug: bool) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []

    for item in top_items:
        name = normalize_menu_label(item.get("name", ""))
        key = name.lower()
        children = item.get("children", []) or []

        if likely_utility_name(name):
            enriched.append({
                "name": name,
                "url": item.get("url"),
                "type": item.get("type", "link"),
                "children": children,
            })
            continue

        try:
            await reset_page_for_nav_probe(page, homepage, debug)
            hover_targets = await collect_top_nav_targets(page, homepage, debug)
            target_map = {normalize_menu_label(t["name"]).lower(): t for t in hover_targets}
            target = target_map.get(key)
            if not target:
                enriched.append({
                    "name": name,
                    "url": item.get("url"),
                    "type": item.get("type", "link"),
                    "children": children,
                })
                continue

            locator = page.locator(f'[data-top-nav-id="{target["nav_id"]}"]').first
            if await locator.count() == 0:
                enriched.append({
                    "name": name,
                    "url": item.get("url"),
                    "type": item.get("type", "link"),
                    "children": children,
                })
                continue

            before_url = page.url
            activation = await page.evaluate(
                """
                (navId) => {
                  function visible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' &&
                           style.visibility !== 'hidden' &&
                           style.opacity !== '0' &&
                           rect.width > 0 &&
                           rect.height > 0;
                  }

                  const el = document.querySelector(`[data-top-nav-id="${navId}"]`);
                  if (!el || !visible(el)) {
                    return { found: false };
                  }
                  try {
                    el.focus({ preventScroll: true });
                  } catch (e) {
                    try { el.focus(); } catch (inner) {}
                  }

                  const tag = (el.tagName || '').toLowerCase();
                  const buttonLike =
                    tag === 'button' ||
                    el.getAttribute('role') === 'button' ||
                    el.getAttribute('aria-haspopup') !== null ||
                    el.getAttribute('aria-expanded') !== null;

                  if (buttonLike) {
                    try { el.click(); } catch (e) {}
                  } else {
                    function fire(type, ctorName) {
                      const Ctor = window[ctorName] || window.MouseEvent;
                      try {
                        el.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: true, view: window }));
                      } catch (e) {
                        try {
                          el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                        } catch (inner) {}
                      }
                    }
                    fire('pointerenter', 'PointerEvent');
                    fire('mouseenter', 'MouseEvent');
                    fire('mouseover', 'MouseEvent');
                    fire('pointerover', 'PointerEvent');
                    fire('mousemove', 'MouseEvent');
                  }
                  return {
                    found: true,
                    text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim(),
                    aria_expanded: el.getAttribute('aria-expanded') || '',
                    aria_controls: el.getAttribute('aria-controls') || ''
                  };
                }
                """,
                target["nav_id"],
            )
            await page.wait_for_timeout(650)

            overlays = await extract_visible_overlay_content(page, homepage, debug)

            if page.url != before_url:
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=4000)
                    await wait_for_settle(page, 6000, debug)
                except Exception:
                    pass
                enriched.append({
                    "name": name,
                    "url": item.get("url"),
                    "type": item.get("type", "menu" if children else item.get("type", "link")),
                    "children": children,
                })
                continue

            overlay_children = select_relevant_overlay_children(
                overlays,
                controlled_id=(activation or {}).get("aria_controls"),
                debug=debug,
            )

            if overlay_children:
                children = overlay_children

            enriched.append({
                "name": name,
                "url": item.get("url"),
                "type": "menu" if children else item.get("type", "link"),
                "children": children,
            })

            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(150)
            except Exception:
                pass

        except Exception as exc:
            debug_log(debug, f"Hover mega menu extraction failed for '{name}': {exc}")
            enriched.append({
                "name": name,
                "url": item.get("url"),
                "type": item.get("type", "link"),
                "children": children,
            })

    return enriched


def merge_top_nav_with_submenus(top_items: List[Dict[str, Any]], submenu_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        key = (normalize_menu_label(item.get("name", "")).lower(), (item.get("url") or "").rstrip("/"))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


def evaluate_nav_quality(navbars: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not navbars:
        return {"is_weak": True, "reason": "no_navbars", "menu_count": 0, "meaningful_count": 0, "has_children": False}

    main_urls = navbars[0].get("urls", []) or []
    meaningful = []

    weak_names = {
        "home", "contact", "cart", "panier", "basket", "wishlist",
        "search", "signin", "sign in", "login", "signup", "sign up",
    }

    has_children = any(item.get("children") for item in main_urls)

    for item in main_urls:
        name = normalize_menu_label(item.get("name", "")).lower()
        if not name:
            continue
        if name in weak_names:
            continue
        if weak_is_ui_control(name):
            continue
        meaningful.append(item)

    is_weak = False
    reason = "good"
    if len(main_urls) < 4:
        is_weak = True
        reason = "too_few_menu_items"
    elif len(meaningful) < 3:
        is_weak = True
        reason = "too_few_meaningful_items"
    elif not has_children:
        is_weak = True
        reason = "no_children_detected"

    return {
        "is_weak": is_weak,
        "reason": reason,
        "menu_count": len(main_urls),
        "meaningful_count": len(meaningful),
        "has_children": has_children,
    }


async def extract_clickable_candidates(page: Page, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Extracting clickable candidates for AI navigation recovery")

    script = """
    () => {
      function visible(el) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' &&
               s.visibility !== 'hidden' &&
               s.opacity !== '0' &&
               r.width > 8 &&
               r.height > 8;
      }

      const nodes = Array.from(document.querySelectorAll(
        'a[href], button, [role="button"], summary, input, [aria-label], [title]'
      ));

      const out = [];
      let count = 0;

      for (const el of nodes) {
        if (!visible(el)) continue;

        const r = el.getBoundingClientRect();
        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const aria = (el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
        const title = (el.getAttribute('title') || '').replace(/\\s+/g, ' ').trim();
        const href = el.getAttribute('href');
        const role = el.getAttribute('role');
        const tag = (el.tagName || '').toLowerCase();

        const iconLike =
          text.length === 0 &&
          (el.querySelector('svg, img, i') || ['svg', 'img', 'i'].includes(tag));

        const candidateId = `ai_candidate_${count++}`;
        el.setAttribute('data-ai-candidate-id', candidateId);

        out.push({
          candidate_id: candidateId,
          tag,
          role,
          text,
          aria_label: aria,
          title,
          href,
          top: Math.round(r.top),
          left: Math.round(r.left),
          width: Math.round(r.width),
          height: Math.round(r.height),
          near_top: r.top < 260,
          icon_like: !!iconLike
        });
      }

      return out.slice(0, 120);
    }
    """
    return await page.evaluate(script)


async def click_ai_candidate(page: Page, candidate_id: str, debug: bool) -> bool:
    if not candidate_id:
        return False
    try:
        loc = page.locator(f'[data-ai-candidate-id="{candidate_id}"]').first
        if await loc.count() == 0:
            return False
        await loc.click(timeout=2500)
        await page.wait_for_timeout(800)
        debug_log(debug, f"Clicked AI candidate: {candidate_id}")
        return True
    except Exception as exc:
        debug_log(debug, f"Failed clicking AI candidate {candidate_id}: {exc}")
        return False


async def get_ai_candidate_metadata(page: Page, candidate_id: str, debug: bool) -> Dict[str, Any]:
    if not candidate_id:
        return {}
    try:
        return await page.evaluate(
            """
            (candidateId) => {
              const el = document.querySelector(`[data-ai-candidate-id="${candidateId}"]`);
              if (!el) return {};

              const rect = el.getBoundingClientRect();
              return {
                candidate_id: candidateId,
                tag: (el.tagName || '').toLowerCase(),
                role: el.getAttribute('role') || '',
                text: ((el.innerText || el.textContent || '')).replace(/\\s+/g, ' ').trim(),
                aria_label: (el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim(),
                title: (el.getAttribute('title') || '').replace(/\\s+/g, ' ').trim(),
                href: el.getAttribute('href') || '',
                id: el.id || '',
                class_name: String(el.className || ''),
                aria_controls: el.getAttribute('aria-controls') || '',
                aria_expanded: el.getAttribute('aria-expanded') || '',
                top: Math.round(rect.top),
                left: Math.round(rect.left),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
              };
            }
            """,
            candidate_id,
        ) or {}
    except Exception as exc:
        debug_log(debug, f"Failed reading AI candidate metadata for {candidate_id}: {exc}")
        return {}


def flatten_menu_children_to_items(children: List[Dict[str, Any]], homepage: str) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []

    for child in children or []:
        nested_items = child.get("children") or []
        nodes = nested_items if child.get("type") == "section" else [child]

        for node in nodes:
            name = normalize_menu_label(node.get("name", ""))
            url = node.get("url")
            if not name or not url:
                continue
            if is_homepage_url(homepage, url):
                continue
            if weak_is_ui_control(name):
                continue
            if weak_is_auth(name, url) or weak_is_cta(name, url):
                continue
            if looks_like_bad_menu_url(url):
                continue
            if not allowed_external_for_nav(homepage, url):
                continue

            item = {"name": name, "url": url, "type": "link", "children": []}
            description = clean_text(node.get("description", ""))
            if description and description.lower() != name.lower():
                item["description"] = description
            flattened.append(item)

    return merge_menu_lists([], flattened)


async def extract_controlled_panel_navigation(
    page: Page,
    homepage: str,
    control_id: str,
    debug: bool,
) -> List[Dict[str, Any]]:
    control_id = clean_text(control_id)
    if not control_id:
        return []

    debug_log(debug, f"Extracting controlled menu panel navigation from #{control_id}")

    script = """
    (panelId) => {
      function visible(el, minWidth = 1, minHeight = 1) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' &&
               style.visibility !== 'hidden' &&
               style.opacity !== '0' &&
               rect.width > minWidth &&
               rect.height > minHeight;
      }

      function cleanText(s) {
        return (s || '').replace(/\\s+/g, ' ').trim();
      }

      function uniqBy(items, keyFn) {
        const seen = new Set();
        const out = [];
        for (const item of items) {
          const key = keyFn(item);
          if (seen.has(key)) continue;
          seen.add(key);
          out.push(item);
        }
        return out;
      }

      function absHref(href) {
        try {
          return new URL(href, window.location.href).href;
        } catch (e) {
          return null;
        }
      }

      function parseLinkTextFromElement(a) {
        const raw = cleanText(a.innerText || a.textContent || a.getAttribute('aria-label') || a.getAttribute('title') || '');
        if (!raw) return { name: '', description: '' };

        const lines = raw
          .split(/\\n+/)
          .map(cleanText)
          .filter(Boolean);

        if (lines.length >= 2) {
          return {
            name: lines[0],
            description: cleanText(lines.slice(1).join(' ')),
          };
        }

        return { name: raw, description: '' };
      }

      function extractLinks(root) {
        return uniqBy(
          Array.from(root.querySelectorAll('a[href]'))
            .filter(a => visible(a, 4, 4))
            .map(a => {
              const parsed = parseLinkTextFromElement(a);
              return {
                name: parsed.name,
                description: parsed.description,
                url: absHref(a.getAttribute('href') || '')
              };
            })
            .filter(x => x.name && x.url),
          x => JSON.stringify([x.name.toLowerCase(), x.url])
        );
      }

      const panel = document.getElementById(panelId);
      if (!panel || !visible(panel, 50, 20)) {
        return null;
      }

      const sections = [];
      const usedRoots = new Set();

      const headingNodes = Array.from(
        panel.querySelectorAll('h1, h2, h3, h4, h5, h6, strong, .title, .heading, [class*="title"], [class*="heading"], [class*="header"]')
      ).filter(el => visible(el, 20, 10));

      for (const heading of headingNodes) {
        const title = cleanText(heading.innerText || heading.textContent || '');
        if (!title) continue;

        let root = heading.parentElement;
        while (root && root !== panel) {
          const links = extractLinks(root);
          if (links.length > 0 && visible(root, 80, 20)) {
            break;
          }
          root = root.parentElement;
        }

        if (!root || usedRoots.has(root)) {
          continue;
        }

        const links = extractLinks(root);
        if (!links.length) {
          continue;
        }

        usedRoots.add(root);
        sections.push({ title, urls: links });
      }

      const looseLinks = extractLinks(panel);
      return {
        panel_id: panel.id || '',
        panel_class: String(panel.className || ''),
        sections,
        links: looseLinks
      };
    }
    """

    try:
        raw = await page.evaluate(script, control_id)
    except Exception as exc:
        debug_log(debug, f"Controlled menu extraction failed for #{control_id}: {exc}")
        return []

    if not raw:
        return []

    children = build_children_from_sections_and_links(
        raw.get("sections", []) or [],
        raw.get("links", []) or [],
    )
    flattened = flatten_menu_children_to_items(children, homepage)
    debug_log(debug, f"Controlled panel #{control_id} yielded {len(flattened)} navigation links")
    return flattened


async def extract_menu_toggle_candidates(page: Page, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Extracting visible menu-toggle candidates")

    script = """
    () => {
      function visible(el) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' &&
               s.visibility !== 'hidden' &&
               s.opacity !== '0' &&
               r.width > 8 &&
               r.height > 8;
      }

      function cleanText(s) {
        return (s || '').replace(/\\s+/g, ' ').trim();
      }

      const nodes = Array.from(document.querySelectorAll(
        'button[aria-controls], [role="button"][aria-controls], summary[aria-controls]'
      )).filter(visible);

      const out = [];
      let count = 0;

      for (const el of nodes) {
        const rect = el.getBoundingClientRect();
        const text = cleanText(el.innerText || el.textContent || '');
        const aria = cleanText(el.getAttribute('aria-label') || '');
        const title = cleanText(el.getAttribute('title') || '');
        const className = String(el.className || '');
        const controlId = cleanText(el.getAttribute('aria-controls') || '');
        const haystack = `${text} ${aria} ${title} ${className} ${controlId}`.toLowerCase();

        let score = 0;
        if (!controlId) continue;
        if (haystack.includes('menu')) score += 5;
        if (haystack.includes('navigation')) score += 4;
        if (text.toLowerCase() === 'menu') score += 3;
        if (rect.top < 420) score += 2;
        if (rect.width < 240) score += 1;

        if (score < 4) continue;

        const toggleId = `menu_toggle_${count++}`;
        el.setAttribute('data-menu-toggle-id', toggleId);

        out.push({
          toggle_id: toggleId,
          text,
          aria_label: aria,
          title,
          class_name: className,
          aria_controls: controlId,
          top: Math.round(rect.top),
          left: Math.round(rect.left),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          score
        });
      }

      out.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        if (a.top !== b.top) return a.top - b.top;
        return a.left - b.left;
      });
      return out.slice(0, 8);
    }
    """
    return await page.evaluate(script)


async def extract_visible_navigation_link_candidates(page: Page, homepage: str, debug: bool) -> List[Dict[str, Any]]:
    debug_log(debug, "Extracting visible navigation link candidates")

    script = """
    () => {
      function visible(el, minWidth = 1, minHeight = 1) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' &&
               s.visibility !== 'hidden' &&
               s.opacity !== '0' &&
               r.width > minWidth &&
               r.height > minHeight;
      }

      function cleanText(s) {
        return (s || '').replace(/\\s+/g, ' ').trim();
      }

      function uniqBy(items, keyFn) {
        const seen = new Set();
        const out = [];
        for (const item of items) {
          const key = keyFn(item);
          if (seen.has(key)) continue;
          seen.add(key);
          out.push(item);
        }
        return out;
      }

      const nodes = Array.from(document.querySelectorAll(
        'header a[href], nav a[href], [role="navigation"] a[href], .header a[href], .menu a[href], .nav a[href], .drawer a[href], .sidebar a[href]'
      ))
        .filter(a => visible(a, 4, 4))
        .map(a => {
          const rect = a.getBoundingClientRect();
          return {
            name: cleanText(a.innerText || a.textContent || a.getAttribute('aria-label') || a.getAttribute('title') || ''),
            href: a.getAttribute('href') || '',
            top: Math.round(rect.top),
            left: Math.round(rect.left),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          };
        })
        .filter(x => x.name && x.href)
        .filter(x => x.top > -120 && x.top < Math.max(window.innerHeight, 900));

      return uniqBy(nodes, x => JSON.stringify([x.name.toLowerCase(), x.href]));
    }
    """

    raw = await page.evaluate(script)
    items: List[Dict[str, Any]] = []
    for item in raw or []:
        name = normalize_menu_label(item.get("name", ""))
        url = absolute_url(homepage, item.get("href", "")) if item.get("href") else None
        url = force_english_url(url)
        if not name or not url:
            continue
        if weak_is_ui_control(name):
            continue
        if weak_is_auth(name, url) or weak_is_cta(name, url):
            continue
        if not allowed_external_for_nav(homepage, url):
            continue
        if looks_like_bad_menu_url(url):
            continue
        items.append({
            "name": name,
            "url": url,
            "type": "link",
            "children": [],
        })
    return merge_menu_lists([], items)


async def click_menu_toggle_candidate(page: Page, toggle_id: str, debug: bool) -> bool:
    if not toggle_id:
        return False
    try:
        clicked = await page.evaluate(
            """
            (toggleId) => {
              const el = document.querySelector(`[data-menu-toggle-id="${toggleId}"]`);
              if (!el) return false;
              try { el.focus({ preventScroll: true }); } catch (e) {
                try { el.focus(); } catch (inner) {}
              }
              try { el.click(); return true; } catch (e) { return false; }
            }
            """,
            toggle_id,
        )
        if clicked:
            await page.wait_for_timeout(700)
            debug_log(debug, f"Clicked heuristic menu toggle: {toggle_id}")
        return bool(clicked)
    except Exception as exc:
        debug_log(debug, f"Failed clicking heuristic menu toggle {toggle_id}: {exc}")
        return False


async def recover_navigation_from_menu_toggles(page: Page, homepage: str, debug: bool) -> List[Dict[str, Any]]:
    candidates = await extract_menu_toggle_candidates(page, debug)
    for candidate in candidates:
        control_id = clean_text(candidate.get("aria_controls", ""))
        if not control_id:
            continue
        clicked = await click_menu_toggle_candidate(page, candidate.get("toggle_id", ""), debug)
        if not clicked:
            continue
        items = await extract_controlled_panel_navigation(page, homepage, control_id, debug)
        if len(items) >= 3:
            return items
    return []


def build_navbars_from_recovered_items(
    existing_navbars: List[Dict[str, Any]],
    recovered_items: List[Dict[str, Any]],
    source_name: str,
) -> List[Dict[str, Any]]:
    if not recovered_items:
        return existing_navbars or []

    if existing_navbars:
        merged_urls = merge_menu_lists(existing_navbars[0].get("urls", []) or [], recovered_items)
        first = dict(existing_navbars[0])
        first["urls"] = merged_urls
        first["menu_count"] = len(merged_urls)
        first["navbar_score"] = max(float(first.get("navbar_score", 0)), 8.0)
        first["container_selector"] = source_name
        return [first] + list(existing_navbars[1:])

    return [
        {
            "id": 1,
            "name": "Main Navigation",
            "menu_count": len(recovered_items),
            "navbar_score": 8.0,
            "container_selector": source_name,
            "urls": recovered_items,
        }
    ]


def merge_navbar_url_candidates(navbars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for navbar in navbars or []:
        merged = merge_menu_lists(merged, navbar.get("urls", []) or [])
    return merged


def merge_ai_top_categories_into_items(
    items: List[Dict[str, Any]],
    ai_result: Dict[str, Any],
    fallback_items: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    ai_categories = [
        normalize_menu_label(x.get("name", ""))
        for x in (ai_result.get("top_categories") or [])
        if normalize_menu_label(x.get("name", ""))
    ]
    if not ai_categories:
        return items

    fallback_by_name: Dict[str, Dict[str, Any]] = {}
    for item in fallback_items or []:
        name = normalize_menu_label(item.get("name", ""))
        if not name:
            continue
        fallback_by_name[name.lower()] = item

    existing = {normalize_menu_label(x.get("name", "")).lower() for x in items}
    merged = list(items)

    for name in ai_categories:
        if name.lower() in existing:
            continue
        fallback = fallback_by_name.get(name.lower(), {})
        merged.append(
            {
                "name": name,
                "url": fallback.get("url"),
                "type": fallback.get("type", "menu"),
                "children": fallback.get("children", []) or [],
            }
        )

    return merged


async def run_navigation_pass(context: BrowserContext, page: Page, homepage: str, options: CrawlOptions) -> Dict[str, Any]:
    page_language = await get_page_language(page)
    html = await get_page_html(page)
    page_metrics = await get_page_metrics(page)
    internal_links, external_links = await count_internal_external_links(page, homepage)

    raw_candidates = await extract_navigation_candidates(page, options.debug)
    navbars = choose_best_navbars(homepage, raw_candidates)

    if not navbars:
        controlled_items = await recover_navigation_from_menu_toggles(page, homepage, options.debug)
        if controlled_items:
            navbars = build_navbars_from_recovered_items(
                [],
                controlled_items,
                "heuristic_controlled_panel",
            )

    for nav in navbars:
        nav["urls"] = await enrich_with_hover_mega_menus(page, homepage, nav["urls"], options.debug)

    auth = await detect_auth(context, page, homepage, options.timeout, options.debug)
    search = await detect_search_on_current_page(page, homepage, options.debug)

    soup = BeautifulSoup(html, "lxml")
    title = clean_text(page_metrics.get("title")) or clean_text(soup.title.string if soup.title else "")

    return {
        "auth": {"signin": auth.get("signin"), "signup": auth.get("signup")},
        "search": search,
        "navbars": navbars,
        "extra": {
            "forms_detected": safe_int(page_metrics.get("forms", 0)),
            "total_internal_links": internal_links,
            "external_links": external_links,
            "title": title,
            "page_size_kb": round(len(html.encode("utf-8")) / 1024, 2),
            "images_found": safe_int(page_metrics.get("images", 0)),
            "mode": "unknown",
            "page_language": page_language,
        },
    }


async def ai_navigation_recovery(
    context: BrowserContext,
    page: Page,
    homepage: str,
    options: CrawlOptions,
    mobile: bool,
    current_result: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ai_meta: Dict[str, Any] = {
        "used": False,
        "reason": None,
        "clicked_candidate": None,
        "ai_response": None,
        "improved": False,
        "error": None,
    }

    quality = evaluate_nav_quality(current_result.get("navbars", []))
    if not quality["is_weak"]:
        ai_meta["reason"] = "not_needed"
        return current_result, ai_meta

    if not options.use_ai_nav:
        ai_meta["reason"] = f"weak_but_disabled:{quality['reason']}"
        return current_result, ai_meta

    ai_meta["used"] = True
    ai_meta["reason"] = quality["reason"]

    try:
        os.makedirs(options.ai_debug_dir, exist_ok=True)
    except Exception:
        pass

    current_nav_items = []
    if current_result.get("navbars"):
        current_nav_items = current_result["navbars"][0].get("urls", []) or []

    candidates = await extract_clickable_candidates(page, options.debug)
    screenshot_bytes = await page.screenshot(full_page=False, type="png")

    try:
        ai_result = call_llama_navigation_detector(
            screenshot_bytes=screenshot_bytes,
            homepage=homepage,
            mobile=mobile,
            current_nav_items=current_nav_items,
            candidates=candidates,
        )
    except Exception as exc:
        ai_meta["error"] = str(exc)
        ai_meta["reason"] = f"ai_failed:{quality['reason']}"
        debug_log(options.debug, f"AI navigation recovery failed; continuing without it: {exc}")
        return current_result, ai_meta
    ai_meta["ai_response"] = ai_result

    if current_result.get("navbars"):
        fallback_nav_items = merge_navbar_url_candidates(current_result.get("navbars", []) or [])
        visible_link_fallbacks = await extract_visible_navigation_link_candidates(page, homepage, options.debug)
        fallback_nav_items = merge_menu_lists(fallback_nav_items, visible_link_fallbacks)
        current_result["navbars"][0]["urls"] = merge_ai_top_categories_into_items(
            current_result["navbars"][0]["urls"],
            ai_result,
            fallback_items=fallback_nav_items,
        )
        current_result["navbars"][0]["urls"] = await enrich_with_hover_mega_menus(
            page,
            homepage,
            current_result["navbars"][0]["urls"],
            options.debug,
        )

    trigger = (ai_result.get("menu_trigger") or {})
    candidate_id = trigger.get("candidate_id") if trigger.get("exists") else None
    candidate_meta = await get_ai_candidate_metadata(page, candidate_id, options.debug) if candidate_id else {}

    clicked = False
    interaction_style = (ai_result.get("interaction_style") or "unknown").lower()

    if candidate_id and (mobile or interaction_style in {"click", "mixed", "unknown"}):
        clicked = await click_ai_candidate(page, candidate_id, options.debug)

    if clicked:
        ai_meta["clicked_candidate"] = candidate_id
        await wait_for_settle(page, options.timeout * 1000, options.debug)
        rerun_result = await run_navigation_pass(context, page, homepage, options)
        rerun_result["extra"]["mode"] = "mobile" if mobile else "desktop"

        old_quality = evaluate_nav_quality(current_result.get("navbars", []))
        new_quality = evaluate_nav_quality(rerun_result.get("navbars", []))

        old_count = old_quality["meaningful_count"]
        new_count = new_quality["meaningful_count"]

        if new_count > old_count or (new_quality["has_children"] and not old_quality["has_children"]):
            ai_meta["improved"] = True
            return rerun_result, ai_meta

        controlled_items = await extract_controlled_panel_navigation(
            page,
            homepage,
            candidate_meta.get("aria_controls", ""),
            options.debug,
        )
        if controlled_items:
            recovered_from_panel = dict(rerun_result)
            recovered_from_panel["navbars"] = build_navbars_from_recovered_items(
                rerun_result.get("navbars", []) or [],
                controlled_items,
                f"ai_controlled_panel:{candidate_meta.get('aria_controls', '')}",
            )
            panel_quality = evaluate_nav_quality(recovered_from_panel.get("navbars", []))
            if (
                panel_quality["meaningful_count"] > new_quality["meaningful_count"]
                or (panel_quality["has_children"] and not new_quality["has_children"])
                or panel_quality["menu_count"] > new_quality["menu_count"]
            ):
                ai_meta["improved"] = True
                return recovered_from_panel, ai_meta

    ai_meta["clicked_candidate"] = candidate_id if clicked else None
    improved_quality = evaluate_nav_quality(current_result.get("navbars", []))
    if improved_quality["meaningful_count"] > quality["meaningful_count"] or (
        improved_quality["has_children"] and not quality["has_children"]
    ):
        ai_meta["improved"] = True

    return current_result, ai_meta


async def crawl_single_view(context: BrowserContext, homepage: str, options: CrawlOptions, mobile: bool) -> Dict[str, Any]:
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
        if mobile:
            await click_expandable_menu_buttons(page, options.debug)

        result = await run_navigation_pass(context, page, homepage, options)
        result["extra"]["mode"] = "mobile" if mobile else "desktop"

        recovered_result, ai_meta = await ai_navigation_recovery(
            context=context,
            page=page,
            homepage=homepage,
            options=options,
            mobile=mobile,
            current_result=result,
        )

        recovered_result["extra"]["mode"] = "mobile" if mobile else "desktop"
        recovered_result["extra"]["ai_recovery"] = ai_meta
        return recovered_result
    finally:
        if page:
            await page.close()


def merge_nav_results(homepage: str, desktop_result: Dict[str, Any], mobile_result: Dict[str, Any]) -> Dict[str, Any]:
    desktop_navbars = desktop_result.get("navbars", []) or []
    mobile_navbars = mobile_result.get("navbars", []) or []

    desktop_main = merge_navbar_url_candidates(desktop_navbars)
    mobile_main = merge_navbar_url_candidates(mobile_navbars)

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
        if item_type in {"auth", "cta", "link"}:
            item["children"] = []
        cleaned_main.append(item)

    final_main = []
    seen = set()
    for item in cleaned_main:
        key = (normalize_menu_label(item.get("name", "")).lower(), (item.get("url") or "").rstrip("/"))
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
    search = desktop_result.get("search") or mobile_result.get("search")

    return {
        "homepage": homepage,
        "language": (
            desktop_result.get("extra", {}).get("page_language")
            or mobile_result.get("extra", {}).get("page_language")
            or "unknown"
        ),
        "auth": {"signin": signin, "signup": signup},
        "search": search,
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
                extra_http_headers={**DEFAULT_HEADERS, "Accept-Language": "en-US,en;q=1"},
                viewport={"width": 1440, "height": 1200},
                java_script_enabled=True,
                is_mobile=False,
            )

            mobile_context = await browser.new_context(
                user_agent=user_agent,
                locale="en-US",
                extra_http_headers={**DEFAULT_HEADERS, "Accept-Language": "en-US,en;q=1"},
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
                    merged["language_warning"] = f"Requested English, but detected page language was {detected_language}."

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract structured navigation menus and submenus from a website.")
    parser.add_argument("url", help="Website URL, example: https://example.com")
    parser.add_argument("--timeout", type=int, default=25, help="Per-page timeout in seconds")
    parser.add_argument("--debug", action="store_true", help="Show debug logs on stderr")
    parser.add_argument(
        "--json-out",
        default=str(DEFAULT_OUTPUT_FILE),
        help="Optional output path",
    )
    parser.add_argument("--use-ai-nav", action="store_true", help="Enable LLaMA/Ollama AI fallback for hidden or weak navigation")
    parser.add_argument("--ai-debug-dir", default="ai_nav_debug", help="Directory for AI debug artifacts")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    options = CrawlOptions(
        timeout=max(5, args.timeout),
        debug=args.debug,
        use_ai_nav=args.use_ai_nav,
        ai_debug_dir=args.ai_debug_dir,
    )

    output_file = Path(args.json_out) if args.json_out else DEFAULT_OUTPUT_FILE
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = await crawl_site(args.url, options)

        with output_file.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print_json(result)
        print(f"\\nSaved to {output_file}")

    except PlaywrightTimeoutError:
        error = {"error": "Failed to crawl: timeout"}
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(error, f, indent=2, ensure_ascii=False)
        print_json(error)
        raise SystemExit(1)

    except PlaywrightError as exc:
        error = {"error": f"Failed to crawl: browser error: {str(exc)}"}
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(error, f, indent=2, ensure_ascii=False)
        print_json(error)
        raise SystemExit(1)

    except Exception as exc:
        error = {"error": f"Failed to crawl: {str(exc)}"}
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(error, f, indent=2, ensure_ascii=False)
        print_json(error)
        raise SystemExit(1)


def main() -> None:
    force_utf8_output()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
