'use client';
import { INV_COLS, INV_ROWS, type InvSnapshot } from '@/lib/invaders';
import {
  playSIMarch, playSIShot, playSIBoom, playSIShipBoom, playSILose, playMiniCelebrate,
} from '@/lib/audio';

// Shared tick-diff sound engine for the SPACE INVADERS ambush — used by BOTH
// the TV stage and every phone controller, so the march, shots, explosions and
// the win/lose stingers play on every device in the room. Returns the diffed
// events so the TV can also spawn explosion visuals from them.

export interface InvTickEvents {
  kills: { r: number; c: number }[];        // invaders destroyed this tick
  shipBooms: { idx: number; fatal: boolean }[]; // ships hit (fatal = destroyed)
}

export function applyInvaderTickSfx(
  prev: InvSnapshot | null,
  t: InvSnapshot,
  endSounded: { current: boolean },
): InvTickEvents {
  const ev: InvTickEvents = { kills: [], shipBooms: [] };
  if (!prev) return ev;

  if (t.st === 'playing' && t.step !== prev.step) playSIMarch(t.step);
  if (t.shots > prev.shots) playSIShot();

  for (let r = 0; r < INV_ROWS; r++) {
    const died = prev.a[r] & ~t.a[r];
    if (!died) continue;
    for (let c = 0; c < INV_COLS; c++) if (died & (1 << c)) ev.kills.push({ r, c });
  }
  if (ev.kills.length) playSIBoom();

  t.sh.forEach((sh, i) => {
    const p = prev.sh[i];
    if (!p) return;
    if (sh[1] < p[1] || (p[2] === 1 && sh[2] === 0)) ev.shipBooms.push({ idx: i, fatal: sh[2] === 0 });
  });
  if (ev.shipBooms.length) playSIShipBoom();

  if (t.st !== prev.st && !endSounded.current) {
    if (t.st === 'won') { endSounded.current = true; playMiniCelebrate(); }
    if (t.st === 'lost') { endSounded.current = true; playSILose(); }
  }
  return ev;
}
