import os


def _env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_optional_positive_int(name, default):
    value = _env_int(name, default)
    return value if isinstance(value, int) and value > 0 else None


def _env_text(name, default=""):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


AUDIT_CONFIG = {
    "browser": {
        "headless": _env_bool("AUDIT_BROWSER_HEADLESS", True),
        "browserType": _env_text("AUDIT_BROWSER_TYPE", "chromium") or "chromium",
        "channel": _env_text("AUDIT_BROWSER_CHANNEL", ""),
        "slowMoMs": _env_int("AUDIT_BROWSER_SLOW_MO_MS", 0),
        "ignoreHttpsErrors": _env_bool("AUDIT_BROWSER_IGNORE_HTTPS_ERRORS", True),
        "viewport": {
            "width": _env_int("AUDIT_BROWSER_VIEWPORT_WIDTH", 1440),
            "height": _env_int("AUDIT_BROWSER_VIEWPORT_HEIGHT", 900),
        },
    },
    "navigation": {
        "timeoutMs": 15000,
        "waitUntil": "domcontentloaded",
        "postLoadDelayMs": 300,
    },
    "pageReadiness": {
        "networkIdleTimeoutMs": 4000,
        "assetTimeoutMs": 5000,
        "settleDelayMs": 350,
    },
    "paths": {
        "inputFile": "shared/generated/website_menu.json",
        "screenshotDir": "shared/output/screenshots",
        "resultsDir": "shared/output/results",
    },
    "outputCleanup": {
        "clearWebsiteScreenshotsBeforeRun": True,
        "keepDebugArtifacts": False,
    },
    "screenshot": {
        "fullPage": True,
        "type": "png",
    },
    "pageCapture": {
        "dismissCookieBanners": True,
        "captureScrollScreenshots": True,
        "scrollMaxRounds": 4,
        "saveDomSnapshot": True,
        "saveNetworkLog": True,
    },
    "presentationChecks": {
        "responsiveDesktopMobile": {
            "enabled": _env_bool("AUDIT_RESPONSIVE_CHECK_ENABLED", True),
            "mobileWidth": _env_int("AUDIT_RESPONSIVE_MOBILE_WIDTH", 390),
            "mobileHeight": _env_int("AUDIT_RESPONSIVE_MOBILE_HEIGHT", 844),
        },
        "runtimeMotion": {
            "enabled": True,
            "sampleIntervalMs": 350,
            "sampleCount": 4,
            "motionThresholdPx": 6,
            "opacityThreshold": 0.2,
            "maxElements": 120,
        },
    },
    "urlNormalization": {
        "removeHash": True,
        "removeTrailingSlash": False,
        "removeCommonTrackingParams": True,
        "trackingParams": [
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "gclid",
            "fbclid",
        ],
    },
    "inputParsing": {
        "includeHomepage": True,
        "includeAuthPages": False,
        "includeNavbarUrls": True,
        "includeSectionUrls": True,
    },
    "clickableDetection": {
        "selectors": [
            "a[href]",
            "button",
            '[role="button"]',
            'input[type="button"]',
            'input[type="submit"]',
        ],
        "maxElementsPerPage": 500,
    },
    "classification": {
        "forbiddenKeywords": [
            "logout",
            "log out",
            "sign out",
            "delete",
            "remove",
            "remove account",
            "deactivate",
            "unsubscribe",
            "pay",
            "buy",
            "purchase",
            "checkout",
            "place order",
            "confirm",
            "submit",
            "send",
            "save",
            "publish",
            "reset",
            "clear cart",
            "cancel subscription",
            "close account",
            "login",
            "log in",
            "connexion",
            "se connecter",
            "sign in",
            "register",
            "sign up",
        ],
        "safeKeywords": [
            "home",
            "about",
            "contact",
            "learn more",
            "read more",
            "details",
            "view details",
            "open",
            "menu",
            "next",
            "previous",
            "back",
            "search",
            "filter",
            "sort",
            "show more",
            "see more",
        ],
        "forbiddenHrefKeywords": [
            "logout",
            "signout",
            "delete",
            "remove",
            "checkout",
            "payment",
            "purchase",
            "unsubscribe",
            "deactivate",
            "login",
            "customer_authentication",
            "cart",
            "register",
            "signup",
            "signin",
        ],
    },
    "renderedUi": {
        "enabled": True,
        "selectors": [
            "html",
            "body",
            "header",
            "nav",
            "main",
            "footer",
            "section",
            "article",
            "aside",
            "form",
            "fieldset",
            "legend",
            "label",
            "input",
            "textarea",
            "select",
            "option",
            "button",
            '[role="button"]',
            "a",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "ul",
            "ol",
            "li",
            "table",
            "thead",
            "tbody",
            "tr",
            "td",
            "th",
            "dialog",
            '[role="dialog"]',
            '[role="alert"]',
            '[role="tab"]',
            '[role="tabpanel"]',
            '[role="navigation"]',
            '[role="main"]',
            '[role="form"]',
            "[data-testid]",
            "[class*='card']",
            "[class*='modal']",
            "[class*='dialog']",
            "[class*='form']",
            "[class*='input']",
            "[class*='field']",
            "[class*='button']",
        ],
        "maxElementsPerPage": 300,
    },
    "interactionTesting": {
        "enabled": True,
        "onlyVisible": True,
        "maxSafeInteractionsPerPage": _env_optional_positive_int("AUDIT_MAX_SAFE_INTERACTIONS_PER_PAGE", 25),
        "actionTimeoutMs": 5000,
        "postClickDelayMs": 250,
        "testSamePageAnchors": False,
        "skipExternalOrigins": True,
        "captureAllInteractionScreenshots": True,
        "captureSuccessfulInteractionScreenshots": True,
    },
    "execution": {
        "pageConcurrency": _env_int("AUDIT_PAGE_CONCURRENCY", 2),
    },
    "mobileAudit": {
        "appium": {
            "url": "http://127.0.0.1:4723",
            "platformName": "Android",
            "automationName": "UiAutomator2",
            "deviceName": "Android Emulator",
            "adbPath": "",
            "androidSdkRoot": "",
            "newCommandTimeoutSec": 120,
            "adbExecTimeoutMs": 120000,
            "uiautomator2ServerInstallTimeoutMs": 120000,
            "uiautomator2ServerLaunchTimeoutMs": 120000,
            "uiautomator2ServerReadTimeoutMs": 120000,
            "androidInstallTimeoutMs": 120000,
            "appWaitDurationMs": 120000,
            "skipDeviceInitialization": False,
            "disableWindowAnimation": True,
            "deviceReadyTimeoutMs": 180000,
            "deviceReadyPollMs": 2000,
            "autoGrantPermissions": True,
            "noReset": False,
        },
        "capture": {
            "launchTimeoutMs": 15000,
            "settleDelayMs": 1200,
            "stabilizationTimeoutMs": 10000,
            "stabilizationPollMs": 700,
        },
        "initialization": {
            "maxBackPresses": 2,
            "postBackDelayMs": 900,
            "maxRelaunches": 1,
            "postRelaunchDelayMs": 1400,
        },
        "exploration": {
            "maxScreens": 80,
            "maxActionsTotal": 192,
            "maxActionsPerScreen": 8,
            "maxScrollsPerPath": 5,
            "maxBacktrackSteps": 4,
            "scrollPostDelayMs": 900,
            "scrollPercent": 0.82,
        },
        "semantic": {
            "webviewContentDescValues": ["web view"],
            "homeFeedSignals": [
                "search or type web address",
                "discover",
                "options for discover",
            ],
            "browserMenuSignals": [
                "new tab",
                "history",
                "downloads",
                "bookmarks",
                "settings",
                "help & feedback",
                "find in page",
            ],
        },
        "paths": {
            "outputRoot": "shared/generated/mobile-audits",
            "screenshotDirName": "screenshots",
            "hierarchyDirName": "hierarchies",
        },
    },
}
