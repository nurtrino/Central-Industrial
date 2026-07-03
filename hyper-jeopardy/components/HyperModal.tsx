'use client';
import { useEffect, useRef } from 'react';
import { GameState } from '@/lib/gameEngine';
import { playRandomLaser, preloadLasers } from '@/lib/audio';
import MiniGameController, { type MGFeedback } from '@/components/MiniGameController';

interface Props {
  state: GameState;
  playerId: string | null;
  onEndHyper: () => void;
  onMiniGameAction: (a: { type: string; payload?: unknown }) => Promise<MGFeedback>;
}

/**
 * Phone-side overlay for HYPER MODE. Shows the activation splash, then the
 * live mini-game controls (MiniGameController). The board controller (or host)
 * can end the round early.
 */
export default function HyperModal({ state, playerId, onEndHyper, onMiniGameAction }: Props) {
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

          <div className="px-5 py-6">
            <MiniGameController state={state} playerId={playerId} onAction={onMiniGameAction} />
          </div>

          <div className="px-6 pb-5">
            {canEnd && (
              <button onClick={onEndHyper} className="w-full py-2 rounded-lg text-xs jeo-headline uppercase tracking-[0.2em] text-blue-200/60 border border-white/10 hover:text-[var(--neon-magenta)] hover:border-[var(--neon-magenta)] transition">
                End Hyper Round
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
