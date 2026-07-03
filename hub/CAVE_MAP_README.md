# Ohio Caves Map — canonical source (2026-07-02)

**Live:** https://centralindustrial.ai/cave (served by `hub_server.py`'s `/cave` route → `cave_map.html`)

## Files
- `cave_map.html` — the entire app. Self-contained: all cave data (ocsCaves,
  expansionCaves, top10 arrays) is hardcoded in a `<script>` block. No local
  file dependencies — every external call is a public API (Leaflet/Esri-Leaflet
  via unpkg CDN; Esri/USGS basemap tiles; ODNR live karst MapServer; USDA/ODNR
  public-land boundary layers; a GitHub-hosted US-states GeoJSON). Verified
  byte-identical to the working local copy and JS-syntax-clean on every push.
- `cave_map_data.csv` — human-readable mirror of the same cave data (name, lat,
  lng, county, bedrock, precision, notes) for reference/download. NOT loaded by
  the page at runtime — kept in sync by hand when caves are added/edited.

## Workflow (as of 2026-07-02)
This repo copy is now the **canonical, single source of truth**. All future
edits (new caves, corrected coordinates, notes, layer changes) get made here
and pushed to `main` — Render auto-deploys. The old local working copy at
`Desktop\caves\Ohio\OH_Caves_map.html` is deprecated once this note is added;
do not edit it going forward.

Research backing (Primary Sources corpus, LiDAR PNGs, source PDFs) that
*informs* the coordinates baked into `cave_map.html` stays in the local
`Desktop\caves\Primary Sources\` library — that's the research archive, not a
runtime dependency of the page, and is a separate (much larger) asset not
migrated here.
