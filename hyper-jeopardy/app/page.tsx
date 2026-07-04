'use client';
import { useEffect, useRef, useState, useCallback } from 'react';
import { Socket } from 'socket.io-client';
import { getSocket } from '@/lib/socket-client';
import { GameState, Player } from '@/lib/gameEngine';
import Lobby, { Account } from '@/components/Lobby';
import Board from '@/components/Board';
import Scoreboard from '@/components/Scoreboard';
import ClueModal from '@/components/ClueModal';
import HyperModal from '@/components/HyperModal';
import type { MGFeedback } from '@/components/MiniGameController';
import FinalJeopardy from '@/components/FinalJeopardy';
import Rejoin from '@/components/Rejoin';
import { playBoardFill, playGameStart, playWelcome } from '@/lib/audio';

interface AnswerResult {
  playerId: string;
  answer: string;
  result: string;
  correct?: string;
}

export default function Home() {
  const [socket, setSocket] = useState<Socket | null>(null);
  const [state, setState] = useState<GameState | null>(null);
  const [playerId, setPlayerId] = useState<string | null>(null);
  const [player, setPlayer] = useState<Player | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastAnswerResult, setLastAnswerResult] = useState<AnswerResult | null>(null);
  const [dbCount, setDbCount] = useState(0);
  const [scraping, setScraping] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [revealHyper, setRevealHyper] = useState(false);
  const lastBoardPhaseRef = useRef<string | null>(null);
  // Remembers how we joined so we can silently re-join after a socket reconnect
  // (socket.io hands us a NEW socket.id, but the server still has our player
  // record under the OLD id until we re-emit `join`).
  const joinParamsRef = useRef<{ name: string; avatar?: string; accountId?: string } | null>(null);

  // Testing phase: hyper (mini-game) cells are marked on the board BY DEFAULT so
  // they can be activated in rapid succession. Add ?reveal=off (or ?hide) to
  // restore the hidden, surprise behavior for a real playthrough.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setRevealHyper(!(q.get('reveal') === 'off' || q.has('hide')));
  }, []);

  // "Welcome to Hyper Jeopardy" voice cue — fires once when the app is opened
  // from the Central Industrial hub. Autoplay may be blocked on this fresh page
  // load, so playWelcome() falls back to the player's first tap/keypress.
  useEffect(() => {
    playWelcome();
  }, []);

  useEffect(() => {
    const s = getSocket();
    setSocket(s);

    s.on('connect', () => {
      setConnected(true);
      // Reconnect recovery: after a dropped connection socket.io reconnects with
      // a NEW socket.id. Re-emit `join` so the server re-attaches our player
      // record (by accountId/name) to the new id — otherwise host-only actions
      // like Start Game, plus buzzing and clue selection, silently no-op because
      // the server matches every action by socket.id. (No-op on the first
      // connect: joinParamsRef is null until the user actually joins.)
      const jp = joinParamsRef.current;
      if (jp) s.emit('join', { name: jp.name, avatar: jp.avatar, accountId: jp.accountId });
    });
    s.on('disconnect', () => setConnected(false));
    s.on('state', (newState: GameState) => setState(newState));
    s.on('joined', ({ playerId: pid, player: p }: { playerId: string; player: Player }) => {
      setPlayerId(pid);
      setPlayer(p);
    });
    s.on('error', (msg: string) => { setError(msg); setTimeout(() => setError(null), 4000); });
    s.on('answer_result', (result: AnswerResult) => {
      setLastAnswerResult(result);
      setTimeout(() => setLastAnswerResult(null), 3000);
    });
    s.on('accounts', (list: Account[]) => setAccounts(list));

    fetch('/api/scrape').then(r => r.json()).then(d => setDbCount(d.count));

    return () => {
      s.off('connect');
      s.off('disconnect');
      s.off('state');
      s.off('joined');
      s.off('error');
      s.off('answer_result');
      s.off('accounts');
    };
  }, []);

  // Board-reveal SFX. The very first board (game start) fires the laser-charge
  // cue in place of the Jeopardy jingle; later rounds keep the board-fill sound.
  useEffect(() => {
    const phase = state?.phase;
    if (!phase) return;
    if ((phase === 'jeopardy' || phase === 'double_jeopardy') && lastBoardPhaseRef.current !== phase) {
      lastBoardPhaseRef.current = phase;
      if (phase === 'jeopardy') playGameStart();
      else playBoardFill();
    }
  }, [state?.phase]);

  // Keep local `player` in sync with server's player list. If we got purged
  // (e.g. host pressed Reset Lobby) clear it so the join form shows again.
  useEffect(() => {
    if (!state) return;
    if (!playerId) { setPlayer(null); return; }
    const me = state.players.find(p => p.id === playerId);
    if (!me) setPlayer(null);
    else setPlayer(me);
  }, [state, playerId]);

  const handleJoin = useCallback((name: string, avatar?: string, accountId?: string) => {
    joinParamsRef.current = { name, avatar, accountId };
    socket?.emit('join', { name, isHost: !state?.players?.length, avatar, accountId });
  }, [socket, state]);

  const handleRename = useCallback((name: string) => {
    socket?.emit('rename', { name });
  }, [socket]);

  // Account CRUD via socket.io ack callbacks — lets the Lobby chain a join
  // immediately after creation by awaiting the new account id.
  const handleCreateAccount = useCallback(
    (name: string, avatar?: string) => new Promise<Account | null>(resolve => {
      if (!socket) { resolve(null); return; }
      socket.emit('create_account', { name, avatar }, (resp: { account?: Account; error?: string }) => {
        resolve(resp?.account ?? null);
      });
    }),
    [socket],
  );

  const handleUpdateAccount = useCallback(
    (id: string, patch: { name?: string; avatar?: string }) => new Promise<Account | null>(resolve => {
      if (!socket) { resolve(null); return; }
      socket.emit('update_account', { id, ...patch }, (resp: { account?: Account; error?: string }) => {
        resolve(resp?.account ?? null);
      });
    }),
    [socket],
  );

  const handleDeleteAccount = useCallback((id: string) => {
    socket?.emit('delete_account', { id });
  }, [socket]);

  const handleStart = useCallback(() => socket?.emit('start_game'), [socket]);

  const handleScrape = useCallback(async () => {
    setScraping(true);
    const res = await fetch('/api/scrape', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: 15, startId: 9100 }),
    });
    const data = await res.json();
    setDbCount(data.total);
    setScraping(false);
  }, []);

  const handleSelectClue = useCallback((catIdx: number, clueIdx: number) => {
    socket?.emit('select_clue', { catIdx, clueIdx });
  }, [socket]);

  const handleGiveUp = useCallback(() => socket?.emit('give_up'), [socket]);

  // Emit a mini-game move and resolve with the server's ack (correct/wrong/points).
  const handleMiniGameAction = useCallback(
    (action: { type: string; payload?: unknown }) => new Promise<MGFeedback>(resolve => {
      if (!socket) { resolve({}); return; }
      socket.emit('mini_game_action', action, (feedback: MGFeedback) => resolve(feedback || {}));
    }),
    [socket],
  );

  const handleBuzz = useCallback(() => socket?.emit('buzz'), [socket]);

  const handleSkip = useCallback(() => socket?.emit('skip_clue'), [socket]);

  const handleAnswer = useCallback((answer: string) => {
    socket?.emit('answer', { answer });
  }, [socket]);

  const handleDailyDoubleWager = useCallback((wager: number) => {
    socket?.emit('daily_double_wager', { wager });
  }, [socket]);

  const handleFinalWager = useCallback((wager: number) => socket?.emit('final_wager', { wager }), [socket]);
  const handleFinalAnswer = useCallback((answer: string) => socket?.emit('final_answer', { answer }), [socket]);
  const handleRevealFinal = useCallback(() => socket?.emit('reveal_final'), [socket]);
  const handleNewGame = useCallback(() => socket?.emit('new_game'), [socket]);
  const handleResetLobby = useCallback(() => {
    if (typeof window !== 'undefined' && !window.confirm('Reset the lobby? This kicks everyone out.')) return;
    socket?.emit('reset_lobby');
  }, [socket]);
  const handleSetScore = useCallback(
    (id: string, score: number) => socket?.emit('set_score', { playerId: id, score }),
    [socket],
  );

  if (!connected) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 border-4 border-[var(--jeo-gold)] border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="jeo-headline tracking-widest uppercase text-blue-200/80">Connecting...</p>
        </div>
      </div>
    );
  }

  if (!state || state.phase === 'lobby') {
    return (
      <>
        {error && (
          <div className="fixed top-4 right-4 bg-red-600 text-white px-4 py-2 rounded-lg z-50 shadow-lg">
            {error}
          </div>
        )}
        <Lobby
          state={state ?? { players: [], showNumber: 0, airDate: '' } as unknown as GameState}
          playerId={playerId}
          player={player}
          onJoin={handleJoin}
          onRename={handleRename}
          onStart={handleStart}
          onScrape={handleScrape}
          onResetLobby={handleResetLobby}
          scraping={scraping}
          dbCount={dbCount}
          accounts={accounts}
          onCreateAccount={handleCreateAccount}
          onUpdateAccount={handleUpdateAccount}
          onDeleteAccount={handleDeleteAccount}
        />
      </>
    );
  }

  // Game is in progress but this client isn't a recognized player —
  // show the rejoin screen so a previously-joined player who lost their
  // session can take their seat back.
  if (!player) {
    return (
      <>
        {error && (
          <div className="fixed top-4 right-4 bg-red-600 text-white px-4 py-2 rounded-lg z-50 shadow-lg jeo-headline tracking-wide">
            {error}
          </div>
        )}
        <Rejoin state={state} onJoin={handleJoin} />
      </>
    );
  }

  if (state.phase === 'final_jeopardy' || state.phase === 'game_over') {
    return (
      <>
        {error && (
          <div className="fixed top-4 right-4 bg-red-600 text-white px-4 py-2 rounded-lg z-50 shadow-lg">
            {error}
          </div>
        )}
        {state.phase === 'game_over' && player?.isHost && (
          <div className="fixed top-4 left-4 z-50">
            <button
              onClick={handleNewGame}
              className="jeo-btn-gold px-4 py-2 rounded-lg text-sm"
            >
              New Game
            </button>
          </div>
        )}
        <FinalJeopardy
          state={state}
          playerId={playerId}
          onWager={handleFinalWager}
          onAnswer={handleFinalAnswer}
          onReveal={handleRevealFinal}
        />
      </>
    );
  }

  const boardController = state.players.find(p => p.id === state.boardController);
  const roundLabel = state.phase === 'jeopardy' ? 'Hyper Jeopardy!' : 'Double Jeopardy!';

  return (
    <div className="min-h-screen flex flex-col pb-24 sm:pb-0">
      {error && (
        <div className="fixed top-4 right-4 bg-red-600/90 text-white px-4 py-2 rounded-lg z-50 shadow-lg jeo-headline tracking-wide">
          {error}
        </div>
      )}

      {/* Header */}
      <div className="bg-[rgba(8,10,30,0.7)] border-b border-[rgba(0,229,255,0.2)] backdrop-blur py-2 sm:py-3 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0 flex-1">
              <h1 className="jeo-title text-xl sm:text-3xl truncate">{roundLabel}</h1>
              <p className="text-blue-200/60 text-[10px] jeo-headline tracking-[0.3em] uppercase mt-0.5 truncate">
                Show #{state.showNumber} · {state.airDate}
              </p>
            </div>
            {player?.isHost && (
              <button
                onClick={handleNewGame}
                className="shrink-0 jeo-headline tracking-wider uppercase text-[11px] sm:text-xs px-3 py-1.5 rounded-md border border-[rgba(0,229,255,0.45)] text-[var(--jeo-gold)] bg-[rgba(0,229,255,0.05)] hover:bg-[rgba(0,229,255,0.15)] active:scale-95 transition"
              >
                New Game
              </button>
            )}
          </div>
          {boardController && state.cluePhase === 'idle' && (
            <p className="mt-1 text-xs jeo-headline tracking-widest uppercase text-blue-200/80">
              Board: <span className="text-[var(--jeo-gold)]">{boardController.name}</span>
            </p>
          )}
        </div>
      </div>

      {/* Board */}
      <div className="flex-1 p-2 sm:p-4">
        <div className="max-w-6xl mx-auto">
          {state.currentBoard && (
            <Board
              board={state.currentBoard}
              state={state}
              playerId={playerId}
              onSelectClue={handleSelectClue}
              revealHyper={revealHyper}
            />
          )}
        </div>
      </div>

      {/* Scoreboard — desktop: bottom row, full size */}
      <div className="hidden sm:block py-4 px-4 bg-[rgba(8,10,30,0.4)] border-t border-[rgba(0,229,255,0.12)]">
        <div className="max-w-6xl mx-auto">
          <Scoreboard
            players={state.players}
            currentPlayerId={playerId}
            buzzedPlayerId={state.buzzedPlayerId}
            compact={false}
            isHost={!!player?.isHost}
            onSetScore={handleSetScore}
          />
        </div>
      </div>

      {/* Scoreboard — mobile: fixed bottom, compact */}
      <div className="sm:hidden fixed bottom-0 inset-x-0 z-30 bg-[rgba(8,10,30,0.92)] backdrop-blur border-t border-[rgba(0,229,255,0.25)] px-2 py-2">
        <Scoreboard
          players={state.players}
          currentPlayerId={playerId}
          buzzedPlayerId={state.buzzedPlayerId}
          compact={true}
          isHost={!!player?.isHost}
          onSetScore={handleSetScore}
        />
      </div>

      {/* Clue overlay */}
      <ClueModal
        state={state}
        playerId={playerId}
        onBuzz={handleBuzz}
        onSkip={handleSkip}
        onAnswer={handleAnswer}
        onDailyDoubleWager={handleDailyDoubleWager}
        lastAnswerResult={lastAnswerResult}
      />

      {/* Hyper Mode overlay */}
      <HyperModal state={state} playerId={playerId} onGiveUp={handleGiveUp} onMiniGameAction={handleMiniGameAction} />
    </div>
  );
}
