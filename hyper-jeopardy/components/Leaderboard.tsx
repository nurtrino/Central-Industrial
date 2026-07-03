'use client';

// Accounts double as the leaderboard — same id/name/avatar, plus a win count.
// We accept the shared Account shape so this component doesn't duplicate the
// type and stays in sync if the server's schema evolves.
export interface LeaderboardAccount {
  id: string;
  name: string;
  avatar?: string;
  wins: number;
}

interface Props {
  entries: LeaderboardAccount[];
  onClose: () => void;
}

const MEDAL: Record<number, string> = { 0: '🥇', 1: '🥈', 2: '🥉' };

export default function Leaderboard({ entries, onClose }: Props) {
  const sorted = [...entries].sort((a, b) => b.wins - a.wins || a.name.localeCompare(b.name));
  // Rank with ties: equal point totals share a rank number.
  const ranked = sorted.map((e, idx) => {
    let rank = idx;
    for (let i = idx - 1; i >= 0; i--) {
      if (sorted[i].wins === e.wins) { rank = i; break; }
    }
    return { ...e, rank };
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4 bg-black/60" onClick={onClose}>
      <div
        className="jeo-card rounded-3xl p-6 w-full max-w-md relative"
        onClick={e => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          aria-label="Close leaderboard"
          className="absolute top-3 right-3 text-blue-200/60 hover:text-[var(--jeo-gold)] text-xl"
        >
          ✕
        </button>
        <h2 className="jeo-headline text-2xl text-center text-[var(--jeo-gold)] uppercase tracking-wider mb-1">
          Leaderboard
        </h2>
        <p className="text-center text-xs text-blue-200/60 jeo-headline tracking-[0.2em] uppercase mb-5">
          +1 each game you win
        </p>

        {ranked.length === 0 ? (
          <p className="text-center text-blue-200/60 py-8">No accounts yet.</p>
        ) : (
          <ol className="space-y-2">
            {ranked.map((e) => (
              <li
                key={e.id}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 border ${
                  e.rank === 0
                    ? 'bg-[rgba(0,229,255,0.10)] border-[var(--jeo-gold)]/40'
                    : 'bg-[rgba(12,16,46,0.6)] border-[rgba(0,229,255,0.15)]'
                }`}
              >
                <span
                  className="jeo-headline text-sm font-bold w-6 text-center"
                  style={{ color: e.rank === 0 ? 'var(--jeo-gold)' : 'rgba(255,255,255,0.7)' }}
                >
                  {MEDAL[e.rank] ?? `${e.rank + 1}.`}
                </span>
                <Avatar account={e} size={32} />
                <span className="font-semibold text-white flex-1 truncate">{e.name}</span>
                <span className="jeo-value text-xl">
                  {e.wins}
                  <span className="jeo-headline text-[10px] tracking-widest uppercase text-blue-200/60 ml-1">
                    {e.wins === 1 ? 'win' : 'wins'}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function Avatar({ account, size }: { account: { name: string; avatar?: string }; size: number }) {
  if (account.avatar) {
    return (
      <img
        src={account.avatar}
        alt=""
        className="rounded-full object-cover border border-[var(--jeo-gold)]/40 flex-shrink-0"
        style={{ width: size, height: size }}
      />
    );
  }
  const initials = account.name.trim().slice(0, 2).toUpperCase() || '?';
  return (
    <div
      className="rounded-full flex items-center justify-center font-bold text-white border border-[var(--jeo-gold)]/40 bg-[rgba(12,16,46,0.85)] flex-shrink-0"
      style={{ width: size, height: size, fontSize: size * 0.4 }}
    >{initials}</div>
  );
}
