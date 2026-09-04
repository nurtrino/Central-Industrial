'use client';

import { useEffect, useState } from 'react';

/**
 * "← Special Projects" — the C64-styled button every Central Industrial tool carries in
 * its top-left corner. Links to the hub's /?return entry, which lands on the menu of tools
 * directly (no boot screen, no access-code prompt while the session is valid). The hub URL
 * is fetched at runtime from /api/hub so the same build is right on Render and locally.
 */
export default function HubLink() {
  const [href, setHref] = useState<string>('http://127.0.0.1:5050/?return');
  useEffect(() => {
    let alive = true;
    fetch('/api/hub', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (alive && j && typeof j.url === 'string') setHref(j.url); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return (
    <a
      href={href}
      title="Back to the Special Projects menu"
      style={{
        position: 'fixed', top: 8, left: 8, zIndex: 9999,
        fontFamily: '"Courier New", monospace', fontSize: 11, fontWeight: 700,
        letterSpacing: '.5px', textTransform: 'uppercase', lineHeight: 1,
        color: '#6C5EB5', background: '#352879', border: '2px solid #6C5EB5',
        padding: '5px 8px', textDecoration: 'none', opacity: 0.9,
      }}
    >
      ← Special Projects
    </a>
  );
}
