"""One-shot effect AST (CR 610) executed on spell/ability resolution.

Each node's resolve(game, ctx) performs the instruction through the Game
API, which routes every state change through the replacement machinery
(rule 614) and event system (rule 603). Nodes are produced by the oracle
text compiler (compiler.py) or by hand-authored card overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mtgrules.cr import rule
from mtgrules.layers import ContinuousEffect
from mtgrules.manasys import parse_cost
from mtgrules.objects import Characteristics, GameObject, Player, Zone
from mtgrules.stack import StackItem

if TYPE_CHECKING:
    from mtgrules.abilities import TokenSpec
    from mtgrules.game import Game
    from mtgrules.protocols import CountValue, OneShot
    from mtgrules.stack import Target


@dataclass
class Ctx:
    """Resolution context (rule 608.2).

    Who controls the effect, its source, chosen targets, X, and any
    resolution-time choices.
    """

    controller: Player
    source: GameObject | None = None
    targets: list[Target] = field(default_factory=list)
    x: int = 0
    #: for triggered abilities: the object of the triggering event
    #: ("that creature", rule 603.10a look-back)
    event_obj: GameObject | None = None

    def target(self, i: int = 0) -> Target | None:
        """Return the i-th chosen target, or None when it was not chosen."""
        return self.targets[i] if i < len(self.targets) else None


def _n(value: CountValue, game: Game, ctx: Ctx) -> int:
    """Count expressions: plain int, 'x', or callable(game, ctx)."""
    if callable(value):
        return value(game, ctx)
    if value == "x":
        return ctx.x
    return int(value)


class Effect:
    """Base class of the one-shot effect nodes (rule 610.1)."""

    def resolve(self, game: Game, ctx: Ctx) -> None:  # pragma: no cover
        """Perform the instruction through the Game API (interface)."""
        raise NotImplementedError


@dataclass
class Sequence(Effect):
    """Effects performed in written order (rule 608.2c)."""

    parts: list[Effect]

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Resolve each part in order."""
        for p in self.parts:
            p.resolve(game, ctx)


@dataclass
class Custom(Effect):
    """A hand-written effect body ``fn(game, ctx)`` (overrides.py)."""

    fn: OneShot
    note: str = ""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Delegate to the hand-written body."""
        self.fn(game, ctx)


@rule("111.2")
@dataclass
class CreateTokens(Effect):
    """Create N tokens from a TokenSpec (rule 111.2)."""

    count: CountValue
    spec: TokenSpec
    controller: str = "you"
    tapped: bool | None = None  # None: use the spec's default

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Create the tokens under the effect controller's control."""
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
    """You (or each player) draw N cards (rule 121.1)."""

    count: CountValue = 1
    who: str = "you"  # you|each|controller_of_target

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Draw for the affected player(s)."""
        players = [ctx.controller] if self.who == "you" else game.players_apnap()
        for p in players:
            game.draw(p, _n(self.count, game, ctx))


@rule("119.3", "118.5")
@dataclass
class GainLife(Effect):
    """You gain N life (rule 119.3)."""

    amount: CountValue

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Gain the life."""
        game.gain_life(ctx.controller, _n(self.amount, game, ctx))


@dataclass
class LoseLife(Effect):
    """A player or each opponent loses N life (rule 119.3)."""

    amount: CountValue
    who: str = "each_opponent"  # you|each_opponent|target

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Apply the life loss to the affected player(s)."""
        n = _n(self.amount, game, ctx)
        if self.who == "target":
            t = ctx.target()
            if isinstance(t, Player):
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

    amount: CountValue = 1

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Drain each opponent and gain the total."""
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
    """The source deals N damage (rule 120.1)."""

    amount: CountValue
    to: str = "target"  # target|each_creature|any|divided

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Deal the damage to the chosen recipient(s)."""
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
            if isinstance(t, GameObject | Player) and game.still_legal_target(
                t,
                ctx,
                0,
            ):
                game.deal_damage(ctx.source, t, n)


@rule("701.7")
@dataclass
class Destroy(Effect):
    """Destroy the target, or every matching permanent (rule 701.7)."""

    index: int = 0
    all_of: str = ""  # "" | creatures | each filter

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Destroy the affected permanent(s)."""
        if self.all_of == "creatures":
            for obj in list(game.battlefield_objects()):
                if "Creature" in obj.chars(game).types:
                    game.destroy(obj)
            return
        t = ctx.target(self.index)
        if isinstance(t, GameObject) and game.still_legal_target(t, ctx, self.index):
            game.destroy(t)


@rule("701.9")
@dataclass
class ExileObj(Effect):
    """Exile the target permanent (rule 701.9)."""

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Exile the target if it is still legal."""
        t = ctx.target(self.index)
        if isinstance(t, GameObject) and game.still_legal_target(t, ctx, self.index):
            game.exile(t)


@rule("701.22")
@dataclass
class SacrificeSelf(Effect):
    """Sacrifice the effect's source (rule 701.22)."""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Sacrifice the source if it is still on the battlefield."""
        if ctx.source is not None and ctx.source.zone == "battlefield":
            game.sacrifice(ctx.controller, ctx.source)


@dataclass
class ReturnToHand(Effect):
    """Return the target (or one of your lands) to its owner's hand."""

    index: int = 0
    self_land: bool = False  # bounce lands returning own land

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Bounce the affected permanent."""
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
        if isinstance(t, GameObject) and game.still_legal_target(t, ctx, self.index):
            game.move_zone(t, "hand")


@rule("122.1")
@dataclass
class PutCounters(Effect):
    """Put counters on the target / self / each matching creature."""

    kind: str  # "+1/+1" | "-1/-1" | "charge" | ...
    count: CountValue = 1
    on: str = "target"  # target|self|each_creature|
    #                                    each_opponent_creature|own_choice

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Place the counters (rule 122.1)."""
        n = _n(self.count, game, ctx)
        if self.on == "self":
            if ctx.source is not None:
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
            if isinstance(t, GameObject) and game.still_legal_target(t, ctx, 0):
                game.put_counters(t, self.kind, n)


@rule("702.87")
@dataclass
class Proliferate(Effect):
    """Proliferate, N times (rule 702.87)."""

    times: int = 1

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Run each proliferate round."""
        for _ in range(self.times):
            game.proliferate(ctx.controller)


@rule("701.23")
@dataclass
class SearchLands(Effect):
    """Ramp: search library for up to N basic/any lands."""

    count: CountValue = 1
    tapped: bool = True
    to_hand: bool = False
    basic_only: bool = True

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Search and put the lands where the card says."""
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
    """Removal rider (Path to Exile / Assassin's Trophy).

    The destroyed permanent's controller may search for a basic land onto
    the battlefield.
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Let the target's controller fetch a basic land."""
        t = ctx.target(self.index)
        if not isinstance(t, GameObject):
            return
        game.search_lands(t.controller, 1, tapped=False, basic_only=True)


@dataclass
class TargetControllerGainsPower(Effect):
    """Swords to Plowshares rider.

    The target's controller gains life equal to its power (last-known
    power).
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Grant the life to the target's controller."""
        t = ctx.target(self.index)
        if not isinstance(t, GameObject):
            return
        game.gain_life(t.controller, t.base.power or 0)


@dataclass
class LoseLifeTargetMV(Effect):
    """Feed the Swarm rider.

    You lose life equal to the target's mana value.
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Pay the life."""
        t = ctx.target(self.index)
        if not isinstance(t, GameObject):
            return
        game.lose_life(ctx.controller, parse_cost(t.base.mana_cost).mv)


@rule("707.10")
@dataclass
class CopySpell(Effect):
    """Copy target instant or sorcery spell (targets unchanged)."""

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Put the copy on the stack (rule 707.10)."""
        t = ctx.target(self.index)
        if isinstance(t, StackItem) and t in game.stack:
            game.copy_spell(t, ctx.controller)


@dataclass
class PutLandFromHand(Effect):
    """Dread Tiller-style.

    Put a land card from your hand onto the battlefield tapped.
    """

    tapped: bool = True

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Pick and play the land."""
        lands = [c for c in ctx.controller.hand if "Land" in c.base.types]
        if not lands:
            return
        pick = game.policy(ctx.controller).best_land(game, ctx.controller, lands)
        game.move_zone(pick, Zone.BATTLEFIELD, to_battlefield_tapped=self.tapped)


@dataclass
class TakeDeadCreature(Effect):
    """The Reaper-style graveyard theft.

    Put the creature that just died onto the battlefield under your
    control (rule 603.10a look-back).
    """

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Steal the dead creature if it is still in a graveyard."""
        obj = ctx.event_obj
        if obj is None or obj.is_token or obj.zone != Zone.GRAVEYARD:
            return
        obj.controller = ctx.controller
        game.move_zone(obj, Zone.BATTLEFIELD)
        ctx.controller.stat("grave_robs")


@rule("106.2")
@dataclass
class AddMana(Effect):
    """Add mana to the controller's pool (rule 106.2)."""

    types: tuple[str, ...] = ()  # e.g. ("C", "C") or ("ANY",)
    any_color: bool = False
    commander_identity: bool = False

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Add the produced mana."""
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
    """Counter the target spell (rule 701.5)."""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Counter the spell if it is still on the stack."""
        t = ctx.target()
        if isinstance(t, StackItem) and t in game.stack:
            ctx.controller.stat("counterspells_used")
            game.counter_spell(t)


@dataclass
class PumpAll(Effect):
    """Creatures you control get +P/+T until end of turn."""

    power: int
    toughness: int
    tokens_only: bool = False

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Register the until-end-of-turn pump (rule 611.2)."""
        me = ctx.controller
        tok = self.tokens_only

        def applies(_g: Game, obj: GameObject, ch: Characteristics) -> bool:
            return (
                obj.controller is me
                and "Creature" in ch.types
                and (not tok or obj.is_token)
            )

        def boost(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.power = (ch.power or 0) + self.power
            ch.toughness = (ch.toughness or 0) + self.toughness

        game.add_floating_effect(
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=ctx.source,
                applies_to=applies,
                apply=boost,
                duration="end_of_turn",
            ),
        )


@dataclass
class ProtectAll(Effect):
    """Your permanents gain hexproof and indestructible until end of turn."""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Register the until-end-of-turn protection (rule 611.2)."""
        me = ctx.controller
        game.add_floating_effect(
            ContinuousEffect(
                layer=6,
                sublayer="",
                source=ctx.source,
                applies_to=lambda _g, o, _ch: o.controller is me,
                apply=lambda _g, _o, ch: ch.keywords.update(
                    {"hexproof", "indestructible"},
                ),
                duration="end_of_turn",
            ),
        )


@dataclass
class Populate(Effect):
    """Populate, N times (rule 701.34)."""

    times: int = 1

    @rule("701.34")
    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Run each populate round."""
        for _ in range(self.times):
            game.populate(ctx.controller)


@dataclass
class EnergyGain(Effect):
    """You get N energy counters (rule 122.1)."""

    count: CountValue = 1

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Add the energy."""
        ctx.controller.energy += _n(self.count, game, ctx)


@dataclass
class Scry(Effect):
    """Scry N (rule 701.26)."""

    count: CountValue = 1

    @rule("701.26")
    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Scry via the controller's policy."""
        game.scry(ctx.controller, _n(self.count, game, ctx))


@dataclass
class TutorAny(Effect):
    """Search your library for a card and put it into your hand."""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Tutor via the controller's policy."""
        game.tutor(ctx.controller)


@dataclass
class Untap(Effect):
    """Untap the source (or the target)."""

    on: str = "self"

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Untap the affected permanent."""
        obj = ctx.source if self.on == "self" else ctx.target()
        if isinstance(obj, GameObject) and obj.zone == "battlefield":
            game.untap(obj)


@dataclass
class TapTarget(Effect):
    """Tap the target permanent."""

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Tap the target if it is still legal."""
        t = ctx.target(self.index)
        if isinstance(t, GameObject) and game.still_legal_target(t, ctx, self.index):
            game.tap(t)


@dataclass
class Blink(Effect):
    """Exile target, return it to the battlefield.

    Conjurer's Closet / Restoration Angel style.
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Blink the target if it is still legal."""
        t = ctx.target(self.index)
        if isinstance(t, GameObject) and game.still_legal_target(t, ctx, self.index):
            game.blink(t)


@dataclass
class Noop(Effect):
    """A clause the compiler recognized but the engine does not model.

    Recorded on the card so the coverage report can list it.
    """

    note: str = ""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Do nothing (the clause is reported, not silently wrong)."""
