# rust-sounds

MP3s and a searchable web UI for every invokable effect prefab in Rust.

Live site: https://blaineddev.github.io/rust-sounds/

## What this is

For Rust modders/server admins. Browse every prefab the `effect` console command (or
`Effect.server.Run("...")`) can invoke, preview the sound in your browser, and copy the
prefab path with one click. Re-runnable on every Rust patch.

## Using the site

Open the live site, type a search term (`ak47`, `door`, `explosion`, etc.), click ▶ to
preview, click 📋 to copy the prefab path, paste into your plugin / console.

## Re-running the extractor (when Rust patches)

You need: Python 3.11+, ffmpeg on `PATH`, the Rust game installed via Steam.

```bash
git clone https://github.com/blaineddev/rust-sounds.git
cd rust-sounds
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python extract.py                  # auto-detects Steam install
# or
python extract.py --rust-dir "/mnt/c/Program Files (x86)/Steam/steamapps/common/Rust"
```

The script writes `audio/*.mp3` and `index.json` in place. Push the result and GitHub
Pages redeploys automatically.

### CLI options

```
--rust-dir PATH    Rust install root (auto-detected if omitted)
--out PATH         Output directory (default: current dir)
--ffmpeg PATH      ffmpeg binary (default: 'ffmpeg' on PATH)
--limit N          Stop after N prefabs (debug)
--strict           Abort on any extraction error
```

## Local site dev (without running the extractor)

```bash
python -m http.server 8000
```
Open `http://localhost:8000?fixtures=1` to load demo entries from `fixtures/`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
The smoke test in `tests/test_smoke.py` is skipped unless `RUST_DIR` is set.

## How it works

`extract.py` walks `<rust-dir>/Bundles/**/*.bundle` with [UnityPy](https://github.com/K0lb3/UnityPy),
finds every prefab whose container path is under `assets/` and which carries an `AudioClip`,
decodes the clip to WAV, pipes it through ffmpeg to 128kbps mono MP3, and writes a
deterministic `audio/<sha1(prefab)[:12]>.mp3` plus a row in `index.json`. The static site
in `index.html`/`site.js` consumes `index.json` over `fetch()` and renders a CSS-grid
tile board with search and click-to-copy.

See `docs/superpowers/specs/2026-05-05-rust-sounds-design.md` for the full design.
