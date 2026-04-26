def clean_text(value):
    return " ".join(str(value or "").split()).strip()


def unique_by_fingerprint(items):
    seen = set()
    result = []

    for item in items:
        fingerprint = "||".join(
            [
                item["tag"],
                item["text"],
                item["href"],
                item["ariaLabel"],
                item["role"],
                item["id"],
                item["name"],
                item["type"],
                item["xpathHint"],
            ]
        )

        if fingerprint in seen:
            continue

        seen.add(fingerprint)
        result.append(item)

    return result


async def detect_clickables(page, config):
    selector = ", ".join(config["clickableDetection"]["selectors"])

    raw_clickables = await page.locator(selector).evaluate_all(
        """
        (elements) => {
          function getXPathHint(element) {
            const tag = (element.tagName || '').toLowerCase();
            const id = element.getAttribute('id');
            const classes = (element.getAttribute('class') || '')
              .trim()
              .split(/\\s+/)
              .filter(Boolean)
              .slice(0, 3)
              .join('.');

            let hint = tag || 'element';

            if (id) {
              hint += `#${id}`;
            }

            if (classes) {
              hint += `.${classes}`;
            }

            return hint;
          }

          return elements.map((element, index) => {
            const rect = element.getBoundingClientRect();
            const text =
              element.innerText ||
              element.textContent ||
              element.getAttribute('value') ||
              '';

            return {
              domIndex: index,
              tag: (element.tagName || '').toLowerCase(),
              text,
              href: element.getAttribute('href'),
              ariaLabel: element.getAttribute('aria-label'),
              role: element.getAttribute('role'),
              id: element.getAttribute('id'),
              className: element.getAttribute('class'),
              title: element.getAttribute('title'),
              name: element.getAttribute('name'),
              type: element.getAttribute('type'),
              value: element.getAttribute('value'),
              onclick: element.getAttribute('onclick'),
              disabled:
                element.hasAttribute('disabled') ||
                element.getAttribute('aria-disabled') === 'true',
              visible:
                rect.width > 0 &&
                rect.height > 0 &&
                window.getComputedStyle(element).visibility !== 'hidden' &&
                window.getComputedStyle(element).display !== 'none',
              rect: {
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height
              },
              xpathHint: getXPathHint(element)
            };
          });
        }
        """
    )

    normalized = []
    for index, item in enumerate(raw_clickables):
        normalized.append(
            {
                "index": index + 1,
                "domIndex": item["domIndex"],
                "tag": clean_text(item.get("tag")),
                "text": clean_text(item.get("text")),
                "href": clean_text(item.get("href")),
                "ariaLabel": clean_text(item.get("ariaLabel")),
                "role": clean_text(item.get("role")),
                "id": clean_text(item.get("id")),
                "className": clean_text(item.get("className")),
                "title": clean_text(item.get("title")),
                "name": clean_text(item.get("name")),
                "type": clean_text(item.get("type")),
                "value": clean_text(item.get("value")),
                "onclick": clean_text(item.get("onclick")),
                "disabled": bool(item.get("disabled")),
                "visible": bool(item.get("visible")),
                "rect": item.get("rect"),
                "xpathHint": clean_text(item.get("xpathHint")),
            }
        )

    deduplicated = unique_by_fingerprint(normalized)
    limited = deduplicated[: config["clickableDetection"]["maxElementsPerPage"]]

    return [{**item, "index": index + 1} for index, item in enumerate(limited)]
