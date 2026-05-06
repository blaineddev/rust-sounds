from typing import Any, Protocol


class AudioClipRef(Protocol):
    name: str
    duration_ms: int
    raw_handle: Any  # UnityPy AudioClip object, used later for decoding


class PrefabView(Protocol):
    """The minimum surface our filter needs from UnityPy.

    Adapter in pipeline.py converts a real UnityPy GameObject into one of these.
    """
    container: str
    has_audio_source: bool
    audio_clips: list[AudioClipRef]


def is_effect_prefab(view: PrefabView) -> bool:
    if not view.container.startswith("assets/"):
        return False
    if not view.audio_clips:
        return False
    # Reaching here means there's at least one AudioClip on this prefab, which alone
    # qualifies it (regardless of whether an AudioSource component is present).
    return True


def pick_audio_clip(
    clips: list[AudioClipRef],
    root_source_clip: AudioClipRef | None,
) -> tuple[AudioClipRef | None, list[str]]:
    """Return (chosen_clip, extra_clip_names).

    Selection rules (per spec):
    1. If root AudioSource has a clip, that wins.
    2. Otherwise, pick the alphabetically-first clip name.
    3. extras = sorted names of every clip *not* chosen.
    """
    if not clips:
        return None, []

    if root_source_clip is not None and root_source_clip in clips:
        chosen = root_source_clip
    else:
        chosen = sorted(clips, key=lambda c: c.name)[0]

    extras = sorted(c.name for c in clips if c is not chosen)
    return chosen, extras
