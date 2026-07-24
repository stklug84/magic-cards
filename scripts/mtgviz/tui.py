"""rich-based TUI: renders recorder streams (replay and live).

The board is always the latest snapshot at or before the cursor; events
form a scrolling, color-coded log. Requires the optional 'rich' package.
"""

from __future__ import annotations

import time
from collections import Counter

from . import keys
from .schema import HIGHLIGHTS

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAVE_RICH = True
except ImportError:                                  # pragma: no cover
    HAVE_RICH = False


# --------------------------------------------------------------- view state
class ViewState:
    """Cursor over a list of schema records (events + snapshots)."""

    def __init__(self, records=None, meta=None):
        self.records = records if records is not None else []
        self.meta = meta or {}
        self.cursor = -1          # index of last visible record
        self.result = None        # {"t": "end", ...} once known

    def append(self, rec):
        if rec.get("t") == "end":
            self.result = rec
        else:
            self.records.append(rec)

    # ---- queries ---------------------------------------------------------
    def snapshot(self):
        for i in range(min(self.cursor, len(self.records) - 1), -1, -1):
            if self.records[i]["t"] == "s":
                return self.records[i]
        return None

    def visible_events(self, n=14, player=None):
        out = []
        for i in range(min(self.cursor, len(self.records) - 1), -1, -1):
            rec = self.records[i]
            if rec["t"] != "e":
                continue
            if player and player not in _event_players(rec):
                continue
            out.append(rec)
            if len(out) >= n:
                break
        return list(reversed(out))

    def at_end(self):
        return self.cursor >= len(self.records) - 1

    # ---- navigation ------------------------------------------------------
    def step(self, direction=1):
        """Move to the next/previous *event* record."""
        i = self.cursor + direction
        while 0 <= i < len(self.records):
            if self.records[i]["t"] == "e" or direction > 0 \
                    and i == len(self.records) - 1:
                self.cursor = i
                return True
            i += direction
        self.cursor = max(-1, min(self.cursor + direction,
                                  len(self.records) - 1))
        return False

    def seek_event(self, direction=1, match=None):
        """Advance until an event satisfying match(rec) (e.g. next phase)."""
        i = self.cursor + direction
        while 0 <= i < len(self.records):
            rec = self.records[i]
            if rec["t"] == "e" and (match is None or match(rec)):
                self.cursor = i
                return True
            i += direction
        self.cursor = len(self.records) - 1 if direction > 0 else -1
        return False

    def next_phase(self, direction=1):
        return self.seek_event(direction,
                               lambda r: r["kind"] == "phase")

    def next_turn(self, direction=1):
        here = self._turn_at(self.cursor)
        return self.seek_event(
            direction, lambda r: r["kind"] == "phase"
            and r["data"].get("phase") == "untap"
            and (r["turn"] > here if direction > 0 else r["turn"] < here))

    def jump_turn(self, turn):
        self.cursor = -1
        self.seek_event(1, lambda r: r["kind"] == "phase" and
                        r["turn"] >= turn)

    def _turn_at(self, i):
        if 0 <= i < len(self.records):
            return self.records[i].get("turn", 0)
        return 0


def _event_players(rec):
    d = rec["data"]
    return {v for k, v in d.items()
            if k in ("who", "player", "target") and isinstance(v, str)}


# --------------------------------------------------------------- formatting
_EVENT_STYLE = {
    "phase": "dim", "turn": "bold", "draw": "dim",
    "cast": "cyan", "resolve": "blue", "trigger": "dim cyan",
    "activate": "cyan", "land": "green4", "token": "green",
    "attack": "yellow", "block": "gold3", "damage": "red",
    "life": "white", "dies": "red3", "counter": "magenta",
    "fizzle": "magenta", "player_loses": "bold red",
}


def format_event(rec) -> str:
    k, d = rec["kind"], rec["data"]
    if k == "phase":
        return f"-- T{rec['turn']} {d.get('phase')} ({d.get('who')}) --"
    if k == "turn":
        return f"== Turn {d.get('n')}: {d.get('who')} =="
    if k == "cast":
        extra = f" (X={d['x']})" if d.get("x") else ""
        cmd = " [commander]" if d.get("commander") else ""
        return f"{d.get('who')} casts {d.get('card')}{extra}{cmd}"
    if k == "resolve":
        return f"resolves: {d.get('what')}"
    if k == "trigger":
        return f"trigger: {d.get('what')} ({d.get('who')})"
    if k == "activate":
        return f"{d.get('who')} activates {d.get('source')}"
    if k == "land":
        t = " (tapped)" if d.get("tapped") else ""
        return f"{d.get('who')} plays {d.get('card')}{t}"
    if k == "token":
        pt = f" {d['pt']}" if d.get("pt") else ""
        return f"{d.get('who')} creates {d.get('name')}{pt} token"
    if k == "attack":
        return f"{d.get('card')} attacks {d.get('target')}"
    if k == "block":
        return f"{d.get('blocker')} blocks {d.get('attacker')}"
    if k == "damage":
        c = " (combat)" if d.get("combat") else ""
        return f"{d.get('src')} deals {d.get('n')} to {d.get('target')}{c}"
    if k == "life":
        return f"{d.get('who')} {d.get('delta'):+d} life " \
               f"-> {d.get('total')}"
    if k == "dies":
        t = " token" if d.get("token") else ""
        return f"{d.get('card')}{t} dies ({d.get('who')})"
    if k == "counter":
        return f"{d.get('spell')} ({d.get('who')}) is countered"
    if k == "fizzle":
        return f"fizzles: {d.get('what')}"
    if k == "draw":
        return f"{d.get('who')} draws"
    if k == "player_loses":
        return f"{d.get('who')} LOSES: {d.get('why')}"
    return f"{k} {d}"


def _life_style(life):
    if life > 30:
        return "bold green"
    if life > 10:
        return "bold yellow"
    return "bold red"


def _group_battlefield(perms):
    """Group identical battlefield entries: '3x Insect 1/1'."""
    groups = Counter()
    order = []
    for p in perms:
        key = (p["name"], p.get("pt"), p["tapped"],
               tuple(sorted((p.get("counters") or {}).items())),
               p.get("attacking"), tuple(p.get("blocking") or ()),
               p.get("token", False), p.get("commander", False),
               p.get("damage", 0))
        if key not in groups:
            order.append((key, p))
        groups[key] += 1
    return [(groups[key], p) for key, p in order]


def _perm_line(count, p) -> Text:
    t = Text()
    if count > 1:
        t.append(f"{count}x ", style="bold")
    style = "dim" if p["tapped"] else ""
    if p.get("commander"):
        t.append("* ", style="gold3")
    t.append(p["name"], style=style or ("italic" if p.get("token") else ""))
    if p.get("pt"):
        dmg = f"({p['damage']})" if p.get("damage") else ""
        t.append(f" {p['pt']}{dmg}", style="bold" if not p["tapped"]
                 else "dim")
    if p["tapped"]:
        t.append(" (T)", style="dim")
    for kind, n in (p.get("counters") or {}).items():
        t.append(f" [{kind}:{n}]", style="cyan")
    if p.get("attacking"):
        t.append(f" >>{p['attacking']}", style="yellow")
    if p.get("blocking"):
        t.append(f" ##{','.join(p['blocking'])}", style="gold3")
    return t


def _player_panel(pd, active, width_hint=40):
    head = Text()
    head.append(f"{pd['life']:>3} ", style=_life_style(pd["life"]))
    head.append(f"H:{pd['hand']} L:{pd['library']} G:{pd['graveyard']}",
                style="dim")
    if pd.get("energy"):
        head.append(f" E:{pd['energy']}", style="cyan")
    if pd.get("cmd_damage"):
        dmg = " ".join(f"{k[:14]}:{v}" for k, v in pd["cmd_damage"].items())
        head.append(f"  cmd<{dmg}", style="red")
    if pd.get("commander_in_command"):
        head.append("  [cmd zone]", style="gold3")
    lines = [head]
    lands, rest = [], []
    for count, p in _group_battlefield(pd["battlefield"]):
        (lands if "Land" in p.get("types", []) else rest).append((count, p))
    for count, p in rest:
        lines.append(_perm_line(count, p))
    if lands:
        summary = Text()
        n = sum(c for c, _ in lands)
        tapped = sum(c for c, p in lands if p["tapped"])
        summary.append(f"{n} lands ({n - tapped} untapped)", style="dim")
        lines.append(summary)
    title = Text(pd["name"][:28])
    if pd.get("lost"):
        title.stylize("strike red")
        lines.append(Text(f"LOST: {pd['lost']}", style="bold red"))
    border = "bright_yellow" if active and not pd.get("lost") else (
        "red" if pd.get("lost") else "grey42")
    return Panel(Group(*lines), title=title,
                 subtitle="ACTIVE" if active else None,
                 border_style=border, padding=(0, 1))


def render_frame(view: ViewState, status: str, log_filter=None):
    snap = view.snapshot()
    if snap is None:
        return Panel("waiting for first snapshot ...")
    # header
    header = Text()
    header.append(f" Turn {snap['turn']} ", style="bold white on grey23")
    header.append(f" {snap['phase']} ", style="bold cyan")
    header.append(f" active: {snap['active']} ", style="yellow")
    if view.meta.get("seed") is not None:
        header.append(f" seed {view.meta['seed']}"
                      f" game {view.meta.get('game', 1)} ", style="dim")
    if view.result and view.at_end():        # no spoilers mid-replay
        header.append(f"  WINNER: {view.result['winner']}"
                      f" ({view.result['reason']}) ", style="bold green")
    # player grid
    players = snap["players"]
    grid = Table.grid(expand=True)
    per_row = 2 if len(players) > 1 else 1
    for _ in range(per_row):
        grid.add_column(ratio=1)
    row = []
    for pd in players:
        row.append(_player_panel(pd, pd["name"] == snap["active"]))
        if len(row) == per_row:
            grid.add_row(*row)
            row = []
    if row:
        grid.add_row(*row, *[Text("")] * (per_row - len(row)))
    # stack + combat
    mid = Table.grid(expand=True)
    mid.add_column(ratio=1)
    mid.add_column(ratio=1)
    stack_lines = [Text(f"{len(snap['stack']) - i}. {s}",
                        style="bold blue" if i == 0 else "blue")
                   for i, s in enumerate(reversed(snap["stack"]))] \
        or [Text("(empty)", style="dim")]
    combat_lines = []
    for pd in players:
        for count, p in _group_battlefield(pd["battlefield"]):
            if p.get("attacking"):
                n = f"{count}x " if count > 1 else ""
                blocked = []
                for qd in players:
                    for c2, q in _group_battlefield(qd["battlefield"]):
                        if p["name"] in (q.get("blocking") or []):
                            blocked.append(q["name"])
                arrow = f"{n}{p['name']} {p.get('pt', '')} -> " \
                        f"{p['attacking']}"
                if blocked:
                    arrow += f"  blocked by {', '.join(blocked)}"
                    style = "gold3"
                else:
                    arrow += "  unblocked"
                    style = "yellow"
                combat_lines.append(Text(arrow, style=style))
    combat_lines = combat_lines or [Text("(no combat)", style="dim")]
    mid.add_row(Panel(Group(*stack_lines), title="STACK (top first)",
                      border_style="blue", padding=(0, 1)),
                Panel(Group(*combat_lines), title="COMBAT",
                      border_style="yellow", padding=(0, 1)))
    # event log
    log_lines = []
    for rec in view.visible_events(n=12, player=log_filter):
        style = _EVENT_STYLE.get(rec["kind"], "")
        line = Text()
        line.append(f"T{rec['turn']:>2} ", style="dim")
        line.append(format_event(rec), style=style)
        log_lines.append(line)
    ftitle = "LOG" + (f" [{log_filter}]" if log_filter else "")
    log_panel = Panel(Group(*(log_lines or [Text("...")])), title=ftitle,
                      border_style="grey42", padding=(0, 1))
    footer = Text(status, style="black on grey66")
    return Group(header, grid, mid, log_panel, footer)


# --------------------------------------------------------------- replay app
REPLAY_HELP = (" space:event  n/N:phase  t/T:turn  arrows:event/turn "
               " g<turn>:jump  a:auto  +/-:speed  h:pause-on-highlight "
               " f:filter  q:quit ")


def run_replay(records, meta, result=None):
    import sys
    if not HAVE_RICH or not sys.stdin.isatty():
        from .replay import plain_replay
        plain_replay(records, meta, result)
        return
    view = ViewState(records, meta)
    view.result = result
    view.step(1)
    console = Console()
    autoplay = False
    speed = 1.0
    pause_hl = True
    log_filter = None
    player_names = [p["name"] for p in (view.snapshot() or {}).get(
        "players", [])]
    goto = None

    def status():
        mode = f"auto x{speed:g}" if autoplay else "paused"
        pos = f"{max(view.cursor, 0)}/{len(view.records)}"
        g = f"  goto: {goto}_" if goto is not None else ""
        return f" {mode}  {pos}{g} |{REPLAY_HELP}"

    with keys.raw_terminal(), Live(render_frame(view, status()),
                                   console=console, screen=True,
                                   auto_refresh=False) as live:
        while True:
            live.update(render_frame(view, status(), log_filter),
                        refresh=True)
            key = keys.read_key(0.35 / speed if autoplay else None)
            if key is None:                       # autoplay tick
                moved = view.step(1)
                if not moved and view.at_end():
                    autoplay = False
                elif pause_hl and view.records[view.cursor]["t"] == "e" \
                        and view.records[view.cursor]["kind"] in HIGHLIGHTS:
                    autoplay = False
                continue
            if goto is not None:
                if key.isdigit():
                    goto += key
                    continue
                if key in ("\r", "\n") and goto:
                    view.jump_turn(int(goto))
                goto = None
                continue
            if key in ("q", "\x03"):
                return
            elif key == " ":
                view.step(1)
            elif key in ("right",):
                view.step(1)
            elif key in ("left", "b"):
                view.step(-1)
            elif key == "n":
                view.next_phase(1)
            elif key == "N":
                view.next_phase(-1)
            elif key in ("t", "down"):
                view.next_turn(1)
            elif key in ("T", "up"):
                view.next_turn(-1)
            elif key == "a":
                autoplay = not autoplay
            elif key == "+":
                speed = min(8.0, speed * 2)
            elif key == "-":
                speed = max(0.25, speed / 2)
            elif key == "h":
                pause_hl = not pause_hl
            elif key == "f":
                opts = [None] + player_names
                log_filter = opts[(opts.index(log_filter) + 1) % len(opts)]
            elif key == "g":
                goto = ""
            elif key == "G":
                view.cursor = len(view.records) - 1


def print_plain_frame(view, console_print=print):
    """One-frame text dump (fallback when rich is unavailable)."""
    snap = view.snapshot()
    if snap is None:
        return
    console_print(f"=== Turn {snap['turn']} {snap['phase']} "
                  f"(active: {snap['active']}) ===")
    for pd in snap["players"]:
        lost = f"  LOST: {pd['lost']}" if pd.get("lost") else ""
        console_print(f"  {pd['name']}: {pd['life']} life, "
                      f"hand {pd['hand']}, lib {pd['library']}, "
                      f"gy {pd['graveyard']}{lost}")
        for count, p in _group_battlefield(pd["battlefield"]):
            n = f"{count}x " if count > 1 else ""
            pt = f" {p['pt']}" if p.get("pt") else ""
            tap = " (T)" if p["tapped"] else ""
            console_print(f"      {n}{p['name']}{pt}{tap}")
    if snap["stack"]:
        console_print("  stack: " + " <- ".join(snap["stack"]))
