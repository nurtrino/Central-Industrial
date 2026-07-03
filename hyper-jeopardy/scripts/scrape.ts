/**
 * Scrapes games from j-archive and appends them to scripts/scraped-extra.ts.
 *
 *   npx tsx scripts/scrape.ts              # scrape 50 games walking back from
 *                                          # one before the oldest game already
 *                                          # in the dataset
 *   npx tsx scripts/scrape.ts 100          # ditto, but 100 games
 *   npx tsx scripts/scrape.ts 100 8500     # start from show #8500 specifically
 *
 * After scraping, run `npx tsx scripts/build-seed.ts` to regenerate
 * data/seed.json. The runtime app only reads seed.json.
 */

import fs from 'fs';
import path from 'path';
import * as cheerio from 'cheerio';
import { GAMES, type GameData, type CategoryData, type ClueData, type FinalData } from './games-data';
import { EXTRA_GAMES } from './scraped-extra';

const BASE_URL = 'https://j-archive.com';
const DELAY_MS = 1500;

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function parseClueElement($: cheerio.CheerioAPI, el: any): { question: string; answer: string; isDailyDouble: boolean } | null {
  const $el = $(el);
  const isDailyDouble = !!$el.find('.clue_value_daily_double').length;

  const questionEl = $el.find('.clue_text').first();
  if (!questionEl.length) return null;
  // Strip "(image clue)" link text — j-archive doesn't archive the media,
  // so we drop the placeholder so the spoken question reads cleanly.
  const qClone = questionEl.clone();
  qClone.find('a').remove();
  const question = qClone.text().trim();

  // Correct response sits in a second .clue_text (toggled by mouseover), or
  // is embedded in the onmouseover handler as a fallback.
  let answer = $el.find('.clue_text').eq(1).text().trim();
  if (!answer) {
    const toggle = $el.find('[onmouseover]').attr('onmouseover') ?? '';
    const m = toggle.match(/correct_response['":\s]+([^<"]+)/i);
    answer = m ? m[1].trim() : '';
  }

  if (!question || !answer) return null;
  return { question, answer, isDailyDouble };
}

function parseRound($: cheerio.CheerioAPI, roundSelector: string): CategoryData[] | null {
  const round = $(roundSelector);
  if (!round.length) return null;

  const categoryNames: string[] = [];
  round.find('.category_name').each((_, el) => { categoryNames.push($(el).text().trim()); });
  if (categoryNames.length !== 6) return null;

  // J-Archive lays out clues in a 6-wide, 5-tall grid; iteration order is
  // row-major (row 0 col 0..5, then row 1 col 0..5, ...).
  const grid: Array<Array<{ question: string; answer: string; isDailyDouble: boolean } | null>> =
    Array.from({ length: 6 }, () => Array.from({ length: 5 }, () => null));

  round.find('.clue').each((i, el) => {
    const col = i % 6;
    const row = Math.floor(i / 6);
    if (row > 4 || col > 5) return;
    const parsed = parseClueElement($, el);
    grid[col][row] = parsed;
  });

  const categories: CategoryData[] = [];
  for (let col = 0; col < 6; col++) {
    const colClues = grid[col];
    if (colClues.some(c => c === null)) return null;
    const clues: ClueData[] = colClues.map(c => ({
      question: c!.question,
      answer: c!.answer,
    }));
    const ddIdx = colClues.findIndex(c => c!.isDailyDouble);
    const category: CategoryData = { category: categoryNames[col], clues };
    if (ddIdx >= 0) category.dailyDoubleAt = ddIdx;
    categories.push(category);
  }
  return categories;
}

function parseFinal($: cheerio.CheerioAPI): FinalData | null {
  const fj = $('#final_jeopardy_round');
  if (!fj.length) return null;
  const category = fj.find('.category_name').first().text().trim();
  const questionEl = fj.find('.clue_text').first();
  if (!questionEl.length) return null;
  const qClone = questionEl.clone();
  qClone.find('a').remove();
  const question = qClone.text().trim();

  const toggle = fj.find('[onmouseover]').attr('onmouseover') ?? '';
  const m = toggle.match(/correct_response['":\s]+([^<"]+)/i);
  const answer = m ? m[1].trim() : fj.find('.clue_text').eq(1).text().trim();
  if (!category || !question || !answer) return null;
  return { category, question, answer };
}

async function scrapeGame(gameId: number): Promise<GameData | null> {
  const url = `${BASE_URL}/showgame.php?game_id=${gameId}`;
  let html: string;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 Jeopardy-App scraper (educational)' },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return null;
    html = await res.text();
  } catch {
    return null;
  }

  const $ = cheerio.load(html);
  const title = $('#game_title').text().trim();
  if (!title) return null;

  const showMatch = title.match(/Show #(\d+)/);
  const showNumber = showMatch ? parseInt(showMatch[1], 10) : gameId;
  const airDateMatch = title.match(/(\w+ \d+, \d{4})/);
  const airDate = airDateMatch ? airDateMatch[1] : 'Unknown';

  const jRound = parseRound($, '#jeopardy_round');
  const djRound = parseRound($, '#double_jeopardy_round');
  const fj = parseFinal($);
  if (!jRound || !djRound || !fj) return null;

  return { showNumber, airDate, jeopardy: jRound, doubleJeopardy: djRound, finalJeopardy: fj };
}

function writeExtras(games: GameData[]): void {
  const sorted = [...games].sort((a, b) => a.showNumber - b.showNumber);
  const body = sorted.map(g => '  ' + JSON.stringify(g, null, 2).replace(/\n/g, '\n  ')).join(',\n');
  const out =
`// Output target for scripts/scrape.ts. Kept separate from games-data.ts so
// the hand-curated main dataset stays untouched. build-seed.ts merges this
// with GAMES (by showNumber) when generating data/seed.json.

import type { GameData } from './games-data';

export const EXTRA_GAMES: GameData[] = [
${body}
];
`;
  fs.writeFileSync(path.join(process.cwd(), 'scripts', 'scraped-extra.ts'), out);
}

async function main() {
  const count = parseInt(process.argv[2] ?? '50', 10);
  const explicitStart = process.argv[3] ? parseInt(process.argv[3], 10) : null;

  const knownShows = new Set<number>([
    ...GAMES.map(g => g.showNumber),
    ...EXTRA_GAMES.map(g => g.showNumber),
  ]);
  const oldestKnown = Math.min(...knownShows);
  const startId = explicitStart ?? (Number.isFinite(oldestKnown) ? oldestKnown - 1 : 9000);

  console.log(`\nJeopardy Scraper`);
  console.log(`================`);
  console.log(`Already have:    ${knownShows.size} games (oldest: #${oldestKnown})`);
  console.log(`Target:          ${count} new games`);
  console.log(`Walking back from: #${startId}`);
  console.log(`Delay:           ${DELAY_MS}ms between requests\n`);

  const collected: GameData[] = [...EXTRA_GAMES];
  let scanned = 0;
  let inserted = 0;

  // Scan up to count*4 IDs in case we hit gaps (404s, special tournament shows
  // that don't parse cleanly).
  const maxScan = count * 4;
  for (let id = startId; id > 0 && inserted < count && scanned < maxScan; id--) {
    scanned++;
    const game = await scrapeGame(id);
    if (!game) {
      process.stdout.write(`  game_id=${id}: skip\n`);
    } else if (knownShows.has(game.showNumber)) {
      process.stdout.write(`  game_id=${id} -> show #${game.showNumber}: already have\n`);
    } else {
      collected.push(game);
      knownShows.add(game.showNumber);
      inserted++;
      process.stdout.write(`  game_id=${id} -> show #${game.showNumber} (${game.airDate})\n`);
    }

    // Persist incrementally so a Ctrl-C doesn't lose progress.
    if (inserted > 0 && inserted % 5 === 0) writeExtras(collected);

    await sleep(DELAY_MS);
  }

  writeExtras(collected);
  console.log(`\nDone. Inserted ${inserted} new games (scanned ${scanned} IDs).`);
  console.log(`scripts/scraped-extra.ts now holds ${collected.length} games.`);
  console.log(`Run \`npm run build-seed\` to refresh data/seed.json.`);
}

main().catch(err => { console.error(err); process.exit(1); });
