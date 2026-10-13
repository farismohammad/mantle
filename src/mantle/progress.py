"""Visible status for slow steps, always on stderr so stdout stays parseable."""
from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console


def _console() -> Console:
    return Console(stderr=True, highlight=False, markup=False, soft_wrap=True)


def enabled() -> bool:
    return not os.environ.get("MANTLE_QUIET")


@contextmanager
def step(message: str, done: str | None = None) -> Iterator[None]:
    """Show `message` while a slow operation runs.

    Terminals get a spinner followed by a completion line. Other outputs (logs,
    CI) get a single plain line before the work starts.
    """
    if not enabled():
        yield
        return
    console = _console()
    started = time.monotonic()
    if console.is_terminal:
        with console.status(message):
            yield
        elapsed = time.monotonic() - started
        if done:
            suffix = f" ({elapsed:.0f}s)" if elapsed >= 1 else ""
            console.print(f"✔ {done}{suffix}")
    else:
        console.print(message)
        yield


def note(message: str) -> None:
    """Print an informational line on stderr; unlike progress, never suppressed."""
    _console().print(message)
