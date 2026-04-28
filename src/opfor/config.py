"""Config loading/saving (port of `internal/shared/config.go`).

Stores JSON at ``$XDG_CONFIG_HOME/opforjellyfin/config.json`` (Linux default
``~/.config/opforjellyfin/config.json``).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from .types import Config, ScraperConfig, ScraperFields, ValidationConfig


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
    }


def _dict_to_config(data: dict[str, Any]) -> Config:
    src = data.get("source") or {}
    fields_raw = src.get("fields") or {}
    val_raw = src.get("validation") or {}
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
    )


def ensure_config_exists() -> Path:
    path = get_config_path()
    if not path.exists():
        save_config(Config())
    return path


def load_config() -> Config:
    path = ensure_config_exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    return _dict_to_config(data)


def save_config(cfg: Config) -> None:
    path = get_config_path()
    path.write_text(
        json.dumps(_config_to_dict(cfg), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
