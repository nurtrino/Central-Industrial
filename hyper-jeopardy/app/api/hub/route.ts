// Where the Central Industrial hub lives, resolved at RUNTIME (not build time) so the
// same image works on Render (HOME_URL=https://centralindustrial.ai) and on the local
// suite (the local hub sets HOME_URL=http://127.0.0.1:5050/ when it starts us).
export const dynamic = 'force-dynamic';

export function GET() {
  const home = (process.env.HOME_URL || process.env.HUB_URL || 'http://127.0.0.1:5050/').trim();
  // "/?return" tells the hub to skip its boot screen + access prompt and land on the menu.
  return Response.json({ url: home.replace(/\/+$/, '') + '/?return' });
}
