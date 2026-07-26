"""Load recorded viz JSONL files and replay them (rich TUI or plain text).

replay_file is the --replay entry point; run_replay is the interactive
keyboard-driven app on top of tui.render_frame, with plain_replay as the
line-based fallback when 'rich' is unavailable or stdin is not a TTY.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgviz import keys
from mtgviz.schema import HIGHLIGHTS
from mtgviz.tui import (
    BF_FILTERS,
    HAVE_RICH,
    ViewState,
    format_event,
    print_plain_frame,
    render_frame,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mtgviz.schema import Record

try:
    from rich.console import Console
    from rich.live import Live
except ImportError:  # pragma: no cover - all uses are HAVE_RICH-guarded
    pass


def load_games(path: str | Path) -> dict[int, dict[str, Any]]:
    """Split a --viz-file JSONL into per-game streams.

    Returns {game_no: {"meta": {...}, "records": [...], "result": {...}}}.
    """
    games: dict[int, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    try:
        with Path(path).open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                rec = json.loads(line)
                t = rec.get("t")
                if t == "game":
                    current = {"meta": rec, "records": [], "result": None}
                    games[rec["game"]] = current
                elif current is None:
                    continue
                elif t == "end":
                    current["result"] = rec
                else:
                    current["records"].append(rec)
    except OSError as exc:
        sys.exit(f"cannot open replay file: {exc}")
    return games


def replay_file(path: str | Path, game: int = 1) -> None:
    """Replay one recorded game from *path* (the --replay entry point)."""
    games = load_games(path)
    if not games:
        sys.exit(f"{path}: no recorded games found (record with --viz-file)")
    if game not in games:
        sys.exit(
            f"{path}: no game {game}; available: {', '.join(map(str, sorted(games)))}",
        )
    g = games[game]
    if not HAVE_RICH:
        print(  # noqa: T201 - user-facing CLI notice on stderr
            "note: 'rich' not installed (pip install rich); "
            "using the plain step viewer",
            file=sys.stderr,
        )
    run_replay(g["records"], g["meta"], g["result"])


def plain_replay(
    records: list[Record],
    meta: dict[str, Any],
    result: Record | None = None,
) -> None:
    """Fallback viewer: prints one phase per <Enter>, 'q' to quit."""
    view = ViewState(records, meta)
    print(  # noqa: T201 - this viewer's UI
        f"replaying game {meta.get('game', 1)} (seed "
        f"{meta.get('seed')}), {len(records)} records; <Enter> for next "
        f"phase, 'a<Enter>' for all, 'q<Enter>' to quit",
    )
    auto = False
    while not view.at_end():
        start = view.cursor
        view.next_phase(1)
        for i in range(start + 1, view.cursor + 1):
            rec = view.records[i]
            if rec["t"] == "e" and rec["kind"] != "phase":
                print(f"    {format_event(rec)}")  # noqa: T201
        print_plain_frame(view)
        if auto:
            continue
        try:
            ans = input("> ").strip().lower()
        except EOFError:
            return
        if ans == "q":
            return
        if ans == "a":
            auto = True
    if result:
        print(  # noqa: T201 - this viewer's UI
            f"=== winner: {result['winner']} ({result['reason']}) "
            f"after {result['turns']} turns ===",
        )


# --------------------------------------------------------------- replay app
REPLAY_HELP = (
    " space:event  n/N:phase  t/T:turn  arrows:event/turn "
    " g<turn>:jump  a:auto  +/-:speed  h:pause-on-highlight "
    " f:log-filter  c:battlefield  q:quit "
)

_MAX_SPEED = 8.0
_MIN_SPEED = 0.25


@dataclass
class _ReplaySettings:
    """Mutable replay-app mode switches, adjusted by keypresses."""

    autoplay: bool = False
    speed: float = 1.0
    pause_hl: bool = True
    log_filter: str | None = None
    bf_filter: str = "all"
    goto: str | None = None  # digits typed after 'g', None when inactive


#: navigation keys -> ViewState motion
_NAV_KEYS: dict[str, Callable[[ViewState], bool]] = {
    " ": lambda v: v.step(1),
    "right": lambda v: v.step(1),
    "left": lambda v: v.step(-1),
    "b": lambda v: v.step(-1),
    "n": lambda v: v.next_phase(1),
    "N": lambda v: v.next_phase(-1),
    "t": lambda v: v.next_turn(1),
    "down": lambda v: v.next_turn(1),
    "T": lambda v: v.next_turn(-1),
    "up": lambda v: v.next_turn(-1),
}


def _status_line(view: ViewState, st: _ReplaySettings) -> str:
    mode = f"auto x{st.speed:g}" if st.autoplay else "paused"
    pos = f"{max(view.cursor, 0)}/{len(view.records)}"
    g = f"  goto: {st.goto}_" if st.goto is not None else ""
    return f" {mode}  {pos}{g} |{REPLAY_HELP}"


def _autoplay_tick(view: ViewState, st: _ReplaySettings) -> None:
    """Advance one event; stop at the end or on highlight events."""
    moved = view.step(1)
    if (not moved and view.at_end()) or (
        st.pause_hl
        and view.records[view.cursor]["t"] == "e"
        and view.records[view.cursor]["kind"] in HIGHLIGHTS
    ):
        st.autoplay = False


def _goto_key(key: str, view: ViewState, st: _ReplaySettings) -> None:
    """Collect digits after 'g'; <Enter> jumps, anything else cancels."""
    if key.isdigit():
        st.goto = (st.goto or "") + key
        return
    if key in ("\r", "\n") and st.goto:
        view.jump_turn(int(st.goto))
    st.goto = None


def _mode_key(
    key: str,
    view: ViewState,
    st: _ReplaySettings,
    player_names: list[str],
) -> None:
    """Mode toggles: autoplay, speed, pauses, filters, goto, end."""
    if key == "a":
        st.autoplay = not st.autoplay
    elif key == "+":
        st.speed = min(_MAX_SPEED, st.speed * 2)
    elif key == "-":
        st.speed = max(_MIN_SPEED, st.speed / 2)
    elif key == "h":
        st.pause_hl = not st.pause_hl
    elif key == "f":
        opts: list[str | None] = [None, *player_names]
        st.log_filter = opts[(opts.index(st.log_filter) + 1) % len(opts)]
    elif key == "c":
        st.bf_filter = BF_FILTERS[
            (BF_FILTERS.index(st.bf_filter) + 1) % len(BF_FILTERS)
        ]
    elif key == "g":
        st.goto = ""
    elif key == "G":
        view.cursor = len(view.records) - 1


def _replay_key(
    key: str,
    view: ViewState,
    st: _ReplaySettings,
    player_names: list[str],
) -> bool:
    """Apply one keypress; returns False when the app should quit."""
    if key in ("q", "\x03"):
        return False
    nav = _NAV_KEYS.get(key)
    if nav is not None:
        nav(view)
    else:
        _mode_key(key, view, st, player_names)
    return True


def run_replay(
    records: list[Record],
    meta: dict[str, Any],
    result: Record | None = None,
) -> None:
    """Interactive replay of one recorded game (plain fallback without rich)."""
    if not HAVE_RICH or not sys.stdin.isatty():
        plain_replay(records, meta, result)
        return
    view = ViewState(records, meta)
    view.result = result
    view.step(1)
    st = _ReplaySettings()
    first = view.snapshot() or {}
    player_names = [p["name"] for p in first.get("players", [])]
    console = Console()
    with (
        keys.raw_terminal(),
        Live(
            render_frame(view, _status_line(view, st)),
            console=console,
            screen=True,
            auto_refresh=False,
        ) as live,
    ):
        while True:
            live.update(
                render_frame(
                    view,
                    _status_line(view, st),
                    st.log_filter,
                    bf_filter=st.bf_filter,
                ),
                refresh=True,
            )
            key = keys.read_key(0.35 / st.speed if st.autoplay else None)
            if key is None:  # autoplay tick
                _autoplay_tick(view, st)
                continue
            if st.goto is not None:
                _goto_key(key, view, st)
                continue
            if not _replay_key(key, view, st, player_names):
                return
