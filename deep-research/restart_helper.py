"""Detached restart helper for dr_server (the Deep Research standalone).

Launched (detached) by /api/restart. Waits for the old server to release port 5006,
force-frees it if a busy job kept it bound, then relaunches dr_server.py and verifies
it binds the port (retrying a couple of times). Running detached is what lets the
server restart itself. All actions log to _restart.log; the relaunched server's
stdout/stderr go to _srv.log so a startup failure is visible, not swallowed by pythonw.
"""
import os
import sys
import time
import socket
import subprocess

PORT = 5006
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "dr_server.py")
HELPER_LOG = os.path.join(HERE, "_restart.log")
SERVER_LOG = os.path.join(HERE, "_srv.log")

CREATE_NO_WINDOW = 0x08000000
PORT_FREE_WAIT = 25
UP_TIMEOUT = 35
MAX_ATTEMPTS = 3


def _log(msg: str) -> None:
    try:
        with open(HELPER_LOG, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _env_for_relaunch() -> dict:
    """Drop every key defined in .env so the fresh process reloads them from .env
    (otherwise a stale inherited value would win, since _load_dotenv only fills empty
    vars). Makes .env authoritative across restarts."""
    env = os.environ.copy()
    envfile = os.path.join(HERE, ".env")
    try:
        with open(envfile, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k = line.split("=", 1)[0].strip()
                if k:
                    env.pop(k, None)
    except Exception:
        pass
    return env


def _port_in_use() -> bool:
    c = socket.socket()
    c.settimeout(0.3)
    try:
        c.connect(("127.0.0.1", PORT))
        c.close()
        return True
    except OSError:
        try:
            c.close()
        except OSError:
            pass
        return False


def _free_port() -> None:
    """Force-free PORT by terminating whatever is LISTENING on it (a stale server that
    failed to exit because it was busy on a job). A restart must cleanly REPLACE the old
    instance — never leave two servers bound to the same port."""
    try:
        import psutil
    except Exception:
        _log("psutil unavailable — cannot force-free the port")
        return
    me = os.getpid()
    victims = []
    try:
        for c in psutil.net_connections(kind="inet"):
            try:
                if (c.laddr and c.laddr.port == PORT and c.status == psutil.CONN_LISTEN
                        and c.pid and c.pid != me):
                    victims.append(c.pid)
            except Exception:
                continue
    except Exception as e:  # noqa: BLE001
        _log(f"could not enumerate port owners: {type(e).__name__}")
        return
    for pid in set(victims):
        try:
            psutil.Process(pid).terminate()
            _log(f"terminated stale server pid {pid} holding port {PORT}")
        except Exception:
            pass
    time.sleep(1.5)
    for pid in set(victims):
        try:
            p = psutil.Process(pid)
            if p.is_running():
                p.kill()
                _log(f"hard-killed stale server pid {pid}")
        except Exception:
            pass


def _launch():
    exe = sys.executable or "python"
    out = open(SERVER_LOG, "a", encoding="utf-8", errors="replace")
    out.write(f"\n===== relaunch {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    out.flush()
    return subprocess.Popen(
        [exe, SERVER], cwd=HERE, env=_env_for_relaunch(),
        stdout=out, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)


def _wait_until_up(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_in_use():
            return True
        time.sleep(0.4)
    return False


def main() -> None:
    _log("restart helper started")
    for _ in range(int(PORT_FREE_WAIT / 0.25)):
        if not _port_in_use():
            break
        time.sleep(0.25)
    if _port_in_use():
        _log(f"port {PORT} still in use after {PORT_FREE_WAIT}s; force-freeing it")
        _free_port()
        for _ in range(12):
            if not _port_in_use():
                break
            time.sleep(0.25)
    if _port_in_use():
        _log(f"WARNING: port {PORT} STILL in use after force-free; launching anyway")
    else:
        _log("port is free; launching new server")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proc = _launch()
        _log(f"attempt {attempt}/{MAX_ATTEMPTS}: launched pid {proc.pid}; waiting up to {UP_TIMEOUT}s")
        if _wait_until_up(UP_TIMEOUT):
            _log(f"OK: server is up on attempt {attempt} (pid {proc.pid})")
            return
        rc = proc.poll()
        _log(f"attempt {attempt} FAILED: not bound within {UP_TIMEOUT}s "
             f"(exit={rc!r}; see _srv.log for any traceback)")
        if rc is None:
            try:
                proc.kill()
            except Exception:
                pass
        time.sleep(1.0)
    _log(f"ERROR: server did not come up after {MAX_ATTEMPTS} attempts — see _srv.log")


if __name__ == "__main__":
    main()
