import fs from 'fs';
import path from 'path';

// Re-exported for convenience so existing `from '@/lib/games'` imports keep
// working; the actual sentinel lives in lib/clueSentinel.ts (no fs/path) so
// client components can import it without pulling this server-only module
// (and its Node fs/path usage) into the browser bundle.
export { UNAVAILABLE_CLUE_SENTINEL, isUnavailableClue } from './clueSentinel';

export interface ClueForPlay {
  id: number;
  value: number;
  question: string;
  answer: string;
  isDailyDouble: boolean;
  used: boolean;
}

export interface CategoryForPlay {
  id: number;
  name: string;
  clues: ClueForPlay[];
}

export interface GameForPlay {
  showNumber: number;
  airDate: string;
  jeopardyRound: CategoryForPlay[];
  doubleJeopardyRound: CategoryForPlay[];
  finalJeopardy: { category: string; question: string; answer: string };
}

interface SeedGame {
  id: number;
  show_number: number;
  air_date: string;
}

interface SeedCategory {
  id: number;
  game_id: number;
  round: string;
  name: string;
  position: number;
}

interface SeedClue {
  id: number;
  category_id: number;
  game_id: number;
  value: number;
  question: string;
  answer: string;
  is_daily_double: number;
  position: number;
}

let _games: GameForPlay[] | null = null;

function loadGames(): GameForPlay[] {
  if (_games) return _games;

  const seedPath = path.join(process.cwd(), 'data', 'seed.json');
  const seed = JSON.parse(fs.readFileSync(seedPath, 'utf8')) as {
    games: SeedGame[];
    categories: SeedCategory[];
    clues: SeedClue[];
  };

  // Index by id for fast lookup
  const catsByGameId = new Map<number, SeedCategory[]>();
  for (const cat of seed.categories) {
    const arr = catsByGameId.get(cat.game_id) ?? [];
    arr.push(cat);
    catsByGameId.set(cat.game_id, arr);
  }

  const cluesByCatId = new Map<number, SeedClue[]>();
  for (const clue of seed.clues) {
    const arr = cluesByCatId.get(clue.category_id) ?? [];
    arr.push(clue);
    cluesByCatId.set(clue.category_id, arr);
  }

  const games: GameForPlay[] = [];

  for (const g of seed.games) {
    const cats = (catsByGameId.get(g.id) ?? []).sort((a, b) => a.position - b.position);

    const jeopardyCats: CategoryForPlay[] = [];
    const doubleCats: CategoryForPlay[] = [];
    let finalJeopardy: GameForPlay['finalJeopardy'] = { category: '', question: '', answer: '' };

    for (const cat of cats) {
      const clues = (cluesByCatId.get(cat.id) ?? [])
        .sort((a, b) => a.position - b.position)
        .map(c => ({
          id: c.id,
          value: c.value,
          question: c.question,
          answer: c.answer,
          isDailyDouble: c.is_daily_double === 1,
          used: false,
        }));

      if (cat.round === 'jeopardy') {
        jeopardyCats.push({ id: cat.id, name: cat.name, clues });
      } else if (cat.round === 'double_jeopardy') {
        doubleCats.push({ id: cat.id, name: cat.name, clues });
      } else if (cat.round === 'final_jeopardy' && clues[0]) {
        finalJeopardy = { category: cat.name, question: clues[0].question, answer: clues[0].answer };
      }
    }

    if (jeopardyCats.length && doubleCats.length && finalJeopardy.category) {
      games.push({
        showNumber: g.show_number,
        airDate: g.air_date,
        jeopardyRound: jeopardyCats,
        doubleJeopardyRound: doubleCats,
        finalJeopardy,
      });
    }
  }

  console.log(`[games] Loaded ${games.length} games from seed.json`);
  _games = games;
  return _games;
}

export function loadRandomGame(): GameForPlay | null {
  const games = loadGames();
  if (!games.length) return null;
  return games[Math.floor(Math.random() * games.length)];
}

export function getGameCount(): number {
  return loadGames().length;
}
