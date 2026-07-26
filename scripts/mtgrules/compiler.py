"""Oracle text -> ability AST compiler (R2).

Compiles each card's :oracleText from the knowledge graph into structured
abilities (SpellAbility / ActivatedAbility / TriggeredAbility /
StaticAbility) executed by the rules engine. The grammar covers the
dominant templates of the deck pool; complex cards get hand-written
implementations in overrides.py. Clauses neither compiled nor overridden
are recorded as unknown (Noop) and reported by the adapter - nothing is
skipped silently.

Effect clauses are parsed by a table of per-pattern handlers
(_CLAUSE_PARSERS): each handler owns one oracle template and either
returns its Effect node or None to let the next handler try.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mtgrules import overrides
from mtgrules.abilities import (
    TREASURE,
    ActivatedAbility,
    SpellAbility,
    StaticAbility,
    TargetSpec,
    TokenSpec,
    TriggeredAbility,
    TriggerSpec,
)
from mtgrules.cr import rule
from mtgrules.effects import (
    AddMana,
    CopySpell,
    CounterSpell,
    CreateTokens,
    Ctx,
    Custom,
    DealDamage,
    Destroy,
    Drain,
    DrawCards,
    Effect,
    EnergyGain,
    ExileObj,
    GainLife,
    LoseLife,
    LoseLifeTargetMV,
    Noop,
    Populate,
    Proliferate,
    ProtectAll,
    PumpAll,
    PutCounters,
    PutLandFromHand,
    ReturnToHand,
    SacrificeSelf,
    Scry,
    SearchLands,
    Sequence,
    TakeDeadCreature,
    TapTarget,
    TargetControllerBasicLand,
    TargetControllerGainsPower,
    TutorAny,
)
from mtgrules.events import EventType
from mtgrules.layers import ContinuousEffect
from mtgrules.objects import Characteristics, Zone

if TYPE_CHECKING:
    from collections.abc import Callable

    from mtgrules.events import Event
    from mtgrules.game import Game
    from mtgrules.objects import GameObject
    from mtgrules.protocols import CardRef, TriggerCondition

#: filled by compile_card: card name -> set of uncompiled clauses
UNKNOWN_CLAUSES: dict[str, set[str]] = {}

_KEYWORD_WORDS = {
    "flying",
    "vigilance",
    "trample",
    "haste",
    "deathtouch",
    "lifelink",
    "menace",
    "reach",
    "defender",
    "indestructible",
    "hexproof",
    "flash",
    "first strike",
    "double strike",
    "wither",
    "infect",
    "islandwalk",
    "fear",
    "intimidate",
    "persist",
    "exalted",
}

_NUM: dict[str, int | str] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "x": "x",
}


def _num(word: str) -> int | str:
    """Parse a count word: an int, or the literal 'x'."""
    w = word.lower()
    if w in _NUM:
        return _NUM[w]
    return int(w) if w.isdigit() else 1


def _note(name: str, clause: str) -> None:
    """Record an uncompiled clause for the coverage report."""
    UNKNOWN_CLAUSES.setdefault(name, set()).add(clause)


# ---------------------------------------------------------------- effects

_TOKEN_RE = re.compile(
    r"create (a|an|one|two|three|four|five|x|\d+)"
    r"(?P<tapped> tapped)?[^.]*?"
    r"(?P<p>\d+)/(?P<t>\d+) (?P<colors>[a-z ]*?)"
    r"(?P<art>artifact )?creature tokens?"
    r"(?P<kw> with [a-z ]+)?",
    re.IGNORECASE,
)
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}


def _parse_token_clause(m: re.Match[str]) -> tuple[int | str, TokenSpec]:
    """Extract (count, TokenSpec) from a _TOKEN_RE match."""
    count = _num(m.group(1))
    colors = frozenset(
        c for w, c in _COLOR_WORDS.items() if w in (m.group("colors") or "")
    )
    kws = frozenset(k for k in _KEYWORD_WORDS if k in (m.group("kw") or ""))
    # subtype: last capitalized word before "creature token" if present
    sub = re.findall(
        r"(\d+/\d+ [a-z ]*?)([A-Z][a-z]+(?: [A-Z][a-z]+)?)"
        r"(?: artifact)? creature token",
        m.string[m.start() :],
    )
    subtypes = frozenset(sub[0][1].split()) if sub else frozenset()
    spec = TokenSpec(
        name=" ".join(sorted(subtypes)) or "Token",
        power=int(m.group("p")),
        toughness=int(m.group("t")),
        colors=colors,
        types=frozenset({"Creature"} | ({"Artifact"} if m.group("art") else set())),
        subtypes=subtypes,
        keywords=kws,
        tapped=bool(m.group("tapped")),
    )
    return count, spec


@rule("601.2c")
def _target_spec(text: str) -> TargetSpec | None:
    """Parse one 'target <what>' phrase into a TargetSpec."""
    m = re.search(
        r"(?:up to (?P<upto>one|two|three) )?target (?P<what>[a-z' ]+?)"
        r"(?: an opponent controls| you control| you don't control)?"
        r"(?:$|[.,;])",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    what_raw = m.group("what").strip()
    controller = (
        "opponent"
        if "an opponent controls" in text
        else "you"
        if "you control" in text
        else "opponent"
        if "you don't control" in text
        else "any"
    )
    other = what_raw.startswith("another ")
    what_raw = what_raw.removeprefix("another ")
    mapping = [
        ("creature or planeswalker", "creature_or_planeswalker"),
        ("artifact or enchantment", "art_ench"),
        ("creature or enchantment", "cre_ench"),
        ("nonland permanent", "nonland"),
        ("permanent", "permanent"),
        ("creature", "creature"),
        ("artifact", "artifact"),
        ("enchantment", "enchantment"),
        ("planeswalker", "creature_or_planeswalker"),
        ("player", "player"),
        ("opponent", "opponent"),
        ("spell", "spell"),
        ("land", "land"),
    ]
    what = next((v for k, v in mapping if k in what_raw), None)
    if what is None:
        return None
    count = _num(m.group("upto") or "one")
    return TargetSpec(
        what=what,
        controller=controller,
        other=other,
        optional=bool(m.group("upto")),
        count=count if isinstance(count, int) else 1,
    )


@dataclass
class _Clause:
    """One effect sentence handed to the clause-parser table."""

    clause: str  # stripped, trailing dot removed, original case
    low: str  # lowercased form of `clause`
    name: str  # the card name (self-reference detection)
    #: growing target list of the whole effect text; handlers append their
    #: TargetSpec and index into it
    targets: list[TargetSpec] = field(default_factory=list)

    def short_name(self) -> str:
        """Return the card's short (pre-comma) name, lowercased."""
        return self.name.split(",", maxsplit=1)[0].lower()


#: one entry of the clause-parser table
type ClauseParser = Callable[[_Clause], Effect | None]


def _p_create_creature_tokens(c: _Clause) -> Effect | None:
    """'Create N P/T ... creature tokens ...'."""
    m = _TOKEN_RE.search(c.clause)
    if not m:
        return None
    count, spec = _parse_token_clause(m)
    return CreateTokens(count, spec)


def _p_create_treasures(c: _Clause) -> Effect | None:
    """'Create N (tapped) Treasure tokens.'."""
    m = re.search(r"create (a|one|two|three|x|\d+) (tapped )?treasure tokens?", c.low)
    if not m:
        return None
    return CreateTokens(_num(m.group(1)), TREASURE, tapped=bool(m.group(2)) or None)


def _p_draw(c: _Clause) -> Effect | None:
    """'(Each player) draw(s) N cards.'."""
    m = re.search(r"draw (a|one|two|three|four|x|\d+) cards?", c.low)
    if not m:
        return None
    who = "each" if "each player" in c.low else "you"
    return DrawCards(_num(m.group(1)), who)


def _p_each_opponent_loses(c: _Clause) -> Effect | None:
    """'Each opponent loses N life (you gain that much).'."""
    m = re.search(r"each opponent loses (a|one|two|three|x|\d+) life", c.low)
    if not m:
        return None
    n = _num(m.group(1))
    if "you gain" in c.low and ("that much" in c.low or "equal" in c.low):
        return Drain(n)
    return LoseLife(n, "each_opponent")


def _p_gain_life(c: _Clause) -> Effect | None:
    """'You gain N life.'."""
    m = re.search(r"you gain (\d+|x) life", c.low)
    return GainLife(_num(m.group(1))) if m else None


def _p_lose_life(c: _Clause) -> Effect | None:
    """'You lose N life.'."""
    m = re.search(r"you lose (\d+) life", c.low)
    return LoseLife(int(m.group(1)), "you") if m else None


def _p_destroy_all_creatures(c: _Clause) -> Effect | None:
    """'Destroy all creatures.'."""
    if re.search(r"destroy all creatures", c.low):
        return Destroy(all_of="creatures")
    return None


def _p_damage_each_creature(c: _Clause) -> Effect | None:
    """'... deals N damage to each creature.'."""
    m = re.search(r"deals? (\d+|x) damage to each creature", c.low)
    return DealDamage(_num(m.group(1)), "each_creature") if m else None


def _p_damage(c: _Clause) -> Effect | None:
    """'... deals N damage to <target|divided|each opponent>'."""
    m = re.search(r"deals? (\d+|x) damage(?:,| to)", c.low)
    if not m:
        return None
    spec = _target_spec(c.clause)
    if spec:
        c.targets.append(spec)
        return DealDamage(_num(m.group(1)), "target")
    if "divided" in c.low:
        return DealDamage(_num(m.group(1)), "divided")
    if "each opponent" in c.low:
        return LoseLife(_num(m.group(1)), "each_opponent")
    return None


def _p_destroy_target(c: _Clause) -> Effect | None:
    """'Destroy target <what>.'."""
    if not (c.low.startswith("destroy target") or " destroy target" in c.low):
        return None
    spec = _target_spec(c.clause)
    if spec:
        c.targets.append(spec)
        return Destroy(index=len(c.targets) - 1)
    return None


def _p_exile_target(c: _Clause) -> Effect | None:
    """'Exile target <what>.'."""
    if not (c.low.startswith("exile target") or " exile target" in c.low):
        return None
    spec = _target_spec(c.clause)
    if spec:
        c.targets.append(spec)
        return ExileObj(index=len(c.targets) - 1)
    return None


def _p_counter_spell(c: _Clause) -> Effect | None:
    """'Counter target ... spell.'."""
    if re.search(r"counter target .*spell", c.low):
        c.targets.append(TargetSpec(what="spell"))
        return CounterSpell()
    return None


def _p_copy_spell(c: _Clause) -> Effect | None:
    """'Copy target instant or sorcery spell.'."""
    if re.search(r"copy target (?:instant(?: or sorcery)?|sorcery) spell", c.low):
        c.targets.append(TargetSpec(what="spell"))
        return CopySpell(index=len(c.targets) - 1)
    return None


def _p_tap_target(c: _Clause) -> Effect | None:
    """'Tap target creature.'."""
    if not re.match(r"tap target creature", c.low):
        return None
    spec = _target_spec("target creature")
    if spec is None:  # pragma: no cover - the fixed phrase always parses
        return None
    c.targets.append(spec)
    return TapTarget(index=len(c.targets) - 1)


def _p_rider_basic_land(c: _Clause) -> Effect | None:
    """Parse the its-controller-may-fetch-a-basic-land rider."""
    if re.match(
        r"its controller may search their library for a basic land card",
        c.low,
    ):
        return TargetControllerBasicLand()
    return None


def _p_rider_gain_power(c: _Clause) -> Effect | None:
    """Parse the its-controller-gains-life-equal-to-power rider."""
    if re.match(r"its controller gains life equal to its power", c.low):
        return TargetControllerGainsPower()
    return None


def _p_rider_lose_mv(c: _Clause) -> Effect | None:
    """Parse the you-lose-life-equal-to-its-mana-value rider."""
    if re.match(r"you lose life equal to that permanent's mana value", c.low):
        return LoseLifeTargetMV()
    return None


def _p_put_land_from_hand(c: _Clause) -> Effect | None:
    """'You may put a land card from your hand onto the battlefield.'."""
    if re.match(
        r"you may put a land card from your hand(?: or graveyard)?"
        r" onto the battlefield tapped",
        c.low,
    ):
        # graveyard option simplified to hand-only
        return PutLandFromHand(tapped=True)
    return None


def _p_take_dead_creature(c: _Clause) -> Effect | None:
    """'You may put that card onto the battlefield under your control.'."""
    if re.match(
        r"you may put that card onto the battlefield under your control",
        c.low,
    ):
        return TakeDeadCreature()
    return None


def _p_put_counters(c: _Clause) -> Effect | None:
    """'Put N +1/+1 (or -1/-1) counters on <self|target|each ...>'."""
    m = re.search(r"put (a|one|two|three|x|\d+) ([+-]1/[+-]1) counters? on", c.low)
    if not m:
        return None
    n, kind = _num(m.group(1)), m.group(2)
    if (
        "each creature you don't control" in c.low
        or "each creature your opponents control" in c.low
    ):
        return PutCounters(kind, n, "each_opponent_creature")
    if "each creature" in c.low or "each other creature" in c.low:
        return PutCounters(kind, n, "each_creature")
    if "on it" in c.low or f"on {c.short_name()}" in c.low:
        return PutCounters(kind, n, "self")
    spec = _target_spec(c.clause)
    if spec:
        c.targets.append(spec)
        return PutCounters(kind, n, "target")
    return PutCounters(kind, n, "self")


def _p_proliferate(c: _Clause) -> Effect | None:
    """'Proliferate (twice).'."""
    if "proliferate" in c.low:
        times = 2 if "proliferate twice" in c.low else 1
        return Proliferate(times)
    return None


def _p_populate(c: _Clause) -> Effect | None:
    """'Populate.'."""
    return Populate() if re.match(r"populate", c.low) else None


def _p_search_lands(c: _Clause) -> Effect | None:
    """'Search your library for (up to) N ... land(s) ...'."""
    m = re.search(
        r"search your library for (?:up to )?(a|an|one|two|three|x)"
        r"[^.]*?(land|plains|island|swamp|mountain|forest)",
        c.low,
    )
    if not m:
        return None
    tapped = "tapped" in c.low
    to_hand = "hand" in c.low and "battlefield" not in c.low
    basic = "basic" in c.low or m.group(2) != "land"
    return SearchLands(
        _num(m.group(1)),
        tapped=tapped,
        to_hand=to_hand,
        basic_only=basic,
    )


def _p_return_own_land(c: _Clause) -> Effect | None:
    """Bounce lands: 'return a land you control to its owner's hand'."""
    if re.match(r"return a land you control to (?:its|their) owner", c.low):
        return ReturnToHand(self_land=True)
    return None


def _p_scry(c: _Clause) -> Effect | None:
    """'Scry N.'."""
    m = re.search(r"scry (\d+)", c.low)
    return Scry(int(m.group(1))) if m else None


def _p_tutor(c: _Clause) -> Effect | None:
    """'Search your library for a card ... hand.'."""
    if re.search(r"search your library for a card.*hand", c.low):
        return TutorAny()
    return None


def _p_return_target_to_hand(c: _Clause) -> Effect | None:
    """'Return target ... to its owner's hand.'."""
    if not re.search(
        r"return target .* to (?:its owner's|their owners?') hands?",
        c.low,
    ):
        return None
    spec = _target_spec(c.clause)
    if spec:
        c.targets.append(spec)
        return ReturnToHand(index=len(c.targets) - 1)
    return None


def _p_energy(c: _Clause) -> Effect | None:
    """'You get {E}{E}...'."""
    if re.search(r"you get (\{e\})+", c.low):
        return EnergyGain(c.low.count("{e}"))
    return None


def _p_pump_all(c: _Clause) -> Effect | None:
    """'Creatures you control get +N/+N until end of turn.'."""
    m = re.search(
        r"creatures you control get \+(\d+)/\+(\d+) until end of turn",
        c.low,
    )
    return PumpAll(int(m.group(1)), int(m.group(2))) if m else None


def _p_protect_all(c: _Clause) -> Effect | None:
    """Akroma's-Will-style team protection clauses."""
    if "gain hexproof and indestructible" in c.low or (
        "permanents you control gain" in c.low and "protection" in c.low
    ):
        return ProtectAll()
    return None


def _p_sacrifice_self(c: _Clause) -> Effect | None:
    """'Sacrifice <this card>.'."""
    if c.low.startswith("sacrifice ") and c.short_name() in c.low:
        return SacrificeSelf()
    return None


#: ordered clause-parser table: first handler to return an Effect wins
_CLAUSE_PARSERS: tuple[ClauseParser, ...] = (
    _p_create_creature_tokens,
    _p_create_treasures,
    _p_draw,
    _p_each_opponent_loses,
    _p_gain_life,
    _p_lose_life,
    _p_destroy_all_creatures,
    _p_damage_each_creature,
    _p_damage,
    _p_destroy_target,
    _p_exile_target,
    _p_counter_spell,
    _p_copy_spell,
    _p_tap_target,
    _p_rider_basic_land,
    _p_rider_gain_power,
    _p_rider_lose_mv,
    _p_put_land_from_hand,
    _p_take_dead_creature,
    _p_put_counters,
    _p_proliferate,
    _p_populate,
    _p_search_lands,
    _p_return_own_land,
    _p_scry,
    _p_tutor,
    _p_return_target_to_hand,
    _p_energy,
    _p_pump_all,
    _p_protect_all,
    _p_sacrifice_self,
)


def parse_effect_clause(
    clause: str,
    name: str,
    targets: list[TargetSpec],
) -> Effect | None:
    """One sentence -> Effect node (or None if unrecognized)."""
    stripped = clause.strip().rstrip(".")
    low = stripped.lower()
    if not low:
        return None
    c = _Clause(clause=stripped, low=low, name=name, targets=targets)
    for parser in _CLAUSE_PARSERS:
        eff = parser(c)
        if eff is not None:
            return eff
    return None


#: riders that are true no-ops in this engine (mechanics it never uses,
#: e.g. there is no regeneration), so they need no unknown-clause report
_INERT_RIDERS = re.compile(
    r"^(it can't be regenerated|cycling |evoke |compleated$|"
    r"as long as you control|shuffle|activate only|"
    r"you may choose new targets|do this only once each turn)",
    re.IGNORECASE,
)


def parse_effect_text(text: str, name: str) -> tuple[Effect, list[TargetSpec]]:
    """Full effect text -> (Effect, [TargetSpec, ...])."""
    targets: list[TargetSpec] = []
    parts: list[Effect] = []
    for raw_sentence in re.split(r"(?<=[.;])\s+", text):
        sentence = raw_sentence.strip()
        if not sentence:
            continue
        low = sentence.lower().rstrip(".")
        # "You gain life equal to the life lost this way." merges the
        # preceding each-opponent life loss into a Drain (Exsanguinate)
        last = parts[-1] if parts else None
        if (
            re.match(
                r"you gain life equal to the (?:life lost|total life"
                r" lost)(?: this way)?",
                low,
            )
            and isinstance(last, LoseLife)
            and last.who == "each_opponent"
        ):
            parts[-1] = Drain(last.amount)
            continue
        eff = parse_effect_clause(sentence, name, targets)
        if eff is None:
            if not _INERT_RIDERS.match(sentence):
                _note(name, sentence)
            parts.append(Noop(sentence))
        else:
            parts.append(eff)
    effect = parts[0] if len(parts) == 1 else Sequence(parts)
    return effect, targets


# ---------------------------------------------------------------- triggers

_TRIGGER_TABLE: list[tuple[str, Callable[[], TriggerSpec]]] = [
    (
        (
            r"^when(?:ever)? (?:this creature|this permanent|this artifact"
            r"|this enchantment|this land|~|{name}) enters"
        ),
        lambda: TriggerSpec(EventType.ENTERS_BATTLEFIELD),
    ),
    (
        r"^when(?:ever)? (?:this creature|this permanent|~|{name}) dies",
        lambda: TriggerSpec(EventType.DIES),
    ),
    (
        r"^when(?:ever)? (?:this creature|~|{name}) attacks",
        lambda: TriggerSpec(EventType.ATTACKS),
    ),
    (
        r"^at the beginning of your upkeep",
        lambda: TriggerSpec(EventType.BEGIN_STEP, condition=_step("upkeep", mine=True)),
    ),
    (
        r"^at the beginning of each (?:player's )?upkeep",
        lambda: TriggerSpec(EventType.BEGIN_STEP, condition=_step("upkeep")),
    ),
    (
        r"^at the beginning of your end step",
        lambda: TriggerSpec(EventType.BEGIN_STEP, condition=_step("end", mine=True)),
    ),
    (
        r"^at the beginning of combat on your turn",
        lambda: TriggerSpec(
            EventType.BEGIN_STEP,
            condition=_step("combat_begin", mine=True),
        ),
    ),
    (
        r"^when(?:ever)? another creature you control dies",
        lambda: TriggerSpec(EventType.DIES, condition=_dies(own=True, other=True)),
    ),
    (
        r"^when(?:ever)? another creature dies",
        lambda: TriggerSpec(EventType.DIES, condition=_dies(other=True)),
    ),
    (
        r"^when(?:ever)? a creature you control dies",
        lambda: TriggerSpec(EventType.DIES, condition=_dies(own=True)),
    ),
    (
        r"^when(?:ever)? a creature dies",
        lambda: TriggerSpec(EventType.DIES, condition=_dies()),
    ),
    (
        r"^when(?:ever)? a creature an opponent controls dies",
        lambda: TriggerSpec(EventType.DIES, condition=_dies(opponent=True)),
    ),
    (
        (
            r"^when(?:ever)? a creature an opponent controls with"
            r" (?:a|one or more) -1/-1 counters? on it dies"
        ),
        lambda: TriggerSpec(
            EventType.DIES,
            condition=_dies_with_counter(opponent=True, kind="-1/-1"),
        ),
    ),
    (
        (
            r"^when(?:ever)? a creature an opponent controls with"
            r" (?:a|one or more) counters? on it dies"
        ),
        lambda: TriggerSpec(
            EventType.DIES,
            condition=_dies_with_counter(opponent=True),
        ),
    ),
    (
        (
            r"^when(?:ever)? a creature you control with (?:a|one or more)"
            r" counters? on it dies"
        ),
        lambda: TriggerSpec(EventType.DIES, condition=_dies_with_counter(own=True)),
    ),
    (
        (
            r"^when(?:ever)? a creature with (?:a|one or more) -1/-1 counters?"
            r" on it dies"
        ),
        lambda: TriggerSpec(EventType.DIES, condition=_dies_with_counter(kind="-1/-1")),
    ),
    (
        (
            r"^landfall - when(?:ever)? a land you control enters|"
            r"^when(?:ever)? a land you control enters"
        ),
        lambda: TriggerSpec(EventType.LAND_PLAYED, condition=_own_event()),
    ),
    (
        r"^when(?:ever)? you create (?:a|one or more) tokens?",
        lambda: TriggerSpec(EventType.ENTERS_BATTLEFIELD, condition=_token_etb()),
    ),
]


def _step(step: str, *, mine: bool = False) -> TriggerCondition:
    """Condition: a BEGIN_STEP event for *step* (optionally our own)."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        if event.data.get("step") != step:
            return False
        return not mine or event.data.get("player") is source.controller

    return cond


def _dies(
    *,
    own: bool = False,
    other: bool = False,
    opponent: bool = False,
) -> TriggerCondition:
    """Condition: a creature died (optionally ours/another's/theirs)."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        if obj is None or "Creature" not in obj.base.types:
            return False
        if other and obj is source:
            return False
        if own and obj.controller is not source.controller:
            return False
        return not (opponent and obj.controller is source.controller)

    return cond


def _dies_with_counter(
    *,
    own: bool = False,
    opponent: bool = False,
    kind: str = "",
) -> TriggerCondition:
    """Condition: a countered creature died (rule 603.10a look-back).

    Checks the dead creature's last-known counters; lki_counters is
    captured on leaving the battlefield.
    """

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        if obj is None or "Creature" not in obj.base.types:
            return False
        if own and obj.controller is not source.controller:
            return False
        if opponent and obj.controller is source.controller:
            return False
        counters = getattr(obj, "lki_counters", {}) or {}
        if kind:
            return bool(counters.get(kind, 0) > 0)
        return any(n > 0 for n in counters.values())

    return cond


def _own_event() -> TriggerCondition:
    """Condition: the event's player is the ability's controller."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        p = event.data.get("player")
        return p is source.controller

    return cond


def _token_etb() -> TriggerCondition:
    """Condition: one of the controller's tokens entered."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return obj is not None and obj.is_token and obj.controller is source.controller

    return cond


def parse_trigger_line(line: str, name: str) -> TriggeredAbility | None:
    """Parse one 'When/Whenever/At ..., <effect>' oracle line."""
    low = line.lower()
    short = name.split(",", maxsplit=1)[0].lower()
    comma = low.find(", ")
    if comma < 0:
        return None
    head, body = low[:comma], line[comma + 2 :]
    once = bool(re.search(r"do this only once each turn\.?\s*$", body, re.IGNORECASE))
    for pattern, factory in _TRIGGER_TABLE:
        pat = pattern.replace("{name}", re.escape(short))
        if re.match(pat, head):
            effect, targets = parse_effect_text(body, name)
            return TriggeredAbility(
                trigger=factory(),
                effect=effect,
                targets=targets,
                text=line,
                optional=body.lower().startswith("you may"),
                once_each_turn=once,
            )
    return None


# ---------------------------------------------------------------- activated

_MINUS = "\u2212"


def parse_activated_line(line: str, name: str) -> ActivatedAbility | None:
    """Parse one '[Cost]: [Effect]' oracle line (rule 602.1)."""
    m = re.match(r"^cycling ((?:\{[^}]+\})+)\.?$", line, re.IGNORECASE)
    if m:  # rule 702.29a
        return ActivatedAbility(
            mana_cost=m.group(1),
            from_hand=True,
            effect=DrawCards(1),
            text=line,
        )
    m = re.match(rf"^([+{_MINUS}]?\d+|0): (.+)$", line)
    if m:  # loyalty ability
        n = int(m.group(1).replace(_MINUS, "-"))
        effect, targets = parse_effect_text(m.group(2), name)
        return ActivatedAbility(
            loyalty_cost=n,
            effect=effect,
            targets=targets,
            text=line,
        )
    m = re.match(r"^((?:\{[^}]+\})*(?:, )?[^:]*): (.+)$", line)
    if not m or ":" not in line:
        return None
    cost_part, body = line.split(":", 1)
    body = body.strip()
    mana = "".join(re.findall(r"\{[^}]+\}", cost_part.replace("{T}", "")))
    tap = "{T}" in cost_part
    sac = ""
    sm = re.search(r"sacrifice (a|an|another)?\s*([a-z' ]+)", cost_part, re.IGNORECASE)
    if sm:
        target = sm.group(2).strip().lower()
        sac = (
            "self"
            if target.startswith(name.split(",", maxsplit=1)[0].lower())
            or target in ("this creature", "this artifact", "it")
            else target
        )
    life = 0
    lm = re.search(r"pay (\d+) life", cost_part, re.IGNORECASE)
    if lm:
        life = int(lm.group(1))
    # mana ability?
    am = re.match(r"^add (.+)$", body, re.IGNORECASE)
    if am and tap:
        types = tuple(s for s in re.findall(r"\{([^}]+)\}", body) if s in "WUBRGC")
        any_color = "any color" in body.lower()
        cid = "commander's color identity" in body.lower()
        return ActivatedAbility(
            mana_cost=mana,
            tap_cost=tap,
            sac_cost=sac,
            life_cost=life,
            is_mana_ability=True,
            effect=AddMana(types=types, any_color=any_color, commander_identity=cid),
            text=line,
        )
    effect, targets = parse_effect_text(body, name)
    sorcery = (
        "only as a sorcery" in line.lower()
        or "only any time you could cast a sorcery" in line.lower()
    )
    return ActivatedAbility(
        mana_cost=mana,
        tap_cost=tap,
        sac_cost=sac,
        life_cost=life,
        effect=effect,
        targets=targets,
        sorcery_only=sorcery,
        text=line,
    )


# ---------------------------------------------------------------- statics


def parse_static_line(line: str, _name: str) -> StaticAbility | None:
    """Parse one static-ability oracle line into a StaticAbility."""
    low = line.lower()
    if re.match(r"^spells you control can't be countered\.?$", low):
        return StaticAbility(text=line, uncounterable_spells=True)
    m = re.match(
        r"^(creatures?|creature tokens?|artifact creatures?)"
        r" you control get \+(\d+)/\+(\d+)"
        r"(?: and have ([a-z ]+?))?\.?$",
        low,
    )
    if not m:
        return None
    granted = {
        k.strip()
        for k in (m.group(4) or "").split(" and ")
        if k.strip() in _KEYWORD_WORDS
    }
    if m.group(4) and not granted:
        return None  # unknown keyword grant
    return _anthem_static(
        line,
        boost=(int(m.group(2)), int(m.group(3))),
        granted=granted,
        tokens_only="token" in m.group(1),
        art_only="artifact" in m.group(1),
    )


def _anthem_static(
    line: str,
    *,
    boost: tuple[int, int],
    granted: set[str],
    tokens_only: bool,
    art_only: bool,
) -> StaticAbility:
    """Build the anthem StaticAbility for parse_static_line."""
    boost_p, boost_t = boost

    def continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
        me = source.controller

        def applies(_g: Game, obj: GameObject, ch: Characteristics) -> bool:
            if obj.controller is not me or "Creature" not in ch.types:
                return False
            if tokens_only and not obj.is_token:
                return False
            return not (art_only and "Artifact" not in ch.types)

        def boost(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.power = (ch.power or 0) + boost_p
            ch.toughness = (ch.toughness or 0) + boost_t

        def grant(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.keywords.update(granted)

        effects = [
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=source,
                applies_to=applies,
                apply=boost,
            ),
        ]
        if granted:
            effects.append(
                ContinuousEffect(
                    layer=6,
                    source=source,
                    applies_to=applies,
                    apply=grant,
                ),
            )
        return effects

    return StaticAbility(continuous=continuous, text=line)


# ---------------------------------------------------------------- keywords


def parse_keyword_line(line: str) -> set[str] | None:
    """Parse a keywords-only line into a set of keyword strings."""
    got = set()
    for part in re.split(r"[,;] ", line.rstrip(".")):
        p = part.strip().lower()
        p = re.sub(r"\s*\(.*\)$", "", p)
        if p in _KEYWORD_WORDS:
            got.add(p)
        elif m := re.match(r"^ward\s*\{(\d+)\}$", p):
            got.add(f"ward:{m.group(1)}")
        elif re.match(r"^ward\b", p):
            got.add("ward:2")
        elif m := re.match(r"^toxic (\d+)$", p):
            got.add(f"toxic:{m.group(1)}")
        elif p == "station":
            got.add("station")
        else:
            return None
    return got or None


# ---------------------------------------------------------------- compile


@rule("113.2")
def compile_card(ref: CardRef) -> Characteristics:
    """CardData (from the knowledge graph) -> compiled Characteristics.

    Overrides in overrides.py win per card.
    """
    ch = Characteristics(
        name=ref.name,
        mana_cost=ref.mana_cost,
        supertypes=set(ref.supertypes or ()),
        types=set(ref.types),
        subtypes=set(ref.subtypes),
        power=ref.power if isinstance(ref.power, int) else None,
        toughness=ref.toughness if isinstance(ref.toughness, int) else None,
        loyalty=ref.loyalty,
    )
    ch.colors = set(ref.color_identity) & set("WUBRG") if ref.mana_cost else set()

    if overrides.apply_override(ch, ref):
        return ch

    is_spell = bool(ch.types & {"Instant", "Sorcery"})
    spell_clauses = _compile_oracle_lines(ch, ref, is_spell=is_spell)
    if is_spell:
        text = " ".join(spell_clauses)
        effect, targets = parse_effect_text(text, ref.name)
        ch.abilities.append(SpellAbility(effect=effect, targets=targets, text=text))

    _apply_land_behavior(ch, ref)
    _add_keyword_abilities(ch)
    return ch


def _compile_oracle_lines(
    ch: Characteristics,
    ref: CardRef,
    *,
    is_spell: bool,
) -> list[str]:
    """Compile each oracle line onto *ch*; return leftover spell clauses."""
    name = ref.name
    spell_clauses: list[str] = []
    for raw_line in (ref.oracle or "").split("\n"):
        line = re.sub(r"\s*\([^)]*\)", "", raw_line).strip()
        if not line:
            continue
        # taplands are modeled through the graph's :entersTapped fact
        # (conditional forms conservatively enter tapped, like the
        # heuristic engine)
        if re.match(
            r"^this land enters (the battlefield )?tapped",
            line,
            re.IGNORECASE,
        ):
            continue
        if _parse_cost_modifier_line(ch, line):
            continue
        kws = parse_keyword_line(line)
        if kws is not None:
            ch.keywords |= kws
            continue
        ab = parse_activated_line(line, name)
        if ab is not None:
            ch.abilities.append(ab)
            continue
        trig = parse_trigger_line(line, name)
        if trig is not None:
            ch.abilities.append(trig)
            continue
        static = parse_static_line(line, name)
        if static is not None:
            ch.abilities.append(static)
            continue
        if is_spell:
            spell_clauses.append(line)
            continue
        _note(name, line)
    return spell_clauses


@rule("601.2b", "601.2f")
def _parse_cost_modifier_line(ch: Characteristics, line: str) -> bool:
    """Cost-modifier lines: rule 601.2f reductions, 601.2b extra costs."""
    m = re.match(
        r"^this spell costs \{(\d+)\} less to cast for each"
        r" creature on the battlefield\.?$",
        line,
        re.IGNORECASE,
    )
    if m:  # rule 601.2f
        ch.cost_less_per_creature = int(m.group(1))
        return True
    m = re.match(
        r"^as an additional cost to cast this spell,"
        r" (sacrifice a creature|discard a card)\.?$",
        line,
        re.IGNORECASE,
    )
    if m:  # rule 601.2b
        ch.additional_cost = (
            "sacrifice_creature" if "sacrifice" in m.group(1) else "discard_card"
        )
        return True
    return False


def _apply_land_behavior(ch: Characteristics, ref: CardRef) -> None:
    """Graph land facts -> mana abilities, tapped markers, fetches."""
    if "Land" not in ch.types:
        return
    if ref.behavior.get("land_colors"):
        colors = set(ref.behavior["land_colors"])
        any_c = colors >= set("WUBRG")
        types = tuple(c for c in colors if c in "WUBRGC") if not any_c else ()
        if not any(
            isinstance(a, ActivatedAbility) and a.is_mana_ability for a in ch.abilities
        ):
            ch.abilities.append(
                ActivatedAbility(
                    tap_cost=True,
                    is_mana_ability=True,
                    effect=AddMana(types=types or ("C",), any_color=any_c),
                    text="{T}: Add mana.",
                ),
            )
    if ref.behavior.get("enters_tapped"):
        ch.abilities.append(
            StaticAbility(enters_tapped=True, text="enters the battlefield tapped"),
        )
    if ref.behavior.get("fetch_land"):
        ch.abilities.append(
            ActivatedAbility(
                tap_cost=True,
                sac_cost="self",
                effect=SearchLands(1, tapped=True),
                text="{T}, Sacrifice: fetch a basic land.",
            ),
        )


@rule("702.79")
def _add_keyword_abilities(ch: Characteristics) -> None:
    """Keywords that carry rules baggage beyond combat checks."""
    if "persist" in ch.keywords:

        def persist_return(game: Game, ctx: Ctx) -> None:
            src = ctx.source
            if (
                src is not None
                and src.zone == Zone.GRAVEYARD
                and not src.is_token
                and src.lki_counters.get("-1/-1", 0) == 0
            ):
                src.controller = ctx.controller
                game.move_zone(src, Zone.BATTLEFIELD, counters={"-1/-1": 1})

        ch.abilities.append(
            TriggeredAbility(
                trigger=TriggerSpec(EventType.DIES),
                effect=Custom(persist_return),
                text="Persist (rule 702.79): return with a -1/-1 counter",
            ),
        )
