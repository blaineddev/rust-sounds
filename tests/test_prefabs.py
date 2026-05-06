import json
from pathlib import Path

from rust_sounds.prefabs import extract_prefab_paths, fetch_prefabs


class TestFetchPrefabs:
    def test_reads_cache_when_present(self, tmp_path):
        cache = tmp_path / "p.json"
        cache.write_text('[{"Path": "assets/foo.prefab"}]', encoding="utf-8")
        # Bad URL — proves we never hit the network when cache is hot.
        assert fetch_prefabs(url="http://invalid.test/", cache_path=cache) == [
            {"Path": "assets/foo.prefab"},
        ]

    def test_ignores_cache_when_missing(self, tmp_path, monkeypatch):
        captured_url: dict[str, str] = {}

        class _Resp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
            def read(self_inner):
                return b'[{"Path": "assets/from-net.prefab"}]'

        def fake_urlopen(url, timeout):
            captured_url["url"] = url
            return _Resp()

        monkeypatch.setattr("rust_sounds.prefabs.urllib.request.urlopen", fake_urlopen)
        result = fetch_prefabs(url="http://example.test/p.json", cache_path=tmp_path / "missing.json")
        assert captured_url["url"] == "http://example.test/p.json"
        assert result == [{"Path": "assets/from-net.prefab"}]


class TestExtractPrefabPaths:
    def test_lowercases_and_sorts_uniquely(self):
        data = [
            {"Path": "Assets/Effects/B.prefab"},
            {"Path": "assets/effects/a.prefab"},
            {"Path": "Assets/Effects/A.prefab"},  # duplicate of #2 once lowercased
            {"Path": "assets/effects/c.prefab"},
        ]
        assert extract_prefab_paths(data) == [
            "assets/effects/a.prefab",
            "assets/effects/b.prefab",
            "assets/effects/c.prefab",
        ]

    def test_drops_non_prefab_extensions(self):
        data = [
            {"Path": "assets/foo.fbx"},
            {"Path": "assets/foo.prefab"},
            {"Path": "assets/foo.mat"},
        ]
        assert extract_prefab_paths(data) == ["assets/foo.prefab"]

    def test_drops_paths_outside_assets_root(self):
        data = [
            {"Path": "outside/foo.prefab"},
            {"Path": "assets/foo.prefab"},
        ]
        assert extract_prefab_paths(data) == ["assets/foo.prefab"]

    def test_handles_missing_or_null_path(self):
        data = [
            {"Path": None},
            {},
            {"Path": "assets/foo.prefab"},
        ]
        assert extract_prefab_paths(data) == ["assets/foo.prefab"]

    def test_empty(self):
        assert extract_prefab_paths([]) == []
