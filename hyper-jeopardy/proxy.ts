import { NextRequest, NextResponse } from 'next/server';

/**
 * Central Industrial access gate — mirrors the scheme in the hub + Monkey Read
 * Monkey Do (lib scheme: token "<exp>.<sig>", sig = HMAC-SHA256(AUTH_SECRET,
 * "<purpose>:<exp>")). Entry must come THROUGH the hub, which appends a
 * short-lived ?t=<sso> handoff token to the tool link; we swap it for a
 * host-only ci_sess cookie and trust that. A cookieless direct visit bounces
 * to the hub to log in. The shared apex ci_auth cookie is deliberately NOT
 * trusted here (it would let people skip the hub).
 *
 * Gate is active only when AUTH_SECRET is set (so local dev stays open).
 * The realtime socket endpoint (/api/socket) and static assets are exempt via
 * the matcher below, so gating the pages never breaks the game's live layer.
 */
const AUTH_SECRET = process.env.AUTH_SECRET || '';
const HOME_URL = (process.env.HOME_URL || process.env.HUB_URL || 'https://centralindustrial.ai').trim();
const GATE_ON = !!AUTH_SECRET;
const SESS_TTL = 8 * 3600; // host session granted after a hub handshake (8h)

const enc = new TextEncoder();

async function hmacHex(msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(AUTH_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(msg));
  return Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
}

// constant-time string compare (equal length signatures)
function ctEq(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function verify(purpose: string, tok: string | undefined | null): Promise<boolean> {
  if (!tok) return false;
  const dot = tok.indexOf('.');
  if (dot < 0) return false;
  const exp = parseInt(tok.slice(0, dot), 10);
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return false;
  return ctEq(tok.slice(dot + 1), await hmacHex(`${purpose}:${exp}`));
}

async function makeSess(): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + SESS_TTL;
  return `${exp}.${await hmacHex(`sess:${exp}`)}`;
}

export async function proxy(req: NextRequest) {
  if (!GATE_ON) return NextResponse.next();

  const url = req.nextUrl;

  // Fresh arrival from the hub: swap ?t=<sso> for a host-only ci_sess cookie,
  // then redirect to the same URL minus the token.
  const sso = url.searchParams.get('t');
  if (sso && (await verify('sso', sso))) {
    const clean = url.clone();
    clean.searchParams.delete('t');
    const res = NextResponse.redirect(clean);
    res.cookies.set('ci_sess', await makeSess(), {
      httpOnly: true, secure: true, sameSite: 'lax', path: '/',
    });
    return res;
  }

  if (await verify('sess', req.cookies.get('ci_sess')?.value)) {
    return NextResponse.next();
  }

  // Not authed: API calls get 401, page requests bounce to the hub to log in.
  if (url.pathname.startsWith('/api/')) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  return NextResponse.redirect(HOME_URL);
}

export const config = {
  // Gate everything EXCEPT Next internals, the realtime socket, the health
  // check, and static assets (which carry no auth meaning and must stay open).
  matcher: [
    '/((?!_next/|api/socket|api/health|favicon\\.ico|sounds/|.*\\.(?:mp3|png|jpg|jpeg|svg|ico|woff2?)).*)',
  ],
};
