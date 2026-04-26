import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def normalize_url(raw_url, options=None):
    config = options or {}
    remove_hash = config.get("removeHash", True)
    remove_trailing_slash = config.get("removeTrailingSlash", False)
    remove_common_tracking_params = config.get("removeCommonTrackingParams", True)
    tracking_params = set(config.get("trackingParams", []))

    try:
        parsed = urlsplit(raw_url)
    except Exception as error:
        raise ValueError(f"Invalid URL: {raw_url}") from error

    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {raw_url}")

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    if remove_common_tracking_params:
        query_items = [(key, value) for key, value in query_items if key not in tracking_params]

    query_items.sort(key=lambda item: item[0])
    fragment = "" if remove_hash else parsed.fragment

    normalized = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            urlencode(query_items, doseq=True),
            fragment,
        )
    )

    if remove_trailing_slash:
        normalized = re.sub(r"/$", "", normalized)

    return normalized


def safe_normalize_url(raw_url, options=None):
    try:
        return normalize_url(raw_url, options)
    except Exception:
        return None


def slugify(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_text).strip()
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"_+", "_", slug)
    return slug.lower()


def deduplicate_pages(pages, normalization_options=None):
    seen = set()
    unique_pages = []
    duplicates = []

    for page in pages:
        normalized_url = normalize_url(page["url"], normalization_options or {})
        enriched_page = {
            **page,
            "normalizedUrl": normalized_url,
        }

        if normalized_url in seen:
            duplicates.append(enriched_page)
            continue

        seen.add(normalized_url)
        unique_pages.append(enriched_page)

    return {
        "uniquePages": unique_pages,
        "duplicates": duplicates,
    }


def get_origin_safe(raw_url):
    try:
        parsed = urlsplit(raw_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


def get_hostname_safe(raw_url):
    try:
        parsed = urlsplit(raw_url)
        return parsed.hostname.lower() if parsed.hostname else None
    except Exception:
        return None


def sanitize_path_segment(value, fallback="item", keep_dots=False):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r'[<>:"/\\|?*]', " ", ascii_text)

    if not keep_dots:
        cleaned = cleaned.replace(".", " ")

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")

    if not cleaned:
        cleaned = fallback

    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    return cleaned[:100]


def build_website_folder_name(raw_url):
    hostname = get_hostname_safe(raw_url)
    return sanitize_path_segment(hostname or "website", fallback="website", keep_dots=True)


def build_page_folder_name(page_name, fallback="page"):
    return sanitize_path_segment(page_name or fallback, fallback=fallback, keep_dots=False)
