"""Config loading/saving (port of `internal/shared/config.go`).

Stores JSON at ``$XDG_CONFIG_HOME/opforjellyfin/config.json`` (Linux default
``~/.config/opforjellyfin/config.json``).
"""

from __future__ import annotations

import os
import json
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from .types import Config, QBittorrentConfig, ScraperConfig, ScraperFields, ValidationConfig


APP_NAME = "opforjellyfin"


def get_config_dir() -> Path:
    p = Path(user_config_dir(APP_NAME, appauthor=False, ensure_exists=True))
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def _config_to_dict(cfg: Config) -> dict[str, Any]:
    """Match the original Go JSON shape (snake_case top-level)."""
    return {
        "target_dir": cfg.target_dir,
        "github_base_url": cfg.github_repo,
        "source": {
            "name": cfg.source.name,
            "base_url": cfg.source.base_url,
            "search_path_template": cfg.source.search_path_template,
            "search_query": cfg.source.search_query,
            "row_selector": cfg.source.row_selector,
            "fields": asdict(cfg.source.fields),
            "validation": {"required_in_title": cfg.source.validation.required_in_title},
        },
        "qbittorrent": {
            "enabled": cfg.qbittorrent.enabled,
            "url": cfg.qbittorrent.url,
            "username": cfg.qbittorrent.username,
            "password": cfg.qbittorrent.password,
            "category": cfg.qbittorrent.category,
        },
    }


def _dict_to_config(data: dict[str, Any]) -> Config:
    src = data.get("source") or {}
    fields_raw = src.get("fields") or {}
    val_raw = src.get("validation") or {}
    qbt_raw = data.get("qbittorrent") or {}
    return Config(
        target_dir=data.get("target_dir", "") or "",
        github_repo=data.get("github_base_url", "tissla/one-pace-jellyfin") or "tissla/one-pace-jellyfin",
        source=ScraperConfig(
            name=src.get("name", ""),
            base_url=src.get("base_url", ""),
            search_path_template=src.get("search_path_template", ""),
            search_query=src.get("search_query", ""),
            row_selector=src.get("row_selector", ""),
            fields=ScraperFields(
                title=fields_raw.get("title", ""),
                seeders=fields_raw.get("seeders", ""),
                torrent_link=fields_raw.get("torrent_link", ""),
                torrent_id=fields_raw.get("torrent_id", ""),
                upload_date=fields_raw.get("upload_date", ""),
            ),
            validation=ValidationConfig(
                required_in_title=val_raw.get("required_in_title", "")
            ),
        ),
        qbittorrent=QBittorrentConfig(
            enabled=bool(qbt_raw.get("enabled", False)),
            url=qbt_raw.get("url", "") or "",
            username=qbt_raw.get("username", "") or "",
            password=qbt_raw.get("password", "") or "",
            category=qbt_raw.get("category", "opfor") or "opfor",
        ),
    )


def ensure_config_exists() -> Path:
    path = get_config_path()
    if not path.exists():
        save_config(Config())
    return path


def load_config() -> Config:
    path = ensure_config_exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = _dict_to_config(data)
    # Allow overriding the qBittorrent password via an environment variable
    # so users who don't want to persist it on disk can keep it out of the
    # config file.
    env_pwd = os.environ.get("OPFOR_QBT_PASSWORD")
    if env_pwd:
        cfg.qbittorrent.password = env_pwd
    return cfg


def save_config(cfg: Config) -> None:
    path = get_config_path()
    path.write_text(
        json.dumps(_config_to_dict(cfg), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # The config file may contain secrets (e.g. qBittorrent Web UI
    # password). Restrict permissions so only the owner can read/write it.
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort: filesystems without POSIX perms (e.g. some networked
        # mounts) may reject chmod; the JSON itself was still written.
        pass
