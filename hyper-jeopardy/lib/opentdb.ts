// Open Trivia Database (opentdb.com) source for mini-games.
//
// Policy (per the game design):
//   - difficulty defaults to "medium"
//   - category is chosen RANDOMLY from all topics EXCEPT
//     13 = "Entertainment: Musicals & Theatres", unless a mini-game forces a
//     specific category.
//
// SERVER-ONLY: uses global fetch (Node 20+). Never import this into a client
// bundle — client code should `import type { TriviaQuestion }` only.
//
// Robustness: OpenTDB rate-limits to ~1 request / 5s per IP (response_code 5)
// and hands out a session token to avoid repeats within a game. We serialize
// requests with 5s spacing, manage the token, and fall back to a small bundled
// question bank if the API is unreachable or returns nothing — so a mini-game
// never hard-fails for lack of a question.

export interface TriviaQuestion {
  category: string;
  categoryId: number | null;
  difficulty: string;
  type: 'multiple' | 'boolean';
  question: string;
  correct: string;
  incorrect: string[];
  choices: string[]; // correct + incorrect, shuffled — index-stable for the round
  source: 'opentdb' | 'fallback';
}

// OpenTDB's stable category ids (9–32). Kept inline so we don't spend a request
// (or a network round-trip) just to list them.
export const OPENTDB_CATEGORIES: Record<number, string> = {
  9: 'General Knowledge',
  10: 'Entertainment: Books',
  11: 'Entertainment: Film',
  12: 'Entertainment: Music',
  13: 'Entertainment: Musicals & Theatres',
  14: 'Entertainment: Television',
  15: 'Entertainment: Video Games',
  16: 'Entertainment: Board Games',
  17: 'Science & Nature',
  18: 'Science: Computers',
  19: 'Science: Mathematics',
  20: 'Mythology',
  21: 'Sports',
  22: 'Geography',
  23: 'History',
  24: 'Politics',
  25: 'Art',
  26: 'Celebrities',
  27: 'Animals',
  28: 'Vehicles',
  29: 'Entertainment: Comics',
  30: 'Science: Gadgets',
  31: 'Entertainment: Japanese Anime & Manga',
  32: 'Entertainment: Cartoon & Animations',
};

// Categories the random picker never draws from:
//   13 — Musicals & Theatres (too niche for a speed round)
//   31 — Japanese Anime & Manga (banned by request; it kept surfacing)
export const EXCLUDED_CATEGORY_IDS = new Set<number>([13, 31]);

export const ALLOWED_CATEGORY_IDS = Object.keys(OPENTDB_CATEGORIES)
  .map(Number)
  .filter((id) => !EXCLUDED_CATEGORY_IDS.has(id));

export function randomAllowedCategory(): number {
  return ALLOWED_CATEGORY_IDS[Math.floor(Math.random() * ALLOWED_CATEGORY_IDS.length)];
}

interface RawResult {
  category: string;
  type: 'multiple' | 'boolean';
  difficulty: string;
  question: string;
  correct_answer: string;
  incorrect_answers: string[];
}

const API = 'https://opentdb.com';
const dec = (s: string) => {
  try { return decodeURIComponent(s); } catch { return s; }
};

function shuffle<T>(arr: T[]): T[] {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Map one OpenTDB result (encode=url3986) into our shape. Exported for testing.
export function mapResult(r: RawResult, categoryId: number | null): TriviaQuestion {
  const correct = dec(r.correct_answer);
  const incorrect = (r.incorrect_answers || []).map(dec);
  return {
    category: dec(r.category),
    categoryId,
    difficulty: dec(r.difficulty),
    type: r.type,
    question: dec(r.question),
    correct,
    incorrect,
    choices: shuffle([correct, ...incorrect]),
    source: 'opentdb',
  };
}

// ── rate-limit + token state (module-level; single server process) ──────────
let lastRequestAt = 0;
let sessionToken: string | null = null;
const MIN_SPACING_MS = 5200; // OpenTDB: ~1 request / 5s per IP
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function spacing() {
  const dt = Date.now() - lastRequestAt;
  if (dt < MIN_SPACING_MS) await sleep(MIN_SPACING_MS - dt);
  lastRequestAt = Date.now();
}

async function getToken(): Promise<string | null> {
  if (sessionToken) return sessionToken;
  try {
    const r = await fetch(`${API}/api_token.php?command=request`);
    const d = await r.json();
    if (d.response_code === 0 && d.token) sessionToken = d.token;
  } catch { /* token is optional; proceed without */ }
  return sessionToken;
}

export interface FetchOpts {
  amount?: number;
  category?: number | 'random';
  difficulty?: 'easy' | 'medium' | 'hard';
  type?: 'multiple' | 'boolean' | 'any';
}

export async function fetchTrivia(opts: FetchOpts = {}): Promise<TriviaQuestion[]> {
  const amount = Math.max(1, Math.min(50, opts.amount ?? 1));
  const categoryId =
    opts.category === undefined || opts.category === 'random'
      ? randomAllowedCategory()
      : opts.category;
  const difficulty = opts.difficulty ?? 'medium';
  const type = opts.type ?? 'multiple';

  const build = (token: string | null) => {
    const p = new URLSearchParams({
      amount: String(amount),
      category: String(categoryId),
      difficulty,
      encode: 'url3986',
    });
    if (type !== 'any') p.set('type', type);
    if (token) p.set('token', token);
    return `${API}/api.php?${p.toString()}`;
  };

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const token = await getToken();
      await spacing();
      const res = await fetch(build(token));
      const data = await res.json();
      const code = data.response_code;

      if (code === 0 && Array.isArray(data.results) && data.results.length) {
        return (data.results as RawResult[]).map((r) => mapResult(r, categoryId));
      }
      if (code === 3 || code === 4) { sessionToken = null; continue; }      // token missing/exhausted → reset
      if (code === 5) { await sleep(MIN_SPACING_MS); continue; }            // rate-limited → wait + retry
      break; // 1 (no results) / 2 (bad param) — don't spin; fall back
    } catch {
      // network error (or blocked egress) — fall back
      break;
    }
  }
  return fallbackQuestions(amount, categoryId, difficulty);
}

// ── bundled fallback bank ───────────────────────────────────────────────────
// Used only when OpenTDB is unreachable/rate-limited/empty. Keeps mini-games
// playable offline; clearly tagged source:'fallback'.
const FALLBACK: Array<Omit<TriviaQuestion, 'choices' | 'source' | 'difficulty'>> = [
  { category: 'General Knowledge', categoryId: 9, type: 'multiple', question: 'What is the hardest natural substance on Earth?', correct: 'Diamond', incorrect: ['Quartz', 'Titanium', 'Obsidian'] },
  { category: 'Science & Nature', categoryId: 17, type: 'multiple', question: 'What is the chemical symbol for potassium?', correct: 'K', incorrect: ['P', 'Po', 'Pt'] },
  { category: 'Geography', categoryId: 22, type: 'multiple', question: 'The Danube river empties into which sea?', correct: 'Black Sea', incorrect: ['Caspian Sea', 'Adriatic Sea', 'Baltic Sea'] },
  { category: 'History', categoryId: 23, type: 'multiple', question: 'In which year did the Berlin Wall fall?', correct: '1989', incorrect: ['1987', '1991', '1985'] },
  { category: 'Entertainment: Film', categoryId: 11, type: 'multiple', question: 'Who directed the 1975 film "Jaws"?', correct: 'Steven Spielberg', incorrect: ['George Lucas', 'Ridley Scott', 'Francis Ford Coppola'] },
  { category: 'Sports', categoryId: 21, type: 'multiple', question: 'How many players are on the field per side in standard soccer?', correct: '11', incorrect: ['9', '10', '12'] },
  { category: 'Art', categoryId: 25, type: 'multiple', question: 'Which artist painted "The Starry Night"?', correct: 'Vincent van Gogh', incorrect: ['Claude Monet', 'Pablo Picasso', 'Salvador Dalí'] },
  { category: 'Mythology', categoryId: 20, type: 'multiple', question: 'In Greek myth, who is the god of the sea?', correct: 'Poseidon', incorrect: ['Zeus', 'Hades', 'Apollo'] },
];

function fallbackQuestions(amount: number, categoryId: number, difficulty: string): TriviaQuestion[] {
  const pool = shuffle(FALLBACK);
  const out: TriviaQuestion[] = [];
  for (let i = 0; i < amount; i++) {
    const q = pool[i % pool.length];
    out.push({
      ...q,
      difficulty,
      choices: shuffle([q.correct, ...q.incorrect]),
      source: 'fallback',
    });
  }
  return out;
}
