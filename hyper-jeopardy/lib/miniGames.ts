// Phase 1 mini-game engine (server-side). Three games fire on HYPER MODE cells:
//   anagram_race   — unscramble the word, points by solve order (multi)
//   rapid_fire     — 45s sprint through one random OpenTDB category (multi)
//   letter_reveal  — guess the 5-letter word; fewer letters revealed = more (multi)
//
// Secret answers live ONLY in `mgSecret` (module-level, server process) — never
// in miniGameData, which is broadcast to every client. The generic per-game
// state shapes are exported as types so the client can render them.

import type { GameState } from './gameEngine';
import { ANAGRAM_WORDS, FIVE_LETTER_WORDS, randomWord, scramble } from './wordBanks';

// ── timing / scoring ────────────────────────────────────────────────────────
export const INTRO_MS = 5_000;        // rules screen shown on all screens before play
export const HYPER_ROUND_MS = 60_000; // Anagram / Letter Reveal round cap (also ends
                                       // early once every player is resolved)
export const RAPID_ROUND_MS = 30_000; // Rapid Fire: a hard 30s sprint, no give up
export const REVEAL_INTERVAL_MS = 4_500;
export const RESULTS_MS = 5_000;      // per-round standings shown before the board returns

// Memory Matrix — humanbenchmark.com/tests/memory style, starting at their
// level 3 (4×4 grid, 5 lit) and topping out at 5×5 with 8 lit. Each player
// runs their OWN ladder on their phone: the pattern flashes, hides, and they
// tap every lit cell from memory. House rule: 3 wrong guesses AT ANY TIME
// across the whole run and you're out. Furthest-fastest wins.
export interface MemoryLevelSpec { grid: number; lit: number; }
export const MEMORY_LEVELS: MemoryLevelSpec[] = [
  { grid: 16, lit: 5 },  // 4×4 — humanbenchmark level 3
  { grid: 16, lit: 6 },
  { grid: 25, lit: 7 },  // grid grows to 5×5
  { grid: 25, lit: 8 },
  { grid: 25, lit: 9 },  // final rung — one notch harder
];
export const MEMORY_STRIKES = 3;

// Placement scoring shared by every mini-game: points are a MULTIPLE of the
// board-cell value by finish position — 1st = full value, 2nd = half, 3rd = 0,
// 4th+ = −full value. Players who didn't place (no solve / no correct answer)
// get 0. Half values round to the nearest point (board values are always even).
const PLACEMENT_MULT = [1, 0.5, 0, -1];

// ── broadcast state shapes (no secrets) ─────────────────────────────────────
export type MiniGameStatus = 'intro' | 'playing' | 'results';

export interface MiniGameResultRow {
  playerId: string;
  name: string;
  points: number;
  detail: string;
}

interface BaseMGData {
  key: string;
  status: MiniGameStatus;
  endsAt: number | null;               // round timer for client countdown
  roundScores: Record<string, number>; // points earned THIS round
  results: MiniGameResultRow[] | null;
  answerReveal: string | null;         // shown on the results screen
}

export interface AnagramData extends BaseMGData {
  key: 'anagram_race';
  scrambled: string;      // empty during the intro; set when play begins
  wordLen: number;
  value: number;          // board-space value → scoring multiplier base
  solvedOrder: string[];
  solved: Record<string, boolean>;
}

export interface RapidQ { question: string; choices: string[]; category: string; }
export interface RapidData extends BaseMGData {
  key: 'rapid_fire';
  category: string;
  value: number;          // board-cell value → placement scoring base
  questions: RapidQ[];
  total: number;
  progress: Record<string, number>;
  correct: Record<string, number>;
  wrong: Record<string, number>;
  done: Record<string, boolean>;
}

export interface LetterData extends BaseMGData {
  key: 'letter_reveal';
  value: number;               // board-cell value → placement scoring base
  wordLen: number;
  letters: (string | null)[];  // revealed letters; null = still hidden
  revealCount: number;
  solvedOrder: string[];       // finish order → placement multiplier
  solved: Record<string, boolean>;
  solvedAtReveal: Record<string, number>;
}

export interface MemoryData extends BaseMGData {
  key: 'memory_match';
  value: number;                       // board-cell value → placement scoring base
  levels: MemoryLevelSpec[];           // the ladder (broadcast so clients can size grids)
  level: Record<string, number>;       // per-player rung (0-based); === levels.length → cleared it all
  pattern: Record<string, number[]>;   // per-player CURRENT pattern (their phone flashes it)
  patternSeq: Record<string, number>;  // bumps on every new pattern → phone re-flashes
  found: Record<string, number[]>;     // correct cells found so far this level
  wrongTotal: Record<string, number>;  // strikes — 3 wrong guesses at ANY time → out
  out: Record<string, boolean>;
  doneAt: Record<string, number>;      // finished-or-eliminated timestamp (ranking tie-break)
  solvedOrder: string[];               // cleared-the-whole-ladder order
  solved: Record<string, boolean>;
}

export type MiniGameData = AnagramData | RapidData | LetterData | MemoryData;

export interface ActionResult {
  changed: boolean;   // did broadcast-worthy state change
  complete: boolean;  // did the round just finish (every player resolved)
  feedback: {
    correct?: boolean; points?: number; invalid?: boolean; already?: boolean; stale?: boolean;
    // Memory Matrix extras:
    levelUp?: boolean; finished?: boolean; out?: boolean;
  };
}

// ── server-only secrets ─────────────────────────────────────────────────────
interface MGSecret {
  anagramAnswer?: string;
  anagramScrambled?: string; // withheld until play begins (hidden during intro)
  letterAnswer?: string;
  revealOrder?: number[];
  rapidCorrect?: string[]; // normalized correct answer per question index
}
let mgSecret: MGSecret = {};

// ── helpers ─────────────────────────────────────────────────────────────────
const normalize = (s: string) => s.toUpperCase().replace(/[^A-Z0-9]/g, '');

function shuffledIndices(n: number): number[] {
  const a = Array.from({ length: n }, (_, i) => i);
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function connected(state: GameState) {
  return state.players.filter(p => p.connected);
}

// A player is "resolved" for the round when they've finished it their game's
// way (solved / done / out). The round ends once every connected player is
// resolved — or when the round clock runs out (see the server orchestration).
function isResolved(d: MiniGameData, id: string): boolean {
  if (d.key === 'rapid_fire') return !!d.done[id];
  if (d.key === 'memory_match') return !!d.solved[id] || !!d.out[id]; // cleared the ladder or struck out
  return !!d.solved[id]; // anagram_race, letter_reveal
}
function allResolved(state: GameState, d: MiniGameData): boolean {
  const c = connected(state);
  return c.length > 0 && c.every(p => isResolved(d, p.id));
}

// ── init ────────────────────────────────────────────────────────────────────
// Games open in status 'intro' — a rules screen shown on all screens for
// INTRO_MS before play begins (see beginMiniGamePlaying). The playable content
// (scrambled letters, revealed letters) is withheld until then.
export function initMiniGame(state: GameState): void {
  mgSecret = {};
  const key = state.activeMiniGame?.key;
  const now = Date.now();
  const introEndsAt = now + INTRO_MS;

  if (key === 'anagram_race') {
    const word = randomWord(ANAGRAM_WORDS);
    mgSecret.anagramAnswer = normalize(word);
    mgSecret.anagramScrambled = scramble(word).toUpperCase();
    const data: AnagramData = {
      key: 'anagram_race', status: 'intro', endsAt: introEndsAt,
      roundScores: {}, results: null, answerReveal: null,
      scrambled: '', wordLen: word.length,
      value: state.activeClue?.value ?? 200,
      solvedOrder: [], solved: {},
    };
    state.miniGameData = data as unknown as Record<string, unknown>;
  } else if (key === 'letter_reveal') {
    const word = randomWord(FIVE_LETTER_WORDS);
    mgSecret.letterAnswer = normalize(word);
    mgSecret.revealOrder = shuffledIndices(word.length);
    const data: LetterData = {
      key: 'letter_reveal', status: 'intro', endsAt: introEndsAt,
      roundScores: {}, results: null, answerReveal: null,
      value: state.activeClue?.value ?? 200,
      wordLen: word.length, letters: Array(word.length).fill(null),
      revealCount: 0, solvedOrder: [], solved: {}, solvedAtReveal: {},
    };
    state.miniGameData = data as unknown as Record<string, unknown>;
  } else if (key === 'rapid_fire') {
    const qs = (state.miniGameTrivia ?? []).filter(q => q.type === 'multiple' && q.choices.length >= 2);
    mgSecret.rapidCorrect = qs.map(q => normalize(q.correct));
    const data: RapidData = {
      key: 'rapid_fire', status: 'intro', endsAt: introEndsAt,
      roundScores: {}, results: null, answerReveal: null,
      category: qs[0]?.category ?? 'Trivia',
      value: state.activeClue?.value ?? 200,
      questions: qs.map(q => ({ question: q.question, choices: q.choices, category: q.category })),
      total: qs.length, progress: {}, correct: {}, wrong: {}, done: {},
    };
    state.miniGameData = data as unknown as Record<string, unknown>;
  } else if (key === 'memory_match') {
    const data: MemoryData = {
      key: 'memory_match', status: 'intro', endsAt: introEndsAt,
      roundScores: {}, results: null, answerReveal: null,
      value: state.activeClue?.value ?? 200,
      levels: MEMORY_LEVELS,
      level: {}, pattern: {}, patternSeq: {}, found: {},
      wrongTotal: {}, out: {}, doneAt: {}, solvedOrder: [], solved: {},
    };
    state.miniGameData = data as unknown as Record<string, unknown>;
  }
}

// Start (or lazily join) a player's Memory Matrix run at rung 0, 0 strikes.
function ensureMemoryPlayer(d: MemoryData, id: string): void {
  if (d.level[id] !== undefined) return;
  d.level[id] = 0;
  d.wrongTotal[id] = 0;
  d.found[id] = [];
  dealMemoryPattern(d, id);
}

// Deal a fresh pattern for the player's current rung and bump the sequence so
// their phone knows to flash it.
function dealMemoryPattern(d: MemoryData, id: string): void {
  const spec = d.levels[Math.min(d.level[id] ?? 0, d.levels.length - 1)];
  d.pattern[id] = shuffledIndices(spec.grid).slice(0, spec.lit).sort((a, b) => a - b);
  d.patternSeq[id] = (d.patternSeq[id] ?? 0) + 1;
  d.found[id] = [];
}

// Rules screen over → open play: reveal the content and start the round clock.
export function beginMiniGamePlaying(state: GameState): void {
  const d = state.miniGameData as unknown as MiniGameData | null;
  if (!d || d.status !== 'intro') return;
  d.status = 'playing';
  const now = Date.now();
  if (d.key === 'anagram_race') {
    d.scrambled = mgSecret.anagramScrambled ?? '';
    d.endsAt = now + HYPER_ROUND_MS;
  } else if (d.key === 'rapid_fire') {
    d.endsAt = now + RAPID_ROUND_MS; // hard 30s sprint
  } else if (d.key === 'letter_reveal') {
    d.endsAt = now + HYPER_ROUND_MS; // 60s round; letters also reveal on their own cadence
  } else if (d.key === 'memory_match') {
    // Everyone's ladder starts now — patterns deal per player, phones flash them.
    for (const p of state.players.filter(pl => pl.connected)) ensureMemoryPlayer(d, p.id);
    d.endsAt = now + HYPER_ROUND_MS;
  }
}

// ── actions ─────────────────────────────────────────────────────────────────
const NONE: ActionResult = { changed: false, complete: false, feedback: {} };

export function handleMiniGameAction(
  state: GameState,
  playerId: string,
  action: { type: string; payload?: unknown },
): ActionResult {
  const d = state.miniGameData as unknown as MiniGameData | null;
  if (!d || d.status !== 'playing') return NONE;
  if (!state.players.find(p => p.id === playerId)) return NONE;

  if (d.key === 'anagram_race') return anagramSubmit(state, d, playerId, action.payload);
  if (d.key === 'letter_reveal') return letterSubmit(state, d, playerId, action.payload);
  if (d.key === 'rapid_fire') return rapidSubmit(state, d, playerId, action.payload);
  if (d.key === 'memory_match') return memoryPick(state, d, playerId, action.payload);
  return NONE;
}

// Memory Matrix: one tap at a time. Hits light the cell; find every lit cell
// to climb a rung (grid grows). 3 wrong guesses at ANY time across the run →
// out. Clearing the final rung finishes the ladder. Points settle by placement
// at round end.
function memoryPick(state: GameState, d: MemoryData, playerId: string, payload: unknown): ActionResult {
  ensureMemoryPlayer(d, playerId); // late joiners start at rung 0
  if (d.solved[playerId] || d.out[playerId]) return { changed: false, complete: false, feedback: { already: true } };

  const spec = d.levels[Math.min(d.level[playerId], d.levels.length - 1)];
  const cell = Number((payload as { cell?: unknown } | null)?.cell);
  if (!Number.isInteger(cell) || cell < 0 || cell >= spec.grid) return NONE;

  const pattern = d.pattern[playerId] ?? [];
  const found = d.found[playerId] ?? (d.found[playerId] = []);
  if (found.includes(cell)) return { changed: false, complete: false, feedback: { already: true } };

  if (pattern.includes(cell)) {
    found.push(cell);
    if (found.length === pattern.length) {
      // rung cleared → climb (or finish the ladder)
      d.level[playerId] += 1;
      if (d.level[playerId] >= d.levels.length) {
        d.solved[playerId] = true;
        d.solvedOrder.push(playerId);
        d.doneAt[playerId] = Date.now();
        return { changed: true, complete: allResolved(state, d), feedback: { correct: true, finished: true } };
      }
      dealMemoryPattern(d, playerId);
      return { changed: true, complete: false, feedback: { correct: true, levelUp: true } };
    }
    return { changed: true, complete: false, feedback: { correct: true } };
  }

  // miss — a strike, wherever you are in the run. 3 strikes total → out.
  d.wrongTotal[playerId] = (d.wrongTotal[playerId] ?? 0) + 1;
  if (d.wrongTotal[playerId] >= MEMORY_STRIKES) {
    d.out[playerId] = true;
    d.doneAt[playerId] = Date.now();
    return { changed: true, complete: allResolved(state, d), feedback: { correct: false, out: true } };
  }
  return { changed: true, complete: false, feedback: { correct: false } };
}

// Round over → rank the runs: most rungs cleared, then earliest finish, then
// fewest total misses. Placement pays the usual 2×/1×/0/−1× of the cell value;
// clearing nothing (or giving up) scores 0.
function scoreMemoryByPlacement(state: GameState, d: MemoryData): void {
  const runners = state.players
    .map(p => ({
      id: p.id,
      cleared: d.solved[p.id] ? d.levels.length : (d.level[p.id] ?? 0),
      doneAt: d.doneAt[p.id] ?? Number.MAX_SAFE_INTEGER,
      wrong: d.wrongTotal[p.id] ?? 0,
    }))
    .filter(r => r.cleared > 0)
    .sort((a, b) => b.cleared - a.cleared || a.doneAt - b.doneAt || a.wrong - b.wrong);
  runners.forEach((r, i) => {
    const mult = PLACEMENT_MULT[i] ?? PLACEMENT_MULT[PLACEMENT_MULT.length - 1]; // 4th+ = −1×
    d.roundScores[r.id] = Math.round(d.value * mult);
  });
}

function anagramSubmit(state: GameState, d: AnagramData, playerId: string, payload: unknown): ActionResult {
  if (d.solved[playerId]) return { changed: false, complete: false, feedback: { already: true } };
  const guess = normalize(String(payload ?? ''));
  if (!guess) return NONE;
  if (guess === mgSecret.anagramAnswer) {
    d.solved[playerId] = true;
    d.solvedOrder.push(playerId);
    const place = d.solvedOrder.length - 1;              // 0-indexed placement
    const mult = PLACEMENT_MULT[place] ?? PLACEMENT_MULT[PLACEMENT_MULT.length - 1]; // 4th+ = −1×
    const pts = Math.round(d.value * mult);
    d.roundScores[playerId] = pts;
    return { changed: true, complete: allResolved(state, d), feedback: { correct: true, points: pts } };
  }
  return { changed: false, complete: false, feedback: { correct: false } };
}

function letterSubmit(state: GameState, d: LetterData, playerId: string, payload: unknown): ActionResult {
  if (d.solved[playerId]) return { changed: false, complete: false, feedback: { already: true } };
  const guess = normalize(String(payload ?? ''));
  if (guess.length !== d.wordLen) return { changed: false, complete: false, feedback: { correct: false, invalid: true } };
  if (guess === mgSecret.letterAnswer) {
    d.solved[playerId] = true;
    d.solvedAtReveal[playerId] = d.revealCount;
    d.solvedOrder.push(playerId);
    const place = d.solvedOrder.length - 1;
    const mult = PLACEMENT_MULT[place] ?? PLACEMENT_MULT[PLACEMENT_MULT.length - 1]; // 4th+ = −1×
    const pts = Math.round(d.value * mult);
    d.roundScores[playerId] = pts;
    return { changed: true, complete: allResolved(state, d), feedback: { correct: true, points: pts } };
  }
  return { changed: false, complete: false, feedback: { correct: false } };
}

function rapidSubmit(state: GameState, d: RapidData, playerId: string, payload: unknown): ActionResult {
  const p = (payload ?? {}) as { index?: number; choice?: string };
  if (d.done[playerId]) return NONE;
  const cur = d.progress[playerId] ?? 0;
  if (Number(p.index) !== cur) return { changed: false, complete: false, feedback: { stale: true } }; // ignore stale taps
  const isCorrect = normalize(String(p.choice ?? '')) === (mgSecret.rapidCorrect?.[cur] ?? '\u0000');
  // Placement scoring (like Anagram): only the tallies matter during play;
  // points are awarded by finish position at the end (scoreRapidByPlacement).
  if (isCorrect) d.correct[playerId] = (d.correct[playerId] ?? 0) + 1;
  else d.wrong[playerId] = (d.wrong[playerId] ?? 0) + 1;
  d.progress[playerId] = cur + 1;
  if (d.progress[playerId] >= d.total) d.done[playerId] = true;
  return { changed: true, complete: allResolved(state, d), feedback: { correct: isCorrect } };
}

// At round end, rank Rapid Fire players by correct answers (tie-break: fewer
// wrong) and award the placement multiple of the cell value, mirroring Anagram.
// Players with zero correct answers don't place → 0.
function scoreRapidByPlacement(state: GameState, d: RapidData): void {
  const scorers = state.players
    .map(p => ({ id: p.id, correct: d.correct[p.id] ?? 0, wrong: d.wrong[p.id] ?? 0 }))
    .filter(r => r.correct > 0)
    .sort((a, b) => b.correct - a.correct || a.wrong - b.wrong);
  scorers.forEach((r, i) => {
    const mult = PLACEMENT_MULT[i] ?? PLACEMENT_MULT[PLACEMENT_MULT.length - 1]; // 4th+ = −1×
    d.roundScores[r.id] = Math.round(d.value * mult);
  });
}

// ── letter reveal tick ──────────────────────────────────────────────────────
export function revealLetter(state: GameState): { fullyRevealed: boolean } {
  const d = state.miniGameData as unknown as LetterData | null;
  if (!d || d.key !== 'letter_reveal' || d.status !== 'playing') return { fullyRevealed: true };
  const order = mgSecret.revealOrder ?? [];
  const ans = mgSecret.letterAnswer ?? '';
  if (d.revealCount < order.length) {
    const pos = order[d.revealCount];
    d.letters[pos] = ans[pos] ?? '?';
    d.revealCount += 1;
  }
  return { fullyRevealed: d.revealCount >= d.wordLen };
}

// ── finish → score + results ────────────────────────────────────────────────
export function finishMiniGame(state: GameState): void {
  const d = state.miniGameData as unknown as MiniGameData | null;
  if (!d || d.status === 'results') return;

  // Rapid Fire and Memory Matrix settle by finish position at the end
  // (Anagram/Letter set their roundScores as players solve).
  if (d.key === 'rapid_fire') scoreRapidByPlacement(state, d);
  if (d.key === 'memory_match') scoreMemoryByPlacement(state, d);

  const rows: MiniGameResultRow[] = [];
  for (const p of state.players) {
    const pts = d.roundScores[p.id] ?? 0;
    if (pts !== 0) { // apply gains AND penalties (Anagram can go negative)
      p.score += pts;
      state.scores[p.id]?.push(p.score);
    }
    rows.push({ playerId: p.id, name: p.name, points: pts, detail: resultDetail(d, p.id) });
  }
  rows.sort((a, b) => b.points - a.points);

  d.results = rows;
  d.answerReveal = answerReveal(d);
  d.status = 'results';
  d.endsAt = null;
}

// The mini-game winner = the player who finished 1st (top positive score).
// Called after finishMiniGame() (which sorts `results` by points desc) so the
// server can hand board control to the winner for the next pick. Returns null
// when nobody placed (all zero), so the caller keeps the current controller.
export function miniGameWinnerId(state: GameState): string | null {
  const d = state.miniGameData as unknown as MiniGameData | null;
  const top = d?.results?.[0];
  return top && top.points > 0 ? top.playerId : null;
}

function resultDetail(d: MiniGameData, playerId: string): string {
  if (d.key === 'anagram_race') {
    const i = d.solvedOrder.indexOf(playerId);
    return i >= 0 ? `#${i + 1} to solve` : 'did not solve';
  }
  if (d.key === 'memory_match') {
    const cleared = d.solved[playerId] ? d.levels.length : (d.level[playerId] ?? 0);
    if (d.solved[playerId]) return `cleared all ${d.levels.length} levels`;
    return `cleared ${cleared}/${d.levels.length}${d.out[playerId] ? ' · struck out' : ''}`;
  }
  if (d.key === 'letter_reveal') {
    const i = d.solvedOrder.indexOf(playerId);
    if (i >= 0) {
      const hidden = d.wordLen - d.solvedAtReveal[playerId];
      return `#${i + 1} · ${hidden} hidden`;
    }
    return 'did not solve';
  }
  // rapid_fire
  return `${d.correct[playerId] ?? 0} ✓  ${d.wrong[playerId] ?? 0} ✗`;
}

function answerReveal(d: MiniGameData): string | null {
  if (d.key === 'anagram_race') return mgSecret.anagramAnswer ?? null;
  if (d.key === 'letter_reveal') return mgSecret.letterAnswer ?? null;
  return d.key === 'rapid_fire' ? d.category : null;
}
