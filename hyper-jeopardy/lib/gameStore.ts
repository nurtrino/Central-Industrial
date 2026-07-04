// Durable snapshot of the in-progress game so a server restart / redeploy /
// cold start doesn't lose a game in progress. Writes land in DATA_DIR (a
// persistent disk on Render — see lib/dataDir.ts). Paired with the client's
// reconnect re-join: when the server comes back, it restores the board + scores
// and each player's client auto re-attaches to their seat.
//
// What survives: phase/round, board, players, scores, used clues, board
// controller, hyper assignments, Final Jeopardy entries. What does NOT: the
// live, timer-driven interaction for a single clue/mini-game in flight (buzz
// windows, mini-game clocks) — timers can't be serialized, so on restore that
// one clue resets to the board at rest and can simply be re-picked.

import fs from 'fs';
import { GameState, serializeState } from './gameEngine';
import type { GameForPlay } from './games';
import { ensureDataDir, dataPath } from './dataDir';

const SNAPSHOT_FILE = dataPath('game-state.json');
const SNAPSHOT_VERSION = 1;

interface Snapshot {
  v: number;
  savedAt: number;
  gameState: ReturnType<typeof serializeState>;
  currentGame: GameForPlay;
}

let writeTimer: ReturnType<typeof setTimeout> | null = null;
let pending: { gameState: GameState; currentGame: GameForPlay } | null = null;

function buildSnapshot(gameState: GameState, currentGame: GameForPlay): Snapshot {
  return {
    v: SNAPSHOT_VERSION,
    savedAt: Date.now(),
    gameState: serializeState(gameState), // converts usedClues Set → array
    currentGame,
  };
}

// Debounced async write — coalesces the burst of broadcasts a single action can
// trigger into one disk write. Persistence errors never bubble up: losing a
// snapshot must never break live gameplay.
export function persistGame(gameState: GameState | null, currentGame: GameForPlay | null): void {
  if (!gameState || !currentGame) return;
  pending = { gameState, currentGame };
  if (writeTimer) return;
  writeTimer = setTimeout(() => {
    writeTimer = null;
    const p = pending;
    pending = null;
    if (!p) return;
    try {
      ensureDataDir();
      fs.promises
        .writeFile(SNAPSHOT_FILE, JSON.stringify(buildSnapshot(p.gameState, p.currentGame)))
        .catch(() => {});
    } catch { /* ignore */ }
  }, 400);
}

// Synchronous flush for graceful shutdown (SIGTERM on a Render redeploy), so the
// last few hundred ms of play aren't lost.
export function flushGameSync(): void {
  if (writeTimer) { clearTimeout(writeTimer); writeTimer = null; }
  const p = pending;
  pending = null;
  if (!p) return;
  try {
    ensureDataDir();
    fs.writeFileSync(SNAPSHOT_FILE, JSON.stringify(buildSnapshot(p.gameState, p.currentGame)));
  } catch { /* ignore */ }
}

// Drop the snapshot — called when the game returns to the lobby (reset / new
// game), so a stale in-progress game can't be resurrected on the next boot.
export function clearGame(): void {
  if (writeTimer) { clearTimeout(writeTimer); writeTimer = null; }
  pending = null;
  try { if (fs.existsSync(SNAPSHOT_FILE)) fs.unlinkSync(SNAPSHOT_FILE); } catch { /* ignore */ }
}

// Load + sanitize a persisted game. Returns null when there's nothing worth
// resuming (no file, corrupt, wrong version, or just a lobby). Timer-driven
// sub-state is reset to a safe resting point; durable progress is kept. All
// players come back as disconnected — they re-attach as their clients reconnect.
export function loadGame(): { gameState: GameState; currentGame: GameForPlay } | null {
  try {
    if (!fs.existsSync(SNAPSHOT_FILE)) return null;
    const snap = JSON.parse(fs.readFileSync(SNAPSHOT_FILE, 'utf8')) as Snapshot;
    if (!snap || snap.v !== SNAPSHOT_VERSION || !snap.gameState || !snap.currentGame) return null;

    const gs = snap.gameState as unknown as Record<string, unknown>;
    const phase = gs.phase as string | undefined;
    if (!phase || phase === 'lobby') return null; // only resume real games

    const gameState = {
      ...(gs as unknown as GameState),
      usedClues: new Set<number>(Array.isArray(gs.usedClues) ? (gs.usedClues as number[]) : []),
      // reset the single in-flight clue/mini-game interaction (its timers are gone)
      cluePhase: 'idle',
      activeClue: null,
      activeCategoryName: null,
      activeCategoryIdx: null,
      activeClueIdx: null,
      buzzedPlayerId: null,
      buzzOrder: [],
      wrongAnswerers: [],
      skippedBy: [],
      timerEndsAt: null,
      dailyDoubleWager: null,
      activeMiniGame: null,
      miniGameTrivia: null,
      miniGameData: null,
      players: (Array.isArray(gs.players) ? gs.players : []).map(
        (p) => ({ ...(p as GameState['players'][number]), connected: false }),
      ),
    } as GameState;

    return { gameState, currentGame: snap.currentGame };
  } catch {
    return null;
  }
}
