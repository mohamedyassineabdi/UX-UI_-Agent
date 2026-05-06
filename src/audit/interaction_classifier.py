import re
from urllib.parse import urlparse


def normalize_for_match(value):
    return " ".join(str(value or "").lower().split()).strip()


def contains_any_keyword(haystack, keywords):
    for keyword in keywords:
        normalized_keyword = normalize_for_match(keyword)
        if normalized_keyword in haystack:
            return normalized_keyword
    return None


def contains_forbidden_href_keyword(href, keywords):
    parsed = urlparse(str(href or ""))
    path = parsed.path.lower()
    query = parsed.query.lower()
    blob = f"{path}?{query}" if query else path

    for keyword in keywords:
        normalized_keyword = normalize_for_match(keyword)
        if not normalized_keyword:
            continue
        if "/" in normalized_keyword or "_" in normalized_keyword or "-" in normalized_keyword:
            if normalized_keyword in blob:
                return normalized_keyword
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])", blob):
            return normalized_keyword
    return None


def build_search_blob(clickable):
    return normalize_for_match(
        " ".join(
            [
                clickable.get("text", ""),
                clickable.get("ariaLabel", ""),
                clickable.get("title", ""),
                clickable.get("name", ""),
                clickable.get("value", ""),
                clickable.get("id", ""),
                clickable.get("className", ""),
                clickable.get("onclick", ""),
            ]
        )
    )


def is_likely_safe_anchor(clickable):
    return clickable.get("tag") == "a" and bool(clickable.get("href"))


def classify_clickable(clickable, config):
    search_blob = build_search_blob(clickable)
    href = normalize_for_match(clickable.get("href"))

    if clickable.get("disabled"):
        return {
            "classification": "unknown",
            "reason": "element is disabled",
        }

    forbidden_keyword = contains_any_keyword(search_blob, config["classification"]["forbiddenKeywords"])
    if forbidden_keyword:
        return {
            "classification": "forbidden",
            "reason": f"matched forbidden keyword: {forbidden_keyword}",
        }

    forbidden_href_keyword = contains_forbidden_href_keyword(href, config["classification"]["forbiddenHrefKeywords"])
    if forbidden_href_keyword:
        return {
            "classification": "forbidden",
            "reason": f"matched forbidden href keyword: {forbidden_href_keyword}",
        }

    safe_keyword = contains_any_keyword(search_blob, config["classification"]["safeKeywords"])
    if safe_keyword:
        return {
            "classification": "safe",
            "reason": f"matched safe keyword: {safe_keyword}",
        }

    if is_likely_safe_anchor(clickable):
        return {
            "classification": "safe",
            "reason": "anchor with href and no forbidden signals",
        }

    if clickable.get("tag") == "button" or clickable.get("role") == "button":
        return {
            "classification": "unknown",
            "reason": "button-like element without clear safe/forbidden signal",
        }

    return {
        "classification": "unknown",
        "reason": "no rule matched",
    }


def classify_clickables(clickables, config):
    output = []
    for clickable in clickables:
        classification_result = classify_clickable(clickable, config)
        output.append(
            {
                **clickable,
                "classification": classification_result["classification"],
                "classificationReason": classification_result["reason"],
            }
        )
    return output


def summarize_classification(classified_clickables):
    summary = {"safe": 0, "forbidden": 0, "unknown": 0}

    for item in classified_clickables:
        if item.get("classification") == "safe":
            summary["safe"] += 1
        elif item.get("classification") == "forbidden":
            summary["forbidden"] += 1
        else:
            summary["unknown"] += 1

    return summary
