"""Logging port of `internal/logger/logging.go`.

A `debug.log` file in the current working directory is used for debug output,
matching the Go behaviour. The user-facing log goes to stdout.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

_DEBUG_PATH = Path("debug.log")
_logger = logging.getLogger("opfor")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_debug_enabled = False
_file_handler: logging.FileHandler | None = None


def enable_debug_logging() -> None:
    """Open `debug.log` (truncated) and start writing debug entries to it."""
    global _debug_enabled, _file_handler
    if _debug_enabled:
        return
    try:
        # Truncate the file like the Go version does.
        _DEBUG_PATH.write_text("", encoding="utf-8")
        handler = logging.FileHandler(_DEBUG_PATH, mode="a", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(filename)s:%(lineno)d: %(message)s")
        )
        _logger.addHandler(handler)
        _file_handler = handler
        _debug_enabled = True
    except OSError as e:
        sys.stderr.write(f"logger: could not open debug.log: {e}\n")


def log(show_user: bool, fmt: str, *args: Any) -> None:
    """Thread-safe log helper.

    If ``show_user`` is true, the message is also printed to stdout.
    """
    msg = fmt % args if args else fmt
    if show_user:
        print(msg)
    if _debug_enabled:
        _logger.debug(msg)


def show_log_entries(n: int) -> None:
    if not _DEBUG_PATH.exists():
        sys.stderr.write("logger: could not open debug.log\n")
        return
    try:
        lines = _DEBUG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        sys.stderr.write(f"logger: could not open debug.log: {e}\n")
        return
    for line in lines[-n:]:
        if line:
            print(line)
