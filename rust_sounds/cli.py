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


def _walk_bundles_via_unitypy(
    bundles_dir: Path,
    *,
    bundle_glob: str = "audio*.bundle",
    max_bundle_mb: float | None = None,
    rss_abort_mb: int = 4096,
) -> Iterator[Any]:
    """Yield one PrefabView-shaped object per AudioClip with an `assets/...` container.

    Why we walk AudioClips instead of GameObjects: in current Rust bundles, the
    effect-prefab GameObjects (`assets/prefabs/.../foo.prefab`) live in `content.bundle`,
    which is too large to load on consumer hardware (4 GB on disk → ~15 GB resident).
    The AudioClips themselves carry container paths under `assets/...` directly inside
    `audio.bundle` (~1.7 GB resident, well within budget), so we use those paths as
    identifiers. See conversation notes in the README.
    """
    import UnityPy
    from types import SimpleNamespace

    _log(f"scanning {bundles_dir} for {bundle_glob} …")
    t0 = time.monotonic()
    bundle_paths = sorted(p for p in bundles_dir.rglob(bundle_glob))
    _log(f"found {len(bundle_paths)} bundles matching {bundle_glob} in {time.monotonic() - t0:.1f}s")

    if not bundle_paths:
        return

    sizes = sorted(
        ((p.stat().st_size, p) for p in bundle_paths),
        key=lambda t: -t[0],
    )
    _log("matched bundles (largest first):")
    for size, p in sizes[:10]:
        _log(f"  {size / (1024 * 1024):>8.1f} MB  {p.name}")

    try:
        from tqdm import tqdm
        bundle_iter = tqdm(bundle_paths, desc="bundles", unit="bundle", file=sys.stderr)
    except ImportError:
        bundle_iter = bundle_paths

    for bundle_path in bundle_iter:
        size_mb = bundle_path.stat().st_size / (1024 * 1024)

        if max_bundle_mb is not None and size_mb > max_bundle_mb:
            _log(f"SKIP oversized bundle {bundle_path.name} ({size_mb:.1f} MB > {max_bundle_mb} MB cap)")
            continue

        rss_before = _rss_mb()
        if rss_before > rss_abort_mb:
            _log(f"ABORT: RSS {rss_before} MB exceeds --rss-abort-mb {rss_abort_mb}")
            raise SystemExit(3)

        _log(f"load  {bundle_path.name}  size={size_mb:.1f} MB  RSS_before={rss_before}")
        t_load = time.monotonic()
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception as exc:
            print(f"[skip] could not parse bundle {bundle_path.name}: {exc}", file=sys.stderr, flush=True)
            continue
        load_s = time.monotonic() - t_load
        _log(f"loaded {bundle_path.name}  in {load_s:.2f}s  RSS_after_load={_rss_mb()}")

        # Cheap scan: collect AudioClip handles by type without .read()-ing them.
        audio_clip_objs = [obj for obj in env.objects if obj.type.name == "AudioClip"]
        _log(f"AudioClips in {bundle_path.name}: {len(audio_clip_objs)}  RSS={_rss_mb()}")

        if not audio_clip_objs:
            _log(f"no AudioClips in {bundle_path.name}, skipping")
            del env, audio_clip_objs
            gc.collect()
            continue

        yielded = 0
        skipped_no_path = 0
        for ac_obj in audio_clip_objs:
            container = (ac_obj.container or "").lower()
            if not container.startswith("assets/"):
                skipped_no_path += 1
                continue
            try:
                clip = ac_obj.read()
            except Exception:
                continue
            # UnityPy 1.20 stores the asset name under `m_Name` (Unity's native field).
            # Fall back to the path leaf if that's missing.
            clip_name = (
                getattr(clip, "m_Name", None)
                or getattr(clip, "name", None)
                or container.rsplit("/", 1)[-1]
            )
            clip_ref = SimpleNamespace(name=clip_name, raw_handle=clip)
            yielded += 1
            yield SimpleNamespace(
                container=container,
                has_audio_source=True,
                audio_clips=[clip_ref],
                root_source_clip=clip_ref,
            )

        _log(
            f"yielded {yielded} clips from {bundle_path.name} "
            f"(skipped_no_assets_path={skipped_no_path})  RSS={_rss_mb()}"
        )

        del env, audio_clip_objs
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
        "--max-bundle-mb",
        type=float,
        default=None,
        help="Skip bundles larger than this many MB (default: no cap).",
    )
    parser.add_argument(
        "--rss-abort-mb",
        type=int,
        default=4096,
        help="Abort if process RSS exceeds this many MB (default: 4096).",
    )
    parser.add_argument(
        "--bundle-glob",
        default="audio*.bundle",
        help=(
            "Which bundle filenames to load, as an rglob pattern (default: audio*.bundle). "
            "Other Rust bundles contain no AudioClips and would just burn RAM. "
            "Pass --bundle-glob '*.bundle' to scan everything."
        ),
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
        return _walk_bundles_via_unitypy(
            bd,
            bundle_glob=args.bundle_glob,
            max_bundle_mb=args.max_bundle_mb,
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
