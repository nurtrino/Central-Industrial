# Audio assets

## `laser-charge.mp3` (expected — not yet committed)

The game-start sound effect. On launch (the "Press to Launch" screen), the app
plays **`assets/laser-charge.mp3`** in place of the traditional Jeopardy opening
jingle. Source clip: *gregorquendel-laser-charge* (the file Brad supplied locally).

**This binary isn't in the repo yet** — it lives on Brad's local machine at a
Windows path the cloud build environment can't reach. Drop the mp3 in here (keep
the name `laser-charge.mp3`) and it plays automatically; no code change needed.

Until then, `index.html` falls back to a Web-Audio–synthesized laser charge so the
launch moment still has sound (used by the standalone artifact preview).

Keep clips short (< 3 s) and reasonably small; they're fetched same-origin at load.
