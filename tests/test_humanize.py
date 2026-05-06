from rust_sounds.humanize import name_from_path, category_from_path, prefab_hash


class TestNameFromPath:
    def test_strips_dir_and_extension(self):
        assert name_from_path("assets/prefabs/weapons/ak47/effects/fire.prefab") == "fire"

    def test_humanizes_underscores(self):
        assert name_from_path("assets/prefabs/foo/bar/shell_eject.prefab") == "shell eject"

    def test_humanizes_camel_case(self):
        assert name_from_path("assets/prefabs/foo/bar/ShellEject.prefab") == "shell eject"

    def test_handles_no_directory(self):
        assert name_from_path("fire.prefab") == "fire"


class TestCategoryFromPath:
    def test_strips_assets_prefabs_prefix(self):
        assert category_from_path("assets/prefabs/weapons/ak47/effects/fire.prefab") == "weapons/ak47/effects"

    def test_returns_root_when_directly_under_prefabs(self):
        assert category_from_path("assets/prefabs/foo.prefab") == ""

    def test_handles_paths_outside_prefabs(self):
        # Some bundle paths may begin with assets/content/ or similar
        assert category_from_path("assets/content/textures/foo.prefab") == "content/textures"


class TestPrefabHash:
    def test_returns_12_hex_chars(self):
        h = prefab_hash("assets/prefabs/weapons/ak47/effects/fire.prefab")
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_is_deterministic(self):
        # Pinned to the actual SHA-1[:12] of this exact input. If this assertion
        # ever fails, the hash algorithm or input encoding has changed — that's
        # a breaking change for already-extracted audio file names.
        assert prefab_hash("assets/prefabs/foo/bar.prefab") == "d3536f6fbe2c"

    def test_differs_for_different_paths(self):
        assert prefab_hash("a.prefab") != prefab_hash("b.prefab")
