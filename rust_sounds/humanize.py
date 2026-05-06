import hashlib
import re
from pathlib import PurePosixPath

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def name_from_path(prefab_path: str) -> str:
    """Humanize the leaf of a prefab path: 'foo/bar/ShellEject.prefab' -> 'shell eject'."""
    leaf = PurePosixPath(prefab_path).stem
    leaf = leaf.replace("_", " ").replace("-", " ")
    leaf = _CAMEL_BOUNDARY.sub(" ", leaf)
    return " ".join(leaf.lower().split())


def category_from_path(prefab_path: str) -> str:
    """Parent directory of the prefab, with 'assets/prefabs/' or 'assets/' stripped."""
    parent = str(PurePosixPath(prefab_path).parent).replace("\\", "/")
    # PurePosixPath strips the trailing slash, so the parent of
    # "assets/prefabs/foo.prefab" is "assets/prefabs" with no trailing slash.
    # This exact-match check must come before the startswith() guards below.
    if parent == "assets/prefabs":
        return ""
    if parent.startswith("assets/prefabs/"):
        return parent[len("assets/prefabs/"):]
    if parent.startswith("assets/"):
        return parent[len("assets/"):]
    if parent in (".", ""):
        return ""
    return parent


def prefab_hash(prefab_path: str) -> str:
    """Deterministic 12-char hex hash; used as the MP3 filename stem."""
    return hashlib.sha1(prefab_path.encode("utf-8")).hexdigest()[:12]
