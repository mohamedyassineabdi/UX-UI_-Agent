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

function normalizeFlatPage(page, index = 0) {
  if (!page || typeof page !== 'object') {
    throw new Error(`Item at index ${index} is not a valid object.`);
  }

  if (!page.url || typeof page.url !== 'string') {
    throw new Error(`Item at index ${index} is missing a valid "url".`);
  }

  return {
    name:
      typeof page.name === 'string' && page.name.trim()
        ? page.name.trim()
        : `Page_${index + 1}`,
    url: page.url.trim()
  };
}

function extractPagesFromPartnerJson(input, config) {
  const pages = [];

  if (config.inputParsing.includeHomepage && typeof input.homepage === 'string') {
    pages.push({
      name: 'Home',
      url: input.homepage
    });
  }

  if (config.inputParsing.includeAuthPages && input.auth && typeof input.auth === 'object') {
    if (input.auth.signin?.url) {
      pages.push({
        name: input.auth.signin.name || 'Sign In',
        url: input.auth.signin.url
      });
    }

    if (input.auth.signup?.url) {
      pages.push({
        name: input.auth.signup.name || 'Sign Up',
        url: input.auth.signup.url
      });
    }
  }

  if (Array.isArray(input.navbars)) {
    for (const navbar of input.navbars) {
      const navbarName =
        typeof navbar?.name === 'string' && navbar.name.trim()
          ? navbar.name.trim()
          : 'Navbar';

      if (config.inputParsing.includeNavbarUrls && Array.isArray(navbar?.urls)) {
        for (const entry of navbar.urls) {
          if (!entry?.url) continue;

          pages.push({
            name: entry.name?.trim()
              ? `${entry.name.trim()}`
              : `${navbarName}_Link`,
            url: entry.url
          });
        }
      }

      if (config.inputParsing.includeSectionUrls && Array.isArray(navbar?.sections)) {
        for (const section of navbar.sections) {
          const sectionTitle =
            typeof section?.title === 'string' && section.title.trim()
              ? section.title.trim()
              : '';

          if (!Array.isArray(section?.urls)) continue;

          for (const entry of section.urls) {
            if (!entry?.url) continue;

            pages.push({
              name: entry.name?.trim()
                ? `${entry.name.trim()}`
                : sectionTitle
                  ? `${sectionTitle}_Link`
                  : `${navbarName}_Section_Link`,
              url: entry.url
            });
          }
        }
      }
    }
  }

  return pages.map((page, index) => normalizeFlatPage(page, index));
}

function parseInputToPages(rawInput, config) {
  if (Array.isArray(rawInput)) {
    return rawInput.map((page, index) => normalizeFlatPage(page, index));
  }

  if (rawInput && typeof rawInput === 'object') {
    return extractPagesFromPartnerJson(rawInput, config);
  }

  throw new Error('Input JSON must be either an array of pages or the partner navigation object.');
}

function summarizeRun(pageResults) {
  const aggregate = {
    totalClickablesDetected: 0,
    safeClickables: 0,
    forbiddenClickables: 0,
    unknownClickables: 0,
    safeCandidates: 0,
    testedInteractions: 0,
    skippedSafeInteractions: 0,
    successfulInteractions: 0,
    failedInteractions: 0,
    navigationInteractions: 0,
    domChangeInteractions: 0,
    popupInteractions: 0,
    dialogInteractions: 0,
    noEffectInteractions: 0,
    errorInteractions: 0,
    notFoundInteractions: 0,
    interactionScreenshotsCreated: 0
  };

  for (const pageResult of pageResults) {
    aggregate.totalClickablesDetected += pageResult.clickableSummary?.totalDetected || 0;
    aggregate.safeClickables += pageResult.clickableSummary?.safe || 0;
    aggregate.forbiddenClickables += pageResult.clickableSummary?.forbidden || 0;
    aggregate.unknownClickables += pageResult.clickableSummary?.unknown || 0;

    aggregate.safeCandidates += pageResult.interactionSummary?.safeCandidates || 0;
    aggregate.testedInteractions += pageResult.interactionSummary?.tested || 0;
    aggregate.skippedSafeInteractions += pageResult.interactionSummary?.skippedSafe || 0;
    aggregate.successfulInteractions += pageResult.interactionSummary?.successful || 0;
    aggregate.failedInteractions += pageResult.interactionSummary?.failed || 0;
    aggregate.navigationInteractions += pageResult.interactionSummary?.navigations || 0;
    aggregate.domChangeInteractions += pageResult.interactionSummary?.domChanges || 0;
    aggregate.popupInteractions += pageResult.interactionSummary?.popups || 0;
    aggregate.dialogInteractions += pageResult.interactionSummary?.dialogs || 0;
    aggregate.noEffectInteractions += pageResult.interactionSummary?.noEffects || 0;
    aggregate.errorInteractions += pageResult.interactionSummary?.errors || 0;
    aggregate.notFoundInteractions += pageResult.interactionSummary?.notFound || 0;
    aggregate.interactionScreenshotsCreated +=
      pageResult.interactionSummary?.interactionScreenshotsCreated || 0;
  }

  return aggregate;
}

async function main() {
  const startedAt = new Date();

  console.log('Starting audit...');
  console.log(`Reading input file: ${AUDIT_CONFIG.paths.inputFile}`);

  await ensureOutputDirs(AUDIT_CONFIG.paths);

  const rawInput = await readJsonFile(AUDIT_CONFIG.paths.inputFile);
  const pagesParsed = parseInputToPages(rawInput, AUDIT_CONFIG);

  const { uniquePages, duplicates } = deduplicatePages(
    pagesParsed,
    AUDIT_CONFIG.urlNormalization
  );

  console.log(`Total pages extracted from input: ${pagesParsed.length}`);
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
        console.log(
          `  Clickables -> total: ${result.clickableSummary.totalDetected}, safe: ${result.clickableSummary.safe}, forbidden: ${result.clickableSummary.forbidden}, unknown: ${result.clickableSummary.unknown}`
        );
        console.log(
          `  Interactions -> tested: ${result.interactionSummary.tested}, success: ${result.interactionSummary.successful}, screenshots: ${result.interactionSummary.interactionScreenshotsCreated}`
        );
      } else {
        console.log(`  Failed -> ${result.error}`);
      }
    }
  } finally {
    await browser.close();
  }

  const finishedAt = new Date();
  const timestamp = buildTimestampForFileName(finishedAt);
  const runSummary = summarizeRun(pageResults);

  const summary = {
    runStartedAt: startedAt.toISOString(),
    runFinishedAt: finishedAt.toISOString(),
    inputFile: AUDIT_CONFIG.paths.inputFile,
    browserType: AUDIT_CONFIG.browser.browserType,
    headless: AUDIT_CONFIG.browser.headless,
    totalPagesExtractedFromInput: pagesParsed.length,
    uniquePagesVisited: uniquePages.length,
    duplicatePagesSkipped: duplicates.length,
    pagesSucceeded: pageResults.filter((r) => r.status === 'success').length,
    pagesFailed: pageResults.filter((r) => r.status === 'failed').length,
    totalClickablesDetected: runSummary.totalClickablesDetected,
    safeClickables: runSummary.safeClickables,
    forbiddenClickables: runSummary.forbiddenClickables,
    unknownClickables: runSummary.unknownClickables,
    safeCandidates: runSummary.safeCandidates,
    testedInteractions: runSummary.testedInteractions,
    skippedSafeInteractions: runSummary.skippedSafeInteractions,
    successfulInteractions: runSummary.successfulInteractions,
    failedInteractions: runSummary.failedInteractions,
    navigationInteractions: runSummary.navigationInteractions,
    domChangeInteractions: runSummary.domChangeInteractions,
    popupInteractions: runSummary.popupInteractions,
    dialogInteractions: runSummary.dialogInteractions,
    noEffectInteractions: runSummary.noEffectInteractions,
    errorInteractions: runSummary.errorInteractions,
    notFoundInteractions: runSummary.notFoundInteractions,
    interactionScreenshotsCreated: runSummary.interactionScreenshotsCreated
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
  console.log('Audit completed.');
}

main().catch((error) => {
  console.error('Fatal error while running audit:');
  console.error(error);
  process.exit(1);
});