'use client';
import { useEffect, useRef, useState } from 'react';
import type { GameState } from '@/lib/gameEngine';
import type { AnagramData, RapidData, LetterData, MemoryData, MiniGameData, MiniGameResultRow } from '@/lib/miniGames';
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
    <span className={`jeo-value text-4xl sm:text-5xl ${left <= 5 ? 'text-red-400 mg-urgent' : left <= 10 ? 'text-[#ffcf5c]' : ''}`}>{left}s</span>
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
        : d.key === 'memory_match' ? <MemoryStage state={state} d={d} />
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
        <div className="space-y-6">
          <p className="text-blue-100/90 text-2xl sm:text-3xl leading-relaxed max-w-3xl mx-auto">
            One category — <span className="text-[var(--jeo-gold)]">{d.category}</span>. 10 questions, 30 seconds — answer as many as you can. Most correct wins.
          </p>
          <div className="flex flex-wrap justify-center gap-3">
            {[['1st', 2 * d.value], ['2nd', d.value], ['3rd', 0], ['4th', -d.value]].map(([label, pts]) => (
              <span key={label} className={`jeo-headline uppercase tracking-widest text-lg sm:text-2xl px-5 py-3 rounded-xl border border-white/10 bg-[rgba(6,8,26,0.5)] ${ptsClass(pts as number)}`}>
                {label} <span className="jeo-value">{fmtPts(pts as number)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {d.key === 'memory_match' && (
        <div className="space-y-6">
          <p className="text-blue-100/90 text-2xl sm:text-3xl leading-relaxed max-w-3xl mx-auto">
            Tiles flash on your phone — find them all from memory. Clear a level and it grows: 4×4 with 5 lit up to 5×5 with 8. Three wrong guesses and you&apos;re out. Climb furthest, fastest.
          </p>
          <div className="flex flex-wrap justify-center gap-3">
            {[['1st', 2 * d.value], ['2nd', d.value], ['3rd', 0], ['4th', -d.value]].map(([label, pts]) => (
              <span key={label} className={`jeo-headline uppercase tracking-widest text-lg sm:text-2xl px-5 py-3 rounded-xl border border-white/10 bg-[rgba(6,8,26,0.5)] ${ptsClass(pts as number)}`}>
                {label} <span className="jeo-value">{fmtPts(pts as number)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {d.key === 'letter_reveal' && (
        <div className="space-y-6">
          <p className="text-blue-100/90 text-2xl sm:text-3xl leading-relaxed max-w-3xl mx-auto">
            A hidden 5-letter word — letters reveal one at a time. First to solve on your phone wins big.
          </p>
          <div className="flex flex-wrap justify-center gap-3">
            {[['1st', 2 * d.value], ['2nd', d.value], ['3rd', 0], ['4th', -d.value]].map(([label, pts]) => (
              <span key={label} className={`jeo-headline uppercase tracking-widest text-lg sm:text-2xl px-5 py-3 rounded-xl border border-white/10 bg-[rgba(6,8,26,0.5)] ${ptsClass(pts as number)}`}>
                {label} <span className="jeo-value">{fmtPts(pts as number)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Anagram Race ─────────────────────────────────────────────────────────── */
/* First-solve celebration — shared by the word games. Watches solvedOrder and,
   the moment the FIRST player solves, fires the fanfare + a banner in a
   reserved slot (so tiles never shift or get overlapped). */
function FirstSolveBanner({ state, solvedOrder, roundScores, fallbackPts, label = 'solved first!' }: {
  state: GameState; solvedOrder: string[]; roundScores: Record<string, number>;
  fallbackPts?: number;  // shown when scores settle at round end (e.g. Memory Matrix)
  label?: string;
}) {
  const [flash, setFlash] = useState<{ name: string; pts: number } | null>(null);
  const seen = useRef(0);
  useEffect(() => {
    const n = solvedOrder.length;
    if (n > seen.current) {
      if (seen.current === 0) {
        const id = solvedOrder[0];
        const p = state.players.find(x => x.id === id);
        playMiniCelebrate();
        setFlash({ name: p?.name ?? 'Someone', pts: roundScores[id] ?? fallbackPts ?? 0 });
        setTimeout(() => setFlash(null), 2200);
      }
      seen.current = n;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [solvedOrder.length]);

  return (
    <div className="min-h-[3.25rem] flex items-center justify-center">
      {flash && (
        <div className="mg-flash flex items-center gap-3 whitespace-nowrap px-6 py-2.5 rounded-2xl border border-[var(--neon-lime)] bg-[rgba(125,255,178,0.12)] shadow-[0_0_34px_rgba(125,255,178,0.35)]">
          <span className="jeo-headline uppercase tracking-widest text-[var(--neon-lime)] text-lg sm:text-2xl">🏆 {flash.name} {label}</span>
          <span className="jeo-value text-xl sm:text-3xl text-[var(--neon-lime)]">{fmtPts(flash.pts)}</span>
        </div>
      )}
    </div>
  );
}

function AnagramStage({ state, d }: { state: GameState; d: AnagramData }) {
  return (
    <div className="space-y-6 text-center">
      <FirstSolveBanner state={state} solvedOrder={d.solvedOrder} roundScores={d.roundScores} />
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
    .map(p => ({ id: p.id, name: p.name, correct: d.correct[p.id] ?? 0, done: d.progress[p.id] ?? 0, over: !!d.done[p.id], quit: !!d.gaveUp?.[p.id] && !d.done[p.id] }))
    .sort((a, b) => b.correct - a.correct);
  return (
    <div className="space-y-8">
      <p className="text-center jeo-headline uppercase tracking-[0.28em] text-[var(--jeo-gold)] text-xl sm:text-2xl">
        {d.category} <span className="text-blue-200/50 text-base">· most correct wins</span>
      </p>
      <div className="space-y-3 max-w-3xl mx-auto">
        {rows.map((r, i) => (
          <div key={r.id} className="flex items-center gap-4">
            <span className="w-28 sm:w-40 text-right jeo-headline uppercase tracking-widest text-white truncate">
              {i === 0 && r.correct > 0 ? '👑 ' : ''}{r.name}
            </span>
            <div className="flex-1 h-6 rounded-full bg-[rgba(6,8,26,0.7)] overflow-hidden border border-white/5">
              <div className="h-full bg-gradient-to-r from-[#00e5ff] to-[#7b5cff]" style={{ width: `${Math.min(100, (r.done / Math.max(1, d.total)) * 100)}%` }} />
            </div>
            <span className="w-24 jeo-value text-2xl">{r.correct}<span className="text-base text-[var(--neon-lime)] ml-1">✓</span></span>
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
  return (
    <div className="space-y-6 text-center">
      <FirstSolveBanner state={state} solvedOrder={d.solvedOrder} roundScores={d.roundScores} />
      <div className="flex justify-center gap-3 sm:gap-4">
        {d.letters.map((ch, i) => (
          <span key={i} className={`rounded-xl w-16 h-20 sm:w-24 sm:h-28 flex items-center justify-center jeo-value text-4xl sm:text-6xl border ${ch ? 'jeo-tile v1 mg-pop' : 'border-dashed border-[rgba(0,229,255,0.3)] bg-[rgba(6,8,26,0.5)]'}`}>
            {ch ?? ''}
          </span>
        ))}
      </div>
      <p className="jeo-headline uppercase tracking-[0.28em] text-blue-200/70 text-sm">
        {d.revealCount}/{d.wordLen} letters revealed · solve first for more points
      </p>
      <SolveChips state={state} solvedOrder={d.solvedOrder} gaveUp={d.gaveUp} />
    </div>
  );
}

/* ── Memory Matrix ────────────────────────────────────────────────────────── */
// Each player's pattern flashes on their OWN phone, so the shared screen is a
// live race dashboard: rung climbed, tiles found this level, lives left.
function MemoryStage({ state, d }: { state: GameState; d: MemoryData }) {
  const totalLevels = d.levels.length;
  const rows = state.players
    .filter(p => p.connected || (d.level[p.id] ?? 0) > 0)
    .map(p => {
      const cleared = d.solved[p.id] ? totalLevels : (d.level[p.id] ?? 0);
      const spec = d.levels[Math.min(d.level[p.id] ?? 0, totalLevels - 1)];
      return {
        id: p.id, name: p.name, cleared,
        found: (d.found[p.id] ?? []).length, lit: spec.lit,
        strikesLeft: Math.max(0, 3 - (d.wrongTotal[p.id] ?? 0)),
        done: !!d.solved[p.id], out: !!d.out[p.id],
        quit: !!d.gaveUp?.[p.id] && !d.solved[p.id],
      };
    })
    .sort((a, b) => b.cleared - a.cleared);
  return (
    <div className="space-y-6">
      <FirstSolveBanner state={state} solvedOrder={d.solvedOrder} roundScores={d.roundScores} fallbackPts={2 * d.value} label="cleared the ladder first!" />
      <p className="text-center jeo-headline uppercase tracking-[0.28em] text-[#ffd97a] text-lg sm:text-xl">
        4×4 · 5 lit → 5×5 · 8 lit <span className="text-blue-200/50 text-base">· watch your phone</span>
      </p>
      <div className="space-y-3 max-w-3xl mx-auto">
        {rows.map(r => (
          <div key={r.id} className="flex items-center gap-4">
            <span className="w-28 sm:w-40 text-right jeo-headline uppercase tracking-widest text-white truncate">
              {r.done ? '👑 ' : ''}{r.name}
            </span>
            <div className="flex-1 h-6 rounded-full bg-[rgba(6,8,26,0.7)] overflow-hidden border border-white/5">
              <div className="h-full bg-gradient-to-r from-[#ffc43c] to-[#ff7ad9] transition-all duration-300" style={{ width: `${Math.min(100, (r.cleared / totalLevels) * 100)}%` }} />
            </div>
            <span key={r.cleared} className="w-24 jeo-value text-xl mg-pop">{r.done ? 'DONE' : `L${Math.min(r.cleared + 1, totalLevels)}·${r.found}/${r.lit}`}</span>
            <span className="w-20 text-left text-sm">
              {r.out ? <span className="text-red-300/80 jeo-headline uppercase text-xs">out</span>
                : r.quit ? <span className="text-red-300/60 jeo-headline uppercase text-xs">gave up</span>
                : <span className="text-[#ff7d92]">{'♥'.repeat(r.strikesLeft)}<span className="text-white/15">{'♥'.repeat(3 - r.strikesLeft)}</span></span>}
            </span>
          </div>
        ))}
        {rows.length === 0 && <p className="text-center text-blue-200/60 jeo-headline tracking-widest uppercase">Waiting for players…</p>}
      </div>
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
