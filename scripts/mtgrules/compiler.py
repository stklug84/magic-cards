"""Oracle text -> ability AST compiler (R2).

Compiles each card's :oracleText from the knowledge graph into structured
abilities (SpellAbility / ActivatedAbility / TriggeredAbility /
StaticAbility) executed by the rules engine. The grammar covers the
dominant templates of the deck pool; complex cards get hand-written
implementations in overrides.py. Clauses neither compiled nor overridden
are recorded as unknown (Noop) and reported by the adapter - nothing is
skipped silently.
"""

from __future__ import annotations

import re

from .abilities import (ActivatedAbility, SpellAbility, StaticAbility,
                        TargetSpec, TokenSpec, TriggeredAbility, TriggerSpec,
                        TREASURE)
from .cr import rule
from .effects import (AddMana, CounterSpell, CreateTokens, DealDamage,
                      Destroy, Drain, DrawCards, EnergyGain, ExileObj,
                      GainLife, LoseLife, Noop, Populate, Proliferate,
                      ProtectAll, PumpAll, PutCounters, ReturnToHand,
                      SacrificeSelf, Scry, SearchLands, Sequence, TutorAny)
from .events import EventType
from .objects import Characteristics

#: filled by compile_card: card name -> set of uncompiled clauses
UNKNOWN_CLAUSES: dict[str, set] = {}

_KEYWORD_WORDS = {
    "flying", "vigilance", "trample", "haste", "deathtouch", "lifelink",
    "menace", "reach", "defender", "indestructible", "hexproof", "flash",
    "first strike", "double strike", "wither", "infect", "islandwalk",
    "fear", "intimidate", "persist", "exalted",
}

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "x": "x"}


def _num(word):
    w = word.lower()
    if w in _NUM:
        return _NUM[w]
    return int(w) if w.isdigit() else 1


def _note(name, clause):
    UNKNOWN_CLAUSES.setdefault(name, set()).add(clause)


# ---------------------------------------------------------------- effects

_TOKEN_RE = re.compile(
    r"create (a|an|one|two|three|four|five|x|\d+)"
    r"(?P<tapped> tapped)?[^.]*?"
    r"(?P<p>\d+)/(?P<t>\d+) (?P<colors>[a-z ]*?)"
    r"(?P<art>artifact )?creature tokens?"
    r"(?P<kw> with [a-z ]+)?", re.I)
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R",
                "green": "G"}


def _parse_token_clause(m) -> tuple:
    count = _num(m.group(1))
    colors = frozenset(c for w, c in _COLOR_WORDS.items()
                       if w in (m.group("colors") or ""))
    kws = frozenset(k for k in _KEYWORD_WORDS
                    if k in (m.group("kw") or ""))
    # subtype: last capitalized word before "creature token" if present
    sub = re.findall(r"(\d+/\d+ [a-z ]*?)([A-Z][a-z]+(?: [A-Z][a-z]+)?)"
                     r"(?: artifact)? creature token", m.string[m.start():])
    subtypes = frozenset(sub[0][1].split()) if sub else frozenset()
    spec = TokenSpec(name=" ".join(sorted(subtypes)) or "Token",
                     power=int(m.group("p")), toughness=int(m.group("t")),
                     colors=colors,
                     types=frozenset({"Creature"} | ({"Artifact"}
                                     if m.group("art") else set())),
                     subtypes=subtypes, keywords=kws,
                     tapped=bool(m.group("tapped")))
    return count, spec


@rule("601.2c")
def _target_spec(text) -> TargetSpec | None:
    m = re.search(
        r"(?:up to (?P<upto>one|two|three) )?target (?P<what>[a-z' ]+?)"
        r"(?: an opponent controls| you control| you don't control)?"
        r"(?:$|[.,;])", text, re.I)
    if not m:
        return None
    what_raw = m.group("what").strip()
    controller = ("opponent" if "an opponent controls" in text
                  else "you" if "you control" in text
                  else "opponent" if "you don't control" in text else "any")
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
    return TargetSpec(what=what, controller=controller, other=other,
                      optional=bool(m.group("upto")),
                      count=_num(m.group("upto") or "one"))


def parse_effect_clause(clause: str, name: str, targets: list):
    """One sentence -> Effect node (or None if unrecognized)."""
    c = clause.strip().rstrip(".")
    low = c.lower()
    if not low:
        return None

    m = _TOKEN_RE.search(c)
    if m:
        count, spec = _parse_token_clause(m)
        return CreateTokens(count, spec)
    m = re.search(r"create (a|one|two|three|x|\d+) (?:tapped )?"
                  r"treasure tokens?", low)
    if m:
        return CreateTokens(_num(m.group(1)), TREASURE)
    m = re.search(r"draw (a|one|two|three|four|x|\d+) cards?", low)
    if m:
        who = "each" if "each player" in low else "you"
        return DrawCards(_num(m.group(1)), who)
    m = re.search(r"each opponent loses (a|one|two|three|x|\d+) life", low)
    if m:
        n = _num(m.group(1))
        if "you gain" in low and ("that much" in low or "equal" in low):
            return Drain(n)
        return LoseLife(n, "each_opponent")
    m = re.search(r"you gain (\d+|x) life", low)
    if m:
        return GainLife(_num(m.group(1)))
    m = re.search(r"you lose (\d+) life", low)
    if m:
        return LoseLife(int(m.group(1)), "you")
    if re.search(r"destroy all creatures", low):
        return Destroy(all_of="creatures")
    m = re.search(r"deals? (\d+|x) damage to each creature", low)
    if m:
        return DealDamage(_num(m.group(1)), "each_creature")
    m = re.search(r"deals? (\d+|x) damage(?:,| to)", low)
    if m:
        spec = _target_spec(c)
        if spec:
            targets.append(spec)
            return DealDamage(_num(m.group(1)), "target")
        if "divided" in low:
            return DealDamage(_num(m.group(1)), "divided")
        if "each opponent" in low:
            return LoseLife(_num(m.group(1)), "each_opponent")
    if low.startswith("destroy target") or " destroy target" in low:
        spec = _target_spec(c)
        if spec:
            targets.append(spec)
            return Destroy(index=len(targets) - 1)
    if low.startswith("exile target") or " exile target" in low:
        spec = _target_spec(c)
        if spec:
            targets.append(spec)
            return ExileObj(index=len(targets) - 1)
    if re.search(r"counter target .*spell", low):
        targets.append(TargetSpec(what="spell"))
        return CounterSpell()
    m = re.search(r"put (a|one|two|three|x|\d+) ([+-]1/[+-]1) counters? on",
                  low)
    if m:
        n, kind = _num(m.group(1)), m.group(2)
        if "each creature you don't control" in low \
                or "each creature your opponents control" in low:
            return PutCounters(kind, n, "each_opponent_creature")
        if "each creature" in low or "each other creature" in low:
            return PutCounters(kind, n, "each_creature")
        if "on it" in low or f"on {name.split(',')[0].lower()}" in low:
            return PutCounters(kind, n, "self")
        spec = _target_spec(c)
        if spec:
            targets.append(spec)
            return PutCounters(kind, n, "target")
        return PutCounters(kind, n, "self")
    if "proliferate" in low:
        times = 2 if "proliferate twice" in low else 1
        return Proliferate(times)
    if re.match(r"populate", low):
        return Populate()
    m = re.search(
        r"search your library for (?:up to )?(a|an|one|two|three|x)"
        r"[^.]*?(land|plains|island|swamp|mountain|forest)", low)
    if m:
        tapped = "tapped" in low
        to_hand = "hand" in low and "battlefield" not in low
        basic = "basic" in low or m.group(2) != "land"
        return SearchLands(_num(m.group(1)), tapped=tapped, to_hand=to_hand,
                           basic_only=basic)
    if re.match(r"return a land you control to (?:its|their) owner", low):
        return ReturnToHand(self_land=True)
    m = re.search(r"scry (\d+)", low)
    if m:
        return Scry(int(m.group(1)))
    if re.search(r"search your library for a card.*hand", low):
        return TutorAny()
    if re.search(r"return target .* to (?:its owner's|their owners?')"
                 r" hands?", low):
        spec = _target_spec(c)
        if spec:
            targets.append(spec)
            return ReturnToHand(index=len(targets) - 1)
    m = re.search(r"you get (\{e\})+", low)
    if m:
        return EnergyGain(low.count("{e}"))
    m = re.search(r"creatures you control get \+(\d+)/\+(\d+)"
                  r" until end of turn", low)
    if m:
        return PumpAll(int(m.group(1)), int(m.group(2)))
    if "gain hexproof and indestructible" in low \
            or ("permanents you control gain" in low
                and "protection" in low):
        return ProtectAll()
    if low.startswith("sacrifice ") and name.split(",")[0].lower() in low:
        return SacrificeSelf()
    return None


#: riders that are true no-ops in this engine (mechanics it never uses,
#: e.g. there is no regeneration), so they need no unknown-clause report
_INERT_RIDERS = re.compile(
    r"^(it can't be regenerated|cycling |evoke |compleated$|"
    r"as long as you control|shuffle)", re.I)


def parse_effect_text(text: str, name: str) -> tuple:
    """Full effect text -> (Effect, [TargetSpec, ...])."""
    targets: list = []
    parts = []
    for sentence in re.split(r"(?<=[.;])\s+", text):
        sentence = sentence.strip()
        if not sentence:
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

_TRIGGER_TABLE = [
    (r"^when(?:ever)? (?:this creature|this permanent|this artifact"
     r"|this enchantment|this land|~|{name}) enters",
     lambda: TriggerSpec(EventType.ENTERS_BATTLEFIELD)),
    (r"^when(?:ever)? (?:this creature|this permanent|~|{name}) dies",
     lambda: TriggerSpec(EventType.DIES)),
    (r"^when(?:ever)? (?:this creature|~|{name}) attacks",
     lambda: TriggerSpec(EventType.ATTACKS)),
    (r"^at the beginning of your upkeep",
     lambda: TriggerSpec(EventType.BEGIN_STEP, condition=_step("upkeep",
                                                               mine=True))),
    (r"^at the beginning of each (?:player's )?upkeep",
     lambda: TriggerSpec(EventType.BEGIN_STEP, condition=_step("upkeep"))),
    (r"^at the beginning of your end step",
     lambda: TriggerSpec(EventType.BEGIN_STEP, condition=_step("end",
                                                               mine=True))),
    (r"^at the beginning of combat on your turn",
     lambda: TriggerSpec(EventType.BEGIN_STEP,
                         condition=_step("combat_begin", mine=True))),
    (r"^when(?:ever)? another creature you control dies",
     lambda: TriggerSpec(EventType.DIES, condition=_dies(own=True,
                                                         other=True))),
    (r"^when(?:ever)? another creature dies",
     lambda: TriggerSpec(EventType.DIES, condition=_dies(other=True))),
    (r"^when(?:ever)? a creature you control dies",
     lambda: TriggerSpec(EventType.DIES, condition=_dies(own=True))),
    (r"^when(?:ever)? a creature dies",
     lambda: TriggerSpec(EventType.DIES, condition=_dies())),
    (r"^when(?:ever)? a creature an opponent controls dies",
     lambda: TriggerSpec(EventType.DIES, condition=_dies(opponent=True))),
    (r"^landfall - when(?:ever)? a land you control enters|"
     r"^when(?:ever)? a land you control enters",
     lambda: TriggerSpec(EventType.LAND_PLAYED, condition=_own_event())),
    (r"^when(?:ever)? you create (?:a|one or more) tokens?",
     lambda: TriggerSpec(EventType.ENTERS_BATTLEFIELD,
                         condition=_token_etb())),
]


def _step(step, mine=False):
    def cond(game, source, event):
        if event.data.get("step") != step:
            return False
        return not mine or event.data.get("player") is source.controller
    return cond


def _dies(own=False, other=False, opponent=False):
    def cond(game, source, event):
        obj = event.data.get("obj")
        if obj is None or "Creature" not in obj.base.types:
            return False
        if other and obj is source:
            return False
        if own and obj.controller is not source.controller:
            return False
        if opponent and obj.controller is source.controller:
            return False
        return True
    return cond


def _own_event():
    def cond(game, source, event):
        p = event.data.get("player")
        return p is source.controller
    return cond


def _token_etb():
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and obj.is_token
                and obj.controller is source.controller)
    return cond


def parse_trigger_line(line: str, name: str):
    low = line.lower()
    short = name.split(",")[0].lower()
    comma = low.find(", ")
    if comma < 0:
        return None
    head, body = low[:comma], line[comma + 2:]
    for pattern, factory in _TRIGGER_TABLE:
        pat = pattern.replace("{name}", re.escape(short))
        if re.match(pat, head):
            effect, targets = parse_effect_text(body, name)
            return TriggeredAbility(
                trigger=factory(), effect=effect, targets=targets,
                text=line, optional=body.lower().startswith("you may"))
    return None


# ---------------------------------------------------------------- activated

_MINUS = "\u2212"


def parse_activated_line(line: str, name: str):
    m = re.match(r"^([+%s]?\d+|0): (.+)$" % _MINUS, line)
    if m:                                          # loyalty ability
        n = int(m.group(1).replace(_MINUS, "-"))
        effect, targets = parse_effect_text(m.group(2), name)
        return ActivatedAbility(loyalty_cost=n, effect=effect,
                                targets=targets, text=line)
    m = re.match(r"^((?:\{[^}]+\})*(?:, )?[^:]*): (.+)$", line)
    if not m or ":" not in line:
        return None
    cost_part, body = line.split(":", 1)
    body = body.strip()
    mana = "".join(re.findall(r"\{[^}]+\}", cost_part.replace("{T}", "")))
    tap = "{T}" in cost_part
    sac = ""
    sm = re.search(r"sacrifice (a|an|another)?\s*([a-z' ]+)",
                   cost_part, re.I)
    if sm:
        target = sm.group(2).strip().lower()
        sac = "self" if target.startswith(name.split(",")[0].lower()) \
            or target in ("this creature", "this artifact", "it") \
            else target
    life = 0
    lm = re.search(r"pay (\d+) life", cost_part, re.I)
    if lm:
        life = int(lm.group(1))
    # mana ability?
    am = re.match(r"^add (.+)$", body, re.I)
    if am and tap:
        types = tuple(s for s in re.findall(r"\{([^}]+)\}", body)
                      if s in "WUBRGC")
        any_color = "any color" in body.lower()
        cid = "commander's color identity" in body.lower()
        return ActivatedAbility(
            mana_cost=mana, tap_cost=tap, sac_cost=sac, life_cost=life,
            is_mana_ability=True,
            effect=AddMana(types=types, any_color=any_color,
                           commander_identity=cid),
            text=line)
    effect, targets = parse_effect_text(body, name)
    sorcery = "only as a sorcery" in line.lower() \
        or "only any time you could cast a sorcery" in line.lower()
    return ActivatedAbility(
        mana_cost=mana, tap_cost=tap, sac_cost=sac, life_cost=life,
        effect=effect, targets=targets, sorcery_only=sorcery, text=line)


# ---------------------------------------------------------------- statics

def parse_static_line(line: str, name: str):
    low = line.lower()
    m = re.match(r"^(creatures?|creature tokens?|artifact creatures?)"
                 r" you control get \+(\d+)/\+(\d+)\.?$", low)
    if m:
        boost_p, boost_t = int(m.group(2)), int(m.group(3))
        tokens_only = "token" in m.group(1)
        art_only = "artifact" in m.group(1)
        from .layers import ContinuousEffect

        def continuous(game, source):
            me = source.controller

            def applies(g, obj, ch):
                if obj.controller is not me or "Creature" not in ch.types:
                    return False
                if tokens_only and not obj.is_token:
                    return False
                if art_only and "Artifact" not in ch.types:
                    return False
                return True

            return [ContinuousEffect(
                layer=7, sublayer="c", source=source, applies_to=applies,
                apply=lambda g, o, ch: (
                    setattr(ch, "power", (ch.power or 0) + boost_p),
                    setattr(ch, "toughness", (ch.toughness or 0) + boost_t)))]

        return StaticAbility(continuous=continuous, text=line)
    return None


# ---------------------------------------------------------------- keywords

def parse_keyword_line(line: str) -> set | None:
    """A line consisting only of keywords -> set of keyword strings."""
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
        elif p == "station":
            got.add("station")
        else:
            return None
    return got or None


# ---------------------------------------------------------------- compile

@rule("113.2")
def compile_card(ref) -> Characteristics:
    """CardData (from the knowledge graph) -> base Characteristics with
    compiled abilities. Overrides in overrides.py win per card."""
    from . import overrides

    ch = Characteristics(
        name=ref.name, mana_cost=ref.mana_cost,
        supertypes=set(getattr(ref, "supertypes", ()) or ()),
        types=set(ref.types), subtypes=set(ref.subtypes),
        power=ref.power if isinstance(ref.power, int) else None,
        toughness=ref.toughness if isinstance(ref.toughness, int) else None,
        loyalty=getattr(ref, "loyalty", None))
    ch.colors = {c for c in ref.color_identity} & set("WUBRG") \
        if ref.mana_cost else set()

    if overrides.apply_override(ch, ref):
        return ch

    name = ref.name
    is_spell = bool(ch.types & {"Instant", "Sorcery"})
    spell_clauses = []
    for raw_line in (ref.oracle or "").split("\n"):
        line = re.sub(r"\s*\([^)]*\)", "", raw_line).strip()
        if not line:
            continue
        # taplands are modeled through the graph's :entersTapped fact
        # (conditional forms conservatively enter tapped, like the
        # heuristic engine)
        if re.match(r"^this land enters (the battlefield )?tapped",
                    line, re.I):
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

    if is_spell:
        text = " ".join(spell_clauses)
        effect, targets = parse_effect_text(text, name)
        ch.abilities.append(SpellAbility(effect=effect, targets=targets,
                                         text=text))

    # graph mana facts -> land mana abilities
    if "Land" in ch.types and ref.behavior.get("land_colors"):
        colors = set(ref.behavior["land_colors"])
        any_c = colors >= set("WUBRG")
        types = tuple(c for c in colors if c in "WUBRGC") \
            if not any_c else ()
        if not any(getattr(a, "is_mana_ability", False)
                   for a in ch.abilities):
            ch.abilities.append(ActivatedAbility(
                tap_cost=True, is_mana_ability=True,
                effect=AddMana(types=types or ("C",), any_color=any_c),
                text="{T}: Add mana."))
    if "Land" in ch.types and ref.behavior.get("enters_tapped"):
        ab = ActivatedAbility(text="(enters tapped)")
        ab.enters_tapped = True
        ch.abilities.append(_EntersTappedMarker())
    if "Land" in ch.types and ref.behavior.get("fetch_land"):
        ch.abilities.append(ActivatedAbility(
            tap_cost=True, sac_cost="self",
            effect=SearchLands(1, tapped=True),
            text="{T}, Sacrifice: fetch a basic land."))
    _add_keyword_abilities(ch)
    return ch


@rule("702.79")
def _add_keyword_abilities(ch):
    """Keywords that carry rules baggage beyond combat checks."""
    if "persist" in ch.keywords:
        from .overrides import Custom
        from .objects import Zone

        def persist_return(game, ctx):
            src = ctx.source
            if src.zone == Zone.GRAVEYARD and not src.is_token \
                    and src.lki_counters.get("-1/-1", 0) == 0:
                src.controller = ctx.controller
                game.move_zone(src, Zone.BATTLEFIELD,
                               counters={"-1/-1": 1})

        ch.abilities.append(TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES),
            effect=Custom(persist_return),
            text="Persist (rule 702.79): return with a -1/-1 counter"))


class _EntersTappedMarker:
    """Marker consumed by Game.play_land / ETB replacement."""
    kind = "static"
    continuous = None
    replacement = None
    enters_tapped = True
    text = "enters the battlefield tapped"
