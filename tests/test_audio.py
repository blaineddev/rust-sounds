import shutil
import struct
import wave
from io import BytesIO
from pathlib import Path

import pytest

from src.audio import TranscodeError, transcode_wav_to_mp3

ffmpeg_missing = shutil.which("ffmpeg") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not on PATH")


def _silent_wav_bytes(seconds: float = 0.1, sample_rate: int = 22050) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        n = int(seconds * sample_rate)
        w.writeframes(struct.pack("<" + "h" * n, *([0] * n)))
    return buf.getvalue()


class TestTranscodeWavToMp3:
    def test_produces_mp3_file(self, tmp_path: Path):
        out = tmp_path / "out.mp3"
        transcode_wav_to_mp3(_silent_wav_bytes(), out)
        assert out.exists()
        assert out.stat().st_size > 0
        # MP3 frames start with 0xFF + (0xE0..0xFF). ID3 tags start with 'ID3'.
        head = out.read_bytes()[:3]
        assert head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)

    def test_creates_parent_directory(self, tmp_path: Path):
        out = tmp_path / "nested/dir/out.mp3"
        transcode_wav_to_mp3(_silent_wav_bytes(), out)
        assert out.exists()

    def test_raises_on_invalid_wav_bytes(self, tmp_path: Path):
        out = tmp_path / "out.mp3"
        with pytest.raises(TranscodeError):
            transcode_wav_to_mp3(b"this is not wav data at all", out)
