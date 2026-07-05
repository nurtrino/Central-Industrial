'use client';
import { useEffect, useRef } from 'react';
import { GameState } from '@/lib/gameEngine';
import { playHyperStart, preloadLasers } from '@/lib/audio';
import MiniGameController, { type MGFeedback } from '@/components/MiniGameController';
import HyperFlair from '@/components/HyperFlair';

interface Props {
  state: GameState;
  playerId: string | null;
  onMiniGameAction: (a: { type: string; payload?: unknown }) => Promise<MGFeedback>;
}

/**
 * Phone-side overlay for HYPER MODE. Shows the activation splash, then the
 * live mini-game controls (MiniGameController). Every round is time-limited;
 * it ends when the clock runs out or everyone has finished — no quitting.
 */
export default function HyperModal({ state, playerId, onMiniGameAction }: Props) {
  const { cluePhase, activeMiniGame } = state;
  const prevPhaseRef = useRef<string | null>(null);

  // Preload the hyper-start clips once so activation fires instantly.
  useEffect(() => { preloadLasers(); }, []);

  // HYPER MODE activation: play the server-seeded clip — the SAME sound as
  // every other phone and the shared screen, in sync.
  useEffect(() => {
    if (prevPhaseRef.current !== 'hyper_intro' && cluePhase === 'hyper_intro') {
      playHyperStart(state.hyperSeed ?? 0);
    }
    prevPhaseRef.current = cluePhase;
  }, [cluePhase, state.hyperSeed]);

  if (cluePhase !== 'hyper_intro' && cluePhase !== 'hyper_active') return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-sm">
      {/* radial burst + ambient flair behind the card */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
        <div className="hyper-burst w-[70vmin] h-[70vmin] rounded-full" />
      </div>
      <HyperFlair density="lite" />

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

          <div className="px-5 py-6 pb-7">
            <MiniGameController state={state} playerId={playerId} onAction={onMiniGameAction} />
          </div>
        </div>
      )}
    </div>
  );
}
