"""Abilities (CR 112, 113, 602, 603, 604, 605) and targeting (CR 115).

Four ability kinds (rule 113.3): spell abilities, activated abilities,
triggered abilities, and static abilities. Static abilities generate
continuous effects (rule 604.1) and/or replacement effects (rule 614).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mtgrules.cr import rule
from mtgrules.manasys import parse_cost
from mtgrules.objects import GameObject, Player
from mtgrules.stack import StackItem

if TYPE_CHECKING:
    from collections.abc import Callable

    from mtgrules.effects import Ctx, Effect
    from mtgrules.events import Event
    from mtgrules.game import Game
    from mtgrules.protocols import (
        ContinuousFactory,
        InterveningIf,
        ReplacementFactory,
        TriggerCondition,
    )
    from mtgrules.stack import Target

# ---------------------------------------------------------------- targets


@rule("115.1")
@dataclass
class TargetSpec:
    """One target requirement of a spell/ability."""

    what: str  # creature|permanent|artifact|enchantment|
    #                           art_ench|cre_ench|nonland|player|opponent|
    #                           spell|creature_or_planeswalker
    controller: str = "any"  # any|opponent|you
    count: int = 1
    optional: bool = False  # "up to N"
    other: bool = False  # "another target ..."

    @rule("115.4")
    def legal(self, game: Game, ctx: Ctx, target: Target) -> bool:
        """Return whether *target* is a legal target right now (rule 115.4)."""
        if self.what in ("player", "opponent"):
            if not isinstance(target, Player) or target.lost:
                return False
            return not (self.what == "opponent" and target is ctx.controller)
        if self.what == "spell":
            return (
                isinstance(target, StackItem)
                and target in game.stack
                and target.is_spell
            )
        if not isinstance(target, GameObject):
            return False
        return self._legal_object(game, ctx, target)

    def _legal_object(self, game: Game, ctx: Ctx, target: GameObject) -> bool:
        """Rule 115.4 legality of a battlefield-object target."""
        if target.zone != "battlefield":
            return False
        if self.other and target is ctx.source:
            return False
        ch = target.chars(game)
        # rule 702.16 protection / hexproof-style grants
        if game.cant_be_targeted(target, ctx):
            return False
        if self.controller == "opponent" and target.controller is ctx.controller:
            return False
        if self.controller == "you" and target.controller is not ctx.controller:
            return False
        need: Callable[[], object] = {
            "creature": lambda: "Creature" in ch.types,
            "permanent": lambda: True,
            "nonland": lambda: "Land" not in ch.types,
            "artifact": lambda: "Artifact" in ch.types,
            "enchantment": lambda: "Enchantment" in ch.types,
            "art_ench": lambda: ch.types & {"Artifact", "Enchantment"},
            "cre_ench": lambda: ch.types & {"Creature", "Enchantment"},
            "creature_or_planeswalker": lambda: ch.types & {"Creature", "Planeswalker"},
            "land": lambda: "Land" in ch.types,
        }.get(self.what, lambda: True)
        return bool(need())


# ---------------------------------------------------------------- triggers


@rule("603.1")
@dataclass
class TriggerSpec:
    """When/Whenever/At condition of a triggered ability (rule 603.1)."""

    event: str  # EventType value
    #: predicate(game, source_obj, event) -> bool; None = self only default
    condition: TriggerCondition | None = None
    #: "you" | "any" - whose step for BEGIN_STEP triggers
    step: str = ""  # upkeep|end|combat_begin|...

    def matches(self, game: Game, source: GameObject, event: Event) -> bool:
        """Whether *event* makes an ability with this spec trigger."""
        if event.type != self.event:
            return False
        if self.condition is not None:
            return bool(self.condition(game, source, event))
        # default: the event is about the ability's source itself
        return event.data.get("obj") is source


@rule("113.3c", "603.1")
@dataclass
class TriggeredAbility:
    """[Trigger condition], [effect] (rule 603.1)."""

    trigger: TriggerSpec
    effect: Effect
    targets: list[TargetSpec] = field(default_factory=list)
    #: rule 603.4 intervening "if" clause: pred(game, source) checked both
    #: at trigger time and on resolution
    intervening_if: InterveningIf | None = None
    text: str = ""
    optional: bool = False  # "you may ..."
    once_each_turn: bool = False  # "Do this only once each turn."

    kind = "triggered"


@rule("113.3b", "602.1")
@dataclass
class ActivatedAbility:
    """[Cost]: [Effect] (rule 602.1)."""

    mana_cost: str = ""
    tap_cost: bool = False  # {T}, rule 602.5a and 107.5
    sac_cost: str = ""  # "a creature", "another artifact", ~
    life_cost: int = 0
    loyalty_cost: int | None = None  # rule 606; planeswalkers
    effect: Effect | None = None
    targets: list[TargetSpec] = field(default_factory=list)
    is_mana_ability: bool = False  # rule 605.1a
    sorcery_only: bool = False  # "Activate only as a sorcery" 602.5d
    once_per_turn: bool = False
    #: rule 702.29a cycling-style: activated from the hand; the card itself
    #: is discarded as part of the cost
    from_hand: bool = False
    #: activated while the card is in the graveyard (Reassembling Skeleton)
    from_graveyard: bool = False
    text: str = ""

    kind = "activated"

    def __post_init__(self) -> None:
        """Parse the printed mana cost once (rule 202.1)."""
        self.cost = parse_cost(self.mana_cost)


@rule("113.3a", "112.1")
@dataclass
class SpellAbility:
    """The instructions of an instant/sorcery spell (rule 113.3a)."""

    effect: Effect
    targets: list[TargetSpec] = field(default_factory=list)
    text: str = ""

    kind = "spell"


@rule("113.3d", "604.1")
@dataclass
class StaticAbility:
    """A static ability (rules 604.1-604.2).

    Generates continuous and/or replacement effects while its source is
    on the battlefield.
    """

    #: factory(game, source) -> list of layers.ContinuousEffect
    continuous: ContinuousFactory | None = None
    #: factory(game, source) -> list of replacements.Replacement
    replacement: ReplacementFactory | None = None
    text: str = ""
    #: "spells you control can't be countered" (Chimil style)
    uncounterable_spells: bool = False
    #: marker consumed by Game.play_land: the land enters tapped
    enters_tapped: bool = False

    kind = "static"


#: any of the four ability kinds carried by Characteristics.abilities
type Ability = ActivatedAbility | SpellAbility | StaticAbility | TriggeredAbility


# ---------------------------------------------------------------- tokens


@rule("111.1", "111.4")
@dataclass
class TokenSpec:
    """The characteristics a token is created with (rule 111.4)."""

    name: str = ""
    power: int | None = None
    toughness: int | None = None
    colors: frozenset[str] = frozenset()
    types: frozenset[str] = frozenset({"Creature"})
    subtypes: frozenset[str] = frozenset()
    keywords: frozenset[str] = frozenset()
    tapped: bool = False
    #: predefined token abilities, e.g. Treasure's sacrifice mana ability
    predefined: str = ""  # treasure|clue|food|blood|map
    #: extra ability factories () -> Ability attached to created tokens
    abilities: tuple[Callable[[], Ability], ...] = ()


TREASURE = TokenSpec(
    name="Treasure",
    types=frozenset({"Artifact"}),
    subtypes=frozenset({"Treasure"}),
    predefined="treasure",
)
CLUE = TokenSpec(
    name="Clue",
    types=frozenset({"Artifact"}),
    subtypes=frozenset({"Clue"}),
    predefined="clue",
)
FOOD = TokenSpec(
    name="Food",
    types=frozenset({"Artifact"}),
    subtypes=frozenset({"Food"}),
    predefined="food",
)
