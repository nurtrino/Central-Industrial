import { GameForPlay, CategoryForPlay, ClueForPlay } from './games';
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
  | 'hyper_active';     // HYPER MODE mini-game running (placeholder for now)

// A mini-game that can fire when a "hyper" cell is chosen. These are
// placeholders for now — the registry names/blurbs come from the concept
// list; each becomes a real playable round in a later phase.
export interface MiniGame {
  key: string;
  title: string;
  family: string;
  blurb: string;
}

export const MINI_GAMES: MiniGame[] = [
  { key: 'fake_it',      title: 'Fake It',         family: 'Bluff',       blurb: 'Everyone writes a convincing fake answer. Score for spotting the truth — and for fooling the table.' },
  { key: 'the_spectrum', title: 'The Spectrum',    family: 'Estimation',  blurb: 'One player sees the hidden target and gives a single-word clue. The rest dial it in.' },
  { key: 'connections',  title: 'Connections',     family: 'Word Puzzle', blurb: 'Sixteen words, four secret groups. Sort them before your rivals do.' },
  { key: 'zoom_out',     title: 'Zoom Out',        family: 'Perception',  blurb: 'An image slowly pulls back. Buzz the instant you know what it is — sooner scores more.' },
  { key: 'most_likely',  title: 'Most Likely To…', family: 'Social',      blurb: 'Vote on who around the table best fits the prompt. Match the majority to score.' },
  { key: 'higher_lower', title: 'Higher or Lower', family: 'Estimation',  blurb: 'Guess the number — closest without going over takes the points.' },
  { key: 'rapid_fire',   title: 'Rapid Fire',      family: 'Speed',       blurb: 'Thirty seconds, one topic. As many as you can — wrong answers cost you.' },
];

export function pickMiniGame(): MiniGame {
  return MINI_GAMES[Math.floor(Math.random() * MINI_GAMES.length)];
}

// Choose 5–10 random non-Daily-Double clues in a round's board to become
// "hyper" cells. Selecting one fires HYPER MODE (a mini-game) instead of the
// normal clue — like a Daily Double, but a mini-game. Returns clue ids.
export function assignHyperClues(board: CategoryForPlay[]): number[] {
  const ids = board.flatMap(c => c.clues).filter(cl => !cl.isDailyDouble).map(cl => cl.id);
  for (let i = ids.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [ids[i], ids[j]] = [ids[j], ids[i]];
  }
  const count = Math.min(ids.length, 5 + Math.floor(Math.random() * 6)); // 5..10 inclusive
  return ids.slice(0, count);
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
  activeMiniGame: MiniGame | null;  // the mini-game running during a hyper cell
}

const BUZZ_WINDOW_MS = 10_000;
const ANSWER_TIME_MS = 15_000;
const READING_DELAY_MS = 6_000;
const REVEAL_PAUSE_MS = 3_000;
const DD_ANSWER_MS = 30_000;
export const HYPER_INTRO_MS = 3_500;   // activation splash duration
export const HYPER_MAX_MS = 120_000;   // safety cap so a placeholder can't hang the board

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
    activeMiniGame: null,
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
  state.hyperClues = assignHyperClues(game.jeopardyRound);
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

  // HYPER MODE fires first — a hyper cell is never also a Daily Double
  // (assignHyperClues excludes DDs), so the branches don't collide.
  if (state.hyperClues.includes(clue.id)) {
    state.activeMiniGame = pickMiniGame();
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
