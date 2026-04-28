"""pytest port of `internal/shared/parsers_test.go`."""

import pytest

from opfor.parsers import (
    extract_chapter_range_from_nfo,
    extract_chapter_range_from_title,
    rough_extract_chapter_from_title,
)


@pytest.mark.parametrize(
    ("input_title", "expected"),
    [
        ("[One Pace][8-11] adas", "8-11"),
        ("[One Pace][42] single1", "42-42"),
        ("[One Pace][3, 153-156] single2", "3-3"),
        ("[One Pace][123-124, 520] tail", "123-124"),
        ("nothingatall", ""),
    ],
)
def test_extract_chapter_range_from_title(input_title, expected):
    assert extract_chapter_range_from_title(input_title) == expected


@pytest.mark.parametrize(
    ("input_title", "want", "expect_range"),
    [
        ("Chapter 01", "01", False),
        ("Chapter 22", "22", False),
        ("Chapter 583240", "583240", False),
        ("Chapter33", "33", False),
        ("Chapter 22  55", "22", False),
        ("Chapter", "00", False),
        ("NotAChapter", "00", False),
        ("Chapter 841-845", "841-845", True),
        ("Chapter35-36", "35-36", True),
        ("Chapters 35-36", "35-36", True),
        (
            "One Pace] Paced One Piece - Thriller Bark Episode 18 [720p][2295F0A1].mkv",
            "18",
            False,
        ),
        ("[One Pace] Chapter 831-832 [720p][DF6B6FEC].mkv", "831-832", True),
    ],
)
def test_rough_extract_chapter_from_title(input_title, want, expect_range):
    got, is_range = rough_extract_chapter_from_title(input_title)
    assert got == want
    assert is_range == expect_range


def test_extract_chapter_range_from_nfo():
    assert extract_chapter_range_from_nfo("Manga Chapter(s): 42, 22") == "42-42"
    assert extract_chapter_range_from_nfo("Manga Chapter(s): 8-11") == "8-11"
    assert extract_chapter_range_from_nfo("Manga Chapter(s): 7") == "7-7"
