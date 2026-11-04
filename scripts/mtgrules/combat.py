"""The combat phase (CR 506-511) and combat-relevant keywords (CR 702)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mtgrules.cr import rule
from mtgrules.events import Event, EventType
from mtgrules.objects import GameObject, Player, Zone

if TYPE_CHECKING:
    from mtgrules.game import Game
    from mtgrules.objects import Characteristics

#: (source, target, amount) of one combat-damage assignment (rule 510.1)
type _Assignment = tuple[GameObject, Player | GameObject, int]


@rule("508.1")
def can_attack(game: Game, obj: GameObject) -> bool:
    """Whether *obj* can legally be declared as an attacker (rule 508.1)."""
    ch = obj.chars(game)
    if "Creature" not in ch.types or obj.tapped:
        return False
    if obj.entered_this_turn and "haste" not in ch.keywords:
        return False  # rule 302.6
    if "defender" in ch.keywords:  # rule 702.3
        return False
    # Kulrath-style locks
    return "shackled" not in ch.keywords


#: landwalk keyword -> the land subtype the defending player must control
_LANDWALK = {
    "plainswalk": "Plains",
    "islandwalk": "Island",
    "swampwalk": "Swamp",
    "mountainwalk": "Mountain",
    "forestwalk": "Forest",
}


@rule("509.1b", "702.9", "702.13b", "702.14c", "702.36b", "702.111")
def can_block(game: Game, blocker: GameObject, attacker: GameObject) -> bool:
    """Whether *blocker* can legally block *attacker* (rule 509.1b).

    Evasion: flying (702.9c), fear (702.36b), intimidate (702.13b) and
    landwalk (702.14c). Menace is a count restriction checked after all
    blockers are declared (702.111b).
    """
    ch_b = blocker.chars(game)
    if "Creature" not in ch_b.types or blocker.tapped or "shackled" in ch_b.keywords:
        return False
    return not _evades(game, attacker.chars(game), ch_b, blocker.controller)


def _evades(
    game: Game,
    ch_a: Characteristics,
    ch_b: Characteristics,
    defender: Player,
) -> bool:
    """Whether an evasion keyword of the attacker rules this blocker out."""
    kw = ch_a.keywords
    if "flying" in kw and not (ch_b.keywords & {"flying", "reach"}):
        return True  # rules 702.9c / 702.17
    artifact = "Artifact" in ch_b.types
    if "fear" in kw and not (artifact or "B" in ch_b.colors):
        return True  # rule 702.36b
    if "intimidate" in kw and not (artifact or ch_b.colors & ch_a.colors):
        return True  # rule 702.13b
    walked = {_LANDWALK[k] for k in kw if k in _LANDWALK}
    if not walked:
        return False
    # rule 702.14c: unblockable while the defending player controls a land
    # of the walked type
    return any(
        "Land" in land.chars(game).types and land.chars(game).subtypes & walked
        for land in defender.battlefield
    )


class CombatPhase:
    """One combat phase: declarations, damage steps, cleanup (CR 506-511)."""

    def __init__(self, game: Game) -> None:
        """Bind the game with no attackers declared yet."""
        self.game = game
        self.attackers: list[GameObject] = []

    @rule("507.1", "508.1", "509.1", "510.1", "511.1")
    def run(self) -> None:
        """Run the whole combat phase for the active player."""
        game = self.game
        active = game.active_player
        # 507: beginning of combat
        game.queue_triggers(
            Event(EventType.BEGIN_STEP, {"step": "combat_begin", "player": active}),
        )
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
            fs = [
                a
                for a in self.combatants()
                if a.chars(game).keywords & {"first strike", "double strike"}
            ]
            if fs:
                self.damage_step(first_strike=True)
                game.priority_loop()
                if game.game_over:
                    return
            self.damage_step(first_strike=False)
            game.priority_loop()
            if game.game_over:
                return
        # 511: end of combat
        game.queue_triggers(
            Event(EventType.END_STEP_EVT, {"step": "combat_end", "player": active}),
        )
        game.priority_loop()
        for obj in list(game.battlefield_objects()):
            obj.attacking = None
            obj.blocking = []
            obj.blocked_by = []

    def combatants(self) -> list[GameObject]:
        """All creatures currently in combat (attackers and blockers)."""
        out = list(self.attackers)
        for a in self.attackers:
            out.extend(a.blocked_by)
        return [o for o in out if o.zone == Zone.BATTLEFIELD]

    @rule("508.1", "508.2", "702.20", "702.83b")
    def _declare_attackers(self) -> None:
        """Rule 508.1: the active player declares attackers."""
        game = self.game
        active = game.active_player
        candidates = [o for o in active.battlefield if can_attack(game, o)]
        picks = game.policy(active).declare_attackers(game, active, candidates)
        for obj, defender in picks:
            obj.attacking = defender
            if "vigilance" not in obj.chars(game).keywords:  # rule 702.20
                obj.tapped = True
            self.attackers.append(obj)
            active.stat("attacks")
            dfn = defender if isinstance(defender, Player) else defender.controller
            dfn.grudges[active.name] = dfn.grudges.get(active.name, 0) + (
                obj.chars(game).power or 0
            )
            game.log(
                "attack",
                who=active.name,
                card=obj.base.name,
                target=(
                    defender.name
                    if isinstance(defender, Player)
                    else defender.base.name
                ),
            )
        # rule 508.1: "attacks" triggers fire once the declaration is
        # complete; "alone" (rule 702.83b, exalted) needs the full list
        alone = len(self.attackers) == 1
        for obj in list(self.attackers):
            game.queue_triggers(
                Event(
                    EventType.ATTACKS,
                    {"obj": obj, "defender": obj.attacking, "alone": alone},
                ),
            )
        game.bump()
        if self.attackers:
            game.priority_loop()
        self.attackers = [a for a in self.attackers if a.zone == Zone.BATTLEFIELD]

    @rule("509.1", "509.1a")
    def _declare_blockers(self) -> None:
        """Rule 509.1: each defending player declares blockers."""
        game = self.game
        for defender in game.players_apnap()[1:]:
            mine = [
                a
                for a in self.attackers
                if a.attacking is defender
                or (
                    isinstance(a.attacking, GameObject)
                    and a.attacking.controller is defender
                )
            ]
            if not mine:
                continue
            blockers = [
                o
                for o in defender.battlefield
                if "Creature" in o.chars(game).types and not o.tapped
            ]
            assignment = game.policy(defender).declare_blockers(
                game,
                defender,
                mine,
                blockers,
            )
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
        for a in self.attackers:
            for b in a.blocked_by:
                game.log(
                    "block",
                    who=b.controller.name,
                    blocker=b.base.name,
                    attacker=a.base.name,
                )
        game.bump()
        game.priority_loop()

    def _strikes(self, obj: GameObject, *, first_strike: bool) -> bool:
        """Whether *obj* deals damage in this damage step (rule 510.5)."""
        kw = obj.chars(self.game).keywords
        if first_strike:
            return bool(kw & {"first strike", "double strike"})
        return "first strike" not in kw or "double strike" in kw

    @rule("510.1", "510.2", "510.4", "702.2", "702.19")
    def damage_step(self, *, first_strike: bool) -> None:
        """Assign then deal all combat damage simultaneously (510.2).

        Attackers and blockers are handled independently (rule 510.1):
        a blocker strikes in the step its own keywords allow, whether or
        not the attacker it blocks deals damage in that step (fixes F2 and
        F3 - first-strike blockers and blockers of first-strikers or of
        0-power attackers used to deal no damage at all).
        """
        game = self.game
        assignments: list[_Assignment] = []
        for a in list(self.attackers):
            if a.zone != Zone.BATTLEFIELD:
                continue
            blockers = [b for b in a.blocked_by if b.zone == Zone.BATTLEFIELD]
            if self._strikes(a, first_strike=first_strike):
                assignments.extend(self._attacker_assignments(a, blockers))
            assignments.extend(
                self._blocker_strikes(a, blockers, first_strike=first_strike),
            )
        for source, target, amount in assignments:
            if isinstance(target, Player):
                game.deal_damage(source, target, amount, combat=True)
                source.controller.stat("combat_damage", amount)
            elif target.zone == Zone.BATTLEFIELD:
                game.deal_damage(source, target, amount, combat=True)

    @rule("510.1", "510.1c", "702.2", "702.19")
    def _attacker_assignments(
        self,
        a: GameObject,
        blockers: list[GameObject],
    ) -> list[_Assignment]:
        """Damage assignments of one attacker to its blockers / defender.

        Rule 510.1a: assignment order = policy order; rules 510.1c-d
        lethal assignment; deathtouch (702.2b) makes any nonzero amount
        lethal; trample (702.19e) assigns the excess to the defender, and
        all of it when every blocker has left combat (702.19d).
        """
        game = self.game
        out: list[_Assignment] = []
        ch = a.chars(game)
        power = max(0, ch.power or 0)
        if not power or a.attacking is None:
            return out
        trample = "trample" in ch.keywords
        if not blockers:
            # unblocked (510.1c), or blocked but every blocker left and
            # the attacker has trample (702.19d)
            if not a.blocked_by or trample:
                out.append((a, a.attacking, power))
            return out
        remaining = power
        deathtouch = "deathtouch" in ch.keywords
        for b in blockers:
            if remaining <= 0:
                break
            bt = b.chars(game).toughness or 0
            lethal = 1 if deathtouch else max(1, bt - b.damage)
            deal = min(remaining, lethal)
            if b is blockers[-1] and not trample:
                deal = remaining
            out.append((a, b, deal))
            remaining -= deal
        if remaining > 0 and trample:
            out.append((a, a.attacking, remaining))
        return out

    @rule("510.1c")
    def _blocker_strikes(
        self,
        a: GameObject,
        blockers: list[GameObject],
        *,
        first_strike: bool,
    ) -> list[_Assignment]:
        """Assign the blockers' damage back at the attacker (510.1c)."""
        out: list[_Assignment] = []
        for b in blockers:
            if self._strikes(b, first_strike=first_strike):
                bp = max(0, b.chars(self.game).power or 0)
                if bp:
                    out.append((b, a, bp))
        return out
