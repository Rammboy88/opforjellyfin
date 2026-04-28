"""Pure parsing helpers, port of `internal/shared/parsers.go`."""

from __future__ import annotations

import re
from typing import Tuple


_RE_CHAPTER_FROM_TITLE = re.compile(r"\[One Pace\]\[([^\]]+)\]", re.IGNORECASE)
_RE_NUMBER_ONLY = re.compile(r"^\d+$")
_RE_RANGE_ONLY = re.compile(r"^\d+-\d+$")
_RE_NFO = re.compile(r"Manga\s*Chapter\(s\)?:\s*(\d+(?:\s*-\s*\d+)?)", re.IGNORECASE)
_RE_EP_FROM_KEY = re.compile(r"E(\d+)$")
_RE_SEASON_FROM_KEY = re.compile(r"S(\d+)E\d+")
_RE_ROUGH_RANGE = re.compile(r"(?i)(Chapters?|Episodes?)\s*(\d+)\s*-\s*(\d+)")
_RE_ROUGH_SINGLE = re.compile(r"(?i)(Chapters?|Episodes?)\s*(\d+)\b")


def is_episode_nfo(filename: str) -> bool:
    """Episode .nfo files (i.e. not season.nfo / tvshow.nfo)."""
    return (
        filename.endswith(".nfo")
        and "season" not in filename
        and "tvshow" not in filename
    )


def extract_chapter_range_from_title(title: str) -> str:
    """Strict extraction. `[One Pace][x-y]...` -> "x-y"; `[One Pace][42]...` -> "42-42"."""
    m = _RE_CHAPTER_FROM_TITLE.search(title)
    if not m:
        return ""
    chapter_info = m.group(1)
    first = chapter_info.split(",")[0].strip()
    if _RE_NUMBER_ONLY.match(first):
        return f"{first}-{first}"
    if _RE_RANGE_ONLY.match(first):
        return first
    return ""


def parse_range(r: str) -> Tuple[int, int]:
    """"x-y" -> (x, y); invalid -> (-1, -1). Non-int parts are silently 0."""
    parts = r.split("-")
    if len(parts) != 2:
        return -1, -1
    try:
        a = int(parts[0])
    except ValueError:
        a = 0
    try:
        b = int(parts[1])
    except ValueError:
        b = 0
    return a, b


def extract_xml_tag(data: bytes | str, tag: str) -> str:
    """Extract first occurrence of `<tag>...</tag>` content (case-insensitive)."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("latin-1", errors="replace")
    else:
        text = data
    pat = re.compile(rf"<{tag}>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    m = pat.search(text)
    return m.group(1).strip() if m else ""


def extract_chapter_range_from_nfo(content: str) -> str:
    """Parse "Manga Chapter(s): 8-11" -> "8-11"; "1" -> "1-1"; "1, 5" -> "1-1"."""
    m = _RE_NFO.search(content)
    if not m:
        return ""
    range_str = m.group(1).replace(" ", "")
    if "-" in range_str:
        parts = range_str.split("-")
        if len(parts) == 2:
            return f"{parts[0]}-{parts[1]}"
    return f"{range_str}-{range_str}"


def extract_season_number(season_key: str) -> str:
    """Folder name -> season number. "Season 02" -> "02"."""
    parts = season_key.split()
    if len(parts) == 2:
        return parts[1]
    return "00"


def extract_episode_number_from_key(episode_key: str) -> str:
    """"S02E03" -> "03"."""
    m = _RE_EP_FROM_KEY.search(episode_key)
    return m.group(1) if m else "00"


def extract_season_number_from_key(episode_key: str) -> str:
    """"S05E04" -> "05"."""
    m = _RE_SEASON_FROM_KEY.search(episode_key)
    return m.group(1) if m else "00"


def rough_extract_chapter_from_title(title: str) -> Tuple[str, bool]:
    """Find `Chapter X` or `Chapter X-Y` in a title.

    Returns (value, is_range). Defaults to ("00", False).
    """
    m = _RE_ROUGH_RANGE.search(title)
    if m:
        return f"{m.group(2)}-{m.group(3)}", True
    m = _RE_ROUGH_SINGLE.search(title)
    if m:
        return m.group(2), False
    return "00", False


def normalize_dash(s: str) -> str:
    """Replace en/em dashes with hyphen-minus."""
    return s.replace("\u2013", "-").replace("\u2014", "-")


def ranges_overlap(a1: int, a2: int, b1: int, b2: int) -> bool:
    return a1 <= b2 and b1 <= a2
