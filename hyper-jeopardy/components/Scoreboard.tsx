'use client';
import { useState } from 'react';
import { Player } from '@/lib/gameEngine';

interface Props {
  players: Player[];
  currentPlayerId: string | null;
  buzzedPlayerId?: string | null;
  compact?: boolean;
  isHost?: boolean;
  onSetScore?: (playerId: string, score: number) => void;
}

interface CardProps {
  p: Player;
  isBuzzed: boolean;
  isYou: boolean;
  compact: boolean;
  isHost: boolean;
  onSetScore?: (playerId: string, score: number) => void;
}

function ScoreCard({ p, isBuzzed, isYou, compact, isHost, onSetScore }: CardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(p.score));

  const startEdit = () => {
    if (!isHost || !onSetScore) return;
    setDraft(String(p.score));
    setEditing(true);
  };

  const submit = () => {
    const n = parseInt(draft, 10);
    if (!Number.isNaN(n) && onSetScore) onSetScore(p.id, n);
    setEditing(false);
  };

  const padding = compact ? 'px-2 py-1.5' : 'px-6 py-3';
  const minWidth = compact ? 'flex-1 min-w-0 max-w-[120px]' : 'min-w-[140px]';
  const labelSize = compact ? 'text-[9px] tracking-[0.18em]' : 'text-xs tracking-[0.2em]';
  const valueSize = compact ? 'text-base leading-tight' : 'text-3xl mt-0.5';
  const rounded = compact ? 'rounded-md' : 'rounded-xl';

  if (editing) {
    return (
      <div className={`jeo-tile ${rounded} ${padding} text-center ${minWidth} ring-2 ring-[var(--jeo-gold)]`}>
        <div className={`jeo-headline uppercase ${labelSize} text-blue-200/80 truncate`}>{p.name}</div>
        <input
          autoFocus
          type="number"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') submit();
            else if (e.key === 'Escape') setEditing(false);
          }}
          className={`jeo-value ${valueSize} bg-transparent text-center w-full outline-none border-b border-[var(--jeo-gold)] mt-0.5`}
        />
        <div className="flex justify-center gap-1.5 mt-1">
          <button
            onClick={submit}
            className="jeo-headline uppercase tracking-wider text-[9px] px-2 py-0.5 rounded bg-[var(--jeo-gold)] text-blue-900"
          >
            Set
          </button>
          <button
            onClick={() => setEditing(false)}
            className="jeo-headline uppercase tracking-wider text-[9px] px-2 py-0.5 rounded border border-blue-300/40 text-blue-200/80"
          >
            ✕
          </button>
        </div>
      </div>
    );
  }

  const avatarSize = compact ? 18 : 28;
  const initials = p.name.trim().slice(0, 2).toUpperCase() || '?';
  return (
    <div
      className={`
        jeo-tile ${rounded} ${padding} text-center ${minWidth} relative transition-all
        ${isBuzzed ? (compact ? 'jeo-glow-gold ring-2 ring-[var(--jeo-gold)]' : 'jeo-glow-gold scale-105 ring-2 ring-[var(--jeo-gold)]') : ''}
        ${isYou && !isBuzzed ? (compact ? 'ring-1 ring-white/60' : 'ring-2 ring-white/70') : ''}
        ${!p.connected ? 'opacity-50' : ''}
      `}
    >
      <div className={`flex items-center justify-center gap-1.5 ${labelSize}`}>
        {p.avatar ? (
          <img
            src={p.avatar}
            alt=""
            className="rounded-full object-cover border border-[var(--jeo-gold)]/40 flex-shrink-0"
            style={{ width: avatarSize, height: avatarSize }}
          />
        ) : (
          <div
            className="rounded-full flex items-center justify-center font-bold text-white border border-[var(--jeo-gold)]/40 bg-[rgba(12,16,46,0.85)] flex-shrink-0"
            style={{ width: avatarSize, height: avatarSize, fontSize: avatarSize * 0.42 }}
          >{initials}</div>
        )}
        <div className={`jeo-headline uppercase text-blue-200/80 truncate`}>{p.name}</div>
      </div>
      <div className={`jeo-value ${valueSize} ${p.score < 0 ? '!text-red-400' : ''}`}>
        {p.score < 0 ? `-$${Math.abs(p.score).toLocaleString()}` : `$${p.score.toLocaleString()}`}
      </div>
      {isHost && onSetScore && (
        <button
          onClick={startEdit}
          aria-label={`Edit ${p.name}'s score`}
          className={`absolute ${compact ? 'top-0.5 right-0.5' : 'top-1.5 right-1.5'} jeo-headline uppercase tracking-widest text-[9px] px-1.5 py-0.5 rounded text-blue-200/70 hover:text-[var(--jeo-gold)] hover:bg-[rgba(0,229,255,0.1)] transition`}
        >
          ✎
        </button>
      )}
    </div>
  );
}

export default function Scoreboard({ players, currentPlayerId, buzzedPlayerId, compact = false, isHost = false, onSetScore }: Props) {
  const sorted = [...players].sort((a, b) => b.score - a.score);
  return (
    <div className={compact ? 'flex gap-1.5 justify-center' : 'flex gap-4 justify-center flex-wrap'}>
      {sorted.map(p => (
        <ScoreCard
          key={p.id}
          p={p}
          isBuzzed={p.id === buzzedPlayerId}
          isYou={p.id === currentPlayerId}
          compact={compact}
          isHost={isHost}
          onSetScore={onSetScore}
        />
      ))}
    </div>
  );
}
