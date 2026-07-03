'use client';
import { useEffect, useRef, useState } from 'react';
import { GameState } from '@/lib/gameEngine';
import { playThinkMusic, stopAllMusic, playCorrect, playWrong } from '@/lib/audio';

interface Props {
  state: GameState;
  playerId: string | null;
  onWager: (wager: number) => void;
  onAnswer: (answer: string) => void;
  onReveal: () => void;
}

const FINAL_THINK_SECONDS = 45;

export default function FinalJeopardy({ state, playerId, onWager, onAnswer, onReveal }: Props) {
  const [wager, setWager] = useState('');
  const [answer, setAnswer] = useState('');
  const musicStopRef = useRef<(() => void) | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const revealedSoundRef = useRef(false);

  const player = state.players.find(p => p.id === playerId);
  const entry = playerId ? state.finalEntries[playerId] : null;
  const isHost = player?.isHost;
  const eligible = (p: { score: number }) => p.score > 0;
  const allWagered = state.players.filter(eligible).every(p => state.finalEntries[p.id]?.wager !== null);
  const allAnswered = state.players.filter(eligible).every(p => state.finalEntries[p.id]?.answer !== null);

  useEffect(() => {
    if (allWagered && !state.finalRevealed && !musicStopRef.current) {
      musicStopRef.current = playThinkMusic(FINAL_THINK_SECONDS);
      startedAtRef.current = Date.now();
      setSecondsLeft(FINAL_THINK_SECONDS);
    }
    if (state.finalRevealed && musicStopRef.current) {
      musicStopRef.current();
      musicStopRef.current = null;
    }
  }, [allWagered, state.finalRevealed]);

  useEffect(() => () => {
    if (musicStopRef.current) musicStopRef.current();
    musicStopRef.current = null;
    stopAllMusic();
  }, []);

  useEffect(() => {
    if (startedAtRef.current === null) return;
    const i = setInterval(() => {
      const elapsed = (Date.now() - (startedAtRef.current ?? Date.now())) / 1000;
      const remaining = Math.max(0, FINAL_THINK_SECONDS - elapsed);
      setSecondsLeft(Math.ceil(remaining));
      if (remaining <= 0) clearInterval(i);
    }, 200);
    return () => clearInterval(i);
  }, [allWagered]);

  useEffect(() => {
    if (secondsLeft === 0 && allWagered && entry && entry.answer === null && eligible(player ?? { score: 0 })) {
      onAnswer(answer || '');
    }
  }, [secondsLeft, allWagered, entry, player, answer, onAnswer]);

  useEffect(() => {
    if (state.finalRevealed && !revealedSoundRef.current) {
      revealedSoundRef.current = true;
      const me = playerId && state.finalEntries[playerId];
      if (me && me.correct) playCorrect();
      else if (me) playWrong();
    }
  }, [state.finalRevealed, playerId, state.finalEntries]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4">
      <h1 className="jeo-title text-5xl sm:text-6xl mb-6">FINAL JEOPARDY!</h1>

      <div className="jeo-card rounded-2xl p-8 w-full max-w-2xl space-y-6">
        <div className="text-center">
          <p className="text-blue-200/70 text-xs jeo-headline tracking-[0.3em] uppercase">Category</p>
          <p className="jeo-value text-3xl mt-1.5">{state.finalJeopardy?.category}</p>
        </div>

        {!state.finalRevealed ? (
          <>
            {entry?.wager == null && player && player.score > 0 ? (
              <div className="space-y-3">
                <p className="text-center text-blue-200/80 jeo-headline tracking-wider">
                  Your score: <span className="jeo-value">${player.score.toLocaleString()}</span>
                </p>
                <input
                  className="jeo-input w-full px-4 py-3 rounded-lg text-xl text-center jeo-value"
                  placeholder="Enter wager"
                  type="number"
                  min="0"
                  max={player.score}
                  value={wager}
                  onChange={e => setWager(e.target.value)}
                />
                <button
                  onClick={() => wager && onWager(parseInt(wager))}
                  className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
                >
                  Lock In Wager
                </button>
              </div>
            ) : entry?.wager != null ? (
              <p className="text-center text-green-300 jeo-headline tracking-widest uppercase">
                Wager locked: ${entry.wager?.toLocaleString()}
              </p>
            ) : (
              <p className="text-center text-blue-200/70 jeo-headline tracking-wider uppercase text-sm">
                You cannot wager (score must be positive)
              </p>
            )}

            {allWagered && (
              <div className="space-y-4 border-t border-[rgba(0,229,255,0.2)] pt-5">
                {secondsLeft !== null && (
                  <div className="text-center">
                    <p className={`jeo-value text-4xl ${secondsLeft <= 5 ? '!text-red-400 animate-pulse' : ''}`}>
                      {secondsLeft}s
                    </p>
                  </div>
                )}
                <p className="text-white text-2xl jeo-headline font-semibold text-center leading-relaxed">
                  {state.finalJeopardy?.question}
                </p>

                {!entry?.answer && (
                  <div className="space-y-3">
                    <input
                      className="jeo-input w-full px-4 py-3 rounded-lg text-xl"
                      placeholder="What is..."
                      value={answer}
                      onChange={e => setAnswer(e.target.value)}
                    />
                    <button
                      onClick={() => onAnswer(answer)}
                      className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
                    >
                      Submit Final Answer
                    </button>
                  </div>
                )}

                {entry?.answer && (
                  <p className="text-center text-green-300 jeo-headline tracking-widest uppercase">
                    Answer submitted
                  </p>
                )}

                {allAnswered && isHost && (
                  <button
                    onClick={onReveal}
                    className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
                  >
                    Reveal Results
                  </button>
                )}

                <p className="text-center text-blue-200/70 text-xs jeo-headline tracking-widest uppercase">
                  {state.players.filter(p => state.finalEntries[p.id]?.answer !== null).length}/{state.players.length} answered
                </p>
              </div>
            )}
          </>
        ) : (
          <div className="space-y-4">
            <div className="rounded-lg p-4 text-center bg-[rgba(12,16,46,0.7)] border border-[rgba(0,229,255,0.25)]">
              <p className="text-blue-200/70 text-xs jeo-headline tracking-widest uppercase mb-1">Correct answer</p>
              <p className="jeo-value text-2xl">{state.finalJeopardy?.answer}</p>
            </div>

            {state.players
              .sort((a, b) => b.score - a.score)
              .map(p => {
                const e = state.finalEntries[p.id];
                return (
                  <div
                    key={p.id}
                    className={`rounded-lg p-4 border ${e?.correct ? 'border-green-500/60 bg-green-900/30' : 'border-red-500/60 bg-red-900/30'}`}
                  >
                    <div className="flex justify-between items-center">
                      <span className="jeo-headline text-lg tracking-wide">{p.name}</span>
                      <span className={`jeo-value text-xl ${p.score < 0 ? '!text-red-300' : ''}`}>
                        ${p.score.toLocaleString()}
                      </span>
                    </div>
                    <p className="text-blue-200/70 text-sm mt-1">
                      &ldquo;{e?.answer}&rdquo; · wager ${e?.wager?.toLocaleString()}
                    </p>
                  </div>
                );
              })}

            <div className="text-center pt-2">
              <p className="jeo-title text-3xl">
                Winner: {state.players.sort((a, b) => b.score - a.score)[0]?.name}!
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
