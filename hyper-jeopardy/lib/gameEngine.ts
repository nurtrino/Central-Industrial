import { GameForPlay, CategoryForPlay, ClueForPlay } from './games';
import type { TriviaQuestion } from './opentdb';
import { v4 as uuidv4 } from 'uuid';

export type GamePhase =
  | 'lobby'
  | 'jeopardy'
  | 'double_jeopardy'
  | 'final_jeopardy'
  | 'game_over';

export type CluePhase =
  | 'idle'              // board shown, waiting for selection
  | 'reading'           // clue displayed, 5s read time before buzz opens
  | 'buzzing'           // buzz window open
  | 'answering'         // player buzzed, 5s to answer
  | 'judging'           // host sees answer, auto-judges
  | 'reveal'            // show correct answer, 3s pause
  | 'daily_double_wager'// DD: player wagering
  | 'daily_double_answer'// DD: player answering
  | 'hyper_intro'       // HYPER MODE activation splash before the mini-game
  | 'hyper_active'      // HYPER MODE mini-game running
  | 'invaders';         // SPACE INVADERS AMBUSH takeover (mid-Double-Jeopardy)

// A mini-game that can fire when a "hyper" cell is chosen.
//   mode   — 'single' (one player's spotlight) or 'multi' (whole table competes)
//   trivia — where its questions come from, when it uses trivia:
//              false      → no trivia (social / estimation / puzzle games)
//              'random'   → OpenTDB medium, random category (excludes Musicals
//                           & Theatres) — the default rule
//              <number>   → a forced OpenTDB category id (game rules require it)
// These entries are still placeholders; the real list (and per-game modules)
// arrives next, built as wireframes.
export interface MiniGame {
  key: string;
  title: string;
  family: string;
  blurb: string;
  mode: 'single' | 'multi';
  trivia: false | 'random' | number;
  triviaCount?: number; // how many questions to pre-fetch (default 1)
}

// Phase 1 mini-games (all multiplayer). Their logic lives in lib/miniGames.ts.
export const MINI_GAMES: MiniGame[] = [
  { key: 'anagram_race',  title: 'Anagram Race',  family: 'Word Race',   mode: 'multi', trivia: false,                     blurb: 'Unscramble the word before your rivals — the faster you solve, the more you bank.' },
  { key: 'rapid_fire',    title: 'Rapid Fire',    family: 'Speed',       mode: 'multi', trivia: 'random', triviaCount: 10, blurb: 'Ten questions, one category, thirty seconds. Answer as many as you can — most correct wins.' },
  { key: 'letter_reveal', title: 'Letter Reveal', family: 'Word Reveal', mode: 'multi', trivia: false,                     blurb: 'Five hidden letters reveal one by one. Guess early — the fewer shown, the bigger the score.' },
  { key: 'memory_match',  title: 'Memory Matrix', family: 'Memory',      mode: 'multi', trivia: false,                     blurb: 'A pattern flashes on the grid — memorize it, then rebuild it from memory. First perfect match wins.' },
];

export function pickMiniGame(): MiniGame {
  // Test/demo hook: HYPER_FORCE_GAME=<key> pins the mini-game (server-only).
  const forced = typeof process !== 'undefined' ? process.env?.HYPER_FORCE_GAME : undefined;
  if (forced) {
    const m = MINI_GAMES.find(g => g.key === forced);
    if (m) return m;
  }
  return MINI_GAMES[Math.floor(Math.random() * MINI_GAMES.length)];
}

export const HYPER_PER_ROUND = 8;   // mini-game cells per round
export const DD_PER_ROUND = 2;      // Daily Doubles per round (house rule)

// Randomly place this round's special cells: DD_PER_ROUND Daily Doubles + a
// disjoint set of HYPER_PER_ROUND hyper (mini-game) cells. Each hyper cell is
// PRE-ASSIGNED a specific mini-game (hyperGames: clueId → game key) so the
// board can be colored per game for testing, and every game appears at least
// once. We control both counts, so any seed Daily Doubles are cleared first.
export function assignSpecialCells(board: CategoryForPlay[]): {
  hyperClues: number[];
  ddClues: number[];
  hyperGames: Record<number, string>;
} {
  const clues = board.flatMap(c => c.clues);
  for (const cl of clues) cl.isDailyDouble = false;

  const ids = clues.map(cl => cl.id);
  for (let i = ids.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [ids[i], ids[j]] = [ids[j], ids[i]];
  }
  const ddClues = ids.slice(0, Math.min(DD_PER_ROUND, ids.length));
  const hyperClues = ids.slice(ddClues.length, ddClues.length + HYPER_PER_ROUND);

  const ddSet = new Set(ddClues);
  for (const cl of clues) if (ddSet.has(cl.id)) cl.isDailyDouble = true;

  const hyperGames = assignHyperGames(hyperClues);
  return { hyperClues, ddClues, hyperGames };
}

// Assign a mini-game to each hyper cell. HYPER_FORCE_GAME pins them all to one
// game; otherwise every game is guaranteed at least once, then the rest random.
function assignHyperGames(hyperClues: number[]): Record<number, string> {
  const keys = MINI_GAMES.map(g => g.key);
  const forced = typeof process !== 'undefined' ? process.env?.HYPER_FORCE_GAME : undefined;
  const games: Record<number, string> = {};

  if (forced && keys.includes(forced)) {
    for (const id of hyperClues) games[id] = forced;
    return games;
  }

  const list: string[] = [...keys]; // one of each guaranteed
  while (list.length < hyperClues.length) list.push(keys[Math.floor(Math.random() * keys.length)]);
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  hyperClues.forEach((id, i) => { games[id] = list[i]; });
  return games;
}

export interface Player {
  id: string;
  name: string;
  score: number;
  connected: boolean;
  isHost: boolean;
  avatar?: string;   // data URL, small JPEG — see lib/profile.ts
  accountId?: string; // null/undefined = guest; populated when joined via an Account
}

export interface FinalJeopardyEntry {
  playerId: string;
  wager: number | null;
  answer: string | null;
  correct: boolean | null;
}

// Broadcast-facing summary of the SPACE INVADERS AMBUSH. The high-rate battle
// telemetry travels on its own socket event ('invaders'); this summary rides
// the normal state broadcast so late joiners/refreshes know who's flying what.
export interface InvadersSummary {
  status: 'intro' | 'playing' | 'won' | 'lost';
  roster: { id: string; name: string; color: string }[];
}

export interface GameState {
  gameId: string;
  phase: GamePhase;
  cluePhase: CluePhase;
  players: Player[];
  currentBoard: CategoryForPlay[] | null;
  activeClue: ClueForPlay | null;
  activeCategoryName: string | null;
  activeCategoryIdx: number | null;
  activeClueIdx: number | null;
  buzzedPlayerId: string | null;
  buzzOrder: string[];
  wrongAnswerers: string[];
  skippedBy: string[];
  boardController: string | null; // player who controls the board
  showNumber: number;
  airDate: string;
  finalJeopardy: { category: string; question: string; answer: string } | null;
  finalEntries: Record<string, FinalJeopardyEntry>;
  finalRevealed: boolean;
  timerEndsAt: number | null;
  dailyDoubleWager: number | null;
  usedClues: Set<number>;
  scores: Record<string, number[]>; // history for display
  hyperClues: number[];             // clue ids that trigger HYPER MODE this round
  hyperGames: Record<number, string>; // clue id → pre-assigned mini-game key
  hyperSeed: number;                // rolled on each hyper activation — every client
                                    // plays the SAME start clip (seed % clip count)
  // SPACE INVADERS AMBUSH — fires once, at a random point in Double Jeopardy.
  invaders: InvadersSummary | null; // roster + status while the battle runs
  invadersDone: boolean;            // one ambush per game
  invadersTriggerAt: number;        // usedClues.size threshold that springs it
  invadersArmed: boolean;           // playtest: host armed the ambush to spring
                                    // after the NEXT resolved clue (any round)
  activeMiniGame: MiniGame | null;  // the mini-game running during a hyper cell
  miniGameTrivia: TriviaQuestion[] | null; // pre-fetched questions for the active mini-game
  miniGameData: Record<string, unknown> | null; // per-game runtime state (wireframe prototyping)
}

const BUZZ_WINDOW_MS = 10_000;
const ANSWER_TIME_MS = 15_000;
const READING_DELAY_MS = 6_000;
const REVEAL_PAUSE_MS = 3_000;
const DD_ANSWER_MS = 30_000;
export const HYPER_INTRO_MS = 3_500;   // activation splash duration
export const HYPER_MAX_MS = 75_000;    // absolute safety cap (5s rules + 60s round + buffer)

export function createGame(showNumber: number, airDate: string): GameState {
  return {
    gameId: uuidv4(),
    phase: 'lobby',
    cluePhase: 'idle',
    players: [],
    currentBoard: null,
    activeClue: null,
    activeCategoryName: null,
    activeCategoryIdx: null,
    activeClueIdx: null,
    buzzedPlayerId: null,
    buzzOrder: [],
    wrongAnswerers: [],
    skippedBy: [],
    boardController: null,
    showNumber,
    airDate,
    finalJeopardy: null,
    finalEntries: {},
    finalRevealed: false,
    timerEndsAt: null,
    dailyDoubleWager: null,
    usedClues: new Set(),
    scores: {},
    hyperClues: [],
    hyperGames: {},
    hyperSeed: 0,
    invaders: null,
    invadersDone: false,
    invadersTriggerAt: 0,
    invadersArmed: false,
    activeMiniGame: null,
    miniGameTrivia: null,
    miniGameData: null,
  };
}

export function addPlayer(
  state: GameState,
  name: string,
  socketId: string,
  isHost: boolean = false,
  avatar?: string,
  accountId?: string,
): Player {
  const player: Player = {
    id: socketId, name, score: 0, connected: true, isHost,
    ...(avatar ? { avatar } : {}),
    ...(accountId ? { accountId } : {}),
  };
  state.players.push(player);
  state.scores[socketId] = [0];
  return player;
}

// In-lobby rename: a player may edit their displayed name while the game
// hasn't started yet. Returns true on success.
export function renamePlayer(state: GameState, socketId: string, newName: string): boolean {
  if (state.phase !== 'lobby') return false;
  const trimmed = newName.trim();
  if (!trimmed) return false;
  const p = state.players.find(pl => pl.id === socketId);
  if (!p) return false;
  // Block exact-name collisions with other players in the same lobby.
  if (state.players.some(o => o.id !== socketId && o.name === trimmed)) return false;
  p.name = trimmed;
  return true;
}

export function startGame(state: GameState, game: GameForPlay): void {
  state.phase = 'jeopardy';
  state.cluePhase = 'idle';
  state.currentBoard = game.jeopardyRound;
  state.finalJeopardy = game.finalJeopardy;
  state.usedClues = new Set();
  const special = assignSpecialCells(game.jeopardyRound);
  state.hyperClues = special.hyperClues;
  state.hyperGames = special.hyperGames;
  state.activeMiniGame = null;
  // First player to join controls board first (they're host)
  const host = state.players.find(p => p.isHost);
  state.boardController = host?.id ?? state.players[0]?.id ?? null;
}

export function selectClue(
  state: GameState,
  catIdx: number,
  clueIdx: number,
  requestingPlayerId: string
): boolean {
  if (state.cluePhase !== 'idle') return false;
  if (state.boardController !== requestingPlayerId) return false;
  if (!state.currentBoard) return false;

  const cat = state.currentBoard[catIdx];
  if (!cat) return false;
  const clue = cat.clues[clueIdx];
  if (!clue || clue.used || state.usedClues.has(clue.id)) return false;

  state.activeClue = clue;
  state.activeCategoryName = cat.name;
  state.activeCategoryIdx = catIdx;
  state.activeClueIdx = clueIdx;
  state.buzzOrder = [];
  state.wrongAnswerers = [];
  state.skippedBy = [];
  state.buzzedPlayerId = null;
  state.activeMiniGame = null;
  state.miniGameTrivia = null;
  state.miniGameData = null;

  // HYPER MODE fires first — a hyper cell is never also a Daily Double, so the
  // branches don't collide. Use the game pre-assigned to this cell.
  if (state.hyperClues.includes(clue.id)) {
    const key = state.hyperGames[clue.id];
    state.activeMiniGame = MINI_GAMES.find(g => g.key === key) ?? pickMiniGame();
    state.hyperSeed = Math.floor(Math.random() * 1_000_000); // same start clip on every screen
    state.cluePhase = 'hyper_intro';
    state.timerEndsAt = Date.now() + HYPER_INTRO_MS;
  } else if (clue.isDailyDouble) {
    state.cluePhase = 'daily_double_wager';
    state.timerEndsAt = null;
  } else {
    state.cluePhase = 'reading';
    state.timerEndsAt = Date.now() + READING_DELAY_MS;
  }

  return true;
}

// HYPER MODE: advance from the activation splash into the running mini-game.
export function beginHyperActive(state: GameState): void {
  if (state.cluePhase !== 'hyper_intro') return;
  state.cluePhase = 'hyper_active';
  state.timerEndsAt = Date.now() + HYPER_MAX_MS;
}

// HYPER MODE: close out the mini-game and return control to the board. The
// clue is consumed; the selecting player keeps the board. (Placeholder
// mini-games don't score yet — scoring arrives with the real games.)
export function endHyper(state: GameState): void {
  if (state.cluePhase !== 'hyper_intro' && state.cluePhase !== 'hyper_active') return;
  markClueUsed(state);
  state.activeClue = null;
  state.activeCategoryName = null;
  state.activeCategoryIdx = null;
  state.activeClueIdx = null;
  state.activeMiniGame = null;
  state.miniGameTrivia = null;
  state.miniGameData = null;
  state.timerEndsAt = null;
  state.cluePhase = 'idle';
}

export function openBuzzing(state: GameState): void {
  if (state.cluePhase !== 'reading') return;
  state.cluePhase = 'buzzing';
  state.timerEndsAt = Date.now() + BUZZ_WINDOW_MS;
}

export function submitBuzz(state: GameState, playerId: string): boolean {
  if (state.cluePhase !== 'buzzing') return false;
  if (state.buzzedPlayerId !== null) return false;
  if (state.wrongAnswerers.includes(playerId)) return false;
  if (state.buzzOrder.includes(playerId)) return false;

  state.buzzOrder.push(playerId);
  state.buzzedPlayerId = playerId;
  state.cluePhase = 'answering';
  state.timerEndsAt = Date.now() + ANSWER_TIME_MS;
  return true;
}

export function submitAnswer(state: GameState, playerId: string, answer: string): 'correct' | 'wrong' | 'ignored' {
  if (state.cluePhase === 'daily_double_answer') {
    if (state.buzzedPlayerId !== playerId) return 'ignored';
    const isCorrect = judgeAnswer(answer, state.activeClue!.answer);
    const wager = state.dailyDoubleWager ?? 0;
    const player = state.players.find(p => p.id === playerId)!;
    player.score += isCorrect ? wager : -wager;
    state.scores[playerId]?.push(player.score);
    markClueUsed(state);
    state.cluePhase = 'reveal';
    state.timerEndsAt = Date.now() + REVEAL_PAUSE_MS;
    return isCorrect ? 'correct' : 'wrong';
  }

  if (state.cluePhase !== 'answering') return 'ignored';
  if (state.buzzedPlayerId !== playerId) return 'ignored';

  const isCorrect = judgeAnswer(answer, state.activeClue!.answer);
  const value = state.activeClue!.value;
  const player = state.players.find(p => p.id === playerId)!;

  if (isCorrect) {
    player.score += value;
    state.scores[playerId]?.push(player.score);
    state.boardController = playerId;
    markClueUsed(state);
    state.cluePhase = 'reveal';
    state.timerEndsAt = Date.now() + REVEAL_PAUSE_MS;
    return 'correct';
  } else {
    player.score -= value;
    state.scores[playerId]?.push(player.score);
    state.wrongAnswerers.push(playerId);
    state.buzzedPlayerId = null;

    const remaining = state.players.filter(
      p => p.connected && !state.wrongAnswerers.includes(p.id)
    );
    if (remaining.length === 0) {
      markClueUsed(state);
      state.cluePhase = 'reveal';
      state.timerEndsAt = Date.now() + REVEAL_PAUSE_MS;
    } else {
      state.cluePhase = 'buzzing';
      state.timerEndsAt = Date.now() + BUZZ_WINDOW_MS;
    }
    return 'wrong';
  }
}

export function buzzTimeout(state: GameState): void {
  if (state.cluePhase !== 'buzzing') return;
  markClueUsed(state);
  state.cluePhase = 'reveal';
  state.timerEndsAt = Date.now() + REVEAL_PAUSE_MS;
}

// Returns true if everyone who can still act has skipped — caller should
// then advance to reveal.
export function submitSkip(state: GameState, playerId: string): { added: boolean; allSkipped: boolean } {
  if (state.cluePhase !== 'reading' && state.cluePhase !== 'buzzing') {
    return { added: false, allSkipped: false };
  }
  if (!state.players.find(p => p.id === playerId)) {
    return { added: false, allSkipped: false };
  }
  if (!state.skippedBy.includes(playerId)) state.skippedBy.push(playerId);

  // Eligible voters = connected players who haven't already locked themselves
  // out with a wrong answer (locked-out players can't buzz, so their vote is
  // implicit). If everyone eligible has now skipped, we're done.
  const eligible = state.players.filter(p => p.connected && !state.wrongAnswerers.includes(p.id));
  const allSkipped = eligible.length > 0 && eligible.every(p => state.skippedBy.includes(p.id));

  if (allSkipped) {
    markClueUsed(state);
    state.cluePhase = 'reveal';
    state.timerEndsAt = Date.now() + REVEAL_PAUSE_MS;
  }
  return { added: true, allSkipped };
}

export function answerTimeout(state: GameState): void {
  if (state.cluePhase !== 'answering') return;
  const player = state.players.find(p => p.id === state.buzzedPlayerId);
  if (player) {
    player.score -= state.activeClue!.value;
    state.scores[player.id]?.push(player.score);
    state.wrongAnswerers.push(player.id);
  }
  state.buzzedPlayerId = null;

  const remaining = state.players.filter(
    p => p.connected && !state.wrongAnswerers.includes(p.id)
  );
  if (remaining.length === 0) {
    markClueUsed(state);
    state.cluePhase = 'reveal';
    state.timerEndsAt = Date.now() + REVEAL_PAUSE_MS;
  } else {
    state.cluePhase = 'buzzing';
    state.timerEndsAt = Date.now() + BUZZ_WINDOW_MS;
  }
}

export function submitDailyDoubleWager(state: GameState, playerId: string, wager: number): boolean {
  if (state.cluePhase !== 'daily_double_wager') return false;
  if (state.boardController !== playerId) return false;

  const player = state.players.find(p => p.id === playerId)!;
  // Wager rules:
  //   - If the player has a positive score, they can wager up to their
  //     current total (no betting more than you have).
  //   - If they're at 0 or negative, they get a floor of the round's max
  //     clue value ($1000 in Jeopardy!, $2000 in Double Jeopardy!) so they
  //     have a chance to climb back into it.
  // Minimum wager is $5 in either case.
  const roundMax = state.phase === 'jeopardy' ? 1000 : 2000;
  const maxWager = player.score > 0 ? player.score : roundMax;
  const safeWager = Math.max(5, Math.min(wager, maxWager));

  state.dailyDoubleWager = safeWager;
  state.buzzedPlayerId = playerId;
  state.cluePhase = 'daily_double_answer';
  state.timerEndsAt = Date.now() + DD_ANSWER_MS;
  return true;
}

export function endReveal(state: GameState): void {
  if (state.cluePhase !== 'reveal') return;
  state.activeClue = null;
  state.activeCategoryName = null;
  state.activeCategoryIdx = null;
  state.activeClueIdx = null;
  state.buzzedPlayerId = null;
  state.buzzOrder = [];
  state.wrongAnswerers = [];
  state.skippedBy = [];
  state.dailyDoubleWager = null;
  state.timerEndsAt = null;
  state.cluePhase = 'idle';
  // Round transitions are handled by the server's maybeAdvanceRound, which
  // has access to the next round's board. Auto-advancing here used to fire
  // a second time on the server and skip Double Jeopardy entirely.
}

export function advanceRound(state: GameState): void {
  if (state.phase === 'jeopardy') {
    state.phase = 'double_jeopardy';
    state.cluePhase = 'idle';
    // board will be set by caller from game.doubleJeopardyRound
  } else if (state.phase === 'double_jeopardy') {
    state.phase = 'final_jeopardy';
    state.cluePhase = 'idle';
    // Initialize final jeopardy entries
    state.players.forEach(p => {
      state.finalEntries[p.id] = { playerId: p.id, wager: null, answer: null, correct: null };
    });
  }
}

export function isBoardComplete(state: GameState): boolean {
  if (!state.currentBoard) return true;
  return state.currentBoard.every(cat =>
    cat.clues.every(clue => clue.used || state.usedClues.has(clue.id))
  );
}

export function submitFinalWager(state: GameState, playerId: string, wager: number): boolean {
  if (state.phase !== 'final_jeopardy') return false;
  const player = state.players.find(p => p.id === playerId);
  if (!player) return false;

  const maxWager = Math.max(0, player.score);
  const safeWager = Math.max(0, Math.min(wager, maxWager));
  state.finalEntries[playerId] = { ...state.finalEntries[playerId], wager: safeWager };
  return true;
}

export function submitFinalAnswer(state: GameState, playerId: string, answer: string): boolean {
  if (state.phase !== 'final_jeopardy') return false;
  if (!state.finalEntries[playerId]) return false;
  state.finalEntries[playerId].answer = answer;
  return true;
}

export function revealFinalJeopardy(state: GameState): void {
  if (!state.finalJeopardy) return;
  for (const entry of Object.values(state.finalEntries)) {
    if (entry.answer && entry.wager !== null) {
      const correct = judgeAnswer(entry.answer, state.finalJeopardy.answer);
      entry.correct = correct;
      const player = state.players.find(p => p.id === entry.playerId)!;
      player.score += correct ? entry.wager : -entry.wager;
      state.scores[player.id]?.push(player.score);
    }
  }
  state.finalRevealed = true;
  state.phase = 'game_over';
}

function markClueUsed(state: GameState): void {
  if (state.activeClue) {
    state.activeClue.used = true;
    state.usedClues.add(state.activeClue.id);
    if (state.currentBoard && state.activeCategoryIdx !== null && state.activeClueIdx !== null) {
      const clue = state.currentBoard[state.activeCategoryIdx]?.clues[state.activeClueIdx];
      if (clue) clue.used = true;
    }
  }
}

export function judgeAnswer(given: string, correct: string): boolean {
  const normalize = (s: string) =>
    s.toLowerCase()
      .replace(/^(what|who|where|when|is|are|was|were|a|an|the)\s+/gi, '')
      .replace(/[^a-z0-9]/g, '')
      .trim();

  const g = normalize(given);
  const c = normalize(correct);

  if (g === c) return true;
  if (c.includes(g) && g.length > 3) return true;
  if (g.includes(c) && c.length > 3) return true;

  // Levenshtein distance for typos
  return levenshtein(g, c) <= Math.floor(c.length * 0.25);
}

function levenshtein(a: string, b: string): number {
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, (_, i) =>
    Array.from({ length: n + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0))
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[m][n];
}

export function serializeState(state: GameState) {
  return {
    ...state,
    usedClues: Array.from(state.usedClues),
  };
}
