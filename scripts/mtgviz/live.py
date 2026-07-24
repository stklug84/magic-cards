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

from .recorder import Recorder, VizWriter
from .schema import HIGHLIGHTS
from .tui import HAVE_RICH, ViewState, format_event, render_frame
from . import keys

LIVE_HELP = (" p/space:pause-resume  +/-:speed  h:pause-on-highlight "
             " q:quit ")

#: extra dwell time (seconds, at speed 1.0) after notable events
_DWELL = {"phase": 0.5, "cast": 0.6, "resolve": 0.6, "attack": 0.5,
          "block": 0.5, "counter": 1.0, "player_loses": 1.5, "dies": 0.4,
          "damage": 0.3, "life": 0.2, "token": 0.2}


class Throttle:
    """Paces the engine thread; the TUI adjusts speed / pauses it."""

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.speed = 1.0
        self.aborted = False

    def wait(self, kind):
        self.gate.wait()
        if self.aborted:
            raise _Abort()
        time.sleep(_DWELL.get(kind, 0.08) / self.speed)


class _Abort(Exception):
    pass


def watch_game(decks, db, seed, turn_cap=40, profiles=None, viz_path=None):
    from mtgrules.adapter import run_game

    q: queue.Queue = queue.Queue()
    throttle = Throttle()
    writer = VizWriter(viz_path) if viz_path else None
    file_sink = writer.game_sink(1, seed) if writer else None

    def sink(rec):
        if file_sink is not None:
            file_sink(rec)
        q.put(rec)
        if rec.get("t") == "e":
            throttle.wait(rec.get("kind"))

    recorder = Recorder(sink)
    outcome = {}

    def engine():
        try:
            rec = run_game(decks, db, random.Random(seed),
                           turn_cap=turn_cap, profiles=profiles,
                           recorder=recorder)
            outcome.update(rec)
        except _Abort:
            pass
        except Exception as exc:                     # pragma: no cover
            outcome["error"] = repr(exc)
        finally:
            q.put({"t": "eof"})

    worker = threading.Thread(target=engine, daemon=True)
    worker.start()
    try:
        if HAVE_RICH and sys.stdin.isatty():
            _rich_loop(q, throttle, seed)
        else:
            print("note: plain event stream ('rich' missing or not a "
                  "TTY)", file=sys.stderr)
            throttle.speed = 1000.0        # no pacing without a UI
            _plain_loop(q)
    finally:
        throttle.aborted = True
        throttle.gate.set()
        worker.join(timeout=2)
        if writer:
            writer.close()
            print(f"viz recording written to {viz_path}")
        if outcome.get("error"):
            sys.exit(f"engine error: {outcome['error']}")
        if outcome:
            print(f"winner: {outcome.get('winner')} "
                  f"({outcome.get('reason')}) after "
                  f"{outcome.get('turns')} turns")


def _drain(q, view):
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


def _rich_loop(q, throttle, seed):
    from rich.console import Console
    from rich.live import Live

    view = ViewState(meta={"seed": seed, "game": 1})
    pause_hl = True
    done = False

    def status():
        mode = f"live x{throttle.speed:g}" if throttle.gate.is_set() \
            else "PAUSED"
        return f" {mode} |{LIVE_HELP}"

    console = Console()
    with keys.raw_terminal(), Live(render_frame(view, status()),
                                   console=console, screen=True,
                                   auto_refresh=False) as live:
        while True:
            done = _drain(q, view) or done
            view.cursor = len(view.records) - 1
            if pause_hl and throttle.gate.is_set():
                tail = view.visible_events(n=1)
                if tail and tail[-1]["kind"] in HIGHLIGHTS:
                    throttle.gate.clear()
            live.update(render_frame(view, status()), refresh=True)
            if done and q.empty():
                live.update(render_frame(
                    view, " game over - any key to exit "), refresh=True)
                keys.read_key(None)
                return
            key = keys.read_key(0.1)
            if key in ("q", "\x03"):
                return
            if key in ("p", " "):
                if throttle.gate.is_set():
                    throttle.gate.clear()
                else:
                    throttle.gate.set()
            elif key == "+":
                throttle.speed = min(8.0, throttle.speed * 2)
            elif key == "-":
                throttle.speed = max(0.25, throttle.speed / 2)
            elif key == "h":
                pause_hl = not pause_hl


def _plain_loop(q):
    while True:
        rec = q.get()
        if rec.get("t") == "eof":
            return
        if rec.get("t") == "e":
            print(f"T{rec['turn']:>2} [{rec['kind']}] {format_event(rec)}")
