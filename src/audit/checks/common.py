from __future__ import annotations

import json
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

TRUE = "TRUE"
FALSE = "FALSE"
NA = "N/A"

SITE_BRAND_STOPWORDS = {
    "home", "catalog", "contact", "panier", "shop", "store", "boutique",
    "search", "cart", "checkout", "3afsa", "3afsatunisia", "3afsatunisie",
}

SYSTEM_HEADINGS = {
    "article ajoute au panier",
    "article ajouté au panier",
    "subscribe to our emails",
    "you may also like",
    "related products",
}

TOOLTIP_TOKENS = {"?", "help", "aide", "info", "information", "more info", "en savoir plus"}

NOISE_PATTERNS = (
    "commerce electronique propulse par shopify",
    "commerce électronique propulsé par shopify",
    "powered by shopify",
    "return_to",
    "form_type",
    "utf8",
)

COUNTRY_HINTS = {
    "afghanistan", "albanie", "algerie", "algérie", "allemagne", "andorre", "angola",
    "arabie saoudite", "argentine", "australie", "autriche", "bahrein", "belgique",
    "bresil", "brésil", "burkina faso", "canada", "comores", "congo", "coree",
    "corée", "costa rica", "cote d'ivoire", "cote d’ivoire", "egypte", "émirats",
    "espagne", "etats-unis", "états-unis", "france", "georgie", "géorgie", "maroc",
    "tunisie", "antigua-et-barbuda",
}

UNITS_AND_SAFE_SHORT_TOKENS = {
    "ml", "cl", "cm", "mm", "kg", "g", "l", "xl", "xxl", "m", "s", "dt", "tnd",
    "usd", "eur", "mad", "dhs", "pcs",
}

SAFE_UPPER_TOKENS = {
    "TND", "USD", "EUR", "DT", "ML", "CM", "KG", "XL", "XXL",
}

FORM_FIELD_NOISE_TYPES = {"hidden", "submit", "reset", "button", "image"}
CHOICE_FIELD_TYPES = {"checkbox", "radio", "select"}
FORMAT_SENSITIVE_FIELD_TYPES = {"email", "tel", "number", "date", "datetime-local", "month", "password", "url"}
REQUIRED_MARKERS = {"required", "obligatoire", "mandatory"}
GUIDANCE_MARKERS = {"example", "exemple", "format", "yyyy", "dd/mm", "mm/yy", "e.g", "ex."}
LOCALIZATION_TOKENS = {"country", "currency", "locale", "region", "pays", "région", "localization"}
SEARCH_FORM_TOKENS = {"search", "recherche"}
FILTER_FORM_TOKENS = {"filter", "facet", "sort", "tri", "availability", "price", "prix"}


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def normalize_text(text: Any) -> str:
    text = "" if text is None else str(text)
    text = strip_accents(text).lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_text(text: Any) -> str:
    text = "" if text is None else str(text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'’\-_/.]*", clean_text(text))
        if token
    ]


def unique_preserve_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        cleaned = clean_text(item)
        key = normalize_text(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def normalize_evidence_items(evidence: Any) -> List[str]:
    if evidence in (None, ""):
        return []

    if isinstance(evidence, str):
        items = [evidence]
    else:
        try:
            items = list(evidence)
        except Exception:
            items = [str(evidence)]

    out: List[str] = []
    seen = set()
    for item in items:
        cleaned = clean_text(item)
        if not cleaned:
            continue
        key = normalize_text(cleaned)
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def safe_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def looks_like_locale_picker(text: str) -> bool:
    raw = clean_text(text)
    norm = normalize_text(raw)
    if not norm:
        return False
    if "د.ت" in raw or "| tnd" in norm or " tnd " in f" {norm} ":
        return True
    if "localization" in norm or "country_filter" in norm or "country code" in norm:
        return True
    return any(country in norm for country in COUNTRY_HINTS)


def looks_like_measurement_or_variant(text: str) -> bool:
    norm = normalize_text(text)
    if not norm:
        return False
    if re.search(r"\b\d+(?:[\.,]\d+)?\s?(?:ml|cm|mm|kg|g|l)\b", norm):
        return True
    if re.search(r"\b\d+(?:[\.,]\d+)?\b", norm) and len(tokenize(text)) <= 3:
        return True
    return False


def looks_like_system_or_noise(text: str) -> bool:
    raw = clean_text(text)
    norm = normalize_text(raw)
    if not norm:
        return True
    if looks_like_locale_picker(raw):
        return True
    if norm in {"3afsa", "1"}:
        return True
    if norm in SYSTEM_HEADINGS:
        return True
    if any(pattern in norm for pattern in NOISE_PATTERNS):
        return True
    return False


def looks_like_marketing_banner(text: str) -> bool:
    norm = normalize_text(text)
    return any(
        key in norm
        for key in (
            "bienvenue",
            "decouvrez notre selection",
            "découvrez notre sélection",
            "craquez pour",
            "apportez une touche",
            "preparez-vous",
            "préparez-vous",
        )
    )


def is_meaningful_heading(text: str) -> bool:
    txt = clean_text(text)
    norm = normalize_text(txt)
    if not txt or looks_like_system_or_noise(txt):
        return False
    if len(tokenize(txt)) < 1:
        return False
    if norm in {"subscribe to our emails", "article ajoute au panier"}:
        return False
    return True


def is_probably_real_nav_label(text: str) -> bool:
    txt = clean_text(text)
    norm = normalize_text(txt)
    if not txt or looks_like_system_or_noise(txt):
        return False
    if len(tokenize(txt)) == 1 and norm in SITE_BRAND_STOPWORDS:
        return False
    if norm in {"facebook", "instagram", "tiktok"}:
        return False
    if "shopify" in norm:
        return False
    return True


def is_user_facing_label(text: str) -> bool:
    txt = clean_text(text)
    norm = normalize_text(txt)
    if not txt or looks_like_system_or_noise(txt):
        return False
    if norm in {"facebook", "instagram", "tiktok"}:
        return False
    if "shopify" in norm:
        return False
    return True


def page_title_core(title: str) -> str:
    title = clean_text(title)
    if not title:
        return ""
    parts = [clean_text(p) for p in re.split(r"[|\-–—]+", title) if clean_text(p)]
    candidates: List[str] = []
    for part in parts or [title]:
        norm = normalize_text(part)
        if norm in SITE_BRAND_STOPWORDS:
            continue
        if norm in {"3afsa", "3afsatunisie", "3afsatunisia"}:
            continue
        candidates.append(part)
    if not candidates:
        candidates = parts or [title]
    candidates.sort(key=lambda item: (len(tokenize(item)), len(item)), reverse=True)
    return normalize_text(candidates[0])


def comparable_label(text: str) -> str:
    norm = page_title_core(text)
    norm = re.sub(r"\b(votre|notre|the|la|le|les|des|de|du|d')\b", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def average(values: Iterable[float]) -> float:
    values = [float(v) for v in values]
    return statistics.mean(values) if values else 0.0


def median(values: Iterable[float]) -> float:
    values = [float(v) for v in values]
    return statistics.median(values) if values else 0.0


def percent(condition_count: int, total: int) -> float:
    return (condition_count / total) if total else 0.0


def average_words_per_paragraph(paragraphs: Iterable[str]) -> float:
    counts = [len(tokenize(p)) for p in paragraphs if clean_text(p)]
    return statistics.mean(counts) if counts else 0.0


def _is_abbreviation_token(token: str) -> bool:
    cleaned = token.strip("._-/")
    norm = normalize_text(cleaned)
    if not cleaned or norm in UNITS_AND_SAFE_SHORT_TOKENS:
        return False
    if re.fullmatch(r"\d+(?:[\.,]\d+)?", cleaned):
        return False
    if re.fullmatch(r"\d+(?:ml|cm|mm|kg|g|l)", norm):
        return False
    if re.fullmatch(r"[A-Z]{2,5}", cleaned):
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}\.?", cleaned) and norm not in {"de", "du", "la", "le", "et", "ou", "en", "a", "à"}:
        return True
    return False


def abbreviation_ratio(texts: Iterable[str]) -> float:
    tokens: List[str] = []
    for text in texts:
        if looks_like_system_or_noise(text):
            continue
        tokens.extend(tokenize(text))
    if not tokens:
        return 0.0
    bad = sum(1 for token in tokens if _is_abbreviation_token(token))
    return bad / len(tokens)


def uppercase_token_ratio(texts: Iterable[str]) -> float:
    tokens: List[str] = []
    for text in texts:
        if looks_like_system_or_noise(text):
            continue
        tokens.extend(tokenize(text))
    alpha_tokens = [token for token in tokens if re.search(r"[A-Za-zÀ-ÿ]", token)]
    if not alpha_tokens:
        return 0.0

    def is_upper_noise(token: str) -> bool:
        stripped = token.strip("._-/")
        if stripped in SAFE_UPPER_TOKENS:
            return False
        if re.fullmatch(r"\d+(?:[\.,]\d+)?", stripped):
            return False
        if re.search(r"[a-zà-ÿ]", stripped):
            return False
        if len(stripped) <= 2:
            return False
        if re.fullmatch(r"[A-Z0-9]{3,}", stripped):
            return True
        return False

    return sum(1 for token in alpha_tokens if is_upper_noise(token)) / len(alpha_tokens)


def field_display_label(field: Dict[str, Any]) -> str:
    return clean_text(
        field.get("label")
        or field.get("placeholder")
        or field.get("ariaLabel")
        or field.get("accessibleName")
        or field.get("name")
        or ""
    )


def button_display_label(button: Dict[str, Any]) -> str:
    return clean_text(
        button.get("accessibleName")
        or button.get("label")
        or button.get("text")
        or button.get("ariaLabel")
        or button.get("name")
        or ""
    )


def field_semantic_type(field: Dict[str, Any]) -> str:
    raw_type = normalize_text(field.get("type"))
    semantic_type = normalize_text(field.get("semanticType"))
    tag = normalize_text(field.get("tag"))

    if raw_type:
        return raw_type
    if semantic_type:
        return semantic_type
    return tag


def field_has_required_indicator(field: Dict[str, Any]) -> bool:
    if field.get("required") is True:
        return True

    label = field_display_label(field)
    norm = normalize_text(label)
    if "*" in label:
        return True

    return any(marker in norm for marker in REQUIRED_MARKERS)


def field_has_guidance_signal(field: Dict[str, Any]) -> bool:
    label = field_display_label(field)
    placeholder = clean_text(field.get("placeholder"))
    described_by = clean_text(field.get("ariaDescribedBy") or field.get("helperText"))

    if described_by:
        return True

    normalized_placeholder = normalize_text(re.sub(r"[*:]+", " ", placeholder))
    normalized_label = normalize_text(re.sub(r"[*:]+", " ", label))

    if placeholder and normalized_placeholder != normalized_label:
        return True

    combined = normalize_text(" ".join(part for part in (label, placeholder, described_by) if part))
    if any(marker in combined for marker in GUIDANCE_MARKERS):
        return True

    if placeholder and any(token in placeholder for token in ("@", "/", "(", ")")):
        return True

    if placeholder and re.search(r"\d", placeholder):
        return True

    return False


def _heading_item_text(item: Any) -> str:
    if isinstance(item, dict):
        return clean_text(item.get("text") or item.get("label") or item.get("title") or "")
    return clean_text(item)


def _iter_heading_texts(data: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("rawHeadings", "contentHeadings", "headings", "h1", "h2", "h3", "h4", "h5", "h6"):
        values = data.get(key, [])
        if not isinstance(values, list):
            continue
        for item in values:
            txt = _heading_item_text(item)
            if txt:
                out.append(txt)
    return unique_preserve_order(out)


def _contains_any_token(text: str, tokens: Iterable[str]) -> bool:
    norm = normalize_text(text)
    return any(token in norm for token in tokens)


def _form_identity_text(form: Dict[str, Any]) -> str:
    parts = [
        form.get("formAction"),
        form.get("formKey"),
        form.get("formId"),
        form.get("formName"),
    ]
    parts.extend(field_display_label(field) for field in form.get("fields", []))
    parts.extend(button_display_label(button) for button in form.get("buttons", []))
    return normalize_text(" | ".join(part for part in parts if part))


def _form_is_localization_like(form: Dict[str, Any]) -> bool:
    haystack = _form_identity_text(form)
    if _contains_any_token(haystack, LOCALIZATION_TOKENS):
        return True
    return any(
        looks_like_locale_picker(field_display_label(field))
        for field in form.get("fields", [])
    )


def _form_is_search_like(form: Dict[str, Any]) -> bool:
    haystack = _form_identity_text(form)
    fields = form.get("fields", [])

    if _contains_any_token(haystack, SEARCH_FORM_TOKENS):
        return True

    if not fields:
        return False

    for field in fields:
        semantic_type = field_semantic_type(field)
        name = normalize_text(field.get("name"))
        if semantic_type == "search" or name in {"q", "query", "search"}:
            continue
        return False

    return True


def _form_is_filter_like(form: Dict[str, Any]) -> bool:
    haystack = _form_identity_text(form)
    if _contains_any_token(haystack, FILTER_FORM_TOKENS):
        return True

    for field in form.get("fields", []):
        name = normalize_text(field.get("name"))
        label = normalize_text(field_display_label(field))
        if _contains_any_token(name, FILTER_FORM_TOKENS) or _contains_any_token(label, FILTER_FORM_TOKENS):
            return True

    return False


def _has_help_marker(text: str) -> bool:
    norm = normalize_text(text)
    if not norm:
        return False

    words = set(re.findall(r"[a-z0-9]+", norm))
    if words.intersection({"contact", "support", "help", "aide", "assistance", "email", "phone", "telephone"}):
        return True
    return "live chat" in norm


def _component_rect(component: Dict[str, Any]) -> Optional[Dict[str, float]]:
    rect = component.get("rect") or {}
    try:
        x = float(rect.get("x"))
        y = float(rect.get("y"))
        width = float(rect.get("width"))
        height = float(rect.get("height"))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "x": round(x, 2),
        "y": round(y, 2),
        "width": round(width, 2),
        "height": round(height, 2),
    }


def _merged_rect(components: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    rects = [_component_rect(component) for component in components]
    usable = [rect for rect in rects if rect]
    if not usable:
        return None
    left = min(rect["x"] for rect in usable)
    top = min(rect["y"] for rect in usable)
    right = max(rect["x"] + rect["width"] for rect in usable)
    bottom = max(rect["y"] + rect["height"] for rect in usable)
    return {
        "x": round(left, 2),
        "y": round(top, 2),
        "width": round(right - left, 2),
        "height": round(bottom - top, 2),
    }


def component_evidence_target(
    component: Dict[str, Any],
    *,
    reason: str,
    target_kind: str = "component",
    issue_kind: str = "presence",
) -> Optional[Dict[str, Any]]:
    rect = _component_rect(component)
    if not rect:
        return None
    return {
        "target_kind": target_kind,
        "issue_kind": issue_kind,
        "page_name": clean_text(component.get("_pageName")),
        "page_url": clean_text(component.get("_pageUrl")),
        "final_url": clean_text(component.get("_finalUrl") or component.get("_pageUrl")),
        "screenshot_path": clean_text(component.get("_screenshotPath")),
        "component_type": clean_text(component.get("semanticType") or component.get("tag") or component.get("uxRole")),
        "component_text": clean_text(component.get("accessibleName") or component.get("label") or component.get("text") or component.get("placeholder")),
        "highlight_shape": "circle",
        "reason": clean_text(reason),
        "rect": rect,
    }


def region_evidence_target(
    components: List[Dict[str, Any]],
    *,
    page_name: str,
    page_url: str,
    final_url: str,
    screenshot_path: str,
    reason: str,
    target_kind: str = "region",
    issue_kind: str = "absence",
    component_type: str = "region",
    component_text: str = "",
) -> Optional[Dict[str, Any]]:
    rect = _merged_rect(components)
    if not rect:
        return None
    return {
        "target_kind": target_kind,
        "issue_kind": issue_kind,
        "page_name": clean_text(page_name),
        "page_url": clean_text(page_url),
        "final_url": clean_text(final_url or page_url),
        "screenshot_path": clean_text(screenshot_path),
        "component_type": clean_text(component_type),
        "component_text": clean_text(component_text),
        "highlight_shape": "circle",
        "reason": clean_text(reason),
        "rect": rect,
    }


def build_evidence_bundle(
    *,
    criterion: str,
    source: str,
    target: Optional[Dict[str, Any]],
    alternatives: Optional[List[Dict[str, Any]]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    bundle: Dict[str, Any] = {
        "version": 1,
        "criterion": clean_text(criterion),
        "source": clean_text(source),
        "notes": clean_text(notes),
        "target": target or {},
        "alternatives": [item for item in (alternatives or []) if item],
    }
    return bundle


@dataclass
class CheckResult:
    sheet: str
    row: int
    criterion: str
    status: str
    confidence: float
    rationale: str
    evidence: List[str]
    decision_basis: str = "direct"
    evidence_bundle: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PageBundle:
    person_a: Dict[str, Any]
    rendered: Dict[str, Any]


class AuditContext:
    def __init__(self, person_a_data: Dict[str, Any], rendered_data: Dict[str, Any]) -> None:
        self.person_a_data = person_a_data
        self.rendered_data = rendered_data
        self.pages: List[PageBundle] = []
        self._build_page_pairs()

    @classmethod
    def from_files(cls, person_a_path: str | Path, rendered_path: str | Path) -> "AuditContext":
        with open(person_a_path, "r", encoding="utf-8") as file:
            person_a_data = json.load(file)
        with open(rendered_path, "r", encoding="utf-8") as file:
            rendered_data = json.load(file)
        return cls(person_a_data, rendered_data)

    @staticmethod
    def _page_key(page: Dict[str, Any]) -> str:
        url = page.get("finalUrl") or page.get("url") or page.get("pageMeta", {}).get("data", {}).get("finalUrl") or ""
        name = page.get("name") or page.get("pageMeta", {}).get("data", {}).get("name") or ""
        return f"{normalize_text(url)}|{normalize_text(name)}"

    def _build_page_pairs(self) -> None:
        rendered_by_key: Dict[str, Dict[str, Any]] = {}
        for page in self.rendered_data.get("pages", []):
            rendered_by_key[self._page_key(page)] = page
        for page in self.person_a_data.get("pages", []):
            key = self._page_key(page)
            rendered = rendered_by_key.get(key)
            if rendered is not None:
                self.pages.append(PageBundle(person_a=page, rendered=rendered))

    def page_names(self) -> List[str]:
        return [clean_text(page.person_a.get("name")) for page in self.pages]

    def page_titles(self) -> List[str]:
        return [clean_text(page.person_a.get("pageMeta", {}).get("data", {}).get("title")) for page in self.pages]

    @staticmethod
    def _page_provenance(page: PageBundle) -> Dict[str, str]:
        page_meta = page.person_a.get("pageMeta", {}).get("data", {})
        screenshot_paths = page_meta.get("screenshotPaths", {}) or {}
        return {
            "_pageName": clean_text(page.person_a.get("name") or page_meta.get("name")),
            "_pageUrl": clean_text(page.person_a.get("url") or page_meta.get("url")),
            "_finalUrl": clean_text(page.person_a.get("finalUrl") or page_meta.get("finalUrl") or page.person_a.get("url") or page_meta.get("url")),
            "_screenshotPath": clean_text(screenshot_paths.get("page") or ""),
        }

    @classmethod
    def _with_page_provenance(cls, component: Dict[str, Any], page: PageBundle) -> Dict[str, Any]:
        copied = dict(component)
        copied.update(cls._page_provenance(page))
        return copied

    def page_languages(self) -> List[str]:
        langs: List[str] = []
        for page in self.pages:
            lang = page.person_a.get("pageMeta", {}).get("data", {}).get("language")
            if lang:
                langs.append(str(lang).lower())
        return langs

    def all_paragraphs(self) -> List[str]:
        out: List[str] = []
        for page in self.pages:
            for item in page.person_a.get("textContent", {}).get("data", {}).get("paragraphs", []):
                txt = clean_text(item.get("text"))
                if txt:
                    out.append(txt)
        return out

    def meaningful_paragraphs(self) -> List[str]:
        out: List[str] = []
        for text in self.all_paragraphs():
            if looks_like_system_or_noise(text):
                continue
            if looks_like_measurement_or_variant(text) and len(tokenize(text)) <= 3:
                continue
            out.append(text)
        return unique_preserve_order(out)

    def all_list_items(self) -> List[str]:
        out: List[str] = []
        for page in self.pages:
            for item in page.person_a.get("textContent", {}).get("data", {}).get("listItems", []):
                txt = clean_text(item.get("text"))
                if txt:
                    out.append(txt)
        return out

    def meaningful_list_items(self) -> List[str]:
        out: List[str] = []
        for text in self.all_list_items():
            if looks_like_system_or_noise(text):
                continue
            if len(tokenize(text)) == 1 and normalize_text(text) in SITE_BRAND_STOPWORDS:
                continue
            out.append(text)
        return unique_preserve_order(out)

    def all_navigation_labels(self) -> List[str]:
        out: List[str] = []
        nav_keys = [
            "primaryNav",
            "primaryNavItems",
            "footerNav",
            "footerNavUseful",
            "sideNav",
            "secondaryNavItems",
            "breadcrumbs",
            "utilityNav",
            "localeOrPickerLinks",
            "allNavItems",
        ]
        for page in self.pages:
            nav = page.person_a.get("navigation", {}).get("data", {})
            for key in nav_keys:
                for item in nav.get(key, []):
                    txt = clean_text(item.get("text") or item.get("label"))
                    if txt:
                        out.append(txt)
        return out

    def meaningful_navigation_labels(self) -> List[str]:
        return unique_preserve_order([text for text in self.all_navigation_labels() if is_probably_real_nav_label(text)])

    def active_navigation_labels(self) -> List[str]:
        out: List[str] = []
        for page in self.pages:
            for item in page.person_a.get("navigation", {}).get("data", {}).get("activeItems", []):
                txt = clean_text(item.get("text") or item.get("label"))
                if txt and is_probably_real_nav_label(txt):
                    out.append(txt)
        return unique_preserve_order(out)

    def all_forms(self) -> List[Dict[str, Any]]:
        forms: List[Dict[str, Any]] = []
        for page in self.pages:
            page_name = clean_text(page.person_a.get("name"))
            page_url = clean_text(page.person_a.get("finalUrl") or page.person_a.get("url"))
            rendered_forms = page.rendered.get("renderedUi", {}).get("forms", [])

            if rendered_forms:
                for form in rendered_forms:
                    copied = dict(form)
                    copied["fields"] = [dict(field) for field in form.get("fields", [])]
                    copied["buttons"] = [dict(button) for button in form.get("buttons", [])]
                    copied["_pageName"] = page_name
                    copied["_pageUrl"] = page_url
                    copied["_formAction"] = clean_text(form.get("formAction") or "")
                    copied["_formKey"] = clean_text(form.get("formKey") or form.get("formId") or form.get("formName") or "")
                    forms.append(copied)
                continue

            person_a_forms = page.person_a.get("forms", {}).get("data", {}).get("items", [])
            for form in person_a_forms:
                fields = []
                for field in form.get("visibleFields", []) or form.get("userInputFields", []) or form.get("fields", []):
                    fields.append(dict(field))

                copied = dict(form)
                copied["fields"] = fields
                copied["buttons"] = []
                copied["_pageName"] = page_name
                copied["_pageUrl"] = page_url
                copied["_formAction"] = clean_text(form.get("action") or "")
                copied["_formKey"] = clean_text(form.get("formKey") or form.get("id") or form.get("name") or "")
                forms.append(copied)

        return forms

    def user_forms(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for form in self.all_forms():
            if _form_is_localization_like(form):
                continue
            if not form.get("fields") and not form.get("buttons"):
                continue
            out.append(form)
        return out

    def task_forms(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for form in self.user_forms():
            visible_fields = []
            for field in form.get("fields", []):
                semantic_type = field_semantic_type(field)
                if semantic_type in FORM_FIELD_NOISE_TYPES:
                    continue
                if field.get("visible") is False:
                    continue
                visible_fields.append(field)

            if not visible_fields:
                continue
            if _form_is_search_like(form):
                continue
            if _form_is_filter_like(form):
                continue

            copied = dict(form)
            copied["fields"] = visible_fields
            out.append(copied)

        return out

    def all_form_fields(self) -> List[Dict[str, Any]]:
        fields: List[Dict[str, Any]] = []
        for form in self.user_forms():
            form_action = clean_text(form.get("_formAction") or form.get("formAction") or form.get("action") or "")
            form_key = clean_text(form.get("_formKey") or form.get("formKey") or "")
            page_name = clean_text(form.get("_pageName"))
            page_url = clean_text(form.get("_pageUrl"))
            is_search_like = _form_is_search_like(form)
            is_filter_like = _form_is_filter_like(form)

            for field in form.get("fields", []):
                semantic_type = field_semantic_type(field)
                if semantic_type in FORM_FIELD_NOISE_TYPES:
                    continue
                if field.get("visible") is False:
                    continue

                copied = dict(field)
                copied["_formAction"] = form_action
                copied["_formKey"] = form_key
                copied["_pageName"] = page_name
                copied["_pageUrl"] = page_url
                copied["_formIsSearchLike"] = is_search_like
                copied["_formIsFilterLike"] = is_filter_like
                fields.append(copied)

        return fields

    def user_form_fields(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for field in self.all_form_fields():
            action = normalize_text(field.get("_formAction"))
            name = normalize_text(field.get("name"))
            fid = normalize_text(field.get("id"))
            label = normalize_text(field_display_label(field))
            if "localization" in action:
                continue
            if field.get("_formIsSearchLike") or field.get("_formIsFilterLike"):
                continue
            if any(part in name for part in ("country", "currency", "locale")):
                continue
            if any(part in fid for part in ("country", "currency", "locale")):
                continue
            if looks_like_locale_picker(label):
                continue
            out.append(field)
        return out

    def form_submit_buttons(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for form in self.user_forms():
            for button in form.get("buttons", []):
                if button.get("visible") is False:
                    continue

                label = button_display_label(button)
                if not label:
                    continue
                if normalize_text(label) in {"epuise", "sold out", "out of stock"}:
                    continue

                button_type = normalize_text(button.get("type"))
                if button_type == "submit" or any(
                    token in normalize_text(label)
                    for token in ("submit", "envoyer", "send", "save", "s'inscrire", "search", "recherche")
                ):
                    copied = dict(button)
                    copied["_pageName"] = form.get("_pageName")
                    copied["_pageUrl"] = form.get("_pageUrl")
                    copied["_formKey"] = form.get("_formKey") or form.get("formKey")
                    copied["_formAction"] = form.get("_formAction") or form.get("formAction")
                    out.append(copied)
        return out

    def all_media_images(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            out.extend(page.person_a.get("media", {}).get("data", {}).get("images", []))
        return out

    def all_quality_flags(self) -> List[str]:
        out: List[str] = []
        for page in self.pages:
            out.extend(page.person_a.get("qualitySignals", {}).get("flags", []))
        return out

    def all_buttons(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            for button in page.rendered.get("renderedUi", {}).get("components", {}).get("buttons", []):
                if isinstance(button, dict):
                    out.append(self._with_page_provenance(button, page))
        return out

    def user_buttons(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for button in self.all_buttons():
            txt = clean_text(button.get("accessibleName") or button.get("label") or button.get("text"))
            if not txt or not is_user_facing_label(txt):
                continue
            if button.get("uxRole") == "localization-control":
                continue
            out.append(button)
        return out

    def all_links(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            comps = page.rendered.get("renderedUi", {}).get("components", {})
            for link in comps.get("links", []):
                if isinstance(link, dict):
                    out.append(self._with_page_provenance(link, page))
            for link in comps.get("navLinks", []):
                if isinstance(link, dict):
                    out.append(self._with_page_provenance(link, page))
        return out

    def user_links(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for link in self.all_links():
            txt = clean_text(link.get("accessibleName") or link.get("label") or link.get("text"))
            if txt and is_user_facing_label(txt):
                out.append(link)
        return out

    def all_inputs(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            for field in page.rendered.get("renderedUi", {}).get("components", {}).get("inputs", []):
                if isinstance(field, dict):
                    out.append(self._with_page_provenance(field, page))
        return out

    def site_search_inputs(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for field in self.all_inputs():
            input_type = normalize_text(field.get("type"))
            if input_type != "search":
                continue
            name = normalize_text(field.get("name"))
            fid = normalize_text(field.get("id"))
            cls = normalize_text(field.get("className"))
            label = clean_text(field.get("label") or field.get("placeholder") or field.get("name") or "")
            if any(token in name for token in ("country", "locale", "currency")):
                continue
            if any(token in fid for token in ("country", "locale", "currency")):
                continue
            if looks_like_locale_picker(label):
                continue
            if name == "q" or "search" in fid or "search" in cls:
                out.append(field)
        return out

    def all_headings(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            out.extend(page.rendered.get("renderedUi", {}).get("components", {}).get("headings", []))
        return out

    def content_headings(self) -> List[str]:
        out: List[str] = []
        for page in self.pages:
            data = page.person_a.get("titlesAndHeadings", {}).get("data", {})
            for txt in _iter_heading_texts(data):
                if is_meaningful_heading(txt):
                    out.append(txt)
        return unique_preserve_order(out)

    def all_text_blocks(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            for block in page.rendered.get("renderedUi", {}).get("components", {}).get("textBlocks", []):
                if isinstance(block, dict):
                    out.append(self._with_page_provenance(block, page))
        return out

    def meaningful_text_blocks(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for block in self.all_text_blocks():
            txt = clean_text(block.get("accessibleName") or block.get("text") or "")
            if not txt or looks_like_system_or_noise(txt):
                continue
            out.append(block)
        return out

    def nav_components(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            for component in page.rendered.get("renderedUi", {}).get("components", {}).get("navigation", []):
                if isinstance(component, dict):
                    out.append(self._with_page_provenance(component, page))
        return out

    def design_summaries(self) -> List[Dict[str, Any]]:
        return [page.rendered.get("renderedUi", {}).get("designSummary", {}) for page in self.pages]

    def consistency_metrics(self) -> List[Dict[str, Any]]:
        return [page.rendered.get("renderedUi", {}).get("consistencyMetrics", {}) for page in self.pages]

    def has_search_on_every_page(self) -> bool:
        if not self.pages:
            return False
        for page in self.pages:
            inputs = page.rendered.get("renderedUi", {}).get("components", {}).get("inputs", [])
            page_has_site_search = False
            for field in inputs:
                field_type = normalize_text(field.get("type"))
                if field_type != "search":
                    continue
                name = normalize_text(field.get("name"))
                fid = normalize_text(field.get("id"))
                cls = normalize_text(field.get("className"))
                label = clean_text(field.get("label") or field.get("placeholder") or field.get("name") or "")
                if looks_like_locale_picker(label):
                    continue
                if name == "q" or "search" in fid or "search" in cls:
                    page_has_site_search = True
                    break
            if not page_has_site_search:
                return False
        return True

    def search_input_widths(self) -> List[float]:
        widths: List[float] = []
        for field in self.site_search_inputs():
            width = safe_float((field.get("rect") or {}).get("width"))
            if width is not None:
                widths.append(width)
        return widths

    def button_labels(self) -> List[str]:
        labels = [clean_text(b.get("accessibleName") or b.get("label") or b.get("text")) for b in self.user_buttons()]
        return unique_preserve_order([label for label in labels if label])

    def link_labels(self) -> List[str]:
        labels = [clean_text(l.get("accessibleName") or l.get("label") or l.get("text")) for l in self.user_links()]
        return unique_preserve_order([label for label in labels if label])

    def all_feedback_headings(self) -> List[str]:
        out: List[str] = []
        for page in self.pages:
            data = page.person_a.get("titlesAndHeadings", {}).get("data", {})
            for txt in _iter_heading_texts(data):
                if txt:
                    out.append(txt)
        return out

    def has_contact_or_help_path(self) -> bool:
        labels = self.meaningful_navigation_labels() + self.button_labels() + self.link_labels()
        for label in labels:
            if _has_help_marker(label):
                return True
        return False


def make_result(
    sheet: str,
    row: int,
    criterion: str,
    status: str,
    confidence: float,
    rationale: str,
    evidence: Optional[List[str]] = None,
    decision_basis: str = "direct",
    evidence_bundle: Optional[Dict[str, Any]] = None,
) -> CheckResult:
    normalized_evidence = normalize_evidence_items(evidence)
    if not normalized_evidence and status in {TRUE, FALSE}:
        fallback_rationale = clean_text(rationale)
        if fallback_rationale:
            normalized_evidence = [fallback_rationale]

    return CheckResult(
        sheet=sheet,
        row=row,
        criterion=criterion,
        status=status,
        confidence=max(0.0, min(1.0, float(confidence))),
        rationale=rationale,
        evidence=normalized_evidence,
        decision_basis=decision_basis,
        evidence_bundle=evidence_bundle,
    )
