import json
import os
import shutil
from pathlib import Path

import pytest

from rust_sounds.cli import main

RUST_DIR = os.environ.get("RUST_DIR")
ffmpeg_missing = shutil.which("ffmpeg") is None


@pytest.mark.skipif(not RUST_DIR, reason="Set RUST_DIR=/path/to/Rust to run this smoke test")
@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not on PATH")
def test_extracts_at_least_one_prefab(tmp_path: Path):
    rc = main([
        "--rust-dir", RUST_DIR,
        "--out", str(tmp_path),
        "--limit", "5",
    ])
    assert rc == 0

    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index) >= 1, "expected at least one extracted prefab"

    first = index[0]
    assert first["prefab"].startswith("assets/")
    assert first["duration_ms"] > 0

    mp3 = tmp_path / first["file"]
    assert mp3.exists()
    assert mp3.stat().st_size > 0
