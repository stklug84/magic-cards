"""One-shot effect AST (CR 610) executed on spell/ability resolution.

Each node's resolve(game, ctx) performs the instruction through the Game
API, which routes every state change through the replacement machinery
(rule 614) and event system (rule 603). Nodes are produced by the oracle
text compiler (compiler.py) or by hand-authored card overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mtgrules.abilities import ActivatedAbility
from mtgrules.cr import rule
from mtgrules.layers import ContinuousEffect
from mtgrules.manasys import land_mana_types, mana_order, parse_cost
from mtgrules.objects import Characteristics, GameObject, Player, Zone
from mtgrules.stack import StackItem

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mtgrules.abilities import TargetSpec, TokenSpec
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
    #: every chosen target in choice order (see :attr:`target_groups`)
    targets: list[Target] = field(default_factory=list)
    x: int = 0
    #: for triggered abilities: the object of the triggering event
    #: ("that creature", rule 603.10a look-back)
    event_obj: GameObject | None = None
    #: the chosen targets per TargetSpec (rule 601.2c); when empty, each
    #: entry of ``targets`` is its own group (one target per spec)
    target_groups: list[list[Target]] = field(default_factory=list)
    #: the TargetSpecs the groups were chosen for (rule 608.2b re-check)
    specs: list[TargetSpec] = field(default_factory=list)
    #: rule 601.2d: announced division of a "divided" amount, aligned
    #: with the targets of the dividing effect's spec
    division: list[int] = field(default_factory=list)

    def group(self, i: int = 0) -> list[Target]:
        """Return the targets chosen for the i-th TargetSpec."""
        if self.target_groups:
            return list(self.target_groups[i]) if i < len(self.target_groups) else []
        return [self.targets[i]] if i < len(self.targets) else []

    def target(self, i: int = 0) -> Target | None:
        """Return the first target chosen for the i-th spec, or None."""
        group = self.group(i)
        return group[0] if group else None

    def legal_group(self, game: Game, i: int = 0) -> list[Target]:
        """Return the i-th spec's targets that are still legal (608.2b)."""
        return [t for t in self.group(i) if game.still_legal_target(t, self, i)]


def count_value(value: CountValue, game: Game, ctx: Ctx) -> int:
    """Evaluate a count expression: plain int, 'x', or callable(game, ctx)."""
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
            count_value(self.count, game, ctx),
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
            game.draw(p, count_value(self.count, game, ctx))


@rule("119.3", "118.5")
@dataclass
class GainLife(Effect):
    """You gain N life (rule 119.3)."""

    amount: CountValue

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Gain the life."""
        game.gain_life(ctx.controller, count_value(self.amount, game, ctx))


@dataclass
class LoseLife(Effect):
    """A player or each opponent loses N life (rule 119.3)."""

    amount: CountValue
    who: str = "each_opponent"  # you|each_opponent|target

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Apply the life loss to the affected player(s)."""
        n = count_value(self.amount, game, ctx)
        if self.who == "target":
            for t in ctx.legal_group(game, 0):
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
        n = count_value(self.amount, game, ctx)
        total = 0
        for p in game.opponents(ctx.controller):
            game.lose_life(p, n)
            p.stat("drained_taken", n)
            total += n
        ctx.controller.stat("drain", total)
        game.gain_life(ctx.controller, total)


@rule("120.1", "601.2d")
@dataclass
class DealDamage(Effect):
    """The source deals N damage (rule 120.1).

    ``to="target"`` deals N to every target of the spec at ``index``;
    ``to="divided"`` deals the division announced on casting (rule
    601.2d, ``ctx.division``) to the targets that are still legal - a
    vanished target's share is lost, not redistributed.
    """

    amount: CountValue
    to: str = "target"  # target|each_creature|divided
    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Deal the damage to the chosen recipient(s)."""
        n = count_value(self.amount, game, ctx)
        if self.to == "each_creature":
            for obj in list(game.battlefield_objects()):
                if "Creature" in obj.chars(game).types:
                    game.deal_damage(ctx.source, obj, n)
        elif self.to == "divided":
            self._resolve_divided(game, ctx)
        else:
            for t in ctx.legal_group(game, self.index):
                if isinstance(t, GameObject | Player):
                    game.deal_damage(ctx.source, t, n)

    def _resolve_divided(self, game: Game, ctx: Ctx) -> None:
        """Deal the announced shares to the targets that are still legal."""
        for tgt, part in zip(ctx.group(self.index), ctx.division, strict=False):
            if isinstance(tgt, GameObject | Player) and game.still_legal_target(
                tgt,
                ctx,
                self.index,
            ):
                game.deal_damage(ctx.source, tgt, part)


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
        for t in ctx.legal_group(game, self.index):
            if isinstance(t, GameObject):
                game.destroy(t)


@rule("701.9")
@dataclass
class ExileObj(Effect):
    """Exile the target permanent(s) (rule 701.9)."""

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Exile every target of the spec that is still legal."""
        for t in ctx.legal_group(game, self.index):
            if isinstance(t, GameObject):
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
        for t in ctx.legal_group(game, self.index):
            if isinstance(t, GameObject):
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
        n = count_value(self.count, game, ctx)
        for obj in self._recipients(game, ctx):
            game.put_counters(obj, self.kind, n)

    def _recipients(self, game: Game, ctx: Ctx) -> list[GameObject]:
        """Return the permanents the counters go on."""
        if self.on == "self":
            return [ctx.source] if ctx.source is not None else []
        if self.on in ("each_creature", "each_opponent_creature"):
            return [
                obj
                for obj in list(game.battlefield_objects())
                if "Creature" in obj.chars(game).types
                and (self.on == "each_creature" or obj.controller is not ctx.controller)
            ]
        return [t for t in ctx.legal_group(game, 0) if isinstance(t, GameObject)]


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
            count_value(self.count, game, ctx),
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
        """Let each target's controller fetch a basic land."""
        for t in ctx.group(self.index):
            if isinstance(t, GameObject):
                game.search_lands(t.controller, 1, tapped=False, basic_only=True)


@dataclass
class TargetControllerGainsPower(Effect):
    """Swords to Plowshares rider.

    The target's controller gains life equal to its power (last-known
    power).
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Grant the life to each target's controller."""
        for t in ctx.group(self.index):
            if isinstance(t, GameObject):
                game.gain_life(t.controller, t.base.power or 0)


@dataclass
class LoseLifeTargetMV(Effect):
    """Feed the Swarm rider.

    You lose life equal to the target's mana value.
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Pay the life."""
        for t in ctx.group(self.index):
            if isinstance(t, GameObject):
                game.lose_life(ctx.controller, parse_cost(t.base.mana_cost).mv)


@rule("707.10")
@dataclass
class CopySpell(Effect):
    """Copy target instant or sorcery spell (targets unchanged)."""

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Put the copy on the stack (rule 707.10)."""
        for t in ctx.group(self.index):
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
    """Add mana to the controller's pool (rule 106.2).

    ``types`` lists the produced mana symbols in order: "Add {B}{R}" is
    ``("B", "R")`` and produces both. With ``choice`` the source produces
    ONE mana of one of the listed types ("Add {B} or {R}", fix F1).
    ``any_color`` / ``commander_identity`` produce one mana of a colour
    chosen from WUBRG / the commander's colour identity; ``amount`` is how
    many mana of that one chosen colour ("Add three mana of any one
    color").
    """

    types: tuple[str, ...] = ()  # e.g. ("C", "C") or ("B", "R")
    any_color: bool = False
    commander_identity: bool = False
    choice: bool = False
    amount: int = 1

    @property
    def chooses(self) -> bool:
        """Whether the produced mana's type is chosen when it is produced."""
        return self.choice or self.any_color or self.commander_identity

    def allowed(self, game: Game, controller: Player) -> str:
        """Return the types the controller may choose from, in canonical order."""
        if self.commander_identity:
            return "".join(mana_order(game.commander_identity(controller))) or "C"
        if self.any_color:
            return "WUBRG"
        return "".join(mana_order(self.types)) or "C"

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Add the produced mana."""
        pool = ctx.controller.mana_pool
        if self.chooses:
            allowed = self.allowed(game, ctx.controller)
            pick = game.policy(ctx.controller).choose_mana_color(game, ctx, allowed)
            pool.add(pick, self.amount)
            return
        for t in self.types:
            pool.add(t)


def land_mana_ability(colors: Iterable[str]) -> ActivatedAbility:
    """Build the '{T}: Add ...' ability of a land known only by its graph colours.

    A multi-colour land offers ONE mana of one of its colours (the "Add
    {B} or {R}" reading, fix F1); all five colours mean "any color".
    """
    types, any_c = land_mana_types(colors)
    return ActivatedAbility(
        tap_cost=True,
        is_mana_ability=True,
        effect=AddMana(types=types or ("C",), any_color=any_c, choice=len(types) > 1),
        text="{T}: Add mana.",
    )


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
        ctx.controller.energy += count_value(self.count, game, ctx)


@dataclass
class Scry(Effect):
    """Scry N (rule 701.26)."""

    count: CountValue = 1

    @rule("701.26")
    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Scry via the controller's policy."""
        game.scry(ctx.controller, count_value(self.count, game, ctx))


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
        """Untap the affected permanent(s)."""
        objs = [ctx.source] if self.on == "self" else ctx.legal_group(game, 0)
        for obj in objs:
            if isinstance(obj, GameObject) and obj.zone == "battlefield":
                game.untap(obj)


@dataclass
class TapTarget(Effect):
    """Tap the target permanent(s)."""

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Tap every target of the spec that is still legal."""
        for t in ctx.legal_group(game, self.index):
            if isinstance(t, GameObject):
                game.tap(t)


@dataclass
class Blink(Effect):
    """Exile target(s), return them to the battlefield.

    Conjurer's Closet / Restoration Angel style.
    """

    index: int = 0

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Blink every target of the spec that is still legal."""
        for t in ctx.legal_group(game, self.index):
            if isinstance(t, GameObject):
                game.blink(t)


@dataclass
class Noop(Effect):
    """A clause the compiler recognized but the engine does not model.

    Recorded on the card so the coverage report can list it.
    """

    note: str = ""

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Do nothing (the clause is reported, not silently wrong)."""
