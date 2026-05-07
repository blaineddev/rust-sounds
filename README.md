# rust-sounds

A searchable, previewable catalog of every FX prefab that Rust's `Effect.server.Run("…")`
can spawn — built directly from the game's bundled assets.

Live site: https://blaineddev.github.io/rust-sounds/

## What this is

For Rust modders / server admins. Browse every effect prefab in the game, preview the
sound(s) it plays in your browser, copy the prefab path, paste into your plugin or
console.

Each entry is **reference-resolved**: the extractor walks the actual SoundPlayer →
SoundDefinition → AudioClip pointer chain inside Unity's bundles. There is no
basename-matching or guesswork. Multi-variant prefabs (random / distance-bucketed)
expose every variant.

## Using the site

Search for the prefab you want (`ak47`, `door`, `explosion`, …), filter by category
pill, hit ▶ to preview, 📋 to copy the path. Adjust playback volume in the header.
For prefabs with multiple variants, the count badge (`×N`) on the play button shows
how many; clicking again cycles through them.

## Re-running the extractor (when Rust patches)

You need Python 3.11+, ffmpeg on `PATH`, the Rust client installed via Steam, and ~10 GB
of free RAM during extraction.

```bash
git clone https://github.com/blaineddev/rust-sounds.git
cd rust-sounds
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python extract.py                  # auto-detects Steam install
# or
python extract.py --rust-dir "D:\SteamLibrary\steamapps\common\Rust"
```

Writes `audio/*.mp3` and `data/sounds.json` in place. Push and GitHub Pages redeploys.

### CLI options

```
--rust-dir PATH        Rust install root (auto-detected if omitted)
--out PATH             Output directory (default: current dir)
--ffmpeg PATH          ffmpeg binary (default: 'ffmpeg' on PATH)
--rss-abort-mb N       Abort if process RSS exceeds this many MB (default: 9216)
```

## Local site dev (without running the extractor)

```bash
python -m http.server 8000
```

Open `http://localhost:8000` for live data, or `?fixtures=1` for demo entries from
`fixtures/sounds.json`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## How it works

End-to-end pipeline (`src/reference_extract.py`), three Unity bundle passes:

1. **`assetscenes.bundle`** — every FX prefab GameObject lives here as scene content.
   Walked by m_Name to find every prefab matching `/fx/` or `/effects/`. Components are
   read via embedded TypeTree; SoundPlayer.soundDefinition and AudioSource.m_audioClip
   PPtrs collected, plus any GameObjectRef-shaped fields for compound classification.
2. **`content.bundle`** — holds SoundDefinitions and the GameManifest ScriptableObject.
   Each SoundDefinition's weightedAudioClips and distanceAudioClips arrays are walked
   to AudioClip PPtrs. GameManifest's `prefabProperties` and `pooledStrings` give us
   the canonical FX-prefab universe and a guid → path table for resolving GORs.
3. **`audio.bundle`** — every AudioClip's `samples` is decoded to WAV and piped
   through ffmpeg to mono 128 kbps MP3. One MP3 per unique referenced clip,
   filenames derived from `sha1(container_path)[:12]`.

Compound prefabs (those whose GORs point at other FX prefabs) are excluded from the
output entirely; the underlying child prefab is normally itself in the catalog.

Static site (`index.html` + `site.js` + `site.css`) reads `data/sounds.json` over
relative URLs and renders a CSS-grid tile board with search, category pills, and
volume control.

See `docs/superpowers/specs/2026-05-05-rust-sounds-design.md` for the original v1
design (basename-matching) and the conversation history for how Option A
(reference-based extraction via scene-bundle walk) replaced it.
