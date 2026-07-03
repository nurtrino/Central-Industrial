'use client';
import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { GameState, Player } from '@/lib/gameEngine';
import { unlockAudio } from '@/lib/audio';
import {
  fileToAvatarDataURL,
  getLastUsedAccountId,
  setLastUsedAccountId,
} from '@/lib/profile';
import Leaderboard from './Leaderboard';

// The shape we get from the server's accounts broadcast.
export interface Account {
  id: string;
  name: string;
  avatar?: string;
  wins: number;
  createdAt: number;
}

interface Props {
  state: GameState;
  playerId: string | null;
  player: Player | null;
  onJoin: (name: string, avatar?: string, accountId?: string) => void;
  onRename?: (name: string) => void;
  onStart: () => void;
  onScrape: () => void;
  onResetLobby?: () => void;
  scraping: boolean;
  dbCount: number;
  // Server-driven account list.
  accounts: Account[];
  onCreateAccount: (name: string, avatar?: string) => Promise<Account | null>;
  onUpdateAccount: (id: string, patch: { name?: string; avatar?: string }) => Promise<Account | null>;
  onDeleteAccount: (id: string) => void;
}

const MIN_PLAYERS = 2;

type Mode = 'new' | 'returning' | 'create' | 'edit';

export default function Lobby({
  state, playerId, player, onJoin, onRename, onStart, onResetLobby, dbCount,
  accounts, onCreateAccount, onUpdateAccount, onDeleteAccount,
}: Props) {
  const [name, setName] = useState('');
  const [joining, setJoining] = useState(false);
  const [mode, setMode] = useState<Mode>('new');
  const [editingAccount, setEditingAccount] = useState<Account | null>(null);
  const [showLeaderboard, setShowLeaderboard] = useState(false);
  // We have to wait for the first accounts broadcast before defaulting to the
  // returning-player grid, otherwise we'd briefly flash the new-player form.
  const initializedDefault = useRef(false);

  useEffect(() => {
    if (initializedDefault.current || player) return;
    // Once we know there are any accounts, default to that mode. If the
    // device last played as one of them, prefill the name for visual cue.
    if (accounts.length > 0) {
      setMode('returning');
      const last = getLastUsedAccountId();
      if (last) {
        const found = accounts.find(a => a.id === last);
        if (found) setName(found.name);
      }
      initializedDefault.current = true;
    }
  }, [accounts, player]);

  const isHost = player?.isHost;
  const count = state.players.length;
  const canStart = count >= MIN_PLAYERS;

  const doJoin = (n: string, avatar?: string, accountId?: string) => {
    if (joining) return;
    setJoining(true);
    unlockAudio();
    onJoin(n, avatar, accountId);
  };

  const joinWithAccount = (a: Account) => {
    setLastUsedAccountId(a.id);
    doJoin(a.name, a.avatar, a.id);
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4">
      <div className="text-center mb-10">
        <Image
          src="/jeopardy-logo.png"
          alt="JEOPARDY!"
          width={3840}
          height={2160}
          priority
          className="jeo-logo w-[280px] sm:w-[420px] md:w-[520px] h-auto mx-auto select-none"
        />
        <p className="mt-4 text-blue-200/80 jeo-headline text-sm tracking-[0.3em] uppercase">
          {state.showNumber ? `Show #${state.showNumber} · ${state.airDate}` : 'Loading...'}
        </p>
      </div>

      <div className="jeo-card rounded-3xl p-8 w-full max-w-md">
        {!player ? (
          <>
            {mode === 'new' && (
              <NewPlayerForm
                name={name}
                setName={setName}
                joining={joining}
                onJoin={() => name.trim() && doJoin(name.trim())}
                onShowReturning={() => setMode('returning')}
                dbCount={dbCount}
                hasAccounts={accounts.length > 0}
              />
            )}
            {mode === 'returning' && (
              <ReturningPlayerList
                accounts={accounts}
                onPick={joinWithAccount}
                onCreate={() => { setEditingAccount(null); setMode('create'); }}
                onEdit={(a) => { setEditingAccount(a); setMode('edit'); }}
                onShowNew={() => setMode('new')}
              />
            )}
            {(mode === 'create' || mode === 'edit') && (
              <AccountForm
                initial={mode === 'edit' ? editingAccount : null}
                onCancel={() => setMode('returning')}
                onCreate={onCreateAccount}
                onUpdate={onUpdateAccount}
                onDelete={mode === 'edit' && editingAccount
                  ? () => { onDeleteAccount(editingAccount.id); setMode('returning'); }
                  : undefined}
                onDone={(acct, joinNow) => {
                  if (joinNow && acct) joinWithAccount(acct);
                  else setMode('returning');
                }}
              />
            )}
          </>
        ) : (
          <InLobby
            state={state}
            player={player}
            playerId={playerId}
            isHost={!!isHost}
            canStart={canStart}
            count={count}
            onStart={onStart}
            onResetLobby={onResetLobby}
            onRename={onRename}
          />
        )}

        <button
          onClick={() => setShowLeaderboard(true)}
          className="mt-5 w-full text-center jeo-headline tracking-[0.25em] uppercase text-xs text-blue-200/70 hover:text-[var(--jeo-gold)] transition py-1.5 border-t border-[rgba(0,229,255,0.12)]"
        >
          🏆 Leaderboard
        </button>
      </div>

      {showLeaderboard && (
        <Leaderboard entries={accounts} onClose={() => setShowLeaderboard(false)} />
      )}
    </div>
  );
}

// --- pre-join sub-screens -------------------------------------------------

function NewPlayerForm({
  name, setName, joining, onJoin, onShowReturning, dbCount, hasAccounts,
}: {
  name: string;
  setName: (s: string) => void;
  joining: boolean;
  onJoin: () => void;
  onShowReturning: () => void;
  dbCount: number;
  hasAccounts: boolean;
}) {
  return (
    <div className="space-y-5">
      <h2 className="jeo-headline text-2xl text-center text-[var(--jeo-gold)] uppercase tracking-wider">
        Join Game
      </h2>
      <input
        className="jeo-input w-full px-4 py-3 rounded-lg text-xl"
        placeholder="Your name"
        value={name}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && name.trim() && onJoin()}
        disabled={joining}
      />
      <button
        onClick={onJoin}
        disabled={joining || !name.trim()}
        className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
      >
        {joining ? 'Joining...' : 'Join'}
      </button>
      <button
        onClick={onShowReturning}
        className="block w-full text-center text-blue-200/70 hover:text-[var(--jeo-gold)] jeo-headline text-xs uppercase tracking-[0.25em] transition"
      >
        Returning player?{hasAccounts ? '' : ' (create account)'}
      </button>
      <p className="text-center text-xs text-blue-200/60 jeo-headline tracking-widest uppercase">
        {dbCount} games available
      </p>
    </div>
  );
}

function ReturningPlayerList({
  accounts, onPick, onCreate, onEdit, onShowNew,
}: {
  accounts: Account[];
  onPick: (a: Account) => void;
  onCreate: () => void;
  onEdit: (a: Account) => void;
  onShowNew: () => void;
}) {
  return (
    <div className="space-y-5">
      <h2 className="jeo-headline text-2xl text-center text-[var(--jeo-gold)] uppercase tracking-wider">
        Welcome Back
      </h2>
      <p className="text-center text-xs text-blue-200/60 jeo-headline tracking-[0.2em] uppercase">
        Tap your account to join
      </p>

      <div className="grid grid-cols-2 gap-3">
        {accounts.map(a => (
          <div key={a.id} className="relative">
            <button
              onClick={() => onPick(a)}
              className="w-full jeo-tile rounded-xl p-3 flex flex-col items-center gap-2 hover:ring-2 hover:ring-[var(--jeo-gold)] transition"
            >
              <Avatar account={a} size={64} />
              <span className="font-semibold text-white truncate w-full text-center">{a.name}</span>
              <span className="text-[10px] jeo-headline tracking-widest uppercase text-blue-200/60">
                {a.wins} {a.wins === 1 ? 'win' : 'wins'}
              </span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onEdit(a); }}
              aria-label="Edit account"
              className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full bg-[rgba(12,16,46,0.85)] border border-[var(--jeo-gold)]/40 text-blue-200/80 hover:text-[var(--jeo-gold)] text-xs"
            >
              ✎
            </button>
          </div>
        ))}

        <button
          onClick={onCreate}
          className="jeo-tile rounded-xl p-3 flex flex-col items-center justify-center gap-1 border-dashed text-[var(--jeo-gold)] hover:ring-2 hover:ring-[var(--jeo-gold)] transition"
          style={{ minHeight: 112 }}
        >
          <span className="text-3xl leading-none">+</span>
          <span className="jeo-headline text-[10px] tracking-[0.2em] uppercase">Create account</span>
        </button>
      </div>

      <button
        onClick={onShowNew}
        className="block w-full text-center text-blue-200/55 hover:text-blue-200/90 jeo-headline text-[11px] uppercase tracking-[0.25em] py-1 transition"
      >
        Join as guest →
      </button>
    </div>
  );
}

function AccountForm({
  initial, onCancel, onCreate, onUpdate, onDelete, onDone,
}: {
  initial: Account | null;
  onCancel: () => void;
  onCreate: (name: string, avatar?: string) => Promise<Account | null>;
  onUpdate: (id: string, patch: { name?: string; avatar?: string }) => Promise<Account | null>;
  onDelete?: () => void;
  onDone: (acct: Account | null, joinNow: boolean) => void;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [avatar, setAvatar] = useState<string | undefined>(initial?.avatar);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const url = await fileToAvatarDataURL(file);
      setAvatar(url);
    } catch {
      setErr('Could not load that image');
    } finally {
      setBusy(false);
    }
  };

  const save = async (joinNow: boolean) => {
    const trimmed = name.trim();
    if (!trimmed) { setErr('Please enter a name'); return; }
    setBusy(true); setErr(null);
    const result = initial
      ? await onUpdate(initial.id, { name: trimmed, avatar: avatar ?? '' })
      : await onCreate(trimmed, avatar);
    setBusy(false);
    if (!result) {
      setErr(initial ? 'Could not update' : 'Could not create (name in use?)');
      return;
    }
    onDone(result, joinNow);
  };

  return (
    <div className="space-y-5">
      <h2 className="jeo-headline text-2xl text-center text-[var(--jeo-gold)] uppercase tracking-wider">
        {initial ? 'Edit Account' : 'New Account'}
      </h2>

      <div className="flex flex-col items-center gap-3">
        <button
          onClick={() => fileRef.current?.click()}
          className="rounded-full w-24 h-24 flex items-center justify-center overflow-hidden border-2 border-[var(--jeo-gold)]/50 hover:border-[var(--jeo-gold)] transition relative"
          style={{ background: 'rgba(12,16,46,0.7)' }}
        >
          {avatar
            ? <img src={avatar} alt="" className="w-full h-full object-cover" />
            : <span className="text-3xl text-blue-200/70">+</span>}
          <span className="absolute bottom-0 inset-x-0 jeo-headline text-[9px] tracking-widest uppercase text-[var(--jeo-gold)] bg-[rgba(12,16,46,0.85)] py-0.5">
            {avatar ? 'Change' : 'Photo'}
          </span>
        </button>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={onPickFile} />
      </div>

      <input
        className="jeo-input w-full px-4 py-3 rounded-lg text-xl"
        placeholder="Your name"
        value={name}
        onChange={e => setName(e.target.value)}
        autoFocus
      />

      {err && <p className="text-center text-sm text-red-300">{err}</p>}

      <div className="space-y-2">
        <button
          onClick={() => save(true)}
          disabled={busy || !name.trim()}
          className="jeo-btn-gold w-full py-3 text-lg rounded-lg"
        >
          Save &amp; Join
        </button>
        <button
          onClick={() => save(false)}
          disabled={busy || !name.trim()}
          className="w-full py-2 text-sm rounded-lg border border-[var(--jeo-gold)]/40 text-[var(--jeo-gold)]/90 hover:text-[var(--jeo-gold)] transition"
        >
          Save for later
        </button>
        <button
          onClick={onCancel}
          className="w-full py-2 text-xs jeo-headline tracking-widest uppercase text-blue-200/55 hover:text-blue-200/90 transition"
        >
          Cancel
        </button>
        {onDelete && (
          <button
            onClick={() => { if (confirm('Delete this account?')) onDelete(); }}
            className="w-full text-center text-[11px] text-red-300/70 hover:text-red-300 jeo-headline tracking-widest uppercase py-1 transition"
          >
            Delete account
          </button>
        )}
      </div>
    </div>
  );
}

// --- post-join (in-lobby) view -------------------------------------------

function InLobby({
  state, player, playerId, isHost, canStart, count, onStart, onResetLobby, onRename,
}: {
  state: GameState;
  player: Player;
  playerId: string | null;
  isHost: boolean;
  canStart: boolean;
  count: number;
  onStart: () => void;
  onResetLobby?: () => void;
  onRename?: (name: string) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(player.name);
  useEffect(() => { setDraft(player.name); }, [player.name]);

  const submitRename = () => {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== player.name && onRename) onRename(trimmed);
    setRenaming(false);
  };

  return (
    <div className="space-y-5">
      <h2 className="jeo-headline text-2xl text-center text-[var(--jeo-gold)] uppercase tracking-wider">
        Lobby
      </h2>
      <p className="text-center text-blue-200/80 jeo-headline tracking-wide">
        {count} player{count === 1 ? '' : 's'} · need {MIN_PLAYERS}+ to start
      </p>

      <div className="space-y-2">
        {state.players.map(p => {
          const isMe = p.id === playerId;
          return (
            <div
              key={p.id}
              className="flex items-center gap-3 rounded-lg px-3 py-2 bg-[rgba(12,16,46,0.6)] border border-[rgba(0,229,255,0.15)]"
            >
              <div className={`w-2.5 h-2.5 rounded-full ${p.connected ? 'bg-green-400 shadow-[0_0_8px_rgba(74,222,128,0.7)]' : 'bg-gray-500'}`} />
              <Avatar account={p} size={32} />
              {isMe && renaming ? (
                <input
                  autoFocus
                  className="jeo-input flex-1 min-w-0 px-2 py-1 rounded text-sm"
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  onBlur={submitRename}
                  onKeyDown={e => {
                    if (e.key === 'Enter') submitRename();
                    else if (e.key === 'Escape') { setDraft(player.name); setRenaming(false); }
                  }}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => { if (isMe && onRename) { setDraft(p.name); setRenaming(true); } }}
                  className={`font-semibold text-white text-left truncate flex-1 ${isMe && onRename ? 'hover:text-[var(--jeo-gold)] transition' : 'cursor-default'}`}
                  title={isMe && onRename ? 'Tap to edit your name' : undefined}
                >
                  {p.name}{isMe && onRename && <span className="ml-1.5 text-blue-200/40 text-xs">✎</span>}
                </button>
              )}
              {p.isHost && <span className="text-[10px] jeo-headline tracking-widest text-[var(--jeo-gold)] uppercase">Host</span>}
              {isMe && !p.isHost && <span className="text-[10px] jeo-headline tracking-widest text-green-300 uppercase">You</span>}
            </div>
          );
        })}
        {Array.from({ length: Math.max(0, MIN_PLAYERS - count) }).map((_, i) => (
          <div key={`empty-${i}`} className="rounded-lg px-4 py-2.5 border border-dashed border-[rgba(0,229,255,0.2)] text-blue-200/40 text-sm jeo-headline tracking-wider uppercase">
            Waiting for player...
          </div>
        ))}
      </div>

      {isHost ? (
        <button
          onClick={onStart}
          disabled={!canStart}
          className="jeo-btn-gold w-full py-3 text-xl rounded-lg"
        >
          {canStart
            ? `Start Game (${count} player${count > 1 ? 's' : ''})`
            : `Need ${MIN_PLAYERS - count} more`}
        </button>
      ) : (
        <p className="text-center text-blue-200/70 text-sm jeo-headline tracking-widest uppercase">
          Waiting for host to start...
        </p>
      )}

      {onResetLobby && (
        <button
          onClick={onResetLobby}
          className="w-full text-center text-blue-200/55 hover:text-red-300 text-[11px] jeo-headline tracking-widest uppercase py-1 transition"
        >
          Reset lobby (kick everyone)
        </button>
      )}
    </div>
  );
}

// --- small shared bits ----------------------------------------------------

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
    >
      {initials}
    </div>
  );
}
