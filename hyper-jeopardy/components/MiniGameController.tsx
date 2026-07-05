'use client';
import { useEffect, useRef, useState } from 'react';
import type { GameState } from '@/lib/gameEngine';
import type { AnagramData, RapidData, LetterData, MemoryData, MiniGameData } from '@/lib/miniGames';
import { playSolve, playWrong, playMiniCelebrate, haptic } from '@/lib/audio';

export interface MGFeedback {
  correct?: boolean; points?: number; invalid?: boolean; already?: boolean; stale?: boolean;
  // Memory Matrix extras:
  levelUp?: boolean; finished?: boolean; out?: boolean;
}
type Action = { type: string; payload?: unknown };

interface Props {
  state: GameState;
  playerId: string | null;
  onAction: (a: Action) => Promise<MGFeedback>;
}

// Phone-side controls for the Phase 1 mini-games (wireframe). Emits
// mini_game_action and reads back per-player state from the broadcast.
// signed money format: +$2,000 / $0 / −$1,000
function fmtPts(n: number): string {
  if (n > 0) return `+$${n.toLocaleString()}`;
  if (n < 0) return `−$${Math.abs(n).toLocaleString()}`;
  return '$0';
}
function ptsClass(n: number): string {
  return n > 0 ? 'text-[var(--neon-lime)]' : n < 0 ? 'text-red-400' : 'text-blue-200/60';
}

export default function MiniGameController({ state, playerId, onAction }: Props) {
  const d = state.miniGameData as unknown as MiniGameData | null;
  const solvedCount = (d && 'solvedOrder' in d) ? d.solvedOrder.length : 0;
  const seenSolvesRef = useRef(0);

  // First-solve fanfare on EVERY phone (the shared screen already plays it):
  // the moment the first player solves/finishes, the whole room hears it.
  useEffect(() => {
    if (solvedCount > seenSolvesRef.current) {
      if (seenSolvesRef.current === 0) playMiniCelebrate();
      seenSolvesRef.current = solvedCount;
    }
    if (solvedCount === 0) seenSolvesRef.current = 0; // fresh round
  }, [solvedCount]);

  if (!d || !playerId) return null;

  if (d.status === 'results') return <ResultsView d={d} playerId={playerId} />;
  if (d.status === 'intro') return <IntroCtl d={d} />;
  if (d.key === 'anagram_race') return <AnagramCtl d={d} playerId={playerId} onAction={onAction} />;
  if (d.key === 'rapid_fire') return <RapidCtl d={d} playerId={playerId} onAction={onAction} />;
  if (d.key === 'letter_reveal') return <LetterCtl d={d} playerId={playerId} onAction={onAction} />;
  if (d.key === 'memory_match') return <MemoryCtl d={d} playerId={playerId} onAction={onAction} />;
  return null;
}

/* ── Intro (rules) — phone view while the rules show on all screens ─────────── */
function IntroCtl({ d }: { d: MiniGameData }) {
  return (
    <div className="text-center space-y-3 py-3">
      <p className="jeo-headline uppercase tracking-[0.25em] text-blue-200/70 text-xs">Get ready…</p>
      {d.key === 'anagram_race' && (
        <>
          <p className="text-blue-100/90 text-base leading-snug">Unscramble the word fastest. Type it here when play opens.</p>
          <div className="flex flex-wrap justify-center gap-1.5 text-xs jeo-headline uppercase tracking-wider">
            <span className="text-[var(--neon-lime)]">1st {fmtPts(2 * d.value)}</span>
            <span className="text-[var(--neon-lime)]">· 2nd {fmtPts(d.value)}</span>
            <span className="text-blue-200/60">· 3rd $0</span>
            <span className="text-red-400">· 4th+ {fmtPts(-d.value)}</span>
          </div>
        </>
      )}
      {d.key === 'rapid_fire' && (
        <>
          <p className="text-blue-100/90 text-base leading-snug">{d.category} · 10 questions · 30s. Most correct wins.</p>
          <div className="flex flex-wrap justify-center gap-1.5 text-xs jeo-headline uppercase tracking-wider">
            <span className="text-[var(--neon-lime)]">1st {fmtPts(2 * d.value)}</span>
            <span className="text-[var(--neon-lime)]">· 2nd {fmtPts(d.value)}</span>
            <span className="text-blue-200/60">· 3rd $0</span>
            <span className="text-red-400">· 4th {fmtPts(-d.value)}</span>
          </div>
        </>
      )}
      {d.key === 'memory_match' && (
        <>
          <p className="text-blue-100/90 text-base leading-snug">Tiles flash, then hide — tap them all from memory. Levels grow 4×4→5×5. 3 wrong guesses and you&apos;re out. Climb furthest!</p>
          <div className="flex flex-wrap justify-center gap-1.5 text-xs jeo-headline uppercase tracking-wider">
            <span className="text-[var(--neon-lime)]">1st {fmtPts(2 * d.value)}</span>
            <span className="text-[var(--neon-lime)]">· 2nd {fmtPts(d.value)}</span>
            <span className="text-blue-200/60">· 3rd $0</span>
            <span className="text-red-400">· 4th {fmtPts(-d.value)}</span>
          </div>
        </>
      )}
      {d.key === 'letter_reveal' && (
        <>
          <p className="text-blue-100/90 text-base leading-snug">Guess the hidden 5-letter word — first to solve wins big.</p>
          <div className="flex flex-wrap justify-center gap-1.5 text-xs jeo-headline uppercase tracking-wider">
            <span className="text-[var(--neon-lime)]">1st {fmtPts(2 * d.value)}</span>
            <span className="text-[var(--neon-lime)]">· 2nd {fmtPts(d.value)}</span>
            <span className="text-blue-200/60">· 3rd $0</span>
            <span className="text-red-400">· 4th {fmtPts(-d.value)}</span>
          </div>
        </>
      )}
    </div>
  );
}

/* ── Anagram Race ─────────────────────────────────────────────────────────── */
function AnagramCtl({ d, playerId, onAction }: { d: AnagramData; playerId: string; onAction: Props['onAction'] }) {
  const [guess, setGuess] = useState('');
  const [wrong, setWrong] = useState(false);
  const [pending, setPending] = useState(false);
  const solved = !!d.solved[playerId];

  async function submit() {
    if (pending || !guess.trim()) return;
    setPending(true);
    const fb = await onAction({ type: 'guess', payload: guess });
    setPending(false);
    if (fb.correct) { setGuess(''); playSolve(); haptic(35); }
    else { setWrong(true); playWrong(); haptic([15, 55, 15]); setTimeout(() => setWrong(false), 450); }
  }

  if (solved) return <Solved points={d.roundScores[playerId] ?? 0} sub={`#${d.solvedOrder.indexOf(playerId) + 1} to solve`} />;
  return (
    <div className="space-y-4">
      <div className="flex justify-center gap-1.5 flex-wrap">
        {d.scrambled.split('').map((c, i) => (
          <span key={i} className="jeo-tile v3 mg-pop rounded-md w-9 h-11 flex items-center justify-center jeo-value text-xl" style={{ animationDelay: `${i * 45}ms` }}>{c}</span>
        ))}
      </div>
      <input
        className={`jeo-input w-full px-4 py-3 rounded-lg text-xl text-center uppercase tracking-widest ${wrong ? 'border-red-500 mg-shake' : ''}`}
        placeholder="Your answer" value={guess} autoFocus autoCapitalize="characters"
        onChange={e => setGuess(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && submit()}
      />
      <button onClick={submit} disabled={pending} className="jeo-btn-gold w-full py-3 rounded-lg text-lg disabled:opacity-60">Solve</button>
      {wrong && <p className="text-center text-red-400 jeo-headline uppercase tracking-widest text-sm">Not it — keep trying</p>}
    </div>
  );
}

/* ── Rapid Fire ───────────────────────────────────────────────────────────── */
function RapidCtl({ d, playerId, onAction }: { d: RapidData; playerId: string; onAction: Props['onAction'] }) {
  const idx = d.progress[playerId] ?? 0;
  const [pending, setPending] = useState(false);
  const [flash, setFlash] = useState<'correct' | 'wrong' | null>(null);
  const [streak, setStreak] = useState(0);
  const q = d.questions[idx];

  async function pick(choice: string) {
    if (pending) return;
    setPending(true);
    const fb = await onAction({ type: 'answer', payload: { index: idx, choice } });
    haptic(fb.correct ? 25 : [10, 40, 10]);
    setStreak(s => (fb.correct ? s + 1 : 0));
    setFlash(fb.correct ? 'correct' : 'wrong');
    setTimeout(() => setFlash(null), 450);
    setPending(false);
  }

  if (d.done[playerId] || !q) {
    return (
      <div className="text-center space-y-2 py-4">
        <p className="hyper-title text-4xl">{d.correct[playerId] ?? 0} <span className="text-2xl text-[var(--neon-lime)]">✓</span></p>
        <p className="jeo-headline uppercase tracking-[0.25em] text-[var(--neon-lime)] text-sm">All questions done!</p>
        <p className="text-blue-200/60 text-sm">Standings settle when the round ends</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="jeo-headline uppercase tracking-widest text-[11px] text-blue-200/60">{d.category}</span>
        <span className="flex items-center gap-2">
          {streak >= 3 && <span className="text-sm mg-flash" aria-label={`${streak} in a row`}>🔥×{streak}</span>}
          <span className="jeo-value text-lg">{d.correct[playerId] ?? 0} <span className="text-sm text-[var(--neon-lime)]">✓</span></span>
        </span>
      </div>
      <p className="text-white text-lg leading-snug min-h-[3.5rem]">{q.question}</p>
      <div className="grid grid-cols-1 gap-2">
        {q.choices.map((c, i) => (
          <button key={i} disabled={pending}
            onClick={() => pick(c)}
            className="jeo-btn-gold py-3 rounded-lg text-base normal-case tracking-normal disabled:opacity-50">
            {c}
          </button>
        ))}
      </div>
      {flash && (
        <p className={`text-center jeo-headline uppercase tracking-widest text-sm ${flash === 'correct' ? 'text-[var(--neon-lime)]' : 'text-red-400'}`}>
          {flash === 'correct' ? '✓ Correct' : '✗ Wrong'}
        </p>
      )}
    </div>
  );
}

/* ── Letter Reveal ────────────────────────────────────────────────────────── */
function LetterCtl({ d, playerId, onAction }: { d: LetterData; playerId: string; onAction: Props['onAction'] }) {
  const [guess, setGuess] = useState('');
  const [cooldown, setCooldown] = useState(0);
  const [wrong, setWrong] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const solved = !!d.solved[playerId];

  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  function startCooldown() {
    setCooldown(1500);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setCooldown(c => {
      if (c <= 100) { if (timerRef.current) clearInterval(timerRef.current); return 0; }
      return c - 100;
    }), 100);
  }

  async function submit() {
    if (cooldown > 0 || guess.trim().length !== d.wordLen) return;
    const fb = await onAction({ type: 'guess', payload: guess });
    if (fb.correct) { setGuess(''); playSolve(); haptic(35); }
    else {
      playWrong(); haptic([15, 55, 15]);
      setWrong(true); setTimeout(() => setWrong(false), 450);
      startCooldown();
    }
  }

  if (solved) {
    const place = d.solvedOrder.indexOf(playerId) + 1;
    const hidden = d.wordLen - (d.solvedAtReveal[playerId] ?? 0);
    return <Solved points={d.roundScores[playerId] ?? 0} sub={`#${place} to solve · ${hidden} hidden`} />;
  }
  return (
    <div className="space-y-4">
      <div className="flex justify-center gap-1.5">
        {d.letters.map((c, i) => (
          <span key={i} className={`rounded-md w-9 h-11 flex items-center justify-center jeo-value text-xl border ${c ? 'jeo-tile v1' : 'border-dashed border-[rgba(0,229,255,0.3)]'}`}>{c ?? ''}</span>
        ))}
      </div>
      <input
        className={`jeo-input w-full px-4 py-3 rounded-lg text-xl text-center uppercase tracking-[0.4em] ${wrong ? 'border-red-500 mg-shake' : ''}`}
        placeholder={'•'.repeat(d.wordLen)} value={guess} autoFocus autoCapitalize="characters" maxLength={d.wordLen}
        onChange={e => setGuess(e.target.value.replace(/[^a-zA-Z]/g, ''))}
        onKeyDown={e => e.key === 'Enter' && submit()}
      />
      <button onClick={submit} disabled={cooldown > 0 || guess.length !== d.wordLen}
        className="jeo-btn-gold w-full py-3 rounded-lg text-lg disabled:opacity-40">
        {cooldown > 0 ? `Wait ${(cooldown / 1000).toFixed(1)}s` : 'Guess'}
      </button>
    </div>
  );
}

/* ── Memory Matrix ────────────────────────────────────────────────────────── */
// humanbenchmark.com/tests/memory style, run per player on their own phone:
// the pattern flashes ~1.6s, hides, then tap every lit cell from memory. Hits
// stay lit; 3 wrong guesses at ANY time across the run → out. Clear the final
// 5×5/8 rung to finish the ladder.
const MEMORY_FLASH_MS = 1600;

function MemoryCtl({ d, playerId, onAction }: { d: MemoryData; playerId: string; onAction: Props['onAction'] }) {
  const seq = d.patternSeq[playerId] ?? 0;
  const [showing, setShowing] = useState(true);   // pattern visible (memorize) vs hidden (recall)
  const [missAt, setMissAt] = useState<number | null>(null); // brief red flash on a missed cell
  const [banner, setBanner] = useState<string | null>(null); // "⚡ LEVEL UP" flash in the HUD
  const [strikeShake, setStrikeShake] = useState(false);     // hearts shake on a miss
  const pendingRef = useRef(false);
  const solved = !!d.solved[playerId];
  const out = !!d.out[playerId];

  // Every new pattern (new level) → flash it, then hide.
  useEffect(() => {
    if (seq === 0) return;
    setShowing(true);
    setMissAt(null);
    const t = setTimeout(() => setShowing(false), MEMORY_FLASH_MS);
    return () => clearTimeout(t);
  }, [seq]);

  const lvl = Math.min(d.level[playerId] ?? 0, d.levels.length - 1);
  const spec = d.levels[lvl];
  const cols = Math.round(Math.sqrt(spec.grid));
  const pattern = new Set(d.pattern[playerId] ?? []);
  const found = new Set(d.found[playerId] ?? []);
  const strikesLeft = Math.max(0, 3 - (d.wrongTotal[playerId] ?? 0));

  async function tap(i: number) {
    if (showing || pendingRef.current || found.has(i)) return;
    pendingRef.current = true;
    const fb = await onAction({ type: 'pick', payload: { cell: i } });
    pendingRef.current = false;
    if (fb.correct) {
      haptic(18);
      if (fb.finished) { playSolve(); haptic([40, 60, 80]); }
      else if (fb.levelUp) {
        playSolve(); haptic(45);
        setBanner('⚡ LEVEL UP');
        setTimeout(() => setBanner(null), 1100);
      }
    } else if (!fb.already) {
      playWrong(); haptic([15, 55, 15]);
      setMissAt(i); setTimeout(() => setMissAt(null), 500);
      setStrikeShake(true); setTimeout(() => setStrikeShake(false), 500);
    }
  }

  if (solved) {
    return (
      <div className="text-center space-y-2 py-4">
        <p className="hyper-title text-4xl">🏆 CLEARED</p>
        <p className="jeo-headline uppercase tracking-[0.25em] text-[var(--neon-lime)] text-sm">All {d.levels.length} levels!</p>
        <p className="text-blue-200/60 text-sm">Standings settle when the round ends</p>
      </div>
    );
  }
  if (out) {
    return (
      <div className="text-center space-y-2 py-4">
        <p className="hyper-title text-4xl opacity-80">OUT</p>
        <p className="jeo-headline uppercase tracking-[0.25em] text-red-300/90 text-sm">Cleared {d.level[playerId] ?? 0}/{d.levels.length} levels</p>
        <p className="text-blue-200/60 text-sm">Standings settle when the round ends</p>
      </div>
    );
  }

  return (
    <div className="space-y-3 text-center">
      <div className="flex items-center justify-between text-xs jeo-headline uppercase tracking-[0.2em]">
        <span className="text-[#ffd97a]">Level {lvl + 1}/{d.levels.length}</span>
        <span className={banner ? 'mg-flash text-[var(--neon-lime)]' : showing ? 'text-[#ffd97a]' : 'text-blue-200/60'}>
          {banner ?? (showing ? '👀 Memorize!' : `${found.size}/${spec.lit} found`)}
        </span>
        <span className={`text-[#ff7d92] ${strikeShake ? 'mg-shake inline-block' : ''}`}>
          {'♥'.repeat(strikesLeft)}<span className="text-white/15">{'♥'.repeat(3 - strikesLeft)}</span>
        </span>
      </div>
      <div className="flex justify-center">
        {/* key={seq}: every new pattern remounts the grid so the pop-in replays */}
        <div key={seq} className="inline-grid gap-1.5" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {Array.from({ length: spec.grid }, (_, i) => {
            const lit = (showing && pattern.has(i)) || found.has(i);
            const missed = missAt === i;
            return (
              <button
                key={i}
                onClick={() => tap(i)}
                disabled={showing}
                style={lit && showing ? { animationDelay: `${(i % cols) * 40 + Math.floor(i / cols) * 30}ms` } : undefined}
                className={`rounded-lg border transition-all duration-100 ${cols === 5 ? 'w-12 h-12' : 'w-14 h-14'} ${
                  missed ? 'mg-shake border-red-500 bg-red-500/40'
                  : lit ? 'mg-pop border-[#ffc43c] bg-[rgba(255,196,60,0.32)] shadow-[0_0_12px_rgba(255,196,60,0.5)]'
                  : 'border-[rgba(0,229,255,0.25)] bg-[rgba(6,8,26,0.55)] active:bg-[rgba(0,229,255,0.12)]'}`}
                aria-label={`cell ${i + 1}`}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ── shared bits ──────────────────────────────────────────────────────────── */
function Solved({ points, sub }: { points: number; sub: string }) {
  return (
    <div className="text-center space-y-2 py-4">
      <p className={`hyper-title text-4xl ${points < 0 ? 'opacity-90' : ''}`}>{fmtPts(points)}</p>
      <p className="jeo-headline uppercase tracking-[0.25em] text-[var(--neon-lime)] text-sm">Solved!</p>
      <p className="text-blue-200/60 text-sm">{sub}</p>
    </div>
  );
}

function ResultsView({ d, playerId }: { d: MiniGameData; playerId: string }) {
  const rows = d.results ?? [];
  return (
    <div className="space-y-3 py-1">
      <p className="text-center jeo-headline uppercase tracking-[0.3em] text-blue-200/70 text-sm">Round Over</p>
      {d.answerReveal && d.key !== 'rapid_fire' && (
        <p className="text-center text-blue-200/70 jeo-headline uppercase tracking-widest text-sm">
          Answer: <span className="text-[var(--jeo-gold)]">{d.answerReveal}</span>
        </p>
      )}
      {/* Everyone sees the whole table's scores for the round. */}
      <div className="space-y-1.5">
        {rows.map((r, i) => {
          const me = r.playerId === playerId;
          return (
            <div key={r.playerId} className={`flex items-center gap-2 px-3 py-2 rounded-lg border ${me ? 'border-[var(--jeo-gold)] bg-[rgba(0,229,255,0.10)]' : 'border-white/8 bg-[rgba(6,8,26,0.5)]'}`}>
              <span className="w-6 text-blue-200/60 jeo-headline text-sm">#{i + 1}</span>
              <span className="flex-1 min-w-0 text-left text-white truncate jeo-headline uppercase tracking-wide text-sm">
                {r.name}{me && <span className="text-[var(--jeo-gold)]"> (you)</span>}
              </span>
              <span className={`jeo-value text-lg ${ptsClass(r.points)}`}>{fmtPts(r.points)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
