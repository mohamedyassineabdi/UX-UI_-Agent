from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List
from urllib.parse import urlparse


GENERIC_UTILITY_WORDS = {
    "login", "log in", "sign in", "signin", "sign up", "signup", "register",
    "account", "profile", "cart", "checkout", "search", "menu",
    "contact", "help", "support", "faq", "wishlist", "compare",
    "filter", "filters", "sort", "close", "back", "next", "previous",
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
    "apply", "reset", "clear", "refine", "search"
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


def _is_social_domain(domain: str | None) -> bool:
    if not domain:
        return False
    return any(domain == social or domain.endswith("." + social) for social in SOCIAL_DOMAINS)


def _looks_like_count_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False

    words = cleaned.split()
    if len(words) > 6:
        return False

    digit_count = sum(ch.isdigit() for ch in cleaned)
    if digit_count == 0:
        return False

    lowered = cleaned.casefold()
    count_tokens = {
        "product", "products", "result", "results", "item", "items",
        "article", "articles", "page", "pages"
    }
    return any(token in lowered for token in count_tokens)


def _is_probably_short_utility_text(text: str) -> bool:
    lowered = _normalize_text(text)
    if not lowered:
        return False
    if lowered in GENERIC_UTILITY_WORDS:
        return True
    return len(lowered.split()) <= 2 and len(lowered) <= 14


def _looks_like_picker_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False

    word_count = len(cleaned.split())
    if word_count > 3 or len(cleaned) > 24:
        return False

    letters = sum(ch.isalpha() for ch in cleaned)
    digits = sum(ch.isdigit() for ch in cleaned)
    if letters < 3 or digits > 0:
        return False

    lowered = cleaned.casefold()
    if lowered in GENERIC_UTILITY_WORDS:
        return False
    if _looks_like_count_text(cleaned):
        return False

    return True


def _looks_like_locale_mass(items: List[Dict[str, Any]]) -> bool:
    texts = [_clean_text(item.get("text")) for item in items if _clean_text(item.get("text"))]
    if len(texts) < 8:
        return False

    picker_candidates = [t for t in texts if _looks_like_picker_text(t)]
    unique_candidates = _unique_strings(picker_candidates)
    return len(unique_candidates) >= 8


def _looks_like_price_text(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False

    currency_tokens = {
        "$", "€", "£", "dt", "tnd", "usd", "eur", "gbp", "aed",
        "mad", "cad", "dzd", "sar", "dh"
    }
    lowered = cleaned.casefold()

    if any(token in lowered for token in currency_tokens):
        return True

    digit_count = sum(ch.isdigit() for ch in cleaned)
    return digit_count >= 2 and any(sep in cleaned for sep in [".", ","])


def _is_probably_product_title(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    if _looks_like_count_text(cleaned):
        return False
    if _looks_like_picker_text(cleaned):
        return False
    word_count = len(cleaned.split())
    return len(cleaned) >= 18 or word_count >= 3


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

        if group_looks_like_locale_picker and text and _looks_like_picker_text(text):
            locale_or_picker_links.append(item)
            continue

        if href and current_domain and href_domain and href_domain != current_domain:
            external_links.append(item)
            useful_links.append(item)
            continue

        if text and _is_probably_short_utility_text(text):
            utility_links.append(item)
            continue

        useful_links.append(item)

    return {
        "useful": useful_links,
        "locale": locale_or_picker_links,
        "utility": utility_links,
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


def _is_filter_heading(text: str) -> bool:
    lowered = _normalize_text(text)
    if _looks_like_count_text(text):
        return True
    return any(token in lowered for token in FILTER_HEADING_TOKENS)


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

        if _is_filter_heading(text):
            filter_headings.append(base_entry)
            continue

        if _is_probably_short_utility_text(text) or _looks_like_picker_text(text):
            system_headings.append(base_entry)
            continue

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


def _split_text_noise(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = _clean_text_items(items)

    meaningful = []
    repeated_short = []
    count_like = []
    picker_like = []

    for item in cleaned:
        text = item["text"]

        if _looks_like_count_text(text):
            count_like.append(item)
            continue

        if _looks_like_picker_text(text):
            picker_like.append(item)
            continue

        if len(text.split()) <= 2 and len(text) <= 16:
            repeated_short.append(item)
            continue

        meaningful.append(item)

    return {
        "meaningful": meaningful,
        "repeatedShort": repeated_short,
        "countLike": count_like,
        "pickerLike": picker_like,
    }


def _clean_cta_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    unique_items = _unique_dicts_by_key(
        items,
        lambda item: (_normalize_text(item.get("kind")), _normalize_text(item.get("text"))),
    )

    useful = []
    short_utility = []
    picker_like = []

    for item in unique_items:
        text = _clean_text(item.get("text"))
        if not text:
            continue

        entry = {
            "text": text,
            "kind": _clean_text(item.get("kind")) or None,
        }

        if _looks_like_picker_text(text):
            picker_like.append(entry)
        elif _is_probably_short_utility_text(text):
            short_utility.append(entry)
        else:
            useful.append(entry)

    return {
        "useful": useful,
        "shortUtility": short_utility,
        "pickerLike": picker_like,
    }


def _extract_product_candidates(list_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    meaningful_texts = [item["text"] for item in list_items]
    prices = [text for text in meaningful_texts if _looks_like_price_text(text)]
    titles = [text for text in meaningful_texts if _is_probably_product_title(text)]

    # Only treat titles as product-like if the page also contains price-like text.
    if len(prices) == 0:
        return []

    output = [{"text": text} for text in titles]
    return _unique_dicts_by_key(output, lambda item: _normalize_text(item.get("text")))


def _clean_text_content_block(block: Dict[str, Any]) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}

    paragraphs = _split_text_noise(_safe_list(data.get("paragraphs")))
    list_items = _split_text_noise(_safe_list(data.get("listItems")))
    labels = _split_text_noise(_safe_list(data.get("labels")))
    long_blocks = _split_text_noise(_safe_list(data.get("longTextBlocks")))
    ctas = _clean_cta_items(_safe_list(data.get("ctaTexts")))

    product_like_texts = _extract_product_candidates(list_items["meaningful"])

    return {
        "status": "ok",
        "data": {
            "paragraphs": paragraphs["meaningful"],
            "paragraphsRepeatedShort": paragraphs["repeatedShort"],
            "paragraphsCountLike": paragraphs["countLike"],
            "paragraphsPickerLike": paragraphs["pickerLike"],
            "listItems": list_items["meaningful"],
            "listItemsRepeatedShort": list_items["repeatedShort"],
            "listItemsCountLike": list_items["countLike"],
            "listItemsPickerLike": list_items["pickerLike"],
            "labels": labels["meaningful"],
            "labelsRepeatedShort": labels["repeatedShort"],
            "labelsCountLike": labels["countLike"],
            "labelsPickerLike": labels["pickerLike"],
            "ctaTexts": ctas["useful"],
            "ctaTextsShortUtility": ctas["shortUtility"],
            "ctaTextsPickerLike": ctas["pickerLike"],
            "longTextBlocks": long_blocks["meaningful"],
            "longTextBlocksCountLike": long_blocks["countLike"],
            "longTextBlocksPickerLike": long_blocks["pickerLike"],
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


def _clean_forms_block(block: Dict[str, Any]) -> Dict[str, Any]:
    data = (block or {}).get("data") or {}
    forms = _safe_list(data.get("items"))

    cleaned_forms = []
    total_fields = 0
    visible_fields_count = 0
    hidden_fields_count = 0
    user_input_fields_count = 0

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

        cleaned_forms.append(
            {
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
        )

    return {
        "status": "ok",
        "data": {
            "items": cleaned_forms,
            "totalForms": len(cleaned_forms),
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
            "socialLinkCount": (navigation.get("counts") or {}).get("socialLinks", 0),
            "visibleFieldCount": forms.get("visibleFields") or 0,
            "hiddenFieldCount": forms.get("hiddenFields") or 0,
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