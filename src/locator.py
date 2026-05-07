import os
from pathlib import Path


class RustNotFoundError(RuntimeError):
    pass


def _wsl_steam_candidates() -> list[Path]:
    """Common Rust install paths reachable from WSL via /mnt/c, /mnt/d, etc."""
    paths: list[Path] = []
    for drive in ("c", "d", "e"):
        paths.append(Path(f"/mnt/{drive}/Program Files (x86)/Steam/steamapps/common/Rust"))
        paths.append(Path(f"/mnt/{drive}/Program Files/Steam/steamapps/common/Rust"))
        paths.append(Path(f"/mnt/{drive}/SteamLibrary/steamapps/common/Rust"))
    return paths


def _native_steam_candidates() -> list[Path]:
    home = Path.home()
    return [
        # Linux native
        home / ".steam/steam/steamapps/common/Rust",
        home / ".local/share/Steam/steamapps/common/Rust",
        # macOS
        home / "Library/Application Support/Steam/steamapps/common/Rust",
        # Windows native (when this script runs on Windows Python directly)
        Path("C:/Program Files (x86)/Steam/steamapps/common/Rust"),
        Path("C:/Program Files/Steam/steamapps/common/Rust"),
        Path("D:/SteamLibrary/steamapps/common/Rust"),
    ]


_STEAM_CANDIDATES: list[Path] = _wsl_steam_candidates() + _native_steam_candidates()


def find_rust_install(explicit: Path | None = None) -> Path:
    """Return the Rust install root.

    If `explicit` is given, validate it has a Bundles/ child.
    Otherwise scan known Steam paths.
    Raises RustNotFoundError if nothing usable is found.
    """
    if explicit is not None:
        explicit = Path(explicit)
        if not (explicit / "Bundles").is_dir():
            raise RustNotFoundError(
                f"{explicit} does not contain a Bundles/ directory. "
                "Pass --rust-dir pointing at the Rust install root."
            )
        return explicit

    for candidate in _STEAM_CANDIDATES:
        if (candidate / "Bundles").is_dir():
            return candidate

    tried = "\n  ".join(str(p) for p in _STEAM_CANDIDATES) or "(no candidates)"
    raise RustNotFoundError(
        "Could not auto-detect a Rust install with a Bundles/ directory.\n"
        f"Tried:\n  {tried}\n"
        "Pass --rust-dir to point at it explicitly."
    )


def find_bundles_dir(rust_dir: Path) -> Path:
    bundles = Path(rust_dir) / "Bundles"
    if not bundles.is_dir():
        raise RustNotFoundError(f"No Bundles/ directory under {rust_dir}")
    return bundles
