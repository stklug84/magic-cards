"""Abilities (CR 112, 113, 602, 603, 604, 605) and targeting (CR 115).

Four ability kinds (rule 113.3): spell abilities, activated abilities,
triggered abilities, and static abilities. Static abilities generate
continuous effects (rule 604.1) and/or replacement effects (rule 614).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cr import rule
from .manasys import parse_cost


# ---------------------------------------------------------------- targets

@rule("115.1")
@dataclass
class TargetSpec:
    """One target requirement of a spell/ability."""
    what: str                 # creature|permanent|artifact|enchantment|
    #                           art_ench|cre_ench|nonland|player|opponent|
    #                           spell|creature_or_planeswalker
    controller: str = "any"   # any|opponent|you
    count: int = 1
    optional: bool = False    # "up to N"
    other: bool = False       # "another target ..."

    @rule("115.4")
    def legal(self, game, ctx, target) -> bool:
        """Is *target* a legal target right now (rule 115.4)?"""
        from .objects import GameObject, Player
        if self.what in ("player", "opponent"):
            if not isinstance(target, Player) or target.lost:
                return False
            return not (self.what == "opponent"
                        and target is ctx.controller)
        if self.what == "spell":
            return target in game.stack and target.is_spell
        if not isinstance(target, GameObject):
            return False
        if target.zone != "battlefield":
            return False
        if self.other and target is ctx.source:
            return False
        ch = target.chars(game)
        # rule 702.16 protection / hexproof-style grants
        if game.cant_be_targeted(target, ctx):
            return False
        if self.controller == "opponent" \
                and target.controller is ctx.controller:
            return False
        if self.controller == "you" \
                and target.controller is not ctx.controller:
            return False
        need = {
            "creature": lambda: "Creature" in ch.types,
            "permanent": lambda: True,
            "nonland": lambda: "Land" not in ch.types,
            "artifact": lambda: "Artifact" in ch.types,
            "enchantment": lambda: "Enchantment" in ch.types,
            "art_ench": lambda: ch.types & {"Artifact", "Enchantment"},
            "cre_ench": lambda: ch.types & {"Creature", "Enchantment"},
            "creature_or_planeswalker":
                lambda: ch.types & {"Creature", "Planeswalker"},
            "land": lambda: "Land" in ch.types,
        }.get(self.what, lambda: True)
        return bool(need())


# ---------------------------------------------------------------- triggers

@rule("603.1")
@dataclass
class TriggerSpec:
    """When/Whenever/At condition of a triggered ability (rule 603.1)."""
    event: str                          # EventType value
    #: predicate(game, source_obj, event) -> bool; None = self only default
    condition: object = None
    #: "you" | "any" - whose step for BEGIN_STEP triggers
    step: str = ""                      # upkeep|end|combat_begin|...

    def matches(self, game, source, event) -> bool:
        if event.type != self.event:
            return False
        if self.condition is not None:
            return bool(self.condition(game, source, event))
        # default: the event is about the ability's source itself
        return event.data.get("obj") is source


@rule("113.3c", "603.1")
@dataclass
class TriggeredAbility:
    trigger: TriggerSpec
    effect: object                      # effects.Effect
    targets: list = field(default_factory=list)
    #: rule 603.4 intervening "if" clause: pred(game, source) checked both
    #: at trigger time and on resolution
    intervening_if: object = None
    text: str = ""
    optional: bool = False              # "you may ..."
    once_each_turn: bool = False        # "Do this only once each turn."

    kind = "triggered"


@rule("113.3b", "602.1")
@dataclass
class ActivatedAbility:
    """[Cost]: [Effect] (rule 602.1)."""
    mana_cost: str = ""
    tap_cost: bool = False              # {T}, rule 602.5a and 107.5
    sac_cost: str = ""                  # "a creature", "another artifact", ~
    life_cost: int = 0
    loyalty_cost: int | None = None     # rule 606; planeswalkers
    effect: object = None
    targets: list = field(default_factory=list)
    is_mana_ability: bool = False       # rule 605.1a
    sorcery_only: bool = False          # "Activate only as a sorcery" 602.5d
    once_per_turn: bool = False
    #: rule 702.29a cycling-style: activated from the hand; the card itself
    #: is discarded as part of the cost
    from_hand: bool = False
    text: str = ""

    kind = "activated"

    def __post_init__(self):
        self.cost = parse_cost(self.mana_cost)


@rule("113.3a", "112.1")
@dataclass
class SpellAbility:
    """The instructions of an instant/sorcery spell (rule 113.3a)."""
    effect: object
    targets: list = field(default_factory=list)
    text: str = ""

    kind = "spell"


@rule("113.3d", "604.1")
@dataclass
class StaticAbility:
    """A static ability: generates continuous and/or replacement effects
    while its source is on the battlefield (rules 604.1-604.2)."""
    #: factory(game, source) -> list of layers.ContinuousEffect
    continuous: object = None
    #: factory(game, source) -> list of replacements.Replacement
    replacement: object = None
    text: str = ""

    kind = "static"


# ---------------------------------------------------------------- tokens

@rule("111.1", "111.4")
@dataclass
class TokenSpec:
    name: str = ""
    power: int | None = None
    toughness: int | None = None
    colors: frozenset = frozenset()
    types: frozenset = frozenset({"Creature"})
    subtypes: frozenset = frozenset()
    keywords: frozenset = frozenset()
    tapped: bool = False
    #: predefined token abilities, e.g. Treasure's sacrifice mana ability
    predefined: str = ""                # treasure|clue|food|blood|map
    #: extra ability factories () -> Ability attached to created tokens
    abilities: tuple = ()


TREASURE = TokenSpec(name="Treasure", types=frozenset({"Artifact"}),
                     subtypes=frozenset({"Treasure"}), predefined="treasure")
CLUE = TokenSpec(name="Clue", types=frozenset({"Artifact"}),
                 subtypes=frozenset({"Clue"}), predefined="clue")
FOOD = TokenSpec(name="Food", types=frozenset({"Artifact"}),
                 subtypes=frozenset({"Food"}), predefined="food")
