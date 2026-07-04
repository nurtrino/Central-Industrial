'use client';

/**
 * Decorative animation layer for HYPER MODE surfaces: drifting/breathing
 * nebula blobs, periodic shooting stars, and little rockets cruising across.
 * Pure CSS (keyframes in globals.css), pointer-events-none, aria-hidden, and
 * fully disabled under prefers-reduced-motion.
 *
 * Densities:
 *   full — TV takeover area (2 nebulas, 3 stars, 2 rockets)
 *   lite — phone modal backdrop (1 nebula, 2 stars, 1 rocket)
 *   card — inside the stage card, behind content (2 soft nebulas, 1 star)
 */
export default function HyperFlair({ density = 'full' }: { density?: 'full' | 'lite' | 'card' }) {
  return (
    <div aria-hidden className="hf-layer pointer-events-none absolute inset-0 overflow-hidden rounded-[inherit] z-0">
      <div className="hf-nebula hf-n1" />
      {density !== 'lite' && <div className="hf-nebula hf-n2" />}
      <div className="hf-star hf-s1" />
      {density !== 'card' && <div className="hf-star hf-s2" />}
      {density === 'full' && <div className="hf-star hf-s3" />}
      {density !== 'card' && <span className="hf-rocket hf-r1">🚀</span>}
      {density === 'full' && <span className="hf-rocket hf-r2">🚀</span>}
    </div>
  );
}
