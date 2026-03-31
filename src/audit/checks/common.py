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
        nav_keys = ["primaryNav", "footerNavUseful", "sideNav", "breadcrumbs", "utilityNav", "localeOrPickerLinks"]
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

    def all_form_fields(self) -> List[Dict[str, Any]]:
        fields: List[Dict[str, Any]] = []
        for page in self.pages:
            for form in page.person_a.get("forms", {}).get("data", {}).get("items", []):
                for field in form.get("visibleFields", []):
                    copied = dict(field)
                    copied["_formAction"] = form.get("action")
                    fields.append(copied)
        return fields

    def user_form_fields(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for field in self.all_form_fields():
            action = normalize_text(field.get("_formAction"))
            name = normalize_text(field.get("name"))
            fid = normalize_text(field.get("id"))
            label = normalize_text(field.get("label") or field.get("placeholder") or "")
            if "localization" in action:
                continue
            if any(part in name for part in ("country", "currency", "locale")):
                continue
            if any(part in fid for part in ("country", "currency", "locale")):
                continue
            if looks_like_locale_picker(label):
                continue
            out.append(field)
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
            out.extend(page.rendered.get("renderedUi", {}).get("components", {}).get("buttons", []))
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
            out.extend(comps.get("links", []))
            out.extend(comps.get("navLinks", []))
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
            out.extend(page.rendered.get("renderedUi", {}).get("components", {}).get("inputs", []))
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
            for item in data.get("contentHeadings", []):
                txt = clean_text(item.get("text"))
                if is_meaningful_heading(txt):
                    out.append(txt)
        return unique_preserve_order(out)

    def all_text_blocks(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.pages:
            out.extend(page.rendered.get("renderedUi", {}).get("components", {}).get("textBlocks", []))
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
            out.extend(page.rendered.get("renderedUi", {}).get("components", {}).get("navigation", []))
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
            for item in page.person_a.get("titlesAndHeadings", {}).get("data", {}).get("rawHeadings", []):
                txt = clean_text(item)
                if txt:
                    out.append(txt)
        return out

    def has_contact_or_help_path(self) -> bool:
        labels = self.meaningful_navigation_labels() + self.button_labels() + self.link_labels()
        for label in labels:
            norm = normalize_text(label)
            if any(word in norm for word in ("contact", "support", "help", "aide", "assistance", "email", "phone", "telephone", "téléphone")):
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
) -> CheckResult:
    return CheckResult(
        sheet=sheet,
        row=row,
        criterion=criterion,
        status=status,
        confidence=max(0.0, min(1.0, float(confidence))),
        rationale=rationale,
        evidence=evidence or [],
        decision_basis=decision_basis,
    )