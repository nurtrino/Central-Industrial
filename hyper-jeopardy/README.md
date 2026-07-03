# Hyper Jeopardy

A multiplayer trivia potpourri for 2–6 players — phones as controllers, one shared
screen as the stage. Jeopardy-style board play plus a rotating cast of mini-games
(bluffing, estimation, word puzzles, speed buzzing, wagering, survival rounds).

## Status: Phase 1 — aesthetic shell

`index.html` is a fully self-contained page (fonts inlined, zero external requests)
that establishes the visual identity:

- **Starfield engine** (canvas): twinkling stars with intermittent glint flares,
  shooting stars and tumbling asteroids every 6–14 s, translucent nebulas that
  drift on slow lissajous paths and periodically float in and out of view.
- **The board**: 6 × 5 glass grid over the void; dollar values sweep a neon
  spectrum from cyan ($200) down to magenta ($1000); a neon pulse orbits the
  board frame; a diagonal shimmer sweeps the grid every ~9.5 s; random cells
  ping with a soft neon flash.
- **Clue reveal**: FLIP zoom from the clicked cell into a full-screen glass card
  with an orbiting neon border; response reveal in lime.
- **Podium rail**: per-player neon hue, active-player glow, negative scores in red.

Demo categories/clues and players are placeholders; real game logic (ported from
the existing jeopardy app, then extended with mini-games) arrives in Phase 2.

QA helpers: open with `#clue` in the URL hash to auto-open a clue,
`#clue-answer` to also reveal the response. Honors `prefers-reduced-motion`.

## Planned phases

1. ~~Space-theme aesthetic shell~~ (this)
2. Port real Jeopardy game logic + multiplayer (phone controllers / shared screen)
3. Mini-game potpourri framework (pluggable rounds)
4. Deploy as its own app under centralindustrial.ai, listed on the hub splash screen
