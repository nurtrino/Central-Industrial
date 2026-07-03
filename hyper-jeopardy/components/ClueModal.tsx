'use client';
import { useEffect, useRef, useState } from 'react';
import { GameState } from '@/lib/gameEngine';
import { isUnavailableClue } from '@/lib/clueSentinel';
import {
  playBuzzIn,
  playCorrect,
  playWrong,
  playTimeUp,
  playDailyDouble,
} from '@/lib/audio';

interface Props {
  state: GameState;
  playerId: string | null;
  onBuzz: () => void;
  onSkip: () => void;
  onAnswer: (answer: string) => void;
  onDailyDoubleWager: (wager: number) => void;
  lastAnswerResult?: { playerId: string; answer: string; result: string; correct?: string } | null;
}

export default function ClueModal({ state, playerId, onBuzz, onSkip, onAnswer, onDailyDoubleWager, lastAnswerResult }: Props) {
  const [answer, setAnswer] = useState('');
  const [wager, setWager] = useState('');
  const [timeLeftMs, setTimeLeftMs] = useState<number | null>(null);
  const [timerMaxMs, setTimerMaxMs] = useState<number | null>(null);
  const [buzzPending, setBuzzPending] = useState(false);
  const prevPhaseRef = useRef<string | null>(null);
  const lastResultIdRef = useRef<string | null>(null);
  const lastTimerEndsAtRef = useRef<number | null>(null);

  const { cluePhase, activeClue, activeCategoryName, buzzedPlayerId, wrongAnswerers, skippedBy } = state;
  const isBuzzed = buzzedPlayerId === playerId;
  const isAnswerer = isBuzzed;
  const alreadyWrong = wrongAnswerers?.includes(playerId ?? '');
  const alreadySkipped = (skippedBy ?? []).includes(playerId ?? '');
  const player = state.players.find(p => p.id === playerId);
  const skipEligible = state.players.filter(p => p.connected && !(wrongAnswerers ?? []).includes(p.id));
  const skipCount = (skippedBy ?? []).filter(id => skipEligible.some(p => p.id === id)).length;
  const canSkip = (cluePhase === 'reading' || cluePhase === 'buzzing') && !alreadySkipped && !alreadyWrong;

  // Phase-change sound effects
  useEffect(() => {
    const prev = prevPhaseRef.current;
    if (prev !== cluePhase) {
      if (cluePhase === 'answering' && prev === 'buzzing') playBuzzIn();
      if (cluePhase === 'daily_double_wager' && prev !== 'daily_double_wager') playDailyDouble();
      prevPhaseRef.current = cluePhase;
    }
  }, [cluePhase]);

  // Result stings (correct/wrong) — fire once per result
  useEffect(() => {
    if (!lastAnswerResult) return;
    const id = `${lastAnswerResult.playerId}:${lastAnswerResult.answer}:${lastAnswerResult.result}`;
    if (lastResultIdRef.current === id) return;
    lastResultIdRef.current = id;
    if (lastAnswerResult.result === 'correct') playCorrect();
    else if (lastAnswerResult.result === 'wrong') playWrong();
  }, [lastAnswerResult]);

  // Time-up buzzer when buzz/answer timer expires without input
  useEffect(() => {
    if (timeLeftMs === 0 && (cluePhase === 'buzzing' || cluePhase === 'answering' || cluePhase === 'daily_double_answer')) {
      playTimeUp();
    }
  }, [timeLeftMs, cluePhase]);

  useEffect(() => {
    if (!state.timerEndsAt) {
      setTimeLeftMs(null);
      setTimerMaxMs(null);
      lastTimerEndsAtRef.current = null;
      return;
    }
    // New timer started — capture its full duration in ms once, so the bar
    // animates smoothly from 100% to 0% regardless of which phase we're in.
    if (lastTimerEndsAtRef.current !== state.timerEndsAt) {
      const fullMs = Math.max(100, state.timerEndsAt - Date.now());
      setTimerMaxMs(fullMs);
      lastTimerEndsAtRef.current = state.timerEndsAt;
    }
    const update = () => {
      const remaining = Math.max(0, state.timerEndsAt! - Date.now());
      setTimeLeftMs(remaining);
    };
    update();
    const interval = setInterval(update, 50);
    return () => clearInterval(interval);
  }, [state.timerEndsAt]);

  useEffect(() => {
    setAnswer('');
    setWager('');
    setBuzzPending(false);
  }, [cluePhase, activeClue?.id]);

  // HYPER MODE has its own overlay (HyperModal) — bail out here.
  if (!activeClue || ['idle', 'hyper_intro', 'hyper_active'].includes(cluePhase)) return null;

  // Defensive fallback: if buzzedPlayerId somehow doesn't resolve, use the
  // first player in buzzOrder. Prevents a blank "is answering..." display
  // during rapid simultaneous-buzz races.
  const buzzedPlayer =
    state.players.find(p => p.id === buzzedPlayerId) ??
    state.players.find(p => p.id === (state.buzzOrder ?? [])[0]);
  const isAnsweringPhase = cluePhase === 'answering';
  const showQuestion = cluePhase !== 'daily_double_wager';

  return (
    <div className="fixed inset-0 bg-black/85 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="jeo-card rounded-2xl w-full max-w-2xl overflow-hidden">

        {/* Category + value header */}
        <div className="px-6 py-3 flex items-center justify-between border-b border-[rgba(0,229,255,0.2)] bg-[rgba(12,16,46,0.6)]">
          <span className="jeo-headline text-blue-200/80 text-sm uppercase tracking-[0.25em]">
            {activeCategoryName}
          </span>
          <span className="jeo-value text-2xl">
            {cluePhase === 'daily_double_wager' || cluePhase === 'daily_double_answer'
              ? 'DAILY DOUBLE!'
              : `$${activeClue.value.toLocaleString()}`}
          </span>
        </div>

        {/* Clue text — hidden during DD wager so the player commits before seeing it */}
        {showQuestion ? (
          <div className="px-8 py-10 text-center">
            {isUnavailableClue(activeClue.question) ? (
              <div className="flex flex-col items-center gap-3">
                <div
                  className="w-full max-w-sm h-32 sm:h-40 rounded-xl border-2 border-dashed border-blue-400/40 bg-blue-950/40"
                  aria-label="Clue unavailable — original clue required an image or audio clip"
                />
                <p className="jeo-headline text-blue-200/60 text-xs uppercase tracking-[0.25em]">
                  Clue unavailable — please skip
                </p>
              </div>
            ) : (
              <p className="text-white text-2xl sm:text-3xl font-semibold leading-relaxed jeo-headline tracking-wide">
                {activeClue.question}
              </p>
            )}
          </div>
        ) : (
          <div className="px-8 py-10 text-center">
            <p className="jeo-title text-4xl sm:text-5xl">DAILY DOUBLE!</p>
            <p className="mt-3 text-blue-200/80 jeo-headline tracking-[0.25em] uppercase text-sm">
              Lock in your wager. The clue will appear next.
            </p>
          </div>
        )}

        {/* Timer bar */}
        {timeLeftMs !== null && timerMaxMs !== null && (() => {
          const secsLeft = Math.ceil(timeLeftMs / 1000);
          const pct = Math.max(0, Math.min(100, (timeLeftMs / timerMaxMs) * 100));
          return (
            <div className="px-6 pb-2">
              <div className="h-1.5 bg-[rgba(12,16,46,0.7)] rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${secsLeft <= 2 ? 'bg-red-500' : 'bg-[var(--jeo-gold)]'}`}
                  style={{ width: `${pct}%`, transition: 'width 80ms linear, background-color 200ms' }}
                />
              </div>
              <p className="text-center text-xs jeo-headline tracking-widest text-blue-200/70 mt-1.5 uppercase">
                {secsLeft}s
              </p>
            </div>
          );
        })()}

        {/* Actions */}
        <div className="px-6 pb-6 space-y-3">

          {/* Daily Double Wager */}
          {cluePhase === 'daily_double_wager' && state.boardController === playerId && (
            <div className="space-y-3">
              <p className="jeo-headline uppercase tracking-widest text-[var(--jeo-gold)] text-center text-xl">
                Daily Double! Enter your wager
              </p>
              <p className="text-blue-200/80 text-center text-sm jeo-headline tracking-wider">
                Max: ${((player?.score ?? 0) > 0 ? (player?.score ?? 0) : (state.phase === 'jeopardy' ? 1000 : 2000)).toLocaleString()}
              </p>
              <input
                className="jeo-input w-full px-4 py-3 rounded-lg text-xl text-center jeo-value"
                placeholder="Enter wager"
                type="number"
                min={5}
                max={(player?.score ?? 0) > 0 ? (player?.score ?? 0) : (state.phase === 'jeopardy' ? 1000 : 2000)}
                value={wager}
                onChange={e => setWager(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && wager && onDailyDoubleWager(parseInt(wager))}
              />
              <button
                onClick={() => wager && onDailyDoubleWager(parseInt(wager))}
                className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
              >
                Submit Wager
              </button>
            </div>
          )}

          {cluePhase === 'daily_double_wager' && state.boardController !== playerId && (
            <p className="text-center text-blue-200/80 jeo-headline tracking-wider uppercase">
              {buzzedPlayer?.name ?? state.players.find(p => p.id === state.boardController)?.name} is wagering...
            </p>
          )}

          {/* Daily Double Answer */}
          {cluePhase === 'daily_double_answer' && isAnswerer && (
            <div className="space-y-3">
              <p className="jeo-headline uppercase tracking-widest text-[var(--jeo-gold)] text-center">
                Wager: ${state.dailyDoubleWager?.toLocaleString()}
              </p>
              <input
                className="jeo-input w-full px-4 py-3 rounded-lg text-xl"
                placeholder="What is..."
                value={answer}
                onChange={e => setAnswer(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && onAnswer(answer)}
                autoFocus
              />
              <button
                onClick={() => onAnswer(answer)}
                className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
              >
                Submit Answer
              </button>
            </div>
          )}

          {cluePhase === 'daily_double_answer' && !isAnswerer && (
            <p className="text-center text-blue-200/80 jeo-headline tracking-wider uppercase">{buzzedPlayer?.name} is answering...</p>
          )}

          {/* Buzz button — circular */}
          {cluePhase === 'buzzing' && !alreadyWrong && !isBuzzed && (
            <div className="flex justify-center pt-2">
              <button
                onClick={() => {
                  if (buzzPending) return;
                  setBuzzPending(true);
                  onBuzz();
                }}
                disabled={buzzPending}
                className="jeo-buzz w-40 h-40 sm:w-48 sm:h-48 rounded-full text-white jeo-headline uppercase tracking-[0.25em] text-3xl sm:text-4xl active:scale-95 transition disabled:opacity-60 disabled:cursor-not-allowed"
                aria-label="Buzz in"
              >
                Buzz
              </button>
            </div>
          )}

          {/* Skip vote — available during reading or buzzing.
              Spaced well below the Buzz button so it can't be accidentally tapped. */}
          {(cluePhase === 'reading' || cluePhase === 'buzzing') && !isBuzzed && (
            <div className="flex flex-col items-center gap-2 pt-20 sm:pt-24">
              <button
                onClick={onSkip}
                disabled={!canSkip}
                className="jeo-headline uppercase tracking-[0.18em] text-base sm:text-lg px-8 py-4 rounded-lg border-2 border-[rgba(0,229,255,0.4)] text-blue-200/90 hover:text-[var(--jeo-gold)] hover:border-[var(--jeo-gold)] disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                {alreadySkipped ? 'Skip voted ✓' : 'Skip'}
              </button>
              {skipEligible.length > 0 && (skippedBy ?? []).length > 0 && (
                <p className="text-[10px] jeo-headline tracking-widest uppercase text-blue-200/60">
                  Skip votes: {skipCount}/{skipEligible.length}
                </p>
              )}
            </div>
          )}

          {cluePhase === 'buzzing' && alreadyWrong && (
            <p className="text-center text-red-400 jeo-headline uppercase tracking-widest">Locked out</p>
          )}

          {cluePhase === 'reading' && (
            <p className="text-center text-blue-200/70 jeo-headline tracking-widest uppercase animate-pulse">Get ready to buzz...</p>
          )}

          {/* Answer input */}
          {cluePhase === 'answering' && isAnswerer && (
            <div className="space-y-3">
              <p className="jeo-headline uppercase tracking-widest text-[var(--jeo-gold)] text-center">
                Your turn — in the form of a question
              </p>
              <input
                className="jeo-input w-full px-4 py-3 rounded-lg text-xl"
                placeholder="What is..."
                value={answer}
                onChange={e => setAnswer(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && onAnswer(answer)}
                autoFocus
              />
              <button
                onClick={() => onAnswer(answer)}
                className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
              >
                Submit Answer
              </button>
            </div>
          )}

          {cluePhase === 'answering' && !isAnswerer && (
            <p className="text-center text-[var(--jeo-gold)] jeo-headline tracking-widest uppercase">
              {buzzedPlayer?.name} is answering...
            </p>
          )}

          {/* Reveal */}
          {cluePhase === 'reveal' && (
            <div className="space-y-3">
              {lastAnswerResult && (
                <div className={`text-center p-3 rounded-lg border ${lastAnswerResult.result === 'correct' ? 'bg-green-900/40 border-green-500/50' : 'bg-red-900/40 border-red-500/50'}`}>
                  <p className={`jeo-headline tracking-wide ${lastAnswerResult.result === 'correct' ? 'text-green-300' : 'text-red-300'}`}>
                    {state.players.find(p => p.id === lastAnswerResult.playerId)?.name}: &ldquo;{lastAnswerResult.answer}&rdquo; — {lastAnswerResult.result === 'correct' ? '✓ Correct' : '✗ Wrong'}
                  </p>
                </div>
              )}
              <div className="rounded-lg p-4 text-center bg-[rgba(12,16,46,0.7)] border border-[rgba(0,229,255,0.2)]">
                <p className="text-blue-200/70 text-xs jeo-headline tracking-widest uppercase mb-1">Correct answer</p>
                <p className="jeo-value text-2xl">{activeClue.answer}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
