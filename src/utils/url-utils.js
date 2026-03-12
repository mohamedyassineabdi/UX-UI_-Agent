export function normalizeUrl(rawUrl, options = {}) {
  const {
    removeHash = true,
    removeTrailingSlash = false,
    removeCommonTrackingParams = true,
    trackingParams = []
  } = options;

  let parsed;

  try {
    parsed = new URL(rawUrl);
  } catch (error) {
    throw new Error(`Invalid URL: ${rawUrl}`);
  }

  parsed.protocol = parsed.protocol.toLowerCase();
  parsed.hostname = parsed.hostname.toLowerCase();

  if (removeHash) {
    parsed.hash = '';
  }

  if (removeCommonTrackingParams) {
    for (const param of trackingParams) {
      parsed.searchParams.delete(param);
    }
  }

  const sortedParams = [...parsed.searchParams.entries()].sort(([a], [b]) =>
    a.localeCompare(b)
  );

  parsed.search = '';

  for (const [key, value] of sortedParams) {
    parsed.searchParams.append(key, value);
  }

  let normalized = parsed.toString();

  if (removeTrailingSlash) {
    normalized = normalized.replace(/\/$/, '');
  }

  return normalized;
}

export function safeNormalizeUrl(rawUrl, options = {}) {
  try {
    return normalizeUrl(rawUrl, options);
  } catch {
    return null;
  }
}

export function slugify(input) {
  return String(input)
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .toLowerCase();
}

export function deduplicatePages(pages, normalizationOptions = {}) {
  const seen = new Set();
  const uniquePages = [];
  const duplicates = [];

  for (const page of pages) {
    const normalizedUrl = normalizeUrl(page.url, normalizationOptions);

    const enrichedPage = {
      ...page,
      normalizedUrl
    };

    if (seen.has(normalizedUrl)) {
      duplicates.push(enrichedPage);
      continue;
    }

    seen.add(normalizedUrl);
    uniquePages.push(enrichedPage);
  }

  return {
    uniquePages,
    duplicates
  };
}

export function getOriginSafe(rawUrl) {
  try {
    return new URL(rawUrl).origin;
  } catch {
    return null;
  }
}