"""
Deep Research — standalone local server.

Extracted from the DDDD due-diligence platform's perf_server.py (port 5002) so the
Deep Research Tool (DRT) lives on its own, as a "Special Projects" tool. Unlike the
old setup (GitHub-Pages HTTPS frontend → localhost backend, hence CORS), this server
SERVES ITS OWN UI at / and exposes the DRT API on the SAME origin — like the hub and
Monkey Read Monkey Do. Self-contained: imports engines.research.* (copied verbatim);
config/, prompts/, and the encrypted vault sit beside this file so the engine's
relative path math (dirname×3 → repo root) resolves unchanged.

PORT : 5006
Start: python dr_server.py   (or Deep Research.vbs / the hub's "launch" on demand)
"""

import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
import traceback

from flask import Flask, jsonify, request, send_from_directory

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
_PROMPTS_DIR = os.path.join(_ROOT, "prompts")

# PORT: Render injects $PORT at runtime; default 5006 for local runs.
PORT = int(os.environ.get("PORT", "5006"))


# ── Load .env at startup so the server works when launched via pythonw / the hub
# (no shell environment). The guard only sets a key that is MISSING or EMPTY —
# never overwrite a real value (Claude Code injects an empty key into subprocesses;
# this prevents that from clobbering the real ANTHROPIC_API_KEY). Do not revert this.
def _load_dotenv():
    env_path = os.path.join(_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and not os.environ.get(key, "").strip():  # set if missing or empty
                os.environ[key] = val

_load_dotenv()

app = Flask(__name__)


# ── Headers: same-origin now, but keep CORS permissive (harmless) and force
# no-store so the served page is never stale — mirrors the hub.
@app.after_request
def _headers(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Expires"] = "0"
    return resp


# ── UI (served from disk on every request) ───────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(_ROOT, "index.html")


@app.route("/vendor/<path:fn>")
def vendor(fn):
    return send_from_directory(os.path.join(_ROOT, "vendor"), fn)


@app.route("/fonts/<path:fn>")
def fonts(fn):
    return send_from_directory(os.path.join(_ROOT, "fonts"), fn)


@app.route("/vvd-egg.mp3")
def vvd_egg():
    # little easter egg — plays when the Very Very Deep sidebar button is pressed
    return send_from_directory(_ROOT, "Boonies Basement Tub (128kbit_AAC)-2.mp3")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "tool": "deep-research"})


@app.route("/api/firecrawl/<path:fcpath>", methods=["GET", "POST", "OPTIONS"])
def firecrawl_proxy(fcpath):
    """Proxy for the Firecrawl sub-tool: forwards to https://api.firecrawl.dev/<path>,
    injecting the key server-side (never exposed to the browser). Pass-through JSON + status."""
    if request.method == "OPTIONS":
        return "", 204
    key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not key:
        return jsonify({"error": "FIRECRAWL_API_KEY is not configured — add it to .env and restart."}), 400
    import requests
    url = "https://api.firecrawl.dev/" + fcpath.lstrip("/")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        if request.method == "POST":
            r = requests.post(url, headers=headers, json=(request.get_json(silent=True) or {}), timeout=300)
        else:
            r = requests.get(url, headers=headers, params=request.args, timeout=300)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not reach Firecrawl: {e}"}), 502
    try:
        return jsonify(r.json()), r.status_code
    except Exception:
        return (r.text, r.status_code, {"Content-Type": r.headers.get("Content-Type", "text/plain")})


@app.route("/api/restart", methods=["POST", "OPTIONS"])
def restart_endpoint():
    """Restart the server (re-reads .env + config/drt_models.json). Spawns a detached
    helper that waits for this process to free the port, relaunches, then this exits."""
    if request.method == "OPTIONS":
        return "", 204
    import time as _time
    import subprocess
    helper = os.path.join(_ROOT, "restart_helper.py")
    DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([sys.executable, helper], cwd=_ROOT,
                         creationflags=DETACHED, close_fds=True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    def _bye():
        _time.sleep(0.8)
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()
    return jsonify({"ok": True, "message": "Server restarting…"})


# ── Save a generated report (.docx) to disk + open it in Word ────────────────
# Where "Save & open in Word" writes .docx files. Configurable via DRT_REPORTS_DIR;
# defaults to a folder beside the app so it is cross-platform. On Render's ephemeral
# filesystem this still works for the session — the UI also offers a direct download.
_REPORTS_DIR = os.environ.get("DRT_REPORTS_DIR", os.path.join(_ROOT, "reports"))
_FN_STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "what",
                 "is", "are", "how", "who", "whom", "does", "do", "did", "about", "any",
                 "with", "into", "at", "by", "from", "vs", "re", "&"}


def _slug_from_query(q: str) -> str:
    """A short, human-readable keyword slug drawn from the research topic."""
    words = re.findall(r"[A-Za-z0-9]+", q or "")
    keep = [w for w in words if w.lower() not in _FN_STOPWORDS and len(w) > 1]
    keep = keep[:6] or words[:4]
    return (" ".join(keep).strip())[:70].strip() or "report"


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:140] or "report"


@app.route("/api/save_report", methods=["POST", "OPTIONS"])
def save_report():
    r"""Save a generated .docx to D:\______Documents\___Deep Research Reports with a
    topic-keyword filename, then open it in Word. Body: {docx_b64, query, label, open?}.
    Returns {ok, path, filename, opened}. (open=false skips the Word launch.)"""
    if request.method == "OPTIONS":
        return "", 204
    import time as _time
    data = request.get_json(silent=True) or {}
    b64 = data.get("docx_b64") or ""
    query = (data.get("query") or "").strip()
    label = (data.get("label") or "Deep Research").strip()
    do_open = data.get("open", True)
    if not b64:
        return jsonify({"error": "no document to save"}), 400
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return jsonify({"error": "document data was not valid base64"}), 400
    try:
        os.makedirs(_REPORTS_DIR, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not create the reports folder: {e}"}), 500
    stamp = _time.strftime("%Y-%m-%d_%H%M%S")
    fname = _safe_filename(f"{label} — {_slug_from_query(query)} — {stamp}") + ".docx"
    path = os.path.join(_REPORTS_DIR, fname)
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not save the file: {e}"}), 500
    opened = False
    if do_open:
        try:
            os.startfile(path)   # Windows: opens in the registered .docx app (Word)
            opened = True
        except Exception:
            opened = False
    return jsonify({"ok": True, "path": path, "filename": fname, "opened": opened})


# ── Async job registry (standalone — no longer shared with other tools) ──────
_JOBS = {}
_JOBS_LOCK = threading.Lock()


# ── Shared helpers carried from perf_server.py ───────────────────────────────
def _extract_file_text(path: str, filename: str) -> str:
    """Extract plain text from a supporting document, by extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext == "pdf":
        import pdfplumber
        pages = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
        return "\n\n".join(pages)

    elif ext in ("pptx", "ppt"):
        from pptx import Presentation
        prs = Presentation(path)
        slides = []
        for i, slide in enumerate(prs.slides, 1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        line = " ".join(run.text for run in para.runs).strip()
                        if line:
                            texts.append(line)
            if texts:
                slides.append(f"[Slide {i}]\n" + "\n".join(texts))
        return "\n\n".join(slides)

    elif ext in ("docx", "doc"):
        import mammoth
        with open(path, "rb") as fh:
            result = mammoth.extract_raw_text(fh)
        return result.value

    elif ext in ("xlsx", "xls"):
        import pandas as pd
        sheets = []
        with pd.ExcelFile(path) as xf:
            for sname in xf.sheet_names:
                df = xf.parse(sname, header=None)
                sheets.append(f"[Sheet: {sname}]\n{df.to_string(index=False)}")
        return "\n\n".join(sheets)

    elif ext in ("csv",):
        import pandas as pd
        df = pd.read_csv(path)
        return df.to_string(index=False)

    else:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()


def _backup_then_write(path, content):
    """Write `content` to `path`, keeping a rolling single .bak of the prior version."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as _src, \
                 open(path + ".bak", "w", encoding="utf-8") as _dst:
                _dst.write(_src.read())
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _memo_to_docx_bytes(memo_md: str, manager_name: str, images: dict = None) -> bytes:
    """Convert markdown to a .docx (returned as bytes). Renders GitHub-flavored pipe
    tables as real Word tables, plus headings, bullets, rules, and inline **bold**/*italic*."""
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    for section in doc.sections:
        section.top_margin    = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin   = Inches(0.5)
        section.right_margin  = Inches(0.5)

    HEADER_BG = "1A1A2E"
    _INLINE = re.compile(r"(\*\*.+?\*\*|__.+?__|\*.+?\*|_.+?_)")

    def _add_runs(paragraph, text: str):
        pos = 0
        for m in _INLINE.finditer(text):
            if m.start() > pos:
                paragraph.add_run(text[pos:m.start()])
            tok = m.group(0)
            if tok.startswith("**") or tok.startswith("__"):
                paragraph.add_run(tok[2:-2]).bold = True
            else:
                paragraph.add_run(tok[1:-1]).italic = True
            pos = m.end()
        if pos < len(text):
            paragraph.add_run(text[pos:])

    def _add_heading(text: str, level: int):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(13 if level == 1 else 11.5 if level == 2 else 11)
        run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)
        p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
        p.paragraph_format.space_after  = Pt(2)

    def _add_body(text: str):
        p = doc.add_paragraph()
        _add_runs(p, text)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after  = Pt(4)

    def _add_bullet(text: str):
        p = doc.add_paragraph(style="List Bullet")
        _add_runs(p, text)
        p.paragraph_format.space_after = Pt(2)

    def _split_row(line: str):
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    def _is_sep_row(line: str) -> bool:
        cells = _split_row(line)
        if not cells:
            return False
        saw_dash = False
        for c in cells:
            cc = c.strip()
            if cc == "":
                continue
            if set(cc) <= set("-:") and "-" in cc:
                saw_dash = True
            else:
                return False
        return saw_dash

    def _set_cell_bg(cell, hex_color: str):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_color)
        tcPr.append(shd)

    def _add_table(rows):
        ncols = max(len(r) for r in rows)
        tbl = doc.add_table(rows=0, cols=ncols)
        try:
            tbl.style = "Table Grid"
        except KeyError:
            pass
        tbl.autofit = True
        for ri, row in enumerate(rows):
            cells = tbl.add_row().cells
            for ci in range(ncols):
                val  = row[ci] if ci < len(row) else ""
                cell = cells[ci]
                cell.text = ""
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.space_after  = Pt(1)
                _add_runs(p, val)
                if ri == 0:
                    _set_cell_bg(cell, HEADER_BG)
                    for r in p.runs:
                        r.bold = True
                        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                        r.font.size = Pt(10)
                else:
                    for r in p.runs:
                        r.font.size = Pt(10)
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(2)

    lines = memo_md.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if "|" in stripped and i + 1 < n and _is_sep_row(lines[i + 1]):
            rows = [_split_row(lines[i])]
            j = i + 2
            while j < n and "|" in lines[j] and lines[j].strip().startswith("|"):
                rows.append(_split_row(lines[j]))
                j += 1
            _add_table(rows)
            i = j
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            i += 1
            continue
        m_img = re.fullmatch(r"\{\{IMG:(.+?)\}\}", stripped)
        if m_img and images and m_img.group(1) in images:
            try:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(
                    io.BytesIO(images[m_img.group(1)]["bytes"]), width=Inches(5.5)
                )
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(4)
            except Exception:
                pass
            i += 1
            continue
        if stripped.startswith("### "):
            _add_heading(stripped[4:], 3)
        elif stripped.startswith("## "):
            _add_heading(stripped[3:], 2)
        elif stripped.startswith("# "):
            _add_heading(stripped[2:], 1)
        elif stripped.startswith(("- ", "* ", "• ")):
            _add_bullet(stripped[2:])
        elif re.match(r"^\d+\. ", stripped):
            _add_bullet(re.sub(r"^\d+\. ", "", stripped))
        elif stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
            _add_heading(stripped.strip("*"), 2)
        elif stripped.startswith(">"):
            q = re.sub(r"^(\s*>+\s?)+", "", stripped)
            p = doc.add_paragraph()
            _add_runs(p, q)
            p.paragraph_format.left_indent = Inches(0.25)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after  = Pt(4)
        else:
            _add_body(stripped)
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════
#  DEEP RESEARCH (DRT) — browser-driven, agentic web research
# ══════════════════════════════════════════════════════════════

DR_STAGES = ["stage1", "stage2", "stage3", "stage4", "synthesize", "report"]
_DR_EVENTS = {}     # job_id -> threading.Event (for batched Stage-4 credential prompt)
_DR_SKIP = {}       # job_id -> threading.Event (user "skip this stage" signal)
_DR_SOURCES_PATH = os.path.join(_ROOT, "config", "drt_sources.json")

_MEMO_WORD_CAP = 6000
_DOC_WORD_CAP = 50000


def _truncate_words(text: str, max_words: int) -> str:
    """Trim to the first `max_words` words, preserving formatting up to the cut."""
    if not text:
        return text
    count = 0
    for m in re.finditer(r"\S+", text):
        count += 1
        if count >= max_words:
            end = m.end()
            if end >= len(text.rstrip()):
                return text
            return text[:end] + f"\n\n[… truncated at {max_words:,} words]"
    return text


def _dr_clarify(query):
    """Quick scoping pass: up to 3 clarifying questions, or none."""
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"needs_clarification": False, "questions": []}
    client = anthropic.Anthropic(api_key=api_key)
    sys_p = ("You scope research questions for a deep web-research tool. "
             "If the question is already specific enough to research well, set "
             "needs_clarification false. Otherwise give up to 3 SHORT clarifying questions "
             "that would most sharpen the search (scope, entity, timeframe, angle). "
             "Respond with ONLY a JSON object: "
             '{"needs_clarification": boolean, "questions": ["...", "..."]}')
    r = client.messages.create(model="claude-sonnet-4-6", max_tokens=400,
                               system=sys_p, messages=[{"role": "user", "content": query}])
    txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", txt, re.S)
    data = json.loads(m.group(0)) if m else {}
    qs = [q for q in data.get("questions", []) if isinstance(q, str) and q.strip()][:3]
    return {"needs_clarification": bool(data.get("needs_clarification") and qs), "questions": qs}


def _dr_harvest_to_md(query, h):
    out = [f"# Research audit — sources read\n",
           f"**Question:** {query}\n",
           "> Audit trail for the synthesized report above: exactly what the 4-stage pipeline "
           "searched, read, and surfaced. Use it to trace any claim back to its page.\n",
           f"**Coverage:** {h.pages_used} pages opened from {h.searches_used} browser searches · "
           f"{len(h.items)} items harvested.\n"]
    plan = getattr(h, "plan", None)
    if isinstance(plan, dict) and plan:
        def _w(k):
            ch = plan.get(k, {});
            return f"{int(round(ch.get('weight', 0) * 100))}%" if ch.get("use", True) and ch.get("weight", 0) > 0 else "off"
        emph = plan.get("site_queries", {}).get("emphasis", [])
        et = f", favoring {', '.join(emph)}" if emph else ""
        ns = "on" if plan.get("neural_search", {}).get("use") else "off"
        out.append(
            f"**Research plan** ({'planner' if plan.get('_planned') else 'default'}): "
            f"Claude API {_w('api_search')} · open engines {_w('web_engines')} · "
            f"curated sites {_w('site_queries')} · neural (Exa) {ns}{et}. "
            f"_{plan.get('rationale', '').strip()}_\n")
    try:
        from engines.research.models import all_models
        mm = all_models()
        out.append(
            f"**Models:** plan `{mm['plan']}` · search `{mm['search']}` · route `{mm['route']}` · "
            f"extract `{mm['extract']}` · synthesize `{mm['synthesize']}`\n")
    except Exception:
        pass
    if getattr(h, "curated_searched", None):
        out.append(f"**Curated sites searched ({len(h.curated_searched)}):** "
                   f"{', '.join(h.curated_searched)}\n")
    if getattr(h, "exa_searches", 0) or getattr(h, "exa_similar", 0):
        out.append(f"**Neural search (Exa):** {h.exa_searches} queries · "
                   f"{h.exa_similar} find-similar · "
                   f"{len(getattr(h, 'exa_urls', []))} URLs surfaced\n")
    if getattr(h, "logged_in", None):
        out.append(f"**Logged in this run:** {', '.join(h.logged_in)}\n")
    if getattr(h, "login_warnings", None):
        out.append("**⚠ Stored-login warnings:** "
                   + "; ".join(f"{w.get('domain')} — {w.get('detail')}" for w in h.login_warnings)
                   + "\n")
    if getattr(h, "skipped_gated", None):
        out.append(f"**Gated sources skipped** (login failed / needed 2FA): "
                   f"{', '.join(h.skipped_gated)}\n")
    if getattr(h, "gated_candidates", None):
        out.append(f"**Gated sources not pursued:** {', '.join(h.gated_candidates)}\n")
    if h.stopped_reason:
        out.append(f"**Stop reason:** {h.stopped_reason}\n")

    stage1 = next((it for it in h.items if it.via == "stage1"), None)
    if stage1 and stage1.text.strip():
        out.append("\n## Stage 1 — Claude's web-search brief\n")
        out.append(stage1.text.strip())

    if h.agent_notes:
        out.append("\n## Agent notes (Stages 2–4)\n")
        out.append(h.agent_notes)

    opened = [it for it in h.items if it.via != "stage1"]
    out.append("\n## Pages read (Stages 2–4)\n")
    if not opened:
        out.append("_No pages opened — nothing of value surfaced beyond the Stage 1 brief._")
    else:
        exa_set = set(getattr(h, "exa_urls", []) or [])
        for i, it in enumerate(opened, 1):
            title = (it.title or it.url).strip()
            flag = " · ⚠ thin/screenshot" if it.used_screenshot else ""
            exa_flag = " · 🔮 via Exa" if it.url in exa_set else ""
            out.append(f"{i}. [{title}]({it.url}) — *{it.via}/{it.source_type}*, "
                       f"{len(it.text)} chars{flag}{exa_flag}")

    if getattr(h, "stage1_sources", None):
        out.append("\n## Other sources surfaced by Claude's search (not opened)\n")
        for s in h.stage1_sources[:25]:
            out.append(f"- [{(s.get('title') or s.get('url'))}]({s.get('url')})")
    return "\n".join(out)


def _dr_wrap_report(query, h, synth_md):
    """Wrap the synthesized report with the harvest audit trail (collapsed <details> for
    the web UI; a plain section for the docx)."""
    harvest_md = _dr_harvest_to_md(query, h)
    synth_md = (synth_md or "").strip()
    warn = ""
    if getattr(h, "login_warnings", None):
        lines = "; ".join(f"**{w.get('domain')}** ({w.get('detail')})" for w in h.login_warnings)
        warn = ("> ⚠ **Stored-login warning:** the tool could not complete the saved login for "
                + lines + ". Results from those sites may be limited to publicly visible content — "
                "re-check the credentials via the 🔑 lock in the Sources panel.\n\n")
    if not synth_md:
        return warn + harvest_md, warn + harvest_md
    display_md = (warn + synth_md
                  + "\n\n<details>\n<summary><strong>Research audit — what the tool read"
                    " (harvest detail)</strong></summary>\n\n"
                  + harvest_md + "\n\n</details>")
    docx_md = warn + synth_md + "\n\n---\n\n" + harvest_md
    return display_md, docx_md


def _dr_worker(job_id, query, depth, clarifications, doc_context, channel_overrides=None):
    job = _JOBS[job_id]
    ev = _DR_EVENTS[job_id]
    skip_ev = _DR_SKIP[job_id]

    def prog(stage, pct, message):
        with _JOBS_LOCK:
            job["stage"] = stage
            job["pct"] = pct
            job["message"] = message

    def request_credentials(candidates):
        ev.clear()
        with _JOBS_LOCK:
            job["awaiting_credentials"] = candidates
            job["submitted_credentials"] = None
            job["message"] = f"Waiting for credentials for {len(candidates)} gated source(s)…"
        ok = ev.wait(timeout=600)
        with _JOBS_LOCK:
            submitted = job.get("submitted_credentials") or {}
            job["awaiting_credentials"] = None
            job["submitted_credentials"] = None
        return submitted if ok else {}

    try:
        from engines.research.agent import run_search
        clar = clarifications or ""
        if doc_context:
            clar = (clar + "\n\nSUPPORTING DOCUMENT EXCERPTS (user-provided):\n"
                    + doc_context).strip()
        import anthropic
        from engines.research.browser import DRTBrowser
        from engines.research.agent import _load_governance, run_gap_round
        from engines.research.synthesize import (synthesize, classify_category,
                                                 stop_judge, gap_queries, DEEPEN_ROUNDS)
        gov = _load_governance()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        client = anthropic.Anthropic(api_key=api_key) if api_key else None

        prog("stage1", None, "Starting…")
        br = DRTBrowser(log=lambda m: None).start()
        report_md_synth = ""
        synth_error = None
        try:
            h = run_search(query, depth=depth, clarifications=clar, browser=br,
                           progress=prog, log=lambda m: None,
                           request_credentials=request_credentials, skip_event=skip_ev,
                           channel_overrides=channel_overrides)
            if client and (h.items or doc_context):
                try:
                    prog("synthesize", None, "Synthesizing the report…")
                    category = getattr(h, "category", "") or classify_category(client, query)
                    cache = {}
                    synth = synthesize(h, gov, client, progress=prog, nugget_cache=cache,
                                       category=category, user_docs=doc_context)
                    report_md_synth = synth.get("report_md", "")
                    h.category = category
                    extra = DEEPEN_ROUNDS.get(depth, 1)
                    for rnd in range(1, extra + 1):
                        if skip_ev.is_set():
                            skip_ev.clear(); break
                        stop, reason = stop_judge(client, query, report_md_synth)
                        if stop:
                            h.stopped_reason = reason or "report judged comprehensive"
                            break
                        gaps = gap_queries(client, query, report_md_synth)
                        if not gaps:
                            break
                        prog("synthesize", None, f"Deepening (round {rnd}): {gaps[0][:60]}")
                        added = run_gap_round(client, br, h, gaps, governance=gov,
                                              progress=prog, log=lambda m: None, skip_event=skip_ev)
                        if not added:
                            break
                        synth = synthesize(h, gov, client, progress=prog, nugget_cache=cache,
                                           category=category, user_docs=doc_context)
                        report_md_synth = synth.get("report_md", "")
                except Exception as e:  # noqa: BLE001 — preserve the harvest
                    synth_error = e
                    prog("synthesize", None, "Synthesis interrupted — assembling harvested findings…")
        finally:
            try:
                br.close()
            except Exception:
                pass

        if synth_error is not None:
            detail = str(synth_error).strip()
            if "credit balance is too low" in detail.lower():
                hint = ("the Anthropic API account is **out of credits** — top up at "
                        "console.anthropic.com → Plans & Billing, then re-run")
            else:
                hint = "re-run once the API is reachable"
            report_md_synth = (
                f"> ⚠️ **Report synthesis was interrupted** ({type(synth_error).__name__}); "
                f"{hint}. The web harvest completed and the gathered sources are preserved below.\n>\n"
                f"> _Detail: {detail[:300]}_\n\n"
                + (report_md_synth or "")
            )

        prog("report", None, "Assembling report…")
        report_md, docx_md = _dr_wrap_report(query, h, report_md_synth)
        try:
            docx_b64 = base64.b64encode(_memo_to_docx_bytes(docx_md, "Deep Research")).decode()
        except Exception:
            docx_b64 = ""
        sources = [{"title": it.title, "url": it.url, "type": it.source_type,
                    "chars": len(it.text)} for it in h.items if it.via != "stage1"]
        result = {"query": query, "report_md": report_md, "sources": sources,
                  "source_count": len(sources), "docx_b64": docx_b64,
                  "category": getattr(h, "category", ""),
                  "plan": getattr(h, "plan", {}),
                  "stats": {"searches": h.searches_used, "pages": h.pages_used,
                            "category": getattr(h, "category", "") or "general",
                            "stopped": h.stopped_reason}}
        with _JOBS_LOCK:
            job["result"] = result
            job["stage"] = "report"; job["pct"] = 100; job["message"] = "Done"
            job["done"] = True
    except Exception:
        with _JOBS_LOCK:
            job["error"] = traceback.format_exc()
            job["done"] = True


@app.route("/api/deep_research", methods=["POST", "OPTIONS"])
def deep_research_start():
    if request.method == "OPTIONS":
        return "", 204
    query = (request.form.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    depth = (request.form.get("depth") or "standard").strip()
    clarifications = (request.form.get("clarifications") or "").strip()

    refinement = (request.form.get("refinement") or "").strip()
    prior_report = (request.form.get("prior_report") or "").strip()
    if refinement:
        extra = ("\n\nThis is a REFINEMENT of a prior research run. Focus on the refinement; "
                 "build on what is already known and do NOT repeat it.\n"
                 f"REFINEMENT REQUEST:\n{refinement}")
        if prior_report:
            extra += f"\n\nPRIOR REPORT (context — already produced):\n{prior_report[:6000]}"
        clarifications = (clarifications + extra).strip()

    memo_filename = (request.form.get("memo_filename") or "").strip()

    channel_overrides = {}
    try:
        raw = json.loads(request.form.get("channels") or "{}")
        if isinstance(raw, dict):
            channel_overrides = {k: bool(raw[k]) for k in
                                 ("api_search", "web_engines", "site_queries", "neural_search") if k in raw}
    except Exception:
        channel_overrides = {}

    doc_parts = []
    for f in request.files.getlist("files"):
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "tmp"
        tmp = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
        try:
            f.save(tmp.name); tmp.close()
            txt = _extract_file_text(tmp.name, f.filename)
            if txt:
                cap = _MEMO_WORD_CAP if (memo_filename and f.filename == memo_filename) else _DOC_WORD_CAP
                doc_parts.append(f"[{f.filename}]\n{_truncate_words(txt, cap)}")
        except Exception as e:  # noqa: BLE001
            doc_parts.append(f"[{f.filename}] (could not read: {e})")
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    doc_context = "\n\n".join(doc_parts)

    job_id = os.urandom(8).hex()
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "stage": "stage1", "pct": None, "message": "Starting…",
            "done": False, "error": None, "result": None,
            "awaiting_credentials": None, "submitted_credentials": None,
        }
    _DR_EVENTS[job_id] = threading.Event()
    _DR_SKIP[job_id] = threading.Event()
    threading.Thread(target=_dr_worker,
                     args=(job_id, query, depth, clarifications, doc_context, channel_overrides),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "stages": DR_STAGES}), 202


@app.route("/api/deep_research/status", methods=["GET"])
def deep_research_status():
    job_id = request.args.get("job", "")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        payload = {
            "stage": job["stage"], "pct": job["pct"], "message": job["message"],
            "done": job["done"], "error": job["error"], "stages": DR_STAGES,
            "awaiting_credentials": job.get("awaiting_credentials"),
        }
        if job["done"] and not job["error"]:
            payload["result"] = job["result"]
        if job["done"]:
            _JOBS.pop(job_id, None)
            _DR_EVENTS.pop(job_id, None)
            _DR_SKIP.pop(job_id, None)
    return jsonify(payload)


@app.route("/api/deep_research/skip_stage", methods=["POST", "OPTIONS"])
def deep_research_skip_stage():
    """End the current browser stage early (Stages 2–4). The pipeline rolls forward."""
    if request.method == "OPTIONS":
        return "", 204
    job_id = request.args.get("job", "") or (request.form.get("job") or "")
    ev = _DR_SKIP.get(job_id)
    if ev:
        ev.set()
    return jsonify({"ok": True})


@app.route("/api/deep_research/credentials", methods=["POST", "OPTIONS"])
def deep_research_credentials():
    """Stage-4 credential submission (batched). Body: {credentials: {domain: {username,
    password, login_url} | null}}. Unblocks the worker; null/missing entries are skipped."""
    if request.method == "OPTIONS":
        return "", 204
    job_id = request.args.get("job", "") or (request.form.get("job") or "")
    data = request.get_json(silent=True) or {}
    creds = data.get("credentials", {}) if isinstance(data, dict) else {}
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["submitted_credentials"] = creds
    ev = _DR_EVENTS.get(job_id)
    if ev:
        ev.set()
    return jsonify({"ok": True})


@app.route("/api/deep_research/clarify", methods=["POST", "OPTIONS"])
def deep_research_clarify():
    if request.method == "OPTIONS":
        return "", 204
    query = (request.form.get("query") or "").strip()
    if not query:
        return jsonify({"needs_clarification": False, "questions": []})
    try:
        return jsonify(_dr_clarify(query))
    except Exception as e:  # noqa: BLE001
        return jsonify({"needs_clarification": False, "questions": [], "warn": str(e)})


@app.route("/api/deep_research/vault", methods=["GET", "POST", "OPTIONS"])
def deep_research_vault():
    """Manage stored credentials (encrypted local vault).
    GET -> {"domains": [...]}   POST {domain, username, password, login_url} -> store
    POST {domain, delete: true} -> remove."""
    if request.method == "OPTIONS":
        return "", 204
    from engines.research.login import CredentialVault, normalize_domain
    v = CredentialVault()
    if request.method == "GET":
        return jsonify({"domains": v.domains()})
    data = request.get_json(silent=True) or {}
    domain = normalize_domain(data.get("domain") or "")
    if not domain:
        return jsonify({"error": "domain required"}), 400
    if data.get("delete"):
        sites = v.load(); sites.pop(domain, None); v.save(sites)
        return jsonify({"ok": True, "deleted": domain, "domains": v.domains()})
    if not (data.get("username") or "").strip():
        return jsonify({"error": "username required"}), 400
    v.set(domain, data.get("username", ""), data.get("password", ""),
          login_url=data.get("login_url", ""))
    return jsonify({"ok": True, "domains": v.domains()})


@app.route("/api/deep_research/sources", methods=["GET", "POST", "OPTIONS"])
def deep_research_sources():
    if request.method == "OPTIONS":
        return "", 204
    if request.method == "GET":
        try:
            with open(_DR_SOURCES_PATH, encoding="utf-8") as fh:
                return jsonify(json.load(fh))
        except Exception:
            return jsonify({"sources": []})
    data = request.get_json(silent=True) or {}
    srcs = data.get("sources", [])
    from engines.research.login import normalize_domain
    for s in srcs:
        if isinstance(s, dict) and s.get("domain"):
            s["domain"] = normalize_domain(s["domain"])
    os.makedirs(os.path.dirname(_DR_SOURCES_PATH), exist_ok=True)
    with open(_DR_SOURCES_PATH, "w", encoding="utf-8") as fh:
        json.dump({"sources": srcs}, fh, indent=2)
    return jsonify({"ok": True, "count": len(srcs)})


@app.route("/api/deep_research/prompt", methods=["GET", "POST", "OPTIONS"])
def deep_research_prompt():
    """Read/update the governance prompt (prompts/deep_research.md). Loaded fresh at the
    start of every run, so a save applies to all future runs immediately — no restart.
    A rolling .bak is kept; a blank value is ignored so the file can't be wiped.
    GET → {prompt, name}   POST {prompt} → writes it."""
    if request.method == "OPTIONS":
        return "", 204
    path = os.path.join(_PROMPTS_DIR, "deep_research.md")
    if request.method == "GET":
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        except Exception:
            content = ""
        return jsonify({"prompt": content, "name": "deep_research.md"})
    data = request.get_json(silent=True) or {}
    val = data.get("prompt")
    if not (isinstance(val, str) and val.strip()):
        return jsonify({"ok": True, "saved": False, "message": "ignored blank prompt"})
    try:
        _backup_then_write(path, val)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"could not save: {type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, "saved": True})


# ══════════════════════════════════════════════════════════════
#  ODYSSEUS RESEARCH (sub-tool of Deep Research) — vendored comparison engine
#  Alibaba IterResearch-style loop, headless (DuckDuckGo + curl page-fetch) on
#  the same Anthropic key. Here to A/B the methodology vs the visible-Chrome DRT.
# ══════════════════════════════════════════════════════════════

def _ody_msg(ev: dict) -> str:
    ph = ev.get("phase", "")
    rnd = ev.get("round")
    if ph == "planning":
        return "Planning research strategy…"
    if ph == "searching":
        q = ev.get("query_preview")
        return f"Round {rnd}: searching" + (f" — {q}" if q else "…")
    if ph == "reading":
        t = ev.get("title") or ev.get("url")
        rp = f"Round {rnd}: " if rnd else ""
        return f"{rp}reading — {t[:70]}" if t else f"{rp}reading sources…"
    if ph == "analyzing":
        return f"Round {rnd}: synthesizing findings…"
    if ph == "writing":
        return ev.get("message") or "Writing final report…"
    if ph in ("warning", "error"):
        return ev.get("message") or ph
    return ev.get("message") or (ph or "Working…")


def _ody_worker(job_id, query, max_rounds, max_time, category):
    job = _JOBS[job_id]

    def prog(ev):
        with _JOBS_LOCK:
            job["phase"] = ev.get("phase", "")
            job["message"] = _ody_msg(ev)
            job["stats"] = {"round": ev.get("round"),
                            "sources": ev.get("total_sources"),
                            "findings": ev.get("total_findings")}

    try:
        import asyncio
        from engines.odysseus.deep_research import DeepResearcher
        with _JOBS_LOCK:
            job["message"] = "Starting Odysseus engine…"
        researcher = DeepResearcher(
            llm_endpoint="https://api.anthropic.com/v1/messages",  # ignored by our adapter
            llm_model="claude-sonnet-4-6",
            max_rounds=max_rounds,
            max_time=max_time,
            progress_callback=prog,
            category=(category or None),
        )
        report = asyncio.run(researcher.research(query))
        stats = researcher.get_stats()
        try:
            docx_b64 = base64.b64encode(
                _memo_to_docx_bytes(report or "", "Odysseus Research")).decode()
        except Exception:
            docx_b64 = ""
        result = {
            "query": query,
            "report_md": report or "",
            "docx_b64": docx_b64,
            "stats": stats,
            "sources": researcher.analyzed_urls,
            "source_count": len(researcher.urls_fetched),
        }
        with _JOBS_LOCK:
            job["result"] = result
            job["phase"] = "done"; job["message"] = "Done"; job["done"] = True
    except Exception:
        with _JOBS_LOCK:
            job["error"] = traceback.format_exc()
            job["done"] = True


@app.route("/api/odysseus_research", methods=["POST", "OPTIONS"])
def odysseus_research_start():
    if request.method == "OPTIONS":
        return "", 204
    query = (request.form.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        max_rounds = int(request.form.get("max_rounds") or 4)
    except (ValueError, TypeError):
        max_rounds = 4
    try:
        max_time = int(request.form.get("max_time") or 300)
    except (ValueError, TypeError):
        max_time = 300
    max_rounds = min(12, max(1, max_rounds))
    max_time = min(1200, max(60, max_time))
    category = (request.form.get("category") or "").strip() or None

    job_id = os.urandom(8).hex()
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "phase": "planning", "message": "Starting…",
            "stats": {}, "done": False, "error": None, "result": None,
        }
    threading.Thread(target=_ody_worker,
                     args=(job_id, query, max_rounds, max_time, category),
                     daemon=True).start()
    return jsonify({"job_id": job_id}), 202


@app.route("/api/odysseus_research/status", methods=["GET"])
def odysseus_research_status():
    job_id = request.args.get("job", "")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        payload = {
            "phase": job.get("phase"), "message": job.get("message"),
            "stats": job.get("stats", {}), "done": job["done"], "error": job["error"],
        }
        if job["done"] and not job["error"]:
            payload["result"] = job["result"]
        if job["done"]:
            _JOBS.pop(job_id, None)
    return jsonify(payload)


# ══════════════════════════════════════════════════════════════
#  VERY VERY DEEP (sub-tool) — chain Odysseus → Deep Research
#  Pass 1: a headless Odysseus pre-research pass on the question.
#  Pass 2: its findings (+ the original query + any uploaded docs) are injected as
#  grounding context into the full Deep Research pipeline, which verifies/deepens and
#  writes the final report per the Deep Research governance. Runs unattended: the DR
#  pass auto-skips NEW gated-login sources (vault Stage-3 logins still apply).
# ══════════════════════════════════════════════════════════════

def _dr_pipeline(query, depth, clarifications, doc_context, channel_overrides,
                 prog, request_credentials, skip_ev):
    """Run the Deep Research gather→synthesize pipeline and RETURN the result dict.
    Mirrors `_dr_worker`'s pipeline body but without the job-state plumbing, so it can be
    reused by the Very Very Deep chain. Raises on hard failure (the caller wraps)."""
    from engines.research.agent import run_search
    clar = clarifications or ""
    if doc_context:
        clar = (clar + "\n\nSUPPORTING DOCUMENT EXCERPTS (user-provided):\n"
                + doc_context).strip()
    import anthropic
    from engines.research.browser import DRTBrowser
    from engines.research.agent import _load_governance, run_gap_round
    from engines.research.synthesize import (synthesize, classify_category,
                                             stop_judge, gap_queries, DEEPEN_ROUNDS)
    gov = _load_governance()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    client = anthropic.Anthropic(api_key=api_key) if api_key else None

    prog("stage1", None, "Starting…")
    br = DRTBrowser(log=lambda m: None).start()
    report_md_synth = ""
    synth_error = None
    try:
        h = run_search(query, depth=depth, clarifications=clar, browser=br,
                       progress=prog, log=lambda m: None,
                       request_credentials=request_credentials, skip_event=skip_ev,
                       channel_overrides=channel_overrides)
        if client and (h.items or doc_context):
            try:
                prog("synthesize", None, "Synthesizing the report…")
                category = getattr(h, "category", "") or classify_category(client, query)
                cache = {}
                synth = synthesize(h, gov, client, progress=prog, nugget_cache=cache,
                                   category=category, user_docs=doc_context)
                report_md_synth = synth.get("report_md", "")
                h.category = category
                extra = DEEPEN_ROUNDS.get(depth, 1)
                for rnd in range(1, extra + 1):
                    if skip_ev.is_set():
                        skip_ev.clear(); break
                    stop, reason = stop_judge(client, query, report_md_synth)
                    if stop:
                        h.stopped_reason = reason or "report judged comprehensive"
                        break
                    gaps = gap_queries(client, query, report_md_synth)
                    if not gaps:
                        break
                    prog("synthesize", None, f"Deepening (round {rnd}): {gaps[0][:60]}")
                    added = run_gap_round(client, br, h, gaps, governance=gov,
                                          progress=prog, log=lambda m: None, skip_event=skip_ev)
                    if not added:
                        break
                    synth = synthesize(h, gov, client, progress=prog, nugget_cache=cache,
                                       category=category, user_docs=doc_context)
                    report_md_synth = synth.get("report_md", "")
            except Exception as e:  # noqa: BLE001 — preserve the harvest
                synth_error = e
                prog("synthesize", None, "Synthesis interrupted — assembling harvested findings…")
    finally:
        try:
            br.close()
        except Exception:
            pass

    if synth_error is not None:
        detail = str(synth_error).strip()
        if "credit balance is too low" in detail.lower():
            hint = ("the Anthropic API account is **out of credits** — top up at "
                    "console.anthropic.com → Plans & Billing, then re-run")
        else:
            hint = "re-run once the API is reachable"
        report_md_synth = (
            f"> ⚠️ **Report synthesis was interrupted** ({type(synth_error).__name__}); "
            f"{hint}. The web harvest completed and the gathered sources are preserved below.\n>\n"
            f"> _Detail: {detail[:300]}_\n\n"
            + (report_md_synth or "")
        )

    prog("report", None, "Assembling report…")
    report_md, docx_md = _dr_wrap_report(query, h, report_md_synth)
    try:
        docx_b64 = base64.b64encode(_memo_to_docx_bytes(docx_md, "Deep Research")).decode()
    except Exception:
        docx_b64 = ""
    sources = [{"title": it.title, "url": it.url, "type": it.source_type,
                "chars": len(it.text)} for it in h.items if it.via != "stage1"]
    return {"query": query, "report_md": report_md, "docx_md": docx_md, "sources": sources,
            "source_count": len(sources), "docx_b64": docx_b64,
            "category": getattr(h, "category", ""),
            "plan": getattr(h, "plan", {}),
            "stats": {"searches": h.searches_used, "pages": h.pages_used,
                      "category": getattr(h, "category", "") or "general",
                      "stopped": h.stopped_reason}}


def _vvd_worker(job_id, query, depth, channel_overrides, rounds, doc_context):
    job = _JOBS[job_id]
    skip_ev = _DR_SKIP[job_id]

    def ody_prog(ev):
        with _JOBS_LOCK:
            job["group"] = "odysseus"
            job["phase"] = ev.get("phase", "")
            job["message"] = "Pass 1 · Odysseus — " + _ody_msg(ev)
            job["stats"] = {"round": ev.get("round"),
                            "sources": ev.get("total_sources"),
                            "findings": ev.get("total_findings")}

    def dr_prog(stage, pct, message):
        with _JOBS_LOCK:
            job["group"] = "deep_research"
            job["stage"] = stage
            job["pct"] = pct
            job["message"] = message

    # Unattended chain: auto-skip the interactive Stage-4 login prompt (vault Stage-3
    # logins still apply). Use plain Deep Research when you need to log in mid-run.
    def auto_skip_credentials(candidates):
        if candidates:
            with _JOBS_LOCK:
                job["message"] = (f"Skipping {len(candidates)} gated-login source(s) "
                                  f"(Very Very Deep runs unattended)…")
        return {}

    try:
        # ── Pass 1 — Odysseus pre-research (headless) ──
        import asyncio
        from engines.odysseus.deep_research import DeepResearcher
        with _JOBS_LOCK:
            job["group"] = "odysseus"; job["message"] = "Pass 1 · Odysseus — starting…"
        researcher = DeepResearcher(
            llm_endpoint="https://api.anthropic.com/v1/messages",
            llm_model="claude-sonnet-4-6",
            max_rounds=rounds, max_time=300, progress_callback=ody_prog)
        ody_report = (asyncio.run(researcher.research(query)) or "").strip()
        ody_stats = researcher.get_stats()

        # ── Pass 2 — Deep Research, grounded by the Odysseus findings ──
        with _JOBS_LOCK:
            job["group"] = "deep_research"; job["stage"] = "stage1"
            job["message"] = "Handing off to Deep Research…"
        ody_block = (
            "[PRE-RESEARCH FINDINGS — from a headless Odysseus/IterResearch pass on the SAME "
            "question. Treat this as a strong starting brief to verify, deepen, and extend with "
            "the browser — not as the final answer; confirm its claims against primary sources.]\n\n"
            + _truncate_words(ody_report, _DOC_WORD_CAP))
        combined_doc = (ody_block + "\n\n" + doc_context).strip() if doc_context else ody_block

        result = _dr_pipeline(query, depth, "", combined_doc, channel_overrides,
                              dr_prog, auto_skip_credentials, skip_ev)
        result.pop("docx_md", None)

        # TWO separate deliverables: (1) the combined/edited Deep Research report (already in
        # `result` — report_md + docx_b64), and (2) the Odysseus pre-research on its OWN, as a
        # standalone document (markdown + its own .docx).
        result["odysseus_stats"] = ody_stats
        if ody_report:
            result["odysseus_report_md"] = ody_report
            try:
                result["odysseus_docx_b64"] = base64.b64encode(
                    _memo_to_docx_bytes(ody_report, "Odysseus Research")).decode()
            except Exception:
                result["odysseus_docx_b64"] = ""

        with _JOBS_LOCK:
            job["result"] = result
            job["group"] = "deep_research"; job["stage"] = "report"; job["pct"] = 100
            job["message"] = "Done"; job["done"] = True
    except Exception:
        with _JOBS_LOCK:
            job["error"] = traceback.format_exc()
            job["done"] = True


@app.route("/api/very_very_deep", methods=["POST", "OPTIONS"])
def very_very_deep_start():
    if request.method == "OPTIONS":
        return "", 204
    query = (request.form.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    depth = (request.form.get("depth") or "standard").strip()
    try:
        rounds = int(request.form.get("max_rounds") or 4)
    except (ValueError, TypeError):
        rounds = 4
    rounds = min(12, max(1, rounds))

    channel_overrides = {}
    try:
        raw = json.loads(request.form.get("channels") or "{}")
        if isinstance(raw, dict):
            channel_overrides = {k: bool(raw[k]) for k in
                                 ("api_search", "web_engines", "site_queries", "neural_search") if k in raw}
    except Exception:
        channel_overrides = {}

    doc_parts = []
    for f in request.files.getlist("files"):
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "tmp"
        tmp = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
        try:
            f.save(tmp.name); tmp.close()
            txt = _extract_file_text(tmp.name, f.filename)
            if txt:
                doc_parts.append(f"[{f.filename}]\n{_truncate_words(txt, _DOC_WORD_CAP)}")
        except Exception as e:  # noqa: BLE001
            doc_parts.append(f"[{f.filename}] (could not read: {e})")
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    doc_context = "\n\n".join(doc_parts)

    job_id = os.urandom(8).hex()
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "group": "odysseus", "phase": "planning", "stage": "stage1", "pct": None,
            "message": "Starting…", "stats": {}, "done": False, "error": None, "result": None,
            "awaiting_credentials": None, "submitted_credentials": None,
        }
    _DR_EVENTS[job_id] = threading.Event()
    _DR_SKIP[job_id] = threading.Event()
    threading.Thread(target=_vvd_worker,
                     args=(job_id, query, depth, channel_overrides, rounds, doc_context),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "stages": DR_STAGES}), 202


@app.route("/api/very_very_deep/status", methods=["GET"])
def very_very_deep_status():
    job_id = request.args.get("job", "")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        payload = {
            "group": job.get("group"), "phase": job.get("phase"),
            "stage": job.get("stage"), "pct": job.get("pct"), "message": job.get("message"),
            "stats": job.get("stats", {}), "stages": DR_STAGES,
            "done": job["done"], "error": job["error"],
        }
        if job["done"] and not job["error"]:
            payload["result"] = job["result"]
        if job["done"]:
            _JOBS.pop(job_id, None)
            _DR_EVENTS.pop(job_id, None)
            _DR_SKIP.pop(job_id, None)
    return jsonify(payload)


def _port_already_serving(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    # HOST: bind 0.0.0.0 by default so it works in containers/Render; the loopback
    # duplicate-guard still catches a second local instance on the same port.
    host = os.environ.get("HOST", "0.0.0.0")
    if _port_already_serving(PORT):
        print(f"dr_server: already serving on port {PORT} — refusing to start a duplicate.")
        sys.exit(0)
    print(f"Deep Research server running on http://{host}:{PORT}")
    app.run(host=host, port=PORT, debug=False, threaded=True)
