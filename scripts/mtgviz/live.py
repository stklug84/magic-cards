"""--watch: run one rules-engine game and render it live in the TUI.

The engine runs on a worker thread, paced by a Throttle so the game
unfolds at watchable speed; the main thread renders and handles keys.
Requires 'rich' (falls back to the plain event printer without it).
"""

from __future__ import annotations

import queue
import random
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mtgviz import keys
from mtgviz.recorder import Recorder, VizWriter
from mtgviz.schema import HIGHLIGHTS
from mtgviz.tui import BF_FILTERS, HAVE_RICH, ViewState, format_event, render_frame

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from mtgcards.database import CardDatabase
    from mtgcards.deck import Deck
    from mtgrules.policy import PolicyProfile

try:
    from rich.console import Console
    from rich.live import Live
except ImportError:  # pragma: no cover - all uses are HAVE_RICH-guarded
    pass

LIVE_HELP = (
    " p/space:pause-resume  +/-:speed  h:pause-on-highlight  c:battlefield  q:quit "
)

#: extra dwell time (seconds, at speed 1.0) after notable events
_DWELL = {
    "phase": 0.5,
    "cast": 0.6,
    "resolve": 0.6,
    "attack": 0.5,
    "block": 0.5,
    "counter": 1.0,
    "player_loses": 1.5,
    "dies": 0.4,
    "damage": 0.3,
    "life": 0.2,
    "token": 0.2,
}

_MAX_SPEED = 8.0
_MIN_SPEED = 0.25
#: effectively no pacing (used when there is no interactive UI)
_UNPACED_SPEED = 1000.0


class Throttle:
    """Paces the engine thread; the TUI adjusts speed / pauses it."""

    def __init__(self) -> None:
        """Start unpaused at speed 1.0."""
        self.gate = threading.Event()
        self.gate.set()
        self.speed = 1.0
        self.aborted = False

    def wait(self, kind: str) -> None:
        """Block while paused, then dwell according to the event kind."""
        self.gate.wait()
        if self.aborted:
            raise _AbortError
        time.sleep(_DWELL.get(kind, 0.08) / self.speed)


class _AbortError(Exception):
    """Raised inside the engine thread when the UI quits early."""


@dataclass(frozen=True)
class WatchOptions:
    """Optional watch_game settings, bundled to keep its signature small."""

    turn_cap: int = 40
    profiles: Sequence[PolicyProfile | None] | None = None
    viz_path: str | Path | None = None


def watch_game(
    decks: Sequence[Deck],
    db: CardDatabase,
    seed: int,
    options: WatchOptions | None = None,
) -> None:
    """Run one seeded game on a worker thread and render it live."""
    opts = options or WatchOptions()
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    throttle = Throttle()
    writer = VizWriter(opts.viz_path) if opts.viz_path else None
    file_sink = writer.game_sink(1, seed) if writer else None

    def sink(rec: dict[str, Any]) -> None:
        if file_sink is not None:
            file_sink(rec)
        q.put(rec)
        if rec.get("t") == "e":
            throttle.wait(rec.get("kind", ""))

    recorder = Recorder(sink)
    outcome: dict[str, Any] = {}

    # Deferred: only --watch pulls in the rules engine, and a module-level
    # sibling-package import would couple mtgviz's import graph to the
    # engine. RUF100 is listed because PLC0415 is still globally ignored
    from mtgrules.adapter import MatchOptions, run_game  # noqa: PLC0415

    def engine() -> None:
        try:
            rec: dict[str, Any] = run_game(
                decks,
                db,
                random.Random(seed),
                MatchOptions(turn_cap=opts.turn_cap, profiles=opts.profiles),
                recorder=recorder,
            )
            outcome.update(rec)
        except _AbortError:
            pass
        # Deliberate catch-all: engine errors must reach the UI thread.
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            outcome["error"] = repr(exc)
        finally:
            q.put({"t": "eof"})

    worker = threading.Thread(target=engine, daemon=True)
    worker.start()
    try:
        _render_loop(q, throttle, seed)
    finally:
        throttle.aborted = True
        throttle.gate.set()
        worker.join(timeout=2)
        _finish_report(outcome, writer, opts.viz_path)


def _render_loop(
    q: queue.Queue[dict[str, Any]],
    throttle: Throttle,
    seed: int,
) -> None:
    """Dispatch to the rich TUI or the unpaced plain event stream."""
    if HAVE_RICH and sys.stdin.isatty():
        _rich_loop(q, throttle, seed)
    else:
        print(  # noqa: T201 - user-facing CLI notice on stderr
            "note: plain event stream ('rich' missing or not a TTY)",
            file=sys.stderr,
        )
        throttle.speed = _UNPACED_SPEED  # no pacing without a UI
        _plain_loop(q)


def _finish_report(
    outcome: dict[str, Any],
    writer: VizWriter | None,
    viz_path: str | Path | None,
) -> None:
    """CLI epilogue: close the recording, report errors and the winner."""
    if writer:
        writer.close()
        print(f"viz recording written to {viz_path}")  # noqa: T201
    if outcome.get("error"):
        sys.exit(f"engine error: {outcome['error']}")
    if outcome:
        print(  # noqa: T201 - CLI output
            f"winner: {outcome.get('winner')} "
            f"({outcome.get('reason')}) after "
            f"{outcome.get('turns')} turns",
        )


def _drain(q: queue.Queue[dict[str, Any]], view: ViewState) -> bool:
    """Move queued records into the view; True once the engine is done."""
    done = False
    try:
        while True:
            rec = q.get_nowait()
            if rec.get("t") == "eof":
                done = True
            else:
                view.append(rec)
    except queue.Empty:
        pass
    return done


@dataclass
class _LiveSettings:
    """Mutable live-view mode switches, adjusted by keypresses."""

    pause_hl: bool = True
    bf_filter: str = "all"


def _maybe_pause_on_highlight(
    view: ViewState,
    throttle: Throttle,
    st: _LiveSettings,
) -> None:
    """Auto-pause the engine when the newest event is a highlight."""
    if not (st.pause_hl and throttle.gate.is_set()):
        return
    tail = view.visible_events(n=1)
    if tail and tail[-1]["kind"] in HIGHLIGHTS:
        throttle.gate.clear()


def _live_key(key: str, throttle: Throttle, st: _LiveSettings) -> None:
    """Mode toggles: pause/resume, speed, highlight pause, filter."""
    if key in ("p", " "):
        if throttle.gate.is_set():
            throttle.gate.clear()
        else:
            throttle.gate.set()
    elif key == "+":
        throttle.speed = min(_MAX_SPEED, throttle.speed * 2)
    elif key == "-":
        throttle.speed = max(_MIN_SPEED, throttle.speed / 2)
    elif key == "h":
        st.pause_hl = not st.pause_hl
    elif key == "c":
        st.bf_filter = BF_FILTERS[
            (BF_FILTERS.index(st.bf_filter) + 1) % len(BF_FILTERS)
        ]


def _rich_loop(
    q: queue.Queue[dict[str, Any]],
    throttle: Throttle,
    seed: int,
) -> None:
    """Main-thread render/key loop of the rich live view."""
    view = ViewState(meta={"seed": seed, "game": 1})
    st = _LiveSettings()
    done = False

    def status() -> str:
        mode = f"live x{throttle.speed:g}" if throttle.gate.is_set() else "PAUSED"
        return f" {mode} |{LIVE_HELP}"

    console = Console()
    with (
        keys.raw_terminal(),
        Live(
            render_frame(view, status()),
            console=console,
            screen=True,
            auto_refresh=False,
        ) as live,
    ):
        while True:
            done = _drain(q, view) or done
            view.cursor = len(view.records) - 1
            _maybe_pause_on_highlight(view, throttle, st)
            live.update(
                render_frame(view, status(), bf_filter=st.bf_filter),
                refresh=True,
            )
            if done and q.empty():
                live.update(
                    render_frame(
                        view,
                        " game over - any key to exit ",
                        bf_filter=st.bf_filter,
                    ),
                    refresh=True,
                )
                keys.read_key(None)
                return
            key = keys.read_key(0.1)
            if key in ("q", "\x03"):
                return
            if key is not None:
                _live_key(key, throttle, st)


def _plain_loop(q: queue.Queue[dict[str, Any]]) -> None:
    """Print every engine event as it arrives (no TTY / no rich)."""
    while True:
        rec = q.get()
        if rec.get("t") == "eof":
            return
        if rec.get("t") == "e":
            print(  # noqa: T201 - this viewer's UI
                f"T{rec['turn']:>2} [{rec['kind']}] {format_event(rec)}",
            )
