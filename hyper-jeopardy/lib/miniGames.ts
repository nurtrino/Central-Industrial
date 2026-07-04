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
export const INTRO_MS = 5_000;   // rules screen shown on all screens before play
export const ANAGRAM_MS = 40_000;        // max round length if nobody solves
export const ANAGRAM_LASTCALL_MS = 10_000; // once the 1st player solves, collapse to this
export const RAPID_MS = 45_000;
export const REVEAL_INTERVAL_MS = 4_500;
export const LETTER_GRACE_MS = 6_000;
export const RESULTS_MS = 6_500;

// Anagram Race scores as a MULTIPLE of the board-space value, by solve order:
// 1st = 2×, 2nd = 1×, 3rd = 0, 4th+ = −1×. Non-solvers get 0.
const ANAGRAM_MULT = [2, 1, 0, -1];
const LETTER_AWARDS = [500, 400, 300, 250, 150, 100]; // by revealCount 0..5
const RAPID_CORRECT = 100;
const RAPID_WRONG = -50;

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
  questions: RapidQ[];
  total: number;
  progress: Record<string, number>;
  correct: Record<string, number>;
  wrong: Record<string, number>;
  done: Record<string, boolean>;
}

export interface LetterData extends BaseMGData {
  key: 'letter_reveal';
  wordLen: number;
  letters: (string | null)[];  // revealed letters; null = still hidden
  revealCount: number;
  solved: Record<string, boolean>;
  solvedAtReveal: Record<string, number>;
}

export type MiniGameData = AnagramData | RapidData | LetterData;

export interface ActionResult {
  changed: boolean;   // did broadcast-worthy state change
  complete: boolean;  // did the round just finish (all players done)
  feedback: { correct?: boolean; points?: number; place?: number; invalid?: boolean; already?: boolean; stale?: boolean };
  rescheduleRoundMs?: number; // ask the server to re-arm the round timer (Anagram first-solve collapse)
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

function allSolved(state: GameState, solved: Record<string, boolean>): boolean {
  const c = connected(state);
  return c.length > 0 && c.every(p => solved[p.id]);
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
      wordLen: word.length, letters: Array(word.length).fill(null),
      revealCount: 0, solved: {}, solvedAtReveal: {},
    };
    state.miniGameData = data as unknown as Record<string, unknown>;
  } else if (key === 'rapid_fire') {
    const qs = (state.miniGameTrivia ?? []).filter(q => q.type === 'multiple' && q.choices.length >= 2);
    mgSecret.rapidCorrect = qs.map(q => normalize(q.correct));
    const data: RapidData = {
      key: 'rapid_fire', status: 'intro', endsAt: introEndsAt,
      roundScores: {}, results: null, answerReveal: null,
      category: qs[0]?.category ?? 'Trivia',
      questions: qs.map(q => ({ question: q.question, choices: q.choices, category: q.category })),
      total: qs.length, progress: {}, correct: {}, wrong: {}, done: {},
    };
    state.miniGameData = data as unknown as Record<string, unknown>;
  }
}

// Rules screen over → open play: reveal the content and start the round clock.
export function beginMiniGamePlaying(state: GameState): void {
  const d = state.miniGameData as unknown as MiniGameData | null;
  if (!d || d.status !== 'intro') return;
  d.status = 'playing';
  const now = Date.now();
  if (d.key === 'anagram_race') {
    d.scrambled = mgSecret.anagramScrambled ?? '';
    d.endsAt = now + ANAGRAM_MS;
  } else if (d.key === 'rapid_fire') {
    d.endsAt = now + RAPID_MS;
  } else if (d.key === 'letter_reveal') {
    d.endsAt = null; // reveal-paced, no fixed round clock
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
  return NONE;
}

function anagramSubmit(state: GameState, d: AnagramData, playerId: string, payload: unknown): ActionResult {
  if (d.solved[playerId]) return { changed: false, complete: false, feedback: { already: true } };
  const guess = normalize(String(payload ?? ''));
  if (!guess) return NONE;
  if (guess === mgSecret.anagramAnswer) {
    d.solved[playerId] = true;
    d.solvedOrder.push(playerId);
    const place = d.solvedOrder.length - 1;              // 0-indexed placement
    const mult = ANAGRAM_MULT[place] ?? ANAGRAM_MULT[ANAGRAM_MULT.length - 1]; // 4th+ = −1×
    const pts = Math.round(d.value * mult);
    d.roundScores[playerId] = pts;
    const complete = allSolved(state, d.solved);

    // Snappiness: the moment the FIRST player solves, collapse the round to a
    // short last-call window so it doesn't drag while others are still typing.
    let rescheduleRoundMs: number | undefined;
    if (!complete && d.solvedOrder.length === 1) {
      const collapsed = Date.now() + ANAGRAM_LASTCALL_MS;
      if (d.endsAt == null || collapsed < d.endsAt) {
        d.endsAt = collapsed;
        rescheduleRoundMs = ANAGRAM_LASTCALL_MS;
      }
    }
    return { changed: true, complete, feedback: { correct: true, points: pts, place: place + 1 }, rescheduleRoundMs };
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
    const pts = LETTER_AWARDS[Math.min(d.revealCount, LETTER_AWARDS.length - 1)];
    d.roundScores[playerId] = pts;
    return { changed: true, complete: allSolved(state, d.solved), feedback: { correct: true, points: pts } };
  }
  return { changed: false, complete: false, feedback: { correct: false } };
}

function rapidSubmit(state: GameState, d: RapidData, playerId: string, payload: unknown): ActionResult {
  const p = (payload ?? {}) as { index?: number; choice?: string };
  if (d.done[playerId]) return NONE;
  const cur = d.progress[playerId] ?? 0;
  if (Number(p.index) !== cur) return { changed: false, complete: false, feedback: { stale: true } }; // ignore stale taps
  const isCorrect = normalize(String(p.choice ?? '')) === (mgSecret.rapidCorrect?.[cur] ?? ' ');
  d.roundScores[playerId] = Math.max(0, (d.roundScores[playerId] ?? 0) + (isCorrect ? RAPID_CORRECT : RAPID_WRONG));
  if (isCorrect) d.correct[playerId] = (d.correct[playerId] ?? 0) + 1;
  else d.wrong[playerId] = (d.wrong[playerId] ?? 0) + 1;
  d.progress[playerId] = cur + 1;
  if (d.progress[playerId] >= d.total) d.done[playerId] = true;
  const c = connected(state);
  const complete = c.length > 0 && c.every(pl => d.done[pl.id]);
  return { changed: true, complete, feedback: { correct: isCorrect, points: d.roundScores[playerId] } };
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

function resultDetail(d: MiniGameData, playerId: string): string {
  if (d.key === 'anagram_race') {
    const i = d.solvedOrder.indexOf(playerId);
    return i >= 0 ? `#${i + 1} to solve` : 'did not solve';
  }
  if (d.key === 'letter_reveal') {
    if (!d.solved[playerId]) return 'did not solve';
    const hidden = d.wordLen - d.solvedAtReveal[playerId];
    return `solved with ${hidden} hidden`;
  }
  // rapid_fire
  return `${d.correct[playerId] ?? 0} ✓  ${d.wrong[playerId] ?? 0} ✗`;
}

function answerReveal(d: MiniGameData): string | null {
  if (d.key === 'anagram_race') return mgSecret.anagramAnswer ?? null;
  if (d.key === 'letter_reveal') return mgSecret.letterAnswer ?? null;
  return d.key === 'rapid_fire' ? d.category : null;
}
