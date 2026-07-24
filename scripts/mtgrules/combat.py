"""The combat phase (CR 506-511) and combat-relevant keywords (CR 702)."""

from __future__ import annotations

from .cr import rule
from .events import Event, EventType
from .objects import GameObject, Player, Zone


@rule("508.1")
def can_attack(game, obj) -> bool:
    ch = obj.chars(game)
    if "Creature" not in ch.types or obj.tapped:
        return False
    if obj.entered_this_turn and "haste" not in ch.keywords:
        return False                               # rule 302.6
    if "defender" in ch.keywords:                  # rule 702.3
        return False
    if "shackled" in ch.keywords:                  # Kulrath-style locks
        return False
    return True


@rule("509.1b", "702.9", "702.111")
def can_block(game, blocker, attacker) -> bool:
    ch_b = blocker.chars(game)
    ch_a = attacker.chars(game)
    if "Creature" not in ch_b.types or blocker.tapped:
        return False
    if "shackled" in ch_b.keywords:
        return False
    if "flying" in ch_a.keywords and not (
            ch_b.keywords & {"flying", "reach"}):  # rules 702.9c / 702.17
        return False
    if "menace" in ch_a.keywords:
        return True   # menace is a *count* restriction, checked separately
    return True


class CombatPhase:
    def __init__(self, game):
        self.game = game
        self.attackers: list[GameObject] = []

    @rule("507.1", "508.1", "509.1", "510.1", "511.1")
    def run(self):
        game = self.game
        active = game.active_player
        # 507: beginning of combat
        game._queue_triggers(Event(EventType.BEGIN_STEP,
                                   {"step": "combat_begin",
                                    "player": active}))
        game.priority_loop()
        if game.game_over:
            return
        # 508: declare attackers
        self._declare_attackers()
        if game.game_over:
            return
        if self.attackers:
            # 509: declare blockers
            self._declare_blockers()
            if game.game_over:
                return
            # 510: combat damage; rule 510.5 - an additional first-strike
            # combat damage step if any first/double strike creature
            fs = [a for a in self._combatants()
                  if a.chars(game).keywords & {"first strike",
                                               "double strike"}]
            if fs:
                self._damage_step(first_strike=True)
                game.priority_loop()
                if game.game_over:
                    return
            self._damage_step(first_strike=False)
            game.priority_loop()
            if game.game_over:
                return
        # 511: end of combat
        game._queue_triggers(Event(EventType.END_STEP_EVT,
                                   {"step": "combat_end", "player": active}))
        game.priority_loop()
        for obj in list(game.battlefield_objects()):
            obj.attacking = None
            obj.blocking = []
            obj.blocked_by = []

    def _combatants(self):
        out = list(self.attackers)
        for a in self.attackers:
            out.extend(a.blocked_by)
        return [o for o in out if o.zone == Zone.BATTLEFIELD]

    @rule("508.1", "508.2", "702.20")
    def _declare_attackers(self):
        game = self.game
        active = game.active_player
        candidates = [o for o in active.battlefield if can_attack(game, o)]
        picks = game.policy(active).declare_attackers(game, active,
                                                      candidates)
        for obj, defender in picks:
            obj.attacking = defender
            if "vigilance" not in obj.chars(game).keywords:  # rule 702.20
                obj.tapped = True
            self.attackers.append(obj)
            active.stat("attacks")
            game._queue_triggers(Event(EventType.ATTACKS,
                                       {"obj": obj, "defender": defender}))
        game.bump()
        if self.attackers:
            game.priority_loop()
        self.attackers = [a for a in self.attackers
                          if a.zone == Zone.BATTLEFIELD]

    @rule("509.1", "509.1a")
    def _declare_blockers(self):
        game = self.game
        for defender in game.players_apnap()[1:]:
            mine = [a for a in self.attackers
                    if a.attacking is defender
                    or (isinstance(a.attacking, GameObject)
                        and a.attacking.controller is defender)]
            if not mine:
                continue
            blockers = [o for o in defender.battlefield
                        if "Creature" in o.chars(game).types
                        and not o.tapped]
            assignment = game.policy(defender).declare_blockers(
                game, defender, mine, blockers)
            for blocker, attacker in assignment:
                if not can_block(game, blocker, attacker):
                    continue
                blocker.blocking.append(attacker)
                attacker.blocked_by.append(blocker)
        # rule 702.111b menace: can't be blocked by exactly one creature
        for a in self.attackers:
            if "menace" in a.chars(game).keywords and len(a.blocked_by) == 1:
                a.blocked_by[0].blocking.remove(a)
                a.blocked_by = []
        game.bump()
        game.priority_loop()

    @rule("510.1", "510.2", "510.4", "702.2", "702.19")
    def _damage_step(self, first_strike: bool):
        """Assign then deal all combat damage simultaneously (510.2)."""
        game = self.game
        assignments = []                            # (source, target, amount)

        def strikes(obj):
            kw = obj.chars(game).keywords
            if first_strike:
                return bool(kw & {"first strike", "double strike"})
            return "first strike" not in kw or "double strike" in kw

        for a in list(self.attackers):
            if a.zone != Zone.BATTLEFIELD or not strikes(a):
                continue
            ch = a.chars(game)
            power = max(0, ch.power or 0)
            if not power:
                continue
            blockers = [b for b in a.blocked_by
                        if b.zone == Zone.BATTLEFIELD]
            if not blockers:
                if not a.blocked_by:                # unblocked, rule 510.1c
                    assignments.append((a, a.attacking, power))
                continue
            # rule 510.1a damage assignment order = policy order;
            # rule 510.1c-d lethal assignment, deathtouch (702.2b) makes
            # any nonzero amount lethal; trample (702.19e) excess through
            remaining = power
            deathtouch = "deathtouch" in ch.keywords
            for b in blockers:
                if remaining <= 0:
                    break
                bt = b.chars(game).toughness or 0
                lethal = 1 if deathtouch else max(1, bt - b.damage)
                deal = min(remaining, lethal)
                if b is blockers[-1] and "trample" not in ch.keywords:
                    deal = remaining
                assignments.append((a, b, deal))
                remaining -= deal
            if remaining > 0 and "trample" in ch.keywords:
                assignments.append((a, a.attacking, remaining))
            for b in blockers:
                if strikes(b):
                    bp = max(0, b.chars(game).power or 0)
                    if bp:
                        assignments.append((b, a, bp))
        # blockers of attackers that died before damage still don't hit
        for source, target, amount in assignments:
            if isinstance(target, Player):
                game.deal_damage(source, target, amount, combat=True)
                source.controller.stat("combat_damage", amount)
            elif isinstance(target, GameObject) \
                    and target.zone == Zone.BATTLEFIELD:
                game.deal_damage(source, target, amount, combat=True)
