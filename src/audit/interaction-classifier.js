function normalizeForMatch(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

function containsAnyKeyword(haystack, keywords) {
  return keywords.find((keyword) => haystack.includes(normalizeForMatch(keyword))) || null;
}

function buildSearchBlob(clickable) {
  return normalizeForMatch([
    clickable.text,
    clickable.ariaLabel,
    clickable.title,
    clickable.name,
    clickable.value,
    clickable.id,
    clickable.className,
    clickable.onclick
  ].join(' '));
}

function isLikelySafeAnchor(clickable) {
  return clickable.tag === 'a' && Boolean(clickable.href);
}

export function classifyClickable(clickable, config) {
  const searchBlob = buildSearchBlob(clickable);
  const href = normalizeForMatch(clickable.href);

  if (clickable.disabled) {
    return {
      classification: 'unknown',
      reason: 'element is disabled'
    };
  }

  const forbiddenKeyword = containsAnyKeyword(
    searchBlob,
    config.classification.forbiddenKeywords
  );

  if (forbiddenKeyword) {
    return {
      classification: 'forbidden',
      reason: `matched forbidden keyword: ${forbiddenKeyword}`
    };
  }

  const forbiddenHrefKeyword = containsAnyKeyword(
    href,
    config.classification.forbiddenHrefKeywords
  );

  if (forbiddenHrefKeyword) {
    return {
      classification: 'forbidden',
      reason: `matched forbidden href keyword: ${forbiddenHrefKeyword}`
    };
  }

  const safeKeyword = containsAnyKeyword(
    searchBlob,
    config.classification.safeKeywords
  );

  if (safeKeyword) {
    return {
      classification: 'safe',
      reason: `matched safe keyword: ${safeKeyword}`
    };
  }

  if (isLikelySafeAnchor(clickable)) {
    return {
      classification: 'safe',
      reason: 'anchor with href and no forbidden signals'
    };
  }

  if (clickable.tag === 'button' || clickable.role === 'button') {
    return {
      classification: 'unknown',
      reason: 'button-like element without clear safe/forbidden signal'
    };
  }

  return {
    classification: 'unknown',
    reason: 'no rule matched'
  };
}

export function classifyClickables(clickables, config) {
  return clickables.map((clickable) => {
    const classificationResult = classifyClickable(clickable, config);

    return {
      ...clickable,
      classification: classificationResult.classification,
      classificationReason: classificationResult.reason
    };
  });
}

export function summarizeClassification(classifiedClickables) {
  return classifiedClickables.reduce(
    (acc, item) => {
      if (item.classification === 'safe') acc.safe += 1;
      else if (item.classification === 'forbidden') acc.forbidden += 1;
      else acc.unknown += 1;

      return acc;
    },
    { safe: 0, forbidden: 0, unknown: 0 }
  );
}