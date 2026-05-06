import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rust_sounds.humanize import category_from_path, name_from_path, prefab_hash


@dataclass
class IndexEntry:
    prefab: str
    file: str
    name: str
    category: str
    duration_ms: int
    extra_clips: list[str] = field(default_factory=list)


def build_entry(
    prefab_path: str,
    duration_ms: int,
    extra_clips: list[str] | None = None,
) -> IndexEntry:
    return IndexEntry(
        prefab=prefab_path,
        file=f"audio/{prefab_hash(prefab_path)}.mp3",
        name=name_from_path(prefab_path),
        category=category_from_path(prefab_path),
        duration_ms=duration_ms,
        extra_clips=list(extra_clips) if extra_clips else [],
    )


def write_index_atomic(entries: list[IndexEntry], target: Path) -> None:
    """Write index.json via temp + atomic rename, so a crash never leaves a partial file."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(e) for e in entries]
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
