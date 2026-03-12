function cleanText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim();
}

function uniqueByFingerprint(items) {
  const seen = new Set();
  const result = [];

  for (const item of items) {
    const fingerprint = [
      item.tag,
      item.text,
      item.href,
      item.ariaLabel,
      item.role,
      item.id,
      item.name,
      item.type,
      item.xpathHint
    ].join('||');

    if (seen.has(fingerprint)) {
      continue;
    }

    seen.add(fingerprint);
    result.push(item);
  }

  return result;
}

export async function detectClickables(page, config) {
  const selector = config.clickableDetection.selectors.join(', ');

  const rawClickables = await page.locator(selector).evaluateAll((elements) => {
    function getXPathHint(element) {
      const tag = (element.tagName || '').toLowerCase();
      const id = element.getAttribute('id');
      const classes = (element.getAttribute('class') || '')
        .trim()
        .split(/\s+/)
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
  });

  const normalized = rawClickables.map((item, index) => ({
    index: index + 1,
    domIndex: item.domIndex,
    tag: cleanText(item.tag),
    text: cleanText(item.text),
    href: cleanText(item.href),
    ariaLabel: cleanText(item.ariaLabel),
    role: cleanText(item.role),
    id: cleanText(item.id),
    className: cleanText(item.className),
    title: cleanText(item.title),
    name: cleanText(item.name),
    type: cleanText(item.type),
    value: cleanText(item.value),
    onclick: cleanText(item.onclick),
    disabled: Boolean(item.disabled),
    visible: Boolean(item.visible),
    rect: item.rect,
    xpathHint: cleanText(item.xpathHint)
  }));

  const deduplicated = uniqueByFingerprint(normalized);

  return deduplicated
    .slice(0, config.clickableDetection.maxElementsPerPage)
    .map((item, index) => ({
      ...item,
      index: index + 1
    }));
}