"""
Monkey Read Monkey Do launcher — idempotent.

Ensures the Monkey Read Monkey Do server is running (starts it hidden if not) and opens the
page in the browser. Safe to run repeatedly: if the server is already up it just
opens the browser, never a second server.

Invoked by "Monkey Read Monkey Do.vbs" (which runs it with pythonw = no console window), but
can also be run directly:  .venv/Scripts/python.exe launch.py
"""

import os
import socket
import subprocess
import sys
import time
import webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = 5005
URL = f"http://127.0.0.1:{PORT}"


def is_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_server():
    # pythonw.exe + CREATE_NO_WINDOW -> the server runs with NO window at all.
    # (DETACHED_PROCESS would pop an empty console window on a GUI interpreter,
    # and it can't be combined with CREATE_NO_WINDOW.)
    pyw = os.path.join(BASE, ".venv", "Scripts", "pythonw.exe")
    py = pyw if os.path.exists(pyw) else sys.executable
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    out = open(os.path.join(BASE, "server.log"), "a", buffering=1)
    subprocess.Popen([py, os.path.join(BASE, "app.py")], cwd=BASE,
                     creationflags=flags, stdout=out, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, close_fds=True)


def main():
    if not is_up():
        start_server()
        for _ in range(120):              # wait up to ~60s for it to bind
            if is_up():
                break
            time.sleep(0.5)
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
