import { joinPath } from '../utils/file-utils.js';
import { slugify } from '../utils/url-utils.js';
import { detectClickables } from './element-detector.js';
import {
  classifyClickables,
  summarizeClassification
} from './interaction-classifier.js';

export async function runPageAudit({
  browser,
  pageInfo,
  pageIndex,
  config
}) {
  const page = await browser.newPage({
    viewport: config.browser.viewport
  });

  const result = {
    index: pageIndex + 1,
    name: pageInfo.name,
    originalUrl: pageInfo.url,
    normalizedUrl: pageInfo.normalizedUrl,
    finalUrl: null,
    status: 'pending',
    screenshotPath: null,
    clickableSummary: {
      totalDetected: 0,
      safe: 0,
      forbidden: 0,
      unknown: 0
    },
    clickables: [],
    error: null
  };

  try {
    await page.goto(pageInfo.url, {
      waitUntil: config.navigation.waitUntil,
      timeout: config.navigation.timeoutMs
    });

    if (config.navigation.postLoadDelayMs > 0) {
      await page.waitForTimeout(config.navigation.postLoadDelayMs);
    }

    result.finalUrl = page.url();

    const fileName = `${String(pageIndex + 1).padStart(3, '0')}_${slugify(pageInfo.name || 'page')}.${config.screenshot.type}`;
    const screenshotPath = joinPath(config.paths.screenshotDir, fileName);

    await page.screenshot({
      path: screenshotPath,
      fullPage: config.screenshot.fullPage,
      type: config.screenshot.type
    });

    result.screenshotPath = screenshotPath;

    const detectedClickables = await detectClickables(page, config);
    const classifiedClickables = classifyClickables(detectedClickables, config);
    const classificationSummary = summarizeClassification(classifiedClickables);

    result.clickables = classifiedClickables;
    result.clickableSummary = {
      totalDetected: classifiedClickables.length,
      safe: classificationSummary.safe,
      forbidden: classificationSummary.forbidden,
      unknown: classificationSummary.unknown
    };

    result.status = 'success';
  } catch (error) {
    result.status = 'failed';
    result.error = error.message;
  } finally {
    await page.close();
  }

  return result;
}