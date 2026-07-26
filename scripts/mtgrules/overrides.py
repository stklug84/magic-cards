"""Hand-written card implementations beyond the compiler grammar.

The rules-engine equivalent of per-card scripts in mature engines: each
entry builds real abilities from the card's actual oracle text.
Simplifications are marked SIMPLIFIED and reported via NOTES.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mtgrules.abilities import (
    CLUE,
    FOOD,
    TREASURE,
    ActivatedAbility,
    SpellAbility,
    StaticAbility,
    TargetSpec,
    TokenSpec,
    TriggeredAbility,
    TriggerSpec,
)
from mtgrules.effects import (
    AddMana,
    Blink,
    CounterSpell,
    CreateTokens,
    Ctx,
    Custom,
    DealDamage,
    Drain,
    DrawCards,
    Effect,
    GainLife,
    LoseLife,
    Noop,
    Populate,
    Proliferate,
    ProtectAll,
    PumpAll,
    PutCounters,
    Scry,
    SearchLands,
    Sequence,
)
from mtgrules.events import Event, EventType
from mtgrules.layers import ContinuousEffect
from mtgrules.objects import Characteristics, GameObject, Player, Zone
from mtgrules.replacements import Replacement

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mtgrules.abilities import Ability
    from mtgrules.game import Game
    from mtgrules.protocols import CardRef, TriggerCondition

#: card name -> note about any simplification made
NOTES: dict[str, str] = {}

#: one hand-written card implementation: mutates the compiled base
type OverrideFn = Callable[[Characteristics, CardRef], None]

#: Austere Command mode: destroy creatures with power 3 or greater
_AUSTERE_POWER_MIN = 3
#: Mechanized Production wins at eight same-name artifacts
_MECHANIZED_WIN_COPIES = 8
#: permanents with two or more colors count as multicolored (CR 105.4)
_MULTICOLORED_MIN = 2
#: Infinite Guideline Station animates at twelve charge counters
_STATION_FLIGHT_CHARGES = 12
#: Esix only swaps in a copy at least twice the token's P+T
_ESIX_UPGRADE_FACTOR = 2
#: Mentor of the Meek triggers for creatures with power 2 or less
_MEEK_MAX_POWER = 2


# ---------------------------------------------------------------- helpers


def _counters_put_cond(kind: str, *, own_only: bool | None = None) -> TriggerCondition:
    """Trigger on resolved PUT_COUNTERS events of a counter kind."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        if not event.data.get("resolved"):
            return False
        if event.data.get("kind") != kind:
            return False
        obj = event.data.get("obj")
        if obj is None or "Creature" not in obj.base.types:
            return False
        return not (own_only is True and obj.controller is not source.controller)

    return cond


def _token(  # noqa: PLR0913, PLR0917 - thin TokenSpec builder: one keyword per token characteristic
    name: str,
    p: int,
    t: int,
    colors: str = "",
    types: Iterable[str] = ("Creature",),
    subs: Iterable[str] = (),
    kws: Iterable[str] = (),
    *,
    tapped: bool = False,
    abilities: Iterable[Callable[[], Ability]] = (),
) -> TokenSpec:
    """Shorthand TokenSpec builder for the predefined tokens below."""
    return TokenSpec(
        name=name,
        power=p,
        toughness=t,
        colors=frozenset(colors),
        types=frozenset(types),
        subtypes=frozenset(subs),
        keywords=frozenset(kws),
        tapped=tapped,
        abilities=tuple(abilities),
    )


SNAKE = _token("Snake", 1, 1, "G", subs=("Snake",), kws=("deathtouch",))
INSECT = _token("Insect", 1, 1, "B", subs=("Insect",))
ELF = _token("Elf Warrior", 1, 1, "G", subs=("Elf", "Warrior"))
ZOMBIE = _token("Zombie", 2, 2, "B", subs=("Zombie",))
THOPTER = _token(
    "Thopter",
    1,
    1,
    "U",
    types=("Artifact", "Creature"),
    subs=("Thopter",),
    kws=("flying",),
)
SERVO = _token("Servo", 1, 1, "", types=("Artifact", "Creature"), subs=("Servo",))
SOLDIER = _token("Soldier", 1, 1, "W", subs=("Soldier",))
GNOME = _token("Gnome", 1, 1, "", types=("Artifact", "Creature"), subs=("Gnome",))
ROBOT = _token(
    "Robot",
    2,
    2,
    "",
    types=("Artifact", "Creature"),
    subs=("Robot",),
    tapped=True,
)
MYR = _token("Myr", 1, 1, "", types=("Artifact", "Creature"), subs=("Myr",))
GOLEM = _token(
    "Golem",
    4,
    4,
    "",
    types=("Artifact", "Creature"),
    subs=("Golem",),
    kws=("flying",),
)

#: (name, power, toughness, deeper chain) of one Reef Worm stage
type ReefChain = tuple[str, int, int, "ReefChain"] | None


def _reef_spec(
    name: str,
    p: int,
    t: int,
    next_factory: Callable[[], Ability] | None,
) -> TokenSpec:
    """One Reef Worm chain token, optionally carrying the next stage."""
    ab: tuple[Callable[[], Ability], ...] = ()
    if next_factory is not None:
        ab = (next_factory,)
    return _token(name, p, t, "U", subs=(name,), abilities=ab)


def _reef_chain(
    name: str,
    _p: int,
    _t: int,
    deeper: ReefChain,
) -> Callable[[], Ability]:
    """Fish -> Whale -> Kraken death chain (Reef Worm)."""

    def factory() -> Ability:
        spec = None
        if deeper:
            nname, np, nt, ndeeper = deeper
            spec = _reef_spec(nname, np, nt, _reef_chain(nname, np, nt, ndeeper))

        return TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES),
            effect=CreateTokens(1, spec) if spec else Noop(),
            text=f"When this {name} dies ...",
        )

    return factory


# ---------------------------------------------------------------- effects


class Reanimate(Effect):
    """Return the dying creature to the battlefield under your control.

    Used by Necroskitter.
    """

    def resolve(self, game: Game, ctx: Ctx) -> None:
        """Steal the dead creature (rule 603.10a look-back)."""
        obj = ctx.event_obj
        if obj is not None and obj.zone == Zone.GRAVEYARD and not obj.is_token:
            obj.controller = ctx.controller
            game.move_zone(obj, Zone.BATTLEFIELD)
            ctx.controller.stat("necroskitter_steals")


# ---------------------------------------------------------------- statics


def _grant_all_creatures(
    kw: str,
    *,
    controller_only: bool | None = None,
    others: bool = False,
) -> StaticAbility:
    """Build a static ability granting *kw* to (all) creatures."""

    def continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
        def applies(_g: Game, obj: GameObject, ch: Characteristics) -> bool:
            if "Creature" not in ch.types:
                return False
            if controller_only is True and obj.controller is not source.controller:
                return False
            return not (others and obj is source)

        return [
            ContinuousEffect(
                layer=6,
                source=source,
                applies_to=applies,
                apply=lambda _g, _o, ch: ch.keywords.add(kw),
            ),
        ]

    return StaticAbility(continuous=continuous, text=f"grant {kw}")


def _token_doubler(*, creature_only: bool = False, factor: int = 2) -> StaticAbility:
    """'If one or more tokens would be created ... instead' (rule 614.1c)."""

    def replacement(_game: Game, source: GameObject) -> list[Replacement]:
        def matches(_g: Game, event: Event) -> bool:
            if event.data.get("controller") is not source.controller:
                return False
            return not (creature_only and "Creature" not in event.data["spec"].types)

        def replace(_g: Game, event: Event) -> Event:
            event.data["count"] *= factor
            return event

        return [
            Replacement(
                EventType.CREATE_TOKEN,
                matches=matches,
                replace=replace,
                source=source,
            ),
        ]

    return StaticAbility(replacement=replacement, text="token doubler")


def _counter_doubler() -> StaticAbility:
    """'If one or more counters would be put ... twice that many instead'."""

    def replacement(_game: Game, source: GameObject) -> list[Replacement]:
        def matches(_g: Game, event: Event) -> bool:
            if event.data.get("resolved"):
                return False
            obj = event.data.get("obj")
            return obj is not None and obj.controller is source.controller

        def replace(_g: Game, event: Event) -> Event:
            event.data["count"] *= 2
            return event

        return [
            Replacement(
                EventType.PUT_COUNTERS,
                matches=matches,
                replace=replace,
                source=source,
            ),
        ]

    return StaticAbility(replacement=replacement, text="counter doubler")


# ---------------------------------------------------------------- registry


def _auntie_ool(ch: Characteristics, _ref: CardRef) -> None:
    """Ward-Blight 2 (SIMPLIFIED to Ward {2}); draw / drain on -1/-1."""
    NOTES[ch.name] = "Ward-Blight 2 simplified to Ward {2}"
    ch.keywords |= {"ward:2"}

    def effect(game: Game, ctx: Ctx) -> None:
        obj = ctx.event_obj
        if obj is None:
            return
        if obj.controller is ctx.controller:
            game.draw(ctx.controller, 1)
        else:
            game.lose_life(obj.controller, 1)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.PUT_COUNTERS,
                condition=_counters_put_cond("-1/-1"),
            ),
            effect=Custom(effect),
            text="Whenever one or more -1/-1 counters are put on a creature...",
        ),
    )


def _blowfly(ch: Characteristics, _ref: CardRef) -> None:
    """Blowfly Infestation: countered creature dies -> spread a counter."""
    spec = TargetSpec(what="creature")

    def cond(_game: Game, _source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            obj is not None
            and "Creature" in obj.base.types
            and obj.lki_counters.get("-1/-1", 0) > 0
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=cond),
            effect=PutCounters("-1/-1", 1, "target"),
            targets=[spec],
            text="Whenever a creature with a -1/-1 counter on it dies, put a "
            "-1/-1 counter on target creature.",
        ),
    )


def _necroskitter(ch: Characteristics, _ref: CardRef) -> None:
    """Necroskitter: steal opposing creatures that die with -1/-1."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            obj is not None
            and "Creature" in obj.base.types
            and obj.controller is not source.controller
            and obj.lki_counters.get("-1/-1", 0) > 0
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=cond),
            effect=Reanimate(),
            optional=True,
            text="Whenever a creature an opponent controls with a -1/-1 "
            "counter on it dies, you may return that card to the "
            "battlefield under your control.",
        ),
    )


def _hapatra(ch: Characteristics, _ref: CardRef) -> None:
    """Hapatra: combat damage -> counter; counter placed -> Snake."""

    def dmg_cond(_game: Game, source: GameObject, event: Event) -> bool:
        return bool(
            event.data.get("resolved")
            and event.data.get("combat")
            and event.data.get("source") is source
            and isinstance(event.data.get("target"), Player),
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DAMAGE, condition=dmg_cond),
            effect=PutCounters("-1/-1", 1, "target"),
            targets=[TargetSpec(what="creature")],
            text="combat damage to a player -> -1/-1 counter",
        ),
    )
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.PUT_COUNTERS,
                condition=_counters_put_cond("-1/-1"),
            ),
            effect=CreateTokens(1, SNAKE),
            text="-1/-1 placed -> snake",
            intervening_if=None,
        ),
    )


def _nest_of_scarabs(ch: Characteristics, _ref: CardRef) -> None:
    """Nest of Scarabs: -1/-1 counters placed -> Insects."""

    def cond(_game: Game, _source: GameObject, event: Event) -> bool:
        return bool(
            event.data.get("resolved")
            and event.data.get("kind") == "-1/-1"
            and event.data.get("obj") is not None,
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.PUT_COUNTERS, condition=cond),
            effect=CreateTokens(lambda _g, _c: 1, INSECT),
            text="-1/-1 placed -> insect",
        ),
    )
    NOTES[ch.name] = "one Insect per counter event (not per counter)"


def _flourishing_defenses(ch: Characteristics, _ref: CardRef) -> None:
    """Flourishing Defenses: -1/-1 counter placed -> Elf."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.PUT_COUNTERS,
                condition=_counters_put_cond("-1/-1"),
            ),
            effect=CreateTokens(1, ELF),
            text="-1/-1 placed -> elf",
        ),
    )


def _obelisk_spider(ch: Characteristics, _ref: CardRef) -> None:
    """Obelisk Spider: reach; -1/-1 placed -> drain 1."""
    ch.keywords.add("reach")
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.PUT_COUNTERS,
                condition=_counters_put_cond("-1/-1"),
            ),
            effect=Sequence([LoseLife(1, "each_opponent"), GainLife(1)]),
            text="-1/-1 placed -> drain 1",
        ),
    )
    NOTES[ch.name] = "drain simplified to any -1/-1 event"


def _midnight_banshee(ch: Characteristics, _ref: CardRef) -> None:
    """Midnight Banshee: wither; upkeep -1/-1 on nonblack creatures."""
    ch.keywords |= {"wither"}

    def effect(game: Game, ctx: Ctx) -> None:
        for obj in list(game.battlefield_objects()):
            c = obj.chars(game)
            if "Creature" in c.types and "B" not in c.colors and obj is not ctx.source:
                game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.BEGIN_STEP,
                condition=lambda _g, _s, e: e.data.get("step") == "upkeep",
            ),
            effect=Custom(effect),
            text="each upkeep: -1/-1 on each nonblack creature",
        ),
    )


def _carnifex_demon(ch: Characteristics, _ref: CardRef) -> None:
    """Carnifex Demon: enters with counters; spread them for {B}."""
    ch.keywords.add("flying")
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=PutCounters("-1/-1", 2, "self"),
            text="enters with 2 -1/-1",
        ),
    )

    def effect(game: Game, ctx: Ctx) -> None:
        src = ctx.source
        if src is None or src.counters.get("-1/-1", 0) < 1:
            return
        game.remove_counters(src, "-1/-1", 1)
        for obj in list(game.battlefield_objects()):
            if obj is src:
                continue
            if "Creature" in obj.chars(game).types:
                game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{B}",
            effect=Custom(effect),
            text="{B}, remove -1/-1: -1/-1 on each other creature",
        ),
    )


def _soul_snuffers(ch: Characteristics, _ref: CardRef) -> None:
    """Soul Snuffers: wither; ETB -1/-1 on every creature."""
    ch.keywords.add("wither")

    def effect(game: Game, _ctx: Ctx) -> None:
        for obj in list(game.battlefield_objects()):
            if "Creature" in obj.chars(game).types:
                game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=Custom(effect),
            text="ETB: -1/-1 on each creature",
        ),
    )


def _contagion_engine(ch: Characteristics, _ref: CardRef) -> None:
    """Contagion Engine: ETB player wipe; proliferate twice."""

    def effect(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if isinstance(t, Player):
            for obj in list(t.battlefield):
                if "Creature" in obj.chars(game).types:
                    game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=Custom(effect),
            targets=[TargetSpec(what="player")],
            text="ETB: -1/-1 on each creature target player controls",
        ),
    )
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{4}",
            tap_cost=True,
            effect=Proliferate(2),
            text="{4},{T}: proliferate twice",
        ),
    )


def _contagion_clasp(ch: Characteristics, _ref: CardRef) -> None:
    """Contagion Clasp: ETB -1/-1; tap to proliferate."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=PutCounters("-1/-1", 1, "target"),
            targets=[TargetSpec(what="creature")],
            text="ETB: -1/-1 on target creature",
        ),
    )
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{4}",
            tap_cost=True,
            effect=Proliferate(1),
            text="{4},{T}: proliferate",
        ),
    )


def _skinrender(ch: Characteristics, _ref: CardRef) -> None:
    """Skinrender: ETB three -1/-1 counters."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=PutCounters("-1/-1", 3, "target"),
            targets=[TargetSpec(what="creature", other=True)],
            text="ETB: three -1/-1 on target creature",
        ),
    )


def _yawgmoth(ch: Characteristics, _ref: CardRef) -> None:
    """Yawgmoth: pay life + sac for counters and cards."""
    ch.keywords.add("hexproof")  # SIMPLIFIED: protection from Humans

    ch.abilities.append(
        ActivatedAbility(
            life_cost=1,
            sac_cost="another creature",
            effect=Sequence([PutCounters("-1/-1", 1, "target"), DrawCards(1)]),
            targets=[TargetSpec(what="creature", optional=True)],
            text="Pay 1 life, sacrifice another creature: -1/-1 + draw",
        ),
    )
    NOTES[ch.name] = (
        "protection from Humans simplified to hexproof; {B}{B} discard mode omitted"
    )


def _skullclamp(ch: Characteristics, _ref: CardRef) -> None:
    """Skullclamp: +1/-1 equipment that draws when the bearer dies."""

    def continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
        def applies(_g: Game, obj: GameObject, _c: Characteristics) -> bool:
            return obj is source.attached_to

        def clamp(_g: Game, _o: GameObject, c: Characteristics) -> None:
            c.power = (c.power or 0) + 1
            c.toughness = (c.toughness or 0) - 1

        return [
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=source,
                applies_to=applies,
                apply=clamp,
            ),
        ]

    ch.abilities.append(
        StaticAbility(continuous=continuous, text="equipped gets +1/-1"),
    )

    # note: attachment is cleared on zone change, so capture via the
    # object's override scratch state:
    def died_cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return obj is not None and obj.custom.get("clamped_by") is source

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=died_cond),
            effect=DrawCards(2),
            text="equipped dies: draw 2",
        ),
    )

    def equip(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if isinstance(t, GameObject) and t.zone == Zone.BATTLEFIELD:
            if ctx.source is not None:
                game.attach(ctx.source, t)
            t.custom["clamped_by"] = ctx.source

    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{1}",
            sorcery_only=True,
            effect=Custom(equip),
            targets=[TargetSpec(what="creature", controller="you")],
            text="Equip {1}",
        ),
    )


def _scorpion_god(ch: Characteristics, _ref: CardRef) -> None:
    """Implement The Scorpion God: countered deaths draw cards."""

    def cond(_game: Game, _source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            obj is not None
            and "Creature" in obj.base.types
            and obj.lki_counters.get("-1/-1", 0) > 0
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=cond),
            effect=DrawCards(1),
            text="creature with -1/-1 dies: draw",
        ),
    )
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{1}{B}{R}",
            effect=PutCounters("-1/-1", 1, "target"),
            targets=[TargetSpec(what="creature")],
            text="{1}{B}{R}: -1/-1 on target creature",
        ),
    )
    NOTES[ch.name] = "return-to-hand-from-graveyard upkeep trigger omitted"


def _dusk_urchins(ch: Characteristics, _ref: CardRef) -> None:
    """Dusk Urchins: shrink on attack, draw per counter on death."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ATTACKS),
            effect=PutCounters("-1/-1", 1, "self"),
            text="attacks: -1/-1 on itself",
        ),
    )

    def effect(game: Game, ctx: Ctx) -> None:
        if ctx.source is not None:
            game.draw(ctx.controller, ctx.source.lki_counters.get("-1/-1", 0))

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES),
            effect=Custom(effect),
            text="dies: draw per -1/-1 counter",
        ),
    )
    NOTES[ch.name] = "blocks trigger folded into attacks only"


def _grave_titan(ch: Characteristics, _ref: CardRef) -> None:
    """Grave Titan: deathtouch; Zombies on entry and attack."""
    ch.keywords.add("deathtouch")
    two_zombies = CreateTokens(2, ZOMBIE)
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=two_zombies,
            text="ETB: two Zombies",
        ),
    )
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ATTACKS),
            effect=two_zombies,
            text="attacks: two Zombies",
        ),
    )


def _puppeteer_clique(ch: Characteristics, _ref: CardRef) -> None:
    """Puppeteer Clique: ETB raid the best opposing graveyard creature."""
    ch.keywords |= {"flying", "persist"}

    def effect(game: Game, ctx: Ctx) -> None:
        best, bp = None, -1
        for opp in game.opponents(ctx.controller):
            for card in opp.graveyard:
                if "Creature" in card.base.types and (card.base.power or 0) > bp:
                    best, bp = card, card.base.power or 0
        if best is not None:
            best.controller = ctx.controller
            game.move_zone(best, Zone.BATTLEFIELD)
            ctx.controller.stat("grave_robs")

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=Custom(effect),
            text="ETB: raid an opposing graveyard",
        ),
    )
    NOTES[ch.name] = "stolen creature stays (haste/exile-at-end omitted)"


def _reassembling_skeleton(ch: Characteristics, _ref: CardRef) -> None:
    """Reassembling Skeleton: return itself from the graveyard."""

    def effect(game: Game, ctx: Ctx) -> None:
        src = ctx.source
        if src is not None and src.zone == Zone.GRAVEYARD:
            src.controller = ctx.controller
            game.move_zone(src, Zone.BATTLEFIELD, to_battlefield_tapped=True)

    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{1}{B}",
            effect=Custom(effect),
            from_graveyard=True,
            text="{1}{B}: return from graveyard tapped",
        ),
    )


def _quillspike(ch: Characteristics, _ref: CardRef) -> None:
    """Quillspike: eat a -1/-1 counter for +3/+3."""

    def effect(game: Game, ctx: Ctx) -> None:
        for obj in list(ctx.controller.battlefield):
            if obj.counters.get("-1/-1", 0):
                game.remove_counters(obj, "-1/-1", 1)
                break
        else:
            return
        PumpAll(0, 0)  # no-op placeholder
        src = ctx.source

        def pump(_g: Game, _o: GameObject, c: Characteristics) -> None:
            c.power = (c.power or 0) + 3
            c.toughness = (c.toughness or 0) + 3

        game.add_floating_effect(
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=src,
                applies_to=lambda _g, o, _c: o is src,
                apply=pump,
                duration="end_of_turn",
            ),
        )

    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{B}",
            effect=Custom(effect),
            text="{B/G}, remove a -1/-1 counter: +3/+3",
        ),
    )
    NOTES[ch.name] = "{B/G} simplified to {B}; counter removed from any own creature"


def _everlasting_torment(ch: Characteristics, _ref: CardRef) -> None:
    """Everlasting Torment: no life gain; everything has wither."""

    def replacement(_game: Game, source: GameObject) -> list[Replacement]:
        def matches(_g: Game, _event: Event) -> bool:
            return True

        def replace(_g: Game, _event: Event) -> Event | None:
            return None  # no life gain (615)

        return [
            Replacement(
                EventType.GAIN_LIFE,
                matches=matches,
                replace=replace,
                source=source,
            ),
        ]

    ch.abilities.append(
        StaticAbility(replacement=replacement, text="players can't gain life"),
    )
    ch.abilities.append(_grant_all_creatures("wither"))
    NOTES[ch.name] = "'damage can't be prevented' omitted"


def _kulrath_knight(ch: Characteristics, _ref: CardRef) -> None:
    """Kulrath Knight: countered enemy creatures can't attack/block."""
    ch.keywords |= {"flying", "wither"}

    def continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
        def applies(_g: Game, obj: GameObject, c: Characteristics) -> bool:
            return (
                obj.controller is not source.controller
                and "Creature" in c.types
                and bool(obj.counters)
            )

        return [
            ContinuousEffect(
                layer=6,
                source=source,
                applies_to=applies,
                apply=lambda _g, _o, c: c.keywords.add("shackled"),
            ),
        ]

    ch.abilities.append(
        StaticAbility(
            continuous=continuous,
            text="opposing creatures with counters can't attack or block",
        ),
    )


def _massacre_girl(ch: Characteristics, _ref: CardRef) -> None:
    """Massacre Girl: menace; your creatures have wither."""
    ch.keywords.add("menace")
    ch.abilities.append(_grant_all_creatures("wither", controller_only=True))
    NOTES[ch.name] = "card-draw clause omitted"


def _glissa(ch: Characteristics, _ref: CardRef) -> None:
    """Glissa Sunslayer: first strike + deathtouch; hits draw."""
    ch.keywords |= {"first strike", "deathtouch"}

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        return bool(
            event.data.get("resolved")
            and event.data.get("combat")
            and event.data.get("source") is source
            and isinstance(event.data.get("target"), Player),
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DAMAGE, condition=cond),
            effect=DrawCards(1),
            text="combat damage to player: draw",
        ),
    )
    NOTES[ch.name] = "modal (counters/stun) simplified to draw"


def _fire_covenant(ch: Characteristics, _ref: CardRef) -> None:
    """Fire Covenant: pay life, divide damage."""
    ch.abilities.append(
        SpellAbility(
            effect=Sequence([LoseLife(2, "you"), DealDamage(4, "divided")]),
            text="pay life: 4 damage divided",
        ),
    )
    NOTES[ch.name] = "X life / X damage fixed at 2 life for 4 damage"


def _black_suns_zenith(ch: Characteristics, _ref: CardRef) -> None:
    """Black Sun's Zenith: X -1/-1 counters on each creature."""
    ch.abilities.append(
        SpellAbility(
            effect=PutCounters("-1/-1", "x", "each_creature"),
            text="X -1/-1 counters on each creature",
        ),
    )
    NOTES[ch.name] = "shuffle-back clause omitted"


def _chaos_warp(ch: Characteristics, _ref: CardRef) -> None:
    """Chaos Warp: shuffle target permanent into its owner's library."""

    def effect(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if isinstance(t, GameObject) and t.zone == Zone.BATTLEFIELD:
            owner = t.owner
            game.move_zone(t, Zone.LIBRARY)
            game.shuffle(owner)

    ch.abilities.append(
        SpellAbility(
            effect=Custom(effect),
            targets=[TargetSpec(what="permanent")],
            text="shuffle target permanent into library",
        ),
    )
    NOTES[ch.name] = "reveal/may-cast rider omitted"


def _cultivate(ch: Characteristics, _ref: CardRef) -> None:
    """Cultivate: one basic tapped, one to hand."""
    ch.abilities.append(
        SpellAbility(
            effect=Sequence(
                [SearchLands(1, tapped=True), SearchLands(1, to_hand=True)],
            ),
            text="one basic tapped + one to hand",
        ),
    )


def _farewell(ch: Characteristics, _ref: CardRef) -> None:
    """Farewell: exile all creatures and artifacts."""

    def effect(game: Game, _ctx: Ctx) -> None:
        for obj in list(game.battlefield_objects()):
            c = obj.chars(game)
            if c.types & {"Creature", "Artifact"}:
                game.exile(obj)

    ch.abilities.append(
        SpellAbility(effect=Custom(effect), text="exile all creatures and artifacts"),
    )
    NOTES[ch.name] = "modes fixed: creatures + artifacts, exiled"


def _austere_command(ch: Characteristics, _ref: CardRef) -> None:
    """Austere Command: destroy big creatures + opposing artifacts."""

    def effect(game: Game, ctx: Ctx) -> None:
        for obj in list(game.battlefield_objects()):
            c = obj.chars(game)
            if ("Creature" in c.types and (c.power or 0) >= _AUSTERE_POWER_MIN) or (
                "Artifact" in c.types and obj.controller is not ctx.controller
            ):
                game.destroy(obj)

    ch.abilities.append(
        SpellAbility(
            effect=Custom(effect),
            text="destroy big creatures + opposing artifacts",
        ),
    )
    NOTES[ch.name] = "modes fixed"


def _akromas_will(ch: Characteristics, _ref: CardRef) -> None:
    """Akroma's Will: team protection plus a small pump."""
    ch.abilities.append(
        SpellAbility(
            effect=Sequence([ProtectAll(), PumpAll(1, 1)]),
            text="protection + small pump",
        ),
    )
    NOTES[ch.name] = "both modes approximated"


def _spell_swindle(ch: Characteristics, _ref: CardRef) -> None:
    """Spell Swindle: counter a spell, mint Treasures."""
    ch.abilities.append(
        SpellAbility(
            effect=Sequence([CounterSpell(), CreateTokens(3, TREASURE)]),
            targets=[TargetSpec(what="spell")],
            text="counter + treasures",
        ),
    )
    NOTES[ch.name] = "treasures fixed at 3 (mana value of countered spell)"


def _brasss_bounty(ch: Characteristics, _ref: CardRef) -> None:
    """Brass's Bounty: a Treasure per land."""

    def count(game: Game, ctx: Ctx) -> int:
        return sum(
            1 for o in ctx.controller.battlefield if "Land" in o.chars(game).types
        )

    ch.abilities.append(
        SpellAbility(effect=CreateTokens(count, TREASURE), text="a treasure per land"),
    )


def _curse_of_opulence(ch: Characteristics, _ref: CardRef) -> None:
    """Curse of Opulence: gold each upkeep (SIMPLIFIED)."""

    def effect(game: Game, ctx: Ctx) -> None:
        game.create_tokens(ctx.controller, TREASURE, 1)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.BEGIN_STEP,
                condition=lambda _g, s, e: (
                    e.data.get("step") == "upkeep"
                    and e.data.get("player") is s.controller
                ),
            ),
            effect=Custom(effect),
            text="upkeep: gold",
        ),
    )
    NOTES[ch.name] = (
        "attack-the-cursed-player trigger simplified to one Treasure per own upkeep"
    )


def _bootleggers_stash(ch: Characteristics, _ref: CardRef) -> None:
    """Bootleggers' Stash: tap for a Treasure."""
    ch.abilities.append(
        ActivatedAbility(
            tap_cost=True,
            effect=CreateTokens(1, TREASURE),
            text="{T}: Treasure",
        ),
    )
    NOTES[ch.name] = "grants-lands-the-ability simplified to itself"


def _treasure_vault(ch: Characteristics, _ref: CardRef) -> None:
    """Treasure Vault: colorless mana; burst into Treasures."""
    ch.abilities.append(
        ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(types=("C",)),
            text="{T}: Add {C}",
        ),
    )
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{4}",
            tap_cost=True,
            sac_cost="self",
            effect=CreateTokens(4, TREASURE),
            text="{X}{X},{T}, sac: X Treasures (X=4)",
        ),
    )
    NOTES[ch.name] = "X fixed at 4"


def _retrofitter_foundry(ch: Characteristics, _ref: CardRef) -> None:
    """Retrofitter Foundry: pay and tap for a Thopter."""
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{2}",
            tap_cost=True,
            effect=CreateTokens(1, THOPTER),
            text="{2},{T}: Thopter",
        ),
    )
    NOTES[ch.name] = "untap/upgrade chain simplified"


def _academy_manufactor(ch: Characteristics, _ref: CardRef) -> None:
    """Academy Manufactor: clue/food/treasure -> all three."""

    def replacement(_game: Game, source: GameObject) -> list[Replacement]:
        def matches(_g: Game, event: Event) -> bool:
            if event.data.get("controller") is not source.controller:
                return False
            return event.data["spec"].predefined in ("treasure", "clue", "food")

        def replace(_g: Game, event: Event) -> Event:
            kinds = {"treasure": TREASURE, "clue": CLUE, "food": FOOD}
            have = event.data["spec"].predefined
            event.data["extra_specs"] = [v for k, v in kinds.items() if k != have]
            return event

        return [
            Replacement(
                EventType.CREATE_TOKEN,
                matches=matches,
                replace=replace,
                source=source,
            ),
        ]

    ch.abilities.append(
        StaticAbility(replacement=replacement, text="clue/food/treasure -> all three"),
    )


def _mechanized_production(ch: Characteristics, _ref: CardRef) -> None:
    """Mechanized Production: copy the artifact; win at eight."""

    def effect(game: Game, ctx: Ctx) -> None:
        src = ctx.source
        target = src.attached_to if src is not None else None
        if target is None or target.zone != Zone.BATTLEFIELD:
            return
        tch = target.chars(game)
        spec = TokenSpec(
            name=tch.name,
            power=target.base.power,
            toughness=target.base.toughness,
            colors=frozenset(tch.colors),
            types=frozenset(tch.types),
            subtypes=frozenset(tch.subtypes),
            predefined="treasure" if "Treasure" in tch.subtypes else "",
        )
        game.create_tokens(ctx.controller, spec, 1)
        names: dict[str, int] = {}
        for o in ctx.controller.battlefield:
            c = o.chars(game)
            if "Artifact" in c.types and c.name:
                names[c.name] = names.get(c.name, 0) + 1
        if names and max(names.values()) >= _MECHANIZED_WIN_COPIES:
            game.winner = ctx.controller
            game.game_over = True  # alternate win condition
            ctx.controller.stat("mechanized_wins")

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        return (
            event.data.get("step") == "upkeep"
            and event.data.get("player") is source.controller
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
            effect=Custom(effect),
            text="upkeep: copy enchanted artifact; win at 8 same-name artifacts",
        ),
    )
    ch.abilities.append(
        SpellAbility(
            effect=Noop("enchant artifact"),
            targets=[TargetSpec(what="artifact", controller="you")],
            text="enchant artifact you control",
        ),
    )


def _igs_count_multicolored(game: Game, ctx: Ctx) -> int:
    """Count the controller's multicolored permanents."""
    n = 0
    for o in ctx.controller.battlefield:
        if len(o.chars(game).colors) >= _MULTICOLORED_MIN:
            n += 1
    return n


def _igs_station(game: Game, ctx: Ctx) -> None:
    """Station (rule 702.184): tap the best creature to charge up."""
    src = ctx.source
    if src is None:
        return
    best = None
    for o in ctx.controller.battlefield:
        c = o.chars(game)
        if (
            "Creature" in c.types
            and not o.tapped
            and o is not src
            and not (o.entered_this_turn and "haste" not in c.keywords)
            and (best is None or (c.power or 0) > (best.chars(game).power or 0))
        ):
            best = o
    if best is not None:
        game.tap(best)
        game.put_counters(src, "charge", max(0, best.chars(game).power or 0))


def _igs_continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
    """At twelve charge counters: an artifact creature with flying."""

    def applies(_g: Game, obj: GameObject, _c: Characteristics) -> bool:
        return obj is source and obj.counters.get("charge", 0) >= (
            _STATION_FLIGHT_CHARGES
        )

    def add_type(_g: Game, _o: GameObject, c: Characteristics) -> None:
        c.types.add("Creature")

    def add_kw(_g: Game, _o: GameObject, c: Characteristics) -> None:
        c.keywords.add("flying")

    return [
        ContinuousEffect(
            layer=4,
            source=source,
            applies_to=applies,
            apply=add_type,
        ),
        ContinuousEffect(layer=6, source=source, applies_to=applies, apply=add_kw),
    ]


def _infinite_guideline_station(ch: Characteristics, _ref: CardRef) -> None:
    """Infinite Guideline Station: Robots, draws, and station charges."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=CreateTokens(_igs_count_multicolored, ROBOT),
            text="ETB: a Robot per multicolored permanent",
        ),
    )
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ATTACKS),
            effect=DrawCards(_igs_count_multicolored),
            text="attacks: draw per multicolored permanent",
        ),
    )
    ch.abilities.append(
        ActivatedAbility(
            sorcery_only=True,
            effect=Custom(_igs_station),
            text="Station (rule 702.184)",
        ),
    )
    ch.abilities.append(
        StaticAbility(
            continuous=_igs_continuous,
            text="12+ charge: artifact creature with flying",
        ),
    )


def _anim_pakal(ch: Characteristics, _ref: CardRef) -> None:
    """Anim Pakal: grow on attack, then Gnomes per counter."""

    def effect(game: Game, ctx: Ctx) -> None:
        src = ctx.source
        if src is None:
            return
        game.put_counters(src, "+1/+1", 1)
        n = src.counters.get("+1/+1", 0)
        game.create_tokens(ctx.controller, GNOME, n)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ATTACKS),
            effect=Custom(effect),
            text="attacks: +1/+1 counter, then Gnomes per counter",
        ),
    )
    NOTES[ch.name] = "Gnomes enter untapped, not attacking"


def _esix(ch: Characteristics, _ref: CardRef) -> None:
    """Esix, Fractal Bloom: first tokens each turn may copy a creature."""
    ch.keywords.add("ward:2")

    def replacement(_game: Game, source: GameObject) -> list[Replacement]:
        def matches(g: Game, event: Event) -> bool:
            if event.data.get("controller") is not source.controller:
                return False
            if source.custom.get("esix_turn", -1) == g.turn:
                return False
            return "Creature" in event.data["spec"].types

        def replace(g: Game, event: Event) -> Event:
            source.custom["esix_turn"] = g.turn
            me = source.controller
            best, bv = None, 0
            for o in me.battlefield:
                c = o.chars(g)
                if "Creature" in c.types:
                    v = (c.power or 0) + (c.toughness or 0)
                    if v > bv:
                        best, bv = o, v
            if best is not None and bv > _ESIX_UPGRADE_FACTOR * (
                (event.data["spec"].power or 1) + (event.data["spec"].toughness or 1)
            ):
                c = best.chars(g)
                event.data["spec"] = TokenSpec(
                    name=c.name,
                    power=best.base.power,
                    toughness=best.base.toughness,
                    colors=frozenset(c.colors),
                    types=frozenset(c.types),
                    subtypes=frozenset(c.subtypes),
                    keywords=frozenset(best.base.keywords),
                )
            return event

        return [
            Replacement(
                EventType.CREATE_TOKEN,
                matches=matches,
                replace=replace,
                source=source,
            ),
        ]

    ch.abilities.append(
        StaticAbility(
            replacement=replacement,
            text="first tokens each turn may copy a creature",
        ),
    )


def _adrix_and_nev(ch: Characteristics, _ref: CardRef) -> None:
    """Adrix and Nev: ward and a token doubler."""
    ch.keywords.add("ward:2")
    ch.abilities.append(_token_doubler())


def _doubling_season(ch: Characteristics, _ref: CardRef) -> None:
    """Doubling Season: token and counter doubling."""
    ch.abilities.append(_token_doubler())
    ch.abilities.append(_counter_doubler())


def _ojer_taq(ch: Characteristics, _ref: CardRef) -> None:
    """Ojer Taq: triple creature tokens."""
    ch.abilities.append(_token_doubler(creature_only=True, factor=3))
    NOTES[ch.name] = "back face (Temple of Civilization) not modeled"


def _kaya_geist_hunter(ch: Characteristics, _ref: CardRef) -> None:
    """Kaya, Geist Hunter: team deathtouch; token doubling burst."""

    def plus1(game: Game, ctx: Ctx) -> None:
        me = ctx.controller
        game.add_floating_effect(
            ContinuousEffect(
                layer=6,
                source=ctx.source,
                applies_to=lambda _g, o, c: (
                    o.controller is me and "Creature" in c.types
                ),
                apply=lambda _g, _o, c: c.keywords.add("deathtouch"),
                duration="end_of_turn",
            ),
        )

    ch.abilities.append(
        ActivatedAbility(
            loyalty_cost=1,
            effect=Custom(plus1),
            text="+1: your creatures gain deathtouch",
        ),
    )

    def minus2(game: Game, ctx: Ctx) -> None:
        src = ctx.source
        me = ctx.controller
        game.add_floating_effect(
            ContinuousEffect(
                layer=6,
                source=src,
                applies_to=lambda _g, _o, _c: False,
                apply=lambda _g, _o, _c: None,
                duration="end_of_turn",
            ),
        )
        rep = Replacement(
            EventType.CREATE_TOKEN,
            matches=lambda _g, e: e.data.get("controller") is me,
            replace=_double_count,
            source=src,
            duration="floating",
        )
        game.replacements.floating.append(rep)
        game.custom.setdefault("kaya_reps", []).append(rep)

    ch.abilities.append(
        ActivatedAbility(
            loyalty_cost=-2,
            effect=Custom(minus2),
            text="-2: token doubling until end of turn",
        ),
    )
    NOTES[ch.name] = (
        "-2 doubling persists to end of game (cleanup "
        "of floating replacement simplified)"
    )


def _double_count(_game: Game, event: Event) -> Event:
    """Double the created-token count (Kaya's -2)."""
    event.data["count"] *= 2
    return event


def _saheeli_rai(ch: Characteristics, _ref: CardRef) -> None:
    """Saheeli Rai: +1 scry and ping each opponent."""

    def ping(game: Game, ctx: Ctx) -> None:
        for p in game.opponents(ctx.controller):
            game.deal_damage(ctx.source, p, 1)

    ch.abilities.append(
        ActivatedAbility(
            loyalty_cost=1,
            effect=Sequence([Scry(1), Custom(ping)]),
            text="+1: scry 1, 1 damage to each opponent",
        ),
    )
    NOTES[ch.name] = "-2 copy-artifact and -7 omitted"


def _saheeli_sublime(ch: Characteristics, _ref: CardRef) -> None:
    """Saheeli, Sublime Artificer: noncreature casts make Servos."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            event.data.get("player") is source.controller
            and obj is not None
            and "Creature" not in obj.base.types
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.CAST, condition=cond),
            effect=CreateTokens(1, SERVO),
            text="you cast a noncreature spell: Servo",
        ),
    )


def _sai(ch: Characteristics, _ref: CardRef) -> None:
    """Sai, Master Thopterist: artifact casts make Thopters."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            event.data.get("player") is source.controller
            and obj is not None
            and "Artifact" in obj.base.types
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.CAST, condition=cond),
            effect=CreateTokens(1, THOPTER),
            text="you cast an artifact spell: Thopter",
        ),
    )
    NOTES[ch.name] = "sacrifice-to-draw ability omitted"


def _thopter_spy_network(ch: Characteristics, _ref: CardRef) -> None:
    """Thopter Spy Network: upkeep Thopters; artifact hits draw."""

    def cond(game: Game, source: GameObject, event: Event) -> bool:
        if (
            event.data.get("step") != "upkeep"
            or event.data.get("player") is not source.controller
        ):
            return False
        return any(
            "Artifact" in o.chars(game).types for o in source.controller.battlefield
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
            effect=CreateTokens(1, THOPTER),
            text="upkeep (if you control an artifact): Thopter",
        ),
    )

    def dmg_cond(game: Game, source: GameObject, event: Event) -> bool:
        src = event.data.get("source")
        return bool(
            event.data.get("resolved")
            and event.data.get("combat")
            and isinstance(event.data.get("target"), Player)
            and isinstance(src, GameObject)
            and src.controller is source.controller
            and "Artifact" in src.chars(game).types,
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DAMAGE, condition=dmg_cond),
            effect=DrawCards(1),
            text="artifact combat damage to a player: draw",
        ),
    )


def _bident(ch: Characteristics, _ref: CardRef) -> None:
    """Bident of Thassa: creature hits draw cards."""

    def dmg_cond(game: Game, source: GameObject, event: Event) -> bool:
        src = event.data.get("source")
        return bool(
            event.data.get("resolved")
            and event.data.get("combat")
            and isinstance(event.data.get("target"), Player)
            and isinstance(src, GameObject)
            and src.controller is source.controller
            and "Creature" in src.chars(game).types,
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DAMAGE, condition=dmg_cond),
            effect=DrawCards(1),
            text="creature combat damage to a player: draw",
        ),
    )
    NOTES[ch.name] = "activated force-attack mode omitted"


def _mentor_of_the_meek(ch: Characteristics, _ref: CardRef) -> None:
    """Mentor of the Meek: small creatures draw cards."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            obj is not None
            and obj is not source
            and obj.controller is source.controller
            and "Creature" in obj.base.types
            and (obj.base.power or 0) <= _MEEK_MAX_POWER
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD, condition=cond),
            effect=DrawCards(1),
            text="small creature enters: draw",
        ),
    )
    NOTES[ch.name] = "the {1} payment is waived (SIMPLIFIED)"


def _blood_artist(ch: Characteristics, _ref: CardRef) -> None:
    """Blood Artist: any creature death drains 1."""

    def cond(_game: Game, _source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return obj is not None and "Creature" in obj.base.types

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=cond),
            effect=Drain(1),
            text="any creature dies: drain 1",
        ),
    )
    NOTES[ch.name] = "drains each opponent instead of target player"


def _zulaport(ch: Characteristics, _ref: CardRef) -> None:
    """Zulaport Cutthroat: own creature death drains 1."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            obj is not None
            and "Creature" in obj.base.types
            and obj.controller is source.controller
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=cond),
            effect=Drain(1),
            text="own creature dies: drain 1",
        ),
    )


def _marionette_apprentice(ch: Characteristics, _ref: CardRef) -> None:
    """Marionette Apprentice: own artifact/token death drains 1."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        obj = event.data.get("obj")
        return (
            obj is not None
            and obj.controller is source.controller
            and ("Artifact" in obj.base.types or obj.is_token)
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=cond),
            effect=Drain(1),
            text="own artifact/token dies: drain 1",
        ),
    )


def _shalai(ch: Characteristics, _ref: CardRef) -> None:
    """Shalai: your other permanents have hexproof."""
    ch.keywords.add("flying")

    def continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
        def applies(_g: Game, obj: GameObject, _c: Characteristics) -> bool:
            return obj.controller is source.controller and obj is not source

        return [
            ContinuousEffect(
                layer=6,
                source=source,
                applies_to=applies,
                apply=lambda _g, _o, c: c.keywords.add("hexproof"),
            ),
        ]

    ch.abilities.append(
        StaticAbility(continuous=continuous, text="your other stuff has hexproof"),
    )
    NOTES[ch.name] = "player hexproof + {4}{G}{G} pump omitted"


def _skyclave(ch: Characteristics, _ref: CardRef) -> None:
    """Skyclave Apparition: ETB exile a small nonland permanent."""

    def effect(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if isinstance(t, GameObject) and t.zone == Zone.BATTLEFIELD:
            game.exile(t)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=Custom(effect),
            targets=[TargetSpec(what="nonland", controller="opponent")],
            text="ETB: exile target small nonland permanent",
        ),
    )
    NOTES[ch.name] = "mv<=4 restriction and Illusion give-back omitted"


def _restoration_angel(ch: Characteristics, _ref: CardRef) -> None:
    """Restoration Angel: flash flyer; ETB blink."""
    ch.keywords |= {"flash", "flying"}
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=Blink(),
            optional=True,
            targets=[
                TargetSpec(
                    what="creature",
                    controller="you",
                    other=True,
                    optional=True,
                ),
            ],
            text="ETB: you may blink another creature you control",
        ),
    )


def _conjurers_closet(ch: Characteristics, _ref: CardRef) -> None:
    """Conjurer's Closet: end-step blink."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        return (
            event.data.get("step") == "end"
            and event.data.get("player") is source.controller
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
            effect=Blink(),
            optional=True,
            targets=[TargetSpec(what="creature", controller="you", optional=True)],
            text="end step: blink a creature you control",
        ),
    )


def _reef_worm(ch: Characteristics, _ref: CardRef) -> None:
    """Reef Worm: dies into Fish -> Whale -> Kraken."""
    fish = _reef_spec(
        "Fish",
        3,
        3,
        _reef_chain("Fish", 3, 3, ("Whale", 6, 6, ("Kraken", 9, 9, None))),
    )
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES),
            effect=CreateTokens(1, fish),
            text="dies: Fish -> Whale -> Kraken",
        ),
    )


def _hangarback(ch: Characteristics, _ref: CardRef) -> None:
    """Hangarback Walker: X counters in; Thopters out."""
    ch.etb_x_counters = "+1/+1"

    def effect(game: Game, ctx: Ctx) -> None:
        if ctx.source is None:
            return
        n = ctx.source.lki_counters.get("+1/+1", 0)
        game.create_tokens(ctx.controller, THOPTER, n)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES),
            effect=Custom(effect),
            text="dies: a Thopter per +1/+1 counter",
        ),
    )


def _triplicate_titan(ch: Characteristics, _ref: CardRef) -> None:
    """Triplicate Titan: dies into three Golems."""
    ch.keywords |= {"flying", "vigilance", "trample"}
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES),
            effect=CreateTokens(3, GOLEM),
            text="dies: three 4/4 Golems",
        ),
    )
    NOTES[ch.name] = "Golem keyword split simplified to flying on all"


def _myr_battlesphere(ch: Characteristics, _ref: CardRef) -> None:
    """Myr Battlesphere: ETB four Myr."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=CreateTokens(4, MYR),
            text="ETB: four Myr",
        ),
    )
    NOTES[ch.name] = "attack pump/damage trigger omitted"


def _determined_iteration(ch: Characteristics, _ref: CardRef) -> None:
    """Implement Determined Iteration: populate at combat start."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        return (
            event.data.get("step") == "combat_begin"
            and event.data.get("player") is source.controller
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
            effect=Populate(),
            text="begin combat: populate",
        ),
    )
    NOTES[ch.name] = "haste grant omitted"


def _growing_ranks(ch: Characteristics, _ref: CardRef) -> None:
    """Growing Ranks: populate each upkeep."""

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        return (
            event.data.get("step") == "upkeep"
            and event.data.get("player") is source.controller
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
            effect=Populate(),
            text="upkeep: populate",
        ),
    )


def _extravagant_replication(ch: Characteristics, _ref: CardRef) -> None:
    """Extravagant Replication: upkeep copy of your best creature."""

    def effect(game: Game, ctx: Ctx) -> None:
        me = ctx.controller
        best, bv = None, 0
        for o in me.battlefield:
            c = o.chars(game)
            if "Creature" in c.types and o is not ctx.source:
                v = (c.power or 0) + (c.toughness or 0)
                if v > bv:
                    best, bv = o, v
        if best is not None:
            c = best.chars(game)
            spec = TokenSpec(
                name=c.name,
                power=best.base.power,
                toughness=best.base.toughness,
                colors=frozenset(c.colors),
                types=frozenset(c.types),
                subtypes=frozenset(c.subtypes),
                keywords=frozenset(best.base.keywords),
            )
            game.create_tokens(me, spec, 1)

    def cond(_game: Game, source: GameObject, event: Event) -> bool:
        return (
            event.data.get("step") == "upkeep"
            and event.data.get("player") is source.controller
        )

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
            effect=Custom(effect),
            text="upkeep: token copy of your best creature",
        ),
    )


def _imprisoned_in_the_moon(ch: Characteristics, _ref: CardRef) -> None:
    """Imprisoned in the Moon: enchanted permanent is a colorless land."""

    def continuous(_game: Game, source: GameObject) -> list[ContinuousEffect]:
        def applies(_g: Game, obj: GameObject, _c: Characteristics) -> bool:
            return obj is source.attached_to

        def to_land(_g: Game, _o: GameObject, c: Characteristics) -> None:
            c.types = {"Land"}
            c.subtypes = set()
            c.supertypes -= {"Legendary"}

        def strip_abilities(_g: Game, _o: GameObject, c: Characteristics) -> None:
            c.abilities = [
                ActivatedAbility(
                    tap_cost=True,
                    is_mana_ability=True,
                    effect=AddMana(types=("C",)),
                    text="{T}: Add {C}",
                ),
            ]
            c.keywords = set()

        def zero_pt(_g: Game, _o: GameObject, c: Characteristics) -> None:
            c.power = c.toughness = None

        return [
            ContinuousEffect(layer=4, source=source, applies_to=applies, apply=to_land),
            ContinuousEffect(
                layer=6,
                source=source,
                applies_to=applies,
                apply=strip_abilities,
            ),
            ContinuousEffect(
                layer=7,
                sublayer="b",
                source=source,
                applies_to=applies,
                apply=zero_pt,
            ),
        ]

    ch.abilities.append(
        StaticAbility(
            continuous=continuous,
            text="enchanted permanent is a colorless land",
        ),
    )
    ch.abilities.append(
        SpellAbility(
            effect=Noop("enchant"),
            targets=[TargetSpec(what="creature_or_planeswalker")],
            text="enchant creature or planeswalker",
        ),
    )


def _banishing_light(ch: Characteristics, _ref: CardRef) -> None:
    """Banishing Light: exile until it leaves the battlefield."""

    def effect(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if not isinstance(t, GameObject) or t.zone != Zone.BATTLEFIELD:
            return
        game.exile(t)
        if t.zone == Zone.EXILE and ctx.source is not None:
            ctx.source.custom["imprisoned"] = t

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=Custom(effect),
            targets=[TargetSpec(what="nonland", controller="opponent")],
            text="ETB: exile target nonland permanent",
        ),
    )

    def release_cond(_game: Game, source: GameObject, event: Event) -> bool:
        return event.data.get("obj") is source

    def release(game: Game, ctx: Ctx) -> None:
        held = ctx.source.custom.get("imprisoned") if ctx.source is not None else None
        if isinstance(held, GameObject) and held.zone == Zone.EXILE:
            held.controller = held.owner
            game.move_zone(held, Zone.BATTLEFIELD)

    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.DIES, condition=release_cond),
            effect=Custom(release),
            text="leaves: return the exiled card",
        ),
    )


def _chromatic_lantern(ch: Characteristics, _ref: CardRef) -> None:
    """Chromatic Lantern: tap for any color."""
    ch.abilities.append(
        ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(any_color=True),
            text="{T}: Add one mana of any color",
        ),
    )
    NOTES[ch.name] = "lands-have-any-color static omitted"


def _coalition_relic(ch: Characteristics, _ref: CardRef) -> None:
    """Coalition Relic: tap for any color."""
    ch.abilities.append(
        ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(any_color=True),
            text="{T}: Add one mana of any color",
        ),
    )
    NOTES[ch.name] = "charge counter mode omitted"


def _channeler_initiate(ch: Characteristics, _ref: CardRef) -> None:
    """Channeler Initiate: counters in, any color out."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
            effect=PutCounters("-1/-1", 3, "self"),
            text="enters with three -1/-1 counters",
        ),
    )
    ch.abilities.append(
        ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(any_color=True),
            text="{T}, remove a -1/-1 counter: any color (SIMPLIFIED: "
            "counter not removed)",
        ),
    )
    NOTES[ch.name] = "mana ability does not remove the -1/-1 counter"


def _devoted_druid(ch: Characteristics, _ref: CardRef) -> None:
    """Devoted Druid: tap for green."""
    ch.abilities.append(
        ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(types=("G",)),
            text="{T}: Add {G}",
        ),
    )
    NOTES[ch.name] = "untap-with--1/-1 mode omitted"


def _evolution_sage(ch: Characteristics, _ref: CardRef) -> None:
    """Evolution Sage: landfall proliferate."""
    ch.abilities.append(
        TriggeredAbility(
            trigger=TriggerSpec(
                EventType.LAND_PLAYED,
                condition=lambda _g, s, e: e.data.get("player") is s.controller,
            ),
            effect=Proliferate(),
            text="landfall: proliferate",
        ),
    )


def _heroic_reinforcements(ch: Characteristics, _ref: CardRef) -> None:
    """Heroic Reinforcements: two hasty Soldiers."""
    ch.abilities.append(
        SpellAbility(
            effect=CreateTokens(
                2,
                _token("Soldier", 1, 1, "RW", subs=("Soldier",), kws=("haste",)),
            ),
            text="two hasty Soldiers",
        ),
    )
    NOTES[ch.name] = "+1/+1 until end of turn pump omitted"


def _swan_song(ch: Characteristics, _ref: CardRef) -> None:
    """Swan Song: counter; its controller gets a Bird."""

    def effect(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if t is not None and not isinstance(t, GameObject | Player):
            game.counter_spell(t)
            if t.controller is not ctx.controller:
                game.create_tokens(
                    t.controller,
                    _token("Bird", 2, 2, "U", subs=("Bird",), kws=("flying",)),
                    1,
                )

    ch.abilities.append(
        SpellAbility(
            effect=Custom(effect),
            targets=[TargetSpec(what="spell")],
            text="counter; its controller gets a Bird",
        ),
    )


def _arcane_denial(ch: Characteristics, _ref: CardRef) -> None:
    """Arcane Denial: counter; they draw 2, you draw 1."""

    def effect(game: Game, ctx: Ctx) -> None:
        t = ctx.target()
        if t is not None and not isinstance(t, GameObject | Player):
            other = t.controller
            game.counter_spell(t)
            game.draw(other, 2)
        game.draw(ctx.controller, 1)

    ch.abilities.append(
        SpellAbility(
            effect=Custom(effect),
            targets=[TargetSpec(what="spell")],
            text="counter; they draw 2, you draw 1 (draw timing simplified)",
        ),
    )


def _wayfarers_bauble(ch: Characteristics, _ref: CardRef) -> None:
    """Wayfarer's Bauble: sac to fetch a basic tapped."""
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{2}",
            tap_cost=True,
            sac_cost="self",
            effect=SearchLands(1, tapped=True),
            text="{2},{T}, sac: basic land tapped",
        ),
    )


def _myriad_landscape(ch: Characteristics, _ref: CardRef) -> None:
    """Myriad Landscape: sac to fetch two basics tapped."""
    ch.abilities.append(
        ActivatedAbility(
            mana_cost="{2}",
            tap_cost=True,
            sac_cost="self",
            effect=SearchLands(2, tapped=True),
            text="{2},{T}, sac: two basic lands tapped",
        ),
    )
    NOTES[ch.name] = "'share a land type' restriction omitted"


def _persist_sorcery(ch: Characteristics, _ref: CardRef) -> None:
    """Persist (the sorcery): return your best dead creature."""

    def effect(game: Game, ctx: Ctx) -> None:
        me = ctx.controller
        best, bv = None, 0
        for card in me.graveyard:
            if "Creature" in card.base.types:
                v = (card.base.power or 0) + (card.base.toughness or 0)
                if v > bv:
                    best, bv = card, v
        if best is not None:
            best.controller = me
            game.move_zone(best, Zone.BATTLEFIELD)

    ch.abilities.append(
        SpellAbility(
            effect=Custom(effect),
            text="return a creature from your graveyard (loses legendary "
            "rider omitted)",
        ),
    )
    NOTES[ch.name] = "returns your best creature; legendary clause omitted"


def _aberrant_return(ch: Characteristics, ref: CardRef) -> None:
    """Aberrant Return: as Persist."""
    _persist_sorcery(ch, ref)


def _grave_venerations(ch: Characteristics, ref: CardRef) -> None:
    """Grave Venerations: approximated as graveyard recursion."""
    _persist_sorcery(ch, ref)
    NOTES[ch.name] = "approximated as graveyard recursion"


OVERRIDES: dict[str, OverrideFn] = {
    "Auntie Ool, Cursewretch": _auntie_ool,
    "Blowfly Infestation": _blowfly,
    "Necroskitter": _necroskitter,
    "Hapatra, Vizier of Poisons": _hapatra,
    "Nest of Scarabs": _nest_of_scarabs,
    "Flourishing Defenses": _flourishing_defenses,
    "Obelisk Spider": _obelisk_spider,
    "Midnight Banshee": _midnight_banshee,
    "Carnifex Demon": _carnifex_demon,
    "Soul Snuffers": _soul_snuffers,
    "Contagion Engine": _contagion_engine,
    "Contagion Clasp": _contagion_clasp,
    "Skinrender": _skinrender,
    "Yawgmoth, Thran Physician": _yawgmoth,
    "Skullclamp": _skullclamp,
    "The Scorpion God": _scorpion_god,
    "Dusk Urchins": _dusk_urchins,
    "Grave Titan": _grave_titan,
    "Puppeteer Clique": _puppeteer_clique,
    "Reassembling Skeleton": _reassembling_skeleton,
    "Quillspike": _quillspike,
    "Everlasting Torment": _everlasting_torment,
    "Kulrath Knight": _kulrath_knight,
    "Massacre Girl, Known Killer": _massacre_girl,
    "Glissa Sunslayer": _glissa,
    "Fire Covenant": _fire_covenant,
    "Black Sun's Zenith": _black_suns_zenith,
    "Chaos Warp": _chaos_warp,
    "Cultivate": _cultivate,
    "Farewell": _farewell,
    "Austere Command": _austere_command,
    "Akroma's Will": _akromas_will,
    "Spell Swindle": _spell_swindle,
    "Brass's Bounty": _brasss_bounty,
    "Curse of Opulence": _curse_of_opulence,
    "Bootleggers' Stash": _bootleggers_stash,
    "Treasure Vault": _treasure_vault,
    "Retrofitter Foundry": _retrofitter_foundry,
    "Academy Manufactor": _academy_manufactor,
    "Mechanized Production": _mechanized_production,
    "Infinite Guideline Station": _infinite_guideline_station,
    "Anim Pakal, Thousandth Moon": _anim_pakal,
    "Esix, Fractal Bloom": _esix,
    "Adrix and Nev, Twincasters": _adrix_and_nev,
    "Doubling Season": _doubling_season,
    "Ojer Taq, Deepest Foundation // Temple of Civilization": _ojer_taq,
    "Kaya, Geist Hunter": _kaya_geist_hunter,
    "Saheeli Rai": _saheeli_rai,
    "Saheeli, Sublime Artificer": _saheeli_sublime,
    "Sai, Master Thopterist": _sai,
    "Thopter Spy Network": _thopter_spy_network,
    "Bident of Thassa": _bident,
    "Mentor of the Meek": _mentor_of_the_meek,
    "Blood Artist": _blood_artist,
    "Zulaport Cutthroat": _zulaport,
    "Marionette Apprentice": _marionette_apprentice,
    "Shalai, Voice of Plenty": _shalai,
    "Skyclave Apparition": _skyclave,
    "Restoration Angel": _restoration_angel,
    "Conjurer's Closet": _conjurers_closet,
    "Reef Worm": _reef_worm,
    "Hangarback Walker": _hangarback,
    "Triplicate Titan": _triplicate_titan,
    "Myr Battlesphere": _myr_battlesphere,
    "Determined Iteration": _determined_iteration,
    "Growing Ranks": _growing_ranks,
    "Extravagant Replication": _extravagant_replication,
    "Imprisoned in the Moon": _imprisoned_in_the_moon,
    "Banishing Light": _banishing_light,
    "Chromatic Lantern": _chromatic_lantern,
    "Coalition Relic": _coalition_relic,
    "Channeler Initiate": _channeler_initiate,
    "Devoted Druid": _devoted_druid,
    "Evolution Sage": _evolution_sage,
    "Heroic Reinforcements": _heroic_reinforcements,
    "Swan Song": _swan_song,
    "Arcane Denial": _arcane_denial,
    "Wayfarer's Bauble": _wayfarers_bauble,
    "Myriad Landscape": _myriad_landscape,
    "Persist": _persist_sorcery,
    "Aberrant Return": _aberrant_return,
    "Grave Venerations": _grave_venerations,
}


def apply_override(ch: Characteristics, ref: CardRef) -> bool:
    """Apply the hand-written implementation for *ref*, if one exists."""
    fn = OVERRIDES.get(ref.name)
    if fn is None and " // " in ref.name:
        fn = OVERRIDES.get(ref.name.split(" // ")[0])
    if fn is None:
        return False
    fn(ch, ref)
    # land mana facts still apply (e.g. Treasure Vault, Myriad Landscape)
    if (
        "Land" in ch.types
        and ref.behavior.get("land_colors")
        and not any(
            isinstance(a, ActivatedAbility) and a.is_mana_ability for a in ch.abilities
        )
    ):
        colors = set(ref.behavior["land_colors"])
        any_c = colors >= set("WUBRG")

        ch.abilities.append(
            ActivatedAbility(
                tap_cost=True,
                is_mana_ability=True,
                effect=AddMana(
                    types=tuple(c for c in colors if c in "WUBRGC")
                    if not any_c
                    else (),
                    any_color=any_c,
                ),
                text="{T}: Add mana.",
            ),
        )
    return True
