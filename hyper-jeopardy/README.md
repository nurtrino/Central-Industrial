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
   mini-game and who triggered it, then a ~5s rules screen.
2. **`hyper_active`** — the mini-game runs. There are **four real, playable
   mini-games** (`MINI_GAMES` in `lib/gameEngine.ts`, logic in `lib/miniGames.ts`,
   UI in `MiniGameStage`/`MiniGameController`):
   - **Anagram Race** — unscramble the word before rivals; faster solve banks more.
   - **Rapid Fire** — 10 OpenTDB questions, one category, a hard 30s sprint; most correct wins.
   - **Letter Reveal** — five hidden letters reveal one by one; guess early for a bigger score.
   - **Memory Matrix** — a pattern flashes on a grid, rebuild it from memory; 3 wrong guesses → out.

   Shared rules: a **60s round cap**, no "Give Up" (rounds are purely time-limited),
   and a 5s standings screen before the board returns. A safety timer guarantees a
   round can never hang. Hyper cells are hidden on the board (no marker) so the
   surprise lands, exactly like a Daily Double.

Beyond the hyper cells, **Space Invaders Ambush** fires at a random point mid-Double-
Jeopardy: the board is ambushed and the whole table fights an invader grid co-op
(`lib/invaders.ts`, `InvadersStage`/`InvadersController`). The host has a test button
to trigger it on demand.

See `HYPER_JEOPARDY_HANDOFF.md` for the full architecture + current state.

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
