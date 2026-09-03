"""
Central Industrial — LOCAL hub launcher (idempotent).

Starts hub_server.py in LOCAL mode (CI_LOCAL=1) hidden if it isn't already serving the
single local port, waits for it, then opens the C64 screen. The hub in turn starts and
supervises every local tool (see tools.json), so this is the ONE thing to run.

    pythonw launch_local.py             start (if needed) + open the browser
    pythonw launch_local.py --startup   start quietly (Windows login), no browser tab

Invoked by "Central Industrial.vbs" (pythonw = no console window).
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("HUB_PORT", "5050"))
URL = f"http://127.0.0.1:{PORT}/"


def is_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_server():
    py = sys.executable
    cand = os.path.join(os.path.dirname(py), "pythonw.exe")   # no console window
    if os.path.exists(cand):
        py = cand
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    env = dict(os.environ, CI_LOCAL="1", HUB_PORT=str(PORT), PYTHONUNBUFFERED="1")
    out = open(os.path.join(BASE, "hub.log"), "a", buffering=1, encoding="utf-8",
               errors="replace")
    subprocess.Popen([py, os.path.join(BASE, "hub_server.py")], cwd=BASE, env=env,
                     creationflags=flags, stdout=out, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, close_fds=True)


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if not is_up():
        start_server()
        for _ in range(60):              # wait up to ~15s for it to bind
            if is_up():
                break
            time.sleep(0.25)
    if any("startup" in a or "no-browser" in a for a in args):
        return
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
