// Where runtime-writable state lives: the accounts store and the in-progress
// game snapshot. On Render this points at a mounted PERSISTENT DISK
// (DATA_DIR=/var/data) so it survives restarts, redeploys, and cold starts;
// with no env set it defaults to <cwd>/data for local dev.
//
// IMPORTANT: the read-only game seed (data/seed.json) is always read from
// <cwd>/data via lib/games.ts — never from here. The persistent disk must be
// mounted at its OWN path (e.g. /var/data), NOT over the image's data/ dir, or
// it would shadow seed.json and the app would have no games to load.

import fs from 'fs';
import path from 'path';

export const DATA_DIR = process.env.DATA_DIR
  ? path.resolve(process.env.DATA_DIR)
  : path.join(process.cwd(), 'data');

export function ensureDataDir(): void {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

export function dataPath(file: string): string {
  return path.join(DATA_DIR, file);
}
