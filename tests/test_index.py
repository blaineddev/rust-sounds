import json
from pathlib import Path

from rust_sounds.index import IndexEntry, build_entry, write_index_atomic


class TestBuildEntry:
    def test_populates_all_fields(self):
        entry = build_entry(
            prefab_path="assets/prefabs/weapons/ak47/effects/fire.prefab",
            duration_ms=230,
            extra_clips=["fire_tail"],
        )
        assert entry.prefab == "assets/prefabs/weapons/ak47/effects/fire.prefab"
        assert entry.file == "audio/3f9a1c8b7e02.mp3" or entry.file.startswith("audio/")
        assert entry.file.endswith(".mp3")
        assert len(entry.file) == len("audio/") + 12 + len(".mp3")
        assert entry.name == "fire"
        assert entry.category == "weapons/ak47/effects"
        assert entry.duration_ms == 230
        assert entry.extra_clips == ["fire_tail"]

    def test_extra_clips_defaults_to_empty(self):
        entry = build_entry(
            prefab_path="assets/prefabs/foo/bar.prefab",
            duration_ms=100,
        )
        assert entry.extra_clips == []


class TestWriteIndexAtomic:
    def test_writes_valid_json_array(self, tmp_path: Path):
        entries = [
            build_entry("assets/prefabs/a.prefab", duration_ms=100),
            build_entry("assets/prefabs/b.prefab", duration_ms=200),
        ]
        target = tmp_path / "index.json"
        write_index_atomic(entries, target)

        loaded = json.loads(target.read_text())
        assert isinstance(loaded, list)
        assert len(loaded) == 2
        assert loaded[0]["prefab"] == "assets/prefabs/a.prefab"
        assert loaded[1]["prefab"] == "assets/prefabs/b.prefab"

    def test_atomic_replace_does_not_leave_temp_file(self, tmp_path: Path):
        target = tmp_path / "index.json"
        target.write_text("[]")
        write_index_atomic([], target)

        # No leftover *.tmp files
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_overwrites_existing_file(self, tmp_path: Path):
        target = tmp_path / "index.json"
        target.write_text('[{"old": true}]')

        new_entries = [build_entry("assets/prefabs/x.prefab", duration_ms=50)]
        write_index_atomic(new_entries, target)

        loaded = json.loads(target.read_text())
        assert len(loaded) == 1
        assert loaded[0]["prefab"] == "assets/prefabs/x.prefab"
