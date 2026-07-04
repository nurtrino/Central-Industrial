'use client';
import { useEffect, useRef } from 'react';

/**
 * Ambient outer-space backdrop rendered on one fixed, full-screen canvas that
 * sits behind all page content. Twinkling stars with intermittent glint
 * flares, shooting stars / tumbling asteroids every 6–14s, translucent
 * nebulas drifting on slow lissajous paths, two far-off planets (a ringed gas
 * giant and a small ice world) wandering near the screen edges, and the
 * occasional small spaceship cruising across with an engine trail.
 * Pauses when the tab is hidden and honors prefers-reduced-motion.
 */
export default function Starfield() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let W = 0, H = 0, DPR = 1;
    let raf = 0;
    let nextMeteor = 0;
    let nextShip = 0;

    const rand = (a: number, b: number) => a + Math.random() * (b - a);

    interface Star { x: number; y: number; r: number; base: number; amp: number; spd: number; ph: number; glint: number; tint: string; }
    interface Nebula { img: HTMLCanvasElement; sc: number; ax: number; ay: number; sx: number; sy: number; px: number; breath: number; pb: number; }
    interface Meteor { x: number; y: number; vx: number; vy: number; rock: boolean; tail: number; born: number; }
    interface Planet { img: HTMLCanvasElement; ax: number; ay: number; wx: number; wy: number; sx: number; sy: number; ph: number; size: number; alpha: number; }
    interface Ship { x: number; y: number; vx: number; size: number; bobPh: number; born: number; }

    let stars: Star[] = [];
    let nebulas: Nebula[] = [];
    let planets: Planet[] = [];
    const meteors: Meteor[] = [];
    const ships: Ship[] = [];

    function makeNebulaSprite(hexA: string, hexB: string): HTMLCanvasElement {
      const s = 512;
      const c = document.createElement('canvas');
      c.width = c.height = s;
      const g = c.getContext('2d')!;
      let gr = g.createRadialGradient(s * 0.42, s * 0.46, 10, s * 0.42, s * 0.46, s * 0.5);
      gr.addColorStop(0, hexA); gr.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = gr; g.fillRect(0, 0, s, s);
      gr = g.createRadialGradient(s * 0.62, s * 0.58, 10, s * 0.62, s * 0.58, s * 0.38);
      gr.addColorStop(0, hexB); gr.addColorStop(1, 'rgba(0,0,0,0)');
      g.globalCompositeOperation = 'screen'; g.fillStyle = gr; g.fillRect(0, 0, s, s);
      return c;
    }

    // Pre-render a planet sprite: lit sphere (light from upper-left), optional
    // cloud bands, terminator shading, and an optional tilted ring drawn in two
    // passes (back half behind the sphere, front half over it).
    function makePlanetSprite(colA: string, colB: string, ringed: boolean, banded: boolean): HTMLCanvasElement {
      const s = 360;
      const c = document.createElement('canvas');
      c.width = c.height = s;
      const g = c.getContext('2d')!;
      const cx = s / 2, cy = s / 2;
      const r = ringed ? s * 0.21 : s * 0.3;

      const ring = (from: number, to: number) => {
        g.save();
        g.translate(cx, cy); g.rotate(-0.42); g.scale(1, 0.30);
        for (let i = 0; i < 3; i++) {
          const rr = r * (1.55 + i * 0.22);
          g.globalAlpha = 0.5 - i * 0.13;
          g.strokeStyle = i === 1 ? 'rgba(210,190,255,0.9)' : 'rgba(150,170,230,0.8)';
          g.lineWidth = 7 - i * 1.6;
          g.beginPath(); g.arc(0, 0, rr, from, to); g.stroke();
        }
        g.restore();
        g.globalAlpha = 1;
      };

      if (ringed) ring(Math.PI, Math.PI * 2); // back half first

      // sphere
      let gr = g.createRadialGradient(cx - r * 0.4, cy - r * 0.4, r * 0.1, cx, cy, r * 1.05);
      gr.addColorStop(0, colA); gr.addColorStop(0.72, colB); gr.addColorStop(1, 'rgba(4,6,20,0.95)');
      g.fillStyle = gr;
      g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill();

      if (banded) {
        g.save();
        g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.clip();
        for (let i = 0; i < 5; i++) {
          const by = cy - r + (i + 0.7) * (r * 2 / 6) + Math.sin(i * 2.3) * 4;
          g.globalAlpha = 0.10 + (i % 2) * 0.07;
          g.fillStyle = i % 2 ? 'rgba(255,255,255,0.7)' : 'rgba(20,10,60,0.8)';
          g.beginPath(); g.ellipse(cx, by, r * 1.04, r * 0.10 + (i % 3) * 2, -0.06, 0, Math.PI * 2); g.fill();
        }
        g.restore();
        g.globalAlpha = 1;
      }

      // terminator (night side) from lower-right
      gr = g.createRadialGradient(cx + r * 0.55, cy + r * 0.55, r * 0.2, cx + r * 0.2, cy + r * 0.2, r * 1.35);
      gr.addColorStop(0, 'rgba(3,4,16,0.85)'); gr.addColorStop(0.55, 'rgba(3,4,16,0.25)'); gr.addColorStop(1, 'rgba(3,4,16,0)');
      g.save();
      g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.clip();
      g.fillStyle = gr; g.fillRect(0, 0, s, s);
      g.restore();

      if (ringed) ring(0, Math.PI); // front half over the sphere
      return c;
    }

    // A small neon dart cruising by, with a cyan engine trail and a flickering
    // exhaust. Rare 2-ship formations. Drawn pointing toward +x, then mirrored
    // for right-to-left runs.
    function drawShip(sp: Ship, t: number) {
      const bob = Math.sin(t * 1.8 + sp.bobPh) * 5;
      const y = sp.y + bob;
      const dir = sp.vx >= 0 ? 1 : -1;
      const sSize = sp.size;

      ctx!.save();
      ctx!.translate(sp.x, y);
      ctx!.scale(dir, 1);

      // engine trail
      const trailLen = sSize * (3.2 + Math.sin(t * 22 + sp.bobPh) * 0.35);
      const tg = ctx!.createLinearGradient(-sSize * 0.8, 0, -sSize * 0.8 - trailLen, 0);
      tg.addColorStop(0, 'rgba(0,229,255,0.55)');
      tg.addColorStop(1, 'rgba(0,229,255,0)');
      ctx!.strokeStyle = tg; ctx!.lineWidth = 2.2; ctx!.lineCap = 'round';
      ctx!.globalAlpha = 0.85;
      ctx!.beginPath(); ctx!.moveTo(-sSize * 0.8, 0); ctx!.lineTo(-sSize * 0.8 - trailLen, 0); ctx!.stroke();

      // exhaust flicker
      const er = sSize * 0.26 + Math.sin(t * 30 + sp.born) * sSize * 0.07;
      const eg = ctx!.createRadialGradient(-sSize * 0.78, 0, 0, -sSize * 0.78, 0, er * 2.4);
      eg.addColorStop(0, 'rgba(190,250,255,0.95)'); eg.addColorStop(1, 'rgba(0,229,255,0)');
      ctx!.fillStyle = eg;
      ctx!.beginPath(); ctx!.arc(-sSize * 0.78, 0, er * 2.4, 0, Math.PI * 2); ctx!.fill();

      // hull
      ctx!.globalAlpha = 0.95;
      ctx!.fillStyle = '#8fa8cc';
      ctx!.strokeStyle = 'rgba(0,229,255,0.85)';
      ctx!.lineWidth = 1;
      ctx!.beginPath();
      ctx!.moveTo(sSize, 0);
      ctx!.lineTo(-sSize * 0.7, -sSize * 0.46);
      ctx!.lineTo(-sSize * 0.45, 0);
      ctx!.lineTo(-sSize * 0.7, sSize * 0.46);
      ctx!.closePath();
      ctx!.fill(); ctx!.stroke();

      // cockpit dome
      ctx!.fillStyle = 'rgba(191,244,255,0.95)';
      ctx!.beginPath(); ctx!.arc(sSize * 0.22, -sSize * 0.06, sSize * 0.16, 0, Math.PI * 2); ctx!.fill();

      ctx!.restore();
      ctx!.globalAlpha = 1;
    }

    function init() {
      DPR = Math.min(window.devicePixelRatio || 1, 2);
      W = window.innerWidth; H = window.innerHeight;
      canvas!.width = W * DPR; canvas!.height = H * DPR;
      canvas!.style.width = W + 'px'; canvas!.style.height = H + 'px';
      ctx!.setTransform(DPR, 0, 0, DPR, 0, 0);
      const n = Math.round(Math.min(320, (W * H) / 5500));
      const tints = ['#dff2ff', '#cfe0ff', '#ffe9d6', '#d8fff4', '#f4e6ff'];
      stars = Array.from({ length: n }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        r: rand(0.4, 1.7), base: rand(0.25, 0.85), amp: rand(0.1, 0.4),
        spd: rand(0.4, 1.6), ph: rand(0, Math.PI * 2), glint: 0,
        tint: tints[Math.floor(Math.random() * tints.length)],
      }));
      nebulas = [
        { img: makeNebulaSprite('rgba(123,92,255,0.55)', 'rgba(0,180,255,0.35)'), sc: rand(1.4, 1.9), ax: W * 0.32, ay: H * 0.22, sx: 0.021, sy: 0.014, px: 0, breath: 0.017, pb: 1.2 },
        { img: makeNebulaSprite('rgba(255,47,214,0.45)', 'rgba(123,92,255,0.35)'), sc: rand(1.2, 1.7), ax: W * 0.36, ay: H * 0.26, sx: 0.016, sy: 0.023, px: 2.2, breath: 0.013, pb: 2.0 },
        { img: makeNebulaSprite('rgba(0,229,255,0.4)', 'rgba(60,255,190,0.22)'), sc: rand(1.0, 1.5), ax: W * 0.4, ay: H * 0.3, sx: 0.012, sy: 0.018, px: 4.1, breath: 0.010, pb: 4.6 },
      ];
      // Two far-off planets, kept near the edges (away from the board) and
      // wandering slowly around their anchor so they never crowd the content.
      planets = [
        { img: makePlanetSprite('#b58bff', '#3a2a86', true,  true),  ax: W * 0.86, ay: H * 0.16, wx: 26, wy: 18, sx: 0.007, sy: 0.005, ph: 0.8, size: Math.min(W, H) * 0.34, alpha: 0.5 },
        { img: makePlanetSprite('#9fe8ff', '#1f4e78', false, false), ax: W * 0.09, ay: H * 0.74, wx: 20, wy: 24, sx: 0.005, sy: 0.008, ph: 3.1, size: Math.min(W, H) * 0.16, alpha: 0.42 },
      ];
    }

    function spawnShip(t: number) {
      const fromLeft = Math.random() < 0.5;
      const size = rand(10, 15);
      const speed = rand(65, 130) * (fromLeft ? 1 : -1);
      const y = rand(H * 0.08, H * 0.85);
      const mk = (dy: number, dx: number) => ships.push({
        x: (fromLeft ? -60 : W + 60) + dx, y: y + dy,
        vx: speed, size, bobPh: rand(0, Math.PI * 2), born: t,
      });
      mk(0, 0);
      if (Math.random() < 0.25) mk(rand(18, 30), fromLeft ? -34 : 34); // wingman
      nextShip = t + rand(16, 36);
    }

    function spawnMeteor(t: number) {
      const rock = Math.random() < 0.3;
      const fromLeft = Math.random() < 0.5;
      const y0 = rand(-H * 0.1, H * 0.45);
      const speed = rand(0.55, 0.95) * Math.max(W, H);
      const ang = rand(0.3, 0.62) * (Math.PI / 2);
      const m: Meteor = {
        x: fromLeft ? -60 : W + 60, y: y0,
        vx: Math.cos(ang) * speed * (fromLeft ? 1 : -1),
        vy: Math.abs(Math.sin(ang)) * speed * 0.55,
        rock, tail: rock ? rand(70, 120) : rand(130, 230), born: t,
      };
      meteors.push(m);
      nextMeteor = t + rand(6, 14);
    }

    function draw(t: number) {
      ctx!.clearRect(0, 0, W, H);
      // nebulas
      ctx!.globalCompositeOperation = 'screen';
      for (const nb of nebulas) {
        const cx = W * 0.5 + nb.ax * Math.sin(t * nb.sx + nb.px);
        const cy = H * 0.42 + nb.ay * Math.sin(t * nb.sy + nb.px * 1.7);
        const env = Math.max(0, Math.sin(t * nb.breath + nb.pb));
        ctx!.globalAlpha = 0.24 * env;
        const s = Math.max(W, H) * nb.sc;
        ctx!.drawImage(nb.img, cx - s / 2, cy - s / 2, s, s);
      }
      ctx!.globalCompositeOperation = 'source-over';
      // stars
      for (const st of stars) {
        let a = st.base + st.amp * Math.sin(t * st.spd + st.ph);
        if (!reduced && Math.random() < 0.00045) st.glint = 1;
        if (st.glint > 0.02) { a = Math.min(1, a + st.glint); st.glint *= 0.93; }
        ctx!.globalAlpha = Math.max(0, Math.min(1, a));
        ctx!.fillStyle = st.tint;
        ctx!.beginPath(); ctx!.arc(st.x, st.y, st.r, 0, Math.PI * 2); ctx!.fill();
        if (st.glint > 0.25 && st.r > 1) {
          ctx!.globalAlpha = st.glint * 0.6; ctx!.strokeStyle = '#bfeaff'; ctx!.lineWidth = 0.7;
          const f = st.r * 6 * st.glint;
          ctx!.beginPath();
          ctx!.moveTo(st.x - f, st.y); ctx!.lineTo(st.x + f, st.y);
          ctx!.moveTo(st.x, st.y - f); ctx!.lineTo(st.x, st.y + f);
          ctx!.stroke();
        }
      }
      // planets — above the stars (they're closer), below meteors/ships
      for (const pl of planets) {
        const px = pl.ax + pl.wx * Math.sin(t * pl.sx + pl.ph);
        const py = pl.ay + pl.wy * Math.sin(t * pl.sy + pl.ph * 1.7);
        ctx!.globalAlpha = pl.alpha;
        ctx!.drawImage(pl.img, px - pl.size / 2, py - pl.size / 2, pl.size, pl.size);
      }
      ctx!.globalAlpha = 1;
      // meteors / asteroids
      if (!reduced && t > nextMeteor) spawnMeteor(t);
      for (let i = meteors.length - 1; i >= 0; i--) {
        const m = meteors[i];
        m.x += m.vx / 60; m.y += m.vy / 60;
        if (m.x < -300 || m.x > W + 300 || m.y > H + 300) { meteors.splice(i, 1); continue; }
        const mag = Math.hypot(m.vx, m.vy), ux = m.vx / mag, uy = m.vy / mag;
        const tx = m.x - ux * m.tail, ty = m.y - uy * m.tail;
        const grad = ctx!.createLinearGradient(m.x, m.y, tx, ty);
        if (m.rock) { grad.addColorStop(0, 'rgba(255,176,96,.85)'); grad.addColorStop(1, 'rgba(255,80,40,0)'); }
        else { grad.addColorStop(0, 'rgba(220,246,255,.9)'); grad.addColorStop(1, 'rgba(120,180,255,0)'); }
        ctx!.strokeStyle = grad; ctx!.lineWidth = m.rock ? 2.4 : 1.6; ctx!.lineCap = 'round';
        ctx!.globalAlpha = 0.9;
        ctx!.beginPath(); ctx!.moveTo(m.x, m.y); ctx!.lineTo(tx, ty); ctx!.stroke();
        const hg = ctx!.createRadialGradient(m.x, m.y, 0, m.x, m.y, m.rock ? 7 : 5);
        hg.addColorStop(0, m.rock ? 'rgba(255,220,170,.95)' : 'rgba(255,255,255,.95)');
        hg.addColorStop(1, 'rgba(255,255,255,0)');
        ctx!.fillStyle = hg; ctx!.beginPath(); ctx!.arc(m.x, m.y, m.rock ? 7 : 5, 0, Math.PI * 2); ctx!.fill();
        if (m.rock) {
          ctx!.globalAlpha = 0.95; ctx!.fillStyle = '#4a3a33';
          ctx!.save(); ctx!.translate(m.x, m.y); ctx!.rotate(t * 3 + m.born);
          ctx!.beginPath(); ctx!.moveTo(3, 0); ctx!.lineTo(1.4, 2.6); ctx!.lineTo(-2.4, 1.8);
          ctx!.lineTo(-3, -1.2); ctx!.lineTo(0.6, -2.8); ctx!.closePath(); ctx!.fill(); ctx!.restore();
        }
      }
      // spaceships — the occasional cruiser, front-most layer
      if (!reduced && t > nextShip) spawnShip(t);
      for (let i = ships.length - 1; i >= 0; i--) {
        const sp = ships[i];
        sp.x += sp.vx / 60;
        if (sp.x < -140 || sp.x > W + 140) { ships.splice(i, 1); continue; }
        drawShip(sp, t);
      }
      ctx!.globalAlpha = 1;
    }

    function loop(ms: number) { draw(ms / 1000); raf = requestAnimationFrame(loop); }

    const onResize = () => init();
    const onVis = () => {
      if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
      else if (!raf && !reduced) raf = requestAnimationFrame(loop);
    };
    window.addEventListener('resize', onResize);
    document.addEventListener('visibilitychange', onVis);

    init();
    if (reduced) draw(1);
    else raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, []);

  return <canvas ref={canvasRef} aria-hidden className="fixed inset-0 -z-10 block" />;
}
