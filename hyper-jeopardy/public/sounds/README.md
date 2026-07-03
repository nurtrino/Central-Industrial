# Sound assets

`think.mp3` is the Final Jeopardy "Think!" theme, sourced from the MIT-licensed
[`jeopardy`](https://www.npmjs.com/package/jeopardy) npm package by Ben Drucker.
The full text of the MIT license under which this asset is redistributed is
included in the project root.

If the file is missing for any reason, `lib/audio.ts` falls back to a
synthesized 30-second loop in the same key. Drop a different recording in here
and it'll be used automatically (the client probes via HEAD on first use).

The buzz / correct / wrong / time-up / Daily Double stings are synthesized in
`lib/audio.ts` (Web Audio API) — no files involved.
