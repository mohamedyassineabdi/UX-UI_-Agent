import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';

const ROOT_DIR = process.cwd();
const NAVIGATOR_DIR = path.join(ROOT_DIR, 'navigator');
const GENERATED_DIR = path.join(ROOT_DIR, 'shared', 'generated');
const OUTPUT_JSON = path.join(GENERATED_DIR, 'website_menu.json');
const AUDITOR_ENTRY = path.join(ROOT_DIR, 'src', 'main.js');

function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: 'inherit',
      shell: process.platform === 'win32',
      ...options
    });

    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited with code ${code}`));
    });

    child.on('error', reject);
  });
}

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

async function ensureFileExists(filePath) {
  try {
    await fs.access(filePath);
  } catch {
    throw new Error(`Expected file was not created: ${filePath}`);
  }
}

async function main() {
  const targetUrl = process.argv[2];

  if (!targetUrl) {
    console.error('Usage: npm run scan -- <website-url>');
    process.exit(1);
  }

  await ensureDir(GENERATED_DIR);

  console.log('\n[1/2] Running partner crawler...\n');

  await runCommand(
    'python',
    [path.join(NAVIGATOR_DIR, 'crawler.py'), targetUrl],
    { cwd: GENERATED_DIR }
  );

  await ensureFileExists(OUTPUT_JSON);

  console.log('\n[2/2] Running auditor...\n');

  await runCommand(
    'node',
    [AUDITOR_ENTRY],
    { cwd: ROOT_DIR }
  );

  console.log('\nPipeline completed successfully.');
}

main().catch((error) => {
  console.error('\nPipeline failed:');
  console.error(error.message);
  process.exit(1);
});