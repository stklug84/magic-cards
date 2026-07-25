"""One-shot effect AST (CR 610) executed on spell/ability resolution.

Each node's resolve(game, ctx) performs the instruction through the Game
API, which routes every state change through the replacement machinery
(rule 614) and event system (rule 603). Nodes are produced by the oracle
text compiler (compiler.py) or by hand-authored card overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cr import rule


@dataclass
class Ctx:
    """Resolution context: who controls the effect, its source, chosen
    targets, X, and any resolution-time choices.
    """

    controller: object
    source: object = None
    targets: list = field(default_factory=list)
    x: int = 0
    #: for triggered abilities: the object of the triggering event
    #: ("that creature", rule 603.10a look-back)
    event_obj: object = None

    def target(self, i=0):
        return self.targets[i] if i < len(self.targets) else None


def _n(value, game, ctx):
    """Count expressions: plain int, 'x', or callable(game, ctx)."""
    if callable(value):
        return value(game, ctx)
    if value == "x":
        return ctx.x
    return int(value)


class Effect:
    def resolve(self, game, ctx):  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class Sequence(Effect):
    parts: list

    def resolve(self, game, ctx):
        for p in self.parts:
            p.resolve(game, ctx)


@rule("111.2")
@dataclass
class CreateTokens(Effect):
    count: object
    spec: object  # abilities.TokenSpec
    controller: str = "you"
    tapped: bool | None = None  # None: use the spec's default

    def resolve(self, game, ctx):
        game.create_tokens(
            ctx.controller,
            self.spec,
            _n(self.count, game, ctx),
            source=ctx.source,
            tapped=self.tapped,
        )


@rule("121.1")
@dataclass
class DrawCards(Effect):
    count: object = 1
    who: str = "you"  # you|each|controller_of_target

    def resolve(self, game, ctx):
        players = [ctx.controller] if self.who == "you" else game.players_apnap()
        for p in players:
            game.draw(p, _n(self.count, game, ctx))


@rule("119.3", "118.5")
@dataclass
class GainLife(Effect):
    amount: object

    def resolve(self, game, ctx):
        game.gain_life(ctx.controller, _n(self.amount, game, ctx))


@dataclass
class LoseLife(Effect):
    amount: object
    who: str = "each_opponent"  # you|each_opponent|target

    def resolve(self, game, ctx):
        n = _n(self.amount, game, ctx)
        if self.who == "target":
            t = ctx.target()
            if t is not None:
                game.lose_life(t, n)
            return
        players = (
            game.opponents(ctx.controller)
            if self.who == "each_opponent"
            else [ctx.controller]
        )
        for p in players:
            game.lose_life(p, n)


@dataclass
class Drain(Effect):
    """Each opponent loses N life, you gain that much (aristocrat drains)."""

    amount: object = 1

    def resolve(self, game, ctx):
        n = _n(self.amount, game, ctx)
        total = 0
        for p in game.opponents(ctx.controller):
            game.lose_life(p, n)
            p.stat("drained_taken", n)
            total += n
        ctx.controller.stat("drain", total)
        game.gain_life(ctx.controller, total)


@rule("120.1")
@dataclass
class DealDamage(Effect):
    amount: object
    to: str = "target"  # target|each_creature|any|divided

    def resolve(self, game, ctx):
        n = _n(self.amount, game, ctx)
        if self.to == "each_creature":
            for obj in list(game.battlefield_objects()):
                if "Creature" in obj.chars(game).types:
                    game.deal_damage(ctx.source, obj, n)
        elif self.to == "divided":
            # policy divides among chosen targets
            for tgt, part in game.policy(ctx.controller).divide_damage(game, ctx, n):
                game.deal_damage(ctx.source, tgt, part)
        else:
            t = ctx.target()
            if t is not None and game.still_legal_target(t, ctx, 0):
                game.deal_damage(ctx.source, t, n)


@rule("701.7")
@dataclass
class Destroy(Effect):
    index: int = 0
    all_of: str = ""  # "" | creatures | each filter

    def resolve(self, game, ctx):
        if self.all_of == "creatures":
            for obj in list(game.battlefield_objects()):
                if "Creature" in obj.chars(game).types:
                    game.destroy(obj)
            return
        t = ctx.target(self.index)
        if t is not None and game.still_legal_target(t, ctx, self.index):
            game.destroy(t)


@rule("701.9")
@dataclass
class ExileObj(Effect):
    index: int = 0

    def resolve(self, game, ctx):
        t = ctx.target(self.index)
        if t is not None and game.still_legal_target(t, ctx, self.index):
            game.exile(t)


@rule("701.22")
@dataclass
class SacrificeSelf(Effect):
    def resolve(self, game, ctx):
        if ctx.source is not None and ctx.source.zone == "battlefield":
            game.sacrifice(ctx.controller, ctx.source)


@dataclass
class ReturnToHand(Effect):
    index: int = 0
    self_land: bool = False  # bounce lands returning own land

    def resolve(self, game, ctx):
        if self.self_land:
            lands = [
                o
                for o in ctx.controller.battlefield
                if "Land" in o.chars(game).types and o is not ctx.source
            ]
            pick = game.policy(ctx.controller).choose_bounce_land(game, lands)
            if pick is not None:
                game.move_zone(pick, "hand")
            return
        t = ctx.target(self.index)
        if t is not None and game.still_legal_target(t, ctx, self.index):
            game.move_zone(t, "hand")


@rule("122.1")
@dataclass
class PutCounters(Effect):
    kind: str  # "+1/+1" | "-1/-1" | "charge" | ...
    count: object = 1
    on: str = "target"  # target|self|each_creature|
    #                                    each_opponent_creature|own_choice

    def resolve(self, game, ctx):
        n = _n(self.count, game, ctx)
        if self.on == "self":
            game.put_counters(ctx.source, self.kind, n)
        elif self.on == "each_creature":
            for obj in list(game.battlefield_objects()):
                if "Creature" in obj.chars(game).types:
                    game.put_counters(obj, self.kind, n)
        elif self.on == "each_opponent_creature":
            for obj in list(game.battlefield_objects()):
                if (
                    "Creature" in obj.chars(game).types
                    and obj.controller is not ctx.controller
                ):
                    game.put_counters(obj, self.kind, n)
        else:
            t = ctx.target()
            if t is not None and game.still_legal_target(t, ctx, 0):
                game.put_counters(t, self.kind, n)


@rule("702.87")
@dataclass
class Proliferate(Effect):
    times: int = 1

    def resolve(self, game, ctx):
        for _ in range(self.times):
            game.proliferate(ctx.controller)


@rule("701.23")
@dataclass
class SearchLands(Effect):
    """Ramp: search library for up to N basic/any lands."""

    count: object = 1
    tapped: bool = True
    to_hand: bool = False
    basic_only: bool = True

    def resolve(self, game, ctx):
        game.search_lands(
            ctx.controller,
            _n(self.count, game, ctx),
            tapped=self.tapped,
            to_hand=self.to_hand,
            basic_only=self.basic_only,
        )


@rule("701.23")
@dataclass
class TargetControllerBasicLand(Effect):
    """Removal rider (Path to Exile / Assassin's Trophy): the destroyed
    permanent's controller may search for a basic land onto the
    battlefield.
    """

    index: int = 0

    def resolve(self, game, ctx):
        t = ctx.target(self.index)
        if t is None or not hasattr(t, "controller"):
            return
        game.search_lands(t.controller, 1, tapped=False, basic_only=True)


@dataclass
class TargetControllerGainsPower(Effect):
    """Swords to Plowshares rider: target's controller gains life equal
    to its power (last-known power).
    """

    index: int = 0

    def resolve(self, game, ctx):
        t = ctx.target(self.index)
        if t is None or not hasattr(t, "controller"):
            return
        game.gain_life(t.controller, t.base.power or 0)


@dataclass
class LoseLifeTargetMV(Effect):
    """Feed the Swarm rider: you lose life equal to the target's mana
    value.
    """

    index: int = 0

    def resolve(self, game, ctx):
        from .manasys import parse_cost

        t = ctx.target(self.index)
        if t is None or not hasattr(t, "base"):
            return
        game.lose_life(ctx.controller, parse_cost(t.base.mana_cost).mv)


@rule("707.10")
@dataclass
class CopySpell(Effect):
    """Copy target instant or sorcery spell (targets unchanged)."""

    index: int = 0

    def resolve(self, game, ctx):
        t = ctx.target(self.index)
        if t is not None and t in game.stack:
            game.copy_spell(t, ctx.controller)


@dataclass
class PutLandFromHand(Effect):
    """Dread Tiller-style: put a land card from your hand onto the
    battlefield tapped.
    """

    tapped: bool = True

    def resolve(self, game, ctx):
        from .objects import Zone

        lands = [c for c in ctx.controller.hand if "Land" in c.base.types]
        if not lands:
            return
        pick = (
            game.policy(ctx.controller)._best_land(game, ctx.controller, lands)
            if hasattr(game.policy(ctx.controller), "_best_land")
            else lands[0]
        )
        game.move_zone(pick, Zone.BATTLEFIELD, to_battlefield_tapped=self.tapped)


@dataclass
class TakeDeadCreature(Effect):
    """The Reaper-style: put the creature that just died onto the
    battlefield under your control (rule 603.10a look-back).
    """

    def resolve(self, game, ctx):
        from .objects import Zone

        obj = ctx.event_obj
        if obj is None or obj.is_token or obj.zone != Zone.GRAVEYARD:
            return
        obj.controller = ctx.controller
        game.move_zone(obj, Zone.BATTLEFIELD)
        ctx.controller.stat("grave_robs")


@rule("106.2")
@dataclass
class AddMana(Effect):
    types: tuple = ()  # e.g. ("C", "C") or ("ANY",)
    any_color: bool = False
    commander_identity: bool = False

    def resolve(self, game, ctx):
        pool = ctx.controller.mana_pool
        if self.any_color or self.commander_identity:
            allowed = (
                "WUBRG"
                if not self.commander_identity
                else "".join(sorted(game.commander_identity(ctx.controller))) or "C"
            )
            pick = game.policy(ctx.controller).choose_mana_color(game, ctx, allowed)
            pool.add(pick)
        for t in self.types:
            pool.add(t)


@rule("701.5")
@dataclass
class CounterSpell(Effect):
    def resolve(self, game, ctx):
        t = ctx.target()
        if t is not None and t in game.stack:
            ctx.controller.stat("counterspells_used")
            game.counter_spell(t)


@dataclass
class PumpAll(Effect):
    """Creatures you control get +P/+T until end of turn."""

    power: int
    toughness: int
    tokens_only: bool = False

    def resolve(self, game, ctx):
        from .layers import ContinuousEffect

        me = ctx.controller
        tok = self.tokens_only

        def applies(g, obj, ch):
            return (
                obj.controller is me
                and "Creature" in ch.types
                and (not tok or obj.is_token)
            )

        game.add_floating_effect(
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=ctx.source,
                applies_to=applies,
                apply=lambda g, o, ch: (
                    setattr(ch, "power", (ch.power or 0) + self.power),
                    setattr(ch, "toughness", (ch.toughness or 0) + self.toughness),
                ),
                duration="end_of_turn",
            ),
        )


@dataclass
class ProtectAll(Effect):
    """Your permanents gain hexproof and indestructible until end of turn."""

    def resolve(self, game, ctx):
        from .layers import ContinuousEffect

        me = ctx.controller
        game.add_floating_effect(
            ContinuousEffect(
                layer=6,
                sublayer="",
                source=ctx.source,
                applies_to=lambda g, o, ch: o.controller is me,
                apply=lambda g, o, ch: ch.keywords.update(
                    {"hexproof", "indestructible"},
                ),
                duration="end_of_turn",
            ),
        )


@dataclass
class Populate(Effect):
    times: int = 1

    @rule("701.34")
    def resolve(self, game, ctx):
        for _ in range(self.times):
            game.populate(ctx.controller)


@dataclass
class EnergyGain(Effect):
    count: object = 1

    def resolve(self, game, ctx):
        ctx.controller.energy += _n(self.count, game, ctx)


@dataclass
class Scry(Effect):
    count: object = 1

    @rule("701.26")
    def resolve(self, game, ctx):
        game.scry(ctx.controller, _n(self.count, game, ctx))


@dataclass
class TutorAny(Effect):
    """Search your library for a card and put it into your hand."""

    def resolve(self, game, ctx):
        game.tutor(ctx.controller)


@dataclass
class Untap(Effect):
    on: str = "self"

    def resolve(self, game, ctx):
        obj = ctx.source if self.on == "self" else ctx.target()
        if obj is not None and obj.zone == "battlefield":
            game.untap(obj)


@dataclass
class TapTarget(Effect):
    index: int = 0

    def resolve(self, game, ctx):
        t = ctx.target(self.index)
        if t is not None and game.still_legal_target(t, ctx, self.index):
            game.tap(t)


@dataclass
class Blink(Effect):
    """Exile target, return it to the battlefield (Conjurer's Closet,
    Restoration Angel).
    """

    index: int = 0

    def resolve(self, game, ctx):
        t = ctx.target(self.index)
        if t is not None and game.still_legal_target(t, ctx, self.index):
            game.blink(t)


@dataclass
class Noop(Effect):
    """A clause the compiler recognized but the engine does not model.
    Recorded on the card so the coverage report can list it.
    """

    note: str = ""

    def resolve(self, game, ctx):
        pass
