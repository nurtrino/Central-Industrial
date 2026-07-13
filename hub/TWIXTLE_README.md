# Twixtle — canonical source (2026-07-13)

**Live:** https://centralindustrial.ai/twixtle (served by `hub_server.py`'s `/twixtle` route → `twixtle.html`)

A self-hosted clone of the daily word puzzle **Twixtle** (twixtle.games). Transform a
start word into an end word in exactly **four moves**, one of each type — **anagram,
verb, homophone, compound** — each used once, in any order.

## Files
- `twixtle.html` — the entire app. **Self-contained** (mirrors `cave_map.html`): the
  validator substrate (`window.TWIXT_DATA`) and the puzzle database (`window.PUZZLES`)
  are inlined in `<script>` blocks, so there are **no local file dependencies** and
  nothing external is fetched at runtime. ~1.2 MB.

## How it plays
Free-form 5-box board: start (locked) · three fillable interior boxes · end (locked).
Click a box, type a word, Enter. Each adjacent pair ("seam") must connect by a valid
move type, and the four seams must use four **distinct** types (bipartite match). For a
compound you may type the whole compound word (e.g. `sundial` next to `sun`) and it
extracts the partner. **Difficulty** (All/Easy/Med/Hard) and **Source** (Official /
Generated) selectors are in the header.

## Puzzle data
Currently **57 official puzzles** (`source:"official"`), graded by difficulty
(37 medium, 20 hard). The Official set = the public start/end prompts from twixtle.games
with solutions reconstructed by our own solver (validated against 8 published solutions;
each real puzzle is uniquely solvable). The **Generated** tab is wired but empty until the
generator is built.

## Workflow / regenerating
The build lives in the local project `D:\_______Claude\twixtle\` (solver, difficulty
grader, and the split source `index.html` + `twixtle-data.js` + `puzzles.js`). To update
the deployed page, rebuild the self-contained file (inline the two JS bundles into
`index.html`) and copy it here as `twixtle.html`, then push `main` — Render auto-deploys.
See `D:\_______Claude\twixtle\TWIXTLE_HANDOFF.md`.

## Route
`hub_server.py` maps `/twixtle` and `/twixtle/` → `twixtle.html` (added alongside the
`/cave` route). Served openly like Cave Map — only `/api/status` is behind the access gate.
