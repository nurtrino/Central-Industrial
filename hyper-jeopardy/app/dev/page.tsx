'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import { Socket } from 'socket.io-client';
import { createSocket } from '@/lib/socket-client';
import { GameState, Player } from '@/lib/gameEngine';
import { isUnavailableClue } from '@/lib/clueSentinel';

// Solo developer test harness: 4 independent sockets = 4 simulated players
// in a single tab. Lets you exercise lobby/buzzing/wagering/Final Jeopardy
// without recruiting humans. Pair with /display in another tab to watch the
// TV view react.
//
// State inspector shows phase + cluePhase + the active clue's CORRECT
// ANSWER so you can quickly test the fuzzy-judge logic without alt-tabbing.

const SLOT_NAMES = ['Alice', 'Bob', 'Carol', 'Dave'];
const NUM_SLOTS = 4;

interface SlotState {
  socket: Socket;
  playerId: string | null;
  player: Player | null;
}

export default function Dev() {
  const slotsRef = useRef<SlotState[]>([]);
  const [, setTick] = useState(0); // re-render trigger for slot mutations
  const [state, setState] = useState<GameState | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const bump = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    const slots: SlotState[] = SLOT_NAMES.slice(0, NUM_SLOTS).map(() => ({
      socket: createSocket(),
      playerId: null,
      player: null,
    }));
    slotsRef.current = slots;

    slots.forEach((slot, idx) => {
      slot.socket.on('state', (s: GameState) => {
        setState(s);
        // Keep the slot's own Player reference in sync (score, host flag).
        if (slot.playerId) {
          const me = s.players.find(p => p.id === slot.playerId);
          if (me && me !== slot.player) {
            slot.player = me;
            bump();
          }
          if (!me && slot.player) {
            slot.player = null;
            bump();
          }
        }
      });
      slot.socket.on('joined', ({ playerId, player }: { playerId: string; player: Player }) => {
        slot.playerId = playerId;
        slot.player = player;
        bump();
      });
      slot.socket.on('error', (msg: string) => {
        setLastError(`[slot ${idx + 1}] ${msg}`);
        setTimeout(() => setLastError(null), 4000);
      });
    });

    return () => {
      for (const slot of slots) slot.socket.disconnect();
    };
  }, [bump]);

  const slots = slotsRef.current;

  const joinSlot = (idx: number) => {
    const slot = slots[idx];
    if (!slot || slot.player) return;
    const joinedCount = slots.filter(s => s.player).length;
    slot.socket.emit('join', { name: SLOT_NAMES[idx], isHost: joinedCount === 0 });
  };

  const joinAll = () => {
    slots.forEach((_, i) => joinSlot(i));
  };

  // Emit using whichever socket currently controls the board (for clue picking).
  const controllerEmit = (event: string, payload?: unknown) => {
    if (!state?.boardController) return;
    const slot = slots.find(s => s.playerId === state.boardController);
    slot?.socket.emit(event, payload);
  };

  const hostEmit = (event: string, payload?: unknown) => {
    const slot = slots.find(s => s.player?.isHost);
    slot?.socket.emit(event, payload);
  };

  const selectClue = (catIdx: number, clueIdx: number) => controllerEmit('select_clue', { catIdx, clueIdx });
  const startGame = () => hostEmit('start_game');
  const newGame = () => hostEmit('new_game');
  const resetLobby = () => {
    if (!confirm('Reset lobby and disconnect all simulated players from the server?')) return;
    hostEmit('reset_lobby');
  };
  const revealFinal = () => hostEmit('reveal_final');

  // Returns true if it emitted something. Each call advances at most one
  // game-state transition for the given slot, so callers can chain via the
  // Step / Autoplay loop without risking infinite emits.
  const autoActForSlot = useCallback((slot: SlotState | undefined): boolean => {
    if (!slot || !state) return false;
    const me = slot.player;
    const id = slot.playerId;
    if (!me || !id) return false;

    const cp = state.cluePhase;
    const isBuzzed = state.buzzedPlayerId === id;
    const isController = state.boardController === id;
    const alreadyWrong = (state.wrongAnswerers ?? []).includes(id);
    const usedClueIds = Array.isArray(state.usedClues) ? state.usedClues as number[] : [];

    // Mid-clue: take whatever action this player can take.
    if (cp === 'buzzing' && !isBuzzed && !alreadyWrong) {
      slot.socket.emit('buzz');
      return true;
    }
    if ((cp === 'answering' || cp === 'daily_double_answer') && isBuzzed && state.activeClue) {
      slot.socket.emit('answer', { answer: state.activeClue.answer });
      return true;
    }
    if (cp === 'daily_double_wager' && isController) {
      const roundMax = state.phase === 'jeopardy' ? 1000 : 2000;
      const cap = Math.max(me.score, roundMax);
      slot.socket.emit('daily_double_wager', { wager: Math.min(500, cap) });
      return true;
    }

    // Final Jeopardy: wager small, then answer correctly.
    if (state.phase === 'final_jeopardy' && state.finalJeopardy) {
      const entry = state.finalEntries?.[id];
      if (entry && entry.wager === null) {
        slot.socket.emit('final_wager', { wager: Math.min(100, Math.max(0, me.score)) });
        return true;
      }
      if (entry && entry.answer === null) {
        slot.socket.emit('final_answer', { answer: state.finalJeopardy.answer });
        return true;
      }
    }

    // Between clues: controller picks the next unused clue. Only fire when
    // we're the controller so we don't race other slots' auto-actions.
    if (cp === 'idle' && isController && state.currentBoard &&
        (state.phase === 'jeopardy' || state.phase === 'double_jeopardy')) {
      for (let ci = 0; ci < state.currentBoard.length; ci++) {
        const cat = state.currentBoard[ci];
        for (let ri = 0; ri < cat.clues.length; ri++) {
          const c = cat.clues[ri];
          if (!c.used && !usedClueIds.includes(c.id)) {
            slot.socket.emit('select_clue', { catIdx: ci, clueIdx: ri });
            return true;
          }
        }
      }
    }

    return false;
  }, [state]);

  const stepOnce = useCallback(() => {
    for (const slot of slotsRef.current) {
      if (autoActForSlot(slot)) return;
    }
  }, [autoActForSlot]);

  // Autoplay: re-trigger one auto action whenever the state updates. The
  // helper emits at most one action per call, so the loop drives itself one
  // step per server roundtrip with no risk of runaway emits.
  const [autoplay, setAutoplay] = useState(false);
  useEffect(() => {
    if (!autoplay) return;
    stepOnce();
  }, [autoplay, state, stepOnce]);

  const joinedCount = slots.filter(s => s.player).length;
  const phase = state?.phase ?? '—';
  const cluePhase = state?.cluePhase ?? '—';
  const controller = state?.players.find(p => p.id === state?.boardController);
  const buzzed = state?.players.find(p => p.id === state?.buzzedPlayerId);

  return (
    <div className="min-h-screen p-4 sm:p-6">
      {lastError && (
        <div className="fixed top-4 right-4 bg-red-600 text-white px-4 py-2 rounded-lg z-50 shadow-lg text-sm">
          {lastError}
        </div>
      )}

      <div className="max-w-[1600px] mx-auto space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <h1 className="jeo-title text-2xl sm:text-3xl">DEV / TEST</h1>
          <div className="flex gap-3 text-sm jeo-headline tracking-widest uppercase">
            <a href="/display" target="_blank" rel="noreferrer" className="text-[var(--jeo-gold)] underline">/display ↗</a>
            <a href="/" target="_blank" rel="noreferrer" className="text-[var(--jeo-gold)] underline">/ ↗</a>
          </div>
        </div>

        {/* Quick actions */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={joinAll}
            disabled={joinedCount === NUM_SLOTS}
            className="jeo-btn-gold px-4 py-2 rounded text-sm disabled:opacity-40"
          >
            Auto-join all 4
          </button>
          {state?.phase === 'lobby' && (
            <button
              onClick={startGame}
              disabled={joinedCount < 2}
              className="jeo-btn-gold px-4 py-2 rounded text-sm disabled:opacity-40"
            >
              Start Game
            </button>
          )}
          {state && state.phase !== 'lobby' && (
            <button onClick={newGame} className="bg-blue-800 text-white px-4 py-2 rounded text-sm hover:bg-blue-700">
              New Game
            </button>
          )}
          {state && state.phase !== 'lobby' && state.phase !== 'game_over' && (
            <>
              <button onClick={stepOnce} className="bg-green-700 text-white px-4 py-2 rounded text-sm hover:bg-green-600 font-bold">
                Step (auto-correct)
              </button>
              <button
                onClick={() => setAutoplay(a => !a)}
                className={`px-4 py-2 rounded text-sm font-bold ${
                  autoplay
                    ? 'bg-green-500 text-blue-950 hover:bg-green-400'
                    : 'bg-green-900 text-green-100 hover:bg-green-800'
                }`}
              >
                {autoplay ? 'Autoplay: ON' : 'Autoplay: OFF'}
              </button>
            </>
          )}
          <button onClick={resetLobby} className="bg-red-900 text-red-100 px-4 py-2 rounded text-sm hover:bg-red-800">
            Reset Lobby
          </button>
        </div>

        {/* State inspector */}
        <div className="bg-[rgba(12,16,46,0.6)] border border-[rgba(0,229,255,0.2)] rounded-lg p-3 text-xs space-y-1 font-mono">
          <div>
            <span className="text-blue-200/70">phase:</span> <span className="text-white">{phase}</span>
            <span className="text-blue-200/70 ml-4">cluePhase:</span> <span className="text-white">{cluePhase}</span>
            {state && (
              <>
                <span className="text-blue-200/70 ml-4">show:</span> <span className="text-white">#{state.showNumber}</span>
              </>
            )}
          </div>
          <div>
            <span className="text-blue-200/70">controller:</span> <span className="text-[var(--jeo-gold)]">{controller?.name ?? '—'}</span>
            <span className="text-blue-200/70 ml-4">buzzed:</span> <span className="text-[var(--jeo-gold)]">{buzzed?.name ?? '—'}</span>
          </div>
          {state?.activeClue && (
            <>
              <div>
                <span className="text-blue-200/70">active:</span>{' '}
                <span className="text-white">{state.activeCategoryName} ${state.activeClue.value}</span>
                {state.activeClue.isDailyDouble && <span className="text-[var(--jeo-gold)] ml-2">(DD)</span>}
              </div>
              {state.activeClue.question && (
                <div className={isUnavailableClue(state.activeClue.question) ? 'text-red-300' : 'text-blue-100/90'}>
                  <span className="text-blue-200/70">Q:</span>{' '}
                  {isUnavailableClue(state.activeClue.question) ? '[blank box — image/audio clue, skip this one]' : state.activeClue.question}
                </div>
              )}
              <div className="text-green-300">
                <span className="text-blue-200/70">A:</span> {state.activeClue.answer}
              </div>
            </>
          )}
          {state?.phase === 'final_jeopardy' && state.finalJeopardy && (
            <>
              <div className="text-blue-100/90">
                <span className="text-blue-200/70">FJ Q:</span> {state.finalJeopardy.question}
              </div>
              <div className="text-green-300">
                <span className="text-blue-200/70">FJ A:</span> {state.finalJeopardy.answer}
              </div>
              <button onClick={revealFinal} className="jeo-btn-gold mt-1 px-3 py-1 rounded text-xs">
                Reveal Final
              </button>
            </>
          )}
        </div>

        {/* Clue picker (board controller) */}
        {state && state.currentBoard && state.cluePhase === 'idle' &&
         (state.phase === 'jeopardy' || state.phase === 'double_jeopardy') && (
          <div className="bg-[rgba(12,16,46,0.4)] rounded-lg p-3">
            <div className="text-xs text-blue-200/70 jeo-headline tracking-widest uppercase mb-2">
              Clue picker — clicks emit as {controller?.name ?? '(no controller)'}
            </div>
            <div className="grid grid-cols-6 gap-1">
              {state.currentBoard.map((cat, ci) => (
                <div key={ci} className="space-y-1">
                  <div className="text-[10px] text-blue-200/80 text-center px-1 leading-tight font-bold uppercase truncate" title={cat.name}>
                    {cat.name}
                  </div>
                  {cat.clues.map((c, ri) => (
                    <button
                      key={ri}
                      onClick={() => !c.used && selectClue(ci, ri)}
                      disabled={c.used || !controller}
                      className={`w-full text-xs py-1.5 rounded font-mono transition ${
                        c.used
                          ? 'bg-blue-950 text-blue-800'
                          : 'bg-blue-800 text-[var(--jeo-gold)] hover:bg-blue-700 disabled:opacity-50'
                      }`}
                    >
                      ${c.value}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Player panels */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {Array.from({ length: NUM_SLOTS }).map((_, idx) => (
            <PlayerPanel
              key={idx}
              idx={idx}
              slot={slots[idx]}
              state={state}
              onJoin={() => joinSlot(idx)}
              onAuto={() => autoActForSlot(slots[idx])}
            />
          ))}
        </div>

        <p className="text-xs text-blue-200/60 jeo-headline tracking-wide">
          Each panel runs an independent socket. Auto-join 4, Start Game, then use the clue picker (only
          active when it&apos;s your turn) and the per-panel buzz/answer/wager controls. Open
          <a href="/display" target="_blank" rel="noreferrer" className="text-[var(--jeo-gold)] underline mx-1">/display</a>
          in another tab to watch the TV view react.
        </p>
      </div>

    </div>
  );
}

function PlayerPanel({
  idx,
  slot,
  state,
  onJoin,
  onAuto,
}: {
  idx: number;
  slot: SlotState | undefined;
  state: GameState | null;
  onJoin: () => void;
  onAuto: () => void;
}) {
  const [answer, setAnswer] = useState('');
  const [wager, setWager] = useState('');

  if (!slot) {
    return <div className="rounded-lg border border-[rgba(0,229,255,0.1)] bg-[rgba(12,16,46,0.4)] p-4 text-blue-200/40 text-sm">slot {idx + 1} initializing...</div>;
  }

  const me = slot.player;
  const myId = slot.playerId;
  const cluePhase = state?.cluePhase;
  const isBuzzed = state?.buzzedPlayerId === myId;
  const isController = state?.boardController === myId;
  const alreadyWrong = state?.wrongAnswerers?.includes(myId ?? '');
  const alreadySkipped = (state?.skippedBy ?? []).includes(myId ?? '');
  const finalEntry = me && state?.finalEntries?.[me.id];

  const emit = (event: string, payload?: unknown) => slot.socket.emit(event, payload);
  const submitBuzz = () => emit('buzz');
  const submitSkip = () => emit('skip_clue');
  const submitAnswer = () => { if (answer.trim()) { emit('answer', { answer }); setAnswer(''); } };
  const submitDDWager = () => { const n = parseInt(wager, 10); if (Number.isFinite(n)) { emit('daily_double_wager', { wager: n }); setWager(''); } };
  const submitFinalWager = () => { const n = parseInt(wager, 10); if (Number.isFinite(n)) { emit('final_wager', { wager: n }); setWager(''); } };
  const submitFinalAnswer = () => { if (answer.trim()) { emit('final_answer', { answer }); setAnswer(''); } };

  return (
    <div className={`rounded-lg border p-3 space-y-2 ${
      me
        ? isBuzzed
          ? 'border-[var(--jeo-gold)] bg-[rgba(0,229,255,0.08)]'
          : isController
            ? 'border-green-500/60 bg-[rgba(12,16,46,0.6)]'
            : 'border-[rgba(0,229,255,0.2)] bg-[rgba(12,16,46,0.5)]'
        : 'border-[rgba(0,229,255,0.1)] bg-[rgba(12,16,46,0.3)]'
    }`}>
      <div className="flex items-center justify-between">
        <span className="font-bold text-white text-sm">
          {me?.name ?? `Slot ${idx + 1} (${SLOT_NAMES[idx]})`}
          {me?.isHost && <span className="ml-2 text-[10px] text-[var(--jeo-gold)]">HOST</span>}
        </span>
        <span className={`font-mono text-sm ${(me?.score ?? 0) < 0 ? 'text-red-300' : 'text-[var(--jeo-gold)]'}`}>
          {me ? (me.score < 0 ? `-$${Math.abs(me.score).toLocaleString()}` : `$${me.score.toLocaleString()}`) : '—'}
        </span>
      </div>

      {!me && (
        <button onClick={onJoin} className="jeo-btn-gold w-full py-2 rounded text-sm">
          Join as {SLOT_NAMES[idx]}
        </button>
      )}

      {me && state && state.phase !== 'lobby' && (
        <div className="space-y-2">
          {/* Auto button — single-action shortcut: buzz / answer correctly /
              wager / pick next clue (if controller). Compose with Step or
              Autoplay at the top of the page for fast-forwarding. */}
          <button
            onClick={onAuto}
            className="w-full py-1.5 bg-green-700 hover:bg-green-600 text-white text-xs jeo-headline tracking-widest uppercase rounded"
          >
            Auto
          </button>
          {isController && cluePhase === 'idle' && (
            <div className="text-[11px] text-green-300 jeo-headline tracking-wider uppercase">controls board</div>
          )}
          {isBuzzed && (cluePhase === 'answering' || cluePhase === 'daily_double_answer') && (
            <div className="text-[11px] text-[var(--jeo-gold)] jeo-headline tracking-wider uppercase">your buzz — answer below</div>
          )}
          {alreadyWrong && cluePhase === 'buzzing' && (
            <div className="text-[11px] text-red-300 jeo-headline tracking-wider uppercase">locked out</div>
          )}

          {cluePhase === 'buzzing' && !isBuzzed && !alreadyWrong && (
            <button onClick={submitBuzz} className="w-full py-3 bg-red-600 hover:bg-red-500 text-white font-bold rounded">
              BUZZ
            </button>
          )}

          {(cluePhase === 'reading' || cluePhase === 'buzzing') && !alreadySkipped && !alreadyWrong && (
            <button onClick={submitSkip} className="w-full py-1.5 text-xs text-blue-300 hover:text-[var(--jeo-gold)] underline">
              vote skip
            </button>
          )}

          {(cluePhase === 'answering' || cluePhase === 'daily_double_answer') && isBuzzed && (
            <div className="flex gap-1">
              <input
                value={answer}
                onChange={e => setAnswer(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && submitAnswer()}
                placeholder="answer"
                className="jeo-input flex-1 px-2 py-1.5 text-sm rounded"
                autoFocus
              />
              <button onClick={submitAnswer} className="jeo-btn-gold px-3 py-1.5 rounded text-xs">Send</button>
            </div>
          )}

          {cluePhase === 'daily_double_wager' && isController && (
            <div className="space-y-1">
              <div className="text-[11px] text-[var(--jeo-gold)] jeo-headline tracking-wider uppercase">DD wager</div>
              <div className="flex gap-1">
                <input
                  value={wager}
                  onChange={e => setWager(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && submitDDWager()}
                  type="number"
                  placeholder="amount"
                  className="jeo-input flex-1 px-2 py-1.5 text-sm rounded"
                  autoFocus
                />
                <button onClick={submitDDWager} className="jeo-btn-gold px-3 py-1.5 rounded text-xs">Wager</button>
              </div>
            </div>
          )}

          {state.phase === 'final_jeopardy' && finalEntry && (
            <div className="space-y-1">
              <div className="text-[11px] text-[var(--jeo-gold)] jeo-headline tracking-wider uppercase">
                FJ {finalEntry.wager !== null ? `(wagered $${finalEntry.wager})` : ''} {finalEntry.answer ? `(answered)` : ''}
              </div>
              {finalEntry.wager === null && (
                <div className="flex gap-1">
                  <input
                    value={wager}
                    onChange={e => setWager(e.target.value)}
                    type="number"
                    placeholder="FJ wager"
                    className="jeo-input flex-1 px-2 py-1.5 text-sm rounded"
                  />
                  <button onClick={submitFinalWager} className="jeo-btn-gold px-3 py-1.5 rounded text-xs">Wager</button>
                </div>
              )}
              {finalEntry.wager !== null && finalEntry.answer === null && (
                <div className="flex gap-1">
                  <input
                    value={answer}
                    onChange={e => setAnswer(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && submitFinalAnswer()}
                    placeholder="FJ answer"
                    className="jeo-input flex-1 px-2 py-1.5 text-sm rounded"
                  />
                  <button onClick={submitFinalAnswer} className="jeo-btn-gold px-3 py-1.5 rounded text-xs">Send</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

