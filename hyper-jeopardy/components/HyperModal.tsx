'use client';
import { useEffect, useRef } from 'react';
import { GameState } from '@/lib/gameEngine';
import { playRandomLaser, preloadLasers } from '@/lib/audio';

interface Props {
  state: GameState;
  playerId: string | null;
  onEndHyper: () => void;
}

/**
 * Phone-side overlay for HYPER MODE. Shows the activation splash, then the
 * mini-game placeholder. The board controller (or host) can end the round.
 * Real mini-games will replace the placeholder body; the surrounding
 * activation + close flow stays.
 */
export default function HyperModal({ state, playerId, onEndHyper }: Props) {
  const { cluePhase, activeMiniGame } = state;
  const prevPhaseRef = useRef<string | null>(null);

  // Preload the laser clips once so activation plays instantly.
  useEffect(() => { preloadLasers(); }, []);

  // Random laser clip the moment HYPER MODE fires.
  useEffect(() => {
    if (prevPhaseRef.current !== 'hyper_intro' && cluePhase === 'hyper_intro') {
      playRandomLaser();
    }
    prevPhaseRef.current = cluePhase;
  }, [cluePhase]);

  if (cluePhase !== 'hyper_intro' && cluePhase !== 'hyper_active') return null;

  const me = state.players.find(p => p.id === playerId);
  const canEnd = !!me && (me.isHost || state.boardController === playerId);
  const controller = state.players.find(p => p.id === state.boardController);
  const trivia = state.miniGameTrivia?.[0] ?? null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-sm">
      {/* radial burst behind the card */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
        <div className="hyper-burst w-[70vmin] h-[70vmin] rounded-full" />
      </div>

      {cluePhase === 'hyper_intro' ? (
        <div className="relative text-center space-y-4">
          <p className="jeo-headline uppercase tracking-[0.4em] text-blue-200/80 text-sm">Hyper Mode</p>
          <h2 className="hyper-title text-5xl sm:text-7xl">ACTIVATED</h2>
          <p className="jeo-headline uppercase tracking-[0.25em] text-white/90 text-lg">
            {activeMiniGame ? activeMiniGame.title : 'Mini-game incoming'}
          </p>
        </div>
      ) : (
        <div className="hyper-card relative w-full max-w-md rounded-2xl overflow-hidden">
          <div className="px-6 py-3 flex items-center justify-between border-b border-[rgba(255,47,214,0.25)]">
            <span className="jeo-headline uppercase tracking-[0.3em] text-[var(--neon-magenta)] text-xs">Hyper Mode</span>
            {activeMiniGame && (
              <span className="jeo-headline uppercase tracking-[0.2em] text-blue-200/70 text-[11px]">
                {activeMiniGame.mode === 'single' ? 'Solo' : 'Table'} · {activeMiniGame.family}
              </span>
            )}
          </div>

          <div className="px-6 py-7 text-center space-y-4">
            <h3 className="hyper-title text-3xl sm:text-4xl">{activeMiniGame?.title ?? 'Mini-Game'}</h3>
            <p className="text-blue-100/85 text-sm sm:text-base leading-relaxed">
              {activeMiniGame?.blurb}
            </p>

            {/* Trivia plumbing preview — proves OpenTDB questions are wired in.
                Real mini-game UIs replace this block. */}
            {activeMiniGame && activeMiniGame.trivia !== false && (
              trivia ? (
                <div className="text-left rounded-lg border border-[rgba(0,229,255,0.2)] bg-[rgba(6,8,26,0.6)] p-3 space-y-2">
                  <p className="jeo-headline uppercase tracking-[0.18em] text-[10px] text-[var(--jeo-gold)]">
                    {trivia.category} · {trivia.difficulty}
                    {trivia.source === 'fallback' ? ' · offline' : ''}
                  </p>
                  <p className="text-white text-sm leading-snug">{trivia.question}</p>
                  <div className="grid grid-cols-2 gap-1.5">
                    {trivia.choices.map((c, i) => (
                      <span key={i} className="text-blue-100/80 text-xs rounded border border-white/10 px-2 py-1 truncate">{c}</span>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="jeo-headline uppercase tracking-[0.22em] text-[11px] text-blue-200/55 animate-pulse">
                  Fetching trivia…
                </p>
              )
            )}

            <p className="jeo-headline uppercase tracking-[0.22em] text-[11px] text-blue-200/55 pt-1">
              Placeholder — full mini-game coming soon
            </p>
          </div>

          <div className="px-6 pb-6">
            {canEnd ? (
              <button onClick={onEndHyper} className="jeo-btn-gold w-full py-3 rounded-lg text-base">
                End Hyper Round
              </button>
            ) : (
              <p className="text-center text-blue-200/70 jeo-headline tracking-wider uppercase text-sm">
                {controller ? `${controller.name} is running the round…` : 'Round in progress…'}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
