"""
Central Industrial hub — C64 landing server, the suite ACCESS GATE (hosted) and the
single-port front door + process supervisor for the local suite (local).

ONE codebase, TWO modes (like the tools themselves):

HOSTED (default — Render): binds 0.0.0.0:$PORT, probes each tool's PUBLIC URL over
HTTP, and is the single access gate for the suite — the user enters the access code
once and a signed cookie remembers them. Because cookies can't cross Render
subdomains, the hub also mints short-lived SSO tokens so clicking a gated tool carries
proof-of-auth to that tool's own domain — no second prompt.

LOCAL (CI_LOCAL=1 — this machine): no gate. Everything lives behind ONE port
(HUB_PORT, default 5050) so the local suite mirrors the public site's layout:

    http://127.0.0.1:5050/            the hub (this page; /cave /twixtle /crate too)
    http://notes.localhost:5050/      Monkey Read Monkey Do   (-> 127.0.0.1:5005)
    http://research.localhost:5050/   Deep Research           (-> 127.0.0.1:5006)
    http://home.localhost:5050/       Home Assistant          (-> 127.0.0.1:8123)

A tiny TCP router on the public port reads each connection's Host header and relays
the whole connection byte-for-byte to the right backend (so streaming, long polls
and WebSockets all just work); `<name>.localhost` resolves to 127.0.0.1 inside every
modern browser with no hosts-file edits. The hub's own HTTP server sits on
HUB_PORT+1, loopback only. The tools keep their own venvs and internal ports, but the
hub STARTS them (hidden child processes in a Windows Job Object, so closing the hub
closes them), restarts them on demand, and auto-launches one the moment its hostname
is requested while it's down (a C64 "LOADING" page refreshes until it answers).

Auth env (hosted; gate active only when BOTH are set):
  ACCESS_CODE   the password shown as the C64 access code
  AUTH_SECRET   HMAC key for the auth cookie + SSO tokens (same value on the tools)

Tool registry: tools.json — one list for both modes:
  id, name, url            hosted URL (probed over HTTP in hosted mode)
  path                     served by the hub itself in both modes (/cave, /twixtle ...)
  local_only               hosted mode shows [ LOCAL ] and never probes
  hidden                   supervised but not listed (e.g. the GPU worker)
  local: {host, port, cwd, cmd, env, log, autostart, detached}
                           local mode: host -> <host>.localhost routing; port probed;
                           cmd spawned in cwd (detached: launched, not supervised)
URLs overridable via HUB_URL_<ID>.

  GET  /            -> index.html (the C64 screen)
  GET  /api/status  -> {mode, tools:[{id,name,url,up,kind}], gated, sso}  (401 until authed)
  GET  /api/launch?id=<id>  (local) -> starts that tool, {ok}
  POST /api/login   -> {code} -> sets the signed ci_auth cookie
  POST /api/logout  -> clears it
"""
import hashlib
import hmac
import json
import os
import re
import socket
import socketserver
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


LOCAL = _truthy(os.environ.get("CI_LOCAL"))
if LOCAL:
    PUBLIC_PORT = int(os.environ.get("HUB_PORT", "5050"))   # the ONE port (router)
    BIND = os.environ.get("HUB_BIND", "127.0.0.1")           # 0.0.0.0 = LAN too
    HOST, PORT = "127.0.0.1", PUBLIC_PORT + 1                # hub's own HTTP, loopback
else:
    HOST = os.environ.get("HOST", "0.0.0.0")
    PORT = int(os.environ.get("PORT", "5050"))
    PUBLIC_PORT, BIND = PORT, HOST
PROBE_TIMEOUT = float(os.environ.get("HUB_PROBE_TIMEOUT", "3.5"))

# Shared Twixtle puzzle store. Lives on a persistent disk in production
# (DATA_DIR=/var/data) so puzzles generated/built on the site are server-side and
# shared across every device; falls back to BASE for local. Reads are public;
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
GATE_ON = (not LOCAL) and bool(ACCESS_CODE and AUTH_SECRET)
# The hub re-prompts for the code on EVERY load (see index.html). This cookie only has
# to live long enough to carry that fresh login over to a tool's subdomain, so keep it
# short — it is NOT a "stay logged in" cookie.
AUTH_TTL = 8 * 3600           # shared SSO cookie lifetime (8 hours)
# One-time-ish handoff token put on each tool link so a tool can prove the visitor came
# THROUGH the hub (not straight to the tool's URL). Kept very short; the landing page
# refreshes it continuously, so a click always carries a fresh one.
SSO_TTL = 120                 # hub -> tool handoff token (2 min)


# ── signed tokens (auth cookie + SSO), shared HMAC scheme with the tools ─────
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


def local_url(t):
    """Where a tool lives behind the single local port."""
    if t.get("path"):
        return f"http://127.0.0.1:{PUBLIC_PORT}{t['path']}"
    loc = t.get("local") or {}
    if loc.get("host"):
        return f"http://{loc['host']}.localhost:{PUBLIC_PORT}/"
    if loc.get("port"):
        return f"http://127.0.0.1:{loc['port']}/"
    return _normalize_url(t.get("url", ""))          # hosted-only tool


def tool_kind(t):
    """local: runs here (supervised or hub-served) · hosted: only exists on the public
    site · local_only: only exists on this machine (hosted mode shows [ LOCAL ])."""
    if LOCAL:
        return "local" if (t.get("local") or t.get("path")) else "hosted"
    if t.get("local_only") or t.get("local") is True:
        return "local_only"
    return "hosted"


def load_tools(include_hidden=False):
    try:
        with open(os.path.join(BASE, "tools.json"), "r", encoding="utf-8") as f:
            tools = json.load(f).get("tools", [])
    except Exception:
        return []
    out = []
    for t in tools:
        if t.get("hidden") and not include_hidden:
            continue
        if LOCAL:
            t["url"] = local_url(t)
        else:
            t["url"] = _normalize_url(_env_url_override(t.get("id", ""), t.get("url", "")))
        t["kind"] = tool_kind(t)
        out.append(t)
    return out


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


def port_up(port, host="127.0.0.1", timeout=0.5):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def tool_up(t):
    if LOCAL:
        if t.get("path"):
            return True                                   # served by this very hub
        loc = t.get("local") or {}
        return port_up(loc["port"]) if loc.get("port") else False
    if t.get("kind") == "local_only":
        return False
    return url_up(t.get("url"))


def status_payload():
    tools = load_tools()

    def probe(t):
        return {"id": t.get("id"), "name": t.get("name"), "url": t.get("url"),
                "kind": t.get("kind"), "local": t.get("kind") == "local_only",
                "up": tool_up(t)}

    # Fresh handoff token for the tool links (only meaningful when the gate is on).
    sso = make_token("sso", SSO_TTL) if GATE_ON else ""
    mode = "local" if LOCAL else "hosted"
    if not tools:
        return {"mode": mode, "tools": [], "gated": GATE_ON, "sso": sso}
    with ThreadPoolExecutor(max_workers=min(8, len(tools))) as ex:
        return {"mode": mode, "tools": list(ex.map(probe, tools)),
                "gated": GATE_ON, "sso": sso}


# ── local mode: process supervision (Windows Job Object = children die with us) ──
_children = {}            # tool id -> Popen
_children_lock = threading.Lock()
_JOB = None
_FLAGS = 0
if os.name == "nt":
    _FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _job_object():
    """A Job Object with KILL_ON_JOB_CLOSE: every supervised child (and ITS children —
    Deep Research's Chrome, the worker's python) is torn down when this hub exits."""
    global _JOB
    if _JOB is not None or os.name != "nt":
        return _JOB
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        _JOB = (k32, job)
    except Exception:
        _JOB = None
    return _JOB


def _assign_to_job(proc):
    j = _job_object()
    if j:
        try:
            j[0].AssignProcessToJobObject(j[1], int(proc._handle))
        except Exception:
            pass


def find_tool(tid):
    for t in load_tools(include_hidden=True):
        if t.get("id") == tid:
            return t
    return None


def launch_tool(t):
    """Start a local tool if it isn't up. Returns (ok, message)."""
    loc = t.get("local") or {}
    if not loc.get("cmd"):
        return False, "no launcher configured"
    if loc.get("port") and port_up(loc["port"]):
        return True, "already up"
    with _children_lock:
        p = _children.get(t.get("id"))
        if p is not None and p.poll() is None and not loc.get("detached"):
            return True, "starting"                       # spawned, still binding
        cwd = loc.get("cwd") or BASE
        try:
            if loc.get("detached"):
                subprocess.Popen(loc["cmd"], cwd=cwd, creationflags=_FLAGS,
                                 close_fds=True, stdin=subprocess.DEVNULL)
                return True, "launched"
            out = subprocess.DEVNULL
            if loc.get("log"):
                out = open(os.path.join(cwd, loc["log"]), "a", buffering=1,
                           encoding="utf-8", errors="replace")
            env = dict(os.environ)
            env.update({k: str(v) for k, v in (loc.get("env") or {}).items()})
            p = subprocess.Popen(loc["cmd"], cwd=cwd, env=env, creationflags=_FLAGS,
                                 stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, close_fds=True)
            _assign_to_job(p)
            _children[t.get("id")] = p
            return True, "launched"
        except Exception as e:
            return False, str(e)


def autostart_tools():
    for t in load_tools(include_hidden=True):
        if (t.get("local") or {}).get("autostart"):
            ok, msg = launch_tool(t)
            print(f"  autostart {t.get('id')}: {msg}", flush=True)


# ── local mode: the single-port router (Host header -> backend, raw relay) ────
def route_host(host):
    """'<name>.localhost' -> the tool whose local.host is <name>; else None (= the hub)."""
    host = (host or "").split(":")[0].strip().lower()
    if not host.endswith(".localhost"):
        return None
    name = host[: -len(".localhost")]
    for t in load_tools(include_hidden=True):
        if (t.get("local") or {}).get("host") == name:
            return t
    return None


def _c64_page(title, lines, refresh=None):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    body = "\n".join(lines)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>{meta}
<style>body{{margin:0;background:#6C5EB5;font-family:"C64 Pro Mono","Courier New",monospace}}
.s{{margin:4vmin;min-height:calc(100vh - 8vmin);box-sizing:border-box;background:#352879;color:#6C5EB5;
padding:3.4vmin 4vmin;font-size:clamp(13px,2.25vmin,24px);line-height:1.36;text-transform:uppercase;white-space:pre-wrap}}
a{{color:#9AD284;text-decoration:none}}.c{{display:inline-block;width:1ch;height:1.12em;background:#6C5EB5;
vertical-align:-0.16em;animation:b 1.02s steps(1,end) infinite}}@keyframes b{{0%,49%{{opacity:1}}50%,100%{{opacity:0}}}}</style>
</head><body><div class="s">{body}<span class="c"></span></div></body></html>"""
    return html.encode("utf-8")


def _http_response(status, body, ctype="text/html; charset=utf-8"):
    head = (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            f"Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode("ascii")
    return head + body


def _back_link():
    return f'<a href="http://127.0.0.1:{PUBLIC_PORT}/">&gt; BACK TO THE HUB</a>'


def _relay(a, b):
    """Pump bytes a->b until EOF, then half-close b."""
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        try:
            b.shutdown(socket.SHUT_WR)
        except Exception:
            pass


class Router(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        conn.settimeout(30)
        buf = b""
        try:
            while b"\r\n\r\n" not in buf and len(buf) < 65536:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
        except Exception:
            return
        m = re.search(rb"\r\nHost:[ \t]*([^\r\n]+)", buf, re.I)
        host = m.group(1).decode("latin-1") if m else ""
        tool = route_host(host)
        if tool is None:
            backend = (HOST, PORT)                        # the hub's own HTTP server
        else:
            loc = tool.get("local") or {}
            if not loc.get("port"):
                conn.sendall(_http_response("404 Not Found", _c64_page(
                    "?DEVICE NOT PRESENT", ["?DEVICE NOT PRESENT  ERROR", "", _back_link()])))
                return
            if not port_up(loc["port"]):
                ok, msg = launch_tool(tool)
                name = (tool.get("name") or tool.get("id") or "").upper()
                if ok:
                    conn.sendall(_http_response("503 Service Unavailable", _c64_page(
                        "LOADING", [f'LOAD"{name}",8,1', "", "SEARCHING FOR " + name,
                                    "LOADING", ""], refresh=2)))
                else:
                    conn.sendall(_http_response("503 Service Unavailable", _c64_page(
                        "?DEVICE NOT PRESENT", [f'LOAD"{name}",8,1', "",
                        "?DEVICE NOT PRESENT  ERROR", str(msg).upper(), "", _back_link()])))
                return
            backend = ("127.0.0.1", int(loc["port"]))
        try:
            b = socket.create_connection(backend, timeout=10)
        except Exception:
            conn.sendall(_http_response("502 Bad Gateway", _c64_page(
                "?DEVICE NOT PRESENT", ["?DEVICE NOT PRESENT  ERROR", "", _back_link()])))
            return
        conn.settimeout(None)
        b.settimeout(None)
        try:
            b.sendall(buf)
            t = threading.Thread(target=_relay, args=(conn, b), daemon=True)
            t.start()
            _relay(b, conn)
            t.join()
        finally:
            for s in (b, conn):
                try:
                    s.close()
                except Exception:
                    pass


class RouterServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


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
        if parsed.path == "/api/launch":
            if not LOCAL:
                return self._json({"ok": False, "error": "not available on the hosted hub"}, 404)
            tid = (parse_qs(parsed.query).get("id") or [""])[0]
            t = find_tool(tid)
            if not t:
                return self._json({"ok": False, "error": "unknown tool id"}, 404)
            ok, msg = launch_tool(t)
            return self._json({"ok": ok, "message": msg}, 200 if ok else 500)
        if parsed.path == "/api/twixtle/puzzles":
            return self._json(twixtle_load())   # public read — anyone can play the shared set
        if parsed.path in ("/", ""):
            self.path = "/index.html"
        elif parsed.path in ("/cave", "/cave/"):
            self.path = "/cave_map.html"
        elif parsed.path in ("/twixtle", "/twixtle/"):
            self.path = "/twixtle.html"
        elif parsed.path in ("/crate", "/crate/"):
            self.path = "/crate.html"
        return super().do_GET()


def main():
    os.chdir(BASE)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    if LOCAL:
        router = RouterServer((BIND, PUBLIC_PORT), Router)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"Central Industrial hub (LOCAL) on http://127.0.0.1:{PUBLIC_PORT}  "
              f"[hub http on {HOST}:{PORT}; <tool>.localhost:{PUBLIC_PORT} -> tools]", flush=True)
        threading.Thread(target=autostart_tools, daemon=True).start()
        try:
            router.serve_forever()
        except KeyboardInterrupt:
            pass
        return
    print(f"Central Industrial hub on http://{HOST}:{PORT}")
    print(f"  access gate: {'ON' if GATE_ON else 'OFF (set ACCESS_CODE + AUTH_SECRET)'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
