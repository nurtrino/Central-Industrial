// Open health endpoint for Render's health check — deliberately exempt from the
// access gate (see proxy.ts) so a cookieless prober gets 200, not a redirect.
export const dynamic = 'force-static';

export function GET() {
  return Response.json({ ok: true });
}
