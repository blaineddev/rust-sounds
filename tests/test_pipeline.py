import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from rust_sounds.pipeline import ExtractionResult, run_extraction


@dataclass
class FakeClip:
    name: str
    raw_handle: object = None
    # Decoder side reads this:
    wav_bytes: bytes = b""
    duration_ms: int = 0


@dataclass
class FakeView:
    container: str
    has_audio_source: bool
    audio_clips: list[FakeClip] = field(default_factory=list)
    root_source_clip: FakeClip | None = None


def _bundle_walker(views: list[FakeView]):
    def walk(_bundles_dir):
        yield from views
    return walk


def _decoder(wav: bytes, duration_ms: int):
    def decode(clip):
        return clip.wav_bytes or wav, clip.duration_ms or duration_ms
    return decode


def _transcoder(captured: list[Path]):
    def transcode(wav_bytes, target_path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"FAKE-MP3:" + wav_bytes[:4])
        captured.append(target_path)
    return transcode


class TestRunExtraction:
    def test_writes_index_and_mp3_for_each_effect_prefab(self, tmp_path: Path):
        views = [
            FakeView(
                container="assets/prefabs/weapons/ak47/effects/fire.prefab",
                has_audio_source=True,
                audio_clips=[FakeClip(name="fire", wav_bytes=b"WAVX", duration_ms=230)],
            ),
            FakeView(
                container="assets/prefabs/foo/no_audio.prefab",
                has_audio_source=False,
                audio_clips=[],
            ),
        ]
        captured: list[Path] = []
        result = run_extraction(
            bundles_dir=tmp_path / "bundles",
            output_dir=tmp_path,
            walk_bundles=_bundle_walker(views),
            decode=_decoder(b"WAVX", 230),
            transcode=_transcoder(captured),
        )

        assert isinstance(result, ExtractionResult)
        assert result.extracted == 1
        assert result.candidates == 1
        assert result.skipped_no_audio == 1

        index = json.loads((tmp_path / "index.json").read_text())
        assert len(index) == 1
        assert index[0]["prefab"] == "assets/prefabs/weapons/ak47/effects/fire.prefab"
        assert index[0]["duration_ms"] == 230

        mp3_path = tmp_path / index[0]["file"]
        assert mp3_path.exists()
        assert mp3_path.read_bytes().startswith(b"FAKE-MP3:")

    def test_continues_past_decode_failure(self, tmp_path: Path):
        good = FakeView(
            container="assets/prefabs/a.prefab",
            has_audio_source=True,
            audio_clips=[FakeClip(name="a", wav_bytes=b"WAVX", duration_ms=100)],
        )
        bad = FakeView(
            container="assets/prefabs/b.prefab",
            has_audio_source=True,
            audio_clips=[FakeClip(name="b")],  # no wav_bytes -> decoder will raise via our hook below
        )

        def decode(clip):
            if not clip.wav_bytes:
                raise RuntimeError("decode boom")
            return clip.wav_bytes, clip.duration_ms

        result = run_extraction(
            bundles_dir=tmp_path / "bundles",
            output_dir=tmp_path,
            walk_bundles=_bundle_walker([good, bad]),
            decode=decode,
            transcode=_transcoder([]),
        )
        assert result.extracted == 1
        assert result.skipped_decode_failed == 1

    def test_strict_mode_aborts_on_decode_failure(self, tmp_path: Path):
        bad = FakeView(
            container="assets/prefabs/b.prefab",
            has_audio_source=True,
            audio_clips=[FakeClip(name="b")],
        )

        def decode(clip):
            raise RuntimeError("decode boom")

        import pytest
        with pytest.raises(RuntimeError, match="decode boom"):
            run_extraction(
                bundles_dir=tmp_path / "bundles",
                output_dir=tmp_path,
                walk_bundles=_bundle_walker([bad]),
                decode=decode,
                transcode=_transcoder([]),
                strict=True,
            )

    def test_continues_past_transcode_failure(self, tmp_path: Path):
        # Same shape as the decode-failure test, but the transcoder is the one that raises.
        # Both failures are bucketed into skipped_decode_failed (see ExtractionResult docstring).
        good = FakeView(
            container="assets/prefabs/a.prefab",
            has_audio_source=True,
            audio_clips=[FakeClip(name="a", wav_bytes=b"WAVX", duration_ms=100)],
        )
        bad = FakeView(
            container="assets/prefabs/b.prefab",
            has_audio_source=True,
            audio_clips=[FakeClip(name="b", wav_bytes=b"WAVX", duration_ms=100)],
        )

        calls = {"n": 0}

        def transcode(wav_bytes, target_path):
            calls["n"] += 1
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if calls["n"] == 2:  # second prefab — fail it
                raise RuntimeError("transcode boom")
            target_path.write_bytes(b"FAKE-MP3:" + wav_bytes[:4])

        result = run_extraction(
            bundles_dir=tmp_path / "bundles",
            output_dir=tmp_path,
            walk_bundles=_bundle_walker([good, bad]),
            decode=_decoder(b"WAVX", 100),
            transcode=transcode,
        )
        assert result.extracted == 1
        assert result.skipped_decode_failed == 1

    def test_strict_mode_aborts_on_transcode_failure(self, tmp_path: Path):
        bad = FakeView(
            container="assets/prefabs/b.prefab",
            has_audio_source=True,
            audio_clips=[FakeClip(name="b", wav_bytes=b"WAVX", duration_ms=100)],
        )

        def transcode(wav_bytes, target_path):
            raise RuntimeError("transcode boom")

        import pytest
        with pytest.raises(RuntimeError, match="transcode boom"):
            run_extraction(
                bundles_dir=tmp_path / "bundles",
                output_dir=tmp_path,
                walk_bundles=_bundle_walker([bad]),
                decode=_decoder(b"WAVX", 100),
                transcode=transcode,
                strict=True,
            )

    def test_limit_caps_processed_prefabs(self, tmp_path: Path):
        views = [
            FakeView(
                container=f"assets/prefabs/x{i}.prefab",
                has_audio_source=True,
                audio_clips=[FakeClip(name=f"x{i}", wav_bytes=b"WAVX", duration_ms=10)],
            )
            for i in range(5)
        ]
        result = run_extraction(
            bundles_dir=tmp_path / "bundles",
            output_dir=tmp_path,
            walk_bundles=_bundle_walker(views),
            decode=_decoder(b"WAVX", 10),
            transcode=_transcoder([]),
            limit=2,
        )
        assert result.extracted == 2
