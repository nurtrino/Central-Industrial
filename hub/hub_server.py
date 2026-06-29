"""
Central Industrial hub — dependency-free C64-style landing server.

Serves the Commodore-64 boot screen (index.html + fonts + music) and a tiny JSON
status API. Rebuilt for Render: binds 0.0.0.0:$PORT, checks each tool's PUBLIC URL
over HTTP, and does NOT launch processes — on Render every tool is its own
always-on service, so the page simply links to each tool's URL.

Tool registry: tools.json. Each tool's URL can be overridden by an env var
HUB_URL_<ID> (id uppercased, non-alphanumerics -> "_"), so render.yaml can inject
the deployed service URLs without editing the file. Tools flagged "local": true
(e.g. Monkey Read Monkey Do, which runs on a local GPU) are never probed from the
cloud — they render as [ LOCAL ] and link to their configured URL.

  GET /            -> index.html (the C64 screen)
  GET /api/status  -> { "tools": [ {id,name,url,up,local}, ... ] }
"""
import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5050"))
PROBE_TIMEOUT = float(os.environ.get("HUB_PROBE_TIMEOUT", "3.5"))


def _env_url_override(tool_id, default):
    key = "HUB_URL_" + re.sub(r"[^A-Za-z0-9]", "_", tool_id or "").upper()
    return os.environ.get(key, default)


def _normalize_url(u):
    """Render's `fromService property: host` injects a bare hostname (no scheme).
    Assume https:// for anything that isn't already an absolute http(s) URL."""
    u = (u or "").strip()
    if u and not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def load_tools():
    try:
        with open(os.path.join(BASE, "tools.json"), "r", encoding="utf-8") as f:
            tools = json.load(f).get("tools", [])
    except Exception:
        return []
    for t in tools:
        t["url"] = _normalize_url(_env_url_override(t.get("id", ""), t.get("url", "")))
    return tools


def url_up(url, timeout=PROBE_TIMEOUT):
    """True if the URL answers a quick HTTP GET (anything that isn't a 5xx / dead)."""
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "central-industrial-hub"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500          # a 4xx still means something is listening
    except Exception:
        return False


def status_payload():
    tools = load_tools()

    def probe(t):
        is_local = bool(t.get("local"))
        return {
            "id": t.get("id"),
            "name": t.get("name"),
            "url": t.get("url"),
            "local": is_local,
            # Local-only tools aren't reachable from the cloud — don't probe them.
            "up": False if is_local else url_up(t.get("url")),
        }

    if not tools:
        return {"tools": []}
    with ThreadPoolExecutor(max_workers=min(8, len(tools))) as ex:
        out = list(ex.map(probe, tools))
    return {"tools": out}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def log_message(self, *a):
        pass  # keep the process quiet

    def end_headers(self):
        # never let the browser serve a stale page / status
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            return self._json(status_payload())
        if parsed.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()


def main():
    os.chdir(BASE)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Central Industrial hub on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
