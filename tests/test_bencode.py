"""Tests for the bencode helpers in :mod:`opfor.qbittorrent_client`.

The infohash is the SHA-1 of the bencoded ``info`` dict slice, exactly as
it appears in the .torrent file.  We build a tiny synthetic torrent and
check that the helper picks the right slice.
"""

from __future__ import annotations

import hashlib

import pytest

from opfor.qbittorrent_client import (
    BencodeError,
    _find_info_dict_bytes,
    compute_infohash,
)


def _make_torrent(info_payload: bytes, with_announce: bool = True) -> bytes:
    parts = [b"d"]
    if with_announce:
        announce = b"http://tracker.example/announce"
        parts.append(b"8:announce" + str(len(announce)).encode() + b":" + announce)
    parts.append(b"4:info")
    parts.append(info_payload)
    parts.append(b"e")
    return b"".join(parts)


def test_find_info_dict_bytes_returns_exact_slice():
    info_payload = b"d6:lengthi42e4:name3:foo12:piece lengthi16384e6:pieces0:e"
    torrent = _make_torrent(info_payload)
    assert _find_info_dict_bytes(torrent) == info_payload


def test_find_info_dict_without_announce():
    info_payload = b"d6:lengthi1e4:name1:ae"
    torrent = _make_torrent(info_payload, with_announce=False)
    assert _find_info_dict_bytes(torrent) == info_payload


def test_compute_infohash_matches_sha1_of_info():
    info_payload = b"d6:lengthi42e4:name3:foo12:piece lengthi16384e6:pieces0:e"
    torrent = _make_torrent(info_payload)
    expected = hashlib.sha1(info_payload).hexdigest()
    assert compute_infohash(torrent) == expected


def test_bencode_error_on_garbage():
    with pytest.raises(BencodeError):
        _find_info_dict_bytes(b"not a torrent")


def test_bencode_error_when_info_missing():
    with pytest.raises(BencodeError):
        _find_info_dict_bytes(b"d4:spam4:eggse")


def test_nested_dict_in_info_preserved():
    # info contains a nested files list; still must be returned verbatim.
    info_payload = (
        b"d5:filesld6:lengthi10e4:pathl1:a1:beed6:lengthi5e4:pathl1:ceee"
        b"4:name3:dir12:piece lengthi32768e6:pieces0:e"
    )
    torrent = _make_torrent(info_payload)
    assert _find_info_dict_bytes(torrent) == info_payload
    assert compute_infohash(torrent) == hashlib.sha1(info_payload).hexdigest()
