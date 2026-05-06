from dataclasses import dataclass, field
from typing import Any

from rust_sounds.prefab_filter import (
    AudioClipRef,
    PrefabView,
    is_effect_prefab,
    pick_audio_clip,
)


@dataclass
class FakeAudioClipRef:
    name: str
    duration_ms: int
    raw_handle: Any = None


@dataclass
class FakeView:
    container: str
    has_audio_source: bool
    audio_clips: list[FakeAudioClipRef] = field(default_factory=list)


class TestIsEffectPrefab:
    def test_accepts_effect_with_audio_source(self):
        v = FakeView(
            container="assets/prefabs/weapons/ak47/effects/fire.prefab",
            has_audio_source=True,
            audio_clips=[FakeAudioClipRef("fire", 230)],
        )
        assert is_effect_prefab(v) is True

    def test_accepts_effect_with_audio_clip_but_no_source(self):
        v = FakeView(
            container="assets/prefabs/weapons/ak47/effects/fire.prefab",
            has_audio_source=False,
            audio_clips=[FakeAudioClipRef("fire", 230)],
        )
        assert is_effect_prefab(v) is True

    def test_rejects_when_no_audio_at_all(self):
        v = FakeView(
            container="assets/prefabs/foo.prefab",
            has_audio_source=False,
            audio_clips=[],
        )
        assert is_effect_prefab(v) is False

    def test_rejects_when_container_outside_assets(self):
        v = FakeView(
            container="content/foo.prefab",
            has_audio_source=True,
            audio_clips=[FakeAudioClipRef("x", 100)],
        )
        assert is_effect_prefab(v) is False


class TestPickAudioClip:
    def test_single_clip_returns_it_with_no_extras(self):
        clips = [FakeAudioClipRef("fire", 230)]
        chosen, extras = pick_audio_clip(clips, root_source_clip=clips[0])
        assert chosen is clips[0]
        assert extras == []

    def test_root_source_wins_when_multiple(self):
        a = FakeAudioClipRef("alpha", 100)
        b = FakeAudioClipRef("bravo", 200)
        chosen, extras = pick_audio_clip([a, b], root_source_clip=b)
        assert chosen is b
        assert extras == ["alpha"]

    def test_alphabetical_tiebreak_when_no_root_clip(self):
        a = FakeAudioClipRef("zulu", 100)
        b = FakeAudioClipRef("alpha", 200)
        c = FakeAudioClipRef("mike", 150)
        chosen, extras = pick_audio_clip([a, b, c], root_source_clip=None)
        assert chosen is b  # alphabetical first
        assert extras == ["mike", "zulu"]

    def test_returns_none_for_empty_list(self):
        chosen, extras = pick_audio_clip([], root_source_clip=None)
        assert chosen is None
        assert extras == []
