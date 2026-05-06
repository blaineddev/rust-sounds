from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from rust_sounds.index import build_entry, write_index_atomic
from rust_sounds.prefab_filter import is_effect_prefab, pick_audio_clip


@dataclass
class ExtractionResult:
    candidates: int = 0
    extracted: int = 0
    skipped_no_audio: int = 0
    # Counts both decode failures (UnityPy couldn't read sample bytes) and transcode
    # failures (ffmpeg rejected the WAV). The user-facing summary surfaces them under
    # the single label "decode-failed" because the distinction rarely matters to the
    # operator — the prefab is unusable either way.
    skipped_decode_failed: int = 0


# Type aliases for the injectable boundaries
WalkBundles = Callable[[Path], Iterable[Any]]  # yields PrefabView-like objects
Decode = Callable[[Any], tuple[bytes, int]]    # (wav_bytes, duration_ms)
Transcode = Callable[[bytes, Path], None]


def run_extraction(
    bundles_dir: Path,
    output_dir: Path,
    walk_bundles: WalkBundles,
    decode: Decode,
    transcode: Transcode,
    strict: bool = False,
    limit: int | None = None,
) -> ExtractionResult:
    output_dir = Path(output_dir)
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    result = ExtractionResult()
    entries = []

    for view in walk_bundles(bundles_dir):
        if limit is not None and result.extracted >= limit:
            break

        if not is_effect_prefab(view):
            result.skipped_no_audio += 1
            continue
        result.candidates += 1

        chosen, extras = pick_audio_clip(view.audio_clips, view.root_source_clip if hasattr(view, "root_source_clip") else None)
        if chosen is None:
            result.skipped_no_audio += 1
            continue

        try:
            wav_bytes, duration_ms = decode(chosen)
        except Exception as exc:
            if strict:
                raise
            print(f"[skip] decode failed for {view.container}: {exc}")
            result.skipped_decode_failed += 1
            continue

        entry = build_entry(
            prefab_path=view.container,
            duration_ms=duration_ms,
            extra_clips=extras,
        )
        target = output_dir / entry.file
        try:
            transcode(wav_bytes, target)
        except Exception as exc:
            if strict:
                raise
            print(f"[skip] transcode failed for {view.container}: {exc}")
            result.skipped_decode_failed += 1
            continue

        entries.append(entry)
        result.extracted += 1

    write_index_atomic(entries, output_dir / "index.json")
    return result
