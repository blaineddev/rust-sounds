# fixtures/

This directory holds demo data for local site development when a full Rust asset
extraction has not been run yet.

## `index.json`

A hand-authored three-entry sample that mirrors the shape of the real `index.json`
produced by `extract.py`. Load it in the browser by appending `?fixtures=1` to the
URL, e.g. `http://localhost:8000/?fixtures=1`.

## `silence.mp3` — NOT committed

All three entries in `index.json` reference `fixtures/silence.mp3` as their audio
file. This file is **not** committed to the repository because it must be generated
locally with ffmpeg.

After installing ffmpeg, run the following command from the project root to create it:

```bash
ffmpeg -f lavfi -i anullsrc=r=22050:cl=mono -t 0.5 -codec:a libmp3lame -b:a 128k fixtures/silence.mp3
```

This produces a 0.5-second silent MP3 at 128 kbps — enough for the play button to
function without errors during development.

**Without this file**, the play buttons in `?fixtures=1` mode will display an error
state when clicked (the browser cannot load the audio source). Search and the copy-
prefab-path button still work normally — they do not depend on the audio file.
