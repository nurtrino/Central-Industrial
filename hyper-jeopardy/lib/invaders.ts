// SPACE INVADERS AMBUSH — server-side battle engine.
//
// At a random point in Double Jeopardy the board is ambushed: every player's
// score panel "becomes" a ship (rendered on the shared screen), controlled
// from their phone (◀ ▶ hold-to-move + FIRE). One classic wave of invaders
// marches back and forth, descending; clear it together and the trivia game
// resumes. If the whole fleet is destroyed — or the invaders land — the ENTIRE
// game ends at current scores (no Final Jeopardy).
//
// The engine is authoritative and pure-ish: gameServer runs tickBattle() on an
// interval and broadcasts battleSnapshot() (compact — invaders move as a block,
// so ticks carry just the block origin + per-row alive bitmasks). This module
// has no server-only imports, so clients may import its constants/types.

// ── field geometry (all coordinates in 0–100 field units) ───────────────────
export const INV_COLS = 8;
export const INV_ROWS = 4;
export const INV_SPACING_X = 8;
export const INV_SPACING_Y = 6.5;
export const INV_W = 5;        // invader hitbox
export const INV_H = 3.8;
export const SHIP_Y = 91;      // ships' fixed row
export const SHIP_W = 6;
export const SHIP_H = 3.2;
export const SHIP_LIVES = 2;

const EDGE_MIN_X = 2;
const EDGE_MAX_X = 98 - ((INV_COLS - 1) * INV_SPACING_X + INV_W);
const BLOCK_START_X = 12;
const BLOCK_START_Y = 12;
const STEP_DX = 1.8;           // horizontal march step
const STEP_DY = 3.4;           // descend on edge bounce
const LAND_Y = SHIP_Y - 5;     // block bottom reaching this = invaders land (lose)

const INTRO_MS = 3_000;        // "AMBUSH" splash before control unlocks
const SHIP_SPEED = 34;         // units/sec while holding
const SHIP_BULLET_VY = -60;
const INV_BULLET_VY = 24;
const FIRE_COOLDOWN_MS = 380;
const MAX_BULLETS_PER_SHIP = 2;
const INVULN_MS = 2_000;       // blink window after losing a life

// march cadence scales with remaining invaders (classic speed-up)
const MARCH_MS_SLOW = 760;
const MARCH_MS_FAST = 110;

// test/dev acceleration knob (INVADERS_FAST=1): everything meaner, faster
const FAST = typeof process !== 'undefined' && process.env?.INVADERS_FAST === '1';
const MARCH_SCALE = FAST ? 0.32 : 1;
const INV_FIRE_MIN_MS = FAST ? 420 : 1_000;
const INV_FIRE_MAX_MS = FAST ? 950 : 2_200;

export const SHIP_COLORS = ['#00e5ff', '#ff2fd6', '#7dffb2', '#ffc43c', '#ff7d5c', '#b58bff'];

// ── state ────────────────────────────────────────────────────────────────────
export type BattleStatus = 'intro' | 'playing' | 'won' | 'lost';

export interface InvShip {
  id: string;        // player id at battle start (control also matches by name)
  name: string;
  color: string;
  x: number;
  vx: -1 | 0 | 1;
  lives: number;
  alive: boolean;
  cdUntil: number;
  invulnUntil: number;
}

interface InvBullet {
  x: number;
  y: number;
  vy: number;
  ship: number; // shooter ship index, or -1 for an invader bullet
}

export interface Battle {
  status: BattleStatus;
  ships: InvShip[];
  bullets: InvBullet[];
  alive: boolean[];      // INV_COLS * INV_ROWS, index = row * INV_COLS + col
  aliveCount: number;
  bx: number;            // block origin
  by: number;
  dir: 1 | -1;
  step: number;          // increments per march step → drives the 4-note sound
  lastMarchAt: number;
  nextInvFireAt: number;
  shots: number;         // cumulative player shots → clients play "pew" on diff
  introUntil: number;
  lastTickAt: number;
}

// Compact tick payload (broadcast ~20×/s — keep it tiny).
export interface InvSnapshot {
  st: BattleStatus;
  bx: number;
  by: number;
  step: number;
  a: number[];                              // per-row alive bitmask (bit col = alive)
  sh: [number, number, number, number][];   // [x×10, lives, alive, invuln] per ship
  b: [number, number, number][];            // [x×10, y×10, isInvaderBullet]
  shots: number;
  n: number;                                // invaders remaining
}

const rand = (a: number, b: number) => a + Math.random() * (b - a);

export function initBattle(players: { id: string; name: string }[], now: number): Battle {
  const n = Math.max(1, players.length);
  return {
    status: 'intro',
    ships: players.map((p, i) => ({
      id: p.id,
      name: p.name,
      color: SHIP_COLORS[i % SHIP_COLORS.length],
      x: (100 * (i + 1)) / (n + 1),
      vx: 0,
      lives: SHIP_LIVES,
      alive: true,
      cdUntil: 0,
      invulnUntil: 0,
    })),
    bullets: [],
    alive: Array(INV_COLS * INV_ROWS).fill(true),
    aliveCount: INV_COLS * INV_ROWS,
    bx: BLOCK_START_X,
    by: BLOCK_START_Y,
    dir: 1,
    step: 0,
    lastMarchAt: now + INTRO_MS,
    nextInvFireAt: now + INTRO_MS + 1200,
    shots: 0,
    introUntil: now + INTRO_MS,
    lastTickAt: now,
  };
}

// Phone input. Matching falls back to the player NAME so a mid-battle
// reconnect (new socket id) keeps control of its ship.
export function battleControl(
  b: Battle,
  playerId: string,
  playerName: string | undefined,
  action: string,
  now: number,
): void {
  if (b.status !== 'playing') return;
  const ship =
    b.ships.find(s => s.id === playerId) ??
    (playerName ? b.ships.find(s => s.name === playerName) : undefined);
  if (!ship || !ship.alive) return;

  if (action === 'L') ship.vx = -1;
  else if (action === 'R') ship.vx = 1;
  else if (action === 'S') ship.vx = 0;
  else if (action === 'F') {
    if (now < ship.cdUntil) return;
    const idx = b.ships.indexOf(ship);
    const active = b.bullets.filter(bl => bl.ship === idx).length;
    if (active >= MAX_BULLETS_PER_SHIP) return;
    b.bullets.push({ x: ship.x, y: SHIP_Y - SHIP_H, vy: SHIP_BULLET_VY, ship: idx });
    ship.cdUntil = now + FIRE_COOLDOWN_MS;
    b.shots += 1;
  }
}

function marchInterval(aliveCount: number): number {
  const t = Math.max(0, Math.min(1, (aliveCount - 1) / (INV_COLS * INV_ROWS - 1)));
  return (MARCH_MS_FAST + (MARCH_MS_SLOW - MARCH_MS_FAST) * t) * MARCH_SCALE;
}

function blockBottom(b: Battle): number {
  let last = 0;
  for (let r = INV_ROWS - 1; r >= 0; r--) {
    for (let c = 0; c < INV_COLS; c++) if (b.alive[r * INV_COLS + c]) { last = r; r = -1; break; }
  }
  return b.by + last * INV_SPACING_Y + INV_H;
}

export function tickBattle(b: Battle, now: number): void {
  const dt = Math.min(0.2, Math.max(0, (now - b.lastTickAt) / 1000));
  b.lastTickAt = now;
  if (b.status === 'won' || b.status === 'lost') return;
  if (b.status === 'intro') {
    if (now >= b.introUntil) b.status = 'playing';
    else return;
  }

  // ships move while held
  for (const s of b.ships) {
    if (!s.alive || s.vx === 0) continue;
    s.x = Math.max(3, Math.min(97, s.x + s.vx * SHIP_SPEED * dt));
  }

  // invader march: one block step per interval; descend + flip at the edges
  if (now - b.lastMarchAt >= marchInterval(b.aliveCount)) {
    b.lastMarchAt = now;
    const nx = b.bx + b.dir * STEP_DX;
    if (nx < EDGE_MIN_X || nx > EDGE_MAX_X) {
      b.dir = (b.dir * -1) as 1 | -1;
      b.by += STEP_DY;
    } else {
      b.bx = nx;
    }
    b.step += 1;
    if (blockBottom(b) >= LAND_Y) { b.status = 'lost'; return; } // they landed
  }

  // invader fire: bottom-most alive invader of a random occupied column
  if (now >= b.nextInvFireAt) {
    b.nextInvFireAt = now + rand(INV_FIRE_MIN_MS, INV_FIRE_MAX_MS);
    const cols: number[] = [];
    for (let c = 0; c < INV_COLS; c++) {
      for (let r = 0; r < INV_ROWS; r++) if (b.alive[r * INV_COLS + c]) { cols.push(c); break; }
    }
    if (cols.length) {
      const c = cols[Math.floor(Math.random() * cols.length)];
      let br = -1;
      for (let r = INV_ROWS - 1; r >= 0; r--) if (b.alive[r * INV_COLS + c]) { br = r; break; }
      if (br >= 0) {
        b.bullets.push({
          x: b.bx + c * INV_SPACING_X + INV_W / 2,
          y: b.by + br * INV_SPACING_Y + INV_H,
          vy: INV_BULLET_VY * (FAST ? 1.25 : 1),
          ship: -1,
        });
      }
    }
  }

  // bullets fly + collide
  for (let i = b.bullets.length - 1; i >= 0; i--) {
    const bl = b.bullets[i];
    bl.y += bl.vy * dt;
    if (bl.y < -3 || bl.y > 103) { b.bullets.splice(i, 1); continue; }

    if (bl.ship >= 0) {
      // player bullet vs invaders
      let hit = false;
      for (let r = 0; r < INV_ROWS && !hit; r++) {
        for (let c = 0; c < INV_COLS && !hit; c++) {
          const idx = r * INV_COLS + c;
          if (!b.alive[idx]) continue;
          const ix = b.bx + c * INV_SPACING_X;
          const iy = b.by + r * INV_SPACING_Y;
          if (bl.x >= ix - 0.6 && bl.x <= ix + INV_W + 0.6 && bl.y >= iy - 0.5 && bl.y <= iy + INV_H + 0.5) {
            b.alive[idx] = false;
            b.aliveCount -= 1;
            b.bullets.splice(i, 1);
            hit = true;
          }
        }
      }
      if (b.aliveCount <= 0) { b.status = 'won'; return; }
    } else {
      // invader bullet vs ships
      for (const s of b.ships) {
        if (!s.alive || now < s.invulnUntil) continue;
        if (Math.abs(bl.x - s.x) <= SHIP_W / 2 + 0.6 && bl.y >= SHIP_Y - SHIP_H && bl.y <= SHIP_Y + 2) {
          s.lives -= 1;
          s.invulnUntil = now + INVULN_MS;
          s.vx = 0;
          if (s.lives <= 0) s.alive = false;
          b.bullets.splice(i, 1);
          break;
        }
      }
      if (b.ships.every(s => !s.alive)) { b.status = 'lost'; return; } // fleet destroyed
    }
  }
}

export function battleSnapshot(b: Battle): InvSnapshot {
  const a: number[] = [];
  for (let r = 0; r < INV_ROWS; r++) {
    let m = 0;
    for (let c = 0; c < INV_COLS; c++) if (b.alive[r * INV_COLS + c]) m |= 1 << c;
    a.push(m);
  }
  const now = b.lastTickAt;
  return {
    st: b.status,
    bx: Math.round(b.bx * 10) / 10,
    by: Math.round(b.by * 10) / 10,
    step: b.step,
    a,
    sh: b.ships.map(s => [
      Math.round(s.x * 10),
      s.lives,
      s.alive ? 1 : 0,
      now < s.invulnUntil ? 1 : 0,
    ]),
    b: b.bullets.map(bl => [Math.round(bl.x * 10), Math.round(bl.y * 10), bl.ship < 0 ? 1 : 0]),
    shots: b.shots,
    n: b.aliveCount,
  };
}
