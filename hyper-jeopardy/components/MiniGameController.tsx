'use client';
import { useEffect, useRef, useState } from 'react';
import type { GameState } from '@/lib/gameEngine';
import type { AnagramData, RapidData, LetterData, MiniGameData } from '@/lib/miniGames';

export interface MGFeedback { correct?: boolean; points?: number; invalid?: boolean; already?: boolean; stale?: boolean }
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

export default function MiniGameController({ state, playerId, onAction }: Props) {
  const d = state.miniGameData as unknown as MiniGameData | null;
  if (!d || !playerId) return null;

  if (d.status === 'results') return <ResultsView d={d} playerId={playerId} />;
  if (d.status === 'intro') return <IntroCtl d={d} />;
  if (d.key === 'anagram_race') return <AnagramCtl d={d} playerId={playerId} onAction={onAction} />;
  if (d.key === 'rapid_fire') return <RapidCtl d={d} playerId={playerId} onAction={onAction} />;
  if (d.key === 'letter_reveal') return <LetterCtl d={d} playerId={playerId} onAction={onAction} />;
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
        <p className="text-blue-100/90 text-base leading-snug">{d.category} · 45s. Tap answers fast. <span className="text-[var(--neon-lime)]">+100</span> / <span className="text-red-400">−50</span>.</p>
      )}
      {d.key === 'letter_reveal' && (
        <p className="text-blue-100/90 text-base leading-snug">Guess the hidden 5-letter word — the fewer letters shown when you solve, the more points.</p>
      )}
    </div>
  );
}

/* ── Anagram Race ─────────────────────────────────────────────────────────── */
function AnagramCtl({ d, playerId, onAction }: { d: AnagramData; playerId: string; onAction: Props['onAction'] }) {
  const [guess, setGuess] = useState('');
  const [wrong, setWrong] = useState(false);
  const solved = !!d.solved[playerId];

  async function submit() {
    if (!guess.trim()) return;
    const fb = await onAction({ type: 'guess', payload: guess });
    if (fb.correct) setGuess('');
    else { setWrong(true); setTimeout(() => setWrong(false), 500); }
  }

  if (solved) return <Solved points={d.roundScores[playerId] ?? 0} sub={`#${d.solvedOrder.indexOf(playerId) + 1} to solve`} />;
  return (
    <div className="space-y-4">
      <div className="flex justify-center gap-1.5 flex-wrap">
        {d.scrambled.split('').map((c, i) => (
          <span key={i} className="jeo-tile v3 rounded-md w-9 h-11 flex items-center justify-center jeo-value text-xl">{c}</span>
        ))}
      </div>
      <input
        className={`jeo-input w-full px-4 py-3 rounded-lg text-xl text-center uppercase tracking-widest ${wrong ? 'border-red-500' : ''}`}
        placeholder="Your answer" value={guess} autoFocus autoCapitalize="characters"
        onChange={e => setGuess(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && submit()}
      />
      <button onClick={submit} className="jeo-btn-gold w-full py-3 rounded-lg text-lg">Solve</button>
      {wrong && <p className="text-center text-red-400 jeo-headline uppercase tracking-widest text-sm">Not it — keep trying</p>}
    </div>
  );
}

/* ── Rapid Fire ───────────────────────────────────────────────────────────── */
function RapidCtl({ d, playerId, onAction }: { d: RapidData; playerId: string; onAction: Props['onAction'] }) {
  const idx = d.progress[playerId] ?? 0;
  const [pending, setPending] = useState(false);
  const [flash, setFlash] = useState<'correct' | 'wrong' | null>(null);
  const q = d.questions[idx];

  async function pick(choice: string) {
    if (pending) return;
    setPending(true);
    const fb = await onAction({ type: 'answer', payload: { index: idx, choice } });
    setFlash(fb.correct ? 'correct' : 'wrong');
    setTimeout(() => setFlash(null), 450);
    setPending(false);
  }

  if (d.done[playerId] || !q) {
    return <Solved points={d.roundScores[playerId] ?? 0} sub={`${d.correct[playerId] ?? 0} correct · waiting for the buzzer`} />;
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="jeo-headline uppercase tracking-widest text-[11px] text-blue-200/60">{d.category}</span>
        <span className="jeo-value text-lg">{d.roundScores[playerId] ?? 0}</span>
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
          {flash === 'correct' ? '+100' : '−50'}
        </p>
      )}
    </div>
  );
}

/* ── Letter Reveal ────────────────────────────────────────────────────────── */
function LetterCtl({ d, playerId, onAction }: { d: LetterData; playerId: string; onAction: Props['onAction'] }) {
  const [guess, setGuess] = useState('');
  const [cooldown, setCooldown] = useState(0);
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
    if (fb.correct) setGuess('');
    else { startCooldown(); }
  }

  if (solved) {
    const hidden = d.wordLen - (d.solvedAtReveal[playerId] ?? 0);
    return <Solved points={d.roundScores[playerId] ?? 0} sub={`solved with ${hidden} still hidden`} />;
  }
  return (
    <div className="space-y-4">
      <div className="flex justify-center gap-1.5">
        {d.letters.map((c, i) => (
          <span key={i} className={`rounded-md w-9 h-11 flex items-center justify-center jeo-value text-xl border ${c ? 'jeo-tile v1' : 'border-dashed border-[rgba(0,229,255,0.3)]'}`}>{c ?? ''}</span>
        ))}
      </div>
      <input
        className="jeo-input w-full px-4 py-3 rounded-lg text-xl text-center uppercase tracking-[0.4em]"
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
  const meIdx = rows.findIndex(r => r.playerId === playerId);
  const me = meIdx >= 0 ? rows[meIdx] : null;
  return (
    <div className="text-center space-y-3 py-2">
      <p className="jeo-headline uppercase tracking-[0.3em] text-blue-200/70 text-sm">Round Over</p>
      {me && (
        <>
          <p className="hyper-title text-5xl">{fmtPts(me.points)}</p>
          <p className="jeo-headline uppercase tracking-widest text-white">#{meIdx + 1} · {me.detail}</p>
        </>
      )}
      {d.answerReveal && d.key !== 'rapid_fire' && (
        <p className="text-blue-200/70 jeo-headline uppercase tracking-widest text-sm">Answer: <span className="text-[var(--jeo-gold)]">{d.answerReveal}</span></p>
      )}
    </div>
  );
}
