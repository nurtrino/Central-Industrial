"""Browser MCP bridge — TRIAL (2026-11-12).

Lets the research tool search and read a few sites through the owner's EVERYDAY Brave
(where he is already signed in) instead of the tool's own research profile, which has to
be signed in site by site. The first real run spent its sign-in effort on GitHub's 2FA and
reported a Reddit session it did not have; in the everyday browser neither needs anything.

How it works: Browser MCP (github.com/BrowserMCP/mcp, built from source under
`D:\\_______Claude\\Central-Industrial\\vendor\\browsermcp`, pinned commit a51ddc4) is a
small local server plus a browser extension (loaded in Brave). This module starts the server as a child
process, speaks MCP to it over stdin/stdout, and sends lines to its one `browser` tool.
The agent's tabs are background tabs in the everyday Brave; nothing leaves the computer.

What this module deliberately does NOT expose: the tool can also click, type, upload,
download and save files. None of that is reachable from here — only `goto`, `eval` (read
only page functions written in this file), `tab` and `wait`. The research agents never
see the `browser` tool; they keep their own search/open tools.

Off unless DRT_BMCP=1. Domains routed through it: DRT_BMCP_DOMAINS (default github.com,
reddit.com, x.com; add forums by name). It knows GitHub, Reddit, X and any Discourse or
XenForo forum (sign-in check; own search for all but XenForo; whole-thread reading). Everything falls back to the normal research browser when Browser MCP is not
running, not paired, or a call fails.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from urllib.parse import quote_plus

_DEFAULT_SERVER = r"D:\_______Claude\Central-Industrial\vendor\browsermcp\packages\mcp\dist\index.js"
_MARK = "__drt"
_SLICE = 30000            # characters of page text per eval (the tool caps a read at 75K)
_MAX_PAGE = 300000        # most text taken from one page


def enabled() -> bool:
    # Never on the hosted site, whatever the setting says: the bridge is the owner's everyday
    # Brave on the house PC, signed in as him.
    if os.environ.get("AUTH_SECRET", "").strip():
        return False
    return os.environ.get("DRT_BMCP", "").strip().lower() in ("1", "true", "yes", "on")


def routed_domains() -> list:
    raw = os.environ.get("DRT_BMCP_DOMAINS", "github.com,reddit.com,x.com")
    return [d.strip().lower().lstrip(".") for d in raw.split(",") if d.strip()]


def routes(domain: str) -> bool:
    return site_key(domain) in routed_domains()


def site_key(domain: str) -> str:
    """The routed site a host belongs to (old.reddit.com → reddit.com, twitter.com → x.com),
    or the host itself, lower-case and without www., when it is not routed."""
    d = (domain or "").lower().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if d == "twitter.com" or d.endswith(".twitter.com"):
        d = "x.com"
    for r in routed_domains():
        if d == r or d.endswith("." + r):
            return r
    return d


def _jsq(text: str) -> str:
    """A Python string as a single-quoted JavaScript string literal (no double quotes)."""
    t = re.sub(r"[\r\n\t]+", " ", text or "").replace("\\", "\\\\").replace("'", "\\'").replace('"', "")
    return "'" + t + "'"


class BMCPError(Exception):
    """A Browser MCP call failed; the caller falls back to the research browser."""


def _js(src: str) -> str:
    """A page function as ONE line with no double quotes, ready to sit inside eval "…".
    (The tool takes one command per line and the code is quoted with double quotes.)"""
    one = re.sub(r"\s*\n\s*", " ", src.strip())
    if '"' in one:
        raise ValueError("page functions in bmcp.py must not contain double quotes")
    return one


# ── page functions (read-only; each returns {__drt: 1, …}) ────────────────────
_JS_STATE = _js(r"""
() => ({__drt: 1, url: location.href, title: document.title, ready: document.readyState})
""")

# Whether the everyday browser is signed in — a yes/no ONLY, never the account name.
_JS_SIGNED_GITHUB = _js(r"""
() => { const m = document.querySelector('meta[name=user-login]');
        return {__drt: 1, signed: !!(m && m.content), url: location.href}; }
""")
_JS_SIGNED_REDDIT = _js(r"""
() => { const bounced = /\/login/.test(location.pathname);
        const u = document.querySelector('#header .user a[href*=\'/user/\']');
        const out = document.querySelector('#header .user a.login-required');
        return {__drt: 1, signed: !bounced && !!u && !out, url: location.href}; }
""")

_JS_SIGNED_X = _js(r"""
async () => { const sleep = ms => new Promise(r => setTimeout(r, ms));
  const sel = 'a[data-testid=AppTabBar_Profile_Link], [data-testid=SideNav_AccountSwitcher_Button], a[data-testid=SideNav_NewTweet_Button]';
  for (let i = 0; i < 10 && !document.querySelector(sel) && !/\/i\/flow\/login|\/login/.test(location.pathname); i++) await sleep(400);
  const bounced = /\/i\/flow\/login|\/login/.test(location.pathname);
  return {__drt: 1, signed: !bounced && !!document.querySelector(sel), url: location.href}; }
""")
# Any forum: which software it runs, and whether this browser is signed in to it.
_JS_SIGNED_FORUM = _js(r"""
async () => { const gen = ((document.querySelector('meta[name=generator]') || {}).content || '').toLowerCase();
  const disc = gen.includes('discourse') || !!document.querySelector('#data-discourse-setup');
  const xf = !disc && !!document.querySelector('html[data-app], html#XF, .p-pageWrapper');
  let signed = null;
  if (disc) { try { const r = await fetch('/session/current.json', {credentials: 'include'});
                    signed = r.ok ? !!(((await r.json()) || {}).current_user || {}).id : false; } catch (e) { signed = false; } }
  else if (xf) { const v = document.documentElement.getAttribute('data-logged-in'); signed = v === null ? null : v === 'true'; }
  return {__drt: 1, platform: disc ? 'discourse' : (xf ? 'xenforo' : ''), signed: signed, url: location.href}; }
""")

# Stash the page's readable text on the tab, return its size and the first slice.
_JS_READ = _js(r"""
async () => {
  const pick = () => {
    if (/(^|\.)reddit\.com$/.test(location.hostname) && document.querySelector('.sitetable')) {
      const out = [];
      const t = document.querySelector('.thing.link a.title');
      if (t) out.push(t.innerText.trim());
      const st = document.querySelector('.thing.link .expando .md');
      if (st) out.push('[post 1] ' + st.innerText.trim());
      let n = 1;
      for (const c of document.querySelectorAll('.commentarea .comment')) {
        const md = c.querySelector(':scope > .entry .md');
        if (!md) continue;
        const a = c.querySelector(':scope > .entry .author');
        const s = c.querySelector(':scope > .entry .score.unvoted');
        let depth = 0;
        for (let p = c.parentElement; p; p = p.parentElement) if (p.classList && p.classList.contains('child')) depth++;
        n++;
        out.push('[comment ' + n + ' · depth ' + depth + (a ? ' · ' + a.innerText.trim() : '') + (s ? ' · ' + s.innerText.trim() : '') + '] ' + md.innerText.trim());
      }
      if (out.length > 1) return out.join('\n\n');
    }
    const el = document.querySelector('main, [role=main], article') || document.body;
    return (el.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
  };
  let text = '';
  const disc = /discourse/i.test(((document.querySelector('meta[name=generator]') || {}).content) || '') || !!document.querySelector('#data-discourse-setup');
  const mt = location.pathname.match(/^\/t\/(?:[^\/]+\/)?(\d+)/);
  if (disc && mt) {
    const parts = [];
    for (let pg = 1; pg <= 5; pg++) {
      let t = '';
      try { const r = await fetch('/raw/' + mt[1] + '?page=' + pg, {credentials: 'include'}); t = r.ok ? (await r.text()).trim() : ''; } catch (e) { t = ''; }
      if (!t) break;
      parts.push(t);
      if (t.split('\n-------------------------\n').length - 1 < 95) break;
    }
    if (parts.length) text = document.title.trim() + '\n\n' + parts.join('\n\n-------------------------\n\n');
  }
  if (!text) text = pick();
  window.__drt_text = text;
  const links = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('main a[href], [role=main] a[href], .commentarea a[href], .nav-buttons a[href]')) {
    const href = a.href || '';
    const tx = (a.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    if (!/^https?:/.test(href) || !tx || seen.has(href)) continue;
    seen.add(href);
    const rel = (a.getAttribute('rel') || '') + ' ' + tx.toLowerCase();
    links.push({text: tx, url: href, kind: /next/.test(rel) ? 'next' : (a.hostname === location.hostname ? 'content' : 'external')});
    if (links.length >= 80) break;
  }
  return {__drt: 1, url: location.href, title: document.title, len: text.length, text: text.slice(0, SLICE), links: links};
}
""").replace("SLICE", str(_SLICE))
_JS_SLICE = "() => ({__drt: 1, text: (window.__drt_text || '').slice(%d, %d)})"

# Reddit's own search, old.reddit (server-rendered; needs a session, which is the point).
_JS_SEARCH_REDDIT = _js(r"""
() => { const rows = [];
  for (const r of document.querySelectorAll('.search-result-link')) {
    const a = r.querySelector('a.search-title'); if (!a) continue;
    const tm = r.querySelector('.search-time time');
    const sub = r.querySelector('.search-subreddit-link');
    const sc = r.querySelector('.search-score');
    const cm = r.querySelector('.search-comments');
    const body = r.querySelector('.search-result-body');
    rows.push({title: a.innerText.trim().slice(0, 170), url: a.href,
               date: tm ? (tm.getAttribute('datetime') || '').slice(0, 10) : '',
               snippet: [sub ? sub.innerText.trim() : '', sc ? sc.innerText.trim() : '', cm ? cm.innerText.trim() : '',
                         body ? body.innerText.trim().replace(/\s+/g, ' ').slice(0, 160) : ''].filter(Boolean).join(' · ')});
    if (rows.length >= 25) break; }
  return {__drt: 1, url: location.href, bounced: /\/login/.test(location.pathname), rows: rows}; }
""")

# X: the Top tab of its search, read from the rendered post cards (signed-in only).
_JS_SEARCH_X = _js(r"""
async () => { const sleep = ms => new Promise(r => setTimeout(r, ms));
  const grab = () => Array.from(document.querySelectorAll('article[data-testid=tweet]')).map(a => {
      const t = a.querySelector('[data-testid=tweetText]'); const tm = a.querySelector('time');
      const link = tm ? tm.closest('a') : a.querySelector('a[href*=\'/status/\']');
      return {text: t ? (t.innerText || '') : '', url: link ? link.href : '', date: tm ? (tm.getAttribute('datetime') || '') : ''}; });
  for (let i = 0; i < 20 && !document.querySelector('article[data-testid=tweet]') && !/\/i\/flow\/login|\/login/.test(location.pathname); i++) await sleep(500);
  let rows = grab();
  if (rows.length && rows.length < 12) { window.scrollBy(0, 3000); await sleep(1500); rows = rows.concat(grab()); }
  const seen = new Set(); const out = [];
  for (const r of rows) { const href = (r.url || '').split('?')[0]; const tx = (r.text || '').replace(/\s+/g, ' ').trim();
    const m = href.match(/(?:x|twitter)\.com\/([^\/]+)\/status\/\d+/);
    if (!m || seen.has(href) || tx.length < 15) continue;
    seen.add(href);
    out.push({title: ('@' + m[1] + ': ' + tx).slice(0, 170), url: href, snippet: tx.slice(0, 300), date: (r.date || '').slice(0, 10)}); }
  return {__drt: 1, url: location.href, bounced: /\/i\/flow\/login|\/login/.test(location.pathname), rows: out.slice(0, 25)}; }
""")

# A Discourse forum's own search, asked from a page on the forum (so the session rides along).
_JS_SEARCH_DISCOURSE = _js(r"""
async () => { let data = {}; let status = 0;
  try { const r = await fetch('/search.json?q=' + encodeURIComponent(QUERY), {credentials: 'include'}); status = r.status; if (r.ok) data = await r.json(); } catch (e) { status = -1; }
  const topics = {}; for (const t of (data.topics || [])) topics[t.id] = t;
  const rows = []; const seen = new Set();
  const add = (t, blurb, date) => { if (!t || seen.has(t.id)) return; seen.add(t.id);
    rows.push({title: (t.title || '').trim().slice(0, 170), url: location.origin + '/t/' + (t.slug || 't') + '/' + t.id,
               snippet: (blurb || '').trim().slice(0, 300), date: String(date || t.created_at || '').slice(0, 10)}); };
  for (const p of (data.posts || [])) add(topics[p.topic_id], p.blurb, p.created_at);
  for (const t of (data.topics || [])) add(t, '', t.created_at);
  return {__drt: 1, url: location.href, status: status, rows: rows.slice(0, 25)}; }
""")

# GitHub's signed-in web search. Picks result links by what they point at, not by class
# names (the search page is a React app whose markup changes).
_JS_SEARCH_GITHUB = _js(r"""
() => { const rows = []; const seen = new Set();
  const root = document.querySelector('[data-testid=results-list]') || document.querySelector('main') || document.body;
  const want = /^https:\/\/github\.com\/[^\/]+\/[^\/]+\/(issues|pull|discussions)\/\d+$|^https:\/\/github\.com\/[^\/]+\/[^\/]+$|^https:\/\/github\.com\/[^\/]+\/[^\/]+\/blob\//;
  const skip = /^https:\/\/github\.com\/(search|topics|sponsors|settings|orgs|features|about|login|signup|marketplace|collections|trending|notifications|pulls|issues|explore)(\/|$)/;
  for (const a of root.querySelectorAll('a[href]')) {
    const href = (a.href || '').split('#')[0].split('?')[0];
    const tx = (a.innerText || '').trim().replace(/\s+/g, ' ');
    if (!want.test(href) || skip.test(href) || seen.has(href) || tx.length < 3) continue;
    let box = a;
    for (let i = 0; i < 6 && box.parentElement; i++) { box = box.parentElement; const n = (box.innerText || '').length; if (n >= 80) break; }
    const ctx = (box.innerText || '').replace(/\s+/g, ' ').trim();
    const tm = box.querySelector('relative-time, time');
    seen.add(href);
    const kind = /\/issues\/\d+$/.test(href) ? 'issue' : /\/pull\/\d+$/.test(href) ? 'PR' : /\/discussions\/\d+$/.test(href) ? 'discussion' : /\/blob\//.test(href) ? 'code' : 'repo';
    rows.push({title: ('[' + kind + '] ' + tx).slice(0, 170), url: href, snippet: ctx.slice(0, 220),
               date: tm ? (tm.getAttribute('datetime') || '').slice(0, 10) : ''});
    if (rows.length >= 25) break; }
  return {__drt: 1, url: location.href, bounced: /\/login/.test(location.pathname), rows: rows}; }
""")


class BrowserMCP:
    """One Browser MCP session. Calls are serialized (the tool runs one call per session
    at a time). Thread-safe; safe to call from any thread."""

    def __init__(self, server_js: str | None = None, node: str | None = None, log=None,
                 env: dict | None = None, args: list | None = None):
        self.server_js = server_js or os.environ.get("DRT_BMCP_SERVER", "").strip() or _DEFAULT_SERVER
        self.node = node or os.environ.get("DRT_BMCP_NODE", "").strip() or "node"
        self._log = log or (lambda m: None)
        self._env = env
        self._args = list(args or [])
        self._proc = None
        self._lock = threading.RLock()
        self._wlock = threading.Lock()
        self._pending: dict = {}
        self._next_id = 0
        self._status = None            # (ok, detail, checked_at)
        self._signed: dict = {}        # site -> True / False / None (cannot tell)
        self._platform: dict = {}      # site -> 'discourse' | 'xenforo' | ''
        self.calls = 0
        self.browser_name = ""       # which browser the calls run in, once known

    # ── process + JSON-RPC ───────────────────────────────────
    def start(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self
            if not os.path.isfile(self.server_js):
                raise BMCPError(f"Browser MCP server not found at {self.server_js}")
            env = dict(os.environ)
            env.update(self._env or {})
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._proc = subprocess.Popen(
                    [self.node, self.server_js, *self._args], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                    creationflags=flags)
            except OSError as e:
                raise BMCPError(f"could not start Browser MCP ({type(e).__name__}: {e})")
            threading.Thread(target=self._reader, name="drt-bmcp-reader", daemon=True).start()
            self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "deep-research-tool", "version": "trial"}},
                      timeout=45)
            self._notify("notifications/initialized", {})
            return self

    def _reader(self):
        proc = self._proc
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    q = self._pending.get(msg["id"])
                    if q is not None:
                        q.put(msg)
                elif "id" in msg and "method" in msg:
                    # A request FROM the server (roots/list, ping, sampling…): we offer none.
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "result": {} if msg["method"] == "ping" else {"roots": []}})
        except Exception:  # noqa: BLE001
            pass
        finally:
            for q in list(self._pending.values()):
                q.put({"error": {"message": "Browser MCP exited"}})

    def _send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self._wlock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except Exception as e:  # noqa: BLE001
                raise BMCPError(f"Browser MCP is not running ({type(e).__name__})")

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _rpc(self, method, params, timeout=60):
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            q: queue.Queue = queue.Queue()
            self._pending[rid] = q
            try:
                self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
                try:
                    msg = q.get(timeout=timeout)
                except queue.Empty:
                    raise BMCPError(f"Browser MCP did not answer within {int(timeout)} s")
            finally:
                self._pending.pop(rid, None)
            if msg.get("error"):
                raise BMCPError(str((msg["error"] or {}).get("message") or msg["error"])[:300])
            return msg.get("result") or {}

    def call(self, commands, timeout=150) -> tuple:
        """Run command lines on the `browser` tool. Returns (text, is_error)."""
        if isinstance(commands, (list, tuple)):
            commands = "\n".join(commands)
        self.start()
        res = self._rpc("tools/call", {"name": "browser", "arguments": {"commands": commands}},
                        timeout=timeout)
        self.calls += 1
        text = "\n".join(c.get("text", "") for c in (res.get("content") or [])
                         if isinstance(c, dict) and c.get("type") == "text")
        return text, bool(res.get("isError"))

    def close(self):
        """Close the tab this session opened (best effort) and stop the child process."""
        with self._lock:
            p = self._proc
            if p is None:
                return
            try:
                if p.poll() is None and self.calls:
                    try:
                        self.call("tab close", timeout=10)
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                self._proc = None
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass

    # ── reads ────────────────────────────────────────────────
    @staticmethod
    def _parse(text: str) -> dict:
        """The {__drt: 1, …} object an eval printed, from among the tool's status lines."""
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith('{"' + _MARK + '":1'):
                try:
                    return json.loads(line)
                except ValueError:
                    break
        raise BMCPError("unexpected answer: " + re.sub(r"\s+", " ", text or "")[:260])

    def _eval(self, js: str, before=(), timeout=90) -> dict:
        lines = list(before) + [f'eval "{js}" --max all']
        text, err = self.call(lines, timeout=timeout)
        if err:
            raise BMCPError(re.sub(r"\s+", " ", text)[:300])
        return self._parse(text)

    def status(self, max_age=20.0) -> tuple:
        """(ok, detail). ok = the server is running, the extension is connected and the
        owner has clicked Allow. Cached briefly."""
        st = self._status
        if st and time.time() - st[2] < max_age:
            return st[0], st[1]
        try:
            text, err = self.call("tab list", timeout=45)
            flat = re.sub(r"\s+", " ", text).strip()
            if err:
                ok, detail = False, flat[:300] or "Browser MCP refused the call"
            else:
                ok, detail = self._pick_browser()
        except BMCPError as e:
            ok, detail = False, str(e)
        self._status = (ok, detail, time.time())
        return ok, detail

    def _pick_browser(self) -> tuple:
        """Use the browser named in DRT_BMCP_BROWSER (e.g. brave) and no other. The extension
        can be loaded in several browsers at once; without this the tool would work in
        whichever the owner used last. Returns (ok, detail)."""
        # Default is Brave (owner rule: every browser session is Brave). DRT_BMCP_BROWSER=any
        # removes the pin; it is there for diagnosis only.
        want = os.environ.get("DRT_BMCP_BROWSER", "brave").strip().lower() or "brave"
        if want in ("any", "*"):
            return True, "connected"
        text, err = self.call("browser list", timeout=30)
        rows = re.findall(r'^\s*(\*?)\s*(\S+)\s+"([^"]*)"', text or "", re.M)
        hit = next((r for r in rows if want in r[2].lower()), None)
        if hit is None:
            names = ", ".join(r[2] for r in rows) or "none"
            return False, (f"the Browser MCP extension is not connected in {want.capitalize()} "
                           f"(connected: {names}). Load it there and click Allow")
        if not hit[0]:
            self.call(f"browser use {hit[1]}", timeout=30)
        self.browser_name = hit[2]
        return True, f"connected in {hit[2]}"

    def signed_in(self, domain: str, refresh=False):
        """True / False when this browser's sign-in state for the site can be read; None
        when it cannot (a kind of site this module does not know, or Browser MCP is
        unavailable). The answer is a yes/no only — never an account name."""
        d = site_key(domain)
        if not refresh and d in self._signed:
            return self._signed[d]
        if d == "github.com":
            before, js = ["goto https://github.com/"], _JS_SIGNED_GITHUB
        elif d == "reddit.com":
            before, js = ["goto https://old.reddit.com/"], _JS_SIGNED_REDDIT
        elif d == "x.com":
            before, js = ["goto https://x.com/home"], _JS_SIGNED_X
        else:
            before, js = [f"goto https://{d}/", "wait 600"], _JS_SIGNED_FORUM
        r = None
        for attempt in (1, 2):
            try:
                r = self._eval(js, before=before)
                break
            except BMCPError as e:
                # A background tab's navigation occasionally stalls once (seen live on
                # x.com/home: 30 s timeout, then 1 s on the next try). One retry, no more.
                if attempt == 1 and "timeout" in str(e).lower():
                    self._log(f"[login] Browser MCP timed out checking {d}; trying once more")
                    continue
                self._log(f"[login] Browser MCP could not check {d}: {e}")
                return None
        if "platform" in r:
            self._platform[d] = r.get("platform") or ""
        v = r.get("signed")
        self._signed[d] = None if v is None else bool(v)
        return self._signed[d]

    def read_page(self, url: str) -> dict:
        """{url, title, text, links} for a page, read in the everyday browser."""
        target = url
        m = re.match(r"https?://(?:www\.|new\.|np\.)?reddit\.com(/.*)?$", url or "", re.I)
        if m and self._signed.get("reddit.com"):
            target = "https://old.reddit.com" + (m.group(1) or "/")     # clean server-rendered thread
        settle = ("wait article[data-testid=tweet]" if re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/", target, re.I)
                  else "wait 800")
        r = self._eval(_JS_READ, before=[f"goto {target}", settle], timeout=120)
        text, total = r.get("text") or "", int(r.get("len") or 0)
        pos = len(text)
        while pos < min(total, _MAX_PAGE):
            part = self._eval(_JS_SLICE % (pos, pos + _SLICE)).get("text") or ""
            if not part:
                break
            text += part
            pos += len(part)
        return {"url": url if target != url else (r.get("url") or url), "title": r.get("title") or "",
                "text": text, "links": r.get("links") or [], "read_url": r.get("url") or target}

    def search(self, domain: str, query: str, limit: int = 10) -> list:
        """[{title, url, snippet, date}] from the site's own signed-in search; [] when this
        module has no routine for the site. Raises BMCPError on failure."""
        d = site_key(domain)
        q = quote_plus(query or "")
        rows = []
        if d == "x.com":
            r = self._eval(_JS_SEARCH_X, before=[
                f"goto https://x.com/search?q={q}&src=typed_query&f=top"], timeout=120)
            if r.get("bounced"):
                self._signed["x.com"] = False
                raise BMCPError("x.com is not signed in in the everyday browser")
            rows = r.get("rows") or []
        elif d not in ("github.com", "reddit.com"):
            if d not in self._platform:
                self.signed_in(d)
            if self._platform.get(d) != "discourse":
                return []                      # no routine here: the research browser searches it
            r = self._eval(_JS_SEARCH_DISCOURSE.replace("QUERY", _jsq(query)),
                           before=[f"goto https://{d}/", "wait 400"], timeout=90)
            if int(r.get("status") or 0) != 200:
                raise BMCPError(f"{d} search answered HTTP {r.get('status')}")
            rows = r.get("rows") or []
        elif d.endswith("reddit.com"):
            r = self._eval(_JS_SEARCH_REDDIT, before=[
                f"goto https://old.reddit.com/search?q={q}&sort=relevance", "wait 500"])
            if r.get("bounced"):
                self._signed["reddit.com"] = False
                raise BMCPError("reddit.com is not signed in in the everyday browser")
            rows = r.get("rows") or []
        elif d.endswith("github.com"):
            # Issues/PRs and discussions are where the candid reports are; repositories
            # come from the search API in browser.py. Two page loads per query.
            for kind in ("issues", "discussions"):
                r = self._eval(_JS_SEARCH_GITHUB, before=[
                    f"goto https://github.com/search?q={q}&type={kind}",
                    "wait 1500"], timeout=120)
                if r.get("bounced"):
                    self._signed["github.com"] = False
                    raise BMCPError("github.com is not signed in in the everyday browser")
                got = [x for x in (r.get("rows") or []) if not x.get("title", "").startswith("[repo]")]
                rows += got[:max(3, limit // 2 + 1)]
        seen, out = set(), []
        for x in rows:
            u = (x.get("url") or "").strip()
            if u.startswith("http") and u not in seen and (x.get("title") or "").strip():
                seen.add(u)
                out.append({"title": x["title"].strip(), "url": u,
                            "snippet": (x.get("snippet") or "").strip(), "date": x.get("date") or ""})
            if len(out) >= limit:
                break
        return out
