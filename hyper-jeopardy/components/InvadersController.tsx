'use client';
import { useEffect, useRef, useState } from 'react';
import type { GameState } from '@/lib/gameEngine';
import { getSocket } from '@/lib/socket-client';
import type { InvSnapshot } from '@/lib/invaders';
import { haptic } from '@/lib/audio';
import { applyInvaderTickSfx } from '@/components/invadersSfx';

// SPACE INVADERS AMBUSH — phone battle station. Big ◀ ▶ hold-to-move pads and
// a FIRE button. Movement uses press/release semantics (pointerdown → move,
// pointerup → stop) so the server glides the ship between ticks; fire plays a
// local pew instantly for responsiveness while the server confirms the shot.

type CtlAction = 'L' | 'R' | 'S' | 'F';

export default function InvadersController({
  state, playerId, onCtl,
}: {
  state: GameState;
  playerId: string | null;
  onCtl: (a: CtlAction) => void;
}) {
  const [snap, setSnap] = useState<InvSnapshot | null>(null);
  const holdRef = useRef<CtlAction | null>(null);
  const prevTickRef = useRef<InvSnapshot | null>(null);
  const endSoundRef = useRef(false);
  const roster = state.invaders?.roster ?? [];
  const myName = state.players.find(p => p.id === playerId)?.name;
  let myIdx = roster.findIndex(r => r.id === playerId);
  if (myIdx < 0 && myName) myIdx = roster.findIndex(r => r.name === myName);
  const me = myIdx >= 0 && snap ? snap.sh[myIdx] : null;
  const color = myIdx >= 0 ? roster[myIdx]?.color : '#00e5ff';
  const status = snap?.st ?? state.invaders?.status ?? 'intro';

  useEffect(() => {
    const s = getSocket();
    const onTick = (t: InvSnapshot) => {
      // full battle soundtrack on the phone too — march, shots, booms, stingers
      applyInvaderTickSfx(prevTickRef.current, t, endSoundRef);
      prevTickRef.current = t;
      setSnap(t);
    };
    s.on('invaders', onTick);
    return () => { s.off('invaders', onTick); };
  }, []);

  // Safety: release movement if the overlay unmounts mid-hold.
  useEffect(() => () => { if (holdRef.current) onCtl('S'); }, [onCtl]);

  const press = (a: 'L' | 'R') => { holdRef.current = a; haptic(10); onCtl(a); };
  const release = () => { if (holdRef.current) { holdRef.current = null; onCtl('S'); } };
  // Haptic is instant; the pew itself arrives via the shared tick soundtrack
  // (~a tick later), so every device hears the identical shot at the same time.
  const fire = () => { haptic(18); onCtl('F'); };

  const alive = me ? me[2] === 1 : true;
  const lives = me ? me[1] : 1;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[rgba(2,3,14,0.96)] select-none" style={{ touchAction: 'none' }}>
      {/* status strip */}
      <div className="px-5 pt-5 pb-3 text-center space-y-1">
        <p className="jeo-headline uppercase tracking-[0.35em] text-[#ff5c8a] text-xs">⚠ Space Invaders Ambush ⚠</p>
        {status === 'intro' && (
          <p className="jeo-headline uppercase tracking-[0.2em] text-blue-100/90 text-lg">Battle stations…</p>
        )}
        {status === 'playing' && alive && (
          <p className="jeo-headline uppercase tracking-[0.2em] text-lg" style={{ color }}>
            {myName ?? 'Your ship'} · <span className="text-[#ff7d92]">{'♥'.repeat(Math.max(0, lives))}<span className="text-white/15">{'♥'.repeat(Math.max(0, 1 - lives))}</span></span>
          </p>
        )}
        {status === 'playing' && !alive && (
          <p className="jeo-headline uppercase tracking-[0.2em] text-red-300/90 text-lg">💥 Ship destroyed — root for the fleet!</p>
        )}
        {status === 'won' && <p className="jeo-headline uppercase tracking-[0.2em] text-[var(--neon-lime)] text-lg">🏆 Wave cleared!</p>}
        {status === 'lost' && <p className="jeo-headline uppercase tracking-[0.2em] text-red-300/90 text-lg">💀 The fleet has fallen</p>}
        {status === 'playing' && snap && (
          <p className="text-blue-200/50 text-[11px] jeo-headline uppercase tracking-[0.25em]">{snap.n} invaders remaining</p>
        )}
      </div>

      {/* battle pad */}
      <div className="flex-1 flex flex-col justify-end px-4 pb-8 gap-4">
        {status === 'playing' && alive ? (
          <>
            <button
              onPointerDown={e => { e.preventDefault(); fire(); }}
              onContextMenu={e => e.preventDefault()}
              className="w-full py-8 rounded-2xl jeo-headline uppercase tracking-[0.3em] text-2xl text-white border-2 active:scale-[0.98] transition"
              style={{ borderColor: '#ff5c8a', background: 'rgba(255,92,138,0.18)', boxShadow: '0 0 30px rgba(255,92,138,0.35)' }}
            >
              🔥 Fire
            </button>
            <div className="grid grid-cols-2 gap-4">
              {(['L', 'R'] as const).map(dir => (
                <button
                  key={dir}
                  onPointerDown={e => { e.preventDefault(); press(dir); }}
                  onPointerUp={release}
                  onPointerLeave={release}
                  onPointerCancel={release}
                  onContextMenu={e => e.preventDefault()}
                  className="py-10 rounded-2xl text-4xl border-2 border-[rgba(0,229,255,0.5)] bg-[rgba(0,229,255,0.10)] text-[var(--jeo-gold)] active:bg-[rgba(0,229,255,0.28)] active:scale-[0.98] transition"
                >
                  {dir === 'L' ? '◀' : '▶'}
                </button>
              ))}
            </div>
            <p className="text-center text-blue-200/40 text-[10px] jeo-headline uppercase tracking-[0.25em]">
              Hold to move · watch the big screen
            </p>
          </>
        ) : (
          <div className="text-center pb-10">
            <p className="text-blue-200/60 jeo-headline uppercase tracking-[0.25em] text-sm">
              {status === 'intro' ? 'Controls unlock in a moment…' : 'Watch the big screen'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
