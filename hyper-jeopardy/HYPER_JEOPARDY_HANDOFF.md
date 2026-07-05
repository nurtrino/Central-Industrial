# Hyper Jeopardy — Handoff

_Last updated: 2026-07-05_

## What this is

**Hyper Jeopardy** is a multiplayer party game: the real Jeopardy game engine
(ported from the Bryndal jeopardy app) wrapped in an outer-space visual identity,
plus **Hyper Mode** — random board cells that trigger a party **mini-game** instead
of a clue, and a surprise **Space Invaders Ambush** mid-round.

Phones are the controllers; one shared screen (`/display`) is the stage. It ships as
its own app inside the **Central Industrial** suite, behind the shared SSO access gate
at `jeopardy.centralindustrial.ai`.

- **Folder:** `hyper-jeopardy/`
- **Branch this work lives on:** `claude/hyper-jeopardy-handoff-rbsnyo` (merged to `main` as **PR #3**).
- **Live URL:** `https://jeopardy.centralindustrial.ai` (Render, behind the gate).

## Stack

Next.js 16 (App Router, Turbopack) + React 19 + a **custom Node server** (`server.ts`)
running **socket.io** for realtime multiplayer. Tailwind v4. TypeScript throughout,
run via `tsx` (no separate compile step — `dev` and `start` both `tsx server.ts`).

Game data is `data/seed.json` (275 real Jeopardy games) loaded into memory at boot by
`lib/games.ts`. The runtime **never scrapes** — seed is built offline (see `AGENTS.md`).

## Routes

- `/`         — phone controller (join, buzz, answer, wager, play a hyper round / ambush)
- `/display`  — the shared "TV" screen (board, clues, hyper takeover, invaders, scoreboard)
- `/dev`      — local multi-player simulator (drive several phones from one screen)
- `/api/health` — open 200 endpoint (exempt from the gate; Render health check)
- `/api/socket` — the socket.io endpoint (also open so gating never breaks a live game)

## Key files

| File | Role |
|---|---|
| `server.ts` | Custom Node entry — Next request handler + socket.io, on Render's `$PORT`. Runs as PID 1 (exec form) for clean SIGTERM. |
| `proxy.ts` | The **access gate**. Trusts the hub's SSO handoff (`?t=` → host-only `ci_sess` cookie) via `AUTH_SECRET`; bounces cookieless visits to the hub. Leaves `/api/health` + `/api/socket` open. |
| `lib/gameEngine.ts` | **Pure game state machine** — the heart. Rounds, buzzing, Daily Double, Final, and the hyper flow. No I/O. |
| `lib/gameServer.ts` | Socket wiring — maps socket events ⇄ engine calls, broadcasts state to `/display` + phones. |
| `lib/gameStore.ts` + `lib/dataDir.ts` | Persistence: in-progress game snapshot + leaderboard, written under `DATA_DIR` (`/var/data` on Render). Survives restarts/redeploys/cold starts. |
| `lib/miniGames.ts` | The four hyper mini-games' logic (intro → playing → results state, scoring, timers). |
| `lib/invaders.ts` | **Space Invaders Ambush** server-side battle engine (invader grid, ships, tick loop, snapshots). |
| `lib/opentdb.ts` | OpenTDB trivia source (for Rapid Fire and any trivia-backed mini-game). |
| `lib/accounts.ts` + `lib/profile.ts` | Accounts + persistent leaderboard. |
| `lib/games.ts` | Loads `data/seed.json` into memory; shapes games for play. |
| `lib/audio.ts` + `lib/wordBanks.ts` + `lib/clueSentinel.ts` | Room-wide audio manifest/cues; word banks for Anagram/Letter Reveal; clue text guards. |
| `components/` | `Board`, `ClueModal`, `HyperModal`, `HyperFlair`, `MiniGameStage`/`MiniGameController`, `InvadersStage`/`InvadersController`, `Scoreboard`, `Leaderboard`, `Lobby`, `Rejoin`, `FinalJeopardy`, `Starfield`. |
| `data/seed.json` | 275 games, built offline. **Committed** (5.5 MB). |
| `scripts/` | `scrape.ts`, `build-seed.ts`, `deploy-render.sh`, `e2e-drive.mjs`, plus games-data + scraped-extra sources. |

## Game flow (the state machine)

`GamePhase`: `lobby → jeopardy → double_jeopardy → final_jeopardy → game_over`.

Within a clue, `CluePhase` walks:
`idle → reading (5s) → buzzing → answering (5s) → judging → reveal (3s)`, with
Daily-Double (`daily_double_wager → daily_double_answer`) and the two hyper/invaders
takeovers branching off it:

- **`hyper_intro`** — a full-screen **HYPER MODE · ACTIVATED** splash naming the
  mini-game + who triggered it, then a ~5s rules screen (`INTRO_MS`).
- **`hyper_active`** — the mini-game runs (see below).
- **`invaders`** — the Space Invaders Ambush takeover (fires mid-Double-Jeopardy).

At the start of each round, `assignHyperClues()` marks random non-Daily-Double cells
as "hyper" and pre-assigns a mini-game to each (`pickMiniGame`), color-coding the
board. Hyper cells are hidden like Daily Doubles so the surprise lands.

## Hyper Mode — the four mini-games (all real, all playable)

Registered in `MINI_GAMES` (`lib/gameEngine.ts`); logic in `lib/miniGames.ts`,
UI in `MiniGameStage` (display) + `MiniGameController` (phones):

1. **Anagram Race** — unscramble the word before rivals; faster solve banks more. Value-based scoring.
2. **Rapid Fire** — 10 OpenTDB questions, one category, a hard **30s** sprint (`RAPID_ROUND_MS`); most correct wins. No give-up.
3. **Letter Reveal** — five hidden letters reveal one by one (`REVEAL_INTERVAL_MS`); guess early for a bigger score.
4. **Memory Matrix** — humanbenchmark-style: a pattern flashes on a grid, rebuild it from memory. **3 wrong guesses at any time → out** (`MEMORY_STRIKES`), leveled by `MEMORY_LEVELS`.

Shared rules: **60s round cap** (`HYPER_ROUND_MS`), a 5s rules/intro screen, and a 5s
results/standings screen (`RESULTS_MS`) before the board returns. There is **no
"Give Up"** — rounds are purely time-limited. A safety timer guarantees the round
can never hang. Scoring is unified across the word games (Anagram-style placement).

## Space Invaders Ambush

`lib/invaders.ts` + `InvadersStage`/`InvadersController`. At a random point in **Double
Jeopardy**, the board is ambushed: every player gets a ship (`SHIP_COLORS`, 2 lives)
and the table fights an invader grid co-op. Flow: `intro (3s "AMBUSH" splash) →
playing → won|lost`. Server ticks the battle and broadcasts snapshots; phones steer +
fire. The host has an **ambush-test button** to trigger it on demand.

## Look & sound

Space reskin repaints the original app's semantic classes (`jeo-tile`, `jeo-title`,
`jeo-value`, …) in `app/globals.css` — so game logic was untouched. Global
`<Starfield/>` canvas behind every route (twinkling stars, shooting asteroids,
drifting nebulas); board runs a neon value spectrum (cyan $200 → magenta $1000) with
violet category headers. `<HyperFlair/>` adds animated nebulas / shooting stars /
rockets during Hyper Mode. Fonts are self-hosted Orbitron + Exo 2 via
`next/font/local` (no Google Fonts dependency). **Room-wide synced audio** (`lib/audio.ts`):
a "Welcome to Hyper Jeopardy" voice cue on open, a `laser-charge` start cue in place of
the jingle, and synced hyper-start clips on every screen.

## Data pipeline

See `AGENTS.md`. The runtime never scrapes; `data/seed.json` is built offline from
`scripts/games-data.ts` (hand-curated `GAMES`, ~230 Season 41) merged with
`scripts/scraped-extra.ts` (`EXTRA_GAMES`) by `scripts/build-seed.ts`.
`npm run scrape [count] [startId?]` walks j-archive backwards (usually blocked from
sandboxes — run locally). To deploy new games: commit `data/seed.json` +
`scripts/scraped-extra.ts`. **Image clues are not supported** (j-archive doesn't host
the media); the scraper strips the placeholder anchors.

## Deploy / hosting (Render)

Defined in the repo-root `render.yaml` as the `hyper-jeopardy` web service:

- **Runtime:** Docker (`hyper-jeopardy/Dockerfile`), custom Node server on `$PORT`.
- **Plan:** `starter` (Node + Next SSR + 5.7 MB seed in memory). Bump to `standard` if it OOMs under real load.
- **Health check:** `/api/health` (open; a bare `/` would be 302'd by the gate).
- **Domain:** `jeopardy.centralindustrial.ai`. `autoDeploy: true`.
- **Persistent disk:** `hyper-jeopardy-data`, 1 GB, mounted at `/var/data` — **NOT** over the image's `data/` dir (so it never shadows `data/seed.json`). `DATA_DIR=/var/data` → durable accounts/leaderboard + in-progress game snapshot.
- **Env:** `fromGroup: central-industrial-auth` (shared `AUTH_SECRET` → turns the gate on), `NODE_ENV=production`, `HOME_URL=https://centralindustrial.ai` (where the gate bounces unauthenticated visitors), `DATA_DIR=/var/data`.
- **Gate:** `proxy.ts` trusts the hub's SSO cookie via `AUTH_SECRET`; the socket + health stay open.
- Manual deploy helper: `scripts/deploy-render.sh` (run outside the sandbox).

## Run locally

```
cd hyper-jeopardy
npm install
npm run build && npm run start   # production (custom socket server, tsx)
# or: npm run dev                 # development
# open http://localhost:3000/display on the TV, http://localhost:3000/ on phones
```

`scripts/e2e-drive.mjs` is a socket-level driver to smoke-test the multiplayer flow
headlessly: `node scripts/e2e-drive.mjs board|hyper`. `/dev` is the in-browser
multi-player simulator.

## State at handoff — where we left off

- **All four mini-games are built and playable** (Anagram Race, Rapid Fire, Letter Reveal, Memory Matrix) — they are **no longer placeholders**. The 60s cap + intro/results flow around them works.
- **Space Invaders Ambush** is in and wired (mid-Double-Jeopardy, host test button).
- **Room-wide synced sound** across all screens; hyper flair layer (nebulas / shooting stars / rockets) is live.
- **Persistence** works: in-progress games + leaderboard survive restarts (Render persistent disk at `/var/data`).
- Deploy is wired end-to-end on Render behind the Central Industrial gate.
- Last substantive commits: `e837808` (board declutter, room-wide sound, host ambush-test button) → merged as **PR #3** (`e520867`).

### Loose ends / possible next steps

- This branch (`claude/hyper-jeopardy-handoff-rbsnyo`) later pivoted to an unrelated
  cave-mapping project; those commits at the branch tip are **not** Hyper Jeopardy.
  Start fresh Hyper Jeopardy work from `main` (which has PR #3 merged).
- Mini-game roster is currently four. Any additional games / social modes discussed
  (e.g. Fake It, The Spectrum, Connections, Zoom Out, Most Likely To…, Higher or Lower)
  from earlier planning are **not** built.
- Expand `data/seed.json` beyond 275 games via the offline scrape → build-seed flow.

## Related

- `README.md` — player-facing overview (now updated to match this real state).
- `AGENTS.md` / `CLAUDE.md` — Next.js-16 warning + the data pipeline rules.
- Sibling handoffs: `hub/SPECIAL_PROJECTS_HANDOFF.md`, `deep-research/DEEP_RESEARCH_HANDOFF.md`.
