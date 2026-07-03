/**
 * Builds data/seed.json from scripts/games-data.ts (the hand-authored 5 games).
 *
 * Run with: npx tsx scripts/build-seed.ts
 *
 * The output schema matches what lib/db.ts:seedFromJsonIfEmpty expects:
 *   { games: [...], categories: [...], clues: [...] }
 * where every row has an explicit primary-key id assigned here.
 */

import fs from 'fs';
import path from 'path';
import { GAMES, type GameData, type CategoryData } from './games-data';
import { EXTRA_GAMES } from './scraped-extra';

const J_VALUES = [200, 400, 600, 800, 1000];
const DJ_VALUES = [400, 800, 1200, 1600, 2000];

interface GameRow { id: number; show_number: number; air_date: string; scraped_at: string }
interface CategoryRow { id: number; game_id: number; round: string; name: string; position: number }
interface ClueRow {
  id: number; category_id: number; game_id: number; value: number;
  question: string; answer: string; is_daily_double: number; position: number;
}

function validateGame(g: GameData, idx: number): string | null {
  const tag = `game[${idx}] (#${g.showNumber})`;
  if (g.jeopardy.length !== 6) return `${tag}: jeopardy must have 6 categories`;
  if (g.doubleJeopardy.length !== 6) return `${tag}: doubleJeopardy must have 6 categories`;
  if (!g.finalJeopardy?.category || !g.finalJeopardy?.question || !g.finalJeopardy?.answer) {
    return `${tag}: finalJeopardy is incomplete`;
  }
  for (const round of ['jeopardy', 'doubleJeopardy'] as const) {
    for (let ci = 0; ci < g[round].length; ci++) {
      const c = g[round][ci];
      if (c.clues.length !== 5) return `${tag}.${round}[${ci}] (${c.category}): expected 5 clues, got ${c.clues.length}`;
      for (let cli = 0; cli < c.clues.length; cli++) {
        const cl = c.clues[cli];
        if (!cl.question?.trim()) return `${tag}.${round}[${ci}][${cli}]: empty question`;
        if (!cl.answer?.trim()) return `${tag}.${round}[${ci}][${cli}]: empty answer`;
      }
      if (c.dailyDoubleAt !== undefined && (c.dailyDoubleAt < 1 || c.dailyDoubleAt > 4)) {
        return `${tag}.${round}[${ci}] (${c.category}): dailyDoubleAt must be 1-4`;
      }
    }
  }
  const jDDs = g.jeopardy.filter(c => c.dailyDoubleAt !== undefined).length;
  const djDDs = g.doubleJeopardy.filter(c => c.dailyDoubleAt !== undefined).length;
  if (jDDs !== 1) return `${tag}: jeopardy round must have exactly 1 Daily Double, got ${jDDs}`;
  if (djDDs !== 2) return `${tag}: doubleJeopardy round must have exactly 2 Daily Doubles, got ${djDDs}`;
  return null;
}

function emitRound(
  game: GameData,
  gameId: number,
  roundKey: 'jeopardy' | 'double_jeopardy',
  categories: CategoryData[],
  values: number[],
  catIdGen: () => number,
  clueIdGen: () => number,
  outCats: CategoryRow[],
  outClues: ClueRow[],
): void {
  void game;
  categories.forEach((cat, position) => {
    const catId = catIdGen();
    outCats.push({ id: catId, game_id: gameId, round: roundKey, name: cat.category, position });
    cat.clues.forEach((clue, clueIdx) => {
      outClues.push({
        id: clueIdGen(),
        category_id: catId,
        game_id: gameId,
        value: values[clueIdx],
        question: clue.question.trim(),
        answer: clue.answer.trim(),
        is_daily_double: cat.dailyDoubleAt === clueIdx ? 1 : 0,
        position: clueIdx,
      });
    });
  });
}

function build() {
  // Merge main dataset with anything fresh from scripts/scrape.ts (which
  // writes to scraped-extra.ts). Dedup by showNumber, EXTRA wins so a
  // re-scrape can replace an old entry.
  const byShow = new Map<number, GameData>();
  for (const g of GAMES) byShow.set(g.showNumber, g);
  for (const g of EXTRA_GAMES) byShow.set(g.showNumber, g);
  const allGames = Array.from(byShow.values()).sort((a, b) => a.showNumber - b.showNumber);

  if (allGames.length === 0) {
    throw new Error('No games found in games-data.ts or scraped-extra.ts');
  }

  const validGames: GameData[] = [];
  let skipped = 0;
  allGames.forEach((g, i) => {
    const err = validateGame(g, i);
    if (err) { console.warn(`  skip: ${err}`); skipped++; }
    else validGames.push(g);
  });
  console.log(`Validated: ${validGames.length} good, ${skipped} skipped (main: ${GAMES.length}, extra: ${EXTRA_GAMES.length})`);

  const games: GameRow[] = [];
  const categories: CategoryRow[] = [];
  const clues: ClueRow[] = [];

  let nextCatId = 1;
  let nextClueId = 1;
  const catIdGen = () => nextCatId++;
  const clueIdGen = () => nextClueId++;

  validGames.forEach((g, idx) => {
    const gameId = idx + 1;
    games.push({
      id: gameId,
      show_number: g.showNumber,
      air_date: g.airDate,
      scraped_at: new Date().toISOString().slice(0, 19).replace('T', ' '),
    });

    emitRound(g, gameId, 'jeopardy', g.jeopardy, J_VALUES, catIdGen, clueIdGen, categories, clues);
    emitRound(g, gameId, 'double_jeopardy', g.doubleJeopardy, DJ_VALUES, catIdGen, clueIdGen, categories, clues);

    // Final Jeopardy: 1 category, 1 clue
    const fjCatId = catIdGen();
    categories.push({ id: fjCatId, game_id: gameId, round: 'final_jeopardy', name: g.finalJeopardy.category, position: 0 });
    clues.push({
      id: clueIdGen(),
      category_id: fjCatId,
      game_id: gameId,
      value: 0,
      question: g.finalJeopardy.question.trim(),
      answer: g.finalJeopardy.answer.trim(),
      is_daily_double: 0,
      position: 0,
    });
  });

  const outDir = path.join(process.cwd(), 'data');
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, 'seed.json');
  fs.writeFileSync(outPath, JSON.stringify({ games, categories, clues }, null, 2));

  // Tear down any local SQLite DB so the new seed gets re-loaded next start.
  for (const f of ['jeopardy.db', 'jeopardy.db-shm', 'jeopardy.db-wal']) {
    const p = path.join(outDir, f);
    if (fs.existsSync(p)) fs.unlinkSync(p);
  }

  const sizeKB = (fs.statSync(outPath).size / 1024).toFixed(1);
  console.log(`Built seed.json: ${games.length} games, ${categories.length} categories, ${clues.length} clues (${sizeKB} KB)`);
  console.log(`Output: ${outPath}`);
}

build();
