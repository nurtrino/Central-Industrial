'use client';
import { useState } from 'react';
import Image from 'next/image';
import { GameState } from '@/lib/gameEngine';
import { unlockAudio } from '@/lib/audio';

interface Props {
  state: GameState;
  onJoin: (name: string) => void;
}

export default function Rejoin({ state, onJoin }: Props) {
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const disconnected = state.players.filter(p => !p.connected);

  const handleJoin = (n: string) => {
    if (submitting || !n.trim()) return;
    setSubmitting(true);
    unlockAudio();
    onJoin(n.trim());
    // re-enable shortly so the parent's error toast can let them retry
    setTimeout(() => setSubmitting(false), 1500);
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4">
      <Image
        src="/jeopardy-logo.png"
        alt="JEOPARDY!"
        width={3840}
        height={2160}
        priority
        className="jeo-logo w-[200px] sm:w-[320px] h-auto mb-8 select-none"
      />

      <div className="jeo-card rounded-3xl p-8 w-full max-w-md">
        <h2 className="jeo-headline text-2xl text-center text-[var(--jeo-gold)] uppercase tracking-wider mb-2">
          Game in Progress
        </h2>
        <p className="text-center text-blue-200/80 jeo-headline tracking-wide text-sm mb-5">
          Type your name to rejoin
        </p>

        {disconnected.length > 0 && (
          <div className="mb-5">
            <p className="jeo-headline uppercase tracking-widest text-[10px] text-blue-200/60 mb-2 text-center">
              Disconnected — tap to rejoin
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              {disconnected.map(p => (
                <button
                  key={p.id}
                  onClick={() => handleJoin(p.name)}
                  disabled={submitting}
                  className="jeo-headline tracking-wider uppercase text-xs px-3 py-1.5 rounded-md border border-[rgba(0,229,255,0.45)] text-[var(--jeo-gold)] bg-[rgba(0,229,255,0.05)] hover:bg-[rgba(0,229,255,0.18)] active:scale-95 transition disabled:opacity-50"
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-3">
          <input
            className="jeo-input w-full px-4 py-3 rounded-lg text-xl"
            placeholder="Your name"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleJoin(name)}
            disabled={submitting}
          />
          <button
            onClick={() => handleJoin(name)}
            disabled={submitting || !name.trim()}
            className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
          >
            {submitting ? 'Rejoining...' : 'Rejoin'}
          </button>
        </div>

        <p className="text-center text-blue-200/50 text-[10px] jeo-headline tracking-widest uppercase mt-4">
          Only previous players can rejoin
        </p>
      </div>
    </div>
  );
}
