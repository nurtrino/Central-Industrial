import { Server as SocketIOServer } from 'socket.io';
import { Server as HTTPServer } from 'http';
import {
  GameState,
  createGame,
  addPlayer,
  renamePlayer,
  startGame,
  selectClue,
  submitBuzz,
  submitAnswer,
  openBuzzing,
  buzzTimeout,
  answerTimeout,
  endReveal,
  submitDailyDoubleWager,
  submitFinalWager,
  submitFinalAnswer,
  revealFinalJeopardy,
  serializeState,
  advanceRound,
  submitSkip,
  beginHyperActive,
  endHyper,
  assignSpecialCells,
  HYPER_INTRO_MS,
  HYPER_MAX_MS,
} from './gameEngine';
import { loadRandomGame, getGameCount, GameForPlay } from './games';
import { fetchTrivia, type TriviaQuestion } from './opentdb';
import {
  initMiniGame,
  beginMiniGamePlaying,
  handleMiniGameAction,
  revealLetter,
  finishMiniGame,
  miniGameWinnerId,
  INTRO_MS,
  HYPER_ROUND_MS,
  RAPID_ROUND_MS,
  REVEAL_INTERVAL_MS,
  RESULTS_MS,
} from './miniGames';
import {
  Account, awardWinToAccount, createAccount, deleteAccount,
  getAccount, getAccounts, updateAccount,
} from './accounts';
import { persistGame, clearGame, loadGame, flushGameSync } from './gameStore';
import { Battle, initBattle, tickBattle, battleControl, battleSnapshot } from './invaders';

let io: SocketIOServer | null = null;
let gameState: GameState | null = null;
let currentGame: GameForPlay | null = null;
const timers: Map<string, NodeJS.Timeout> = new Map();
// Trivia for a hyper mini-game is fetched in parallel with the activation
// splash; we await this at the intro→active transition so questions are ready.
let hyperTriviaPromise: Promise<TriviaQuestion[] | null> = Promise.resolve(null);

// SPACE INVADERS AMBUSH — authoritative battle + its tick loop. The battle
// telemetry broadcasts on its own 'invaders' socket event (~20Hz, compact);
// the normal state broadcast only carries phase transitions.
let battle: Battle | null = null;
let invLoop: NodeJS.Timeout | null = null;

export function getIO() { return io; }
export function getGameState() { return gameState; }

function clearTimer(name: string) {
  const t = timers.get(name);
  if (t) { clearTimeout(t); timers.delete(name); }
}

function setTimer(name: string, ms: number, cb: () => void) {
  clearTimer(name);
  timers.set(name, setTimeout(cb, ms));
}

function broadcast() {
  if (!io || !gameState) return;
  io.emit('state', serializeState(gameState));
  // Durability: snapshot in-progress games; drop the snapshot once we're back
  // in the lobby (reset / new game) so a finished game isn't resurrected.
  if (gameState.phase === 'lobby') clearGame();
  else persistGame(gameState, currentGame);
}

function broadcastAccounts() {
  if (!io) return;
  io.emit('accounts', getAccounts());
}

// Reveal Final Jeopardy, credit the win(s), and broadcast the new game state
// + the updated accounts (which carry the leaderboard). Idempotent — only
// fires on the actual play → game_over transition. Only winners who joined
// via a real, still-existing Account get credited (by accountId). Guests
// (no accountId — e.g. typed a one-off name, or renamed in-lobby) NEVER
// create or touch a persisted Account, even on a win. This is deliberate:
// permanent accounts only come into existence through the explicit
// Create Account screen.
function creditWinners() {
  if (!gameState || !gameState.players.length) return;
  const top = Math.max(...gameState.players.map(p => p.score));
  const winners = gameState.players.filter(p => p.score === top);
  let anyCredited = false;
  for (const w of winners) {
    if (w.accountId && getAccount(w.accountId)) {
      awardWinToAccount(w.accountId);
      anyCredited = true;
    }
  }
  if (anyCredited) broadcastAccounts();
}

function finalizeAndAward() {
  if (!gameState) return;
  const wasGameOver = gameState.phase === 'game_over';
  revealFinalJeopardy(gameState);
  if (!wasGameOver && gameState.phase === 'game_over') creditWinners();
}

function ensureGameData() {
  return getGameCount();
}

export function initSocketServer(httpServer: HTTPServer) {
  if (io) return io;

  io = new SocketIOServer(httpServer, {
    cors: { origin: '*', methods: ['GET', 'POST'] },
    path: '/api/socket',
  });

  // Restore a game persisted before a restart/redeploy/cold start. Clients
  // reconnect and auto re-attach to their seats (see the socket `connect`
  // re-join on the controller). A stale in-flight clue is reset to the board.
  if (!gameState) {
    const restored = loadGame();
    if (restored) {
      gameState = restored.gameState;
      currentGame = restored.currentGame;
      console.log(`[persist] restored game — phase=${gameState.phase}, players=${gameState.players.length}`);
    }
  }

  // Flush the latest snapshot on graceful shutdown (Render sends SIGTERM on a
  // redeploy) so the final moments of play survive, then exit normally.
  const onSignal = () => { flushGameSync(); process.exit(0); };
  process.once('SIGTERM', onSignal);
  process.once('SIGINT', onSignal);

  io.on('connection', (socket) => {
    console.log('[socket] connected', socket.id);

    // Send current state + accounts on connect
    if (gameState) socket.emit('state', serializeState(gameState));
    socket.emit('accounts', getAccounts());

    socket.on('get_accounts', () => {
      socket.emit('accounts', getAccounts());
    });

    // --- account CRUD ---------------------------------------------------
    // Ack-style: the client passes a callback so it can chain a join right
    // after creation. Anyone can edit/delete — trust-based, matching the
    // existing reset-lobby and rename-in-lobby behavior.
    socket.on('create_account', (
      { name, avatar }: { name: string; avatar?: string },
      ack?: (resp: { account?: Account; error?: string }) => void,
    ) => {
      const acct = createAccount(name, avatar);
      if (!acct) {
        ack?.({ error: 'Could not create (name already used?)' });
        socket.emit('error', 'Could not create account');
        return;
      }
      ack?.({ account: acct });
      broadcastAccounts();
    });

    socket.on('update_account', (
      { id, name, avatar }: { id: string; name?: string; avatar?: string },
      ack?: (resp: { account?: Account; error?: string }) => void,
    ) => {
      const acct = updateAccount(id, { name, avatar });
      if (!acct) {
        ack?.({ error: 'Could not update' });
        socket.emit('error', 'Could not update account');
        return;
      }
      ack?.({ account: acct });
      // If a live player in the current game is bound to this account, mirror
      // the rename/avatar onto their Player record so it shows immediately.
      if (gameState) {
        const me = gameState.players.find(p => p.accountId === id);
        if (me) {
          if (acct.name) me.name = acct.name;
          me.avatar = acct.avatar;
        }
      }
      broadcastAccounts();
      broadcast();
    });

    socket.on('delete_account', ({ id }: { id: string }) => {
      if (!deleteAccount(id)) { socket.emit('error', 'Could not delete'); return; }
      broadcastAccounts();
    });

    socket.on('join', ({ name, isHost, avatar, accountId }: { name: string; isHost?: boolean; avatar?: string; accountId?: string }) => {
      if (!gameState) {
        ensureGameData();
        const game = loadRandomGame();
        if (!game) { socket.emit('error', 'No game data available'); return; }
        currentGame = game;
        gameState = createGame(game.showNumber, game.airDate);
      }

      // If the client passed an account id, the server's stored name + avatar
      // for that account is the source of truth — overlays whatever the
      // client claimed.
      let resolvedName = name;
      let resolvedAvatar = avatar;
      if (accountId) {
        const acct = getAccount(accountId);
        if (acct) { resolvedName = acct.name; resolvedAvatar = acct.avatar; }
      }

      // Idempotent join: re-attach by socket → accountId → name in that order.
      const matchExisting =
        gameState.players.find(p => p.id === socket.id) ||
        (accountId ? gameState.players.find(p => p.accountId === accountId) : null) ||
        gameState.players.find(p => p.name === resolvedName);
      if (matchExisting) {
        const oldId = matchExisting.id;
        matchExisting.id = socket.id;
        matchExisting.connected = true;
        matchExisting.name = resolvedName;
        matchExisting.avatar = resolvedAvatar || undefined;
        if (accountId) matchExisting.accountId = accountId;
        if (oldId !== socket.id) {
          if (gameState.boardController === oldId) gameState.boardController = socket.id;
          if (gameState.buzzedPlayerId === oldId) gameState.buzzedPlayerId = socket.id;
          gameState.skippedBy = gameState.skippedBy.map(id => id === oldId ? socket.id : id);
          if (gameState.scores[oldId]) {
            gameState.scores[socket.id] = gameState.scores[oldId];
            delete gameState.scores[oldId];
          }
          if (gameState.finalEntries[oldId]) {
            gameState.finalEntries[socket.id] = gameState.finalEntries[oldId];
            delete gameState.finalEntries[oldId];
          }
        }
        socket.emit('joined', { playerId: socket.id, player: matchExisting });
        broadcast();
        return;
      }

      if (gameState.phase !== 'lobby') {
        socket.emit('error', 'Game already started');
        return;
      }

      const hostFlag = isHost || gameState.players.length === 0;
      const player = addPlayer(gameState, resolvedName, socket.id, hostFlag, resolvedAvatar, accountId);
      socket.emit('joined', { playerId: socket.id, player });
      broadcast();
    });

    // In-lobby rename is a per-game, ephemeral display name only — it never
    // touches the persisted Account, even if this player joined via one.
    // "Temporary name for tonight" and "my permanent account" are fully
    // separate; to actually rename your account use the Edit Account screen.
    socket.on('rename', ({ name }: { name: string }) => {
      if (!gameState) return;
      const ok = renamePlayer(gameState, socket.id, name);
      if (!ok) { socket.emit('error', 'Cannot rename now'); return; }
      const me = gameState.players.find(p => p.id === socket.id);
      if (me) socket.emit('joined', { playerId: socket.id, player: me });
      broadcast();
    });

    socket.on('start_game', () => {
      if (!gameState || !currentGame) return;
      if (!gameState.players.find(p => p.id === socket.id && p.isHost)) return;
      if (gameState.players.length < 2) { socket.emit('error', 'Need at least 2 players to start'); return; }
      startGame(gameState, currentGame);
      broadcast();
    });

    socket.on('select_clue', ({ catIdx, clueIdx }: { catIdx: number; clueIdx: number }) => {
      if (!gameState) return;
      const ok = selectClue(gameState, catIdx, clueIdx, socket.id);
      if (!ok) { socket.emit('error', 'Cannot select that clue'); return; }

      broadcast();

      if (gameState.cluePhase === 'hyper_intro') {
        // HYPER MODE: play the activation splash (~3.5s), pre-fetching trivia in
        // parallel so it's ready, then start the mini-game. Trivia is medium,
        // random allowed category (excludes Musicals & Theatres and Anime) unless forced.
        const mg = gameState.activeMiniGame;
        hyperTriviaPromise = (mg && mg.trivia !== false)
          ? fetchTrivia({ amount: mg.triviaCount ?? 1, category: mg.trivia, difficulty: 'medium' })
          : Promise.resolve(null);
        setTimer('hyper_intro', HYPER_INTRO_MS, async () => {
          if (!gameState || gameState.cluePhase !== 'hyper_intro') return;
          const qs = await hyperTriviaPromise.catch(() => null);
          if (!gameState || gameState.cluePhase !== 'hyper_intro') return; // round changed while awaiting
          if (qs) gameState.miniGameTrivia = qs;
          startMiniGame();
        });
      } else if (gameState.cluePhase === 'reading') {
        // After reading delay, open buzzing
        setTimer('reading', 6000, () => {
          if (gameState && gameState.cluePhase === 'reading') {
            openBuzzing(gameState);
            broadcast();
            setTimer('buzz', 10000, () => {
              if (gameState && gameState.cluePhase === 'buzzing') {
                buzzTimeout(gameState);
                broadcast();
                setTimer('reveal', 3000, () => {
                  if (gameState && gameState.cluePhase === 'reveal') {
                    endReveal(gameState);
                    maybeAdvanceRound();
                    broadcast();
                  }
                });
              }
            });
          }
        });
      }
      // daily_double_wager handled separately
    });

    socket.on('buzz', () => {
      if (!gameState) return;
      const ok = submitBuzz(gameState, socket.id);
      if (!ok) return;

      broadcast();

      if (gameState.cluePhase === 'answering') {
        clearTimer('buzz');
        setTimer('answer', 15000, () => {
          if (gameState && (gameState.cluePhase as string) === 'answering') {
            answerTimeout(gameState);
            broadcast();

            const phaseAfter = gameState.cluePhase as string;
            if (phaseAfter === 'buzzing') {
              setTimer('buzz', 10000, () => {
                if (gameState && (gameState.cluePhase as string) === 'buzzing') {
                  buzzTimeout(gameState);
                  broadcast();
                  setTimer('reveal', 3000, () => {
                    if (gameState && (gameState.cluePhase as string) === 'reveal') {
                      endReveal(gameState);
                      maybeAdvanceRound();
                      broadcast();
                    }
                  });
                }
              });
            } else if (phaseAfter === 'reveal') {
              setTimer('reveal', 3000, () => {
                if (gameState && (gameState.cluePhase as string) === 'reveal') {
                  endReveal(gameState);
                  maybeAdvanceRound();
                  broadcast();
                }
              });
            }
          }
        });
      }
    });

    socket.on('skip_clue', () => {
      if (!gameState) return;
      const { added, allSkipped } = submitSkip(gameState, socket.id);
      if (!added) return;
      broadcast();
      if (allSkipped) {
        // Everyone voted skip — clear pending reading/buzz timers and reveal
        clearTimer('reading');
        clearTimer('buzz');
        setTimer('reveal', 3000, () => {
          if (gameState && gameState.cluePhase === 'reveal') {
            endReveal(gameState);
            maybeAdvanceRound();
            broadcast();
          }
        });
      }
    });

    // Mini-game action channel: routes a player's move to the active game's
    // logic, acks immediate feedback (correct/wrong/points) for the phone, and
    // finishes the round the moment every player is done.
    socket.on('mini_game_action', (
      action: { type: string; payload?: unknown },
      ack?: (feedback: unknown) => void,
    ) => {
      if (!gameState) { ack?.({}); return; }
      const res = handleMiniGameAction(gameState, socket.id, action || { type: '' });
      ack?.(res.feedback);
      if (res.changed) broadcast();
      if (res.complete) finishMini();
    });

    // SPACE INVADERS AMBUSH: phone controls. 'L'/'R' start moving (held),
    // 'S' stops, 'F' fires. No broadcast — the 20Hz tick loop carries state.
    socket.on('invader_ctl', ({ a }: { a?: string } = {}) => {
      if (!battle || !gameState || gameState.cluePhase !== 'invaders') return;
      const me = gameState.players.find(p => p.id === socket.id);
      battleControl(battle, socket.id, me?.name, String(a ?? ''), Date.now());
    });

    // Playtest: the host arms (or disarms) the SPACE INVADERS ambush — it
    // springs right after the next resolved clue, in any board round.
    socket.on('arm_invasion', () => {
      if (!gameState) return;
      if (!gameState.players.find(p => p.id === socket.id && p.isHost)) return;
      if (gameState.cluePhase === 'invaders') return;
      gameState.invadersArmed = !gameState.invadersArmed;
      broadcast();
    });

    // Dev/test shortcut (set DEV_SHORTCUTS=1): burn the rest of the current
    // board so the next round — or the ambush — arrives immediately.
    if (process.env.DEV_SHORTCUTS === '1') {
      socket.on('dev_advance_round', () => {
        if (!gameState || !currentGame) return;
        if (!gameState.players.find(p => p.id === socket.id)) return;
        if (gameState.cluePhase !== 'idle') return;
        for (const cat of gameState.currentBoard ?? []) {
          for (const c of cat.clues) { c.used = true; gameState.usedClues.add(c.id); }
        }
        maybeAdvanceRound();
        broadcast();
      });
    }

    socket.on('answer', ({ answer }: { answer: string }) => {
      if (!gameState) return;
      clearTimer('answer');
      const result = submitAnswer(gameState, socket.id, answer);
      if (result === 'ignored') return;

      broadcast();
      io?.emit('answer_result', { playerId: socket.id, answer, result, correct: gameState.activeClue?.answer });

      if (gameState.cluePhase === 'reveal') {
        setTimer('reveal', 3000, () => {
          if (gameState && gameState.cluePhase === 'reveal') {
            endReveal(gameState);
            maybeAdvanceRound();
            broadcast();
          }
        });
      } else if (gameState.cluePhase === 'buzzing') {
        clearTimer('buzz');
        setTimer('buzz', 10000, () => {
          if (gameState && gameState.cluePhase === 'buzzing') {
            buzzTimeout(gameState);
            broadcast();
            setTimer('reveal', 3000, () => {
              if (gameState && gameState.cluePhase === 'reveal') {
                endReveal(gameState);
                maybeAdvanceRound();
                broadcast();
              }
            });
          }
        });
      }
    });

    socket.on('daily_double_wager', ({ wager }: { wager: number }) => {
      if (!gameState) return;
      const ok = submitDailyDoubleWager(gameState, socket.id, wager);
      if (!ok) return;
      broadcast();
      setTimer('dd_answer', 30000, () => {
        if (gameState && gameState.cluePhase === 'daily_double_answer') {
          // Auto-wrong on timeout
          const result = submitAnswer(gameState, socket.id, '');
          broadcast();
          setTimer('reveal', 3000, () => {
            if (gameState && gameState.cluePhase === 'reveal') {
              endReveal(gameState);
              maybeAdvanceRound();
              broadcast();
            }
          });
        }
      });
    });

    socket.on('final_wager', ({ wager }: { wager: number }) => {
      if (!gameState) return;
      submitFinalWager(gameState, socket.id, wager);
      broadcast();

      // Once every eligible (positive-score) player has wagered, start the 45s think clock.
      const eligible = gameState.players.filter(p => p.score > 0);
      const allWagered = eligible.length > 0 && eligible.every(p => gameState!.finalEntries[p.id]?.wager !== null);
      if (allWagered && !timers.has('final_think')) {
        setTimer('final_think', 45_000, () => {
          if (!gameState || gameState.phase !== 'final_jeopardy' || gameState.finalRevealed) return;
          // Lock in any missing answers as empty, then reveal
          for (const p of gameState.players) {
            const e = gameState.finalEntries[p.id];
            if (e && e.answer === null) submitFinalAnswer(gameState, p.id, '');
          }
          finalizeAndAward();
          broadcast();
        });
      }
    });

    socket.on('final_answer', ({ answer }: { answer: string }) => {
      if (!gameState) return;
      submitFinalAnswer(gameState, socket.id, answer);
      broadcast();

      const eligible = gameState.players.filter(p => p.score > 0);
      const allAnswered = eligible.length > 0 && eligible.every(p => gameState!.finalEntries[p.id]?.answer !== null);
      if (allAnswered) {
        clearTimer('final_think');
        finalizeAndAward();
        broadcast();
      }
    });

    socket.on('reveal_final', () => {
      if (!gameState) return;
      if (!gameState.players.find(p => p.id === socket.id && p.isHost)) return;
      clearTimer('final_think');
      finalizeAndAward();
      broadcast();
    });

    socket.on('new_game', async () => {
      if (!gameState) return;
      if (!gameState.players.find(p => p.id === socket.id && p.isHost)) return;
      for (const name of Array.from(timers.keys())) clearTimer(name);
      stopInvasionLoop();
      const game = loadRandomGame();
      if (!game) { socket.emit('error', 'No game data'); return; }
      currentGame = game;
      const prevPlayers = gameState.players.map(p => ({ ...p, score: 0 }));
      gameState = createGame(game.showNumber, game.airDate);
      prevPlayers.forEach(p => addPlayer(gameState!, p.name, p.id, p.isHost));
      broadcast();
    });

    // Purge all players and reset the lobby. Allowed for any current player
    // (including the host) so a stuck lobby can always be cleared.
    socket.on('reset_lobby', () => {
      if (!gameState) return;
      if (!gameState.players.find(p => p.id === socket.id)) return;
      for (const name of Array.from(timers.keys())) clearTimer(name);
      stopInvasionLoop();
      if (!currentGame) currentGame = loadRandomGame();
      if (!currentGame) { socket.emit('error', 'No game data'); return; }
      gameState = createGame(currentGame.showNumber, currentGame.airDate);
      broadcast();
    });

    socket.on('set_score', ({ playerId, score }: { playerId: string; score: number }) => {
      if (!gameState) return;
      if (!gameState.players.find(p => p.id === socket.id && p.isHost)) return;
      const target = gameState.players.find(p => p.id === playerId);
      if (!target) return;
      const next = Math.round(Number(score));
      if (!Number.isFinite(next)) return;
      target.score = next;
      gameState.scores[target.id]?.push(next);
      broadcast();
    });

    socket.on('disconnect', () => {
      if (gameState) {
        const p = gameState.players.find(pl => pl.id === socket.id);
        if (p) p.connected = false;
        broadcast();
      }
    });
  });

  return io;
}

// ── HYPER MODE mini-game orchestration ──────────────────────────────────────
function clearMiniGameTimers() {
  clearTimer('hyper_intro');
  clearTimer('hyper_cap');
  clearTimer('mg_round');
  clearTimer('mg_intro');
  clearTimer('mg_reveal');
  clearTimer('mg_results');
}

// Kick off the mini-game once the activation splash ends and trivia (if any)
// has been attached. First shows a ~5s rules screen (status 'intro') on all
// screens, THEN reveals the game and opens input (beginPlaying).
function startMiniGame() {
  if (!gameState || gameState.cluePhase !== 'hyper_intro') return;
  beginHyperActive(gameState);
  initMiniGame(gameState); // status: 'intro'
  broadcast();
  // safety cap so a game can never hang the board
  setTimer('hyper_cap', HYPER_MAX_MS, () => finishMini());
  setTimer('mg_intro', INTRO_MS, () => beginPlaying());
}

// Rules screen over → reveal the game, open input, start the round timers.
function beginPlaying() {
  if (!gameState || gameState.cluePhase !== 'hyper_active') return;
  const d = gameState.miniGameData as { status?: string } | null;
  if (!d || d.status !== 'intro') return;
  beginMiniGamePlaying(gameState);
  broadcast();

  // Round cap: Rapid Fire is a hard 30s sprint; the others run 60s and can end
  // earlier once every player is resolved (solved / done / out).
  // Memory Matrix paces itself per player (patterns flash on each phone).
  const key = gameState.activeMiniGame?.key;
  setTimer('mg_round', key === 'rapid_fire' ? RAPID_ROUND_MS : HYPER_ROUND_MS, () => finishMini());
  // Letter Reveal also paces its own letter reveals on top of the cap.
  if (key === 'letter_reveal') scheduleReveal();
}

// Letter Reveal: expose one more letter every interval; after the last, a grace
// window, then finish.
function scheduleReveal() {
  setTimer('mg_reveal', REVEAL_INTERVAL_MS, () => {
    if (!gameState || gameState.cluePhase !== 'hyper_active') return;
    const d = gameState.miniGameData as { status?: string } | null;
    if (!d || d.status !== 'playing') return;
    const { fullyRevealed } = revealLetter(gameState);
    broadcast();
    // After the last letter, stop revealing; the round ends on all-resolved or
    // the 60s cap (mg_round) — same as the other games.
    if (!fullyRevealed) scheduleReveal();
  });
}

// End the mini-game: award scores, show the results screen, then return to the
// board after a beat. Idempotent (guards against results-phase re-entry).
function finishMini() {
  if (!gameState || gameState.cluePhase !== 'hyper_active') return;
  const d = gameState.miniGameData as { status?: string } | null;
  if (!d || d.status === 'results') return;
  clearMiniGameTimers();
  finishMiniGame(gameState);
  broadcast();
  setTimer('mg_results', RESULTS_MS, () => {
    if (gameState && gameState.cluePhase === 'hyper_active') {
      // The mini-game winner takes control of the board for the next pick
      // (read before endHyper clears miniGameData). Skip if they've since
      // disconnected, or if nobody placed — then the current controller stays.
      const winnerId = miniGameWinnerId(gameState);
      endHyper(gameState);
      if (winnerId && gameState.players.some(p => p.id === winnerId && p.connected)) {
        gameState.boardController = winnerId;
      }
      maybeAdvanceRound();
      broadcast();
    }
  });
}

// ── SPACE INVADERS AMBUSH orchestration ─────────────────────────────────────
// One wave, sprung once at a random point in Double Jeopardy. Win together →
// trivia resumes. Lose (fleet destroyed or invaders land) → the WHOLE game
// ends at current scores — no Final Jeopardy.
function stopInvasionLoop() {
  if (invLoop) { clearInterval(invLoop); invLoop = null; }
  clearTimer('inv_end');
  battle = null;
}

function startInvasion() {
  if (!gameState || !io) return;
  const roster = gameState.players.filter(p => p.connected);
  if (!roster.length) { gameState.invadersDone = true; return; }
  battle = initBattle(roster.map(p => ({ id: p.id, name: p.name })), Date.now());
  gameState.cluePhase = 'invaders';
  gameState.invaders = {
    status: 'intro',
    roster: battle.ships.map(s => ({ id: s.id, name: s.name, color: s.color })),
  };
  broadcast();
  invLoop = setInterval(tickInvasion, 50);
}

function tickInvasion() {
  if (!gameState || !battle || gameState.cluePhase !== 'invaders') { stopInvasionLoop(); return; }
  tickBattle(battle, Date.now());
  io?.emit('invaders', battleSnapshot(battle));
  if (gameState.invaders) gameState.invaders.status = battle.status;

  if (battle.status === 'won' || battle.status === 'lost') {
    const won = battle.status === 'won';
    if (invLoop) { clearInterval(invLoop); invLoop = null; } // keep `battle` for the outro frame
    broadcast();
    setTimer('inv_end', 5_000, () => (won ? resumeFromInvasion() : endGameByInvasion()));
  }
}

function resumeFromInvasion() {
  if (!gameState) return;
  battle = null;
  gameState.invaders = null;
  gameState.invadersDone = true;
  gameState.cluePhase = 'idle';
  broadcast();
  maybeAdvanceRound(); // the board may already be exhausted → on to Final Jeopardy
}

function endGameByInvasion() {
  if (!gameState) return;
  battle = null;
  gameState.invaders = null;
  gameState.invadersDone = true;
  gameState.cluePhase = 'idle';
  gameState.phase = 'game_over'; // scores stand as-is; Final Jeopardy is skipped
  creditWinners();
  broadcast();
}

function maybeAdvanceRound() {
  if (!gameState || !currentGame) return;
  if (gameState.cluePhase !== 'idle') return;

  // SPACE INVADERS AMBUSH: springs the moment the board goes idle after the
  // trigger threshold — before any round advancement. The host's playtest
  // "arm" button forces it after the next resolved clue in ANY board round
  // (and ignores the one-per-game flag so it can be tested repeatedly).
  const inBoardRound = gameState.phase === 'jeopardy' || gameState.phase === 'double_jeopardy';
  const naturalTrigger =
    gameState.phase === 'double_jeopardy' &&
    !gameState.invadersDone &&
    gameState.invadersTriggerAt > 0 &&
    gameState.usedClues.size >= gameState.invadersTriggerAt;
  if (inBoardRound && (gameState.invadersArmed || naturalTrigger)) {
    gameState.invadersArmed = false;
    startInvasion();
    return;
  }

  const phase = gameState.phase as string;
  const allUsed = gameState.currentBoard?.every(cat =>
    cat.clues.every(c => c.used || gameState!.usedClues.has(c.id))
  );
  if (!allUsed) return;

  if (phase === 'jeopardy') {
    advanceRound(gameState); // → 'double_jeopardy'
    gameState.currentBoard = currentGame.doubleJeopardyRound;
    const djSpecial = assignSpecialCells(currentGame.doubleJeopardyRound);
    gameState.hyperClues = djSpecial.hyperClues;
    gameState.hyperGames = djSpecial.hyperGames;
    // Arm the AMBUSH: it springs after the Nth resolved clue of this round
    // (random 3rd–11th; INVADERS_AFTER=<n> pins it for testing).
    const after = Math.max(1, parseInt(process.env.INVADERS_AFTER || '', 10) || (3 + Math.floor(Math.random() * 9)));
    gameState.invadersTriggerAt = gameState.usedClues.size + after;
    // Real-Jeopardy rule: trailing (lowest-score) connected player picks
    // first in DJ. If everyone is tied, keep the current board controller.
    const connected = gameState.players.filter(p => p.connected);
    const sorted = [...connected].sort((a, b) => a.score - b.score);
    const trailing = sorted[0];
    if (trailing && sorted.some(p => p.score !== trailing.score)) {
      gameState.boardController = trailing.id;
    }
    broadcast();
  } else if (phase === 'double_jeopardy') {
    advanceRound(gameState); // → 'final_jeopardy'
    broadcast();
  }
}
