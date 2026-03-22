from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List
from urllib.parse import urlparse


GENERIC_UTILITY_WORDS = {
    "login", "log in", "sign in", "signin", "sign up", "signup", "register",
    "account", "profile", "cart", "checkout", "search", "menu",
    "contact", "help", "support", "faq", "wishlist", "compare",
    "filter", "filters", "sort", "close", "back", "next", "previous",
    "home", "catalog", "catalogue", "shop", "panier", "compte",
    "connexion", "se connecter", "inscription", "wishlist",
}

GENERIC_FORM_LABELS = {
    "recherche", "rechercher", "search", "email", "e-mail", "e_mail",
    "adresse e-mail", "adresse email", "mot de passe", "password",
    "nom", "name", "prénom", "prenom", "phone", "téléphone", "telephone",
}

SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "youtube.com",
    "pinterest.com",
    "snapchat.com",
    "wa.me",
    "whatsapp.com",
    "telegram.me",
    "t.me",
}

FILTER_HEADING_TOKENS = {
    "filter", "filters", "sort", "results", "result", "items", "products",
    "apply", "reset", "clear", "refine", "search",
    "filtre", "filtres", "trier", "tri", "réinitialiser", "reinitialiser",
    "effacer", "tout supprimer", "supprimer", "appliquer",
    "produits", "articles", "résultats", "resultats",
}

FILTER_CONTROL_WORDS = {
    "réinitialiser", "reinitialiser", "tout supprimer", "supprimer",
    "effacer", "appliquer", "apply", "clear", "reset", "close",
}

SYSTEM_HEADING_EXACT = {
    "article ajouté au panier",
    "article ajoute au panier",
    "subscribe to our emails",
    "pays/région",
    "pays / région",
    "pays / region",
    "filtrer et trier",
    "filtrer",
    "trier par",
    "tri",
    "filter",
    "sort",
    "filters",
}

LOCALE_UI_TOKENS = {
    "country", "country/region", "country / region", "locale",
    "currency", "devise", "langue", "language",
    "tnd", "د.ت", "search country", "rechercher un pays",
    "country_filter", "localization", "sélecteur", "select country",
}

LOCALE_STRONG_WORDS = {
    "pays", "région", "region",
}

NEWSLETTER_TOKENS = {
    "subscribe", "newsletter", "emails", "email address",
    "abonnez", "abonnez-vous", "inscrivez-vous", "newsletter",
}

AUTH_UTILITY_TOKENS = {
    "login", "sign in", "signin", "sign up", "signup", "register",
    "create account", "my account", "account", "compte", "connexion",
    "se connecter", "s’inscrire", "mot de passe", "password",
}

PRODUCT_HINT_TOKENS = {
    "ajouter au panier", "add to cart", "acheter", "buy now",
    "en stock", "out of stock", "promo", "promotion", "sale",
    "vendor", "fournisseur", "marque", "brand", "prix", "price",
}

CATEGORY_HINT_TOKENS = {
    "collection", "collections", "catégorie", "categorie", "univers",
    "range", "gamme", "été", "ete", "enfants", "beauty", "beauté", "beaute",
    "décoration", "decoration", "cadeaux", "thermos", "bambou", "microfibre",
    "plage", "jardin", "organisation", "accessoires", "salle de bain",
    "maison", "cuisine", "bureau", "bébé", "bebe", "été plage", "jardinage",
}

SYSTEM_TEXT_SNIPPETS = {
    "s'ouvre dans une nouvelle fenêtre",
    "s’ouvre dans une nouvelle fenêtre",
    "opens in a new window",
    "le choix d'une sélection entraîne l'actualisation de la page entière",
    "le choix d’une sélection entraîne l’actualisation de la page entière",
    "selection results in a full page refresh",
}

COUNTRY_NAMES = {
    "afghanistan", "afrique du sud", "albanie", "algérie", "algerie", "allemagne",
    "andorre", "angola", "arabie saoudite", "argentine", "arménie", "armenie",
    "australie", "autriche", "belgique", "bénin", "benin", "brésil", "bresil",
    "bulgarie", "burkina faso", "cameroun", "canada", "chili", "chine",
    "chypre", "colombie", "corée du sud", "coree du sud", "côte d’ivoire",
    "cote d'ivoire", "croatie", "danemark", "égypte", "egypte", "espagne",
    "estonie", "états-unis", "etats-unis", "finlande", "france", "gabon",
    "grèce", "grece", "hongrie", "inde", "indonésie", "indonesie", "irak",
    "iran", "irlande", "islande", "israël", "israel", "italie", "japon",
    "jordanie", "kenya", "koweït", "koweit", "lettonie", "liban", "libye",
    "lituanie", "luxembourg", "maroc", "mexique", "monaco", "nigéria", "nigeria",
    "norvège", "norvege", "nouvelle-zélande", "nouvelle zelande", "oman",
    "pakistan", "pays-bas", "pays bas", "pérou", "perou", "pologne",
    "portugal", "qatar", "roumanie", "royaume-uni", "royaume uni", "russie",
    "sénégal", "senegal", "serbie", "singapour", "slovaquie", "slovénie",
    "slovenie", "suède", "suede", "suisse", "tunisie", "turquie",
    "uk", "uae", "united arab emirates", "united kingdom", "united states",
}

CURRENCY_MARKERS = {
    "$", "€", "£", "dt", "tnd", "usd", "eur", "gbp", "aed", "mad",
    "cad", "dzd", "sar", "dh", "د.ت",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_text(value: Any) -> str:
    return _clean_text(value).casefold()


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _unique_strings(values: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _unique_dicts_by_key(items: List[Dict[str, Any]], key_builder) -> List[Dict[str, Any]]:
    seen = set()
    output = []
    for item in items:
        key = key_builder(item)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).netloc.casefold() or None
    except Exception:
        return None


def _extract_path(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).path or "").casefold()
    except Exception:
        return ""


def _is_social_domain(domain: str | None) -> bool:
    if not domain:
        return False
    return any(domain == social or domain.endswith("." + social) for social in SOCIAL_DOMAINS)


def _looks_like_count_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False

    lowered = cleaned.casefold()

    if re.fullmatch(
        r"\d+\s*(product|products|item|items|article|articles|résultat|résultats|resultat|resultats|page|pages|produit|produits)\b.*",
        lowered,
    ):
        return True

    words = cleaned.split()
    if len(words) > 7:
        return False

    digit_count = sum(ch.isdigit() for ch in cleaned)
    if digit_count == 0:
        return False

    count_tokens = {
        "product", "products", "result", "results", "item", "items",
        "article", "articles", "page", "pages", "produit", "produits",
        "résultat", "résultats", "resultat", "resultats",
    }
    return any(token in lowered for token in count_tokens)


def _contains_any_token(text: str, tokens: set[str]) -> bool:
    lowered = _normalize_text(text)
    return any(token in lowered for token in tokens)


def _looks_like_price_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False

    lowered = cleaned.casefold()
    if any(token in lowered for token in CURRENCY_MARKERS):
        return True

    if re.search(r"\b\d+[.,]\d{2}\b", cleaned):
        return True

    if re.search(r"\b\d+\b", cleaned) and any(
        token in lowered for token in {"prix", "price", "dt", "tnd", "usd", "eur"}
    ):
        return True

    return False


def _looks_like_country_currency_line(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.casefold()
    if not cleaned:
        return False

    has_country = lowered in COUNTRY_NAMES
    has_currency = any(marker in lowered for marker in CURRENCY_MARKERS)

    if has_country and has_currency:
        return True

    for country in COUNTRY_NAMES:
        if lowered.startswith(country + " ") and has_currency:
            return True

    if re.fullmatch(r"[^\d]{3,40}\s+(tnd|usd|eur|gbp|aed|mad|cad|dzd|sar|dh|د\.ت)\b.*", lowered):
        return True

    return False


def _looks_like_locale_picker_text(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.casefold()
    if not cleaned:
        return False

    if lowered in GENERIC_FORM_LABELS:
        return False

    if _looks_like_country_currency_line(cleaned):
        return True

    if lowered in COUNTRY_NAMES:
        return True

    if len(cleaned.split()) <= 4 and any(marker in lowered for marker in CURRENCY_MARKERS):
        return True

    if _contains_any_token(cleaned, LOCALE_UI_TOKENS):
        return True

    if any(word in lowered for word in LOCALE_STRONG_WORDS) and any(
        marker in lowered for marker in CURRENCY_MARKERS
    ):
        return True

    return False


def _looks_like_picker_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False

    lowered = cleaned.casefold()

    if lowered in GENERIC_FORM_LABELS:
        return False

    if _looks_like_locale_picker_text(cleaned):
        return True

    word_count = len(cleaned.split())
    if word_count > 3 or len(cleaned) > 24:
        return False

    letters = sum(ch.isalpha() for ch in cleaned)
    digits = sum(ch.isdigit() for ch in cleaned)
    if letters < 3 or digits > 0:
        return False

    if lowered in GENERIC_UTILITY_WORDS:
        return False
    if _looks_like_count_text(cleaned):
        return False

    return False


def _looks_like_locale_mass(items: List[Dict[str, Any]]) -> bool:
    texts = [_clean_text(item.get("text")) for item in items if _clean_text(item.get("text"))]
    if len(texts) < 8:
        return False

    picker_candidates = [t for t in texts if _looks_like_locale_picker_text(t)]
    unique_candidates = _unique_strings(picker_candidates)
    return len(unique_candidates) >= 8


def _is_filter_sort_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    if _looks_like_count_text(text):
        return True
    return any(token in lowered for token in FILTER_HEADING_TOKENS)


def _is_filter_control_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    return lowered in FILTER_CONTROL_WORDS or any(token in lowered for token in FILTER_CONTROL_WORDS)


def _is_system_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False

    if lowered in SYSTEM_HEADING_EXACT:
        return True
    if lowered in SYSTEM_TEXT_SNIPPETS:
        return True

    if any(token in lowered for token in CATEGORY_HINT_TOKENS):
        return False

    if lowered in {
        "subscribe to our emails",
        "newsletter",
        "email address",
        "mot de passe",
        "password",
        "login",
        "sign in",
        "signin",
        "sign up",
        "signup",
        "register",
        "create account",
        "my account",
        "account",
        "compte",
        "connexion",
        "se connecter",
        "s’inscrire",
    }:
        return True

    if "panier" in lowered and "ajout" in lowered:
        return True

    return False


def _is_short_meaningful_commerce_text(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.casefold()
    if not cleaned:
        return False

    if len(cleaned.split()) > 4 or len(cleaned) > 32:
        return False

    if _looks_like_count_text(cleaned):
        return False
    if _looks_like_locale_picker_text(cleaned):
        return False
    if _is_filter_sort_text(cleaned):
        return False
    if lowered in GENERIC_UTILITY_WORDS:
        return False
    if lowered in GENERIC_FORM_LABELS:
        return False

    if re.fullmatch(r"[A-Za-zÀ-ÿ0-9'’&\-\s]+", cleaned) is None:
        return False

    if any(token in lowered for token in CATEGORY_HINT_TOKENS):
        return True

    if len(cleaned.split()) <= 2 and 3 <= len(cleaned) <= 24 and not _is_system_text(cleaned):
        return True

    return False


def _is_probably_short_utility_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    if _is_short_meaningful_commerce_text(text):
        return False
    if lowered in GENERIC_UTILITY_WORDS:
        return True
    return len(lowered.split()) <= 2 and len(lowered) <= 14


def _is_system_heading(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    if lowered in SYSTEM_HEADING_EXACT:
        return True
    if _is_filter_sort_text(text):
        return True
    if _looks_like_locale_picker_text(text):
        return True
    if _is_system_text(text):
        return True
    return False


def _is_probable_product_title(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.casefold()
    if not cleaned:
        return False
    if _looks_like_count_text(cleaned):
        return False
    if _looks_like_locale_picker_text(cleaned):
        return False
    if _is_filter_sort_text(cleaned):
        return False
    if _is_system_text(cleaned):
        return False
    if any(snippet in lowered for snippet in SYSTEM_TEXT_SNIPPETS):
        return False
    if any(token in lowered for token in CATEGORY_HINT_TOKENS):
        return False
    if len(cleaned.split()) <= 1 and len(cleaned) < 18:
        return False
    return len(cleaned) >= 18 or len(cleaned.split()) >= 3


def _has_product_hint(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    if _looks_like_price_text(text):
        return True
    return any(token in lowered for token in PRODUCT_HINT_TOKENS)


def _looks_like_category_or_section_text(text: str) -> bool:
    cleaned = _clean_text(text)
    lowered = cleaned.casefold()
    if not cleaned:
        return False

    if any(token in lowered for token in CATEGORY_HINT_TOKENS):
        return True

    if _is_short_meaningful_commerce_text(cleaned) and not _has_product_hint(cleaned) and not _looks_like_price_text(cleaned):
        return True

    return False


def _summarize_counts(strings: List[str]) -> List[Dict[str, Any]]:
    counter = Counter(_clean_text(item) for item in strings if _clean_text(item))
    return [{"text": text, "count": count} for text, count in counter.most_common()]


def _clean_link_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "text": _clean_text(item.get("text")) or None,
        "href": _clean_text(item.get("href")) or None,
        "ariaLabel": _clean_text(item.get("ariaLabel")) or None,
        "title": _clean_text(item.get("title")) or None,
    }


def _is_probable_locale_link(item: Dict[str, Any]) -> bool:
    text = _clean_text(item.get("text"))
    href = _clean_text(item.get("href"))
    aria = _clean_text(item.get("ariaLabel"))
    title = _clean_text(item.get("title"))

    combined = " ".join(part for part in [text, aria, title] if part).strip()
    path = _extract_path(href)

    if _looks_like_locale_picker_text(combined):
        return True

    if "localization" in path or "country" in path or "locale" in path or "language" in path:
        return True

    return False


def _is_filter_control_link(item: Dict[str, Any]) -> bool:
    text = _clean_text(item.get("text"))
    href = _clean_text(item.get("href"))
    lowered = text.casefold()

    if not text:
        return False

    if _is_filter_control_text(text):
        return True

    if href.endswith("#") and lowered in FILTER_CONTROL_WORDS:
        return True

    return False


def _is_utility_link(item: Dict[str, Any]) -> bool:
    text = _clean_text(item.get("text"))
    href = _clean_text(item.get("href"))

    if not text:
        return False

    lowered = text.casefold()

    if _is_filter_control_link(item):
        return False

    if lowered in {
        "close", "back", "next", "previous", "retour",
    }:
        return True

    if href.endswith("#"):
        return True

    return _is_probably_short_utility_text(text)


def _classify_nav_group(items: List[Dict[str, Any]], current_domain: str | None) -> Dict[str, Any]:
    unique_items = _unique_dicts_by_key(
        items,
        lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
    )

    cleaned_items = [
        _clean_link_item(item)
        for item in unique_items
        if _clean_text(item.get("text")) or _clean_text(item.get("href"))
    ]

    locale_or_picker_links = []
    utility_links = []
    filter_control_links = []
    external_links = []
    social_links = []
    useful_links = []

    group_looks_like_locale_picker = _looks_like_locale_mass(cleaned_items)

    for item in cleaned_items:
        text = item["text"] or ""
        href = item["href"]
        href_domain = _extract_domain(href)

        if href_domain and _is_social_domain(href_domain):
            social_links.append(item)
            external_links.append(item)
            continue

        if _is_probable_locale_link(item) or (
            group_looks_like_locale_picker and text and _looks_like_locale_picker_text(text)
        ):
            locale_or_picker_links.append(item)
            continue

        if href and current_domain and href_domain and href_domain != current_domain:
            external_links.append(item)
            useful_links.append(item)
            continue

        if _is_filter_control_link(item):
            filter_control_links.append(item)
            continue

        if _is_utility_link(item):
            utility_links.append(item)
            continue

        useful_links.append(item)

    return {
        "useful": useful_links,
        "locale": locale_or_picker_links,
        "utility": utility_links,
        "filterControls": filter_control_links,
        "external": external_links,
        "social": social_links,
    }


def _clean_navigation_block(block: Dict[str, Any], final_url: str | None) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}
    current_domain = _extract_domain(final_url)

    primary = _classify_nav_group(_safe_list(data.get("primaryNav")), current_domain)
    footer = _classify_nav_group(_safe_list(data.get("footerNav")), current_domain)
    side = _classify_nav_group(_safe_list(data.get("sideNav")), current_domain)
    breadcrumbs = _classify_nav_group(_safe_list(data.get("breadcrumbs")), current_domain)

    active_items = _unique_dicts_by_key(
        _safe_list(data.get("activeItems")),
        lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
    )

    logo_to_home = data.get("logoToHome")

    return {
        "status": "ok",
        "data": {
            "primaryNav": primary["useful"],
            "footerNavUseful": footer["useful"],
            "sideNav": side["useful"],
            "breadcrumbs": breadcrumbs["useful"],
            "utilityNav": _unique_dicts_by_key(
                primary["utility"] + footer["utility"] + side["utility"],
                lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
            ),
            "filterControlLinks": _unique_dicts_by_key(
                primary["filterControls"] + footer["filterControls"] + side["filterControls"],
                lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
            ),
            "localeOrPickerLinks": _unique_dicts_by_key(
                primary["locale"] + footer["locale"] + side["locale"],
                lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
            ),
            "socialLinks": _unique_dicts_by_key(
                primary["social"] + footer["social"] + side["social"],
                lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
            ),
            "externalLinks": _unique_dicts_by_key(
                primary["external"] + footer["external"] + side["external"],
                lambda item: (_normalize_text(item.get("text")), _clean_text(item.get("href"))),
            ),
            "activeItems": [
                {
                    "text": _clean_text(item.get("text")) or None,
                    "href": _clean_text(item.get("href")) or None,
                    "ariaCurrent": _clean_text(item.get("ariaCurrent")) or None,
                }
                for item in active_items
                if _clean_text(item.get("text")) or _clean_text(item.get("href"))
            ],
            "logoToHome": {
                "text": _clean_text(logo_to_home.get("text")) or None,
                "href": _clean_text(logo_to_home.get("href")) or None,
            } if isinstance(logo_to_home, dict) else None,
            "counts": {
                "primaryNav": len(primary["useful"]),
                "footerNavUseful": len(footer["useful"]),
                "sideNav": len(side["useful"]),
                "breadcrumbs": len(breadcrumbs["useful"]),
                "utilityNav": len(primary["utility"] + footer["utility"] + side["utility"]),
                "filterControlLinks": len(primary["filterControls"] + footer["filterControls"] + side["filterControls"]),
                "localeOrPickerLinks": len(primary["locale"] + footer["locale"] + side["locale"]),
                "socialLinks": len(primary["social"] + footer["social"] + side["social"]),
                "externalLinks": len(primary["external"] + footer["external"] + side["external"]),
            },
        },
        "errors": list((block or {}).get("errors") or []),
    }


def _dedupe_heading_items(headings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _unique_dicts_by_key(
        headings,
        lambda item: (_normalize_text(item.get("level")), _normalize_text(item.get("text"))),
    )


def _clean_headings_block(block: Dict[str, Any]) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}
    headings = _safe_list(data.get("headings"))

    raw_texts = [_clean_text(item.get("text")) for item in headings if _clean_text(item.get("text"))]
    deduped_headings = _dedupe_heading_items(headings)

    content_headings = []
    system_headings = []
    filter_headings = []
    repeated_headings = []

    heading_counts = Counter(raw_texts)

    for item in deduped_headings:
        text = _clean_text(item.get("text"))
        level = _clean_text(item.get("level"))

        if not text:
            continue

        base_entry = {"level": level, "text": text}

        if _is_filter_sort_text(text):
            filter_headings.append(base_entry)
            continue

        if _is_short_meaningful_commerce_text(text):
            content_headings.append(base_entry)
        elif _is_system_heading(text) or _is_probably_short_utility_text(text):
            system_headings.append(base_entry)
            continue
        else:
            content_headings.append(base_entry)

        if heading_counts.get(text, 0) > 1:
            repeated_headings.append(
                {
                    "level": level,
                    "text": text,
                    "count": heading_counts[text],
                }
            )

    h1 = [item["text"] for item in content_headings if item["level"] == "h1"]
    h2 = [item["text"] for item in content_headings if item["level"] == "h2"]
    h3 = [item["text"] for item in content_headings if item["level"] == "h3"]
    h4 = [item["text"] for item in content_headings if item["level"] == "h4"]
    h5 = [item["text"] for item in content_headings if item["level"] == "h5"]
    h6 = [item["text"] for item in content_headings if item["level"] == "h6"]

    anomalies = list(_safe_list(data.get("anomalies")))
    if not h1 and "missing_meaningful_h1" not in anomalies:
        anomalies.append("missing_meaningful_h1")

    return {
        "status": "ok",
        "data": {
            "rawHeadingCount": len(raw_texts),
            "contentHeadingCount": len(content_headings),
            "rawHeadings": raw_texts,
            "contentHeadings": content_headings,
            "systemHeadings": system_headings,
            "filterHeadings": filter_headings,
            "repeatedHeadings": repeated_headings,
            "h1": h1,
            "h2": h2,
            "h3": h3,
            "h4": h4,
            "h5": h5,
            "h6": h6,
            "headingCounts": _summarize_counts(raw_texts),
            "anomalies": anomalies,
        },
        "errors": list((block or {}).get("errors") or []),
    }


def _clean_text_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique_items = _unique_dicts_by_key(items, lambda item: _normalize_text(item.get("text")))
    return [{"text": _clean_text(item.get("text"))} for item in unique_items if _clean_text(item.get("text"))]


def _is_duplicate_non_content_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False

    if lowered in {"facebook", "instagram", "tiktok", "youtube", "linkedin", "x", "twitter"}:
        return True

    if lowered in GENERIC_UTILITY_WORDS:
        return True

    return False


def _split_text_noise(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = _clean_text_items(items)

    meaningful = []
    repeated_short = []
    count_like = []
    picker_like = []
    system_like = []
    short_meaningful = []

    for item in cleaned:
        text = item["text"]
        lowered = text.casefold()

        if _looks_like_count_text(text):
            count_like.append(item)
            continue

        if _looks_like_locale_picker_text(text):
            picker_like.append(item)
            continue

        if _is_duplicate_non_content_text(text):
            repeated_short.append(item)
            continue

        if _is_system_text(text) or _is_filter_sort_text(text):
            system_like.append(item)
            continue

        if lowered in GENERIC_FORM_LABELS:
            meaningful.append(item)
            continue

        if _is_short_meaningful_commerce_text(text):
            short_meaningful.append(item)
            meaningful.append(item)
            continue

        if len(text.split()) <= 2 and len(text) <= 16:
            repeated_short.append(item)
            continue

        meaningful.append(item)

    return {
        "meaningful": meaningful,
        "shortMeaningful": short_meaningful,
        "repeatedShort": repeated_short,
        "countLike": count_like,
        "pickerLike": picker_like,
        "systemLike": system_like,
    }


def _clean_cta_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    unique_items = _unique_dicts_by_key(
        items,
        lambda item: (_normalize_text(item.get("kind")), _normalize_text(item.get("text"))),
    )

    useful = []
    short_utility = []
    picker_like = []
    system_like = []

    for item in unique_items:
        text = _clean_text(item.get("text"))
        if not text:
            continue

        entry = {
            "text": text,
            "kind": _clean_text(item.get("kind")) or None,
        }

        if _looks_like_locale_picker_text(text):
            picker_like.append(entry)
        elif _is_system_text(text) or _is_filter_sort_text(text):
            system_like.append(entry)
        elif _is_probably_short_utility_text(text):
            short_utility.append(entry)
        else:
            useful.append(entry)

    return {
        "useful": useful,
        "shortUtility": short_utility,
        "pickerLike": picker_like,
        "systemLike": system_like,
    }


def _extract_product_candidates(
    list_items: List[Dict[str, Any]],
    paragraphs: List[Dict[str, Any]],
    ctas_useful: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    list_texts = [item["text"] for item in list_items]
    paragraph_texts = [item["text"] for item in paragraphs]
    cta_texts = [item["text"] for item in ctas_useful]

    page_has_prices = any(_looks_like_price_text(text) for text in list_texts + paragraph_texts)
    page_has_product_hints = any(_has_product_hint(text) for text in list_texts + paragraph_texts + cta_texts)

    if not page_has_prices or not page_has_product_hints:
        return []

    titles = []
    for text in list_texts:
        lowered = text.casefold()

        if not _is_probable_product_title(text):
            continue
        if _looks_like_locale_picker_text(text):
            continue
        if _is_system_text(text) or _is_filter_sort_text(text):
            continue
        if _looks_like_category_or_section_text(text):
            continue
        if any(snippet in lowered for snippet in SYSTEM_TEXT_SNIPPETS):
            continue
        if lowered in {
            "cadeaux", "decoration", "décoration", "beauté", "beaute",
            "enfants", "thermos", "bambou", "microfibre",
        }:
            continue

        local_product_evidence = (
            _has_product_hint(text)
            or _looks_like_price_text(text)
            or (
                any(word in lowered for word in {"inox", "ml", "cm", "l", "made in", "pack"})
                and not _looks_like_category_or_section_text(text)
            )
        )

        if not local_product_evidence:
            continue

        titles.append(text)

    output = [{"text": text} for text in titles]
    return _unique_dicts_by_key(output, lambda item: _normalize_text(item.get("text")))


def _clean_text_content_block(block: Dict[str, Any]) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}

    paragraphs = _split_text_noise(_safe_list(data.get("paragraphs")))
    list_items = _split_text_noise(_safe_list(data.get("listItems")))
    labels = _split_text_noise(_safe_list(data.get("labels")))
    long_blocks = _split_text_noise(_safe_list(data.get("longTextBlocks")))
    ctas = _clean_cta_items(_safe_list(data.get("ctaTexts")))

    product_like_texts = _extract_product_candidates(
        list_items["meaningful"],
        paragraphs["meaningful"],
        ctas["useful"],
    )

    return {
        "status": "ok",
        "data": {
            "paragraphs": paragraphs["meaningful"],
            "paragraphsShortMeaningful": paragraphs["shortMeaningful"],
            "paragraphsRepeatedShort": paragraphs["repeatedShort"],
            "paragraphsCountLike": paragraphs["countLike"],
            "paragraphsPickerLike": paragraphs["pickerLike"],
            "paragraphsSystemLike": paragraphs["systemLike"],
            "listItems": list_items["meaningful"],
            "listItemsShortMeaningful": list_items["shortMeaningful"],
            "listItemsRepeatedShort": list_items["repeatedShort"],
            "listItemsCountLike": list_items["countLike"],
            "listItemsPickerLike": list_items["pickerLike"],
            "listItemsSystemLike": list_items["systemLike"],
            "labels": labels["meaningful"],
            "labelsShortMeaningful": labels["shortMeaningful"],
            "labelsRepeatedShort": labels["repeatedShort"],
            "labelsCountLike": labels["countLike"],
            "labelsPickerLike": labels["pickerLike"],
            "labelsSystemLike": labels["systemLike"],
            "ctaTexts": ctas["useful"],
            "ctaTextsShortUtility": ctas["shortUtility"],
            "ctaTextsPickerLike": ctas["pickerLike"],
            "ctaTextsSystemLike": ctas["systemLike"],
            "longTextBlocks": long_blocks["meaningful"],
            "longTextBlocksShortMeaningful": long_blocks["shortMeaningful"],
            "longTextBlocksCountLike": long_blocks["countLike"],
            "longTextBlocksPickerLike": long_blocks["pickerLike"],
            "longTextBlocksSystemLike": long_blocks["systemLike"],
            "productLikeTexts": product_like_texts,
            "counts": {
                "paragraphs": len(paragraphs["meaningful"]),
                "listItems": len(list_items["meaningful"]),
                "labels": len(labels["meaningful"]),
                "ctaTexts": len(ctas["useful"]),
                "longTextBlocks": len(long_blocks["meaningful"]),
                "productLikeTexts": len(product_like_texts),
            },
        },
        "errors": list((block or {}).get("errors") or []),
    }


def _is_user_input_field(field: Dict[str, Any]) -> bool:
    tag = _normalize_text(field.get("tag"))
    field_type = _normalize_text(field.get("type"))

    if tag in {"textarea", "select"}:
        return True

    if tag == "input":
        return field_type not in {"hidden", "submit", "button", "reset", "image", "file"}

    return False


def _is_localization_form(cleaned_form: Dict[str, Any]) -> bool:
    action = _normalize_text(cleaned_form.get("action"))
    visible_fields = cleaned_form.get("visibleFields") or []
    all_fields = cleaned_form.get("allFields") or []

    if "localization" in action:
        return True

    texts = []
    for field in visible_fields + all_fields:
        texts.extend([
            _clean_text(field.get("name")),
            _clean_text(field.get("id")),
            _clean_text(field.get("label")),
            _clean_text(field.get("placeholder")),
            _clean_text(field.get("type")),
        ])

    joined = " | ".join(text for text in texts if text)
    if _contains_any_token(joined, LOCALE_UI_TOKENS):
        return True
    if any(word in _normalize_text(joined) for word in LOCALE_STRONG_WORDS) and any(
        marker in _normalize_text(joined) for marker in CURRENCY_MARKERS
    ):
        return True

    return False


def _clean_forms_block(block: Dict[str, Any]) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}
    forms = _safe_list(data.get("items"))

    cleaned_forms = []
    total_fields = 0
    visible_fields_count = 0
    hidden_fields_count = 0
    user_input_fields_count = 0
    localization_forms_count = 0

    for form in forms:
        fields = _safe_list(form.get("fields"))
        cleaned_fields = []
        visible_fields = []
        hidden_fields = []
        user_input_fields = []

        for field in fields:
            cleaned_field = {
                "tag": _clean_text(field.get("tag")) or None,
                "type": _clean_text(field.get("type")) or None,
                "name": _clean_text(field.get("name")) or None,
                "id": _clean_text(field.get("id")) or None,
                "label": _clean_text(field.get("label")) or None,
                "placeholder": _clean_text(field.get("placeholder")) or None,
                "required": bool(field.get("required")),
                "disabled": bool(field.get("disabled")),
            }

            total_fields += 1
            cleaned_fields.append(cleaned_field)

            if _normalize_text(cleaned_field["type"]) == "hidden":
                hidden_fields.append(cleaned_field)
                hidden_fields_count += 1
            else:
                visible_fields.append(cleaned_field)
                visible_fields_count += 1

            if _is_user_input_field(cleaned_field):
                user_input_fields.append(cleaned_field)
                user_input_fields_count += 1

        cleaned_form = {
            "action": _clean_text(form.get("action")) or None,
            "method": (_clean_text(form.get("method")) or "get").lower(),
            "allFields": cleaned_fields,
            "visibleFields": visible_fields,
            "hiddenFields": hidden_fields,
            "userInputFields": user_input_fields,
            "counts": {
                "allFields": len(cleaned_fields),
                "visibleFields": len(visible_fields),
                "hiddenFields": len(hidden_fields),
                "userInputFields": len(user_input_fields),
            },
        }

        cleaned_form["isLocalizationForm"] = _is_localization_form(cleaned_form)
        if cleaned_form["isLocalizationForm"]:
            localization_forms_count += 1

        cleaned_forms.append(cleaned_form)

    meaningful_forms = [form for form in cleaned_forms if not form.get("isLocalizationForm")]

    return {
        "status": "ok",
        "data": {
            "items": cleaned_forms,
            "meaningfulForms": meaningful_forms,
            "totalForms": len(cleaned_forms),
            "meaningfulFormCount": len(meaningful_forms),
            "localizationFormCount": localization_forms_count,
            "totalFields": total_fields,
            "visibleFields": visible_fields_count,
            "hiddenFields": hidden_fields_count,
            "userInputFields": user_input_fields_count,
        },
        "errors": list((block or {}).get("errors") or []),
    }


def _clean_media_block(block: Dict[str, Any]) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}
    images = _safe_list(data.get("images"))
    videos = _safe_list(data.get("videos"))
    audios = _safe_list(data.get("audios"))

    unique_images = _unique_dicts_by_key(images, lambda item: _clean_text(item.get("src")))

    missing_alt = []
    empty_alt = []
    repeated_sources = []

    counter = Counter(_clean_text(item.get("src")) for item in images if _clean_text(item.get("src")))
    for src, count in counter.items():
        if count > 1:
            repeated_sources.append({"src": src, "count": count})

    for image in unique_images:
        alt = image.get("alt")
        cleaned_image = {
            "src": _clean_text(image.get("src")) or None,
            "alt": _clean_text(alt) if alt is not None else None,
            "width": image.get("width"),
            "height": image.get("height"),
        }
        if alt is None:
            missing_alt.append(cleaned_image)
        elif _clean_text(alt) == "":
            empty_alt.append(cleaned_image)

    cleaned_unique_images = [
        {
            "src": _clean_text(item.get("src")) or None,
            "alt": _clean_text(item.get("alt")) if item.get("alt") is not None else None,
            "width": item.get("width"),
            "height": item.get("height"),
        }
        for item in unique_images
        if _clean_text(item.get("src"))
    ]

    return {
        "status": "ok",
        "data": {
            "images": cleaned_unique_images,
            "videos": videos,
            "audios": audios,
            "hasCaptionTracks": bool(data.get("hasCaptionTracks")),
            "counts": {
                "rawImages": len(images),
                "uniqueImages": len(cleaned_unique_images),
                "videos": len(videos),
                "audios": len(audios),
                "missingAlt": len(missing_alt),
                "emptyAlt": len(empty_alt),
                "repeatedSources": len(repeated_sources),
            },
            "missingAltImages": missing_alt,
            "emptyAltImages": empty_alt,
            "repeatedImageSources": repeated_sources,
        },
        "errors": list((block or {}).get("errors") or []),
    }


def _build_quality_signals(cleaned_page: Dict[str, Any]) -> Dict[str, Any]:
    headings = (((cleaned_page.get("titlesAndHeadings") or {}).get("data")) or {})
    navigation = (((cleaned_page.get("navigation") or {}).get("data")) or {})
    text_content = (((cleaned_page.get("textContent") or {}).get("data")) or {})
    forms = (((cleaned_page.get("forms") or {}).get("data")) or {})
    media = (((cleaned_page.get("media") or {}).get("data")) or {})

    flags = []

    if not headings.get("h1"):
        flags.append("missing_meaningful_h1")

    if len(headings.get("repeatedHeadings") or []) >= 3:
        flags.append("many_repeated_headings")

    if (navigation.get("counts") or {}).get("localeOrPickerLinks", 0) >= 8:
        flags.append("heavy_picker_or_locale_noise")

    if (forms.get("hiddenFields") or 0) > (forms.get("visibleFields") or 0):
        flags.append("forms_have_many_hidden_fields")

    if (forms.get("localizationFormCount") or 0) > 0:
        flags.append("has_localization_form")

    if (media.get("counts") or {}).get("missingAlt", 0) > 0:
        flags.append("images_missing_alt")

    if (text_content.get("counts") or {}).get("productLikeTexts", 0) >= 8:
        flags.append("listing_or_catalog_like_page")

    return {
        "flags": flags,
        "summary": {
            "meaningfulH1Count": len(headings.get("h1") or []),
            "repeatedHeadingCount": len(headings.get("repeatedHeadings") or []),
            "localeOrPickerLinkCount": (navigation.get("counts") or {}).get("localeOrPickerLinks", 0),
            "filterControlLinkCount": (navigation.get("counts") or {}).get("filterControlLinks", 0),
            "socialLinkCount": (navigation.get("counts") or {}).get("socialLinks", 0),
            "visibleFieldCount": forms.get("visibleFields") or 0,
            "hiddenFieldCount": forms.get("hiddenFields") or 0,
            "localizationFormCount": forms.get("localizationFormCount") or 0,
            "missingAltCount": (media.get("counts") or {}).get("missingAlt", 0),
            "productLikeTextCount": (text_content.get("counts") or {}).get("productLikeTexts", 0),
        },
    }


def clean_person_a_page(page: Dict[str, Any]) -> Dict[str, Any]:
    page_meta = page.get("pageMeta") or {}
    page_meta_data = page_meta.get("data") or {}
    final_url = page_meta_data.get("finalUrl") or page.get("finalUrl")

    cleaned_page = {
        "pageId": page.get("pageId") or page_meta_data.get("pageId"),
        "name": page.get("name"),
        "url": page.get("url"),
        "finalUrl": page.get("finalUrl"),
        "status": page.get("status"),
        "pageMeta": page_meta,
        "titlesAndHeadings": _clean_headings_block(page.get("titlesAndHeadings") or {}),
        "navigation": _clean_navigation_block(page.get("navigation") or {}, final_url),
        "textContent": _clean_text_content_block(page.get("textContent") or {}),
        "forms": _clean_forms_block(page.get("forms") or {}),
        "media": _clean_media_block(page.get("media") or {}),
    }

    cleaned_page["qualitySignals"] = _build_quality_signals(cleaned_page)
    return cleaned_page


def clean_person_a_output(raw_output: Dict[str, Any]) -> Dict[str, Any]:
    raw_pages = _safe_list(raw_output.get("pages"))
    cleaned_pages = [clean_person_a_page(page) for page in raw_pages if isinstance(page, dict)]

    return {
        "source": "person_a_cleaned",
        "generatedFrom": "src.audit.person_a_postprocess",
        "basedOn": raw_output.get("source"),
        "totalPages": len(cleaned_pages),
        "pages": cleaned_pages,
    }