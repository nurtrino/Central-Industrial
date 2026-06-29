"""
Deep Research launcher — idempotent, server-only.

Ensures dr_server.py is running on :5006 (starts it hidden if not). Does NOT open a
browser: the Special Projects hub is the front door and navigates to the tool itself,
so opening a tab here would just duplicate it. Safe to run repeatedly.

Starts the server with the tool's OWN venv (.venv\\Scripts\\pythonw.exe) so its deps
(playwright, anthropic, cryptography, …) resolve. Invoked by "Deep Research.vbs"
(pythonw = no console) and by the hub's /api/launch on demand.
"""
import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = 5006


def is_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _server_python() -> str:
    """Prefer the tool's own venv pythonw; fall back to a venv python, then to whatever
    interpreter is running this launcher (with its pythonw sibling if present)."""
    venv_pyw = os.path.join(BASE, ".venv", "Scripts", "pythonw.exe")
    venv_py  = os.path.join(BASE, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_pyw):
        return venv_pyw
    if os.path.exists(venv_py):
        return venv_py
    py = sys.executable
    cand = os.path.join(os.path.dirname(py), "pythonw.exe")
    return cand if os.path.exists(cand) else py


def start_server():
    py = _server_python()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    out = open(os.path.join(BASE, "_srv.log"), "a", buffering=1, encoding="utf-8",
               errors="replace")
    subprocess.Popen([py, os.path.join(BASE, "dr_server.py")], cwd=BASE,
                     creationflags=flags, stdout=out, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, close_fds=True)


def main():
    if not is_up():
        start_server()
        for _ in range(80):              # wait up to ~20s for it to bind
            if is_up():
                break
            time.sleep(0.25)


if __name__ == "__main__":
    main()
