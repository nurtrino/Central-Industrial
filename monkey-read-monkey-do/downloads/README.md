# downloads/

The hosted Read Monkey Do page serves the local transcription helper from here at
`/download/ReadMonkeyDoWorker.exe`.

Drop the built `ReadMonkeyDoWorker.exe` (from `../../mrmd-worker/`, see its
`build_exe.bat`) into this folder so the page's "Download it" link works.

> Note: a multi-hundred-MB exe is large to commit to git. For production, prefer
> hosting the exe on a GitHub Release and pointing the page's download link there
> instead of committing the binary. This folder + route is the simplest wiring.
