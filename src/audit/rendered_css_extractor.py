from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple


IMPORTANT_STYLE_PROPS = [
    "display",
    "position",
    "visibility",
    "opacity",
    "zIndex",
    "overflow",
    "overflowX",
    "overflowY",
    "boxSizing",
    "width",
    "height",
    "minWidth",
    "minHeight",
    "maxWidth",
    "maxHeight",
    "marginTop",
    "marginRight",
    "marginBottom",
    "marginLeft",
    "paddingTop",
    "paddingRight",
    "paddingBottom",
    "paddingLeft",
    "color",
    "backgroundColor",
    "backgroundImage",
    "backgroundSize",
    "backgroundPosition",
    "fontFamily",
    "fontSize",
    "fontWeight",
    "fontStyle",
    "lineHeight",
    "letterSpacing",
    "textAlign",
    "textTransform",
    "textDecoration",
    "whiteSpace",
    "wordBreak",
    "borderTopWidth",
    "borderRightWidth",
    "borderBottomWidth",
    "borderLeftWidth",
    "borderTopColor",
    "borderRightColor",
    "borderBottomColor",
    "borderLeftColor",
    "borderTopStyle",
    "borderRightStyle",
    "borderBottomStyle",
    "borderLeftStyle",
    "borderTopLeftRadius",
    "borderTopRightRadius",
    "borderBottomRightRadius",
    "borderBottomLeftRadius",
    "boxShadow",
    "outline",
    "outlineColor",
    "outlineWidth",
    "outlineStyle",
    "cursor",
    "gap",
    "rowGap",
    "columnGap",
    "alignItems",
    "justifyContent",
    "alignSelf",
    "justifySelf",
    "placeItems",
    "placeContent",
    "flexDirection",
    "flexWrap",
    "flexGrow",
    "flexShrink",
    "order",
    "gridTemplateColumns",
    "gridTemplateRows",
    "gridColumn",
    "gridRow",
    "transform",
    "transition",
    "pointerEvents",
    "objectFit",
]

ROOT_TAGS = {"html", "body"}
INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "summary", "details"}
LANDMARK_TAGS = {"header", "nav", "main", "footer", "aside", "form", "section", "article"}
LANDMARK_ROLES = {
    "banner",
    "navigation",
    "main",
    "contentinfo",
    "complementary",
    "form",
    "search",
    "dialog",
    "alert",
    "tab",
    "tabpanel",
    "menu",
}
BUTTON_INPUT_TYPES = {"button", "submit", "reset"}

MEANINGFUL_AUDIT_TYPES = {
    "heading",
    "button",
    "button-ghost",
    "cta-link",
    "link",
    "nav-link",
    "navigation",
    "form",
    "input",
    "select",
    "textarea",
    "dialog",
    "card",
    "badge",
    "section",
    "hero",
    "table",
    "text-block",
}

SKIP_LINK_TERMS = {
    "skip to content",
    "skip to main content",
    "skip navigation",
    "ignorer et passer au contenu",
    "aller au contenu",
    "passer au contenu",
}

NAV_TERMS = {
    "home",
    "catalog",
    "products",
    "pricing",
    "about",
    "contact",
    "features",
    "services",
    "blog",
    "docs",
    "documentation",
    "support",
    "shop",
    "store",
    "login",
    "sign in",
    "account",
    "cart",
    "accueil",
    "catalogue",
    "produits",
    "tarifs",
    "contact",
    "services",
    "documentation",
    "boutique",
    "connexion",
    "panier",
}

CARD_CLASS_HINTS = {"card", "tile", "panel", "product", "pricing", "collection", "item"}
SECTION_CLASS_HINTS = {"section", "hero", "banner", "content", "container", "footer", "header"}
HEADING_CLASS_HINTS = {"title", "heading", "headline", "subtitle", "hero__title", "section__title"}


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def to_bool(value: Any) -> bool:
    return bool(value)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_color(value: Any) -> str:
    text = clean_text(value)
    lowered = text.lower()
    if lowered in {"", "transparent", "rgba(0, 0, 0, 0)", "initial", "inherit", "unset", "none"}:
        return ""
    return text


def parse_px(value: Any) -> float | None:
    text = clean_text(value).lower()
    if not text or text in {"normal", "auto", "none"}:
        return None
    if text.endswith("px"):
        text = text[:-2].strip()
    try:
        return float(text)
    except Exception:
        return None


def round_px_token(value: Any) -> str:
    parsed = parse_px(value)
    if parsed is None:
        return clean_text(value)
    return f"{int(round(parsed))}px"


def clamp_score(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))


def is_non_empty_rect(rect: Dict[str, Any]) -> bool:
    return safe_float(rect.get("width")) > 0 and safe_float(rect.get("height")) > 0


def unique_text_list(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        text = clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def pick_top_values(values: List[str], limit: int = 12) -> List[str]:
    cleaned = [clean_text(v) for v in values if clean_text(v)]
    if not cleaned:
        return []
    counts = Counter(cleaned)
    return [value for value, _ in counts.most_common(limit)]


def get_border_radius_token(styles: Dict[str, Any]) -> str:
    values = []
    for key in [
        "borderTopLeftRadius",
        "borderTopRightRadius",
        "borderBottomRightRadius",
        "borderBottomLeftRadius",
    ]:
        parsed = parse_px(styles.get(key))
        if parsed is not None:
            values.append(int(round(parsed)))
    if not values:
        return ""
    if len(set(values)) == 1:
        return f"{values[0]}px"
    return "/".join(f"{v}px" for v in values[:4])


def get_padding_signature(styles: Dict[str, Any]) -> str:
    return " ".join(
        round_px_token(styles.get(key)) or "0px"
        for key in ["paddingTop", "paddingRight", "paddingBottom", "paddingLeft"]
    )


def get_margin_signature(styles: Dict[str, Any]) -> str:
    return " ".join(
        round_px_token(styles.get(key)) or "0px"
        for key in ["marginTop", "marginRight", "marginBottom", "marginLeft"]
    )


def get_border_signature(styles: Dict[str, Any]) -> str:
    width = round_px_token(styles.get("borderTopWidth")) or "0px"
    style = clean_text(styles.get("borderTopStyle")) or "none"
    color = normalize_color(styles.get("borderTopColor")) or "transparent"
    return f"{width} {style} {color}"


def get_shadow_token(styles: Dict[str, Any]) -> str:
    shadow = clean_text(styles.get("boxShadow"))
    return "" if shadow.lower() in {"", "none"} else shadow


def get_layout_mode(styles: Dict[str, Any]) -> str:
    display = clean_text(styles.get("display")).lower()
    if display in {"flex", "inline-flex"}:
        return "flex"
    if display in {"grid", "inline-grid"}:
        return "grid"
    if display in {"block", "inline-block", "inline"}:
        return display
    return display or "unknown"


def get_text_density(text: str) -> str:
    n = len(clean_text(text))
    if n == 0:
        return "none"
    if n <= 18:
        return "short"
    if n <= 80:
        return "medium"
    return "long"


def extract_class_tokens(class_name: str) -> List[str]:
    return [token.strip().lower() for token in class_name.split() if token.strip()][:24]


def is_skip_link_text(text: str) -> bool:
    return clean_text(text).lower() in SKIP_LINK_TERMS


def looks_like_nav_text(text: str) -> bool:
    return clean_text(text).lower() in NAV_TERMS


def approximate_luminance_from_rgb_string(color: str) -> float | None:
    text = clean_text(color).lower()
    if not text.startswith("rgb"):
        return None
    try:
        values = text[text.find("(") + 1:text.find(")")].split(",")
        if len(values) < 3:
            return None
        r = int(float(values[0].strip())) / 255.0
        g = int(float(values[1].strip())) / 255.0
        b = int(float(values[2].strip())) / 255.0

        def channel(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        r_l = channel(r)
        g_l = channel(g)
        b_l = channel(b)
        return 0.2126 * r_l + 0.7152 * g_l + 0.0722 * b_l
    except Exception:
        return None


def contrast_ratio(foreground: str, background: str) -> float | None:
    fg_l = approximate_luminance_from_rgb_string(foreground)
    bg_l = approximate_luminance_from_rgb_string(background)
    if fg_l is None or bg_l is None:
        return None
    lighter = max(fg_l, bg_l)
    darker = min(fg_l, bg_l)
    return (lighter + 0.05) / (darker + 0.05)


def likely_interactive(element: Dict[str, Any]) -> bool:
    tag = clean_text(element.get("tag")).lower()
    role = clean_text(element.get("role")).lower()
    styles = element.get("styles") or {}
    return (
        tag in INTERACTIVE_TAGS
        or role in {"button", "link", "tab", "menuitem", "checkbox", "radio", "switch"}
        or clean_text(styles.get("cursor")).lower() == "pointer"
        or bool(clean_text(element.get("href")))
    )


def is_hidden_but_interactive(element: Dict[str, Any]) -> bool:
    return not element.get("visible") and likely_interactive(element)


def get_depth_score(element: Dict[str, Any]) -> int:
    return safe_int(element.get("domDepth"), 0)


def element_area(element: Dict[str, Any]) -> float:
    rect = element.get("rect") or {}
    return safe_float(rect.get("width")) * safe_float(rect.get("height"))


def is_giant_shell(element: Dict[str, Any]) -> bool:
    tag = clean_text(element.get("tag")).lower()
    rect = element.get("rect") or {}
    width = safe_float(rect.get("width"))
    height = safe_float(rect.get("height"))
    class_name = clean_text(element.get("className")).lower()

    if width >= 900 and height >= 180:
        if tag in {"header", "main", "section", "div"}:
            return True
        if "header-wrapper" in class_name or "shopify-section" in class_name:
            return True
    return False


def unique_by_fingerprint(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        rect = item.get("rect") or {}
        styles = item.get("styles") or {}
        fingerprint = "||".join(
            [
                clean_text(item.get("tag")),
                clean_text(item.get("role")),
                clean_text(item.get("id")),
                clean_text(item.get("name")),
                clean_text(item.get("type")),
                clean_text(item.get("href")),
                clean_text(item.get("text"))[:220],
                clean_text(item.get("label"))[:120],
                clean_text(item.get("xpathHint")),
                clean_text(rect.get("x")),
                clean_text(rect.get("y")),
                clean_text(rect.get("width")),
                clean_text(rect.get("height")),
                clean_text(styles.get("fontSize")),
                clean_text(styles.get("fontWeight")),
                clean_text(styles.get("color")),
                clean_text(styles.get("backgroundColor")),
            ]
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(item)
    return out


def looks_like_visual_heading(element: Dict[str, Any]) -> bool:
    text = clean_text(element.get("text"))
    tag = clean_text(element.get("tag")).lower()
    class_name = clean_text(element.get("className")).lower()
    styles = element.get("styles") or {}
    rect = element.get("rect") or {}

    font_size = parse_px(styles.get("fontSize")) or 0
    font_weight_raw = clean_text(styles.get("fontWeight"))
    line_height = parse_px(styles.get("lineHeight")) or 0
    width = safe_float(rect.get("width"))
    height = safe_float(rect.get("height"))
    text_len = len(text)
    child_count = safe_int(element.get("childElementCount"), 0)
    visible_text_descendants = safe_int(element.get("visibleTextDescendantCount"), 0)
    heading_like_hint = to_bool(element.get("headingLikeHint"))

    try:
        font_weight = int(font_weight_raw)
    except Exception:
        font_weight = 700 if font_weight_raw.lower() in {"bold", "bolder"} else 400

    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return True

    if heading_like_hint:
        return True

    if not text or text_len > 140:
        return False
    if is_skip_link_text(text):
        return False
    if width < 50 or height < 14:
        return False
    if child_count > 10 and visible_text_descendants > 4:
        return False

    class_tokens = extract_class_tokens(class_name)
    if any(token in HEADING_CLASS_HINTS for token in class_tokens):
        if font_size >= 16 or font_weight >= 600:
            return True

    if font_size >= 30 and font_weight >= 600 and text_len <= 90:
        return True
    if font_size >= 24 and font_weight >= 600 and text_len <= 100:
        return True
    if font_size >= 20 and font_weight >= 700 and text_len <= 110:
        return True
    if font_size >= 18 and font_weight >= 700 and line_height and line_height <= font_size * 1.45 and text_len <= 80:
        return True

    return False


def looks_like_card_container(element: Dict[str, Any]) -> bool:
    if is_giant_shell(element):
        return False

    tag = clean_text(element.get("tag")).lower()
    class_name = clean_text(element.get("className")).lower()
    class_tokens = extract_class_tokens(class_name)
    rect = element.get("rect") or {}
    styles = element.get("styles") or {}
    width = safe_float(rect.get("width"))
    height = safe_float(rect.get("height"))
    border = get_border_signature(styles)
    shadow = get_shadow_token(styles)
    radius = get_border_radius_token(styles)
    bg = normalize_color(styles.get("backgroundColor"))
    child_count = safe_int(element.get("childElementCount"), 0)
    interactive_descendants = safe_int(element.get("visibleInteractiveDescendantCount"), 0)
    text_descendants = safe_int(element.get("visibleTextDescendantCount"), 0)

    if any(token in CARD_CLASS_HINTS for token in class_tokens):
        return True

    if tag in {"section", "article", "div", "li"} and 120 <= width <= 700 and 60 <= height <= 700:
        if (shadow or radius or ("0px none transparent" not in border and bg)) and child_count >= 2:
            return True
        if interactive_descendants >= 1 and text_descendants >= 1 and child_count >= 2 and bg:
            return True

    return False


def looks_like_section_container(element: Dict[str, Any]) -> bool:
    tag = clean_text(element.get("tag")).lower()
    class_name = clean_text(element.get("className")).lower()
    class_tokens = extract_class_tokens(class_name)
    rect = element.get("rect") or {}
    width = safe_float(rect.get("width"))
    height = safe_float(rect.get("height"))
    text = clean_text(element.get("text"))
    child_count = safe_int(element.get("childElementCount"), 0)
    visible_text_descendants = safe_int(element.get("visibleTextDescendantCount"), 0)
    visible_interactive_descendants = safe_int(element.get("visibleInteractiveDescendantCount"), 0)

    if tag in {"section", "article", "main", "aside"} and width >= 220 and height >= 70:
        return True

    if any(token in SECTION_CLASS_HINTS for token in class_tokens) and width >= 220 and height >= 60:
        return True

    if tag == "div" and width >= 240 and height >= 70 and child_count >= 2:
        if visible_text_descendants >= 1 or visible_interactive_descendants >= 1:
            if len(text) >= 10:
                return True

    return False


def is_noise_element(element: Dict[str, Any]) -> bool:
    tag = clean_text(element.get("tag")).lower()
    role = clean_text(element.get("role")).lower()
    text = clean_text(element.get("text"))
    label = clean_text(element.get("label"))
    aria_label = clean_text(element.get("ariaLabel"))
    class_name = clean_text(element.get("className")).lower()
    styles = element.get("styles") or {}
    rect = element.get("rect") or {}

    if tag == "option":
        return True
    if tag in ROOT_TAGS:
        return True
    if clean_text(styles.get("display")).lower() == "none":
        return True
    if clean_text(styles.get("visibility")).lower() == "hidden" and not likely_interactive(element):
        return True
    if not is_non_empty_rect(rect) and not likely_interactive(element):
        return True

    meaningful_text = text or label or aria_label
    if (
        not meaningful_text
        and tag not in {
            "input", "select", "textarea", "form", "header", "nav", "main", "footer",
            "section", "article", "aside", "table", "dialog", "button", "a", "span", "p", "div", "li"
        }
        and role not in {
            "button", "dialog", "navigation", "main", "form", "alert", "tab",
            "tabpanel", "menu", "link"
        }
        and not looks_like_card_container(element)
        and not looks_like_section_container(element)
    ):
        return True

    noisy_tokens = {"sr-only", "visually-hidden", "screen-reader", "captcha", "cookie-banner-backdrop"}
    if any(token in class_name for token in noisy_tokens) and not meaningful_text:
        return True

    return False


def detect_semantic_type(element: Dict[str, Any]) -> str:
    tag = clean_text(element.get("tag")).lower()
    role = clean_text(element.get("role")).lower()
    text = clean_text(element.get("text"))
    class_name = clean_text(element.get("className")).lower()
    href = clean_text(element.get("href"))
    styles = element.get("styles") or {}
    rect = element.get("rect") or {}
    width = safe_float(rect.get("width"))
    height = safe_float(rect.get("height"))
    closest_landmark = element.get("closestLandmark") or {}
    closest_landmark_tag = clean_text(closest_landmark.get("tag")).lower()
    closest_landmark_role = clean_text(closest_landmark.get("role")).lower()
    nav_ancestor_depth = safe_int(element.get("navAncestorDepth"), 0)

    if tag in ROOT_TAGS:
        return "root"

    if looks_like_visual_heading(element):
        return "heading"

    if tag == "button" or role == "button" or clean_text(element.get("type")).lower() in BUTTON_INPUT_TYPES:
        if is_skip_link_text(text):
            return "link"
        bg = normalize_color(styles.get("backgroundColor"))
        if bg:
            return "button"
        return "button-ghost"

    if tag == "input":
        input_type = clean_text(element.get("type")).lower()
        if input_type in {"checkbox", "radio"}:
            return input_type
        if input_type in BUTTON_INPUT_TYPES:
            return "button"
        return "input"

    if tag == "select":
        return "select"

    if tag == "textarea":
        return "textarea"

    if tag == "a" or role == "link":
        if is_skip_link_text(text):
            return "link"

        if (
            closest_landmark_tag == "nav"
            or closest_landmark_role in {"navigation", "menu"}
            or nav_ancestor_depth > 0
            or looks_like_nav_text(text)
        ):
            return "nav-link"

        if width >= 80 and height >= 28:
            bg = normalize_color(styles.get("backgroundColor"))
            if bg or "btn" in class_name or "button" in class_name or "cta" in class_name:
                return "cta-link"

        if href:
            return "link"

    if tag == "nav" or role in {"navigation", "menu"}:
        return "navigation"

    if tag == "dialog" or role == "dialog":
        return "dialog"

    if tag == "form" or role == "form":
        return "form"

    if tag == "table":
        return "table"

    if any(token in class_name for token in ["hero", "banner", "masthead"]):
        return "hero"

    if looks_like_card_container(element):
        return "card"

    if any(token in class_name for token in ["badge", "chip", "tag", "pill"]):
        return "badge"

    if looks_like_section_container(element):
        return "section"

    if get_text_density(text) in {"medium", "long"} and tag in {"p", "div", "span", "li"}:
        return "text-block"

    if likely_interactive(element):
        return "interactive"

    return "generic"


def detect_component_variant(element: Dict[str, Any], semantic_type: str) -> str:
    text = clean_text(element.get("text")).lower()
    class_name = clean_text(element.get("className")).lower()
    href = clean_text(element.get("href")).lower()
    styles = element.get("styles") or {}

    bg = normalize_color(styles.get("backgroundColor"))
    border = get_border_signature(styles)
    shadow = get_shadow_token(styles)

    if semantic_type in {"button", "button-ghost", "cta-link"}:
        if is_skip_link_text(text):
            return "utility-accessibility"
        if any(keyword in text for keyword in ["buy", "book", "start", "sign up", "get started", "contact", "try", "request", "demo"]):
            return "primary"
        if not bg and "0px none transparent" not in border:
            return "secondary"
        if bg and shadow:
            return "elevated"
        if bg:
            return "filled"
        return "text"

    if semantic_type == "nav-link":
        return "navigation"

    if semantic_type == "link":
        if is_skip_link_text(text):
            return "utility-accessibility"
        if any(keyword in href for keyword in ["login", "signin", "register", "signup"]):
            return "auth"
        if "footer" in class_name:
            return "footer"
        return "text"

    if semantic_type == "heading":
        tag = clean_text(element.get("tag")).lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return tag
        font_size = parse_px(styles.get("fontSize")) or 0
        if font_size >= 36:
            return "display"
        if font_size >= 28:
            return "section-title-lg"
        if font_size >= 22:
            return "section-title-md"
        return "section-title-sm"

    if semantic_type == "card":
        if "pricing" in class_name or "price" in class_name:
            return "pricing"
        if "product" in class_name:
            return "product"
        if shadow:
            return "elevated"
        return "flat"

    if semantic_type == "section":
        if "hero" in class_name or "banner" in class_name:
            return "hero"
        if "footer" in class_name:
            return "footer-section"
        return "content-section"

    if semantic_type == "input":
        input_type = clean_text(element.get("type")).lower()
        return input_type or "text"

    if semantic_type in {"select", "textarea", "dialog", "navigation", "form", "table", "hero", "badge"}:
        return semantic_type

    return "default"


def build_style_signature(element: Dict[str, Any]) -> str:
    styles = element.get("styles") or {}
    return " | ".join(
        [
            detect_semantic_type(element),
            round_px_token(styles.get("fontSize")) or "",
            clean_text(styles.get("fontWeight")) or "",
            normalize_color(styles.get("color")) or "",
            normalize_color(styles.get("backgroundColor")) or "",
            get_padding_signature(styles),
            get_border_radius_token(styles),
            get_border_signature(styles),
            get_shadow_token(styles),
            clean_text(styles.get("textTransform")).lower(),
            clean_text(styles.get("display")).lower(),
        ]
    )


def build_component_group_id(element: Dict[str, Any]) -> str:
    semantic_type = detect_semantic_type(element)
    variant = detect_component_variant(element, semantic_type)
    styles = element.get("styles") or {}
    return " || ".join(
        [
            semantic_type,
            variant,
            round_px_token(styles.get("fontSize")) or "",
            get_border_radius_token(styles),
            normalize_color(styles.get("backgroundColor")) or "",
            get_border_signature(styles),
        ]
    )


def should_keep_as_audit_element(element: Dict[str, Any], semantic_type: str) -> bool:
    text = clean_text(element.get("text"))
    variant = detect_component_variant(element, semantic_type)

    if semantic_type in {"root", "generic", "interactive"}:
        return False
    if semantic_type in {"button", "button-ghost", "cta-link", "link"} and variant == "utility-accessibility":
        return False
    if semantic_type == "text-block" and len(text) < 8:
        return False
    return True


def build_audit_elements(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for element in elements:
        if is_noise_element(element):
            continue

        semantic_type = detect_semantic_type(element)
        if not should_keep_as_audit_element(element, semantic_type):
            continue

        enriched = dict(element)
        enriched["semanticType"] = semantic_type
        enriched["componentVariant"] = detect_component_variant(element, semantic_type)
        enriched["styleSignature"] = build_style_signature(element)
        enriched["componentGroupId"] = build_component_group_id(element)
        enriched["layoutMode"] = get_layout_mode(element.get("styles") or {})
        out.append(enriched)

    return out


def promote_child_interactives(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Promote real visible child anchors/buttons inside visible header/nav/section wrappers.
    """
    by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for element in elements:
        parent = clean_text(element.get("parentXPathHint"))
        if parent:
            by_parent[parent].append(element)

    promoted: List[Dict[str, Any]] = []
    existing_keys = {
        (
            clean_text(e.get("tag")),
            clean_text(e.get("href")),
            clean_text(e.get("text")),
            clean_text(e.get("xpathHint")),
        )
        for e in elements
    }

    for element in elements:
        tag = clean_text(element.get("tag")).lower()
        role = clean_text(element.get("role")).lower()
        semantic_type = clean_text(element.get("semanticType"))
        class_name = clean_text(element.get("className")).lower()

        if semantic_type not in {"section", "navigation"} and tag not in {"header", "nav"}:
            continue

        if not (
            tag in {"header", "nav"}
            or "header" in class_name
            or "nav" in class_name
            or role in {"navigation", "menu"}
        ):
            continue

        children = by_parent.get(clean_text(element.get("xpathHint")), [])
        for child in children:
            child_tag = clean_text(child.get("tag")).lower()
            child_text = clean_text(child.get("text"))
            child_href = clean_text(child.get("href"))
            child_visible = to_bool(child.get("visible"))
            child_styles = child.get("styles") or {}
            child_rect = child.get("rect") or {}

            if not child_visible:
                continue
            if child_tag not in {"a", "button"}:
                continue
            if not child_text and not child_href:
                continue
            if is_skip_link_text(child_text):
                continue
            if safe_float(child_rect.get("width")) < 20 or safe_float(child_rect.get("height")) < 12:
                continue

            promoted_child = dict(child)
            if child_tag == "a":
                if looks_like_nav_text(child_text) or safe_int(child.get("navAncestorDepth"), 0) > 0:
                    promoted_child["semanticType"] = "nav-link"
                    promoted_child["componentVariant"] = "navigation"
                elif normalize_color(child_styles.get("backgroundColor")):
                    promoted_child["semanticType"] = "cta-link"
                    promoted_child["componentVariant"] = "filled"
                else:
                    promoted_child["semanticType"] = "link"
                    promoted_child["componentVariant"] = "text"
            elif child_tag == "button":
                promoted_child["semanticType"] = "button"
                promoted_child["componentVariant"] = "filled" if normalize_color(child_styles.get("backgroundColor")) else "text"

            promoted_child["styleSignature"] = build_style_signature(promoted_child)
            promoted_child["componentGroupId"] = build_component_group_id(promoted_child)
            promoted_child["layoutMode"] = get_layout_mode(promoted_child.get("styles") or {})

            key = (
                clean_text(promoted_child.get("tag")),
                clean_text(promoted_child.get("href")),
                clean_text(promoted_child.get("text")),
                clean_text(promoted_child.get("xpathHint")),
            )
            if key not in existing_keys:
                existing_keys.add(key)
                promoted.append(promoted_child)

    return elements + promoted


def suppress_wrapper_noise(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove large parent wrappers when a more meaningful child already exists.
    """
    children_by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_xpath: Dict[str, Dict[str, Any]] = {}

    for element in elements:
        xpath = clean_text(element.get("xpathHint"))
        parent = clean_text(element.get("parentXPathHint"))
        if xpath:
            by_xpath[xpath] = element
        if parent:
            children_by_parent[parent].append(element)

    kept: List[Dict[str, Any]] = []

    for element in elements:
        semantic_type = clean_text(element.get("semanticType"))
        xpath = clean_text(element.get("xpathHint"))
        rect = element.get("rect") or {}
        width = safe_float(rect.get("width"))
        height = safe_float(rect.get("height"))
        text = clean_text(element.get("text"))
        tag = clean_text(element.get("tag")).lower()

        children = children_by_parent.get(xpath, [])

        meaningful_child_count = 0
        meaningful_interactive_children = 0
        heading_children = 0
        text_children = 0

        for child in children:
            child_type = clean_text(child.get("semanticType"))
            if child_type in {"heading", "nav-link", "link", "button", "button-ghost", "cta-link", "card", "text-block"}:
                meaningful_child_count += 1
            if child_type in {"nav-link", "link", "button", "button-ghost", "cta-link"}:
                meaningful_interactive_children += 1
            if child_type == "heading":
                heading_children += 1
            if child_type == "text-block":
                text_children += 1

        is_large_wrapper = (
            semantic_type in {"section", "card", "text-block"}
            and width >= 400
            and height >= 40
        )

        repeated_announcement = "announcement" in clean_text(element.get("className")).lower()

        suppress = False

        if repeated_announcement and text_children >= 1:
            suppress = True

        if is_large_wrapper and meaningful_child_count >= 2:
            suppress = True

        if semantic_type == "card" and is_giant_shell(element):
            suppress = True

        if semantic_type == "section" and meaningful_interactive_children >= 2 and tag in {"div", "section", "header"}:
            suppress = True

        if semantic_type == "text-block" and text_children >= 1 and len(text) > 0:
            suppress = True

        if semantic_type == "section" and heading_children >= 1 and text_children >= 1:
            suppress = True

        if not suppress:
            kept.append(element)

    return unique_by_fingerprint(kept)


def build_component_entry(element: Dict[str, Any]) -> Dict[str, Any]:
    styles = element.get("styles") or {}
    return {
        "tag": clean_text(element.get("tag")),
        "text": clean_text(element.get("text"))[:240],
        "role": clean_text(element.get("role")),
        "id": clean_text(element.get("id")),
        "className": clean_text(element.get("className")),
        "name": clean_text(element.get("name")),
        "type": clean_text(element.get("type")),
        "href": clean_text(element.get("href")),
        "label": clean_text(element.get("label")),
        "placeholder": clean_text(element.get("placeholder")),
        "required": to_bool(element.get("required")),
        "disabled": to_bool(element.get("disabled")),
        "readOnly": to_bool(element.get("readOnly")),
        "checked": to_bool(element.get("checked")),
        "visible": to_bool(element.get("visible")),
        "semanticType": clean_text(element.get("semanticType")),
        "componentVariant": clean_text(element.get("componentVariant")),
        "styleSignature": clean_text(element.get("styleSignature")),
        "componentGroupId": clean_text(element.get("componentGroupId")),
        "layoutMode": clean_text(element.get("layoutMode")),
        "rect": element.get("rect") or {},
        "styles": styles,
        "tokens": {
            "fontSize": round_px_token(styles.get("fontSize")),
            "fontWeight": clean_text(styles.get("fontWeight")),
            "textColor": normalize_color(styles.get("color")),
            "backgroundColor": normalize_color(styles.get("backgroundColor")),
            "padding": get_padding_signature(styles),
            "margin": get_margin_signature(styles),
            "radius": get_border_radius_token(styles),
            "border": get_border_signature(styles),
            "shadow": get_shadow_token(styles),
        },
        "xpathHint": clean_text(element.get("xpathHint")),
        "closestLandmark": element.get("closestLandmark") or {},
    }


def collect_style_summary(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    text_colors: List[str] = []
    background_colors: List[str] = []
    border_colors: List[str] = []
    font_families: List[str] = []
    font_sizes: List[str] = []
    font_weights: List[str] = []
    line_heights: List[str] = []
    letter_spacings: List[str] = []
    border_radii: List[str] = []
    shadows: List[str] = []
    spacing_values: List[str] = []
    border_styles: List[str] = []
    layout_modes: List[str] = []

    for element in elements:
        styles = element.get("styles") or {}

        color = normalize_color(styles.get("color"))
        if color:
            text_colors.append(color)

        background_color = normalize_color(styles.get("backgroundColor"))
        if background_color:
            background_colors.append(background_color)

        for width_key, color_key in [
            ("borderTopWidth", "borderTopColor"),
            ("borderRightWidth", "borderRightColor"),
            ("borderBottomWidth", "borderBottomColor"),
            ("borderLeftWidth", "borderLeftColor"),
        ]:
            border_width = parse_px(styles.get(width_key))
            border_color = normalize_color(styles.get(color_key))
            if border_width is not None and border_width > 0 and border_color:
                border_colors.append(border_color)

        font_family = clean_text(styles.get("fontFamily"))
        if font_family and font_family.lower() not in {"none", "normal", "auto"}:
            font_families.append(font_family)

        font_size = round_px_token(styles.get("fontSize"))
        if font_size:
            font_sizes.append(font_size)

        font_weight = clean_text(styles.get("fontWeight"))
        if font_weight and font_weight.lower() not in {"none", "normal", "auto"}:
            font_weights.append(font_weight)

        line_height = round_px_token(styles.get("lineHeight"))
        if line_height:
            line_heights.append(line_height)

        letter_spacing_raw = parse_px(styles.get("letterSpacing"))
        if letter_spacing_raw is not None:
            letter_spacings.append(f"{int(round(letter_spacing_raw))}px")

        radius = get_border_radius_token(styles)
        if radius:
            border_radii.append(radius)

        shadow = get_shadow_token(styles)
        if shadow:
            shadows.append(shadow)

        border_signature = get_border_signature(styles)
        if "0px none transparent" not in border_signature:
            border_styles.append(border_signature)

        layout_mode = get_layout_mode(styles)
        if layout_mode:
            layout_modes.append(layout_mode)

        for key in [
            "marginTop", "marginRight", "marginBottom", "marginLeft",
            "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
            "gap", "rowGap", "columnGap",
        ]:
            value = parse_px(styles.get(key))
            if value is None:
                continue
            if 4 <= value <= 160:
                spacing_values.append(f"{int(round(value))}px")

    return {
        "colors": {
            "text": pick_top_values(text_colors, 16),
            "backgrounds": pick_top_values(background_colors, 16),
            "borders": pick_top_values(border_colors, 16),
            "counts": {
                "text": len(set(text_colors)),
                "backgrounds": len(set(background_colors)),
                "borders": len(set(border_colors)),
            },
        },
        "typography": {
            "fontFamilies": pick_top_values(font_families, 12),
            "fontSizes": pick_top_values(font_sizes, 20),
            "fontWeights": pick_top_values(font_weights, 12),
            "lineHeights": pick_top_values(line_heights, 20),
            "letterSpacings": pick_top_values(letter_spacings, 12),
            "counts": {
                "fontFamilies": len(set(font_families)),
                "fontSizes": len(set(font_sizes)),
                "fontWeights": len(set(font_weights)),
                "lineHeights": len(set(line_heights)),
                "letterSpacings": len(set(letter_spacings)),
            },
        },
        "shape": {
            "borderRadii": pick_top_values(border_radii, 16),
            "shadows": pick_top_values(shadows, 16),
            "borderStyles": pick_top_values(border_styles, 16),
            "counts": {
                "borderRadii": len(set(border_radii)),
                "shadows": len(set(shadows)),
                "borderStyles": len(set(border_styles)),
            },
        },
        "spacing": {
            "values": pick_top_values(spacing_values, 24),
            "counts": {"values": len(set(spacing_values))},
        },
        "layout": {
            "modes": pick_top_values(layout_modes, 12),
            "counts": {"modes": len(set(layout_modes))},
        },
    }


def build_landmarks(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for element in elements:
        tag = clean_text(element.get("tag")).lower()
        role = clean_text(element.get("role")).lower()
        if tag not in LANDMARK_TAGS and role not in LANDMARK_ROLES:
            continue
        out.append(
            {
                "tag": tag,
                "role": role,
                "text": clean_text(element.get("text"))[:180],
                "id": clean_text(element.get("id")),
                "className": clean_text(element.get("className")),
                "semanticType": clean_text(element.get("semanticType")),
                "rect": element.get("rect") or {},
                "xpathHint": clean_text(element.get("xpathHint")),
            }
        )
    return out[:100]


def build_component_inventory(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    inventory: Dict[str, List[Dict[str, Any]]] = {
        "buttons": [],
        "links": [],
        "navLinks": [],
        "inputs": [],
        "selects": [],
        "textareas": [],
        "headings": [],
        "tables": [],
        "dialogs": [],
        "cards": [],
        "badges": [],
        "navigation": [],
        "sections": [],
        "textBlocks": [],
    }

    for element in elements:
        entry = build_component_entry(element)
        semantic_type = clean_text(element.get("semanticType"))
        variant = clean_text(element.get("componentVariant"))

        if semantic_type in {"button", "button-ghost", "cta-link"} and variant != "utility-accessibility":
            inventory["buttons"].append(entry)
        elif semantic_type == "link" and variant != "utility-accessibility":
            inventory["links"].append(entry)
        elif semantic_type == "nav-link":
            inventory["navLinks"].append(entry)
        elif semantic_type == "input":
            inventory["inputs"].append(entry)
        elif semantic_type == "select":
            inventory["selects"].append(entry)
        elif semantic_type == "textarea":
            inventory["textareas"].append(entry)
        elif semantic_type == "heading":
            inventory["headings"].append(entry)
        elif semantic_type == "table":
            inventory["tables"].append(entry)
        elif semantic_type == "dialog":
            inventory["dialogs"].append(entry)
        elif semantic_type == "card":
            inventory["cards"].append(entry)
        elif semantic_type == "badge":
            inventory["badges"].append(entry)
        elif semantic_type == "navigation":
            inventory["navigation"].append(entry)
        elif semantic_type in {"section", "hero"}:
            inventory["sections"].append(entry)
        elif semantic_type == "text-block":
            inventory["textBlocks"].append(entry)

    limits = {
        "buttons": 250,
        "links": 400,
        "navLinks": 400,
        "inputs": 200,
        "selects": 100,
        "textareas": 100,
        "headings": 220,
        "tables": 50,
        "dialogs": 50,
        "cards": 200,
        "badges": 200,
        "navigation": 120,
        "sections": 220,
        "textBlocks": 260,
    }

    return {key: value[:limits[key]] for key, value in inventory.items()}


def build_component_families(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for element in elements:
        group_id = clean_text(element.get("componentGroupId"))
        if group_id:
            groups[group_id].append(element)

    families: List[Dict[str, Any]] = []

    for group_id, members in groups.items():
        first = members[0]
        widths = [safe_float((m.get("rect") or {}).get("width")) for m in members if safe_float((m.get("rect") or {}).get("width")) > 0]
        heights = [safe_float((m.get("rect") or {}).get("height")) for m in members if safe_float((m.get("rect") or {}).get("height")) > 0]

        sample_texts = []
        for member in members[:6]:
            sample = clean_text(member.get("text"))[:80] or clean_text(member.get("label"))[:80]
            if sample:
                sample_texts.append(sample)

        families.append(
            {
                "componentGroupId": group_id,
                "semanticType": clean_text(first.get("semanticType")),
                "componentVariant": clean_text(first.get("componentVariant")),
                "styleSignature": clean_text(first.get("styleSignature")),
                "count": len(members),
                "sampleTexts": unique_text_list(sample_texts)[:6],
                "sampleXPathHints": unique_text_list([clean_text(m.get("xpathHint")) for m in members[:6]])[:6],
                "dimensions": {
                    "avgWidth": round(sum(widths) / len(widths), 1) if widths else 0,
                    "avgHeight": round(sum(heights) / len(heights), 1) if heights else 0,
                    "minWidth": round(min(widths), 1) if widths else 0,
                    "maxWidth": round(max(widths), 1) if widths else 0,
                    "minHeight": round(min(heights), 1) if heights else 0,
                    "maxHeight": round(max(heights), 1) if heights else 0,
                },
                "tokens": build_component_entry(first).get("tokens", {}),
            }
        )

    families = [f for f in families if f["semanticType"] in MEANINGFUL_AUDIT_TYPES]
    families.sort(key=lambda x: (-x["count"], x["semanticType"], x["componentVariant"]))
    return families[:220]


def hidden_interactive_priority(item: Dict[str, Any]) -> int:
    semantic_type = clean_text(item.get("semanticType"))
    text = clean_text(item.get("text"))
    class_name = clean_text(item.get("className")).lower()

    score = 0
    if semantic_type in {"nav-link", "link", "button", "button-ghost", "cta-link"}:
        score += 5
    if text:
        score += 3
    if any(token in class_name for token in ["menu", "drawer", "nav", "dialog", "modal"]):
        score += 3
    if semantic_type == "input":
        score -= 3
    return score


def build_hidden_interactive_inventory(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for element in elements:
        if not is_hidden_but_interactive(element):
            continue
        item = {
            "tag": clean_text(element.get("tag")),
            "role": clean_text(element.get("role")),
            "text": clean_text(element.get("text"))[:160],
            "id": clean_text(element.get("id")),
            "className": clean_text(element.get("className")),
            "href": clean_text(element.get("href")),
            "xpathHint": clean_text(element.get("xpathHint")),
            "semanticType": detect_semantic_type(element),
            "visible": to_bool(element.get("visible")),
            "styles": element.get("styles") or {},
            "rect": element.get("rect") or {},
            "closestLandmark": element.get("closestLandmark") or {},
        }
        out.append(item)

    out.sort(key=hidden_interactive_priority, reverse=True)
    return out[:220]


def build_forms(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    forms_map: Dict[str, Dict[str, Any]] = {}

    for element in elements:
        form_key = clean_text(element.get("closestFormKey"))
        if not form_key:
            continue

        if form_key not in forms_map:
            forms_map[form_key] = {
                "formKey": form_key,
                "formId": clean_text(element.get("closestFormId")),
                "formName": clean_text(element.get("closestFormName")),
                "formAction": clean_text(element.get("closestFormAction")),
                "formMethod": clean_text(element.get("closestFormMethod")),
                "fields": [],
                "buttons": [],
            }

        tag = clean_text(element.get("tag")).lower()
        role = clean_text(element.get("role")).lower()

        entry = {
            "tag": tag,
            "type": clean_text(element.get("type")),
            "text": clean_text(element.get("text"))[:200],
            "label": clean_text(element.get("label")),
            "placeholder": clean_text(element.get("placeholder")),
            "name": clean_text(element.get("name")),
            "id": clean_text(element.get("id")),
            "role": role,
            "semanticType": clean_text(element.get("semanticType")),
            "required": to_bool(element.get("required")),
            "disabled": to_bool(element.get("disabled")),
            "readOnly": to_bool(element.get("readOnly")),
            "visible": to_bool(element.get("visible")),
            "ariaLabel": clean_text(element.get("ariaLabel")),
            "ariaDescribedBy": clean_text(element.get("ariaDescribedBy")),
            "autocomplete": clean_text(element.get("autocomplete")),
            "hasVisibleLabel": to_bool(element.get("hasVisibleLabel")),
            "hasAssociatedLabel": to_bool(element.get("hasAssociatedLabel")),
            "valuePreview": clean_text(element.get("value"))[:100],
            "rect": element.get("rect") or {},
            "styles": element.get("styles") or {},
            "xpathHint": clean_text(element.get("xpathHint")),
        }

        if tag in {"input", "select", "textarea"}:
            forms_map[form_key]["fields"].append(entry)

        if tag == "button" or role == "button" or clean_text(element.get("semanticType")) in {
            "button", "button-ghost", "cta-link", "nav-link"
        }:
            forms_map[form_key]["buttons"].append(entry)

    forms = list(forms_map.values())
    forms.sort(key=lambda x: x["formKey"])
    return forms[:120]


def build_consistency_metrics(
    elements: List[Dict[str, Any]],
    design_summary: Dict[str, Any],
    component_families: List[Dict[str, Any]],
    components: Dict[str, Any],
) -> Dict[str, Any]:
    family_counts_by_type: Dict[str, int] = Counter()
    repeated_families = 0

    for family in component_families:
        semantic_type = clean_text(family.get("semanticType"))
        family_counts_by_type[semantic_type] += 1
        if family.get("count", 0) >= 2:
            repeated_families += 1

    button_family_count = (
        family_counts_by_type.get("button", 0)
        + family_counts_by_type.get("button-ghost", 0)
        + family_counts_by_type.get("cta-link", 0)
    )
    nav_family_count = family_counts_by_type.get("nav-link", 0)
    card_family_count = family_counts_by_type.get("card", 0)

    font_size_count = design_summary.get("typography", {}).get("counts", {}).get("fontSizes", 0)
    spacing_count = design_summary.get("spacing", {}).get("counts", {}).get("values", 0)
    radius_count = design_summary.get("shape", {}).get("counts", {}).get("borderRadii", 0)
    shadow_count = design_summary.get("shape", {}).get("counts", {}).get("shadows", 0)
    meaningful_count = len(elements)

    visible_nav_count = len(components.get("navLinks", []))
    visible_heading_count = len(components.get("headings", []))
    visible_button_count = len(components.get("buttons", []))
    visible_section_count = len(components.get("sections", []))
    visible_text_block_count = len(components.get("textBlocks", []))

    typography_score = 100.0
    if font_size_count > 10:
        typography_score -= min(35, (font_size_count - 10) * 4)
    if visible_heading_count == 0 and meaningful_count >= 8:
        typography_score -= 22

    spacing_score = 100.0
    if spacing_count > 16:
        spacing_score -= min(40, (spacing_count - 16) * 3)

    shape_score = 100.0
    if radius_count > 8:
        shape_score -= min(25, (radius_count - 8) * 4)
    if shadow_count > 6:
        shape_score -= min(20, (shadow_count - 6) * 4)

    component_score = 100.0
    if button_family_count > 5:
        component_score -= min(30, (button_family_count - 5) * 5)
    if card_family_count > 6:
        component_score -= min(25, (card_family_count - 6) * 4)
    if nav_family_count > 4:
        component_score -= min(15, (nav_family_count - 4) * 4)

    if visible_nav_count == 0:
        component_score -= 12
    if visible_button_count == 0:
        component_score -= 10
    if visible_section_count == 0:
        component_score -= 10

    reuse_score = 55.0
    if component_families:
        reuse_score = min(100.0, 35.0 + (repeated_families / max(1, len(component_families))) * 65.0)

    evidence_score = 100.0
    if meaningful_count < 8:
        evidence_score = 25.0
    elif meaningful_count < 16:
        evidence_score = 50.0
    elif meaningful_count < 28:
        evidence_score = 72.0

    if visible_heading_count == 0:
        evidence_score -= 12
    if visible_nav_count == 0:
        evidence_score -= 12
    if visible_button_count == 0:
        evidence_score -= 10
    if visible_section_count == 0:
        evidence_score -= 10
    if visible_text_block_count == 0:
        evidence_score -= 8

    evidence_score = max(0.0, evidence_score)

    overall = (
        typography_score
        + spacing_score
        + shape_score
        + component_score
        + reuse_score
        + evidence_score
    ) / 6.0

    return {
        "typographyConsistency": clamp_score(typography_score),
        "spacingConsistency": clamp_score(spacing_score),
        "shapeConsistency": clamp_score(shape_score),
        "componentConsistency": clamp_score(component_score),
        "styleReuse": clamp_score(reuse_score),
        "evidenceQuality": clamp_score(evidence_score),
        "overallDesignSystemHealth": clamp_score(overall),
    }


def build_audit_signals(
    elements: List[Dict[str, Any]],
    forms: List[Dict[str, Any]],
    design_summary: Dict[str, Any],
    component_families: List[Dict[str, Any]],
    hidden_interactive: List[Dict[str, Any]],
    components: Dict[str, Any],
) -> Dict[str, Any]:
    visual_consistency: List[str] = []
    accessibility_risks: List[str] = []
    layout_risks: List[str] = []
    form_risks: List[str] = []
    component_risks: List[str] = []
    evidence_risks: List[str] = []

    font_sizes = design_summary.get("typography", {}).get("fontSizes", [])
    border_radii = design_summary.get("shape", {}).get("borderRadii", [])
    spacing_values = design_summary.get("spacing", {}).get("values", [])
    shadows = design_summary.get("shape", {}).get("shadows", [])
    font_family_count = design_summary.get("typography", {}).get("counts", {}).get("fontFamilies", 0)

    if len(font_sizes) > 8:
        visual_consistency.append("Many distinct font sizes detected on this page.")
    if len(border_radii) > 6:
        visual_consistency.append("Many distinct border radius values detected on this page.")
    if len(spacing_values) > 12:
        visual_consistency.append("Many distinct spacing values detected on this page.")
    if len(shadows) > 5:
        visual_consistency.append("Many distinct shadow styles detected on this page.")
    if font_family_count > 3:
        visual_consistency.append("Many font families detected on this page.")

    headings = [e for e in elements if clean_text(e.get("semanticType")) == "heading"]
    nav_links = components.get("navLinks", [])
    buttons = components.get("buttons", [])
    cards = components.get("cards", [])
    sections = components.get("sections", [])

    if len(elements) < 10:
        evidence_risks.append("Low number of meaningful visible audit elements captured; page interpretation may be incomplete.")
    if len(headings) == 0 and len(elements) >= 8:
        evidence_risks.append("No heading-like elements were captured; heading detection or page extraction may still be incomplete.")
    if len(nav_links) == 0:
        evidence_risks.append("No visible navigation links were captured.")
    if len(buttons) == 0:
        evidence_risks.append("No visible CTA or button components were captured.")
    if len(cards) == 0:
        evidence_risks.append("No visible card-like content blocks were captured.")
    if len(sections) == 0:
        evidence_risks.append("No visible content sections were captured.")

    button_families = [f for f in component_families if clean_text(f.get("semanticType")) in {"button", "button-ghost", "cta-link"}]
    nav_families = [f for f in component_families if clean_text(f.get("semanticType")) == "nav-link"]
    card_families = [f for f in component_families if clean_text(f.get("semanticType")) == "card"]

    if len(button_families) > 5:
        component_risks.append("Many distinct button or CTA style families detected.")
    if len(nav_families) > 4:
        component_risks.append("Many distinct navigation link style families detected.")
    if len(card_families) > 6:
        component_risks.append("Many distinct card style families detected.")

    for family in component_families:
        semantic_type = clean_text(family.get("semanticType"))
        dimensions = family.get("dimensions") or {}
        count = safe_int(family.get("count"), 0)

        if semantic_type == "card" and count >= 3:
            min_height = safe_float(dimensions.get("minHeight"))
            max_height = safe_float(dimensions.get("maxHeight"))
            if min_height > 0 and max_height > 0 and (max_height - min_height) > 80:
                layout_risks.append(
                    f"Repeated card family shows significant height variation: {clean_text(family.get('componentGroupId'))[:120]}."
                )

    for element in elements:
        tag = clean_text(element.get("tag")).lower()
        rect = element.get("rect") or {}
        styles = element.get("styles") or {}
        semantic_type = clean_text(element.get("semanticType"))

        width = safe_float(rect.get("width"))
        height = safe_float(rect.get("height"))
        x = rect.get("x")
        y = rect.get("y")

        if semantic_type in {"button", "button-ghost", "cta-link", "link", "nav-link"}:
            if width and height and (width < 44 or height < 44):
                accessibility_risks.append(
                    f"Small click target detected for interactive element '{clean_text(element.get('text'))[:80] or clean_text(element.get('id')) or clean_text(element.get('xpathHint'))}'."
                )

        if tag in {"input", "textarea", "select"}:
            if not element.get("hasVisibleLabel") and not clean_text(element.get("ariaLabel")):
                form_risks.append(
                    f"Form field without visible label or aria-label detected: {clean_text(element.get('name')) or clean_text(element.get('id')) or clean_text(element.get('xpathHint'))}."
                )

            outline = clean_text(styles.get("outline")).lower()
            outline_width = clean_text(styles.get("outlineWidth")).lower()
            box_shadow = clean_text(styles.get("boxShadow")).lower()
            outline_style = clean_text(styles.get("outlineStyle")).lower()

            if outline in {"", "none"} and outline_width in {"", "0", "0px"} and outline_style in {"", "none"} and box_shadow in {"", "none"}:
                accessibility_risks.append(
                    f"Input may have weak or absent visible focus indication: {clean_text(element.get('name')) or clean_text(element.get('id')) or clean_text(element.get('xpathHint'))}."
                )

        fg = normalize_color(styles.get("color"))
        bg = normalize_color(styles.get("backgroundColor"))
        ratio = contrast_ratio(fg, bg)
        font_size = parse_px(styles.get("fontSize"))
        if ratio is not None and font_size is not None:
            if font_size < 18 and ratio < 4.5:
                accessibility_risks.append(
                    f"Possible low text contrast detected for '{clean_text(element.get('text'))[:60] or clean_text(element.get('xpathHint'))}'."
                )
            elif font_size >= 18 and ratio < 3:
                accessibility_risks.append(
                    f"Possible low large-text contrast detected for '{clean_text(element.get('text'))[:60] or clean_text(element.get('xpathHint'))}'."
                )

        line_height = parse_px(styles.get("lineHeight"))
        if font_size is not None and line_height is not None and semantic_type in {"text-block", "heading"}:
            if line_height < font_size * 1.1:
                visual_consistency.append(
                    f"Tight line-height detected for '{clean_text(element.get('text'))[:60] or clean_text(element.get('xpathHint'))}'."
                )

        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if x < -5 or y < -5:
                layout_risks.append(
                    f"Potential off-screen positioned element detected: {clean_text(element.get('xpathHint'))}."
                )

        if clean_text(styles.get("overflowX")).lower() in {"scroll", "auto"} and width > 0 and width < 220:
            layout_risks.append(
                f"Narrow horizontally scrollable area detected: {clean_text(element.get('xpathHint'))}."
            )

        if clean_text(styles.get("position")).lower() in {"fixed", "sticky"} and safe_float(rect.get("height")) > 180:
            layout_risks.append(
                f"Large fixed or sticky element may consume significant viewport space: {clean_text(element.get('xpathHint'))}."
            )

    for form in forms:
        visible_fields = [field for field in form.get("fields", []) if field.get("visible")]
        if visible_fields and not form.get("buttons"):
            form_risks.append(
                f"Form '{form.get('formId') or form.get('formName') or form.get('formKey')}' has fields but no button detected."
            )

        required_without_label = [
            field for field in visible_fields
            if field.get("required") and not field.get("hasVisibleLabel") and not field.get("ariaLabel")
        ]
        if required_without_label:
            form_risks.append(
                f"Form '{form.get('formId') or form.get('formName') or form.get('formKey')}' contains required fields without clear labeling."
            )

    if len(hidden_interactive) >= 12:
        accessibility_risks.append("Many hidden interactive elements detected; check collapsed navigation, drawers, or hidden controls.")

    return {
        "visualConsistency": unique_text_list(visual_consistency)[:80],
        "accessibilityRisks": unique_text_list(accessibility_risks)[:120],
        "layoutRisks": unique_text_list(layout_risks)[:120],
        "formRisks": unique_text_list(form_risks)[:80],
        "componentRisks": unique_text_list(component_risks)[:80],
        "evidenceRisks": unique_text_list(evidence_risks)[:40],
    }


async def extract_rendered_ui(page, config: Dict[str, Any]) -> Dict[str, Any]:
    rendered_ui_config = config.get("renderedUi", {})
    max_elements_per_page = safe_int(rendered_ui_config.get("maxElementsPerPage", 1800), 1800)

    raw_elements = await page.evaluate(
        """
        (styleProps) => {
          function cleanText(value) {
            return String(value || '').replace(/\\s+/g, ' ').trim();
          }

          function getXPathHint(element) {
            const tag = (element.tagName || '').toLowerCase();
            const id = element.getAttribute('id');
            const classes = (element.getAttribute('class') || '')
              .trim()
              .split(/\\s+/)
              .filter(Boolean)
              .slice(0, 4)
              .join('.');

            let hint = tag || 'element';
            if (id) hint += `#${id}`;
            if (classes) hint += `.${classes}`;
            return hint;
          }

          function isVisibleNode(element, style, rect) {
            if (!rect || rect.width <= 0 || rect.height <= 0) return false;
            if (style.visibility === 'hidden') return false;
            if (style.display === 'none') return false;
            if (parseFloat(style.opacity || '1') === 0) return false;
            return true;
          }

          function isInteractiveNode(element, style) {
            const tag = cleanText((element.tagName || '').toLowerCase());
            const role = cleanText(element.getAttribute('role')).toLowerCase();

            if (['a', 'button', 'input', 'select', 'textarea', 'summary'].includes(tag)) return true;
            if (['button', 'link', 'tab', 'menuitem', 'checkbox', 'radio', 'switch'].includes(role)) return true;
            if (style.cursor === 'pointer') return true;
            return false;
          }

          function getLabelText(element) {
            const ariaLabel = cleanText(element.getAttribute('aria-label'));
            if (ariaLabel) return ariaLabel;

            const id = element.getAttribute('id');
            if (id) {
              const explicitLabel = document.querySelector(`label[for="${CSS.escape(id)}"]`);
              if (explicitLabel) {
                const text = cleanText(explicitLabel.innerText || explicitLabel.textContent);
                if (text) return text;
              }
            }

            const wrappedLabel = element.closest('label');
            if (wrappedLabel) {
              const text = cleanText(wrappedLabel.innerText || wrappedLabel.textContent);
              if (text) return text;
            }

            return '';
          }

          function hasVisibleLabel(element) {
            const id = element.getAttribute('id');
            if (id) {
              const explicitLabel = document.querySelector(`label[for="${CSS.escape(id)}"]`);
              if (explicitLabel) {
                const rect = explicitLabel.getBoundingClientRect();
                const style = window.getComputedStyle(explicitLabel);
                if (isVisibleNode(explicitLabel, style, rect)) return true;
              }
            }

            const wrappedLabel = element.closest('label');
            if (wrappedLabel) {
              const rect = wrappedLabel.getBoundingClientRect();
              const style = window.getComputedStyle(wrappedLabel);
              if (isVisibleNode(wrappedLabel, style, rect)) return true;
            }

            return false;
          }

          function hasAssociatedLabel(element) {
            const id = element.getAttribute('id');
            if (id && document.querySelector(`label[for="${CSS.escape(id)}"]`)) return true;
            return !!element.closest('label');
          }

          function getClosestLandmark(element) {
            const landmark = element.closest(
              'header, nav, main, footer, aside, section, article, form, [role="main"], [role="navigation"], [role="form"], [role="dialog"], [role="search"], [role="menu"]'
            );

            if (!landmark) {
              return { tag: '', role: '', id: '', className: '', xpathHint: '' };
            }

            return {
              tag: cleanText((landmark.tagName || '').toLowerCase()),
              role: cleanText(landmark.getAttribute('role')),
              id: cleanText(landmark.getAttribute('id')),
              className: cleanText(landmark.getAttribute('class')),
              xpathHint: cleanText(getXPathHint(landmark))
            };
          }

          function buildFormKey(form) {
            if (!form) return '';
            const id = cleanText(form.getAttribute('id'));
            const name = cleanText(form.getAttribute('name'));
            const action = cleanText(form.getAttribute('action'));
            return id || name || action || cleanText(getXPathHint(form));
          }

          function getElementStyles(style, styleProps) {
            const styles = {};
            for (const prop of styleProps) {
              styles[prop] = style[prop] || '';
            }
            return styles;
          }

          function getAriaDisabled(element) {
            return element.getAttribute('aria-disabled') === 'true';
          }

          function getVisibleInteractiveDescendantCount(element) {
            const descendants = Array.from(
              element.querySelectorAll(
                'a, button, input, select, textarea, [role="button"], [role="link"], [role="menuitem"], [role="tab"]'
              )
            ).slice(0, 240);

            let count = 0;
            for (const node of descendants) {
              const rect = node.getBoundingClientRect();
              const style = window.getComputedStyle(node);
              if (isVisibleNode(node, style, rect)) count += 1;
            }
            return count;
          }

          function getVisibleTextDescendantCount(element) {
            const descendants = Array.from(element.querySelectorAll('*')).slice(0, 260);
            let count = 0;

            for (const node of descendants) {
              const rect = node.getBoundingClientRect();
              const style = window.getComputedStyle(node);
              const text = cleanText(node.innerText || node.textContent || '');
              if (text && isVisibleNode(node, style, rect)) count += 1;
            }

            return count;
          }

          function getNavAncestorDepth(element) {
            let depth = 0;
            let current = element.parentElement;

            while (current && depth < 10) {
              const tag = cleanText((current.tagName || '').toLowerCase());
              const role = cleanText(current.getAttribute('role')).toLowerCase();
              const className = cleanText(current.getAttribute('class')).toLowerCase();

              if (
                tag === 'nav' ||
                role === 'navigation' ||
                role === 'menu' ||
                className.includes('nav') ||
                className.includes('menu') ||
                className.includes('header')
              ) {
                return depth + 1;
              }

              current = current.parentElement;
              depth += 1;
            }

            return 0;
          }

          function getHeadingLikeHint(element, style, rect) {
            const tag = cleanText((element.tagName || '').toLowerCase());
            const className = cleanText(element.getAttribute('class')).toLowerCase();
            const text = cleanText(element.innerText || element.textContent || '');
            const textLength = text.length;
            const fontSize = parseFloat(style.fontSize || '0');
            const lineHeight = parseFloat(style.lineHeight || '0');
            const fontWeightRaw = cleanText(style.fontWeight);

            let fontWeight = 400;
            if (['bold', 'bolder'].includes(fontWeightRaw.toLowerCase())) {
              fontWeight = 700;
            } else {
              const parsed = parseInt(fontWeightRaw, 10);
              if (!Number.isNaN(parsed)) fontWeight = parsed;
            }

            if (['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(tag)) return true;
            if (!text || textLength > 140) return false;
            if (rect.width < 50 || rect.height < 14) return false;

            if (
              className.includes('title') ||
              className.includes('heading') ||
              className.includes('headline') ||
              className.includes('subtitle')
            ) {
              if (fontSize >= 16 || fontWeight >= 600) return true;
            }

            if (fontSize >= 30 && fontWeight >= 600 && textLength <= 90) return true;
            if (fontSize >= 24 && fontWeight >= 600 && textLength <= 100) return true;
            if (fontSize >= 20 && fontWeight >= 700 && textLength <= 110) return true;
            if (fontSize >= 18 && fontWeight >= 700 && lineHeight && lineHeight <= fontSize * 1.45 && textLength <= 80) return true;
            return false;
          }

          function getDomDepth(element) {
            let depth = 0;
            let current = element.parentElement;
            while (current && depth < 100) {
              depth += 1;
              current = current.parentElement;
            }
            return depth;
          }

          const allNodes = Array.from(document.querySelectorAll('html, body, body *'));
          const candidates = [];

          for (const element of allNodes) {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const visible = isVisibleNode(element, style, rect);
            const interactive = isInteractiveNode(element, style);
            const text = cleanText(element.innerText || element.textContent || element.value || '');
            const tag = cleanText((element.tagName || '').toLowerCase());
            const className = cleanText(element.getAttribute('class'));
            const role = cleanText(element.getAttribute('role'));

            const semanticTag = [
              'header', 'nav', 'main', 'footer', 'section', 'article', 'aside', 'form',
              'input', 'select', 'textarea', 'button', 'a',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'p', 'span', 'ul', 'ol', 'li', 'table', 'dialog'
            ].includes(tag);

            const classHint =
              className.toLowerCase().includes('card') ||
              className.toLowerCase().includes('tile') ||
              className.toLowerCase().includes('panel') ||
              className.toLowerCase().includes('hero') ||
              className.toLowerCase().includes('banner') ||
              className.toLowerCase().includes('nav') ||
              className.toLowerCase().includes('menu') ||
              className.toLowerCase().includes('title') ||
              className.toLowerCase().includes('heading') ||
              className.toLowerCase().includes('product') ||
              className.toLowerCase().includes('button') ||
              className.toLowerCase().includes('btn') ||
              className.toLowerCase().includes('cta');

            if (visible || interactive || text || semanticTag || role || classHint) {
              const form = element.closest('form');
              const closestLandmark = getClosestLandmark(element);
              const parent = element.parentElement;

              candidates.push({
                tag,
                text,
                href: cleanText(element.getAttribute('href')),
                ariaLabel: cleanText(element.getAttribute('aria-label')),
                ariaDescribedBy: cleanText(element.getAttribute('aria-describedby')),
                role,
                id: cleanText(element.getAttribute('id')),
                className,
                title: cleanText(element.getAttribute('title')),
                name: cleanText(element.getAttribute('name')),
                type: cleanText(element.getAttribute('type')),
                value: cleanText(element.value || element.getAttribute('value')),
                placeholder: cleanText(element.getAttribute('placeholder')),
                autocomplete: cleanText(element.getAttribute('autocomplete')),
                required: element.required === true || element.hasAttribute('required'),
                disabled:
                  element.disabled === true ||
                  element.hasAttribute('disabled') ||
                  getAriaDisabled(element),
                readOnly: element.readOnly === true || element.hasAttribute('readonly'),
                checked: !!element.checked,
                visible,
                interactiveHint: interactive,
                rect: {
                  x: rect.x,
                  y: rect.y,
                  width: rect.width,
                  height: rect.height
                },
                label: getLabelText(element),
                hasVisibleLabel: hasVisibleLabel(element),
                hasAssociatedLabel: hasAssociatedLabel(element),
                xpathHint: cleanText(getXPathHint(element)),
                parentXPathHint: parent ? cleanText(getXPathHint(parent)) : '',
                closestFormKey: buildFormKey(form),
                closestFormId: form ? cleanText(form.getAttribute('id')) : '',
                closestFormName: form ? cleanText(form.getAttribute('name')) : '',
                closestFormAction: form ? cleanText(form.getAttribute('action')) : '',
                closestFormMethod: form ? cleanText(form.getAttribute('method')) : '',
                closestLandmark,
                childElementCount: element.childElementCount || 0,
                visibleInteractiveDescendantCount: getVisibleInteractiveDescendantCount(element),
                visibleTextDescendantCount: getVisibleTextDescendantCount(element),
                textLength: text.length,
                navAncestorDepth: getNavAncestorDepth(element),
                headingLikeHint: getHeadingLikeHint(element, style, rect),
                domDepth: getDomDepth(element),
                styles: getElementStyles(style, styleProps)
              });
            }
          }

          return candidates;
        }
        """,
        IMPORTANT_STYLE_PROPS,
    )

    normalized_elements: List[Dict[str, Any]] = []

    for item in raw_elements:
        normalized_elements.append(
            {
                "tag": clean_text(item.get("tag")),
                "text": clean_text(item.get("text")),
                "href": clean_text(item.get("href")),
                "ariaLabel": clean_text(item.get("ariaLabel")),
                "ariaDescribedBy": clean_text(item.get("ariaDescribedBy")),
                "role": clean_text(item.get("role")),
                "id": clean_text(item.get("id")),
                "className": clean_text(item.get("className")),
                "title": clean_text(item.get("title")),
                "name": clean_text(item.get("name")),
                "type": clean_text(item.get("type")),
                "value": clean_text(item.get("value")),
                "placeholder": clean_text(item.get("placeholder")),
                "autocomplete": clean_text(item.get("autocomplete")),
                "required": to_bool(item.get("required")),
                "disabled": to_bool(item.get("disabled")),
                "readOnly": to_bool(item.get("readOnly")),
                "checked": to_bool(item.get("checked")),
                "visible": to_bool(item.get("visible")),
                "interactiveHint": to_bool(item.get("interactiveHint")),
                "rect": item.get("rect") or {},
                "label": clean_text(item.get("label")),
                "hasVisibleLabel": to_bool(item.get("hasVisibleLabel")),
                "hasAssociatedLabel": to_bool(item.get("hasAssociatedLabel")),
                "xpathHint": clean_text(item.get("xpathHint")),
                "parentXPathHint": clean_text(item.get("parentXPathHint")),
                "closestFormKey": clean_text(item.get("closestFormKey")),
                "closestFormId": clean_text(item.get("closestFormId")),
                "closestFormName": clean_text(item.get("closestFormName")),
                "closestFormAction": clean_text(item.get("closestFormAction")),
                "closestFormMethod": clean_text(item.get("closestFormMethod")),
                "closestLandmark": item.get("closestLandmark") or {},
                "childElementCount": safe_int(item.get("childElementCount"), 0),
                "visibleInteractiveDescendantCount": safe_int(item.get("visibleInteractiveDescendantCount"), 0),
                "visibleTextDescendantCount": safe_int(item.get("visibleTextDescendantCount"), 0),
                "textLength": safe_int(item.get("textLength"), 0),
                "navAncestorDepth": safe_int(item.get("navAncestorDepth"), 0),
                "headingLikeHint": to_bool(item.get("headingLikeHint")),
                "domDepth": safe_int(item.get("domDepth"), 0),
                "styles": item.get("styles") or {},
            }
        )

    deduplicated = unique_by_fingerprint(normalized_elements)[:max_elements_per_page]
    audit_elements = build_audit_elements(deduplicated)
    audit_elements = promote_child_interactives(audit_elements)
    audit_elements = suppress_wrapper_noise(audit_elements)
    hidden_interactive = build_hidden_interactive_inventory(deduplicated)

    page_title = await page.title()
    viewport = page.viewport_size or {}
    final_url = page.url

    design_summary = collect_style_summary(audit_elements)
    landmarks = build_landmarks(audit_elements)
    components = build_component_inventory(audit_elements)
    component_families = build_component_families(audit_elements)
    forms = build_forms(audit_elements)
    consistency_metrics = build_consistency_metrics(audit_elements, design_summary, component_families, components)
    audit_signals = build_audit_signals(
        audit_elements,
        forms,
        design_summary,
        component_families,
        hidden_interactive,
        components,
    )

    return {
        "source": "rendered_ui",
        "pageMeta": {
            "title": clean_text(page_title),
            "url": clean_text(final_url),
            "viewport": viewport,
        },
        "designSummary": design_summary,
        "consistencyMetrics": consistency_metrics,
        "structure": {
            "landmarks": landmarks,
            "totalLandmarks": len(landmarks),
        },
        "components": components,
        "componentFamilies": component_families,
        "forms": forms,
        "hiddenInteractiveElements": hidden_interactive,
        "auditSignals": audit_signals,
        "totalElements": len(deduplicated),
        "auditElementCount": len(audit_elements),
        "hiddenInteractiveCount": len(hidden_interactive),
        "auditElements": audit_elements,
        "elements": deduplicated,
    }


def build_rendered_ui_output(page_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    pages: List[Dict[str, Any]] = []

    for page_result in page_results:
        rendered_ui = page_result.get("renderedUi")
        if not rendered_ui:
            continue

        pages.append(
            {
                "name": page_result.get("name"),
                "url": page_result.get("originalUrl"),
                "finalUrl": page_result.get("finalUrl"),
                "status": page_result.get("status"),
                "renderedUi": rendered_ui,
            }
        )

    return {
        "source": "rendered_ui",
        "generatedFrom": "src.audit.rendered_css_extractor",
        "totalPages": len(pages),
        "pages": pages,
    }