"""Metadata index, cache and git sync.

Port of `internal/metadata/*.go`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from . import fsutils
from .config import load_config, save_config
from .logger import log
from .parsers import (
    extract_chapter_range_from_nfo,
    extract_xml_tag,
    is_episode_nfo,
    normalize_dash,
    parse_range,
)
from .types import (
    Config,
    EpisodeData,
    MetadataIndex,
    ScraperConfig,
    ScraperFields,
    SeasonIndex,
    ValidationConfig,
)


_metadata_cache: Optional[MetadataIndex] = None
_cache_lock = threading.Lock()
_cache_loaded = False


def _index_to_dict(idx: MetadataIndex) -> dict:
    return {
        "seasons": {
            sk: {
                "range": s.range,
                "name": s.name,
                "episodes": {ek: asdict(ed) for ek, ed in s.episode_range.items()},
            }
            for sk, s in idx.seasons.items()
        }
    }


def _index_from_dict(data: dict) -> MetadataIndex:
    idx = MetadataIndex()
    for sk, s in (data.get("seasons") or {}).items():
        eps = {
            ek: EpisodeData(title=(ed or {}).get("title", ""))
            for ek, ed in (s.get("episodes") or {}).items()
        }
        idx.seasons[sk] = SeasonIndex(
            range=s.get("range", ""), name=s.get("name", ""), episode_range=eps
        )
    return idx


def load_metadata_cache() -> MetadataIndex:
    """Load `metadata-index.json` once. Returns an empty index on failure."""
    global _metadata_cache, _cache_loaded
    with _cache_lock:
        if _cache_loaded and _metadata_cache is not None:
            return _metadata_cache
        cfg = load_config()
        path = Path(cfg.target_dir) / "metadata-index.json" if cfg.target_dir else None
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                _metadata_cache = _index_from_dict(data)
            except (OSError, json.JSONDecodeError):
                _metadata_cache = MetadataIndex()
        else:
            _metadata_cache = MetadataIndex()
        _cache_loaded = True
    return _metadata_cache


def _save_metadata_index(index: MetadataIndex, base_dir: Path) -> None:
    global _metadata_cache, _cache_loaded
    path = base_dir / "metadata-index.json"
    path.write_text(
        json.dumps(_index_to_dict(index), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with _cache_lock:
        _metadata_cache = index
        _cache_loaded = True


def build_metadata_index(base_dir: str | Path) -> None:
    base = Path(base_dir)
    index = _build_index_from_dir(base)
    _save_metadata_index(index, base)


def _build_index_from_dir(base_dir: Path) -> MetadataIndex:
    index = MetadataIndex()
    for path in base_dir.rglob("*"):
        if not path.is_file() or not is_episode_nfo(path.name):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        season = extract_xml_tag(data, "season")
        episode = extract_xml_tag(data, "episode")
        chapter_range = extract_chapter_range_from_nfo(
            data.decode("utf-8", errors="replace")
        )
        if not season or not episode or not chapter_range:
            log(
                False,
                "indexbuilder: missed param for %s - season: %s - episode %s - chapterRange %s",
                path.name,
                season,
                episode,
                chapter_range,
            )
            continue
        season_key = f"Season {season}" if season not in ("00", "0") else "Specials"
        normalized = normalize_dash(chapter_range)
        ep_title = path.stem
        sidx = index.seasons.setdefault(season_key, SeasonIndex())
        sidx.episode_range[normalized] = EpisodeData(title=ep_title)

    _calculate_season_ranges(index)
    _name_seasons(index, base_dir)
    return index


def _calculate_season_ranges(index: MetadataIndex) -> None:
    for skey, sidx in index.seasons.items():
        if skey == "Specials":
            sidx.range = "00-00"
            continue
        cur_min, cur_max = 99999, -1
        for cr in sidx.episode_range:
            start, end = parse_range(cr)
            if start < cur_min:
                cur_min = start
            if end > cur_max:
                cur_max = end
        sidx.range = f"{cur_min}-{cur_max}"


_NAMED_SEASON_RE = re.compile(
    r'<namedseason\s+number="(\d+)">([^<]+)</namedseason>'
)


def _name_seasons(index: MetadataIndex, base_dir: Path) -> None:
    tvshow = base_dir / "tvshow.nfo"
    try:
        data = tvshow.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log(False, "indexbuilder: Could not read tvshow.nfo: %s", e)
        return
    season_names: dict[str, str] = {}
    for m in _NAMED_SEASON_RE.finditer(data):
        snum = int(m.group(1))
        name = m.group(2).strip()
        # Strip "N. " prefix if present
        idx_dot = name.find(". ")
        if idx_dot != -1:
            name = name[idx_dot + 2 :]
        season_names[f"Season {snum}"] = name
    for season_key, sidx in index.seasons.items():
        # The Go code maps "Season 02" folder name to "Season 2" key. We must
        # try both formats so the assignment is robust.
        if season_key in season_names:
            sidx.name = season_names[season_key]
        else:
            # match "Season 02" -> "Season 2"
            parts = season_key.split()
            if len(parts) == 2 and parts[1].lstrip("0").isdigit():
                alt = f"Season {int(parts[1])}"
                if alt in season_names:
                    sidx.name = season_names[alt]


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def have_metadata(chapter_range: str) -> bool:
    if not chapter_range:
        return False
    idx = load_metadata_cache()
    norm = normalize_dash(chapter_range)
    for season in idx.seasons.values():
        if normalize_dash(season.range) == norm:
            return True
        for ep_range in season.episode_range:
            if normalize_dash(ep_range) == norm:
                return True
    return False


def have_video_status(chapter_range: str) -> int:
    """0 = none, 1 = some, 2 = all."""
    if not chapter_range:
        return 0
    idx = load_metadata_cache()
    cfg = load_config()
    if not cfg.target_dir:
        return 0
    base_dir = Path(cfg.target_dir)
    for season_key, season in idx.seasons.items():
        season_dir = base_dir / season_key
        if season.range == chapter_range:
            v, n = count_videos_and_total(season_dir)
            log(
                False,
                "HaveVideoStatus: counted %d videos and %d nfos for seasonKey: %s",
                v,
                n,
                season_key,
            )
            if v == 0:
                return 0
            if v < n:
                return 1
            return 2
        for ep_range, ep_data in season.episode_range.items():
            if ep_range == chapter_range:
                if (
                    fsutils.file_exists(season_dir / f"{ep_data.title}.mp4")
                    or fsutils.file_exists(season_dir / f"{ep_data.title}.mkv")
                ):
                    return 2
    return 0


def count_videos_and_total(directory: str | Path) -> tuple[int, int]:
    """Count (matched_videos, total_episode_nfos) under ``directory``.

    Matched = video file (.mkv/.mp4) whose stem matches an episode .nfo.
    """
    d = Path(directory)
    if not d.exists():
        return 0, 0
    video_files: dict[str, bool] = {}
    total_nfo = 0
    try:
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            lower = p.name.lower()
            base = p.stem
            if lower.endswith(".mkv") or lower.endswith(".mp4"):
                video_files[base] = video_files.get(base, False)
            if is_episode_nfo(lower):
                total_nfo += 1
                if base in video_files:
                    video_files[base] = True
    except OSError:
        return 0, 0
    matched = sum(1 for v in video_files.values() if v)
    return matched, total_nfo


# ---------------------------------------------------------------------------
# git clone / sync
# ---------------------------------------------------------------------------


def fetch_all_metadata(base_dir: str | Path, cfg: Config) -> None:
    _clone_and_copy_repo(Path(base_dir), cfg, sync_only=False)


def sync_metadata(base_dir: str | Path, cfg: Config) -> None:
    _clone_and_copy_repo(Path(base_dir), cfg, sync_only=True)


def _clone_and_copy_repo(base_dir: Path, cfg: Config, sync_only: bool) -> None:
    tmp_dir = Path(tempfile.gettempdir()) / "repo-tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    repo_url = f"https://github.com/{cfg.github_repo}.git"
    print(f"🌐 Fetching metadata from {repo_url}")
    try:
        subprocess.run(
            ["git", "clone", "--depth=1", repo_url, str(tmp_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {e}") from e

    try:
        src_dir = tmp_dir / "One Pace"
        if not src_dir.exists():
            raise RuntimeError(
                f"unexpected metadata layout: '{src_dir}' missing in repo"
            )
        base_dir.mkdir(parents=True, exist_ok=True)
        if sync_only:
            fsutils.sync_dir(src_dir, base_dir)
        else:
            fsutils.copy_dir(src_dir, base_dir)
        build_metadata_index(base_dir)
        _update_source_config(tmp_dir, cfg)
        print(f"\n✅ Saved metadata index to {base_dir / 'metadata-index.json'}")
        print("✅ Metadata fetch and indexing complete.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _update_source_config(tmp_dir: Path, cfg: Config) -> None:
    cfg_file = tmp_dir / "config.json"
    if not cfg_file.exists():
        return
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log(False, "Error updating source config: %s", e)
        return
    fields = data.get("fields") or {}
    val = data.get("validation") or {}
    cfg.source = ScraperConfig(
        name=data.get("name", ""),
        base_url=data.get("base_url", ""),
        search_path_template=data.get("search_path_template", ""),
        search_query=data.get("search_query", ""),
        row_selector=data.get("row_selector", ""),
        fields=ScraperFields(
            title=fields.get("title", ""),
            seeders=fields.get("seeders", ""),
            torrent_link=fields.get("torrent_link", ""),
            torrent_id=fields.get("torrent_id", ""),
            upload_date=fields.get("upload_date", ""),
        ),
        validation=ValidationConfig(
            required_in_title=val.get("required_in_title", "")
        ),
    )
    save_config(cfg)
