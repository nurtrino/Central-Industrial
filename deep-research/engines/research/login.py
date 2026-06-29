"""
DRT login layer — vault → pause.

When the visible Chrome hits a login / paywall / "are you human" wall on a site
the dedicated profile isn't logged into yet, we:
  1. try a stored credential (local ENCRYPTED vault), auto-filling the form; else
  2. PAUSE — surface the tab and let the user log in by hand (handles 2FA/captcha),
     then resume. The persistent profile means this is a one-time cost per site.

The vault is a Fernet-encrypted JSON blob on disk; the key lives in a sibling
key file (or the DRT_VAULT_KEY env var). Nothing here is ever committed.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from cryptography.fernet import Fernet

_CFG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "config")
_VAULT_PATH = os.path.join(_CFG_DIR, "drt_credentials.enc")
_KEY_PATH = os.path.join(_CFG_DIR, ".drt_vault_key")


# ── login-wall detection ──────────────────────────────────────
_WALL_KEYWORDS = (
    "log in to continue", "sign in to continue", "please log in", "please sign in",
    "you must be logged in", "log in or sign up", "create a free account",
    "sign up to continue", "subscribe to read", "subscribers only",
    "this content is for", "members only", "verify you are human", "are you a robot",
    "press & hold", "enable javascript and cookies", "access denied",
    "you've been blocked", "unusual traffic", "complete the security check",
)
_AUTH_URL_HINTS = ("/login", "/signin", "/sign-in", "/sign_in", "/auth",
                   "accounts.google", "/u/login", "oauth", "/account/login")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def normalize_domain(value: str) -> str:
    """Reduce a user-entered domain/URL to a bare registrable host key.

    'https://www.AVForums.com/forums/' -> 'avforums.com'; 'reddit.com' stays.
    Vault and source keys are stored normalized so host-based lookup in get()
    (which matches by hostname suffix) always resolves — a full URL stored as a
    key is otherwise unreachable.
    """
    d = (value or "").strip().lower().rstrip("/")
    if not d:
        return ""
    host = host_of(d if "://" in d else "http://" + d) or d.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def detect_login_wall(page, text: str) -> tuple[bool, str]:
    """Heuristic: is this page gating content behind a login/paywall/bot check?"""
    t = (text or "").lower()
    url = ""
    try:
        url = (page.url or "").lower()
    except Exception:
        pass
    if any(h in url for h in _AUTH_URL_HINTS):
        return True, "auth url"
    try:
        has_pw = page.locator("input[type=password]").count() > 0
    except Exception:
        has_pw = False
    kw = next((k for k in _WALL_KEYWORDS if k in t), None)
    short = len(t) < 700
    if has_pw and (short or kw):
        return True, f"password field ({kw or 'short page'})"
    if short and kw:
        return True, f"keyword: {kw}"
    return False, ""


# ── credential vault (encrypted) ──────────────────────────────
class CredentialVault:
    def __init__(self, path: str = _VAULT_PATH, key_path: str = _KEY_PATH):
        self.path = path
        self.key_path = key_path

    def _fernet(self) -> Fernet:
        key = os.environ.get("DRT_VAULT_KEY", "").strip()
        if key:
            return Fernet(key.encode())
        if not os.path.exists(self.key_path):
            os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
            with open(self.key_path, "wb") as fh:
                fh.write(Fernet.generate_key())
            try:
                os.chmod(self.key_path, 0o600)
            except Exception:
                pass
        with open(self.key_path, "rb") as fh:
            return Fernet(fh.read().strip())

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "rb") as fh:
                raw = self._fernet().decrypt(fh.read())
            return json.loads(raw.decode()).get("sites", {})
        except Exception:
            return {}

    def save(self, sites: dict):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        blob = json.dumps({"sites": sites}).encode()
        with open(self.path, "wb") as fh:
            fh.write(self._fernet().encrypt(blob))
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def get(self, url_or_domain: str) -> dict | None:
        """Match by domain suffix, so www.reddit.com matches a 'reddit.com' entry."""
        host = host_of(url_or_domain) or url_or_domain.lower()
        sites = self.load()
        if host in sites:
            return sites[host]
        for dom, creds in sites.items():
            if host == dom or host.endswith("." + dom):
                return creds
        return None

    def set(self, domain: str, username: str, password: str, login_url: str = "",
            username_sel: str = "", password_sel: str = "", submit_sel: str = ""):
        sites = self.load()
        sites[normalize_domain(domain)] = {
            "login_url": login_url, "username": username, "password": password,
            "username_sel": username_sel, "password_sel": password_sel,
            "submit_sel": submit_sel,
        }
        self.save(sites)

    def domains(self) -> list[str]:
        return sorted(self.load().keys())


# ── auto-fill ─────────────────────────────────────────────────
_USER_SELS = ("input[autocomplete='username']", "input[type='email']",
              "input[name*='user' i]", "input[name*='email' i]",
              "input[id*='user' i]", "input[id*='email' i]", "input[type='text']")
_PASS_SELS = ("input[type='password']",)
_SUBMIT_SELS = ("button[type='submit']", "input[type='submit']",
                "button:has-text('Log in')", "button:has-text('Sign in')",
                "button:has-text('Log In')", "button:has-text('Continue')")


def _fill_first(page, selectors, value) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.fill(value, timeout=3000)
                return True
        except Exception:
            continue
    return False


def _click_first(page, selectors) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def try_autofill(page, creds: dict, log=None) -> bool:
    """Attempt a vault login on `page`. Returns True if the wall appears cleared."""
    log = log or (lambda m: None)
    try:
        if creds.get("login_url"):
            try:
                page.goto(creds["login_url"], wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(800)
            except Exception:
                pass
        u_sels = ([creds["username_sel"]] if creds.get("username_sel") else []) + list(_USER_SELS)
        p_sels = ([creds["password_sel"]] if creds.get("password_sel") else []) + list(_PASS_SELS)
        s_sels = ([creds["submit_sel"]] if creds.get("submit_sel") else []) + list(_SUBMIT_SELS)
        if not _fill_first(page, u_sels, creds.get("username", "")):
            log("[login] could not find username field")
            return False
        _fill_first(page, p_sels, creds.get("password", ""))
        if not _click_first(page, s_sels):
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        # crude success check: a password field no longer dominates the view
        try:
            still = page.locator("input[type=password]").count() > 0
        except Exception:
            still = False
        ok = not still
        log(f"[login] vault autofill {'OK' if ok else 'uncertain'}")
        return ok
    except Exception as e:  # noqa: BLE001
        log(f"[login] autofill error: {type(e).__name__}: {e}")
        return False
