'use client';
import { useEffect, useRef, useState } from 'react';
import type { GameState } from '@/lib/gameEngine';
import { getSocket } from '@/lib/socket-client';
import {
  INV_COLS, INV_ROWS, INV_SPACING_X, INV_SPACING_Y, INV_W, INV_H,
  SHIP_Y, SHIP_W, type InvSnapshot,
} from '@/lib/invaders';
import {
  playSIShot, playSIBoom, playSIShipBoom, playSIMarch, playSILose, playMiniCelebrate,
} from '@/lib/audio';

// SPACE INVADERS AMBUSH — the shared-screen battle. A fixed overlay above the
// (dimmed) game board: canvas renders the wave, ships, bullets and explosions
// from the server's 20Hz 'invaders' ticks; DOM renders the crisp banners.
// Sounds are driven by tick diffs: march notes per block step, pew per shot,
// booms per bitmask kill, and win/lose stingers.

// Original retro-style pixel sprites (two march frames + a player cannon).
const ALIEN_A = [
  '..X.....X..',
  '...X...X...',
  '..XXXXXXX..',
  '.XX.XXX.XX.',
  'XXXXXXXXXXX',
  'X.XXXXXXX.X',
  'X.X.....X.X',
  '...XX.XX...',
];
const ALIEN_B = [
  '..X.....X..',
  'X..X...X..X',
  'X.XXXXXXX.X',
  'XXX.XXX.XXX',
  '.XXXXXXXXX.',
  '..XXXXXXX..',
  '..X.....X..',
  '.X.......X.',
];
const CANNON = [
  '.....X.....',
  '....XXX....',
  '....XXX....',
  '.XXXXXXXXX.',
  'XXXXXXXXXXX',
  'XXXXXXXXXXX',
];
const ROW_COLORS = ['#7dffb2', '#00e5ff', '#b58bff', '#ff7ad9'];

interface Boom { x: number; y: number; at: number; big: boolean; color: string }

function drawSprite(
  ctx: CanvasRenderingContext2D,
  sprite: string[],
  x: number, y: number, w: number, h: number, color: string,
) {
  const cols = sprite[0].length, rows = sprite.length;
  const px = w / cols, py = h / rows;
  ctx.fillStyle = color;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (sprite[r][c] === 'X') ctx.fillRect(x + c * px, y + r * py, px + 0.5, py + 0.5);
    }
  }
}

export default function InvadersStage({ state }: { state: GameState }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const snapRef = useRef<InvSnapshot | null>(null);
  const prevRef = useRef<InvSnapshot | null>(null);
  const boomsRef = useRef<Boom[]>([]);
  const endSoundRef = useRef(false);
  const [liveStatus, setLiveStatus] = useState<string>(state.invaders?.status ?? 'intro');
  const roster = state.invaders?.roster ?? [];
  const rosterRef = useRef(roster);
  rosterRef.current = roster;

  // Battle telemetry + tick-diff sound effects.
  useEffect(() => {
    const s = getSocket();
    const onTick = (t: InvSnapshot) => {
      const prev = prevRef.current;
      if (prev) {
        if (t.st === 'playing' && t.step !== prev.step) playSIMarch(t.step);
        if (t.shots > prev.shots) playSIShot();
        for (let r = 0; r < INV_ROWS; r++) {
          const died = prev.a[r] & ~t.a[r];
          if (!died) continue;
          for (let c = 0; c < INV_COLS; c++) {
            if (died & (1 << c)) {
              boomsRef.current.push({
                x: t.bx + c * INV_SPACING_X + INV_W / 2,
                y: t.by + r * INV_SPACING_Y + INV_H / 2,
                at: performance.now(), big: false, color: ROW_COLORS[r % ROW_COLORS.length],
              });
              playSIBoom();
            }
          }
        }
        t.sh.forEach((sh, i) => {
          const p = prev.sh[i];
          if (!p) return;
          if (sh[1] < p[1] || (p[2] === 1 && sh[2] === 0)) {
            boomsRef.current.push({
              x: sh[0] / 10, y: SHIP_Y, at: performance.now(),
              big: sh[2] === 0, color: rosterRef.current[i]?.color ?? '#ffffff',
            });
            playSIShipBoom();
          }
        });
        if (t.st !== prev.st) {
          setLiveStatus(t.st);
          if (t.st === 'won' && !endSoundRef.current) { endSoundRef.current = true; playMiniCelebrate(); }
          if (t.st === 'lost' && !endSoundRef.current) { endSoundRef.current = true; playSILose(); }
        }
      } else {
        setLiveStatus(t.st);
      }
      prevRef.current = t;
      snapRef.current = t;
    };
    s.on('invaders', onTick);
    return () => { s.off('invaders', onTick); };
  }, []);

  // Canvas render loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const DPR = Math.min(window.devicePixelRatio || 1, 2);
      const W = window.innerWidth, H = window.innerHeight;
      if (canvas.width !== W * DPR || canvas.height !== H * DPR) {
        canvas.width = W * DPR; canvas.height = H * DPR;
        canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      }
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const t = snapRef.current;
      if (!t) return;
      const fx = (u: number) => (u / 100) * W;   // field → screen
      const fy = (u: number) => (u / 100) * H;

      // invaders (two-frame march animation, tinted per row)
      const iw = fx(INV_W), ih = fy(INV_H);
      const sprite = t.step % 2 ? ALIEN_B : ALIEN_A;
      for (let r = 0; r < INV_ROWS; r++) {
        for (let c = 0; c < INV_COLS; c++) {
          if (!(t.a[r] & (1 << c))) continue;
          drawSprite(ctx, sprite, fx(t.bx + c * INV_SPACING_X), fy(t.by + r * INV_SPACING_Y), iw, ih, ROW_COLORS[r % ROW_COLORS.length]);
        }
      }

      // bullets
      for (const [bx10, by10, inv] of t.b) {
        const x = fx(bx10 / 10), y = fy(by10 / 10);
        if (inv) {
          ctx.fillStyle = '#ff5c8a';
          const wob = Math.sin(y / 6) * 2;
          ctx.fillRect(x - 1.5 + wob, y - 6, 3, 9);
        } else {
          ctx.fillStyle = '#dffcff';
          ctx.fillRect(x - 1.5, y - 9, 3, 11);
          ctx.fillStyle = 'rgba(0,229,255,0.5)';
          ctx.fillRect(x - 1.5, y + 2, 3, 4);
        }
      }

      // ships (player-colored cannons with name + lives)
      const sw = fx(SHIP_W), sh2 = fy(3.2);
      t.sh.forEach((sdat, i) => {
        const [x10, lives, alive, invuln] = sdat;
        if (!alive) return;
        if (invuln && Math.floor(performance.now() / 120) % 2) return; // hit blink
        const x = fx(x10 / 10), color = rosterRef.current[i]?.color ?? '#8fa8cc';
        drawSprite(ctx, CANNON, x - sw / 2, fy(SHIP_Y) - sh2, sw, sh2, color);
        ctx.font = '700 12px var(--font-oswald), sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = color;
        ctx.fillText((rosterRef.current[i]?.name ?? '').toUpperCase().slice(0, 12), x, fy(SHIP_Y) + 16);
        ctx.fillStyle = '#ff7d92';
        ctx.fillText('♥'.repeat(Math.max(0, lives)), x, fy(SHIP_Y) + 30);
      });

      // explosions: expanding pixel starburst, ~450ms
      const now = performance.now();
      boomsRef.current = boomsRef.current.filter(bm => now - bm.at < 450);
      for (const bm of boomsRef.current) {
        const age = (now - bm.at) / 450;
        const R = (bm.big ? 34 : 20) * age + 4;
        ctx.fillStyle = bm.color;
        ctx.globalAlpha = 1 - age;
        for (let k = 0; k < (bm.big ? 12 : 8); k++) {
          const ang = (k / (bm.big ? 12 : 8)) * Math.PI * 2 + (bm.big ? age : 0);
          const px = fx(bm.x) + Math.cos(ang) * R;
          const py = fy(bm.y) + Math.sin(ang) * R;
          const s = bm.big ? 5 : 4;
          ctx.fillRect(px - s / 2, py - s / 2, s, s);
        }
        ctx.globalAlpha = 1;
      }

      // HUD
      ctx.textAlign = 'left';
      ctx.font = '700 13px var(--font-oswald), sans-serif';
      ctx.fillStyle = 'rgba(159,232,255,0.75)';
      ctx.fillText(`INVADERS REMAINING: ${t.n}`, 18, 26);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="fixed inset-0 z-40">
      {/* dim the board behind — it stays faintly visible under the battle */}
      <div className="absolute inset-0 bg-[rgba(2,3,14,0.84)]" />
      <canvas ref={canvasRef} className="absolute inset-0" />

      {liveStatus === 'intro' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-5 text-center pointer-events-none">
          <p className="jeo-headline uppercase tracking-[0.5em] text-[#ff5c8a] text-2xl mg-urgent">⚠ Ambush ⚠</p>
          <h2 className="hyper-title text-6xl sm:text-8xl">SPACE INVADERS</h2>
          <p className="jeo-headline uppercase tracking-[0.3em] text-blue-100/90 text-xl sm:text-2xl">
            Score panels → battle stations!
          </p>
          <p className="jeo-headline uppercase tracking-[0.25em] text-blue-200/70 text-base">
            ◀ ▶ move · FIRE on your phone — clear the wave together
          </p>
        </div>
      )}

      {liveStatus === 'won' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-center pointer-events-none">
          <h2 className="hyper-title text-6xl sm:text-8xl mg-flash">WAVE CLEARED!</h2>
          <p className="jeo-headline uppercase tracking-[0.3em] text-[var(--neon-lime)] text-2xl">🏆 The fleet survives</p>
          <p className="jeo-headline uppercase tracking-[0.25em] text-blue-200/70 text-base">Back to the board…</p>
        </div>
      )}

      {liveStatus === 'lost' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-center pointer-events-none">
          <h2 className="hyper-title text-6xl sm:text-8xl" style={{ filter: 'hue-rotate(300deg)' }}>THE FLEET HAS FALLEN</h2>
          <p className="jeo-headline uppercase tracking-[0.3em] text-red-300/90 text-2xl">💀 Game over — scores stand</p>
        </div>
      )}
    </div>
  );
}
