"""Load recorded viz JSONL files and replay them (TUI or plain text)."""

from __future__ import annotations

import json
import sys

from .tui import HAVE_RICH, ViewState, format_event, print_plain_frame


def load_games(path):
    """Split a --viz-file JSONL into per-game streams.
    Returns {game_no: {"meta": {...}, "records": [...], "result": {...}}}."""
    games = {}
    current = None
    try:
        fh = open(path, encoding="utf-8")
    except OSError as exc:
        sys.exit(f"cannot open replay file: {exc}")
    with fh:
        for line in fh:
            line = line.strip()
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
    return games


def replay_file(path, game=1):
    games = load_games(path)
    if not games:
        sys.exit(f"{path}: no recorded games found (record with "
                 f"--viz-file)")
    if game not in games:
        sys.exit(f"{path}: no game {game}; available: "
                 f"{', '.join(map(str, sorted(games)))}")
    g = games[game]
    if not HAVE_RICH:
        print("note: 'rich' not installed (pip install rich); "
              "using the plain step viewer", file=sys.stderr)
    from .tui import run_replay
    run_replay(g["records"], g["meta"], g["result"])


def plain_replay(records, meta, result=None):
    """Fallback viewer: prints one phase per <Enter>, 'q' to quit."""
    view = ViewState(records, meta)
    print(f"replaying game {meta.get('game', 1)} (seed "
          f"{meta.get('seed')}), {len(records)} records; <Enter> for next "
          f"phase, 'a<Enter>' for all, 'q<Enter>' to quit")
    auto = False
    while not view.at_end():
        start = view.cursor
        view.next_phase(1)
        for i in range(start + 1, view.cursor + 1):
            rec = view.records[i]
            if rec["t"] == "e" and rec["kind"] != "phase":
                print(f"    {format_event(rec)}")
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
        print(f"=== winner: {result['winner']} ({result['reason']}) "
              f"after {result['turns']} turns ===")
