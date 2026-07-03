'use client';
import { useEffect, useRef, useState } from 'react';
import { Socket } from 'socket.io-client';
import { getSocket } from '@/lib/socket-client';
import { GameState } from '@/lib/gameEngine';
import { isUnavailableClue } from '@/lib/clueSentinel';
import { unlockAudio, preloadLasers, playRandomLaser } from '@/lib/audio';
import Board from '@/components/Board';
import Scoreboard from '@/components/Scoreboard';
import MiniGameStage from '@/components/MiniGameStage';

// Read-only "TV" view. Connects to the same socket room but never emits
// `join`, so the server doesn't track it as a player. Renders the board
// + scoreboard, swaps to a fullscreen clue when one is active, and
// reveals the answer when the engine transitions to the reveal phase.
export default function Display() {
  const [, setSocket] = useState<Socket | null>(null);
  const [state, setState] = useState<GameState | null>(null);
  const [connected, setConnected] = useState(false);
  const prevCluePhaseRef = useRef<string | null>(null);

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

  // The shared screen is the stage — play the laser cue here on HYPER MODE
  // activation. Browsers block audio until a gesture, so unlock on the first
  // click/key and preload the clips so they fire instantly.
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

  useEffect(() => {
    const phase = state?.cluePhase ?? null;
    if (prevCluePhaseRef.current !== 'hyper_intro' && phase === 'hyper_intro') {
      playRandomLaser();
    }
    prevCluePhaseRef.current = phase;
  }, [state?.cluePhase]);

  if (!connected || !state) {
    return (
      <div className="min-h-screen flex items-center justify-center">
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
      <div className="min-h-screen flex flex-col items-center justify-center px-8 gap-8">
        <h1 className="jeo-title text-7xl sm:text-9xl">JEOPARDY!</h1>
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
      <div className="min-h-screen flex flex-col items-center justify-center px-8 gap-10">
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
                {p.score < 0 ? `-$${Math.abs(p.score).toLocaleString()}` : `$${p.score.toLocaleString()}`}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const activeClue = state.activeClue;
  const isHyper = state.cluePhase === 'hyper_intro' || state.cluePhase === 'hyper_active';
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
    state.phase === 'jeopardy' ? 'Jeopardy!'
    : state.phase === 'double_jeopardy' ? 'Double Jeopardy!'
    : 'Final Jeopardy!';

  return (
    <div className="min-h-screen flex flex-col">
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
                {isDailyDouble ? 'DAILY DOUBLE' : `$${activeClue.value.toLocaleString()}`}
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

      {/* Scoreboard pinned at bottom, TV-sized */}
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
    </div>
  );
}
