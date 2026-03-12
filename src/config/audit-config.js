export const AUDIT_CONFIG = {
  browser: {
    headless: true,
    browserType: 'chromium', // 'chromium' | 'firefox' | 'webkit'
    viewport: {
      width: 1440,
      height: 900
    }
  },

  navigation: {
    timeoutMs: 30000,
    waitUntil: 'domcontentloaded',
    postLoadDelayMs: 1500
  },

  paths: {
    inputFile: 'input/pages.json',
    screenshotDir: 'output/screenshots',
    resultsDir: 'output/results'
  },

  screenshot: {
    fullPage: true,
    type: 'png'
  },

  urlNormalization: {
    removeHash: true,
    removeTrailingSlash: false,
    removeCommonTrackingParams: true,
    trackingParams: [
      'utm_source',
      'utm_medium',
      'utm_campaign',
      'utm_term',
      'utm_content',
      'gclid',
      'fbclid'
    ]
  }
};