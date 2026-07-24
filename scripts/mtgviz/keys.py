"""Raw-terminal keyboard input (stdlib only; rich does not do input).

read_key() returns single characters, or symbolic names for arrows:
'left', 'right', 'up', 'down'. Returns None on timeout.
"""

from __future__ import annotations

import contextlib
import os
import select
import sys
import termios
import tty

_ESC_MAP = {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}


@contextlib.contextmanager
def raw_terminal():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key(timeout: float | None = None) -> str | None:
    """One keypress (call inside raw_terminal()). None on timeout."""
    fd = sys.stdin.fileno()
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    ch = os.read(fd, 1).decode(errors="ignore")
    if ch != "\x1b":
        return ch
    # escape sequence (arrow keys): read the rest if present
    r, _, _ = select.select([fd], [], [], 0.01)
    if not r:
        return "esc"
    seq = os.read(fd, 2).decode(errors="ignore")
    return _ESC_MAP.get(seq, "esc")
