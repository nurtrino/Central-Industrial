"""Check the Browser MCP bridge against your everyday Brave. Read-only.

    D:\\_______Claude\\Central-Industrial\\deep-research\\.venv\\Scripts\\python.exe bmcp_check.py

Prints, in order: whether the extension is connected and allowed (and, if it is waiting
for you to click Allow, the six-digit code to compare with the extension's popup); for
GitHub and Reddit whether your everyday Brave is signed in (yes/no only); one sample
search on each; and one page read. It opens one background tab and closes it again.
Add --wait to keep asking for up to ten minutes while you click Allow."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
os.environ["DRT_BMCP"] = "1"
from engines.research import bmcp  # noqa: E402

wait = "--wait" in sys.argv
query = next((a for a in sys.argv[1:] if not a.startswith("--")), "XVF3800 esphome")
b = bmcp.BrowserMCP(log=print)
code = 1
try:
    t_end = time.time() + (600 if wait else 0)       # ten minutes to find the popup and click Allow
    while True:
        ok, detail = b.status(max_age=0)
        if ok or time.time() >= t_end:
            break
        print("not ready:", detail)
        time.sleep(4)
    print("extension connected and allowed:", "YES" if ok else "NO")
    if not ok:
        print("  ->", detail)
        sys.exit(2)
    for dom in bmcp.routed_domains():
        t0 = time.time()
        s = b.signed_in(dom, refresh=True)
        print(f"{dom}: signed in in your everyday Brave: "
              f"{'YES' if s is True else 'NO' if s is False else 'could not tell'}  ({time.time() - t0:.1f}s)")
        if s is not True:
            continue
        try:
            t0 = time.time()
            rows = b.search(dom, query, limit=10)
            print(f"  search {query!r}: {len(rows)} results ({time.time() - t0:.1f}s)")
            for r in rows[:4]:
                print(f"    - {r['title'][:95]}  {r['date']}\n      {r['url'][:100]}")
            if rows:
                t0 = time.time()
                pg = b.read_page(rows[0]["url"])
                print(f"  read {rows[0]['url'][:80]}: {len(pg['text'])} chars, {len(pg['links'])} links "
                      f"({time.time() - t0:.1f}s)")
                print("    " + pg["text"][:220].replace("\n", " | "))
        except bmcp.BMCPError as e:
            print("  FAILED:", e)
    code = 0
finally:
    b.close()
print("done." if code == 0 else "stopped.")
sys.exit(code)
