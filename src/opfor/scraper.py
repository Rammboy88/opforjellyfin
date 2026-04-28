"""Torrent listing scraper (port of `internal/scraper/scraper.go`)."""

from __future__ import annotations

import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag

from .metadata import have_metadata, have_video_status
from .parsers import extract_chapter_range_from_title
from .types import Config, ScraperConfig, TorrentEntry


_BRACKETS_RE = re.compile(r"\[[^\]]+\]")


class ScraperConfigError(RuntimeError):
    pass


def fetch_torrents(cfg: Config) -> list[TorrentEntry]:
    src = cfg.source
    if not src.name or not src.base_url:
        raise ScraperConfigError(
            "no scraper configuration found. Please run 'opfor setDir <path>' first"
        )

    base_url = src.base_url
    raw_entries: list[TorrentEntry] = []
    page = 1
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        while True:
            search_url = (base_url + src.search_path_template) % (
                src.search_query,
                page,
            )
            resp = client.get(search_url)
            if resp.status_code != 200:
                raise RuntimeError(f"unexpected status code: {resp.status_code}")
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select(src.row_selector)
            if not rows:
                break
            for row in rows:
                entry = _parse_row(row, src, base_url)
                if entry is not None:
                    raw_entries.append(entry)
            page += 1

    return _process_entries(raw_entries)


def _row_text(row: Tag, selector: str) -> str:
    if not selector:
        return ""
    el = row.select_one(selector)
    return el.get_text(strip=False) if el else ""


def _row_attr(row: Tag, selector: str, attr: str) -> str:
    if not selector:
        return ""
    el = row.select_one(selector)
    if not el:
        return ""
    val = el.get(attr)
    if isinstance(val, list):
        return val[0] if val else ""
    return val or ""


def _parse_row(row: Tag, cfg: ScraperConfig, base_url: str) -> Optional[TorrentEntry]:
    title = _row_text(row, cfg.fields.title)
    seeders_str = _row_text(row, cfg.fields.seeders)
    torrent_link = _row_attr(row, cfg.fields.torrent_link, "href")
    date = _row_text(row, cfg.fields.upload_date)

    if cfg.validation.required_in_title:
        if cfg.validation.required_in_title.lower() not in title.lower():
            return None
    if not torrent_link:
        return None

    torrent_id = 0
    if cfg.fields.torrent_id:
        m = re.search(cfg.fields.torrent_id, torrent_link)
        if m and m.lastindex:
            try:
                torrent_id = int(m.group(1))
            except ValueError:
                torrent_id = 0

    chapter_range = extract_chapter_range_from_title(title)
    raw_index = _extract_raw_index(chapter_range)
    try:
        seeders = int(seeders_str.strip())
    except ValueError:
        seeders = 0
    quality = _parse_quality(title)
    torrent_name = _extract_torrent_name(title)

    if not torrent_link.startswith("http"):
        torrent_link = base_url + torrent_link

    return TorrentEntry(
        title=title,
        quality=quality,
        torrent_name=torrent_name,
        seeders=seeders,
        raw_index=raw_index,
        torrent_link=torrent_link,
        torrent_id=torrent_id,
        chapter_range=chapter_range,
        is_special=chapter_range == "",
        metadata_avail=have_metadata(chapter_range),
        have_it=have_video_status(chapter_range),
        date=date,
        is_extended="extended" in title.lower(),
    )


def _process_entries(entries: list[TorrentEntry]) -> list[TorrentEntry]:
    """Filter dead torrents, sort, assign download keys."""
    filtered = [e for e in entries if e.seeders > 0]
    # Sort by raw_index asc, then seeders desc (stable)
    filtered.sort(key=lambda e: (e.raw_index, -e.seeders))

    key = 1
    special_key = 9999
    for e in filtered:
        if e.is_special or e.chapter_range == "":
            e.download_key = special_key
            special_key -= 1
        else:
            e.download_key = key
            key += 1
    return filtered


def _parse_quality(title: str) -> str:
    t = title.lower()
    if "1080p" in t:
        return "1080p"
    if "720p" in t:
        return "720p"
    if "480p" in t:
        return "480p"
    return "n/a"


def _extract_raw_index(range_str: str) -> int:
    if not range_str:
        return 9999
    head = range_str.split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 9999


def _extract_torrent_name(title: str) -> str:
    parts = _BRACKETS_RE.split(title)
    for p in parts:
        p = p.strip()
        if p:
            return p
    return "Unknown"
