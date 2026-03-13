import fs from 'node:fs/promises';
import path from 'node:path';

export async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

export function joinPath(...segments) {
  return path.join(...segments);
}

export async function ensureOutputDirs(pathsConfig) {
  await ensureDir(pathsConfig.screenshotDir);
  await ensureDir(pathsConfig.resultsDir);
  await ensureDir(joinPath(pathsConfig.screenshotDir, 'interactions'));
}

export async function readJsonFile(filePath) {
  const raw = await fs.readFile(filePath, 'utf-8');
  return JSON.parse(raw);
}

export async function writeJsonFile(filePath, data) {
  const json = JSON.stringify(data, null, 2);
  await fs.writeFile(filePath, json, 'utf-8');
}

export function buildTimestampForFileName(date = new Date()) {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const min = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');

  return `${yyyy}-${mm}-${dd}_${hh}-${min}-${ss}`;
}