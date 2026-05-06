from pathlib import Path

import pytest

from rust_sounds.locator import RustNotFoundError, find_bundles_dir, find_rust_install


class TestFindRustInstall:
    def test_returns_explicit_path_when_provided(self, tmp_path: Path):
        (tmp_path / "Bundles").mkdir()
        (tmp_path / "Bundles" / "manifest").write_bytes(b"")
        assert find_rust_install(explicit=tmp_path) == tmp_path

    def test_raises_if_explicit_path_has_no_bundles(self, tmp_path: Path):
        with pytest.raises(RustNotFoundError, match="Bundles"):
            find_rust_install(explicit=tmp_path)

    def test_searches_known_steam_paths(self, tmp_path: Path, monkeypatch):
        fake_steam = tmp_path / "steam" / "steamapps" / "common" / "Rust"
        (fake_steam / "Bundles").mkdir(parents=True)

        monkeypatch.setattr(
            "rust_sounds.locator._STEAM_CANDIDATES",
            [fake_steam],
        )
        assert find_rust_install() == fake_steam

    def test_raises_with_helpful_message_when_not_found(self, monkeypatch):
        monkeypatch.setattr("rust_sounds.locator._STEAM_CANDIDATES", [])
        with pytest.raises(RustNotFoundError, match="--rust-dir"):
            find_rust_install()


class TestFindBundlesDir:
    def test_returns_bundles_subdirectory(self, tmp_path: Path):
        (tmp_path / "Bundles").mkdir()
        assert find_bundles_dir(tmp_path) == tmp_path / "Bundles"

    def test_raises_if_missing(self, tmp_path: Path):
        with pytest.raises(RustNotFoundError):
            find_bundles_dir(tmp_path)
