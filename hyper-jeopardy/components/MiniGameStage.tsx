'use client';
import { useEffect, useRef, useState } from 'react';
import type { GameState } from '@/lib/gameEngine';
import type { AnagramData, RapidData, LetterData, MiniGameData, MiniGameResultRow } from '@/lib/miniGames';
import { playMiniCelebrate } from '@/lib/audio';

// The shared-screen ("TV") view of a HYPER MODE mini-game — a wireframe pass,
// styled with the space theme. Renders the live board per game, then a results
// screen. Secrets never arrive here; only what all players may see.

function useCountdown(endsAt: number | null): number | null {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!endsAt) { setLeft(null); return; }
    const tick = () => setLeft(Math.max(0, Math.ceil((endsAt - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 200);
    return () => clearInterval(id);
  }, [endsAt]);
  return left;
}

function Timer({ endsAt }: { endsAt: number | null }) {
  const left = useCountdown(endsAt);
  if (left === null) return null;
  return (
    <span className={`jeo-value text-4xl sm:text-5xl ${left <= 5 ? 'text-red-400' : ''}`}>{left}s</span>
  );
}

// signed money format: +$2,000 / $0 / −$1,000
function fmtPts(n: number): string {
  if (n > 0) return `+$${n.toLocaleString()}`;
  if (n < 0) return `−$${Math.abs(n).toLocaleString()}`;
  return '$0';
}
function ptsClass(n: number): string {
  return n > 0 ? 'text-[var(--neon-lime)]' : n < 0 ? 'text-red-400' : 'text-blue-200/60';
}

export default function MiniGameStage({ state }: { state: GameState }) {
  const d = state.miniGameData as unknown as MiniGameData | null;
  const mg = state.activeMiniGame;
  if (!d || !mg) return null;

  return (
    <div className="hyper-card relative w-full max-w-5xl rounded-3xl px-8 sm:px-14 py-10 sm:py-12">
      <div className="flex items-center justify-between mb-8">
        <div className="text-left">
          <p className="jeo-headline uppercase tracking-[0.35em] text-[var(--neon-magenta)] text-sm sm:text-base">
            Hyper Mode · {mg.family}
          </p>
          <h2 className="hyper-title text-4xl sm:text-6xl">{mg.title}</h2>
        </div>
        {d.status !== 'results' && <Timer endsAt={d.endsAt} />}
      </div>

      {d.status === 'results' ? <Results state={state} d={d} />
        : d.status === 'intro' ? <IntroPanel d={d} />
        : d.key === 'anagram_race' ? <AnagramStage state={state} d={d} />
        : d.key === 'rapid_fire' ? <RapidStage state={state} d={d} />
        : <LetterStage state={state} d={d} />}
    </div>
  );
}

/* ── Intro (rules) — shown on all screens for ~5s before play ──────────────── */
function IntroPanel({ d }: { d: MiniGameData }) {
  return (
    <div className="text-center space-y-7 py-2">
      <p className="jeo-headline uppercase tracking-[0.4em] text-blue-200/70 text-lg">How to play</p>
      {d.key === 'anagram_race' && (
        <div className="space-y-6">
          <p className="text-blue-100/90 text-2xl sm:text-3xl leading-relaxed max-w-3xl mx-auto">
            Unscramble the word on your phone. First to solve wins big — but be slow and it&apos;ll cost you.
          </p>
          <div className="flex flex-wrap justify-center gap-3">
            {[['1st', 2 * d.value], ['2nd', d.value], ['3rd', 0], ['4th+', -d.value]].map(([label, pts]) => (
              <span key={label} className={`jeo-headline uppercase tracking-widest text-lg sm:text-2xl px-5 py-3 rounded-xl border border-white/10 bg-[rgba(6,8,26,0.5)] ${ptsClass(pts as number)}`}>
                {label} <span className="jeo-value">{fmtPts(pts as number)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {d.key === 'rapid_fire' && (
        <p className="text-blue-100/90 text-2xl sm:text-3xl leading-relaxed max-w-3xl mx-auto">
          One category — <span className="text-[var(--jeo-gold)]">{d.category}</span>. Answer as many as you can in 45 seconds.
          <br /><span className="text-[var(--neon-lime)]">+100</span> right · <span className="text-red-400">−50</span> wrong.
        </p>
      )}
      {d.key === 'letter_reveal' && (
        <p className="text-blue-100/90 text-2xl sm:text-3xl leading-relaxed max-w-3xl mx-auto">
          A hidden 5-letter word. Letters reveal one at a time — guess it early on your phone; the fewer letters shown, the bigger the score.
        </p>
      )}
    </div>
  );
}

/* ── Anagram Race ─────────────────────────────────────────────────────────── */
function AnagramStage({ state, d }: { state: GameState; d: AnagramData }) {
  const [flash, setFlash] = useState<{ name: string; pts: number } | null>(null);
  const seen = useRef(0);
  useEffect(() => {
    const n = d.solvedOrder.length;
    if (n > seen.current) {
      if (seen.current === 0) {
        // first solver → celebrate loudly on the shared screen
        const id = d.solvedOrder[0];
        const p = state.players.find(x => x.id === id);
        playMiniCelebrate();
        setFlash({ name: p?.name ?? 'Someone', pts: d.roundScores[id] ?? 0 });
        setTimeout(() => setFlash(null), 2200);
      }
      seen.current = n;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.solvedOrder.length]);

  return (
    <div className="space-y-6 text-center">
      {/* reserved slot so the celebration banner never overlaps the tiles or shifts layout */}
      <div className="min-h-[3.25rem] flex items-center justify-center">
        {flash && (
          <div className="mg-flash flex items-center gap-3 whitespace-nowrap px-6 py-2.5 rounded-2xl border border-[var(--neon-lime)] bg-[rgba(125,255,178,0.12)] shadow-[0_0_34px_rgba(125,255,178,0.35)]">
            <span className="jeo-headline uppercase tracking-widest text-[var(--neon-lime)] text-lg sm:text-2xl">🏆 {flash.name} solved first!</span>
            <span className="jeo-value text-xl sm:text-3xl text-[var(--neon-lime)]">{fmtPts(flash.pts)}</span>
          </div>
        )}
      </div>
      <div className="flex justify-center gap-2 sm:gap-3 flex-wrap">
        {d.scrambled.split('').map((ch, i) => (
          <span key={i} className="jeo-tile v3 mg-pop rounded-xl w-14 h-16 sm:w-20 sm:h-24 flex items-center justify-center jeo-value text-3xl sm:text-5xl" style={{ animationDelay: `${i * 55}ms` }}>
            {ch}
          </span>
        ))}
      </div>
      <p className="jeo-headline uppercase tracking-[0.3em] text-blue-200/70 text-sm">Unscramble it on your phone</p>
      <SolveChips state={state} solvedOrder={d.solvedOrder} gaveUp={d.gaveUp} />
    </div>
  );
}

/* ── Rapid Fire ───────────────────────────────────────────────────────────── */
function RapidStage({ state, d }: { state: GameState; d: RapidData }) {
  const rows = state.players
    .filter(p => p.connected || (d.progress[p.id] ?? 0) > 0)
    .map(p => ({ id: p.id, name: p.name, score: d.roundScores[p.id] ?? 0, done: d.progress[p.id] ?? 0, over: !!d.done[p.id], quit: !!d.gaveUp?.[p.id] && !d.done[p.id] }))
    .sort((a, b) => b.score - a.score);
  return (
    <div className="space-y-8">
      <p className="text-center jeo-headline uppercase tracking-[0.28em] text-[var(--jeo-gold)] text-xl sm:text-2xl">
        {d.category}
      </p>
      <div className="space-y-3 max-w-3xl mx-auto">
        {rows.map(r => (
          <div key={r.id} className="flex items-center gap-4">
            <span className="w-28 sm:w-40 text-right jeo-headline uppercase tracking-widest text-white truncate">{r.name}</span>
            <div className="flex-1 h-6 rounded-full bg-[rgba(6,8,26,0.7)] overflow-hidden border border-white/5">
              <div className="h-full bg-gradient-to-r from-[#00e5ff] to-[#7b5cff]" style={{ width: `${Math.min(100, (r.done / Math.max(1, d.total)) * 100)}%` }} />
            </div>
            <span className="w-24 jeo-value text-2xl">{r.score}</span>
            <span className="w-16 text-blue-200/50 text-xs jeo-headline uppercase">{r.over ? 'done' : r.quit ? 'gave up' : `${r.done}/${d.total}`}</span>
          </div>
        ))}
        {rows.length === 0 && <p className="text-center text-blue-200/60 jeo-headline tracking-widest uppercase">Waiting for answers…</p>}
      </div>
    </div>
  );
}

/* ── Letter Reveal ────────────────────────────────────────────────────────── */
function LetterStage({ state, d }: { state: GameState; d: LetterData }) {
  const solvedOrder = state.players.filter(p => d.solved[p.id]).map(p => p.id);
  return (
    <div className="space-y-8 text-center">
      <div className="flex justify-center gap-3 sm:gap-4">
        {d.letters.map((ch, i) => (
          <span key={i} className={`rounded-xl w-16 h-20 sm:w-24 sm:h-28 flex items-center justify-center jeo-value text-4xl sm:text-6xl border ${ch ? 'jeo-tile v1' : 'border-dashed border-[rgba(0,229,255,0.3)] bg-[rgba(6,8,26,0.5)]'}`}>
            {ch ?? ''}
          </span>
        ))}
      </div>
      <p className="jeo-headline uppercase tracking-[0.28em] text-blue-200/70 text-sm">
        {d.revealCount}/{d.wordLen} letters revealed · guess early for more points
      </p>
      <SolveChips state={state} solvedOrder={solvedOrder} gaveUp={d.gaveUp} />
    </div>
  );
}

/* ── shared bits ──────────────────────────────────────────────────────────── */
function SolveChips({ state, solvedOrder, gaveUp = {} }: { state: GameState; solvedOrder: string[]; gaveUp?: Record<string, boolean> }) {
  const active = state.players.filter(p => p.connected);
  if (!active.length) return null;
  return (
    <div className="flex justify-center gap-2 flex-wrap">
      {active.map(p => {
        const rank = solvedOrder.indexOf(p.id);
        const solved = rank >= 0;
        const quit = !solved && !!gaveUp[p.id];
        const cls = solved
          ? 'border-[var(--neon-lime)] text-[var(--neon-lime)] bg-[rgba(125,255,178,0.08)]'
          : quit
            ? 'border-red-400/40 text-red-300/70'
            : 'border-white/10 text-blue-200/60';
        return (
          <span key={p.id} className={`jeo-headline uppercase tracking-widest text-sm px-4 py-2 rounded-full border ${cls}`}>
            {p.name}{solved ? ` ✓ #${rank + 1}` : quit ? ' · gave up' : ''}
          </span>
        );
      })}
    </div>
  );
}

function Results({ state, d }: { state: GameState; d: MiniGameData }) {
  const rows: MiniGameResultRow[] = d.results ?? [];
  return (
    <div className="space-y-6 text-center">
      <p className="jeo-headline uppercase tracking-[0.4em] text-blue-200/80 text-lg">Round Over</p>
      {d.answerReveal && d.key !== 'rapid_fire' && (
        <p className="jeo-value text-4xl sm:text-6xl">{d.answerReveal}</p>
      )}
      <div className="max-w-2xl mx-auto space-y-2">
        {rows.map((r, i) => (
          <div key={r.playerId} className={`flex items-center gap-4 px-6 py-3 rounded-xl border ${i === 0 && r.points > 0 ? 'border-[var(--jeo-gold)] bg-[rgba(0,229,255,0.08)]' : 'border-white/8 bg-[rgba(6,8,26,0.5)]'}`}>
            <span className="w-8 jeo-headline text-blue-200/60">#{i + 1}</span>
            <span className="flex-1 text-left text-xl text-white jeo-headline uppercase tracking-wide truncate">{r.name}</span>
            <span className="text-blue-200/60 text-sm jeo-headline uppercase tracking-wider">{r.detail}</span>
            <span className={`w-28 text-right jeo-value text-2xl ${ptsClass(r.points)}`}>{fmtPts(r.points)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
