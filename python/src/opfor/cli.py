"""``click``-based CLI (port of the ``cmd/`` package).

Commands match the Go version:

    opfor list [-r RANGE] [-t TITLE] [-q QUALITY] [--minquality Q] [-s] [-v]
    opfor download <key> [<key> ...] [--forcekey RANGE]
    opfor setDir <path> [-f]
    opfor sync
    opfor info [-v]
    opfor status
    opfor clear
    opfor logs [-l N]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from . import __version__
from .config import ensure_config_exists, load_config, save_config
from .downloads import clear_active_downloads, get_active_downloads
from .logger import enable_debug_logging, log, show_log_entries
from .parsers import (
    extract_season_number,
    parse_range,
    ranges_overlap,
)
from .types import TorrentEntry
from .ui import (
    COLOR_LBLUE,
    COLOR_PINK,
    get_console,
    multirow_spinner,
    render_torrent_table,
    spinner,
    style_by_range,
    style_str,
)


_QUALITY_CHOICES = ["480p", "720p", "1080p"]


@click.group(
    help="A CLI tool to download One Pace releases and organize them for use with Jellyfin.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.version_option(__version__, prog_name="opfor")
def cli(debug: bool) -> None:
    if debug:
        enable_debug_logging()


# ---------------------------------------------------------------------------
# setDir
# ---------------------------------------------------------------------------


@cli.command("setDir")
@click.argument("path", type=click.Path(file_okay=False))
@click.option("-f", "--force", is_flag=True, help="Force download new metadata")
def set_dir_cmd(path: str, force: bool) -> None:
    """Set the default target directory."""
    from . import metadata as md

    abs_path = str(Path(path).resolve())
    cfg = load_config()
    cfg.target_dir = abs_path
    save_config(cfg)
    click.echo(f"✅ Default target directory set to: {abs_path}")

    Path(abs_path).mkdir(parents=True, exist_ok=True)
    try:
        if force:
            md.fetch_all_metadata(abs_path, cfg)
        else:
            md.sync_metadata(abs_path, cfg)
    except RuntimeError:
        click.echo("⚠️  Unable to sync metadata. (Is git installed?)")


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


@cli.command("clear")
def clear_cmd() -> None:
    """Clear all temporary files, in case something stuck."""
    clear_active_downloads()
    click.echo("✅ Cleared temporary files.")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


@cli.command("sync")
def sync_cmd() -> None:
    """Update metadata library with new content from GitHub."""
    from . import metadata as md

    cfg = load_config()
    if not cfg.target_dir:
        click.echo("⚠️  No target directory set. Use 'setDir' first.")
        return
    try:
        md.sync_metadata(cfg.target_dir, cfg)
    except RuntimeError:
        click.echo("⚠️  Unable to sync metadata. (Is git installed?)")


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


@cli.command("info")
@click.option("-v", "--verbose", "verbose", is_flag=True, help="Show season folder names")
def info_cmd(verbose: bool) -> None:
    """Show current configuration and library status."""
    from . import metadata as md

    cfg = load_config()
    console = get_console()
    console.print("🔧 Current Configuration:")
    console.print(f"📂 Target Directory: {cfg.target_dir}")
    if not cfg.target_dir:
        console.print("⚠️  No target directory set. Use 'opfor setDir <path>'")
        return

    target = Path(cfg.target_dir)
    if not target.exists():
        console.print(f"❌ Could not read target directory: {target}")
        return

    if verbose:
        console.print(f"📡 Torrent Provider: {cfg.source.base_url}")
        console.print(f"🐙 Metadata Source:  https://github.com/{cfg.github_repo}")

    meta_index = md.load_metadata_cache()
    seasons = []
    for entry in target.iterdir():
        if not entry.is_dir() or entry.name == "strayvideos":
            continue
        v, n = md.count_videos_and_total(entry)
        ext_num = extract_season_number(entry.name)
        try:
            s_num = int(ext_num)
        except ValueError:
            s_num = 0
        s_name = ""
        if entry.name in meta_index.seasons:
            s_name = meta_index.seasons[entry.name].name
        seasons.append((s_num, s_name, v, n))

    seasons.sort(key=lambda s: s[0])
    console.print("\n📁 Season folders:")
    for s_num, s_name, v, n in seasons:
        vids = style_by_range(v, 0, n if n > 0 else 1)
        nfos = style_by_range(n, 0, n if n > 0 else 1)
        if s_num == 0:
            console.print(f"   - Specials  : {vids} / {nfos}")
        else:
            num_str = style_str(f"{s_num:>2d}", COLOR_PINK)
            name = style_str(s_name, COLOR_LBLUE) if s_name else ""
            console.print(f"   - Season {num_str}: {vids} / {nfos} | {name}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command("status")
def status_cmd() -> None:
    """Show currently active downloads."""
    downloads = get_active_downloads()
    if not downloads:
        click.echo("📭 No active downloads.")
        return
    click.echo("📦 Active Downloads:")
    for d in downloads:
        pct = (d.progress / d.total_size * 100) if d.total_size else 0.0
        click.echo(f"- {d.title}: {pct:.2f}%")


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@cli.command("logs")
@click.option("-l", "--lines", default=20, type=int, help="Number of lines to show")
def logs_cmd(lines: int) -> None:
    """Show last [n] entries in debug.log."""
    if lines <= 0:
        lines = 20
    show_log_entries(lines)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _check_quality(_ctx, _param, value):
    if value is None:
        return None
    if value not in _QUALITY_CHOICES:
        raise click.BadParameter(f"must be one of {_QUALITY_CHOICES}")
    return value


@cli.command("list")
@click.option("-r", "--range", "range_filter", default="", help="Show seasons in range, e.g. 10-20")
@click.option("-t", "--title", "title_filter", default="", help="Filter by title keyword")
@click.option(
    "-q",
    "--quality",
    "quality_filter",
    default=None,
    callback=_check_quality,
    help="Filter by quality (480p|720p|1080p)",
)
@click.option(
    "--minquality",
    "min_quality_filter",
    default=None,
    callback=_check_quality,
    help="Filter by minimum quality (480p|720p|1080p)",
)
@click.option("-s", "--specials", "only_specials", is_flag=True, help="Show only specials")
@click.option("-v", "--verbose", "verbose_list", is_flag=True, help="Show full titles")
def list_cmd(
    range_filter: str,
    title_filter: str,
    quality_filter: str | None,
    min_quality_filter: str | None,
    only_specials: bool,
    verbose_list: bool,
) -> None:
    """List all available One Pace seasons and specials."""
    from .scraper import ScraperConfigError, fetch_torrents

    cfg = load_config()
    if not cfg.source.base_url:
        click.echo("⚠️  No valid scraper configuration found. Please run 'sync' or 'setDir'")
        return

    with multirow_spinner("🔎 Fetching torrents..."):
        try:
            entries = fetch_torrents(cfg)
        except ScraperConfigError as e:
            click.echo(f"⚠️  {e}")
            return
        except Exception as e:  # noqa: BLE001
            click.echo(f"❌ Error scraping torrents. Site inaccessible? {e}")
            return

    filtered = [
        t
        for t in entries
        if _apply_filters(
            t,
            range_filter=range_filter,
            title_filter=title_filter,
            quality_filter=quality_filter,
            min_quality_filter=min_quality_filter,
            only_specials=only_specials,
        )
    ]
    filtered.sort(key=lambda e: (e.download_key, -e.seeders))

    render_torrent_table(filtered, verbose=verbose_list)


def _apply_filters(
    t: TorrentEntry,
    *,
    range_filter: str,
    title_filter: str,
    quality_filter: str | None,
    min_quality_filter: str | None,
    only_specials: bool,
) -> bool:
    if only_specials and not t.is_special:
        return False
    if range_filter:
        c_min, c_max = parse_range(t.chapter_range)
        f_min, f_max = parse_range(range_filter)
        if c_min < 0 or c_max < 0 or not ranges_overlap(c_min, c_max, f_min, f_max):
            return False
    if title_filter and title_filter.lower() not in t.torrent_name.lower():
        return False
    if quality_filter and t.quality != quality_filter:
        return False
    if min_quality_filter:
        try:
            q = int(t.quality.rstrip("p"))
            mq = int(min_quality_filter.rstrip("p"))
            if q < mq:
                return False
        except ValueError:
            return False
    return True


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


@cli.command("download")
@click.argument("keys", nargs=-1, type=int, required=True)
@click.option(
    "--forcekey",
    default="",
    help="Override chapter range (only for a single download key)",
)
def download_cmd(keys: tuple[int, ...], forcekey: str) -> None:
    """Download one or more One Pace torrents by key."""
    from .scraper import ScraperConfigError, fetch_torrents
    from .torrent_client import handle_download_session

    cfg = load_config()
    if not cfg.target_dir:
        log(True, "⚠️ No target directory set. Use 'setDir <path>' first.")
        return
    if not cfg.source.base_url:
        log(True, "No valid scraper configuration found. Please run 'sync'")
        return

    with spinner("🗃️ Preparing download.."):
        try:
            torrent_list = fetch_torrents(cfg)
        except ScraperConfigError as e:
            log(True, "⚠️ %s", e)
            return
        except Exception as e:  # noqa: BLE001
            log(True, "❌ Error scraping torrents. Site inaccessible? %s", e)
            return

    matches: list[TorrentEntry] = []
    for num in keys:
        match: TorrentEntry | None = None
        for t in torrent_list:
            if t.download_key == num and (match is None or t.seeders > match.seeders):
                match = t
        if match is None:
            log(True, "⚠️  No torrent found for key %d", num)
            continue
        if forcekey:
            if len(keys) > 1:
                log(True, "❌ --forcekey may only be used with a single DownloadKey")
                return
            match.chapter_range = forcekey
        log(
            True,
            "🔍 Matched DownloadKey %4d → %s (%s) [%s]",
            match.download_key,
            match.torrent_name,
            match.quality,
            match.chapter_range,
        )
        log(True, "🎬 Starting download: %s (%s)", match.torrent_name, match.quality)
        matches.append(match)

    if not matches:
        click.echo("⚠️  No downloads to process.")
        sys.exit(0)

    handle_download_session(matches, cfg.target_dir)


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    ensure_config_exists()
    cli()


if __name__ == "__main__":
    main()
