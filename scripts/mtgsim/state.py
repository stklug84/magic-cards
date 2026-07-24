"""Battlefield and player state."""

from __future__ import annotations

from dataclasses import dataclass, field

from .cards import CardData
from .mana import Source, parse_cost

START_LIFE = 40
CMD_DAMAGE_LETHAL = 21
MAX_HAND = 7


class Permanent:
    """A nontoken permanent on the battlefield."""

    __slots__ = ("card", "minus", "plus", "summoning_sick", "station",
                 "is_commander", "source", "tapped")

    def __init__(self, card: CardData, is_commander=False):
        self.card = card
        self.minus = 0            # -1/-1 counters
        self.plus = 0             # +1/+1 counters
        self.summoning_sick = True
        self.station = 0          # station counters (Spacecraft)
        self.is_commander = is_commander
        self.source = None        # mana Source if this produces mana
        self.tapped = False

    @property
    def name(self):
        return self.card.name

    def b(self, key, default=None):
        return self.card.behavior.get(key, default)

    def eff_p(self):
        return max(0, (self.card.power or 0) + self.plus - self.minus)

    def eff_t(self):
        return (self.card.toughness or 0) + self.plus - self.minus

    def is_creature_now(self):
        if "Spacecraft" in self.card.subtypes:
            return self.station >= self.b("station_threshold", 12)
        return self.card.is_creature

    def keywords(self):
        return self.card.keywords


@dataclass
class TokenProto:
    p: int
    t: int
    name: str = "token"
    artifact: bool = False
    keywords: frozenset = frozenset()


class TokenGroup:
    """n identical tokens; -1/-1 counters tracked as per-token 'wounded'."""

    __slots__ = ("proto", "n", "wounded", "summoning_sick")

    def __init__(self, proto: TokenProto, n: int, sick=True):
        self.proto = proto
        self.n = n
        self.wounded = 0
        self.summoning_sick = sick


class PlayerState:
    def __init__(self, name, deck, db, rng, profile):
        self.name = name
        self.deck = deck
        self.db = db
        self.rng = rng
        self.profile = profile
        self.life = START_LIFE
        self.energy = 0
        self.treasures = 0
        self.library = list(deck.cards)
        rng.shuffle(self.library)
        self.hand: list[str] = []
        self.grave: list[str] = []
        self.exile: list[str] = []
        self.battlefield: list[Permanent] = []
        self.tokens: list[TokenGroup] = []
        self.commander = deck.commander
        self.cmd_tax = 0
        self.cmd_in_zone = deck.commander is not None
        self.cmd_locked = False
        self.commander_damage: dict[str, int] = {}
        self.land_played = False
        self.eliminated = False
        self.anim_counters = 0
        self.mulligans = 0
        self.cards_cast: set[str] = set()

    # ------- lookups ----------------------------------------------------
    def card(self, name) -> CardData:
        return self.db.get(name)

    def perms(self, key):
        return [p for p in self.battlefield if p.b(key)]

    def has(self, key):
        return any(p.b(key) for p in self.battlefield)

    def bsum(self, key):
        return sum(p.b(key, 0) for p in self.battlefield
                   if isinstance(p.b(key, 0), (int, float)))

    def sources(self):
        return [p.source for p in self.battlefield if p.source]

    def draw(self, n=1):
        drawn = 0
        for _ in range(n):
            if self.library:
                self.hand.append(self.library.pop())
                drawn += 1
        return drawn

    # ------- derived stats ----------------------------------------------
    def anthem_for(self, artifact_token: bool, is_token=True):
        boost = 0
        for p in self.battlefield:
            a = p.b("anthem")
            if not a:
                continue
            if a.get("art_only") and not artifact_token:
                continue
            if a.get("tokens_only") and not is_token:
                continue
            boost += a.get("boost", 0)
        return boost

    def token_multiplier(self, creature=True):
        mult = 1
        for p in self.battlefield:
            if p.b("doubler"):
                mult *= p.b("doubler")
            if creature and p.b("creature_token_mult"):
                mult *= p.b("creature_token_mult")
        return min(mult, 12)

    def drain_engines(self):
        return sum(1 for p in self.battlefield
                   if p.b("drain_own") or p.b("drain_any"))

    def blood_artists(self):
        return sum(1 for p in self.battlefield if p.b("drain_any"))

    def creatures(self):
        return [p for p in self.battlefield if p.is_creature_now()]

    def token_count(self):
        return sum(g.n for g in self.tokens)

    def total_power(self):
        pw = sum(p.eff_p() for p in self.creatures())
        for g in self.tokens:
            pw += g.n * max(0, g.proto.p +
                            self.anthem_for(g.proto.artifact))
        return pw

    def board_threat(self):
        """Threat score used by opposing AI target selection."""
        threat = self.total_power()
        threat += sum(p.b("key", 0) for p in self.battlefield)
        threat += 2 * len([p for p in self.battlefield
                           if p.b("doubler") or p.b("tokens_per_turn")])
        return threat

    def make_land_source(self, perm: Permanent):
        colors = perm.b("land_colors") or {"C"}
        perm.source = Source(colors, perm.name)
        if perm.b("enters_tapped"):
            perm.source.tapped = True

    def make_rock_source(self, perm: Permanent):
        n = perm.b("rock_mana", 0)
        if n:
            colors = perm.b("rock_colors") or {"C"}
            perm.source = Source(colors, perm.name)

    def commander_cost(self):
        if not self.commander:
            return None
        cost = parse_cost(self.card(self.commander).mana_cost)
        cost.generic += self.cmd_tax
        return cost
