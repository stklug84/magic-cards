"""Combat: attacker selection, evasion-aware multi-blocking, trample,
deathtouch, wither, commander damage, spacecraft station attacks."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import effects
from .state import CMD_DAMAGE_LETHAL


@dataclass
class Unit:
    power: int
    toughness: int
    keywords: frozenset
    is_token: bool
    ref: object                       # Permanent or TokenGroup
    commander: bool = False
    blockers: list = field(default_factory=list)


def _kw(unit, kw):
    return kw in unit.keywords


def gather_attackers(game, pl, dfn):
    kulrath = any(o.has("kulrath_lock")
                  for o in game.opponents_of(pl) if not o.eliminated)
    units = []
    for perm in pl.creatures():
        if perm.summoning_sick and "haste" not in perm.keywords():
            continue
        if perm.eff_p() <= 0 or "defender" in perm.keywords():
            continue
        if kulrath and (perm.minus > 0 or perm.plus > 0):
            game.stat(pl, "kulrath_locked", 1)
            continue
        units.append(Unit(perm.eff_p(), perm.eff_t(), perm.keywords(),
                          False, perm, perm.is_commander))
    # stationed spacecraft attack with their printed stats
    for perm in pl.battlefield:
        if "Spacecraft" in perm.card.subtypes and perm.station >= 12:
            units.append(Unit(perm.card.power or 7, perm.card.toughness or 12,
                              frozenset({"flying"}), False, perm,
                              perm.is_commander))
    for g in pl.tokens:
        if g.summoning_sick and "haste" not in g.proto.keywords:
            continue
        anthem = pl.anthem_for(g.proto.artifact)
        p = g.proto.p + anthem
        t = max(1, g.proto.t + anthem - (1 if g.wounded else 0))
        if p <= 0:
            continue
        for _ in range(g.n):
            units.append(Unit(p, t, g.proto.keywords, True, g))
    return units


def gather_blockers(game, dfn, atk):
    kulrath = atk.has("kulrath_lock")
    wall = []
    for perm in dfn.creatures():
        if perm.eff_t() <= 0 or perm.eff_p() < 0:
            continue
        if kulrath and (perm.minus > 0 or perm.plus > 0):
            continue
        wall.append(Unit(perm.eff_p(), perm.eff_t(), perm.keywords(),
                         False, perm))
    for g in dfn.tokens:
        anthem = dfn.anthem_for(g.proto.artifact)
        for _ in range(g.n):
            wall.append(Unit(g.proto.p + anthem,
                             max(1, g.proto.t + anthem), g.proto.keywords,
                             True, g))
    return wall


def _can_block(blocker, attacker):
    if _kw(attacker, "flying") and not (_kw(blocker, "flying")
                                        or _kw(blocker, "reach")):
        return False
    return True


def assign_blocks(attackers, wall, dfn_life):
    """Greedy defensive assignment; menace needs two blockers."""
    free = list(wall)
    # block the biggest threats first
    for atk_unit in sorted(attackers, key=lambda u: -u.power):
        cands = [b for b in free if _can_block(b, atk_unit)]
        if not cands:
            continue
        need = 2 if _kw(atk_unit, "menace") else 1
        # prefer a single blocker that survives and kills; else chump when
        # life pressure demands it
        killers = [b for b in cands
                   if b.power >= atk_unit.toughness
                   and (b.toughness > atk_unit.power
                        or _kw(b, "deathtouch"))]
        chosen = []
        if killers and len(killers) >= need:
            chosen = sorted(killers, key=lambda b: b.power)[:need]
        elif atk_unit.power >= 4 \
                or dfn_life <= sum(u.power for u in attackers) * 1.4:
            # chump-block the big hitters / anything under life pressure
            chosen = sorted(cands, key=lambda b: (not b.is_token,
                                                  b.power))[:need]
        if len(chosen) < need:
            continue
        for b in chosen:
            free.remove(b)
        atk_unit.blockers = chosen
    return attackers


def _kill_unit(game, owner, unit, with_counters=False):
    if unit.is_token:
        effects.kill_tokens(game, owner, unit.ref, 1)
    else:
        if with_counters and unit.ref.minus == 0:
            unit.ref.minus += 1  # wither damage leaves counters
        effects.kill_permanent(game, owner, unit.ref)


def resolve_combat(game, atk, dfn, attackers):
    """Resolve declared attacks; returns damage dealt to dfn."""
    atk_wither = any("wither" in u.keywords for u in attackers) or \
        atk.has("chain") or any(
            "wither" in p.card.keywords for p in atk.battlefield)
    dmg_to_player = 0
    for unit in attackers:
        if not unit.blockers:
            dmg_to_player += unit.power
            if unit.commander:
                dfn.commander_damage[atk.name] = \
                    dfn.commander_damage.get(atk.name, 0) + unit.power
            continue
        # blockers deal damage to attacker
        bpow = sum(b.power for b in unit.blockers)
        bdt = any(_kw(b, "deathtouch") for b in unit.blockers)
        # attacker assigns lethal damage in order, trample overflow
        remaining = unit.power
        adt = _kw(unit, "deathtouch")
        for b in unit.blockers:
            lethal = 1 if adt else max(1, b.toughness)
            dealt = min(remaining, lethal)
            remaining -= dealt
            if dealt >= (1 if adt else b.toughness):
                _kill_unit(game, dfn, b, with_counters=atk_wither)
            elif atk_wither and not b.is_token:
                b.ref.minus += dealt
                if b.ref.eff_t() <= 0:
                    effects.kill_permanent(game, dfn, b.ref)
        if _kw(unit, "trample") and remaining > 0:
            dmg_to_player += remaining
        if bpow >= unit.toughness or (bdt and bpow > 0):
            if not (_kw(unit, "indestructible")):
                _kill_unit(game, atk, unit,
                           with_counters=dfn.has("chain"))
    atk.tokens = [g for g in atk.tokens if g.n > 0]
    dfn.tokens = [g for g in dfn.tokens if g.n > 0]
    dfn.life -= dmg_to_player
    game.stat(atk, "combat_damage", dmg_to_player)
    if dfn.commander_damage.get(atk.name, 0) >= CMD_DAMAGE_LETHAL:
        dfn.life = min(dfn.life, 0)
        game.log("commander_damage_kill", player=dfn.name, by=atk.name)
    return dmg_to_player


def combat_phase(game, pl):
    """Choose a defender (threat-based), decide whether to attack, fight."""
    opps = [o for o in game.opponents_of(pl) if not o.eliminated]
    if not opps:
        return
    dfn = pl.profile.choose_attack_target(pl, opps)
    attackers = gather_attackers(game, pl, dfn)
    if not attackers:
        return
    wall = gather_blockers(game, dfn, pl)
    total_pow = sum(u.power for u in attackers)
    if not pl.profile.should_attack(pl, dfn, attackers, wall):
        return
    assign_blocks(attackers, wall, dfn.life)
    resolve_combat(game, pl, dfn, attackers)
    # attack triggers
    anim = next((p for p in pl.battlefield if p.b("anim")), None)
    if anim:
        pl.anim_counters += 1
        effects.create_tokens(game, pl, pl.anim_counters, 1, 1, True,
                              name="gnome")
    dr = min(pl.bsum("draw_on_attack"), 3)
    if dr:
        pl.draw(dr)
    game.check_eliminations()
