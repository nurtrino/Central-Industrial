# Hyper Jeopardy

A multiplayer **Hyper Jeopardy** — the real Jeopardy game engine (ported from the
Bryndal jeopardy app) wrapped in an outer-space visual identity, plus **Hyper Mode**:
random cells that trigger a party mini-game instead of a clue.

Phones are the controllers; one shared screen (`/display`) is the stage.

## Stack

Next.js 16 (App Router, Turbopack) + React 19 + a custom Node server (`server.ts`)
running **socket.io** for realtime multiplayer. Game data is `data/seed.json`
(275 real games) loaded into memory at boot by `lib/games.ts`. Tailwind v4.

- `/`         — phone controller (join, buzz, answer, wager, run a hyper round)
- `/display`  — shared "TV" screen (board, clues, hyper takeover, scoreboard)
- `/dev`      — local multi-player simulator
- `server.ts` — Next handler + socket.io (`lib/gameServer.ts`)
- `lib/gameEngine.ts` — pure game state machine (rounds, buzzing, DD, final, **hyper**)

## The space theme

Everything re-themes through the original app's semantic classes (`jeo-tile`,
`jeo-title`, `jeo-value`, …) — repainted in `app/globals.css` — so the game logic
was untouched. A global `<Starfield/>` canvas (twinkling stars, shooting
asteroids, drifting nebulas) sits behind every route; the board runs a neon value
spectrum (cyan $200 → magenta $1000) with violet category headers. Fonts are
self-hosted Orbitron (display) + Exo 2 (body) via `next/font/local` so the build
never depends on Google Fonts. The game-start cue is `public/sounds/laser-charge.mp3`
in place of the Jeopardy jingle.

## Hyper Mode

At the start of each round, `assignHyperClues()` marks **5–10 random non-Daily-Double
cells** as "hyper". Selecting one — like a Daily Double, but a mini-game — fires:

1. **`hyper_intro`** — a full-screen **HYPER MODE · ACTIVATED** splash naming the
   mini-game and who triggered it (~3.5s).
2. **`hyper_active`** — the mini-game runs. Right now every mini-game is a
   **placeholder card** (title + blurb, drawn from `MINI_GAMES` in `lib/gameEngine.ts`
   — Fake It, The Spectrum, Connections, Zoom Out, Most Likely To…, Higher or Lower,
   Rapid Fire). The board controller (or host) taps **End Hyper Round** to return to
   the board; a safety timer caps it so it can never hang.

Real, playable mini-games replace the placeholders in the next phase — the
activation + close flow around them already works. Hyper cells are hidden on the
board (no marker) so the surprise lands, exactly like a Daily Double.

## Run locally

```
npm install
npm run build && npm run start   # production (custom socket server)
# or: npm run dev                 # development
# open http://localhost:3000/display on the TV, http://localhost:3000/ on phones
```

`scripts/e2e-drive.mjs` is a socket-level driver used to smoke-test the multiplayer
flow headlessly (`node scripts/e2e-drive.mjs board|hyper`).

## Data pipeline

See `AGENTS.md` — the runtime never scrapes; `data/seed.json` is built offline from
`scripts/games-data.ts` (+ optional scraped extras) via `npm run build-seed`.
