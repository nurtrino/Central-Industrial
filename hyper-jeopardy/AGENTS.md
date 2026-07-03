<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Game data pipeline

The runtime app **does not scrape**. It loads `data/seed.json` into memory at boot (`lib/games.ts`). Seed is built offline from two TS files:

- `scripts/games-data.ts` — hand-curated main dataset (`GAMES`). Currently 230 Season 41 games.
- `scripts/scraped-extra.ts` — output target of the offline scraper (`EXTRA_GAMES`). Starts empty.

`scripts/build-seed.ts` merges both arrays by `showNumber` (extras win on conflict, so a re-scrape can replace an old entry) and writes `data/seed.json`.

## Adding more games (run locally — j-archive is usually blocked from sandboxes)

```
npm install                 # ensures cheerio is present
npm run scrape [count] [startId?]
npm run build-seed          # regenerates data/seed.json
```

`npm run scrape` (`scripts/scrape.ts`) defaults to 50 games and walks **backwards** from one below the oldest `showNumber` already known (so each run pulls in earlier seasons). `npm run scrape 230` ≈ one full season. Override the start: `npm run scrape 100 8500`. Polite 1.5s delay between requests; writes incrementally every 5 saves so Ctrl-C doesn't lose progress.

To deploy: commit `data/seed.json` and `scripts/scraped-extra.ts`.

## Image clues — not supported

J-archive doesn't actually archive the media files referenced by image clues. The scraper strips `<a>(image clue)</a>` placeholder anchors out of `.clue_text` so the question text reads cleanly, but no image rendering happens anywhere in the runtime. `ClueData.imageUrls?: string[]` lingers on the `scripts/games-data.ts` interface only because older entries in `scripts/scraped-extra.ts` still carry the field; everything downstream (build-seed, lib/games, ClueModal, /display, /dev) ignores it.
