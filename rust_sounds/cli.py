import argparse
import gc
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from rust_sounds.audio import (
    FfmpegMissingError,
    decode_clip_to_wav,
    transcode_wav_to_mp3,
)
from rust_sounds.locator import RustNotFoundError, find_bundles_dir, find_rust_install
from rust_sounds.pipeline import run_extraction
from rust_sounds.prefabs import CARBON_PREFABS_URL, extract_prefab_paths, fetch_prefabs


def _rss_mb() -> int:
    """Resident set size in MB. Returns -1 if /proc/self/status is unreadable."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return -1
    return -1


def _log(msg: str) -> None:
    print(f"[{_rss_mb():>5}MB] {msg}", flush=True)


def _walk_via_prefab_list(
    bundles_dir: Path,
    *,
    prefabs_url: str,
    prefabs_cache: Path | None,
    rss_abort_mb: int = 4096,
) -> Iterator[Any]:
    """Yield one PrefabView per Carbon-listed prefab that we can pair with an
    AudioClip in `audio.bundle` by name-stem.

    Pairing rules per prefab stem `S`:
    - **strict**: an AudioClip whose stem is exactly `S` wins.
    - **loose**: otherwise, any AudioClip whose stem is `S-…` or `S_…` qualifies; we
      pick the alphabetically-first as the chosen clip and surface the rest as
      `extra_clips` in the index.
    Carbon's `Components` field is unreliable for filtering audio (Unity built-ins
    like AudioSource aren't listed), so we treat the whole prefab list as candidates
    and let the name-pair gate decide what makes the final cut.
    """
    import UnityPy
    from types import SimpleNamespace

    audio_bundle = bundles_dir / "shared" / "audio.bundle"
    if not audio_bundle.is_file():
        candidates = sorted(bundles_dir.rglob("audio*.bundle"))
        if not candidates:
            raise RustNotFoundError(
                f"No audio*.bundle found under {bundles_dir}. "
                "Cannot resolve AudioClips referenced by prefabs."
            )
        audio_bundle = candidates[0]

    _log(f"audio bundle: {audio_bundle}")

    rss_before = _rss_mb()
    if rss_before > rss_abort_mb:
        _log(f"ABORT: RSS {rss_before} MB exceeds --rss-abort-mb {rss_abort_mb}")
        raise SystemExit(3)

    t = time.monotonic()
    _log(f"fetching prefab list (url={prefabs_url}, cache={prefabs_cache})")
    try:
        prefabs_data = fetch_prefabs(url=prefabs_url, cache_path=prefabs_cache)
    except Exception as exc:
        raise RustNotFoundError(
            f"failed to fetch prefab list from {prefabs_url}: {exc}. "
            "Provide --prefabs-cache pointing at a local JSON copy if offline."
        ) from exc
    paths = extract_prefab_paths(prefabs_data)
    _log(f"prefab list: {len(paths)} unique paths in {time.monotonic() - t:.2f}s")
    if not paths:
        return

    size_mb = audio_bundle.stat().st_size / (1024 * 1024)
    _log(f"load  {audio_bundle.name}  size={size_mb:.1f} MB  RSS_before={_rss_mb()}")
    t = time.monotonic()
    try:
        env = UnityPy.load(str(audio_bundle))
    except Exception as exc:
        raise RustNotFoundError(f"could not parse {audio_bundle.name}: {exc}") from exc
    _log(f"loaded {audio_bundle.name} in {time.monotonic() - t:.2f}s  RSS_after_load={_rss_mb()}")

    # Build {stem: [(clip_handle, container_path), ...]} by reading every AudioClip
    # in audio.bundle once. Stems can collide across paths (e.g. two monuments with
    # a clip called "door-open-01"); we keep all and sort by container later for
    # deterministic tie-break.
    clips_by_stem: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    for obj in env.objects:
        if obj.type.name != "AudioClip":
            continue
        container = (obj.container or "").lower()
        if not container.startswith("assets/"):
            continue
        stem = container.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        try:
            clip = obj.read()
        except Exception:
            continue
        clips_by_stem[stem].append((clip, container))
    _log(f"indexed {sum(len(v) for v in clips_by_stem.values())} clips across {len(clips_by_stem)} unique stems  RSS={_rss_mb()}")

    strict = loose = unmatched = 0
    try:
        from tqdm import tqdm
        prefab_iter = tqdm(paths, desc="prefabs", unit="prefab", file=sys.stderr)
    except ImportError:
        prefab_iter = paths

    for prefab_path in prefab_iter:
        prefab_stem = prefab_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        # Candidates: (priority, stem, clip, container). priority 0 = strict (stem
        # equals prefab stem), 1 = loose (stem starts with prefab_stem + '-' or '_').
        # Sort by (priority asc, stem asc, container asc) so strict beats loose and
        # ties resolve deterministically.
        candidates: list[tuple[int, str, Any, str]] = []
        for clip, container in clips_by_stem.get(prefab_stem, []):
            candidates.append((0, prefab_stem, clip, container))
        for stem, entries in clips_by_stem.items():
            if stem == prefab_stem:
                continue
            if stem.startswith(prefab_stem + "-") or stem.startswith(prefab_stem + "_"):
                for clip, container in entries:
                    candidates.append((1, stem, clip, container))

        if not candidates:
            unmatched += 1
            continue

        candidates.sort(key=lambda t: (t[0], t[1], t[3]))
        chosen_priority, chosen_name, chosen_clip, _ = candidates[0]
        if chosen_priority == 0:
            strict += 1
        else:
            loose += 1

        # Extras: deduped stems excluding the chosen one. Multiple clips with the
        # same stem (across different paths) collapse to one display name; the
        # chosen's stem is omitted because it would just repeat the name field.
        seen_stems = {chosen_name}
        extra_names: list[str] = []
        for _, stem, _, _ in candidates[1:]:
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            extra_names.append(stem)

        chosen_ref = SimpleNamespace(name=chosen_name, raw_handle=chosen_clip)
        # `extras` get name-only stand-ins (no raw_handle, never decoded). The pipeline's
        # pick_audio_clip uses identity to pick `chosen_ref` and emits the others' names.
        audio_clips = [chosen_ref] + [SimpleNamespace(name=n) for n in extra_names]
        yield SimpleNamespace(
            container=prefab_path,
            has_audio_source=True,
            audio_clips=audio_clips,
            root_source_clip=chosen_ref,
        )

    _log(
        f"prefab pairing: strict={strict} loose={loose} unmatched={unmatched} "
        f"(of {len(paths)} prefabs)  RSS={_rss_mb()}"
    )

    del env, clips_by_stem
    gc.collect()


def _decode(clip_ref: Any) -> tuple[bytes, int]:
    return decode_clip_to_wav(clip_ref.raw_handle)


def _make_transcode(ffmpeg_bin: str):
    def transcode(wav_bytes: bytes, target: Path) -> None:
        transcode_wav_to_mp3(wav_bytes, target, ffmpeg_bin=ffmpeg_bin)
    return transcode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract.py", description="Extract Rust effect-prefab MP3s.")
    parser.add_argument("--rust-dir", type=Path, default=None, help="Path to Rust install root (auto-detected if omitted).")
    parser.add_argument("--out", type=Path, default=Path("."), help="Output directory (default: current dir).")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary (default: 'ffmpeg' on PATH).")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N extracted prefabs (debug).")
    parser.add_argument("--strict", action="store_true", help="Abort on any extraction error.")
    parser.add_argument(
        "--rss-abort-mb",
        type=int,
        default=4096,
        help="Abort if process RSS exceeds this many MB (default: 4096).",
    )
    parser.add_argument(
        "--prefabs-url",
        default=CARBON_PREFABS_URL,
        help=f"URL to fetch the canonical prefab list from (default: {CARBON_PREFABS_URL}).",
    )
    parser.add_argument(
        "--prefabs-cache",
        type=Path,
        default=None,
        help="If provided and the file exists, read the prefab list from here instead of fetching.",
    )
    args = parser.parse_args(argv)

    _log(f"start: pid={os.getpid()} python={sys.version.split()[0]} cwd={os.getcwd()}")

    # Belt-and-braces: a kernel-enforced address-space cap so a runaway allocation
    # (e.g. UnityPy.load on a multi-GB bundle) raises MemoryError instead of OOMing
    # the whole machine. RLIMIT_AS is a Linux/WSL feature; silently ignored elsewhere.
    try:
        import resource
        cap_bytes = int(args.rss_abort_mb * 1.5) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap_bytes, cap_bytes))
        _log(f"set RLIMIT_AS to {cap_bytes // (1024 * 1024)} MB")
    except Exception as exc:
        _log(f"could not set RLIMIT_AS: {exc}")

    try:
        rust_dir = find_rust_install(explicit=args.rust_dir)
        bundles_dir = find_bundles_dir(rust_dir)
    except RustNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _log(f"rust_dir={rust_dir}  bundles_dir={bundles_dir}")

    def walk(bd: Path):
        return _walk_via_prefab_list(
            bd,
            prefabs_url=args.prefabs_url,
            prefabs_cache=args.prefabs_cache,
            rss_abort_mb=args.rss_abort_mb,
        )

    try:
        result = run_extraction(
            bundles_dir=bundles_dir,
            output_dir=args.out,
            walk_bundles=walk,
            decode=_decode,
            transcode=_make_transcode(args.ffmpeg),
            strict=args.strict,
            limit=args.limit,
        )
    except FfmpegMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"\nextracted {result.extracted} of {result.candidates} candidate prefabs, "
        f"skipped {result.skipped_no_audio + result.skipped_decode_failed} "
        f"({result.skipped_no_audio} no-audio, {result.skipped_decode_failed} decode-failed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
