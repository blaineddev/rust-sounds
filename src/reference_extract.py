"""Reference-based extraction of FX prefab → AudioClip mappings.

Three Unity bundles are walked in sequence (memory tight at ~5 GB peak):

  1. assetscenes.bundle — holds the FX prefab GameObjects as scene contents.
     We find each one by matching m_Name against the /fx/-or-/effects/ filter,
     walk its component hierarchy, and record:
       - SoundPlayer.soundDefinition external PPtrs
       - AudioSource.m_audioClip external PPtrs
       - any GameObjectRef-shaped fields ({"guid": "..."}) for compound
         classification later

  2. content.bundle — holds the SoundDefinition assets AND GameManifest. For
     every SoundDefinition we recorded a PPtr to, we walk weightedAudioClips
     + distanceAudioClips to resolve to AudioClip PPtrs. We also pull out
     GameManifest.prefabProperties / .guidPaths so we can resolve the GORs
     from pass 1 to actual prefab paths.

  3. audio.bundle — holds the AudioClips. We decode each one we need (via
     UnityPy.AudioClip.samples) and transcode to mono 128k MP3 with ffmpeg.

The pipeline is single-process; bundles unload between passes so peak RSS
stays around the largest single bundle (~5 GB for content.bundle).

Output:
  data/sounds.json — schema described in the project plan
  audio/<sha1(container_path)[:12]>.mp3 — one file per unique referenced clip
"""
from __future__ import annotations

import gc
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.audio import (
    decode_clip_to_wav,
    transcode_wav_to_mp3,
)


# --- module-level helpers -----------------------------------------------------

def _is_pptr(v: Any) -> bool:
    return isinstance(v, dict) and "m_FileID" in v and "m_PathID" in v


def _is_gameobjectref(v: Any) -> bool:
    # GameObjectRef and ResourceRef<T> serialize as just {"guid": "<hex>"}.
    # Larger structs that happen to contain a "guid" field don't match this
    # exact-key check, so we get clean detection of the ref type.
    return isinstance(v, dict) and set(v.keys()) == {"guid"}


def _cab_basename(s: str | None) -> str:
    if not s:
        return ""
    return s.rstrip("/").rsplit("/", 1)[-1]


def _extern_dep_name(assets_file, file_id: int) -> str | None:
    if file_id <= 0:
        return None
    externals = getattr(assets_file, "externals", None) or []
    if file_id - 1 >= len(externals):
        return None
    ext = externals[file_id - 1]
    return getattr(ext, "path", None) or getattr(ext, "pathName", None) or None


def categories_for(path: str) -> tuple[list[str], bool]:
    """Return (categories, used_fallback) for a prefab path.

    Three known FX path families:
      assets/bundled/prefabs/fx/A/B/C/file.prefab        -> [A, B, C]
      assets/content/effects/A/B/file.prefab             -> [A, B]
      assets/<prefabs|content>/X/Y/effects/file.prefab   -> [X, Y]

    Anything else falls through to a generic split, with `used_fallback=True`
    so the extraction summary can report the count.
    """
    p = path.lower()
    if p.startswith("assets/"):
        p = p[len("assets/"):]

    if p.startswith("bundled/prefabs/fx/"):
        return p[len("bundled/prefabs/fx/"):].split("/")[:-1], False

    if p.startswith("content/effects/"):
        return p[len("content/effects/"):].split("/")[:-1], False

    if "/effects/" in p:
        head = p.split("/effects/", 1)[0]
        for prefix in ("prefabs/", "content/"):
            if head.startswith(prefix):
                return head[len(prefix):].split("/"), False
        return head.split("/"), True

    return p.split("/")[:-1], True


def _name_for(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if base.endswith(".prefab"):
        base = base[: -len(".prefab")]
    return base


# --- pass 1: assetscenes.bundle ----------------------------------------------

@dataclass
class PrefabAudioRefs:
    path: str
    audio_refs: list[dict] = field(default_factory=list)
    gameobjectref_guids: list[str] = field(default_factory=list)


def _collect_descendant_gos(prefab_obj, by_pid: dict[int, Any]) -> list[tuple[Any, dict]]:
    """BFS the GameObject hierarchy via Transform.m_Children. Returns list of
    (game_object_obj, game_object_typetree). We use a within-file path_id
    lookup since FX prefabs and their children all live in one SerializedFile."""
    queue = [prefab_obj]
    seen: set[int] = set()
    out: list[tuple[Any, dict]] = []
    while queue:
        go = queue.pop()
        if go.path_id in seen:
            continue
        seen.add(go.path_id)
        try:
            tt = go.read_typetree()
        except Exception:
            continue
        out.append((go, tt))
        for c in (tt.get("m_Component") or []):
            cp = c.get("component") if isinstance(c, dict) else None
            if not _is_pptr(cp):
                continue
            comp_obj = by_pid.get(cp["m_PathID"])
            if comp_obj is None or comp_obj.type.name != "Transform":
                continue
            try:
                t_tt = comp_obj.read_typetree()
            except Exception:
                continue
            for child_pp in (t_tt.get("m_Children") or []):
                if not _is_pptr(child_pp):
                    continue
                child_t = by_pid.get(child_pp["m_PathID"])
                if child_t is None:
                    continue
                try:
                    ct_tt = child_t.read_typetree()
                except Exception:
                    continue
                child_go_pp = ct_tt.get("m_GameObject")
                if not _is_pptr(child_go_pp):
                    continue
                child_go = by_pid.get(child_go_pp["m_PathID"])
                if child_go is not None and child_go.type.name == "GameObject":
                    queue.append(child_go)
    return out


def run_pass1(assetscenes_bundle: Path, log) -> tuple[list[PrefabAudioRefs], dict]:
    """Walk assetscenes.bundle and emit one PrefabAudioRefs per FX prefab.

    Filter: m_Name ends with .prefab AND contains /fx/ or /effects/.
    """
    import UnityPy

    log(f"pass 1: load {assetscenes_bundle.name}  ({assetscenes_bundle.stat().st_size/1024**3:.2f} GB)")
    env = UnityPy.load(str(assetscenes_bundle))

    # Index every object's path_id within its SerializedFile. Each scene is a
    # separate SerializedFile but for our component-walking purposes we only
    # ever follow within-file PPtrs (m_FileID == 0), so a per-prefab af_id
    # qualifier would be redundant. Path IDs are unique within a SerializedFile.
    log("pass 1: indexing objects + finding FX prefab GameObjects")
    fx_prefabs: list[Any] = []
    by_pid_per_af: dict[int, dict[int, Any]] = defaultdict(dict)
    total = 0
    for obj in env.objects:
        total += 1
        af_id = id(obj.assets_file)
        by_pid_per_af[af_id][obj.path_id] = obj
        if obj.type.name != "GameObject":
            continue
        try:
            tt = obj.read_typetree()
        except Exception:
            continue
        name = (tt.get("m_Name") or "").lower()
        if not name.endswith(".prefab"):
            continue
        if "/fx/" not in name and "/effects/" not in name:
            continue
        fx_prefabs.append(obj)
    log(f"pass 1: scanned {total} objects, found {len(fx_prefabs)} FX prefab GameObjects")

    log("pass 1: walking each prefab's component hierarchy")
    results: list[PrefabAudioRefs] = []
    deps_seen: set[str] = set()
    sd_count = 0
    ac_count = 0
    gor_count = 0

    for i, prefab_obj in enumerate(fx_prefabs):
        af_id = id(prefab_obj.assets_file)
        by_pid = by_pid_per_af[af_id]
        # Re-read the typetree once to get this prefab's own m_Name.
        tt0 = prefab_obj.read_typetree()
        prefab_path = (tt0.get("m_Name") or "").lower()
        rec = PrefabAudioRefs(path=prefab_path)

        for go_obj, go_tt in _collect_descendant_gos(prefab_obj, by_pid):
            for c in (go_tt.get("m_Component") or []):
                cp = c.get("component") if isinstance(c, dict) else None
                if not _is_pptr(cp):
                    continue
                comp_obj = by_pid.get(cp["m_PathID"])
                if comp_obj is None:
                    continue
                if comp_obj.type.name not in ("MonoBehaviour", "AudioSource"):
                    continue
                try:
                    tt = comp_obj.read_typetree()
                except Exception:
                    continue

                sd = tt.get("soundDefinition")
                if _is_pptr(sd) and sd.get("m_PathID"):
                    fid, pid = sd["m_FileID"], sd["m_PathID"]
                    dep = _extern_dep_name(comp_obj.assets_file, fid)
                    if dep:
                        deps_seen.add(dep)
                    rec.audio_refs.append({"kind": "SoundDefinition", "file_id": fid, "path_id": pid, "dep": dep})
                    sd_count += 1

                clip = tt.get("m_audioClip") or tt.get("m_AudioClip")
                if _is_pptr(clip) and clip.get("m_PathID"):
                    fid, pid = clip["m_FileID"], clip["m_PathID"]
                    dep = _extern_dep_name(comp_obj.assets_file, fid)
                    if dep:
                        deps_seen.add(dep)
                    rec.audio_refs.append({"kind": "AudioClip", "file_id": fid, "path_id": pid, "dep": dep})
                    ac_count += 1

                for k, v in tt.items():
                    if _is_gameobjectref(v) and v.get("guid"):
                        rec.gameobjectref_guids.append(v["guid"])
                        gor_count += 1

        results.append(rec)

    log(f"pass 1: SD refs={sd_count}  direct AudioClip refs={ac_count}  GORs={gor_count}")
    log(f"pass 1: external deps seen: {len(deps_seen)}")

    summary = {
        "total_objs_scanned": total,
        "fx_prefabs_found": len(fx_prefabs),
        "sd_refs": sd_count,
        "ac_refs": ac_count,
        "gor_count": gor_count,
        "deps_seen": sorted(deps_seen),
    }

    # Drop env so the bundle's RAM is freed before pass 2 loads content.bundle.
    del env
    gc.collect()
    return results, summary


# --- pass 2: content.bundle ---------------------------------------------------

def run_pass2(
    content_bundle: Path,
    pass1: list[PrefabAudioRefs],
    log,
) -> tuple[list[dict], dict[str, str], set[str], dict]:
    """Resolve every SoundDefinition PPtr from pass 1 to its AudioClip refs.

    Also reads GameManifest (assets/manifest.asset) to build:
      - guid -> path lookup (for resolving GameObjectRef compound signals)
      - canonical_fx_paths: the authoritative set of fx/effects prefab paths
        from GameManifest.pooledStrings. Pass 1 enumerates GameObjects from
        assetscenes by m_Name pattern and picks up false positives (mesh LODs
        with "effects" in the path, automated-test prefabs, etc); we filter
        pass 1's output against this canonical set before returning.

    Returns:
        per-prefab clip-ref list (dicts with path / clip_refs / gor_guids)
        guid_to_path lookup
        canonical_fx_paths set
        summary
    """
    import UnityPy

    needed_sd: set[tuple[str, int]] = set()
    direct_clip_refs_by_prefab: dict[str, list[dict]] = defaultdict(list)
    for pf in pass1:
        for ref in pf.audio_refs:
            if ref["kind"] == "SoundDefinition":
                needed_sd.add((_cab_basename(ref["dep"]), ref["path_id"]))
            elif ref["kind"] == "AudioClip":
                direct_clip_refs_by_prefab[pf.path].append({
                    "file_id": ref["file_id"], "path_id": ref["path_id"], "dep": ref["dep"],
                })

    log(f"pass 2: load {content_bundle.name}  ({content_bundle.stat().st_size/1024**3:.2f} GB)")
    env = UnityPy.load(str(content_bundle))

    log("pass 2: indexing content.bundle by (cab, path_id)")
    by_cab_pid: dict[tuple[str, int], Any] = {}
    manifest_obj = None
    for obj in env.objects:
        cab = getattr(obj.assets_file, "name", "") or ""
        by_cab_pid[(cab, obj.path_id)] = obj
        if obj.type.name == "MonoBehaviour" and (obj.container or "").lower() == "assets/manifest.asset":
            manifest_obj = obj

    if manifest_obj is None:
        log("pass 2: WARNING - GameManifest not found at assets/manifest.asset; "
            "compound classification will be limited")

    # Build guid -> path map AND canonical FX set from GameManifest.
    guid_to_path: dict[str, str] = {}
    canonical_fx: set[str] = set()
    if manifest_obj is not None:
        try:
            mtt = manifest_obj.read_typetree()
            for entry in (mtt.get("prefabProperties") or []):
                if isinstance(entry, dict):
                    g, n = entry.get("guid"), entry.get("name")
                    if g and n:
                        guid_to_path[g.lower()] = n.lower()
            for entry in (mtt.get("guidPaths") or []):
                if isinstance(entry, dict):
                    g, n = entry.get("guid"), entry.get("name")
                    if g and n:
                        guid_to_path.setdefault(g.lower(), n.lower())
            for ps in (mtt.get("pooledStrings") or []):
                s = ps.get("str", "") if isinstance(ps, dict) else ""
                sl = s.lower()
                if sl.endswith(".prefab") and ("/fx/" in sl or "/effects/" in sl):
                    canonical_fx.add(sl)
            log(f"pass 2: GameManifest guid→path entries: {len(guid_to_path)}; "
                f"canonical FX prefabs: {len(canonical_fx)}")
        except Exception as exc:
            log(f"pass 2: failed to read GameManifest: {exc}")

    # Resolve SoundDefinitions.
    log(f"pass 2: resolving {len(needed_sd)} SoundDefinitions")
    sd_to_clips: dict[tuple[str, int], list[dict]] = {}
    sd_resolved = 0
    sd_missing = 0
    audio_deps_seen: set[str] = set()

    for (cab, pid) in needed_sd:
        obj = by_cab_pid.get((cab, pid))
        if obj is None:
            sd_missing += 1
            continue
        try:
            tt = obj.read_typetree()
        except Exception:
            sd_missing += 1
            continue
        af = obj.assets_file
        externals = getattr(af, "externals", None) or []

        def dep_for_fid(fid: int) -> str | None:
            if fid <= 0 or fid - 1 >= len(externals):
                return None
            ext = externals[fid - 1]
            return getattr(ext, "path", None) or getattr(ext, "pathName", None)

        clip_refs: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for wac in (tt.get("weightedAudioClips") or []):
            if not isinstance(wac, dict):
                continue
            ac = wac.get("audioClip")
            if _is_pptr(ac) and ac.get("m_PathID"):
                fid, p = ac["m_FileID"], ac["m_PathID"]
                dep = dep_for_fid(fid) or ""
                key = (_cab_basename(dep), p)
                if key in seen:
                    continue
                seen.add(key)
                if dep:
                    audio_deps_seen.add(dep)
                clip_refs.append({"file_id": fid, "path_id": p, "dep": dep})
        for dacl in (tt.get("distanceAudioClips") or []):
            if not isinstance(dacl, dict):
                continue
            for wac in (dacl.get("audioClips") or []):
                if not isinstance(wac, dict):
                    continue
                ac = wac.get("audioClip")
                if _is_pptr(ac) and ac.get("m_PathID"):
                    fid, p = ac["m_FileID"], ac["m_PathID"]
                    dep = dep_for_fid(fid) or ""
                    key = (_cab_basename(dep), p)
                    if key in seen:
                        continue
                    seen.add(key)
                    if dep:
                        audio_deps_seen.add(dep)
                    clip_refs.append({"file_id": fid, "path_id": p, "dep": dep})
        sd_to_clips[(cab, pid)] = clip_refs
        sd_resolved += 1

    log(f"pass 2: resolved {sd_resolved} SoundDefinitions, {sd_missing} not found")

    # Emit per-prefab clip-ref lists, filtering to the canonical FX set so
    # we drop pass 1 false positives (mesh LODs etc.). If GameManifest didn't
    # load, fall back to including everything pass 1 found.
    out: list[dict] = []
    filtered_out_count = 0
    for pf in pass1:
        if canonical_fx and pf.path not in canonical_fx:
            filtered_out_count += 1
            continue
        clip_refs: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for ref in pf.audio_refs:
            if ref["kind"] == "SoundDefinition":
                cab, pid = _cab_basename(ref["dep"]), ref["path_id"]
                for cr in sd_to_clips.get((cab, pid), []):
                    key = (_cab_basename(cr["dep"]), cr["path_id"])
                    if key not in seen:
                        seen.add(key)
                        clip_refs.append(cr)
            elif ref["kind"] == "AudioClip":
                key = (_cab_basename(ref["dep"]), ref["path_id"])
                if key not in seen:
                    seen.add(key)
                    clip_refs.append(ref)
        out.append({
            "path": pf.path,
            "clip_refs": clip_refs,
            "gameobjectref_guids": pf.gameobjectref_guids,
        })
    log(f"pass 2: filtered out {filtered_out_count} pass-1 prefabs not in "
        f"GameManifest pooledStrings (false-positive mesh LODs etc.)")

    summary = {
        "sd_resolved": sd_resolved,
        "sd_missing": sd_missing,
        "guid_to_path_entries": len(guid_to_path),
        "canonical_fx_count": len(canonical_fx),
        "filtered_out": filtered_out_count,
        "audio_deps_seen": sorted(audio_deps_seen),
    }

    del env
    gc.collect()
    return out, guid_to_path, canonical_fx, summary


# --- pass 3: audio.bundle -----------------------------------------------------

def run_pass3(
    audio_bundle: Path,
    pass2: list[dict],
    audio_dir: Path,
    ffmpeg_bin: str,
    log,
) -> tuple[dict[str, dict], dict[str, str], dict]:
    """Decode every AudioClip referenced by pass 2 and write MP3s.

    Returns:
        clips_meta:  clip_id -> {path, name, container, duration_ms, ...}
        ref_to_clip_id:  "cab|path_id" -> clip_id  (so pass 4 can join)
        summary
    """
    import UnityPy

    needed: set[tuple[str, int]] = set()
    for pf in pass2:
        for c in pf["clip_refs"]:
            needed.add((_cab_basename(c["dep"]), c["path_id"]))

    log(f"pass 3: load {audio_bundle.name}  ({audio_bundle.stat().st_size/1024**3:.2f} GB)")
    env = UnityPy.load(str(audio_bundle))

    log("pass 3: indexing AudioClips")
    by_cab_pid: dict[tuple[str, int], Any] = {}
    for obj in env.objects:
        if obj.type.name != "AudioClip":
            continue
        cab = getattr(obj.assets_file, "name", "") or ""
        by_cab_pid[(cab, obj.path_id)] = obj
    log(f"pass 3: {len(by_cab_pid)} AudioClips indexed; need {len(needed)}")

    audio_dir.mkdir(parents=True, exist_ok=True)
    clips_meta: dict[str, dict] = {}
    ref_to_clip_id: dict[str, str] = {}
    written = 0
    decode_failed = 0
    not_found = 0

    start = time.monotonic()
    for i, (cab, pid) in enumerate(sorted(needed)):
        obj = by_cab_pid.get((cab, pid))
        if obj is None:
            not_found += 1
            continue
        container = (obj.container or "").lower()
        if container:
            clip_id = hashlib.sha1(container.encode("utf-8")).hexdigest()[:12]
        else:
            clip_id = hashlib.sha1(f"{cab}|{pid}".encode("utf-8")).hexdigest()[:12]

        ref_to_clip_id[f"{cab}|{pid}"] = clip_id

        if clip_id in clips_meta:
            continue

        out_path = audio_dir / f"{clip_id}.mp3"
        try:
            clip = obj.read()
            wav_bytes, duration_ms = decode_clip_to_wav(clip)
        except Exception:
            decode_failed += 1
            continue
        try:
            transcode_wav_to_mp3(wav_bytes, out_path, ffmpeg_bin=ffmpeg_bin)
            written += 1
        except Exception:
            decode_failed += 1
            continue

        clips_meta[clip_id] = {
            "path": f"audio/{clip_id}.mp3",
            "name": getattr(clip, "name", "") or container.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            "container": container,
            "duration_ms": duration_ms,
        }

        if (i + 1) % 200 == 0:
            elapsed = time.monotonic() - start
            rate = (i + 1) / max(elapsed, 0.001)
            log(f"pass 3:   {i+1}/{len(needed)} clips  written={written}  "
                f"failed={decode_failed}  rate={rate:.1f}/s")

    log(f"pass 3: written={written}  decode_failed={decode_failed}  not_found={not_found}")

    summary = {
        "needed": len(needed),
        "written": written,
        "decode_failed": decode_failed,
        "not_found": not_found,
    }

    del env
    gc.collect()
    return clips_meta, ref_to_clip_id, summary


# --- pass 4: assemble + classify compound -------------------------------------

def run_pass4_assemble(
    pass1: list[PrefabAudioRefs],
    pass2: list[dict],
    clips_meta: dict[str, dict],
    ref_to_clip_id: dict[str, str],
    guid_to_path: dict[str, str],
    canonical_fx: set[str],
    out_path: Path,
    audio_dir: Path,
    log,
) -> dict:
    """Assemble final data/sounds.json and classify compound prefabs using the
    GameManifest guid→path lookup.

    Compound classification:
      - resolve every gameobjectref_guids entry to a path via guid_to_path
      - keep only resolved targets that are themselves fx/effects .prefab
        (filtering self-references and audio-template GameObjectRefs)
      - 0 fx-targets and >0 audio refs -> simple (kept)
      - 0 fx-targets and 0 audio refs -> silent (excluded)
      - 1 fx-target and 0 audio refs -> thin_wrapper (excluded)
      - >=1 fx-target and >=1 audio refs OR >1 fx-targets -> true_compound (excluded)

    Recoverability: a thin wrapper is "recoverable" if its child prefab is
    itself in our final kept set.
    """
    p1_by_path = {pf.path: pf for pf in pass1}
    p2_by_path = {pf["path"]: pf for pf in pass2}

    # The canonical FX-prefab set comes from GameManifest pooledStrings via
    # pass 2. Pass 1 may have included false positives (mesh LODs etc.) which
    # pass 2 already filtered out, so this set is what we use to recognise
    # "another FX prefab" in GOR targets.
    all_fx_paths: set[str] = canonical_fx if canonical_fx else set(p1_by_path.keys())

    clips_out: dict[str, dict] = {}
    prefabs_out: list[dict] = []
    fallback_category_count = 0
    top_categories: Counter = Counter()

    simple = 0
    silent = 0
    thin_wrapper = 0
    true_compound = 0
    thin_wrapper_recoverable = 0
    thin_wrapper_lost = 0

    skipped_paths: list[str] = []

    # Need final kept set to compute thin-wrapper recoverability. So we do
    # two passes through prefabs: first classify everything, then determine
    # recoverability against the final kept set.

    classifications: list[tuple[str, str, list[str]]] = []  # (prefab_path, status, fx_targets)

    for path, pf in p2_by_path.items():
        gor_guids = pf.get("gameobjectref_guids", [])
        # Resolve guids to paths, keep only those pointing at *another*
        # known FX prefab. GORs to non-FX assets (audio templates, etc.) and
        # self-references are dropped.
        fx_targets: list[str] = []
        for g in gor_guids:
            target = guid_to_path.get(g.lower())
            if not target or target == path:
                continue
            if target not in all_fx_paths:
                continue
            fx_targets.append(target)
        # Dedupe while preserving order.
        seen: set[str] = set()
        fx_targets = [t for t in fx_targets if not (t in seen or seen.add(t))]

        has_audio = bool(pf["clip_refs"])
        n_targets = len(fx_targets)

        if n_targets == 0 and has_audio:
            status = "simple"
        elif n_targets == 0 and not has_audio:
            status = "silent"
        elif n_targets == 1 and not has_audio:
            status = "thin_wrapper"
        else:
            status = "true_compound"

        classifications.append((path, status, fx_targets))

    # Determine kept set for recoverability check.
    kept_paths: set[str] = {p for p, s, _ in classifications if s == "simple"}

    for path, status, fx_targets in classifications:
        if status == "simple":
            pf = p2_by_path[path]
            clip_ids: list[str] = []
            for cr in pf["clip_refs"]:
                key = f"{_cab_basename(cr['dep'])}|{cr['path_id']}"
                cid = ref_to_clip_id.get(key)
                if cid is None:
                    continue
                meta = clips_meta.get(cid)
                if meta is None:
                    continue
                if cid not in clips_out:
                    clips_out[cid] = {
                        "path": meta["path"],
                        "duration": round(meta["duration_ms"] / 1000.0, 3),
                    }
                clip_ids.append(cid)
            if not clip_ids:
                # Should be rare — clip files exist for every (cab, pid) we
                # decoded, and "simple" requires has_audio. Treat as silent.
                silent += 1
                continue

            cats, used_fallback = categories_for(path)
            if used_fallback:
                fallback_category_count += 1
            if cats:
                top_categories[cats[0]] += 1
            simple += 1
            prefabs_out.append({
                "path": path,
                "name": _name_for(path),
                "categories": cats,
                "clips": clip_ids,
                "confidence": "reference",
            })
        elif status == "silent":
            silent += 1
            skipped_paths.append(path)
        elif status == "thin_wrapper":
            thin_wrapper += 1
            child = fx_targets[0]
            if child in kept_paths:
                thin_wrapper_recoverable += 1
            else:
                thin_wrapper_lost += 1
            skipped_paths.append(path)
        else:  # true_compound
            true_compound += 1
            skipped_paths.append(path)

    # Validate every clip path exists on disk
    missing = [c["path"] for c in clips_out.values()
               if not (audio_dir.parent / c["path"]).is_file()
               or (audio_dir.parent / c["path"]).stat().st_size == 0]
    if missing:
        log(f"pass 4: WARNING {len(missing)} clip files missing on disk; "
            f"first few: {missing[:5]}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"clips": clips_out, "prefabs": prefabs_out},
                                   indent=2, sort_keys=False))
    log(f"pass 4: wrote {out_path}  ({out_path.stat().st_size/1024:.1f} KB)")

    # Prune orphan MP3s that no kept prefab references.
    referenced = {audio_dir.parent / c["path"] for c in clips_out.values()}
    removed = 0
    removed_size = 0
    for f in audio_dir.iterdir():
        if not f.is_file() or f.suffix != ".mp3":
            continue
        if f not in referenced:
            removed_size += f.stat().st_size
            f.unlink()
            removed += 1
    log(f"pass 4: pruned {removed} orphan MP3 files ({removed_size/1024**2:.1f} MB)")

    return {
        "fx_total": len(p1_by_path),
        "simple": simple,
        "silent": silent,
        "thin_wrapper": thin_wrapper,
        "thin_wrapper_recoverable": thin_wrapper_recoverable,
        "thin_wrapper_lost": thin_wrapper_lost,
        "true_compound": true_compound,
        "fallback_category_count": fallback_category_count,
        "top_categories": top_categories.most_common(),
        "clips_unique": len(clips_out),
        "orphans_pruned": removed,
        "skipped_paths": skipped_paths,
    }


# --- orchestrator -------------------------------------------------------------

def run(rust_dir: Path, out_dir: Path, ffmpeg_bin: str, log=None) -> dict:
    """End-to-end: load each bundle, run all four passes, write data/sounds.json
    and audio/*.mp3. Returns the final summary dict."""
    bundles = rust_dir / "Bundles" / "shared"
    if not bundles.is_dir():
        raise FileNotFoundError(f"expected {bundles} to exist")

    if log is None:
        def log(m: str) -> None:
            print(m, flush=True)

    audio_dir = out_dir / "audio"
    sounds_json = out_dir / "data" / "sounds.json"

    pass1, p1_summary = run_pass1(bundles / "assetscenes.bundle", log)
    pass2, guid_to_path, canonical_fx, p2_summary = run_pass2(
        bundles / "content.bundle", pass1, log)
    clips_meta, ref_to_clip_id, p3_summary = run_pass3(
        bundles / "audio.bundle", pass2, audio_dir, ffmpeg_bin, log)
    p4_summary = run_pass4_assemble(
        pass1, pass2, clips_meta, ref_to_clip_id, guid_to_path,
        canonical_fx, sounds_json, audio_dir, log)

    return {
        "pass1": p1_summary,
        "pass2": p2_summary,
        "pass3": p3_summary,
        "pass4": p4_summary,
    }


def print_summary(summary: dict, log=None) -> None:
    if log is None:
        def log(m: str) -> None:
            print(m, flush=True)
    p4 = summary["pass4"]
    log("")
    log("=" * 60)
    log("EXTRACTION SUMMARY")
    log("=" * 60)
    log(f"  FX prefabs found:                     {p4['fx_total']}")
    log(f"  simple prefabs (kept, with audio):    {p4['simple']}")
    log(f"  silent prefabs (excluded):            {p4['silent']}")
    log(f"  thin-wrapper prefabs (excluded):      {p4['thin_wrapper']}")
    log(f"    of which recoverable via child:     {p4['thin_wrapper_recoverable']}")
    log(f"    of which lost from dataset:         {p4['thin_wrapper_lost']}")
    log(f"  true-compound prefabs (excluded):     {p4['true_compound']}")
    log(f"  total unique clips:                   {p4['clips_unique']}")
    log(f"  orphan MP3s pruned:                   {p4['orphans_pruned']}")
    log(f"  prefabs using fallback category:      {p4['fallback_category_count']}")
    log("")
    log("  top-level category counts:")
    for cat, n in p4["top_categories"][:20]:
        log(f"    {cat:25s} {n}")
    log("=" * 60)
