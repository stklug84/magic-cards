"""JSON-serializable visualization records.

Three record types flow from the Recorder to a sink (JSONL file or an
in-memory queue):

  {"t": "game", "game": N, "seed": S, "players": [...]}   game header
  {"t": "e", "seq", "turn", "phase", "kind", "data"}      engine log event
  {"t": "s", "seq", "turn", "phase", "active", "stack",
   "players": [...]}                                       full snapshot
  {"t": "end", "winner", "reason", "turns"}                game footer

Snapshots are complete board states; the TUI renders the latest snapshot
at or before the cursor and shows events as a scrolling log.

The *Like Protocols describe the slice of the mtgrules engine (Game,
Player, GameObject, StackItem) that the recorder reads, so mtgviz stays
structurally typed against the engine without importing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

#: one recorder record (see the shapes above) after JSON round-tripping
type Record = dict[str, Any]
#: recorder output callback (JSONL writer, queue.put, list.append, ...)
type RecordSink = Callable[[Record], None]

#: event kinds worth pausing on during autoplay
HIGHLIGHTS = {"counter", "player_loses", "eliminated"}


# ------------------------------------------------------- engine protocols
class CharacteristicsLike(Protocol):
    """Computed characteristics of a game object (CR 109.3)."""

    name: str
    types: set[str]
    power: int | None
    toughness: int | None


class ManaPoolLike(Protocol):
    """A player's mana pool exposing its floating-mana counts (CR 106.4)."""

    mana: Mapping[str, int]


class ObjectLike(Protocol):
    """A card, token, or spell on the battlefield/stack (CR 109.1)."""

    id: int
    base: CharacteristicsLike
    zone: str
    tapped: bool
    is_token: bool
    commander: bool
    damage: int
    counters: Mapping[str, int]
    attacking: PlayerLike | ObjectLike | None
    blocking: Sequence[ObjectLike]

    def chars(self, game: GameLike) -> CharacteristicsLike:
        """Compute current characteristics via the layer system (CR 613)."""


@runtime_checkable
class PlayerLike(Protocol):
    """The slice of a mtgrules Player a snapshot reads (CR 102)."""

    name: str
    life: int
    lose_reason: str
    hand: Sequence[ObjectLike]
    library: Sequence[ObjectLike]
    graveyard: Sequence[ObjectLike]
    exile: Sequence[ObjectLike]
    battlefield: Sequence[ObjectLike]
    energy: int
    poison: int
    mana_pool: ManaPoolLike | None
    commander_obj: ObjectLike | None
    commander_damage: Mapping[int, int]


class StackItemLike(Protocol):
    """A spell or ability on the stack (CR 405.1-405.2)."""

    is_spell: bool
    obj: ObjectLike
    source: ObjectLike
    controller: PlayerLike


class GameLike(Protocol):
    """The slice of a mtgrules Game the recorder reads."""

    turn: int
    phase: str
    players: Sequence[PlayerLike]
    active_player: PlayerLike
    stack: Sequence[StackItemLike]


# ------------------------------------------------------------- records
def _perm(game: GameLike, obj: ObjectLike) -> Record:
    """One battlefield permanent as a display record."""
    ch = obj.chars(game)
    d: Record = {
        "name": ch.name or "(unnamed)",
        "types": sorted(ch.types),
        "tapped": bool(obj.tapped),
    }
    if obj.is_token:
        d["token"] = True
    if obj.commander:
        d["commander"] = True
    if "Creature" in ch.types:
        d["pt"] = f"{ch.power or 0}/{ch.toughness or 0}"
        if obj.damage:
            d["damage"] = obj.damage
    if obj.counters:
        d["counters"] = {k: v for k, v in obj.counters.items() if v}
    target = obj.attacking
    if target is not None:
        # attack target is a player or a planeswalker (CR 508.1)
        d["attacking"] = (
            target.name if isinstance(target, PlayerLike) else target.base.name
        )
    if obj.blocking:
        d["blocking"] = [b.base.name for b in obj.blocking]
    return d


def _stack_entry(item: StackItemLike) -> str:
    """Display line for one stack item."""
    if item.is_spell:
        name = item.obj.base.name
    else:
        name = f"ability of {item.source.base.name}"
    return f"{name} ({item.controller.name})"


def _player_state(game: GameLike, p: PlayerLike, cmd_names: dict[int, str]) -> Record:
    """One player's complete snapshot record."""
    cmd = p.commander_obj
    pool_mana: Mapping[str, int] = p.mana_pool.mana if p.mana_pool is not None else {}
    pool = "".join(sym * n for sym, n in sorted(pool_mana.items()) if n > 0)
    return {
        "name": p.name,
        "life": p.life,
        "lost": p.lose_reason,
        "hand": len(p.hand),
        "library": len(p.library),
        "graveyard": len(p.graveyard),
        "exile": len(p.exile),
        "energy": p.energy,
        "poison": p.poison,
        "mana_pool": pool,
        "commander_in_command": bool(cmd and cmd.zone == "command"),
        "cmd_damage": {
            cmd_names.get(src, str(src)): n
            for src, n in p.commander_damage.items()
            if n
        },
        "battlefield": [_perm(game, o) for o in p.battlefield],
    }


def snapshot(game: GameLike, seq: int) -> Record:
    """Full board state of a mtgrules Game."""
    cmd_names: dict[int, str] = {}
    for p in game.players:
        if p.commander_obj is not None:
            cmd_names[p.commander_obj.id] = p.commander_obj.base.name
    return {
        "t": "s",
        "seq": seq,
        "turn": game.turn,
        "phase": game.phase,
        "active": game.active_player.name,
        "stack": [_stack_entry(i) for i in game.stack],
        "players": [_player_state(game, p, cmd_names) for p in game.players],
    }


def event(game: GameLike, seq: int, kind: str, data: Mapping[str, object]) -> Record:
    """One engine log event as a display record."""
    return {
        "t": "e",
        "seq": seq,
        "turn": game.turn,
        "phase": game.phase,
        "kind": kind,
        "data": data,
    }
