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
    timeoutMs: 20000,
    waitUntil: 'domcontentloaded',
    postLoadDelayMs: 600
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
  },

  clickableDetection: {
    selectors: [
      'a[href]',
      'button',
      '[role="button"]',
      'input[type="button"]',
      'input[type="submit"]'
    ],
    maxElementsPerPage: 500
  },

  classification: {
    forbiddenKeywords: [
      'logout',
      'log out',
      'sign out',
      'delete',
      'remove',
      'remove account',
      'deactivate',
      'unsubscribe',
      'pay',
      'buy',
      'purchase',
      'checkout',
      'place order',
      'confirm',
      'submit',
      'send',
      'save',
      'publish',
      'reset',
      'clear cart',
      'cancel subscription',
      'close account'
    ],

    safeKeywords: [
      'home',
      'about',
      'contact',
      'learn more',
      'read more',
      'details',
      'view details',
      'open',
      'menu',
      'next',
      'previous',
      'back',
      'search',
      'filter',
      'sort',
      'show more',
      'see more'
    ],

    forbiddenHrefKeywords: [
      'logout',
      'signout',
      'delete',
      'remove',
      'checkout',
      'payment',
      'purchase',
      'unsubscribe',
      'deactivate'
    ]
  },

  interactionTesting: {
    enabled: true,
    onlyVisible: true,
    maxSafeInteractionsPerPage: 12,
    actionTimeoutMs: 7000,
    postClickDelayMs: 500,
    testSamePageAnchors: false,
    skipExternalOrigins: true,
    captureSuccessfulInteractionScreenshots: true
  }
};