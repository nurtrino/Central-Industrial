'use client';
import { useEffect, useRef, useState } from 'react';
import { Socket } from 'socket.io-client';
import { getSocket } from '@/lib/socket-client';
import { GameState } from '@/lib/gameEngine';
import { isUnavailableClue } from '@/lib/clueSentinel';
import {
  unlockAudio, preloadLasers, playHyperStart, playGameStart, playBoardFill,
  playBuzzIn, playDailyDouble, playCorrect, playWrong, playTimeUp,
} from '@/lib/audio';
import Board from '@/components/Board';
import Scoreboard from '@/components/Scoreboard';
import MiniGameStage from '@/components/MiniGameStage';
import InvadersStage from '@/components/InvadersStage';

// The shared screen is sized for a TV. On phones (<=640px) the `display-scale`
// class (globals.css) zooms it to 75% so the whole board + scoreboard fit;
// big screens are unaffected (100%).

// Read-only "TV" view. Connects to the same socket room but never emits
// `join`, so the server doesn't track it as a player. Renders the board
// + scoreboard, swaps to a fullscreen clue when one is active, and
// reveals the answer when the engine transitions to the reveal phase.
export default function Display() {
  const [, setSocket] = useState<Socket | null>(null);
  const [state, setState] = useState<GameState | null>(null);
  const [connected, setConnected] = useState(false);
  const [revealHyper, setRevealHyper] = useState(false);
  const prevCluePhaseRef = useRef<string | null>(null);

  // Hyper (mini-game) cells are hidden on the board so they stay a surprise.
  // Add ?reveal=on (or ?reveal) to color-code them again for testing.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setRevealHyper(q.get('reveal') === 'on' || (q.has('reveal') && q.get('reveal') !== 'off'));
  }, []);

  useEffect(() => {
    const s = getSocket();
    setSocket(s);
    s.on('connect', () => setConnected(true));
    s.on('disconnect', () => setConnected(false));
    s.on('state', (newState: GameState) => setState(newState));
    return () => {
      s.off('connect');
      s.off('disconnect');
      s.off('state');
    };
  }, []);

  // Preload the hyper-start clips so activation fires instantly, and unlock
  // audio on the first gesture (browser autoplay policy).
  useEffect(() => {
    preloadLasers();
    const unlock = () => unlockAudio();
    window.addEventListener('pointerdown', unlock, { once: true });
    window.addEventListener('keydown', unlock, { once: true });
    return () => {
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, []);

  // HYPER MODE activation + clue-flow sounds: the shared screen plays the SAME
  // effects as every phone (buzz-in, Daily Double fanfare) on phase changes.
  useEffect(() => {
    const phase = state?.cluePhase ?? null;
    const prev = prevCluePhaseRef.current;
    if (prev !== phase) {
      if (phase === 'hyper_intro') playHyperStart(state?.hyperSeed ?? 0);
      if (phase === 'answering' && prev === 'buzzing') playBuzzIn();
      if (phase === 'daily_double_wager') playDailyDouble();
      prevCluePhaseRef.current = phase;
    }
  }, [state?.cluePhase, state?.hyperSeed]);

  // Round-change sounds — same as the phones: laser-charge for the first
  // board, board-fill for Double Jeopardy.
  const lastBoardPhaseRef = useRef<string | null>(null);
  useEffect(() => {
    const phase = state?.phase;
    if (!phase) return;
    if ((phase === 'jeopardy' || phase === 'double_jeopardy') && lastBoardPhaseRef.current !== phase) {
      lastBoardPhaseRef.current = phase;
      if (phase === 'jeopardy') playGameStart();
      else playBoardFill();
    }
  }, [state?.phase]);

  // Correct/wrong stings on judged answers (same broadcast the phones use).
  useEffect(() => {
    const s = getSocket();
    const onResult = ({ result }: { result?: string }) => {
      if (result === 'correct') playCorrect();
      else if (result === 'wrong') playWrong();
    };
    s.on('answer_result', onResult);
    return () => { s.off('answer_result', onResult); };
  }, []);

  // Time-up buzzer when a buzz/answer clock expires.
  useEffect(() => {
    const phase = state?.cluePhase;
    const endsAt = state?.timerEndsAt;
    if (!endsAt || !phase) return;
    if (phase !== 'buzzing' && phase !== 'answering' && phase !== 'daily_double_answer') return;
    const ms = endsAt - Date.now();
    if (ms <= 0) return;
    const t = setTimeout(() => playTimeUp(), ms);
    return () => clearTimeout(t);
  }, [state?.cluePhase, state?.timerEndsAt]);

  if (!connected || !state) {
    return (
      <div className="display-scale min-h-screen flex items-center justify-center">
        <div className="text-center space-y-6">
          <div className="w-16 h-16 border-4 border-[var(--jeo-gold)] border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="jeo-headline tracking-[0.3em] uppercase text-blue-200/80 text-2xl">
            {connected ? 'Waiting for game...' : 'Connecting...'}
          </p>
        </div>
      </div>
    );
  }

  if (state.phase === 'lobby') {
    return (
      <div className="display-scale min-h-screen flex flex-col items-center justify-center px-8 gap-8">
        <h1 className="jeo-title text-6xl sm:text-8xl">HYPER JEOPARDY</h1>
        <p className="jeo-headline tracking-[0.4em] uppercase text-blue-200/80 text-2xl">Waiting for players</p>
        <div className="mt-8 w-full max-w-3xl space-y-3">
          {state.players.length === 0 && (
            <p className="text-center text-blue-200/50 jeo-headline tracking-widest uppercase text-xl">
              No players yet
            </p>
          )}
          {state.players.map(p => (
            <div
              key={p.id}
              className="flex items-center gap-4 px-8 py-5 rounded-2xl bg-[rgba(12,16,46,0.6)] border border-[rgba(0,229,255,0.2)]"
            >
              <div className={`w-5 h-5 rounded-full ${p.connected ? 'bg-green-400 shadow-[0_0_12px_rgba(74,222,128,0.8)]' : 'bg-gray-500'}`} />
              <span className="text-3xl font-semibold text-white">{p.name}</span>
              {p.isHost && (
                <span className="ml-auto text-sm jeo-headline tracking-widest text-[var(--jeo-gold)] uppercase">Host</span>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (state.phase === 'game_over') {
    const ranked = [...state.players].sort((a, b) => b.score - a.score);
    return (
      <div className="display-scale min-h-screen flex flex-col items-center justify-center px-8 gap-10">
        <h1 className="jeo-title text-6xl sm:text-8xl">GAME OVER</h1>
        <div className="w-full max-w-3xl space-y-3">
          {ranked.map((p, i) => (
            <div
              key={p.id}
              className={`flex items-center gap-4 px-8 py-5 rounded-2xl border ${
                i === 0
                  ? 'bg-[rgba(0,229,255,0.15)] border-[var(--jeo-gold)]'
                  : 'bg-[rgba(12,16,46,0.6)] border-[rgba(0,229,255,0.2)]'
              }`}
            >
              <span className="text-3xl jeo-headline tracking-widest text-blue-200/70">#{i + 1}</span>
              <span className="text-3xl font-semibold text-white">{p.name}</span>
              <span className={`ml-auto jeo-value text-4xl ${p.score < 0 ? 'text-red-300' : ''}`}>
                {p.score.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const activeClue = state.activeClue;
  const isHyper = state.cluePhase === 'hyper_intro' || state.cluePhase === 'hyper_active';
  const isInvaders = state.cluePhase === 'invaders';
  const hyperController = state.players.find(p => p.id === state.boardController);
  const showFullscreenClue = !!activeClue && state.cluePhase !== 'idle' && !isHyper;
  const buzzedPlayer = state.players.find(p => p.id === state.buzzedPlayerId);
  const isReveal = state.cluePhase === 'reveal';
  const isDailyDouble = !!activeClue?.isDailyDouble;
  // While the controller is wagering on a Daily Double, hide the clue so
  // they can't see it before committing — matches ClueModal's player-side
  // gate.
  const hideClueForWager = state.cluePhase === 'daily_double_wager';
  const wageringPlayer = state.players.find(p => p.id === state.boardController);

  const roundLabel =
    state.phase === 'jeopardy' ? 'Hyper Jeopardy!'
    : state.phase === 'double_jeopardy' ? 'Double Jeopardy!'
    : 'Final Jeopardy!';

  return (
    <div className="display-scale min-h-screen flex flex-col">
      {/* Header */}
      <div className="bg-[rgba(8,10,30,0.7)] border-b border-[rgba(0,229,255,0.25)] py-4 px-8">
        <div className="max-w-[1800px] mx-auto flex items-center justify-between">
          <h1 className="jeo-title text-3xl sm:text-5xl">{roundLabel}</h1>
          <p className="jeo-headline tracking-[0.3em] uppercase text-blue-200/70 text-sm sm:text-base">
            Show #{state.showNumber}{state.airDate ? ` · ${state.airDate}` : ''}
          </p>
        </div>
      </div>

      {/* Board OR fullscreen clue OR hyper takeover */}
      <div className="flex-1 flex items-center justify-center p-6">
        {!showFullscreenClue && !isHyper && state.currentBoard && (
          <div className="w-full max-w-[1800px] pointer-events-none">
            <Board
              board={state.currentBoard}
              state={state}
              playerId={null}
              onSelectClue={() => {}}
              revealHyper={revealHyper}
            />
          </div>
        )}

        {isHyper && (
          <div className="relative w-full max-w-[1400px] flex flex-col items-center justify-center gap-8 text-center">
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="hyper-burst w-[60vmin] h-[60vmin] rounded-full" />
            </div>
            {state.cluePhase === 'hyper_intro' ? (
              <div className="relative space-y-6">
                <p className="jeo-headline uppercase tracking-[0.5em] text-blue-200/80 text-2xl">Hyper Mode</p>
                <h2 className="hyper-title text-7xl sm:text-9xl">ACTIVATED</h2>
                <p className="jeo-headline uppercase tracking-[0.3em] text-white/90 text-2xl sm:text-3xl">
                  {state.activeMiniGame ? state.activeMiniGame.title : 'Mini-game incoming'}
                </p>
                {hyperController && (
                  <p className="jeo-headline uppercase tracking-[0.25em] text-[var(--jeo-gold)] text-lg sm:text-xl">
                    Triggered by {hyperController.name}
                  </p>
                )}
              </div>
            ) : (
              <MiniGameStage state={state} />
            )}
          </div>
        )}

        {showFullscreenClue && activeClue && (
          <div className="w-full max-w-[1800px] flex flex-col items-center gap-8">
            <div className="text-center">
              <p className="jeo-headline tracking-[0.4em] uppercase text-blue-200/80 text-xl sm:text-2xl">
                {state.activeCategoryName}
              </p>
              <p className={`jeo-value mt-3 ${isDailyDouble ? 'text-3xl sm:text-4xl' : 'text-5xl sm:text-7xl'}`}>
                {isDailyDouble ? 'DAILY DOUBLE' : activeClue.value.toLocaleString()}
              </p>
            </div>

            <div className="w-full p-10 sm:p-14 bg-[rgba(12,16,46,0.85)] border-2 border-[rgba(0,229,255,0.35)] rounded-3xl">
              {hideClueForWager ? (
                <div className="text-center space-y-4 py-6">
                  <p className="jeo-title text-5xl sm:text-7xl">DAILY DOUBLE!</p>
                  <p className="jeo-headline tracking-[0.3em] uppercase text-blue-200/80 text-xl sm:text-2xl">
                    {wageringPlayer ? `${wageringPlayer.name} is wagering...` : 'Wagering...'}
                  </p>
                </div>
              ) : isUnavailableClue(activeClue.question) ? (
                <div className="flex flex-col items-center gap-4 py-6">
                  <div
                    className="w-full max-w-xl h-48 sm:h-64 rounded-2xl border-4 border-dashed border-blue-300/40 bg-blue-950/40"
                    aria-label="Clue unavailable — original clue required an image or audio clip"
                  />
                  <p className="jeo-headline text-blue-200/60 text-lg sm:text-xl uppercase tracking-[0.25em]">
                    Clue unavailable — please skip
                  </p>
                </div>
              ) : (
                activeClue.question && (
                  <p className="text-3xl sm:text-5xl text-white text-center leading-tight font-semibold">
                    {activeClue.question}
                  </p>
                )
              )}
            </div>

            {isReveal && (
              <div className="text-center">
                <p className="jeo-headline tracking-[0.3em] uppercase text-blue-200/70 text-lg sm:text-xl">
                  Correct response
                </p>
                <p className="jeo-value text-4xl sm:text-6xl mt-3">{activeClue.answer}</p>
              </div>
            )}

            {!isReveal && buzzedPlayer && (
              <p className="jeo-headline tracking-[0.25em] uppercase text-2xl sm:text-3xl text-[var(--jeo-gold)]">
                {buzzedPlayer.name} buzzed in
              </p>
            )}
          </div>
        )}
      </div>

      {/* Scoreboard pinned at bottom, TV-sized. During the SPACE INVADERS
          ambush it hides — the panels have "become" the ships on screen. */}
      {!isInvaders && (
        <div className="bg-[rgba(8,10,30,0.92)] border-t-2 border-[rgba(0,229,255,0.4)] p-6">
          <div className="max-w-[1800px] mx-auto">
            <Scoreboard
              players={state.players}
              currentPlayerId={null}
              buzzedPlayerId={state.buzzedPlayerId}
              compact={false}
              isHost={false}
            />
          </div>
        </div>
      )}

      {/* SPACE INVADERS AMBUSH — full-screen battle over the dimmed board */}
      {isInvaders && <InvadersStage state={state} />}
    </div>
  );
}
