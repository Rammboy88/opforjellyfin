"""Tests for backend dispatch in :mod:`opfor.torrent_client` and config
round-trip for the qBittorrent section.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from opfor import config as cfg_mod
from opfor import torrent_client
from opfor.types import (
    Config,
    QBittorrentConfig,
    ScraperConfig,
    TorrentDownload,
)


def test_config_roundtrip_with_qbittorrent(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_config_path", lambda: tmp_path / "config.json")

    cfg = Config(
        target_dir="/tmp/x",
        qbittorrent=QBittorrentConfig(
            enabled=True,
            url="http://localhost:8080",
            username="admin",
            password="secret",
            category="opfor",
        ),
    )
    cfg_mod.save_config(cfg)
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["qbittorrent"] == {
        "enabled": True,
        "url": "http://localhost:8080",
        "username": "admin",
        "password": "secret",
        "category": "opfor",
    }
    loaded = cfg_mod.load_config()
    assert loaded.qbittorrent.enabled is True
    assert loaded.qbittorrent.url == "http://localhost:8080"
    assert loaded.qbittorrent.category == "opfor"


def test_config_loads_legacy_without_qbittorrent_section(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_config_path", lambda: tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"target_dir": "/tmp/x"}))
    loaded = cfg_mod.load_config()
    assert loaded.target_dir == "/tmp/x"
    # Defaults are filled in.
    assert loaded.qbittorrent.enabled is False
    assert loaded.qbittorrent.url == ""
    assert loaded.qbittorrent.category == "opfor"


def test_save_config_restricts_permissions(tmp_path, monkeypatch):
    import os
    import stat as stat_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_config_path", lambda: tmp_path / "config.json")
    cfg_mod.save_config(Config())
    mode = stat_mod.S_IMODE(os.stat(tmp_path / "config.json").st_mode)
    # Only the owner should be able to read/write the file.
    assert mode == stat_mod.S_IRUSR | stat_mod.S_IWUSR


def test_env_var_overrides_qbittorrent_password(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_config_path", lambda: tmp_path / "config.json")
    cfg_mod.save_config(
        Config(qbittorrent=QBittorrentConfig(url="http://x", password="from-file"))
    )
    monkeypatch.setenv("OPFOR_QBT_PASSWORD", "from-env")
    assert cfg_mod.load_config().qbittorrent.password == "from-env"
    monkeypatch.delenv("OPFOR_QBT_PASSWORD", raising=False)
    assert cfg_mod.load_config().qbittorrent.password == "from-file"


def test_dispatch_uses_qbittorrent_when_enabled(monkeypatch):
    cfg = Config(qbittorrent=QBittorrentConfig(enabled=True, url="http://x"))
    monkeypatch.setattr(torrent_client, "load_config", lambda: cfg)

    called: dict = {}

    def fake_qbt(td, ev):
        called["yes"] = True

    import opfor.qbittorrent_client as qbt_mod

    monkeypatch.setattr(qbt_mod, "start_torrent", fake_qbt)
    # Make libtorrent appear available so dispatch only flips on `enabled`.
    monkeypatch.setattr(torrent_client, "_libtorrent_available", lambda: True)

    torrent_client.start_torrent(TorrentDownload(torrent_id=1), threading.Event())
    assert called == {"yes": True}


def test_dispatch_falls_back_to_qbittorrent_when_libtorrent_missing(monkeypatch):
    cfg = Config(qbittorrent=QBittorrentConfig(enabled=False, url="http://x"))
    monkeypatch.setattr(torrent_client, "load_config", lambda: cfg)
    monkeypatch.setattr(torrent_client, "_libtorrent_available", lambda: False)

    called: dict = {}
    import opfor.qbittorrent_client as qbt_mod

    monkeypatch.setattr(qbt_mod, "start_torrent", lambda td, ev: called.setdefault("yes", True))

    torrent_client.start_torrent(TorrentDownload(torrent_id=1), threading.Event())
    assert called == {"yes": True}


def test_dispatch_raises_when_no_backend(monkeypatch):
    cfg = Config()  # no qbittorrent, no libtorrent
    monkeypatch.setattr(torrent_client, "load_config", lambda: cfg)
    monkeypatch.setattr(torrent_client, "_libtorrent_available", lambda: False)
    # Force the underlying libtorrent import path to raise.
    monkeypatch.setattr(
        torrent_client,
        "_import_libtorrent",
        lambda: (_ for _ in ()).throw(
            RuntimeError("libtorrent is required for downloads. ...")
        ),
    )
    with pytest.raises(RuntimeError, match="libtorrent is required"):
        torrent_client.start_torrent(
            TorrentDownload(torrent_id=1), threading.Event()
        )
