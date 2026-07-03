'use client';
import { useEffect, useRef } from 'react';

/**
 * Ambient outer-space backdrop rendered on one fixed, full-screen canvas that
 * sits behind all page content. Twinkling stars with intermittent glint
 * flares, shooting stars / tumbling asteroids every 6–14s, and translucent
 * nebulas drifting on slow lissajous paths that fade in and out of view.
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

    const rand = (a: number, b: number) => a + Math.random() * (b - a);

    interface Star { x: number; y: number; r: number; base: number; amp: number; spd: number; ph: number; glint: number; tint: string; }
    interface Nebula { img: HTMLCanvasElement; sc: number; ax: number; ay: number; sx: number; sy: number; px: number; breath: number; pb: number; }
    interface Meteor { x: number; y: number; vx: number; vy: number; rock: boolean; tail: number; born: number; }

    let stars: Star[] = [];
    let nebulas: Nebula[] = [];
    const meteors: Meteor[] = [];

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
