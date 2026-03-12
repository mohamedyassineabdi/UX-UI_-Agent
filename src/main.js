import { chromium, firefox, webkit } from 'playwright';
import { AUDIT_CONFIG } from './config/audit-config.js';
import {
  ensureOutputDirs,
  readJsonFile,
  writeJsonFile,
  buildTimestampForFileName,
  joinPath
} from './utils/file-utils.js';
import { deduplicatePages } from './utils/url-utils.js';
import { runPageAudit } from './audit/page-runner.js';

function getBrowserLauncher(browserType) {
  switch (browserType) {
    case 'firefox':
      return firefox;
    case 'webkit':
      return webkit;
    case 'chromium':
    default:
      return chromium;
  }
}

async function main() {
  const startedAt = new Date();

  console.log('Starting Milestone 1 audit...');
  console.log(`Reading input file: ${AUDIT_CONFIG.paths.inputFile}`);

  await ensureOutputDirs(AUDIT_CONFIG.paths);

  const rawPages = await readJsonFile(AUDIT_CONFIG.paths.inputFile);

  if (!Array.isArray(rawPages)) {
    throw new Error('Input JSON must be an array of page objects.');
  }

  const pagesValidated = rawPages.map((page, index) => {
    if (!page || typeof page !== 'object') {
      throw new Error(`Item at index ${index} is not a valid object.`);
    }

    if (!page.url || typeof page.url !== 'string') {
      throw new Error(`Item at index ${index} is missing a valid "url".`);
    }

    return {
      name: typeof page.name === 'string' && page.name.trim()
        ? page.name.trim()
        : `Page_${index + 1}`,
      url: page.url.trim()
    };
  });

  const { uniquePages, duplicates } = deduplicatePages(
    pagesValidated,
    AUDIT_CONFIG.urlNormalization
  );

  console.log(`Total pages in input: ${pagesValidated.length}`);
  console.log(`Unique pages to visit: ${uniquePages.length}`);
  console.log(`Duplicates skipped: ${duplicates.length}`);

  const browserLauncher = getBrowserLauncher(AUDIT_CONFIG.browser.browserType);
  const browser = await browserLauncher.launch({
    headless: AUDIT_CONFIG.browser.headless
  });

  const pageResults = [];

  try {
    for (let i = 0; i < uniquePages.length; i++) {
      const pageInfo = uniquePages[i];
      console.log(
        `[${i + 1}/${uniquePages.length}] Visiting: ${pageInfo.name} -> ${pageInfo.url}`
      );

      const result = await runPageAudit({
        browser,
        pageInfo,
        pageIndex: i,
        config: AUDIT_CONFIG
      });

      pageResults.push(result);

      if (result.status === 'success') {
        console.log(`  Success -> screenshot saved: ${result.screenshotPath}`);
      } else {
        console.log(`  Failed -> ${result.error}`);
      }
    }
  } finally {
    await browser.close();
  }

  const finishedAt = new Date();
  const timestamp = buildTimestampForFileName(finishedAt);

  const summary = {
    runStartedAt: startedAt.toISOString(),
    runFinishedAt: finishedAt.toISOString(),
    inputFile: AUDIT_CONFIG.paths.inputFile,
    browserType: AUDIT_CONFIG.browser.browserType,
    headless: AUDIT_CONFIG.browser.headless,
    totalPagesInInput: pagesValidated.length,
    uniquePagesVisited: uniquePages.length,
    duplicatePagesSkipped: duplicates.length,
    pagesSucceeded: pageResults.filter((r) => r.status === 'success').length,
    pagesFailed: pageResults.filter((r) => r.status === 'failed').length
  };

  const output = {
    summary,
    duplicatesSkipped: duplicates,
    pages: pageResults
  };

  const resultsFilePath = joinPath(
    AUDIT_CONFIG.paths.resultsDir,
    `audit-results_${timestamp}.json`
  );

  await writeJsonFile(resultsFilePath, output);

  console.log(`Results written to: ${resultsFilePath}`);
  console.log('Milestone 1 audit completed.');
}

main().catch((error) => {
  console.error('Fatal error while running audit:');
  console.error(error);
  process.exit(1);
});