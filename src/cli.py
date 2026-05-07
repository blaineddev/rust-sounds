import argparse
import os
import sys
from pathlib import Path

from src import reference_extract
from src.audio import FfmpegMissingError
from src.locator import RustNotFoundError, find_rust_install


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="extract.py",
        description="Reference-based extractor: walks Rust's Unity bundles "
                    "(assetscenes -> content -> audio), resolves every "
                    "prefab -> SoundDefinition -> AudioClip PPtr end-to-end, "
                    "and writes data/sounds.json + audio/*.mp3.",
    )
    parser.add_argument("--rust-dir", type=Path, default=None,
                        help="Rust install root (auto-detected if omitted).")
    parser.add_argument("--out", type=Path, default=Path("."),
                        help="Output directory (default: current dir).")
    parser.add_argument("--ffmpeg", default="ffmpeg",
                        help="ffmpeg binary (default: 'ffmpeg' on PATH).")
    parser.add_argument("--rss-abort-mb", type=int, default=9216,
                        help="Abort if process RSS exceeds this many MB (default: 9216).")
    args = parser.parse_args(argv)

    _log(f"start: pid={os.getpid()} python={sys.version.split()[0]} cwd={os.getcwd()}")

    # Address-space cap so a runaway allocation raises MemoryError instead of
    # OOMing the whole machine. RLIMIT_AS is a Linux/WSL feature; silently
    # ignored elsewhere.
    try:
        import resource
        cap_bytes = int(args.rss_abort_mb * 1.5) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap_bytes, cap_bytes))
        _log(f"set RLIMIT_AS to {cap_bytes // (1024 * 1024)} MB")
    except Exception as exc:
        _log(f"could not set RLIMIT_AS: {exc}")

    try:
        rust_dir = find_rust_install(explicit=args.rust_dir)
    except RustNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _log(f"rust_dir={rust_dir}")

    try:
        summary = reference_extract.run(
            rust_dir=rust_dir,
            out_dir=args.out,
            ffmpeg_bin=args.ffmpeg,
            log=_log,
        )
    except FfmpegMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    reference_extract.print_summary(summary, log=_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
