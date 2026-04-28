"""File placement / matcher (port of `internal/matcher/*.go`)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from . import fsutils
from .config import load_config
from .downloads import mark_placed, save_torrent_download
from .logger import log
from .parsers import (
    extract_chapter_range_from_title,
    extract_season_number,
    normalize_dash,
    parse_range,
    rough_extract_chapter_from_title,
)
from .types import MetadataIndex, SeasonIndex, TorrentDownload


VIDEO_EXTS = (".mkv", ".mp4")


def match_and_place_video(
    video_path: str | Path,
    default_dir: str | Path,
    index: MetadataIndex,
    ogcr: str,
) -> str:
    """Move the video file to its metadata-matching destination.

    Returns a human-readable status message (or empty string if the source
    file no longer exists).
    """
    src = Path(video_path)
    default = Path(default_dir)
    if not src.exists():
        return ""

    file_name = src.name
    log(False, "Placing filename : %s", file_name)
    dst_no_suffix = _find_metadata_match(file_name, index, ogcr)
    log(False, "dstPath for fileName %s will be %s", file_name, dst_no_suffix)

    ext = src.suffix
    final_path = Path(str(dst_no_suffix) + ext)

    try:
        fsutils.safe_move_file(src, final_path)
    except OSError as e:
        log(False, "sfm Error: %s, moving to strayvideos", e)
        stray_dir = default / "strayvideos"
        try:
            fsutils.create_directory(stray_dir)
        except OSError as ce:
            log(True, "Failed to create strayvideos directory: %s", ce)
            raise

        name_no_ext = src.stem
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        stray_name = f"{name_no_ext}_{timestamp}{ext}"
        stray_path = stray_dir / stray_name
        try:
            fsutils.safe_move_file(src, stray_path)
        except OSError as me:
            log(True, "Failed to move to strayvideos: %s", me)
            raise

        out_file = _pad_right(file_name, 26, "..")
        out_rel = _pad_right(f"strayvideos/{stray_name}", 36, "..")
        return f"⚠️  Placed in stray: {out_file} → {out_rel}"

    rel_path = os.path.relpath(final_path, default)
    log(False, "placed: %s → %s", file_name, rel_path)
    file_no_prefix = file_name[10:] if len(file_name) > 10 else file_name
    rel_base = os.path.basename(rel_path)
    rel_no_prefix = rel_base[10:] if len(rel_base) > 10 else rel_base
    out_file = _pad_right(file_no_prefix, 26, "..")
    out_rel = _pad_right(".." + rel_no_prefix, 36, "..")
    return f"🎞️  Placed: {out_file} → {out_rel}"


def _find_metadata_match(file_name: str, index: MetadataIndex, ogcr: str) -> Path:
    cfg = load_config()
    base_dir = Path(cfg.target_dir)
    # ``match_and_place_video`` re-appends the source extension to whatever
    # this function returns, so the stray path must be extension-less to
    # avoid producing names like ``foo.mkv.mkv``.
    stray = base_dir / "strayvideos" / ogcr / Path(file_name).stem

    season_folder, season_index = _find_season_for_chapter(ogcr, index)
    if not season_folder:
        log(False, "findMetaDataMatch: failed to find Season-folder")
        return stray
    log(False, "season found for: %s for range %s", season_folder, ogcr)

    new_name = _find_title_for_chapter(ogcr, season_index)
    if not new_name:
        chapter_range = extract_chapter_range_from_title(file_name)
        if not chapter_range:
            season_z = extract_season_number(season_folder)
            season_num = season_z.zfill(2)
            chapter_num, is_range = rough_extract_chapter_from_title(file_name)
            log(False, "findMetaDataMatch - rough extracted chapterNum: %s", chapter_num)
            if is_range:
                new_name = _find_title_for_chapter(chapter_num, season_index)
            else:
                ep_key = f"S{season_num}E{chapter_num}"
                new_name = _find_title_rough(ep_key, season_index)
        else:
            new_name = _find_title_for_chapter(chapter_range, season_index)
    else:
        log(False, "Title match found: ChapterKey: %s - EpisodeTitle: %s", ogcr, file_name)

    season_dir = base_dir / season_folder
    if not new_name:
        log(False, "Could not determine episode title, sending to stray")
        return stray
    return season_dir / new_name


def _find_title_for_chapter(chapter_key: str, season: SeasonIndex) -> str:
    norm = normalize_dash(chapter_key)
    for ep_range, ep in season.episode_range.items():
        if normalize_dash(ep_range) == norm:
            return ep.title
    return ""


def _find_season_for_chapter(
    chapter_key: str, index: MetadataIndex
) -> tuple[str, SeasonIndex]:
    ch_start, ch_end = parse_range(chapter_key)
    norm = normalize_dash(chapter_key)

    # Prefer a season that explicitly indexes this chapter range. When two
    # seasons' ranges overlap (e.g. Gaimon's "42-42" sits inside Baratie's
    # "42-68"), this avoids returning the wrong season and dropping the file
    # into ``strayvideos`` because the title cannot be found.
    range_match: tuple[str, SeasonIndex] | None = None
    for name, season in index.seasons.items():
        for ep_range in season.episode_range:
            if normalize_dash(ep_range) == norm:
                return name, season
        s_start, s_end = parse_range(season.range)
        if (
            range_match is None
            and ch_start >= s_start
            and ch_end <= s_end
            and (s_start, s_end) != (-1, -1)
        ):
            range_match = (name, season)

    if range_match is not None:
        return range_match
    return "", SeasonIndex()


def _find_title_rough(ep_key: str, season: SeasonIndex) -> str:
    for ep in season.episode_range.values():
        if ep_key in ep.title:
            log(False, "roughFindTitle match found: %s > %s", ep_key, ep.title)
            return ep.title
    log(False, "roughFindTitle did not find a match. for %s", ep_key)
    return ""


def _pad_right(text: str, width: int, tail: str = "") -> str:
    if len(text) > width:
        if tail and width > len(tail):
            return text[: width - len(tail)] + tail
        return text[:width]
    return text + " " * (width - len(text))


# ---------------------------------------------------------------------------
# Process all video files in a torrent's tmp directory
# ---------------------------------------------------------------------------


def process_torrent_files(
    tmp_dir: str | Path,
    out_dir: str | Path,
    td: TorrentDownload,
    index: MetadataIndex,
) -> None:
    tmp = Path(tmp_dir)
    files_checked = 0
    files_placed = 0
    last_error: Optional[Exception] = None

    td.placement_progress = f"🔧 Finding files to place {tmp}"

    vid_paths: list[Path] = []
    for p in tmp.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            log(False, "added path: %s", p)
            vid_paths.append(p)

    if not vid_paths:
        mark_placed(td, "⚠️ No video files found to place!")
        return

    for path in vid_paths:
        log(False, "→ Found: %s", path)
        files_checked += 1
        file_name = path.name
        readable = file_name[10:] if len(file_name) > 10 else file_name
        td.placement_progress = (
            f"🔧 Placing ➝ {files_placed + 1}/{len(vid_paths)} - {readable}"
        )
        try:
            msg = match_and_place_video(path, out_dir, index, td.chapter_range)
        except OSError as e:
            log(True, "Error placing file: %s", e)
            last_error = e
            continue
        if msg:
            files_placed += 1
            td.placement_full.append(msg)
            save_torrent_download(td)

    if files_placed == 0 and last_error is not None:
        placed_msg = f"❌ Failed to place any files! Last error: {last_error}"
    elif files_placed == 0:
        placed_msg = "❌ No files could be placed!"
    elif files_placed == len(vid_paths):
        placed_msg = (
            "✅ 1 file placed!"
            if files_placed == 1
            else f"✅ All {files_placed} files placed!"
        )
    else:
        placed_msg = f"⚠️ {files_placed}/{len(vid_paths)} files placed!"

    mark_placed(td, placed_msg)
    log(
        False,
        "File placement done: %d checked, %d placed",
        files_checked,
        files_placed,
    )
