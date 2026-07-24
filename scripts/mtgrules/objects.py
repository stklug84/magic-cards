"""Game objects, characteristics, and zones (CR 1xx, 2xx, 4xx).

Every in-game entity (card, token, copy of a spell, ability on the stack)
is a GameObject (rule 109.1). Characteristics (rule 109.3) are computed by
the layer system (rule 613) from the printed/base values stored here.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .cr import rule

# ---------------------------------------------------------------- zones

@rule("400.1")
class Zone:
    LIBRARY = "library"
    HAND = "hand"
    BATTLEFIELD = "battlefield"
    GRAVEYARD = "graveyard"
    STACK = "stack"
    EXILE = "exile"
    COMMAND = "command"

    #: rule 400.2 - public vs hidden
    HIDDEN = {LIBRARY, HAND}


COLORS = ("W", "U", "B", "R", "G")

_obj_ids = itertools.count(1)
_timestamps = itertools.count(1)


def next_timestamp() -> int:
    """Rule 613.7c-e: timestamps are totally ordered by creation."""
    return next(_timestamps)


@rule("109.3", "613.1")
@dataclass
class Characteristics:
    """The characteristic set of an object.

    A fresh copy of the *base* (printed / token-defined) characteristics is
    the input to each layer-system evaluation (rule 613.1: start with actual
    object, then apply continuous effects in layer order).
    """
    name: str = ""
    mana_cost: str = ""
    colors: set = field(default_factory=set)          # rule 105 / 202.2
    supertypes: set = field(default_factory=set)      # rule 205.4
    types: set = field(default_factory=set)           # rule 205.2
    subtypes: set = field(default_factory=set)        # rule 205.3
    power: int | None = None                          # rule 208
    toughness: int | None = None
    loyalty: int | None = None                        # rule 209
    abilities: list = field(default_factory=list)     # rule 113
    keywords: set = field(default_factory=set)        # rule 702

    def copy(self) -> "Characteristics":
        return Characteristics(
            name=self.name, mana_cost=self.mana_cost,
            colors=set(self.colors), supertypes=set(self.supertypes),
            types=set(self.types), subtypes=set(self.subtypes),
            power=self.power, toughness=self.toughness,
            loyalty=self.loyalty, abilities=list(self.abilities),
            keywords=set(self.keywords))


@rule("109.1", "110.1", "111.1")
class GameObject:
    """A card, token, or spell/ability on the stack."""

    def __init__(self, base: Characteristics, owner, *, is_token=False,
                 card_ref=None):
        self.id = next(_obj_ids)
        self.base = base                 # printed / token characteristics
        self.owner = owner               # rule 108.3
        self.controller = owner          # rule 109.4
        self.zone = Zone.LIBRARY
        self.is_token = is_token         # rule 111
        self.card_ref = card_ref         # mtgcards CardData (data provenance)
        self.timestamp = next_timestamp()
        # battlefield status, rule 110.5
        self.tapped = False
        self.flipped = False
        self.face_down = False
        self.phased_out = False
        # rule 122 counters: kind -> count
        self.counters: dict[str, int] = {}
        #: snapshot for leave-the-battlefield triggers (rule 603.10a)
        self.lki_counters: dict[str, int] = {}
        # rule 120.6 damage marked
        self.damage = 0
        self.deathtouch_damage = False   # rule 704.5h
        self.attached_to = None          # auras/equipment, rules 303.4/301.5
        self.attachments: list = []
        # combat state
        self.attacking = None            # player or planeswalker attacked
        self.blocking: list = []
        self.blocked_by: list = []
        # turn bookkeeping
        self.entered_this_turn = False   # summoning sickness, rule 302.6
        self.commander = False           # rule 903.3
        # cache filled by the layer system each evaluation tick
        self._chars: Characteristics | None = None
        self._chars_tick = -1

    # -- characteristic access (always via the layer system) -----------
    def chars(self, game) -> Characteristics:
        return game.layers.characteristics(self)

    def __repr__(self):
        return f"<{self.base.name or 'object'}#{self.id} {self.zone}>"

    @rule("110.5", "400.7")
    def reset_battlefield_state(self):
        """An object that changes zones becomes a new object (rule 400.7):
        status, counters, damage, and attachments do not carry over."""
        self.tapped = False
        self.flipped = False
        self.face_down = False
        self.phased_out = False
        self.counters = {}
        self.damage = 0
        self.deathtouch_damage = False
        self.attached_to = None
        self.attachments = []
        self.attacking = None
        self.blocking = []
        self.blocked_by = []
        self.entered_this_turn = False
        self.timestamp = next_timestamp()


@rule("102.1", "119.1")
class Player:
    """A player (rule 102). Life total starts at 40 in Commander
    (rule 903.7)."""

    def __init__(self, name: str, deck_name: str = ""):
        self.name = name
        self.deck_name = deck_name
        self.life = 40                        # rule 903.7
        self.library: list[GameObject] = []
        self.hand: list[GameObject] = []
        self.graveyard: list[GameObject] = []
        self.exile: list[GameObject] = []
        self.command: list[GameObject] = []
        self.battlefield: list[GameObject] = []
        self.mana_pool = None                 # set by game (manasys.ManaPool)
        self.lost = False
        self.lose_reason = ""
        self.commander_obj: GameObject | None = None
        self.commander_casts = 0              # rule 903.8 commander tax
        self.commander_damage: dict[int, int] = {}   # rule 903.10a
        self.lands_played = 0                 # rule 305.2
        self.max_hand_size = 7                # rule 402.2
        self.energy = 0                       # counters a player has, r122.1
        self.experience = 0
        self.poison = 0
        self.drew_from_empty = False          # rule 704.5c flag
        self.attractions = None
        # per-game statistics
        self.stats: dict[str, float] = {}
        self.cards_cast: list[str] = []       # for per-card win-rate lift

    def zone_list(self, zone: str) -> list:
        return {Zone.LIBRARY: self.library, Zone.HAND: self.hand,
                Zone.GRAVEYARD: self.graveyard, Zone.EXILE: self.exile,
                Zone.COMMAND: self.command,
                Zone.BATTLEFIELD: self.battlefield}[zone]

    def stat(self, key: str, n: float = 1):
        self.stats[key] = self.stats.get(key, 0) + n

    def __repr__(self):
        return f"<Player {self.name} life={self.life}>"
