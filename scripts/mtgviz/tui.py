"""rich-based TUI rendering: view state, event formatting, board frames.

The board is always the latest snapshot at or before the cursor; events
form a scrolling, color-coded log. Requires the optional 'rich' package;
the replay/live apps (replay.py, live.py) fall back to print_plain_frame
without it.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mtgviz.schema import Record

try:
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    HAVE_RICH = True
except ImportError:  # pragma: no cover
    HAVE_RICH = False


# --------------------------------------------------------------- view state
class ViewState:
    """Cursor over a list of schema records (events + snapshots)."""

    def __init__(
        self,
        records: list[Record] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Wrap *records* (shared, appendable) and the game-header *meta*."""
        self.records = records if records is not None else []
        self.meta = meta or {}
        self.cursor = -1  # index of last visible record
        self.result: Record | None = None  # {"t": "end", ...} once known

    def append(self, rec: Record) -> None:
        """Add a live record; the end record is kept aside as the result."""
        if rec.get("t") == "end":
            self.result = rec
        else:
            self.records.append(rec)

    # ---- queries ---------------------------------------------------------
    def snapshot(self) -> Record | None:
        """Latest snapshot at or before the cursor."""
        for i in range(min(self.cursor, len(self.records) - 1), -1, -1):
            if self.records[i]["t"] == "s":
                return self.records[i]
        return None

    def visible_events(self, n: int = 14, player: str | None = None) -> list[Record]:
        """Collect the last *n* events up to the cursor (per player, opt.)."""
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

    def at_end(self) -> bool:
        """Whether the cursor is at (or past) the last record."""
        return self.cursor >= len(self.records) - 1

    # ---- navigation ------------------------------------------------------
    def step(self, direction: int = 1) -> bool:
        """Move to the next/previous *event* record."""
        i = self.cursor + direction
        while 0 <= i < len(self.records):
            if self.records[i]["t"] == "e" or (
                direction > 0 and i == len(self.records) - 1
            ):
                self.cursor = i
                return True
            i += direction
        self.cursor = max(-1, min(self.cursor + direction, len(self.records) - 1))
        return False

    def seek_event(
        self,
        direction: int = 1,
        match: Callable[[Record], bool] | None = None,
    ) -> bool:
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

    def next_phase(self, direction: int = 1) -> bool:
        """Seek to the next/previous phase boundary."""
        return self.seek_event(direction, lambda r: r["kind"] == "phase")

    def next_turn(self, direction: int = 1) -> bool:
        """Seek to the untap step of the next/previous turn."""
        here = self._turn_at(self.cursor)
        return self.seek_event(
            direction,
            lambda r: (
                r["kind"] == "phase"
                and r["data"].get("phase") == "untap"
                and (r["turn"] > here if direction > 0 else r["turn"] < here)
            ),
        )

    def jump_turn(self, turn: int) -> None:
        """Jump to the first phase event of *turn* (from the start)."""
        self.cursor = -1
        self.seek_event(1, lambda r: r["kind"] == "phase" and r["turn"] >= turn)

    def _turn_at(self, i: int) -> int:
        if 0 <= i < len(self.records):
            return int(self.records[i].get("turn", 0))
        return 0


def _event_players(rec: Record) -> set[str]:
    d = rec["data"]
    return {
        v
        for k, v in d.items()
        if k in ("who", "player", "target") and isinstance(v, str)
    }


# --------------------------------------------------------------- formatting
_EVENT_STYLE = {
    "phase": "dim",
    "turn": "bold",
    "draw": "dim",
    "cast": "cyan",
    "resolve": "blue",
    "trigger": "dim cyan",
    "activate": "cyan",
    "land": "green4",
    "token": "green",
    "attack": "yellow",
    "block": "gold3",
    "damage": "red",
    "life": "white",
    "dies": "red3",
    "counter": "magenta",
    "fizzle": "magenta",
    "player_loses": "bold red",
}


def _fmt_cast(_rec: Record, d: dict[str, Any]) -> str:
    extra = f" (X={d['x']})" if d.get("x") else ""
    cmd = " [commander]" if d.get("commander") else ""
    return f"{d.get('who')} casts {d.get('card')}{extra}{cmd}"


def _fmt_land(_rec: Record, d: dict[str, Any]) -> str:
    t = " (tapped)" if d.get("tapped") else ""
    return f"{d.get('who')} plays {d.get('card')}{t}"


def _fmt_token(_rec: Record, d: dict[str, Any]) -> str:
    pt = f" {d['pt']}" if d.get("pt") else ""
    return f"{d.get('who')} creates {d.get('name')}{pt} token"


def _fmt_damage(_rec: Record, d: dict[str, Any]) -> str:
    c = " (combat)" if d.get("combat") else ""
    return f"{d.get('src')} deals {d.get('n')} to {d.get('target')}{c}"


def _fmt_life(_rec: Record, d: dict[str, Any]) -> str:
    return f"{d.get('who')} {d.get('delta'):+d} life -> {d.get('total')}"


def _fmt_dies(_rec: Record, d: dict[str, Any]) -> str:
    t = " token" if d.get("token") else ""
    return f"{d.get('card')}{t} dies ({d.get('who')})"


#: event kind -> log-line renderer (unknown kinds fall back to "kind data")
_FORMATTERS: dict[str, Callable[[Record, dict[str, Any]], str]] = {
    "phase": lambda r, d: f"-- T{r['turn']} {d.get('phase')} ({d.get('who')}) --",
    "turn": lambda _r, d: f"== Turn {d.get('n')}: {d.get('who')} ==",
    "cast": _fmt_cast,
    "resolve": lambda _r, d: f"resolves: {d.get('what')}",
    "trigger": lambda _r, d: f"trigger: {d.get('what')} ({d.get('who')})",
    "activate": lambda _r, d: f"{d.get('who')} activates {d.get('source')}",
    "land": _fmt_land,
    "token": _fmt_token,
    "attack": lambda _r, d: f"{d.get('card')} attacks {d.get('target')}",
    "block": lambda _r, d: f"{d.get('blocker')} blocks {d.get('attacker')}",
    "damage": _fmt_damage,
    "life": _fmt_life,
    "dies": _fmt_dies,
    "counter": lambda _r, d: f"{d.get('spell')} ({d.get('who')}) is countered",
    "fizzle": lambda _r, d: f"fizzles: {d.get('what')}",
    "draw": lambda _r, d: f"{d.get('who')} draws",
    "player_loses": lambda _r, d: f"{d.get('who')} LOSES: {d.get('why')}",
}


def format_event(rec: Record) -> str:
    """Render one engine event record as a log line."""
    fmt = _FORMATTERS.get(rec["kind"])
    if fmt is None:
        return f"{rec['kind']} {rec['data']}"
    return fmt(rec, rec["data"])


#: life-total display thresholds against the 40-life start (CR 903.7)
_LIFE_COMFORTABLE = 30
_LIFE_DANGER = 10


def _life_style(life: int) -> str:
    if life > _LIFE_COMFORTABLE:
        return "bold green"
    if life > _LIFE_DANGER:
        return "bold yellow"
    return "bold red"


def _group_battlefield(perms: Iterable[Record]) -> list[tuple[int, Record]]:
    """Group identical battlefield entries: '3x Insect 1/1'."""
    groups: Counter[tuple[object, ...]] = Counter()
    order = []
    for p in perms:
        key = (
            p["name"],
            p.get("pt"),
            p["tapped"],
            tuple(sorted((p.get("counters") or {}).items())),
            p.get("attacking"),
            tuple(p.get("blocking") or ()),
            p.get("token", False),
            p.get("commander", False),
            p.get("damage", 0),
        )
        if key not in groups:
            order.append((key, p))
        groups[key] += 1
    return [(groups[key], p) for key, p in order]


def _perm_line(count: int, p: Record) -> Text:
    t = Text()
    if count > 1:
        t.append(f"{count}x ", style="bold")
    style = "dim" if p["tapped"] else ""
    if p.get("commander"):
        t.append("* ", style="gold3")
    t.append(p["name"], style=style or ("italic" if p.get("token") else ""))
    if p.get("pt"):
        dmg = f"({p['damage']})" if p.get("damage") else ""
        t.append(f" {p['pt']}{dmg}", style="bold" if not p["tapped"] else "dim")
    if p["tapped"]:
        t.append(" (T)", style="dim")
    for kind, n in (p.get("counters") or {}).items():
        t.append(f" [{kind}:{n}]", style="cyan")
    if p.get("attacking"):
        t.append(f" >>{p['attacking']}", style="yellow")
    if p.get("blocking"):
        t.append(f" ##{','.join(p['blocking'])}", style="gold3")
    return t


#: battlefield display filters, cycled with the 'c' key
BF_FILTERS = ("all", "creatures", "nonland")


def _bf_visible(p: Record, bf_filter: str) -> bool:
    types = p.get("types", [])
    if bf_filter == "creatures":
        return "Creature" in types
    if bf_filter == "nonland":
        return "Land" not in types
    return True


def _panel_header(pd: Record) -> Text:
    """Life / zone counts / pools / commander-damage line of a panel."""
    head = Text()
    head.append(f"{pd['life']:>3} ", style=_life_style(pd["life"]))
    head.append(f"H:{pd['hand']} L:{pd['library']} G:{pd['graveyard']}", style="dim")
    if pd.get("poison"):
        head.append(f" P:{pd['poison']}", style="bold green4")
    if pd.get("energy"):
        head.append(f" E:{pd['energy']}", style="cyan")
    if pd.get("mana_pool"):
        head.append(f" pool:{pd['mana_pool']}", style="bold magenta")
    if pd.get("cmd_damage"):
        dmg = " ".join(f"{k[:14]}:{v}" for k, v in pd["cmd_damage"].items())
        head.append(f"  cmd<{dmg}", style="red")
    if pd.get("commander_in_command"):
        head.append("  [cmd zone]", style="gold3")
    return head


def _battlefield_lines(
    pd: Record,
    bf_filter: str,
    max_rows: int | None,
) -> list[Text]:
    """Build grouped battlefield rows plus a one-line land summary."""
    lines = []
    lands: list[tuple[int, Record]] = []
    rest: list[tuple[int, Record]] = []
    for count, p in _group_battlefield(pd["battlefield"]):
        if not _bf_visible(p, bf_filter):
            continue
        (lands if "Land" in p.get("types", []) else rest).append((count, p))
    # busiest boards first when space is tight (4-player pods)
    if max_rows is not None and len(rest) > max_rows:
        shown, hidden = rest[: max_rows - 1], rest[max_rows - 1 :]
        for count, p in shown:
            lines.append(_perm_line(count, p))
        lines.append(Text(f"... +{sum(c for c, _ in hidden)} more", style="dim"))
    else:
        for count, p in rest:
            lines.append(_perm_line(count, p))
    if lands:
        summary = Text()
        n = sum(c for c, _ in lands)
        tapped = sum(c for c, p in lands if p["tapped"])
        summary.append(f"{n} lands ({n - tapped} untapped)", style="dim")
        lines.append(summary)
    return lines


def _player_panel(
    pd: Record,
    *,
    active: bool,
    bf_filter: str = "all",
    max_rows: int | None = None,
) -> Panel:
    lines = [_panel_header(pd), *_battlefield_lines(pd, bf_filter, max_rows)]
    title = Text(pd["name"][:28])
    if pd.get("lost"):
        title.stylize("strike red")
        lines.append(Text(f"LOST: {pd['lost']}", style="bold red"))
    border = (
        "bright_yellow"
        if active and not pd.get("lost")
        else ("red" if pd.get("lost") else "grey42")
    )
    return Panel(
        Group(*lines),
        title=title,
        subtitle="ACTIVE" if active else None,
        border_style=border,
        padding=(0, 1),
    )


#: player panels per grid row (2x2 layout for pods)
_GRID_COLS = 2
#: battlefield rows per panel before truncation in 3-4 player pods
_PANEL_MAX_ROWS = 8


def _frame_header(view: ViewState, snap: Record, bf_filter: str) -> Text:
    """Turn/phase/active header, incl. seed and (at the end) the winner."""
    header = Text()
    header.append(f" Turn {snap['turn']} ", style="bold white on grey23")
    header.append(f" {snap['phase']} ", style="bold cyan")
    header.append(f" active: {snap['active']} ", style="yellow")
    if view.meta.get("seed") is not None:
        header.append(
            f" seed {view.meta['seed']} game {view.meta.get('game', 1)} ",
            style="dim",
        )
    if view.result and view.at_end():  # no spoilers mid-replay
        header.append(
            f"  WINNER: {view.result['winner']} ({view.result['reason']}) ",
            style="bold green",
        )
    if bf_filter != "all":
        header.append(f" [battlefield: {bf_filter}] ", style="magenta")
    return header


def _players_grid(snap: Record, bf_filter: str) -> Table:
    """Player grid (2x2 for pods; compact panels when 3-4 players)."""
    players = snap["players"]
    grid = Table.grid(expand=True)
    per_row = _GRID_COLS if len(players) > 1 else 1
    max_rows = _PANEL_MAX_ROWS if len(players) > _GRID_COLS else None
    for _ in range(per_row):
        grid.add_column(ratio=1)
    row = []
    for pd in players:
        row.append(
            _player_panel(
                pd,
                active=pd["name"] == snap["active"],
                bf_filter=bf_filter,
                max_rows=max_rows,
            ),
        )
        if len(row) == per_row:
            grid.add_row(*row)
            row = []
    if row:
        grid.add_row(*row, *[Text("")] * (per_row - len(row)))
    return grid


def _combat_lines(players: list[Record]) -> list[Text]:
    """Attack arrows with blocker annotations for the COMBAT panel."""
    lines = []
    for pd in players:
        for count, p in _group_battlefield(pd["battlefield"]):
            if not p.get("attacking"):
                continue
            n = f"{count}x " if count > 1 else ""
            blocked = []
            for qd in players:
                for _c2, q in _group_battlefield(qd["battlefield"]):
                    if p["name"] in (q.get("blocking") or []):
                        blocked.append(q["name"])
            arrow = f"{n}{p['name']} {p.get('pt', '')} -> {p['attacking']}"
            if blocked:
                arrow += f"  blocked by {', '.join(blocked)}"
                style = "gold3"
            else:
                arrow += "  unblocked"
                style = "yellow"
            lines.append(Text(arrow, style=style))
    return lines


def _stack_combat_row(snap: Record) -> Table:
    """STACK and COMBAT panels side by side."""
    mid = Table.grid(expand=True)
    mid.add_column(ratio=1)
    mid.add_column(ratio=1)
    stack_lines = [
        Text(f"{len(snap['stack']) - i}. {s}", style="bold blue" if i == 0 else "blue")
        for i, s in enumerate(reversed(snap["stack"]))
    ] or [Text("(empty)", style="dim")]
    combat_lines = _combat_lines(snap["players"]) or [Text("(no combat)", style="dim")]
    mid.add_row(
        Panel(
            Group(*stack_lines),
            title="STACK (top first)",
            border_style="blue",
            padding=(0, 1),
        ),
        Panel(
            Group(*combat_lines),
            title="COMBAT",
            border_style="yellow",
            padding=(0, 1),
        ),
    )
    return mid


def _log_panel(view: ViewState, log_filter: str | None) -> Panel:
    """Scrolling, color-coded event log."""
    log_lines = []
    for rec in view.visible_events(n=12, player=log_filter):
        style = _EVENT_STYLE.get(rec["kind"], "")
        line = Text()
        line.append(f"T{rec['turn']:>2} ", style="dim")
        line.append(format_event(rec), style=style)
        log_lines.append(line)
    ftitle = "LOG" + (f" [{log_filter}]" if log_filter else "")
    return Panel(
        Group(*(log_lines or [Text("...")])),
        title=ftitle,
        border_style="grey42",
        padding=(0, 1),
    )


def render_frame(
    view: ViewState,
    status: str,
    log_filter: str | None = None,
    bf_filter: str = "all",
) -> Group | Panel:
    """Render one full TUI frame: header, boards, stack/combat, log."""
    snap = view.snapshot()
    if snap is None:
        return Panel("waiting for first snapshot ...")
    return Group(
        _frame_header(view, snap, bf_filter),
        _players_grid(snap, bf_filter),
        _stack_combat_row(snap),
        _log_panel(view, log_filter),
        Text(status, style="black on grey66"),
    )


def print_plain_frame(
    view: ViewState,
    console_print: Callable[[str], None] = print,
) -> None:
    """One-frame text dump (fallback when rich is unavailable)."""
    snap = view.snapshot()
    if snap is None:
        return
    console_print(
        f"=== Turn {snap['turn']} {snap['phase']} (active: {snap['active']}) ===",
    )
    for pd in snap["players"]:
        lost = f"  LOST: {pd['lost']}" if pd.get("lost") else ""
        console_print(
            f"  {pd['name']}: {pd['life']} life, "
            f"hand {pd['hand']}, lib {pd['library']}, "
            f"gy {pd['graveyard']}{lost}",
        )
        for count, p in _group_battlefield(pd["battlefield"]):
            n = f"{count}x " if count > 1 else ""
            pt = f" {p['pt']}" if p.get("pt") else ""
            tap = " (T)" if p["tapped"] else ""
            console_print(f"      {n}{p['name']}{pt}{tap}")
    if snap["stack"]:
        console_print("  stack: " + " <- ".join(snap["stack"]))
