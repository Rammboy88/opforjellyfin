"""Tests for :mod:`opfor.qbittorrent_client`.

We drive the client with ``httpx.MockTransport`` so the tests do not
require a running qBittorrent instance and stay fast.
"""

from __future__ import annotations

import hashlib
import threading
from unittest.mock import patch

import httpx
import pytest

from opfor import qbittorrent_client
from opfor.types import (
    Config,
    QBittorrentConfig,
    ScraperConfig,
    TorrentDownload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_torrent_bytes() -> tuple[bytes, str]:
    info = b"d6:lengthi100e4:name3:foo12:piece lengthi16384e6:pieces0:e"
    announce = b"http://tracker.example/announce"
    torrent = (
        b"d8:announce"
        + str(len(announce)).encode()
        + b":"
        + announce
        + b"4:info"
        + info
        + b"e"
    )
    return torrent, hashlib.sha1(info).hexdigest()


def _make_cfg(tmp_path) -> Config:
    return Config(
        target_dir=str(tmp_path),
        source=ScraperConfig(base_url="http://torrents.example"),
        qbittorrent=QBittorrentConfig(
            enabled=True,
            url="http://qbt.example:8080",
            username="admin",
            password="secret",
            category="opfor",
        ),
    )


class _Recorder:
    """Captures the sequence of API calls made to the fake servers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.poll_count = 0
        self.fail_login = False
        self.fail_add = False
        self.never_register = False
        # progression schedule: each poll returns the next entry (or the last
        # one once exhausted).
        self.progress: list[dict] = [
            {"size": 100, "completed": 0, "state": "downloading"},
            {"size": 100, "completed": 50, "state": "downloading"},
            {"size": 100, "completed": 100, "state": "uploading"},
        ]


def _make_handler(rec: _Recorder, torrent_bytes: bytes, infohash: str):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        # Torrent file fetch (different host)
        if request.url.host == "torrents.example":
            rec.calls.append(("GET", path, {}))
            return httpx.Response(200, content=torrent_bytes)
        # qBittorrent endpoints
        if path == "/api/v2/auth/login":
            form = dict(httpx.QueryParams(request.content.decode()))
            rec.calls.append(("POST", path, form))
            if rec.fail_login:
                return httpx.Response(200, text="Fails.")
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/add":
            rec.calls.append(("POST", path, {"content_type": request.headers.get("content-type", "")}))
            if rec.fail_add:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/info":
            rec.calls.append(("GET", path, dict(request.url.params)))
            if rec.never_register:
                return httpx.Response(200, json=[])
            if request.url.params.get("hashes") != infohash:
                return httpx.Response(200, json=[])
            entry = rec.progress[min(rec.poll_count, len(rec.progress) - 1)]
            rec.poll_count += 1
            return httpx.Response(200, json=[entry])
        if path == "/api/v2/torrents/delete":
            form = dict(httpx.QueryParams(request.content.decode()))
            rec.calls.append(("POST", path, form))
            return httpx.Response(200)
        return httpx.Response(404, text=f"unexpected {url}")

    return handler


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Patch ``httpx.get``, ``httpx.Client`` and config loading."""
    torrent_bytes, infohash = _make_torrent_bytes()
    rec = _Recorder()
    handler = _make_handler(rec, torrent_bytes, infohash)
    transport = httpx.MockTransport(handler)

    cfg = _make_cfg(tmp_path)
    monkeypatch.setattr(qbittorrent_client, "load_config", lambda: cfg)
    # Make the polling loop fast.
    monkeypatch.setattr(qbittorrent_client.time, "sleep", lambda *_: None)

    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(qbittorrent_client.httpx, "Client", _client_factory)

    def _get(url, **kwargs):
        with httpx.Client(transport=transport) as c:
            return c.get(url, **kwargs)

    monkeypatch.setattr(qbittorrent_client.httpx, "get", _get)

    return rec, infohash, cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_login_add_poll_and_clean_delete(patched):
    rec, infohash, _cfg = patched
    td = TorrentDownload(title="t", torrent_id=42)
    qbittorrent_client.start_torrent(td, threading.Event())

    paths = [c[1] for c in rec.calls]
    assert "/api/v2/auth/login" in paths
    assert "/api/v2/torrents/add" in paths
    assert paths.count("/api/v2/torrents/info") >= 1
    # Final cleanup must NOT delete files (we still need them on disk).
    delete_calls = [c for c in rec.calls if c[1] == "/api/v2/torrents/delete"]
    assert delete_calls, "expected a /torrents/delete call"
    assert delete_calls[-1][2].get("deleteFiles") == "false"
    assert delete_calls[-1][2].get("hashes") == infohash

    assert td.done is True
    assert td.placement_progress == "⏳ Waiting to place.."
    assert td.total_size == 100
    assert td.progress == 100


def test_add_uses_multipart_torrent_field(patched):
    rec, _infohash, _cfg = patched
    td = TorrentDownload(title="t", torrent_id=7)
    qbittorrent_client.start_torrent(td, threading.Event())
    add_calls = [c for c in rec.calls if c[1] == "/api/v2/torrents/add"]
    assert add_calls
    assert "multipart/form-data" in add_calls[0][2]["content_type"]


def test_login_failure_raises(patched):
    rec, _infohash, _cfg = patched
    rec.fail_login = True
    td = TorrentDownload(title="t", torrent_id=1)
    with pytest.raises(RuntimeError, match="qBittorrent login failed"):
        qbittorrent_client.start_torrent(td, threading.Event())


def test_add_failure_raises(patched):
    rec, _infohash, _cfg = patched
    rec.fail_add = True
    td = TorrentDownload(title="t", torrent_id=1)
    with pytest.raises(RuntimeError, match="torrents/add failed"):
        qbittorrent_client.start_torrent(td, threading.Event())


def test_torrent_never_registered_raises(patched, monkeypatch):
    rec, _infohash, _cfg = patched
    rec.never_register = True
    # Make the registration deadline elapse "instantly".
    times = iter([0.0, 100.0])
    monkeypatch.setattr(qbittorrent_client.time, "monotonic", lambda: next(times))
    td = TorrentDownload(title="t", torrent_id=1)
    with pytest.raises(RuntimeError, match="did not register torrent"):
        qbittorrent_client.start_torrent(td, threading.Event())


def test_cancellation_deletes_with_files(patched):
    rec, infohash, _cfg = patched
    # Make the download "in progress" forever so the cancel branch fires.
    rec.progress = [{"size": 100, "completed": 10, "state": "downloading"}]
    cancel = threading.Event()

    # Patch sleep so it triggers cancel after the first poll iteration.
    polled = {"n": 0}

    def fake_sleep(_t):
        polled["n"] += 1
        if polled["n"] >= 1:
            cancel.set()

    with patch.object(qbittorrent_client.time, "sleep", fake_sleep):
        td = TorrentDownload(title="t", torrent_id=99)
        with pytest.raises(RuntimeError, match="cancelled"):
            qbittorrent_client.start_torrent(td, cancel)

    delete_calls = [c for c in rec.calls if c[1] == "/api/v2/torrents/delete"]
    assert delete_calls, "expected a delete call on cancel"
    # On cancel, files in qBittorrent's view are also wiped.
    assert any(c[2].get("deleteFiles") == "true" for c in delete_calls)
    assert delete_calls[0][2].get("hashes") == infohash


def test_no_url_configured_raises(monkeypatch, tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.qbittorrent.url = ""
    monkeypatch.setattr(qbittorrent_client, "load_config", lambda: cfg)
    td = TorrentDownload(title="t", torrent_id=1)
    with pytest.raises(RuntimeError, match="no URL is configured"):
        qbittorrent_client.start_torrent(td, threading.Event())


def test_login_skipped_when_no_username(monkeypatch, tmp_path):
    """Some setups disable auth on localhost; we shouldn't force login."""
    torrent_bytes, infohash = _make_torrent_bytes()
    rec = _Recorder()
    handler = _make_handler(rec, torrent_bytes, infohash)
    transport = httpx.MockTransport(handler)

    cfg = _make_cfg(tmp_path)
    cfg.qbittorrent.username = ""
    cfg.qbittorrent.password = ""
    monkeypatch.setattr(qbittorrent_client, "load_config", lambda: cfg)
    monkeypatch.setattr(qbittorrent_client.time, "sleep", lambda *_: None)

    real_client = httpx.Client
    monkeypatch.setattr(
        qbittorrent_client.httpx,
        "Client",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )

    def _get(url, **kwargs):
        with httpx.Client(transport=transport) as c:
            return c.get(url, **kwargs)

    monkeypatch.setattr(qbittorrent_client.httpx, "get", _get)

    td = TorrentDownload(title="t", torrent_id=1)
    qbittorrent_client.start_torrent(td, threading.Event())

    paths = [c[1] for c in rec.calls]
    assert "/api/v2/auth/login" not in paths
    assert "/api/v2/torrents/add" in paths
