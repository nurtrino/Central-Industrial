"""
Central Industrial hub — C64 landing server + the suite ACCESS GATE.

Serves the Commodore-64 boot screen and a small JSON API. Rebuilt for Render:
binds 0.0.0.0:$PORT, checks each tool's PUBLIC URL over HTTP, and is the single
access gate for the suite — the user enters the access code here once and a signed
cookie remembers them. Because cookies can't cross Render subdomains, the hub also
mints short-lived SSO tokens so clicking a gated tool (Monkey Read Monkey Do)
carries proof-of-auth to that tool's own domain — no second prompt.

Auth env (gate active only when BOTH are set):
  ACCESS_CODE   the password shown as the C64 access code
  AUTH_SECRET   HMAC key for the auth cookie + SSO tokens. Set the SAME value on the
                Monkey Read Monkey Do service so it trusts hub-issued tokens.

Tool registry: tools.json. URLs overridable via HUB_URL_<ID>. Tools with
"local": true are shown as [ LOCAL ] and never probed.

  GET  /            -> index.html (the C64 screen)
  GET  /api/status  -> {tools:[{id,name,url,up,local,sso}]}  (401 until authed)
  POST /api/login   -> {code} -> sets the signed ci_auth cookie
  POST /api/logout  -> clears it
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5050"))
PROBE_TIMEOUT = float(os.environ.get("HUB_PROBE_TIMEOUT", "3.5"))

# Shared Twixtle puzzle store. Lives on a persistent disk in production
# (DATA_DIR=/var/data) so puzzles generated/built on the site are server-side and
# shared across every device; falls back to BASE for local dev. Reads are public;
# writes are gated by the same access-code auth as /api/status.
DATA_DIR = os.environ.get("DATA_DIR", BASE)
TWIXTLE_STORE = os.path.join(DATA_DIR, "twixtle_puzzles.json")
TWIXTLE_MAX = 5000
_twixtle_lock = threading.Lock()

ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")
# Scope the auth cookie to the apex domain so ALL subdomains (the tools) share it —
# e.g. ".centralindustrial.com". Empty = host-only cookie (local dev).
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "")
GATE_ON = bool(ACCESS_CODE and AUTH_SECRET)
# The hub re-prompts for the code on EVERY load (see index.html). This cookie only has
# to live long enough to carry that fresh login over to a tool's subdomain, so keep it
# short — it is NOT a "stay logged in" cookie.
AUTH_TTL = 8 * 3600           # shared SSO cookie lifetime (8 hours)
# One-time-ish handoff token put on each tool link so a tool can prove the visitor came
# THROUGH the hub (not straight to the tool's URL). Kept very short; the landing page
# refreshes it continuously, so a click always carries a fresh one.
SSO_TTL = 120                 # hub -> tool handoff token (2 min)


# ── signed tokens (auth cookie + SSO), shared HMAC scheme with the MRMD service ──
def _sign(purpose, exp):
    return hmac.new(AUTH_SECRET.encode(), f"{purpose}:{exp}".encode(),
                    hashlib.sha256).hexdigest()


def make_token(purpose, ttl):
    exp = int(time.time()) + ttl
    return f"{exp}.{_sign(purpose, exp)}"


def check_token(purpose, tok):
    try:
        exp_s, sig = (tok or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(purpose, exp))


def _cookie(value):
    # SESSION cookie: NO Max-Age / Expires, so the browser drops it when it closes —
    # the access code is re-entered each browser session (no persistent "remember me").
    dom = f" Domain={COOKIE_DOMAIN};" if COOKIE_DOMAIN else ""
    return f"ci_auth={value}; Path=/;{dom} HttpOnly; SameSite=Lax; Secure"


def _cookie_clear():
    dom = f" Domain={COOKIE_DOMAIN};" if COOKIE_DOMAIN else ""
    return f"ci_auth=; Path=/;{dom} Max-Age=0; HttpOnly; SameSite=Lax; Secure"


# ── tool registry ────────────────────────────────────────────────────────────
def _env_url_override(tool_id, default):
    key = "HUB_URL_" + re.sub(r"[^A-Za-z0-9]", "_", tool_id or "").upper()
    return os.environ.get(key, default)


def _normalize_url(u):
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
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "central-industrial-hub"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def status_payload():
    tools = load_tools()

    def probe(t):
        is_local = bool(t.get("local"))
        return {
            "id": t.get("id"), "name": t.get("name"), "url": t.get("url"),
            "local": is_local,
            "up": False if is_local else url_up(t.get("url")),
        }

    # Fresh handoff token for the tool links (only meaningful when the gate is on).
    sso = make_token("sso", SSO_TTL) if GATE_ON else ""
    if not tools:
        return {"tools": [], "gated": GATE_ON, "sso": sso}
    with ThreadPoolExecutor(max_workers=min(8, len(tools))) as ex:
        return {"tools": list(ex.map(probe, tools)), "gated": GATE_ON, "sso": sso}


# ── shared Twixtle puzzle store (JSON file on the persistent disk) ────────────
def twixtle_load():
    try:
        with open(TWIXTLE_STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("puzzles"), list):
                return data
    except Exception:
        pass
    return {"puzzles": []}


def twixtle_save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TWIXTLE_STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, TWIXTLE_STORE)


def twixtle_key(p):
    return (p.get("start", "") or "").lower().strip() + ">" + (p.get("end", "") or "").lower().strip()


def twixtle_validate(p):
    if not isinstance(p, dict):
        return "bad puzzle"
    word = lambda v: isinstance(v, str) and re.match(r"^[a-z'-]{1,20}$", v) is not None
    if not word(p.get("start")) or not word(p.get("end")):
        return "bad start/end"
    sol = p.get("solution")
    if not isinstance(sol, list) or len(sol) != 5 or not all(word(w) for w in sol):
        return "bad solution"
    ty = p.get("types")
    if not isinstance(ty, list) or sorted(ty) != ["a", "c", "h", "v"]:
        return "bad types"
    if p.get("source") not in ("claude", "user"):
        return "bad source"
    if p.get("difficulty") not in ("easy", "medium", "hard"):
        return "bad difficulty"
    return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, obj, code=200, extra_headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not GATE_ON:
            return True
        c = SimpleCookie(self.headers.get("Cookie", "") or "")
        return "ci_auth" in c and check_token("auth", c["ci_auth"].value)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/login":
            code = str(self._read_json().get("code", ""))
            if not GATE_ON:
                return self._json({"ok": True})
            if ACCESS_CODE and hmac.compare_digest(code, ACCESS_CODE):
                return self._json({"ok": True}, 200,
                                  [("Set-Cookie", _cookie(make_token("auth", AUTH_TTL)))])
            return self._json({"ok": False, "error": "incorrect access code"}, 401)
        if p == "/api/logout":
            return self._json({"ok": True}, 200, [("Set-Cookie", _cookie_clear())])
        if p == "/api/twixtle/puzzles":
            if not self._authed():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            return self._twixtle_write(self._read_json())
        return self._json({"error": "not found"}, 404)

    # Add or delete a shared Twixtle puzzle. {action:"add", puzzle:{...}} or
    # {action:"delete", key:"start>end"}. Gated by _authed() in do_POST.
    def _twixtle_write(self, body):
        action = (body or {}).get("action", "add")
        with _twixtle_lock:
            data = twixtle_load()
            puzzles = data.get("puzzles", [])
            if action == "delete":
                key = str((body or {}).get("key", "")).lower().strip()
                data["puzzles"] = [p for p in puzzles if twixtle_key(p) != key]
                twixtle_save(data)
                return self._json({"ok": True, "count": len(data["puzzles"])})
            pz = (body or {}).get("puzzle")
            err = twixtle_validate(pz)
            if err:
                return self._json({"ok": False, "error": err}, 400)
            if len(puzzles) >= TWIXTLE_MAX:
                return self._json({"ok": False, "error": "archive full"}, 400)
            key = twixtle_key(pz)
            if any(twixtle_key(p) == key for p in puzzles):
                return self._json({"ok": True, "duplicate": True, "count": len(puzzles)})
            puzzles.append(pz)
            data["puzzles"] = puzzles
            twixtle_save(data)
            return self._json({"ok": True, "count": len(puzzles)})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            if not self._authed():
                return self._json({"error": "unauthorized"}, 401)
            return self._json(status_payload())
        if parsed.path == "/api/twixtle/puzzles":
            return self._json(twixtle_load())   # public read — anyone can play the shared set
        if parsed.path in ("/", ""):
            self.path = "/index.html"
        elif parsed.path in ("/cave", "/cave/"):
            self.path = "/cave_map.html"
        elif parsed.path in ("/twixtle", "/twixtle/"):
            self.path = "/twixtle.html"
        return super().do_GET()


def main():
    os.chdir(BASE)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Central Industrial hub on http://{HOST}:{PORT}")
    print(f"  access gate: {'ON' if GATE_ON else 'OFF (set ACCESS_CODE + AUTH_SECRET)'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
