import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any


class TranscodeError(RuntimeError):
    pass


class FfmpegMissingError(RuntimeError):
    pass


def _ensure_ffmpeg(ffmpeg_bin: str = "ffmpeg") -> str:
    resolved = shutil.which(ffmpeg_bin)
    if resolved is None:
        raise FfmpegMissingError(
            f"Could not find '{ffmpeg_bin}' on PATH. Install ffmpeg "
            "(https://ffmpeg.org/download.html) or pass --ffmpeg <path>."
        )
    return resolved


def decode_clip_to_wav(unity_audio_clip: Any) -> tuple[bytes, int]:
    """Decode a UnityPy AudioClip into (wav_bytes, duration_ms).

    UnityPy 1.20+ exposes AudioClip.samples — a dict of {name: wav_bytes}.
    We pick the first sample. Duration is computed from the WAV header.
    """
    samples = getattr(unity_audio_clip, "samples", None)
    if not samples:
        raise TranscodeError(f"AudioClip {getattr(unity_audio_clip, 'name', '?')} produced no samples")
    wav_bytes = next(iter(samples.values()))

    # Parse WAV header for duration: bytes 22..24 = channels, 24..28 = sample rate,
    # 34..36 = bits per sample, then find 'data' chunk size.
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise TranscodeError("Decoded sample is not a valid WAV")
    channels = struct.unpack("<H", wav_bytes[22:24])[0]
    sample_rate = struct.unpack("<I", wav_bytes[24:28])[0]
    bits = struct.unpack("<H", wav_bytes[34:36])[0]
    # Find data chunk
    idx = wav_bytes.find(b"data", 12)
    if idx == -1:
        raise TranscodeError("WAV missing data chunk")
    data_size = struct.unpack("<I", wav_bytes[idx + 4:idx + 8])[0]
    bytes_per_sample = (bits // 8) * channels
    if bytes_per_sample == 0 or sample_rate == 0:
        raise TranscodeError("WAV header malformed (zero sample rate or channels)")
    duration_ms = int(round(1000 * data_size / (sample_rate * bytes_per_sample)))
    return wav_bytes, duration_ms


def transcode_wav_to_mp3(
    wav_bytes: bytes,
    target: Path,
    ffmpeg_bin: str = "ffmpeg",
    bitrate_kbps: int = 128,
) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ensure_ffmpeg(ffmpeg_bin)

    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error",
        "-y",
        "-f", "wav",
        "-i", "pipe:0",
        "-codec:a", "libmp3lame",
        "-b:a", f"{bitrate_kbps}k",
        "-ac", "1",
        str(target),
    ]
    proc = subprocess.run(cmd, input=wav_bytes, capture_output=True)
    if proc.returncode != 0:
        raise TranscodeError(
            f"ffmpeg failed (exit {proc.returncode}): {proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
