// Server-side account store. An Account is the canonical identity for a
// player: it owns the name, the avatar, and the win count. All devices share
// the same accounts, so picking "Kristina" on a brand-new phone joins you as
// the same Kristina who has 5 wins on the leaderboard.
//
// Accounts are FULLY SEPARATE from in-game state on purpose:
//   - A permanent Account can only be created via createAccount() (the
//     explicit "Create Account" screen) and can only be renamed/re-avatared
//     via updateAccount() (the explicit "Edit Account" screen).
//   - Nothing that happens during a game — joining as a guest, an in-lobby
//     rename, even winning — can create or silently mutate an Account,
//     except awardWinToAccount(), which only fires for a player who
//     explicitly picked an existing account at join time.
// This boundary is what stops a one-off lobby rename or guest join from
// spawning a duplicate/phantom account.
//
// File-backed at DATA_DIR/accounts.json with a one-time migration from the older
// leaderboard.json shape. DATA_DIR is a persistent disk on Render (see
// lib/dataDir.ts), so wins now survive restarts/redeploys; on an empty disk the
// SEED below bootstraps it.

import fs from 'fs';
import path from 'path';
import { DATA_DIR, ensureDataDir } from './dataDir';

export interface Account {
  id: string;
  name: string;
  avatar?: string;   // data URL, small JPEG (see lib/profile.ts fileToAvatarDataURL)
  wins: number;
  createdAt: number;
}

const ACCOUNTS_FILE = path.join(DATA_DIR, 'accounts.json');
const LEGACY_LEADERBOARD_FILE = path.join(DATA_DIR, 'leaderboard.json');

// Seeded with the user's regulars. Stable ids so refs survive renames.
// These account NAMES are authoritative: reconcileSeedNames() re-applies
// them on every load so editing this list actually updates an already-
// seeded (and otherwise persistent) accounts.json.
const SEED: Account[] = [
  { id: 'a_jim',      name: 'Brad',     wins: 4, createdAt: 0 },
  { id: 'a_chase',    name: 'Chase',    wins: 3, createdAt: 0 },
  { id: 'a_henry',    name: 'Henry',    wins: 0, createdAt: 0 },
  { id: 'a_kristina', name: 'Kristina', wins: 5, createdAt: 0 },
];

// Force the canonical seed accounts to use the seed's display name. Wins and
// avatars are preserved; only the name is reconciled. Returns true if any
// name changed so the caller can persist.
function reconcileSeedNames(accounts: Account[]): boolean {
  let changed = false;
  for (const s of SEED) {
    const acct = accounts.find(a => a.id === s.id);
    if (acct && acct.name !== s.name) { acct.name = s.name; changed = true; }
  }
  return changed;
}

function loadRaw(): Account[] {
  ensureDataDir();
  if (fs.existsSync(ACCOUNTS_FILE)) {
    try {
      const parsed = JSON.parse(fs.readFileSync(ACCOUNTS_FILE, 'utf8')) as Account[];
      if (Array.isArray(parsed)) {
        const valid = parsed.filter(a => a && typeof a.id === 'string' && typeof a.name === 'string');
        // Make the seed names authoritative even on an already-seeded file.
        if (reconcileSeedNames(valid)) save(valid);
        return valid;
      }
    } catch {}
  }

  // First-run bootstrap. If a legacy leaderboard.json exists, fold its
  // {name, points} entries into our seed by case-insensitive name match so
  // any wins already recorded survive the migration.
  let initial: Account[] = [...SEED];
  if (fs.existsSync(LEGACY_LEADERBOARD_FILE)) {
    try {
      const legacy = JSON.parse(fs.readFileSync(LEGACY_LEADERBOARD_FILE, 'utf8')) as { name: string; points: number }[];
      if (Array.isArray(legacy)) {
        initial = SEED.map(s => {
          const m = legacy.find(o => o.name.trim().toLowerCase() === s.name.toLowerCase());
          return m ? { ...s, wins: m.points } : s;
        });
        for (const o of legacy) {
          if (!initial.some(a => a.name.trim().toLowerCase() === o.name.trim().toLowerCase())) {
            initial.push({
              id: 'a_' + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-3),
              name: o.name.trim(),
              wins: o.points,
              createdAt: Date.now(),
            });
          }
        }
      }
    } catch {}
  }
  save(initial);
  return initial;
}

function save(accounts: Account[]): void {
  ensureDataDir();
  fs.writeFileSync(ACCOUNTS_FILE, JSON.stringify(accounts, null, 2));
}

export function getAccounts(): Account[] {
  return [...loadRaw()].sort((a, b) => b.wins - a.wins || a.name.localeCompare(b.name));
}

export function getAccount(id: string): Account | null {
  return loadRaw().find(a => a.id === id) ?? null;
}

function newAccountId(): string {
  return 'a_' + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-4);
}

export function createAccount(name: string, avatar?: string): Account | null {
  const trimmed = name.trim();
  if (!trimmed) return null;
  const list = loadRaw();
  if (list.some(a => a.name.trim().toLowerCase() === trimmed.toLowerCase())) return null;
  const account: Account = { id: newAccountId(), name: trimmed, avatar, wins: 0, createdAt: Date.now() };
  list.push(account);
  save(list);
  return account;
}

export function updateAccount(id: string, patch: { name?: string; avatar?: string }): Account | null {
  const list = loadRaw();
  const acct = list.find(a => a.id === id);
  if (!acct) return null;
  if (patch.name !== undefined) {
    const trimmed = patch.name.trim();
    if (!trimmed) return null;
    if (list.some(a => a.id !== id && a.name.trim().toLowerCase() === trimmed.toLowerCase())) return null;
    acct.name = trimmed;
  }
  if (patch.avatar !== undefined) acct.avatar = patch.avatar || undefined;
  save(list);
  return acct;
}

export function deleteAccount(id: string): boolean {
  const list = loadRaw();
  const idx = list.findIndex(a => a.id === id);
  if (idx === -1) return false;
  list.splice(idx, 1);
  save(list);
  return true;
}

export function awardWinToAccount(id: string): Account | null {
  const list = loadRaw();
  const acct = list.find(a => a.id === id);
  if (!acct) return null;
  acct.wins += 1;
  save(list);
  return acct;
}
