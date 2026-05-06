import json
import urllib.request
from pathlib import Path
from typing import Any

CARBON_PREFABS_URL = "https://api.carbonmod.gg/meta/rust/prefabs.json"


def fetch_prefabs(
    url: str = CARBON_PREFABS_URL,
    cache_path: Path | None = None,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Return Carbon's prefab list (a list of dicts with `Path`/`Name`/`Components`/`ID`).

    If `cache_path` is provided and the file exists, read it instead of hitting the
    network. Otherwise download from `url`. We don't auto-write the cache here; the
    caller controls persistence so they can decide whether the freshly-fetched data
    should overwrite a stale cache.
    """
    if cache_path is not None and Path(cache_path).is_file():
        return json.loads(Path(cache_path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_prefab_paths(prefabs_data: list[dict[str, Any]]) -> list[str]:
    """Return sorted, unique, lowercased `assets/.../*.prefab` paths from Carbon's data."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in prefabs_data:
        p = (entry.get("Path") or "").lower()
        if not p.startswith("assets/") or not p.endswith(".prefab"):
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    out.sort()
    return out
