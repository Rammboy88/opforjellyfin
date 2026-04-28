"""Dataclasses mirroring `internal/shared/types.go`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ScraperFields:
    title: str = ""
    seeders: str = ""
    torrent_link: str = ""
    torrent_id: str = ""
    upload_date: str = ""


@dataclass
class ValidationConfig:
    required_in_title: str = ""


@dataclass
class ScraperConfig:
    name: str = ""
    base_url: str = ""
    search_path_template: str = ""
    search_query: str = ""
    row_selector: str = ""
    fields: ScraperFields = field(default_factory=ScraperFields)
    validation: ValidationConfig = field(default_factory=ValidationConfig)


@dataclass
class Config:
    target_dir: str = ""
    github_repo: str = "tissla/one-pace-jellyfin"
    source: ScraperConfig = field(default_factory=ScraperConfig)


@dataclass
class EpisodeData:
    title: str = ""


@dataclass
class SeasonIndex:
    range: str = ""
    name: str = ""
    episode_range: dict[str, EpisodeData] = field(default_factory=dict)


@dataclass
class MetadataIndex:
    seasons: dict[str, SeasonIndex] = field(default_factory=dict)


@dataclass
class TorrentEntry:
    title: str = ""
    quality: str = ""
    download_key: int = 0
    torrent_name: str = ""
    seeders: int = 0
    raw_index: int = 0
    torrent_link: str = ""
    torrent_id: int = 0
    chapter_range: str = ""
    metadata_avail: bool = False
    is_special: bool = False
    have_it: int = 0
    date: str = ""
    is_extended: bool = False


@dataclass
class TorrentDownload:
    title: str = ""
    full_title: str = ""
    torrent_id: int = 0
    chapter_range: str = ""
    started: Optional[datetime] = None
    progress: int = 0
    total_size: int = 0
    placement_full: list[str] = field(default_factory=list)
    placement_progress: str = ""
    done: bool = False
    placed: bool = False
