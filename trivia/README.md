# WedgeQuest

A 2–6 player, phones-as-controllers implementation of the classic
wheel-of-wedges trivia game (no Hasbro branding, no Hasbro card text).
Questions come live from the [Open Trivia Database](https://opentdb.com)
(CC BY-SA 4.0 — attribution is shown in the UI).

## How a game works

1. One player opens the site, enters a name, hits **CREATE GAME** → gets a
   4-letter room code (the creator is the host).
2. Friends open the same URL on their own devices and join with the code
   (or via the shareable `…/#CODE` link).
3. Host hits **START**. Everyone sees the full wheel board; play passes
   around the table.
4. On your turn: tap **ROLL** (server-side CSPRNG; a 3D die tumbles on every
   screen), then tap one of the highlighted legal spaces — exact die count,
   no doubling back, the center hub is a legal cut-through.
5. Classic rules: answer correctly → roll again. Correct on a category HQ →
   earn that wedge. Hub before 6 wedges → wild card (pick any category).
   With all 6 wedges, land on the hub by exact count → your **opponents vote
   the final category** — answer it to win.

Multiple games run side by side; reconnects (page refresh, phone lock) are
seamless — identity is a token in localStorage. The host can skip a stuck
player's turn and start rematches.

## Architecture

| Piece | File | Role |
|---|---|---|
| Game engine | `game.py` | Pure rules state machine — board graph, movement, wedges, phases. No IO; fully unit-tested. |
| Questions | `questions.py` | OTDB client: per-category prefetch queues, ~1 req/5s rate limiting, base64 decoding, dedupe, disk cache, offline fallback set. |
| Server | `server.py` | FastAPI: rooms, WebSocket protocol, dice RNG, phase timers, broadcast. |
| Client | `static/` | Vanilla JS SPA: SVG board renderer, CSS-3D dice, question cards, lobby. |

The six wedge categories map to pools of OTDB categories:
Geography · Entertainment (Film/Music/TV) · History ·
Arts & Literature (Art/Books/Musicals) · Science & Nature (Science/Animals) ·
Sports & Leisure (Sports/Board Games).

Rooms are in-process memory: run **one instance** (fine on Render `starter`).

## Run locally

```bash
cd trivia
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server:app --port 5060      # → http://127.0.0.1:5060
```

Open two browser tabs to simulate two players.

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `REVEAL_SECS` | `5` | How long the answer reveal stays up. |
| `VOTE_SECS` | `30` | Final-category vote timeout (then majority-of-cast wins). |
| `QCACHE_PATH` | `./qcache.json` | Question cache file. |
| `OTDB_OFFLINE` | unset | `1` = never call OTDB (tests/dev; uses built-in fallback questions). |

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

## Legal notes

Game *mechanics* aren't copyrightable and facts aren't copyrightable, but
Hasbro's trademarks (the name, the wedge/board trade dress as branded) and
their exact card text are theirs — which is why this uses its own name, its
own board art, and openly-licensed questions with attribution.
