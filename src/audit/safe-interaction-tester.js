import { detectClickables } from './element-detector.js';
import { safeNormalizeUrl, getOriginSafe } from '../utils/url-utils.js';

function cleanText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildFingerprint(clickable) {
  return [
    clickable.tag,
    cleanText(clickable.text),
    cleanText(clickable.href),
    cleanText(clickable.ariaLabel),
    cleanText(clickable.role),
    cleanText(clickable.id),
    cleanText(clickable.className),
    cleanText(clickable.title),
    cleanText(clickable.name),
    cleanText(clickable.type),
    cleanText(clickable.value),
    cleanText(clickable.xpathHint)
  ].join('||');
}

function findMatchingClickableIndex(targetClickable, freshClickables) {
  const targetFingerprint = buildFingerprint(targetClickable);

  for (let i = 0; i < freshClickables.length; i++) {
    if (buildFingerprint(freshClickables[i]) === targetFingerprint) {
      return i;
    }
  }

  for (let i = 0; i < freshClickables.length; i++) {
    const c = freshClickables[i];
    if (
      c.tag === targetClickable.tag &&
      cleanText(c.text) === cleanText(targetClickable.text) &&
      cleanText(c.href) === cleanText(targetClickable.href)
    ) {
      return i;
    }
  }

  return -1;
}

function shouldSkipSafeClickable(clickable, pageUrl, config) {
  if (config.interactionTesting.onlyVisible && !clickable.visible) {
    return 'not visible';
  }

  if (clickable.disabled) {
    return 'disabled';
  }

  if (clickable.tag === 'a' && clickable.href) {
    const href = clickable.href.trim();

    if (!config.interactionTesting.testSamePageAnchors && href.startsWith('#')) {
      return 'same-page hash anchor skipped';
    }

    try {
      const targetUrl = new URL(href, pageUrl).toString();
      const pageOrigin = getOriginSafe(pageUrl);
      const targetOrigin = getOriginSafe(targetUrl);

      if (config.interactionTesting.skipExternalOrigins && pageOrigin && targetOrigin && pageOrigin !== targetOrigin) {
        return 'external origin skipped';
      }
    } catch {
      return 'invalid href';
    }
  }

  return null;
}

async function waitShortlyAfterAction(page, delayMs) {
  if (delayMs > 0) {
    await page.waitForTimeout(delayMs);
  }
}

async function capturePageState(page) {
  return page.evaluate(() => {
    const body = document.body;
    const text = body ? body.innerText || '' : '';
    return {
      title: document.title || '',
      textLength: text.trim().length,
      bodyLength: body ? (body.innerHTML || '').length : 0
    };
  });
}

async function tryHandleDialog(page) {
  let dialogInfo = null;

  const dialogHandler = async (dialog) => {
    dialogInfo = {
      type: dialog.type(),
      message: dialog.message()
    };

    try {
      await dialog.dismiss();
    } catch {
      // ignore
    }
  };

  page.once('dialog', dialogHandler);
  return {
    getDialogInfo: () => dialogInfo
  };
}

export async function testSafeClickables({
  browser,
  pageInfo,
  classifiedClickables,
  config
}) {
  if (!config.interactionTesting.enabled) {
    return {
      testedCount: 0,
      skippedSafeCount: 0,
      safeInteractionResults: []
    };
  }

  const safeClickables = classifiedClickables
    .filter((item) => item.classification === 'safe')
    .slice(0, config.interactionTesting.maxSafeInteractionsPerPage);

  const results = [];
  let skippedSafeCount = 0;

  for (const clickable of safeClickables) {
    const skipReason = shouldSkipSafeClickable(clickable, pageInfo.url, config);

    if (skipReason) {
      skippedSafeCount += 1;
      results.push({
        clickableIndex: clickable.index,
        clickableText: clickable.text,
        clickableTag: clickable.tag,
        classification: clickable.classification,
        tested: false,
        outcomeType: 'skipped',
        success: false,
        reason: skipReason,
        beforeUrl: pageInfo.url,
        afterUrl: null,
        normalizedAfterUrl: null,
        openedNewTab: false,
        dialog: null,
        domChanged: false,
        error: null
      });
      continue;
    }

    const testPage = await browser.newPage({
      viewport: config.browser.viewport
    });

    let interactionResult = {
      clickableIndex: clickable.index,
      clickableText: clickable.text,
      clickableTag: clickable.tag,
      classification: clickable.classification,
      tested: true,
      outcomeType: 'unknown',
      success: false,
      reason: null,
      beforeUrl: pageInfo.url,
      afterUrl: null,
      normalizedAfterUrl: null,
      openedNewTab: false,
      dialog: null,
      domChanged: false,
      error: null
    };

    let popupPage = null;

    try {
      await testPage.goto(pageInfo.url, {
        waitUntil: config.navigation.waitUntil,
        timeout: config.navigation.timeoutMs
      });

      if (config.navigation.postLoadDelayMs > 0) {
        await testPage.waitForTimeout(config.navigation.postLoadDelayMs);
      }

      const beforeUrl = testPage.url();
      const beforeState = await capturePageState(testPage);
      const dialogTracker = await tryHandleDialog(testPage);

      const freshClickables = await detectClickables(testPage, config);
      const targetIndex = findMatchingClickableIndex(clickable, freshClickables);

      if (targetIndex === -1) {
        interactionResult.outcomeType = 'not_found';
        interactionResult.reason = 'matching clickable not found on fresh page load';
        interactionResult.error = null;
        results.push(interactionResult);
        await testPage.close();
        continue;
      }

      const selector = config.clickableDetection.selectors.join(', ');
      const locator = testPage.locator(selector).nth(targetIndex);

      const popupPromise = testPage.waitForEvent('popup', {
        timeout: config.interactionTesting.actionTimeoutMs
      }).catch(() => null);

      await locator.click({
        timeout: config.interactionTesting.actionTimeoutMs
      });

      popupPage = await popupPromise;

      await waitShortlyAfterAction(testPage, config.interactionTesting.postClickDelayMs);

      const afterUrl = testPage.url();
      const afterState = await capturePageState(testPage);
      const normalizedAfterUrl = safeNormalizeUrl(afterUrl, config.urlNormalization);
      const normalizedBeforeUrl = safeNormalizeUrl(beforeUrl, config.urlNormalization);

      interactionResult.afterUrl = afterUrl;
      interactionResult.normalizedAfterUrl = normalizedAfterUrl;
      interactionResult.dialog = dialogTracker.getDialogInfo();
      interactionResult.openedNewTab = Boolean(popupPage);
      interactionResult.domChanged =
        beforeState.title !== afterState.title ||
        beforeState.textLength !== afterState.textLength ||
        beforeState.bodyLength !== afterState.bodyLength;

      if (popupPage) {
        interactionResult.outcomeType = 'popup';
        interactionResult.success = true;
        interactionResult.reason = 'interaction opened a new tab or window';
      } else if (normalizedBeforeUrl && normalizedAfterUrl && normalizedBeforeUrl !== normalizedAfterUrl) {
        interactionResult.outcomeType = 'navigation';
        interactionResult.success = true;
        interactionResult.reason = 'interaction changed the page URL';
      } else if (interactionResult.domChanged) {
        interactionResult.outcomeType = 'dom_change';
        interactionResult.success = true;
        interactionResult.reason = 'interaction changed page DOM without URL change';
      } else if (interactionResult.dialog) {
        interactionResult.outcomeType = 'dialog';
        interactionResult.success = true;
        interactionResult.reason = 'interaction opened a dialog';
      } else {
        interactionResult.outcomeType = 'no_effect';
        interactionResult.success = false;
        interactionResult.reason = 'no visible navigation or DOM change detected';
      }
    } catch (error) {
      interactionResult.outcomeType = 'error';
      interactionResult.success = false;
      interactionResult.error = error.message;
      interactionResult.reason = 'interaction threw an error';
    } finally {
      if (popupPage) {
        try {
          await popupPage.close();
        } catch {
          // ignore
        }
      }

      try {
        await testPage.close();
      } catch {
        // ignore
      }
    }

    results.push(interactionResult);
  }

  return {
    testedCount: results.filter((r) => r.tested && r.outcomeType !== 'skipped').length,
    skippedSafeCount,
    safeInteractionResults: results
  };
}