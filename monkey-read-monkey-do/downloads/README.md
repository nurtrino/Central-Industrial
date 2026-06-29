# downloads/

The hosted Read Monkey Do page serves the local transcription helper from here at
`/download/ReadMonkeyDoWorker-Full.exe` and `/download/ReadMonkeyDoWorker-Lite.exe`.

Build both from `../../mrmd-worker/` (`build_exe.bat` = Full with diarization;
`build_exe_lite.bat` = Lite, Whisper-only) and drop the two exes here so the page's
"Full" / "Lite" download links work.

> Note: a multi-hundred-MB exe is large to commit to git. For production, prefer
> hosting the exe on a GitHub Release and pointing the page's download link there
> instead of committing the binary. This folder + route is the simplest wiring.
