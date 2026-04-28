"""User-facing rendering using ``rich``.

Replaces ``internal/ui/*.go`` (lipgloss/bubbletea) with a simpler and
portable equivalent. The visual output is not pixel-identical to the Go
version, but the UX (colours, tables, progress bars, spinners) is preserved.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import threading
import time
from typing import Any, Iterator, Optional

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .downloads import get_active_downloads


_console = Console(highlight=False)


def get_console() -> Console:
    return _console


# ---------------------------------------------------------------------------
# Color helpers (port of style_factory.go)
# ---------------------------------------------------------------------------


COLOR_PINK = "magenta1"  # ~ANSI 201
COLOR_LBLUE = "bright_blue"  # ~ANSI 12
COLOR_RED = "red"
COLOR_GREEN = "green"


def style(text: str, colour: str) -> Text:
    return Text(text, style=colour)


def style_str(text: str, colour: str) -> str:
    return f"[{colour}]{text}[/]"


_NUMBER_RE = re.compile(r"\d+")


def style_by_range(value: int | str, lo: int, hi: int) -> str:
    """Colour ``value`` from red (≤lo) through to green (≥hi)."""
    if isinstance(value, int):
        text = str(value)
        n = value
    else:
        text = value
        m = _NUMBER_RE.search(value)
        if not m:
            return text
        try:
            n = int(m.group(0))
        except ValueError:
            return text

    if n <= lo:
        return f"[#ff0000]{text}[/]"
    if n >= hi:
        return f"[#00ff00]{text}[/]"
    ratio = (n - lo) / (hi - lo)
    r = int(round(255 * (1 - ratio)))
    g = 255 - r
    return f"[#{r:02x}{g:02x}00]{text}[/]"


def get_terminal_width() -> int:
    try:
        return shutil.get_terminal_size((80, 20)).columns
    except OSError:
        return 80


# ---------------------------------------------------------------------------
# Spinners
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def spinner(message: str) -> Iterator[None]:
    """Simple one-line spinner. Mirrors `NewSpinner`."""
    with _console.status(message, spinner="dots"):
        yield


@contextlib.contextmanager
def multirow_spinner(message: str) -> Iterator[None]:
    """Multi-row spinner – simplified to a status line.

    The Go version animates emoji art; ``rich.status`` keeps the UX clear
    without locking into one specific animation.
    """
    with _console.status(message, spinner="earth"):
        yield


# ---------------------------------------------------------------------------
# Progress bars for active downloads
# ---------------------------------------------------------------------------


def _build_progress() -> Progress:
    return Progress(
        TextColumn("[bold]{task.fields[label]}[/]", justify="left"),
        BarColumn(bar_width=40),
        TextColumn("{task.percentage:>3.0f}%"),
        TextColumn("{task.fields[msg]}"),
        TimeRemainingColumn(),
        console=_console,
        transient=False,
    )


def follow_progress(done_event: threading.Event, refresh_hz: float = 3.0) -> None:
    """Render a live multi-bar view of active downloads until ``done_event`` is set."""
    progress = _build_progress()
    tasks: dict[int, TaskID] = {}

    with Live(progress, console=_console, refresh_per_second=refresh_hz):
        while not done_event.is_set():
            _refresh_tasks(progress, tasks)
            time.sleep(1.0 / refresh_hz)
        # one final refresh
        _refresh_tasks(progress, tasks)


def _refresh_tasks(progress: Progress, tasks: dict[int, TaskID]) -> None:
    downloads = get_active_downloads()
    for td in downloads:
        total = td.total_size or 1
        if td.torrent_id not in tasks:
            tasks[td.torrent_id] = progress.add_task(
                "",
                total=total,
                completed=td.progress,
                label=td.title[:40],
                msg=td.placement_progress or "",
            )
        else:
            progress.update(
                tasks[td.torrent_id],
                total=total,
                completed=td.progress,
                msg=td.placement_progress or "",
            )


# ---------------------------------------------------------------------------
# List rendering (replaces RenderRow zebra logic with a rich Table)
# ---------------------------------------------------------------------------


def render_torrent_table(
    rows: list[Any],
    *,
    verbose: bool,
    title: str = "📚 Filtered Download List",
) -> None:
    """Render the torrent listing.

    ``rows`` are TorrentEntry instances.
    """
    table = Table(
        title=title,
        title_justify="left",
        show_lines=False,
        row_styles=["", "on grey11"],
        header_style="bold bright_blue",
    )
    table.add_column("DKEY", justify="right", style=COLOR_PINK)
    table.add_column("Title", style=COLOR_LBLUE, overflow="fold")
    table.add_column("Have", justify="center")
    table.add_column("Meta", justify="center")
    if not verbose:
        table.add_column("Chapters", justify="center")
        table.add_column("Quality", justify="right")
    table.add_column("Seeders", justify="right")
    table.add_column("Date", justify="left")

    have_marks = {0: "❌", 1: "🟠", 2: "✅"}
    for t in rows:
        have_mark = have_marks.get(t.have_it, "❌")
        meta_mark = "✅" if t.metadata_avail else "❌"
        title_text = t.torrent_name if not verbose else t.title
        if verbose:
            table.add_row(
                f"{t.download_key:4d}",
                title_text,
                have_mark,
                meta_mark,
                style_by_range(t.seeders, 0, 10),
                t.date,
            )
        else:
            table.add_row(
                f"{t.download_key:4d}",
                title_text,
                have_mark,
                meta_mark,
                t.chapter_range or "-",
                style_by_range(t.quality or "n/a", 400, 1000),
                style_by_range(t.seeders, 0, 10),
                t.date,
            )

    _console.print(table)
