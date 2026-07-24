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
"""

from __future__ import annotations

#: event kinds worth pausing on during autoplay
HIGHLIGHTS = {"counter", "player_loses", "eliminated"}


def _perm(game, obj) -> dict:
    ch = obj.chars(game)
    d = {"name": ch.name or "(unnamed)",
         "types": sorted(ch.types),
         "tapped": bool(obj.tapped)}
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
    if obj.attacking is not None:
        t = obj.attacking
        d["attacking"] = t.name if hasattr(t, "life") else t.base.name
    if obj.blocking:
        d["blocking"] = [b.base.name for b in obj.blocking]
    return d


def _stack_entry(item) -> str:
    if item.is_spell:
        name = item.obj.base.name
    else:
        name = f"ability of {item.source.base.name}"
    return f"{name} ({item.controller.name})"


def snapshot(game, seq: int) -> dict:
    """Full board state of a mtgrules Game."""
    cmd_names = {}
    for p in game.players:
        if p.commander_obj is not None:
            cmd_names[p.commander_obj.id] = p.commander_obj.base.name
    players = []
    for p in game.players:
        cmd = p.commander_obj
        players.append({
            "name": p.name,
            "life": p.life,
            "lost": p.lose_reason,
            "hand": len(p.hand),
            "library": len(p.library),
            "graveyard": len(p.graveyard),
            "exile": len(p.exile),
            "energy": p.energy,
            "commander_in_command": bool(cmd and cmd.zone == "command"),
            "cmd_damage": {cmd_names.get(src, str(src)): n
                           for src, n in p.commander_damage.items() if n},
            "battlefield": [_perm(game, o) for o in p.battlefield],
        })
    return {"t": "s", "seq": seq, "turn": game.turn, "phase": game.phase,
            "active": game.active_player.name,
            "stack": [_stack_entry(i) for i in game.stack],
            "players": players}


def event(game, seq: int, kind: str, data: dict) -> dict:
    return {"t": "e", "seq": seq, "turn": game.turn, "phase": game.phase,
            "kind": kind, "data": data}
