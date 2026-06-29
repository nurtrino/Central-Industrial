"""
Monkey Read Monkey Do — local transcription helper (system-tray app).

This is the small app a user downloads from notes.centralindustrial.ai. It runs a tiny
local server on 127.0.0.1:5007 that the hosted Read Monkey Do page calls to transcribe
audio on THIS machine's GPU — the audio never leaves the computer. It lives in the
system tray (right-click for status / open the app / start-on-login / quit) and can
launch on login.

Run from source:  ./.venv/Scripts/python.exe tray_app.py
Packaged:         ReadMonkeyDoWorker.exe   (see build_exe.bat)
"""
import os
import sys
import threading
import webbrowser

# Configure the local-only server BEFORE importing the worker app.
os.environ.setdefault("MRMD_ALLOWED_ORIGIN", "https://notes.centralindustrial.ai")
os.environ.setdefault("PORT", "5007")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import worker_server as W   # noqa: E402  (Flask app + transcription pipeline)

HOSTED_URL = os.environ.get("MRMD_HOSTED_URL", "https://notes.centralindustrial.ai")
PORT = int(os.environ.get("PORT", "5007"))
APP_NAME = "ReadMonkeyDoWorker"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ── start-on-login (Windows registry Run key) ────────────────────────────────
def _launch_command():
    exe = sys.executable
    if getattr(sys, "frozen", False):          # packaged .exe
        return f'"{exe}"'
    return f'"{exe}" "{os.path.abspath(__file__)}"'   # running from source


def autostart_enabled():
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
        try:
            winreg.QueryValueEx(k, APP_NAME)
            return True
        finally:
            winreg.CloseKey(k)
    except OSError:
        return False


def set_autostart(enable):
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        if enable:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
    finally:
        winreg.CloseKey(k)


# ── tray icon ────────────────────────────────────────────────────────────────
def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), (4, 16, 10))      # phosphor-terminal black-green
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, 57, 57], outline=(59, 232, 89), width=3)
    d.text((17, 22), "RM", fill=(116, 251, 128))
    return img


def _status_line(_item=None):
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            model, _ = W.pick_model_for_vram()
            return f"GPU: {name} · model {model}"
        return "No CUDA GPU detected — transcription will fail"
    except Exception:
        return f"Worker on 127.0.0.1:{PORT}"


def run_server():
    # 127.0.0.1 only: not reachable from the network, just this machine's browser.
    W.app.run(host="127.0.0.1", port=PORT, threaded=True)


def main():
    threading.Thread(target=run_server, daemon=True).start()

    import pystray
    from pystray import MenuItem as Item

    def on_open(icon, item):
        webbrowser.open(HOSTED_URL)

    def on_toggle(icon, item):
        set_autostart(not autostart_enabled())
        icon.update_menu()

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        Item(_status_line, None, enabled=False),
        pystray.Menu.SEPARATOR,
        Item("Open Read Monkey Do", on_open, default=True),
        Item("Start on login", on_toggle, checked=lambda i: autostart_enabled()),
        Item("Quit", on_quit),
    )
    # First run: opt into start-on-login (user can untick it from the menu).
    try:
        if not autostart_enabled():
            set_autostart(True)
    except OSError:
        pass

    pystray.Icon(APP_NAME, _icon_image(),
                 "Read Monkey Do — local transcription", menu).run()


if __name__ == "__main__":
    main()
