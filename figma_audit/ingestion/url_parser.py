from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse


# Supports all common Figma URL formats:
# - /file/
# - /design/
# - /proto/
# - /board/
FIGMA_FILE_REGEX = re.compile(r"/(file|design|proto|board)/([a-zA-Z0-9]+)")
ALLOWED_FIGMA_HOSTS = {"figma.com", "www.figma.com"}
NODE_QUERY_KEYS = ("node-id", "starting-point-node-id")


def _url_type(figma_url: str) -> str | None:
    parsed = urlparse(figma_url)
    match = FIGMA_FILE_REGEX.search(parsed.path)
    return match.group(1) if match else None


def _embedded_figma_url(figma_url: str) -> str | None:
    parsed = urlparse(figma_url)
    query_params = parse_qs(parsed.query)
    embedded_url = query_params.get("url", [None])[0]
    if not embedded_url:
        return None
    decoded_url = unquote(embedded_url)
    decoded_host = (urlparse(decoded_url).hostname or "").lower()
    if decoded_host in ALLOWED_FIGMA_HOSTS:
        return decoded_url
    return None


def extract_file_key(figma_url: str) -> str:
    """
    Extract the Figma file key from the URL.

    Example:
    https://www.figma.com/design/ABC123/My-Design?node-id=1-2
    -> ABC123
    """
    embedded_url = _embedded_figma_url(figma_url)
    if embedded_url:
        return extract_file_key(embedded_url)

    parsed = urlparse(figma_url)
    match = FIGMA_FILE_REGEX.search(parsed.path)

    if not match:
        raise ValueError("Invalid Figma URL: cannot extract file key.")

    return match.group(2)


def extract_node_id(figma_url: str) -> str | None:
    """
    Extract node-id from query parameters if present.

    Example:
    node-id=12-34 -> 12:34
    """
    embedded_url = _embedded_figma_url(figma_url)
    if embedded_url:
        return extract_node_id(embedded_url)

    parsed = urlparse(figma_url)
    query_params = parse_qs(parsed.query)

    node_id = None
    for key in NODE_QUERY_KEYS:
        node_id = query_params.get(key, [None])[0]
        if node_id:
            break

    if node_id:
        return normalize_node_id(node_id)

    return None


def normalize_node_id(node_id: str) -> str:
    """
    Normalize node id from URL format to API format.

    Example:
    12-34 -> 12:34
    """
    return unquote(node_id).replace("-", ":")


def parse_figma_url(figma_url: str) -> dict[str, str | None]:
    """
    Main entry point.

    Returns:
    {
        "file_key": "...",
        "node_id": "... or None"
    }
    """
    if not figma_url:
        raise ValueError("Invalid Figma URL provided.")

    parsed = urlparse(figma_url)
    host = (parsed.hostname or "").lower()

    if host not in ALLOWED_FIGMA_HOSTS:
        raise ValueError("Invalid Figma URL provided.")

    embedded_url = _embedded_figma_url(figma_url)
    if embedded_url:
        return parse_figma_url(embedded_url)

    file_key = extract_file_key(figma_url)
    node_id = extract_node_id(figma_url)

    return {
        "file_key": file_key,
        "node_id": node_id,
        "url_type": _url_type(figma_url),
    }
