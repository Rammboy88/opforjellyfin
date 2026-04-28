"""Tests for the file placement / matcher module."""

from __future__ import annotations

from pathlib import Path

import pytest

from opfor import matcher
from opfor.types import Config, EpisodeData, MetadataIndex, SeasonIndex


def _build_index() -> MetadataIndex:
    """A miniature index that mirrors the real ``one-pace-jellyfin`` repo
    around chapter 42, where Gaimon's "42-42" sits inside Baratie's "42-68"."""
    idx = MetadataIndex()
    idx.seasons["Specials"] = SeasonIndex(
        range="00-00",
        episode_range={
            "42-42": EpisodeData(title="One Pace - S00E07 - Gaimon 01"),
        },
    )
    # Baratie season is intentionally inserted *before* Gaimon so that the
    # plain range-overlap iteration in the old ``_find_season_for_chapter``
    # would return it first.
    idx.seasons["Season 5"] = SeasonIndex(
        range="42-68",
        episode_range={
            "42-44": EpisodeData(title="One Pace - S05E01 - Enter Sanji"),
            "45-47": EpisodeData(title="One Pace - S05E02 - Don Krieg"),
            "48-52": EpisodeData(title="One Pace - S05E03 - The Oath"),
        },
    )
    idx.seasons["Season 4"] = SeasonIndex(
        range="42-42",
        episode_range={
            "42-42": EpisodeData(title="One Pace - S04E01 - You're the Rare Breed"),
        },
    )
    return idx


def test_find_season_prefers_exact_episode_range_over_overlap() -> None:
    idx = _build_index()
    name, season = matcher._find_season_for_chapter("42-42", idx)
    # Specials and Season 4 both contain the exact key. Specials comes first
    # in iteration order, so it wins; either is acceptable as long as we do
    # not fall back to Season 5 (Baratie) which only overlaps by range.
    assert name in {"Specials", "Season 4"}
    assert "42-42" in season.episode_range


def test_find_season_falls_back_to_range_when_no_exact_key() -> None:
    idx = _build_index()
    # 46-46 is not an exact key but falls inside Baratie's 42-68 range.
    name, _ = matcher._find_season_for_chapter("46-46", idx)
    assert name == "Season 5"


def test_find_season_returns_empty_when_nothing_matches() -> None:
    idx = _build_index()
    name, season = matcher._find_season_for_chapter("999-999", idx)
    assert name == ""
    assert season.episode_range == {}


def test_match_and_place_routes_gaimon_to_correct_season(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(matcher, "load_config", lambda: Config(target_dir=str(target)))

    src = tmp_path / "[One Pace][42,22] Gaimon 01 [1080p][9269F40F].mkv"
    src.write_bytes(b"data")

    idx = _build_index()
    msg = matcher.match_and_place_video(src, target, idx, "42-42")

    # The file must end up inside a real season folder, never in strayvideos.
    placed = list(target.rglob("*.mkv"))
    assert len(placed) == 1
    final = placed[0]
    assert "strayvideos" not in final.parts
    assert final.parent.name in {"Season 4", "Specials"}
    # No double extension.
    assert final.name.endswith(".mkv") and not final.name.endswith(".mkv.mkv")
    assert msg.startswith("🎞️")
    assert not src.exists()


def test_match_and_place_stray_path_has_single_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no season matches, the stray path must not duplicate the suffix."""
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(matcher, "load_config", lambda: Config(target_dir=str(target)))

    src = tmp_path / "[One Pace][999-999] Mystery [1080p].mkv"
    src.write_bytes(b"data")

    idx = _build_index()
    msg = matcher.match_and_place_video(src, target, idx, "999-999")

    placed = list((target / "strayvideos").rglob("*.mkv"))
    assert len(placed) == 1
    final = placed[0]
    assert final.name.endswith(".mkv")
    assert not final.name.endswith(".mkv.mkv")
    # Returned message is one of the two stray-style messages.
    assert "Placed" in msg
