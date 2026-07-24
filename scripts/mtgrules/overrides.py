"""Hand-written card implementations for cards whose oracle text exceeds
the compiler grammar (the rules-engine equivalent of per-card scripts in
mature engines). Each entry builds real abilities from the card's actual
text; simplifications are marked SIMPLIFIED and reported via NOTES.
"""

from __future__ import annotations

from dataclasses import dataclass

from .abilities import (ActivatedAbility, SpellAbility, StaticAbility,
                        TargetSpec, TokenSpec, TriggeredAbility, TriggerSpec,
                        TREASURE, CLUE, FOOD)
from .effects import (AddMana, CounterSpell, CreateTokens, DealDamage,
                      Destroy, Drain, DrawCards, Effect, ExileObj, GainLife,
                      LoseLife, Blink, Noop, Populate, Proliferate,
                      ProtectAll, PumpAll, PutCounters, Scry, SearchLands,
                      Sequence)
from .events import Event, EventType
from .layers import ContinuousEffect
from .objects import GameObject, Player, Zone
from .replacements import Replacement

#: card name -> note about any simplification made
NOTES: dict[str, str] = {}


# ---------------------------------------------------------------- helpers

def _dies_cond(pred):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return obj is not None and pred(game, source, obj)
    return cond


def _counters_put_cond(kind, own_only=None):
    """Trigger on resolved PUT_COUNTERS events of a counter kind."""
    def cond(game, source, event):
        if not event.data.get("resolved"):
            return False
        if event.data.get("kind") != kind:
            return False
        obj = event.data.get("obj")
        if obj is None or "Creature" not in obj.base.types:
            return False
        if own_only is True and obj.controller is not source.controller:
            return False
        return True
    return cond


@dataclass
class Custom(Effect):
    fn: object
    note: str = ""

    def resolve(self, game, ctx):
        self.fn(game, ctx)


def _token(name, p, t, colors="", types=("Creature",), subs=(), kws=(),
           tapped=False, abilities=()):
    return TokenSpec(name=name, power=p, toughness=t,
                     colors=frozenset(colors), types=frozenset(types),
                     subtypes=frozenset(subs), keywords=frozenset(kws),
                     tapped=tapped, abilities=tuple(abilities))


SNAKE = _token("Snake", 1, 1, "G", subs=("Snake",), kws=("deathtouch",))
INSECT = _token("Insect", 1, 1, "B", subs=("Insect",))
ELF = _token("Elf Warrior", 1, 1, "G", subs=("Elf", "Warrior"))
ZOMBIE = _token("Zombie", 2, 2, "B", subs=("Zombie",))
THOPTER = _token("Thopter", 1, 1, "U", types=("Artifact", "Creature"),
                 subs=("Thopter",), kws=("flying",))
SERVO = _token("Servo", 1, 1, "", types=("Artifact", "Creature"),
               subs=("Servo",))
SOLDIER = _token("Soldier", 1, 1, "W", subs=("Soldier",))
GNOME = _token("Gnome", 1, 1, "", types=("Artifact", "Creature"),
               subs=("Gnome",))
ROBOT = _token("Robot", 2, 2, "", types=("Artifact", "Creature"),
               subs=("Robot",), tapped=True)
MYR = _token("Myr", 1, 1, "", types=("Artifact", "Creature"), subs=("Myr",))
GOLEM = _token("Golem", 4, 4, "", types=("Artifact", "Creature"),
               subs=("Golem",), kws=("flying",))


def _reef_spec(name, p, t, next_factory):
    ab = ()
    if next_factory is not None:
        ab = (next_factory,)
    return _token(name, p, t, "U", subs=(name,), abilities=ab)


def _reef_chain(name, p, t, deeper):
    """Fish -> Whale -> Kraken death chain (Reef Worm)."""
    def factory():
        spec = None
        if deeper:
            nname, np, nt, ndeeper = deeper
            spec = _reef_spec(nname, np, nt,
                              _reef_chain(nname, np, nt, ndeeper))

        def build_trigger():
            return TriggeredAbility(
                trigger=TriggerSpec(EventType.DIES),
                effect=CreateTokens(1, spec) if spec else Noop(),
                text=f"When this {name} dies ...")
        return build_trigger()
    return factory


# ---------------------------------------------------------------- effects

class Reanimate(Effect):
    """Return the dying creature to the battlefield under your control
    (Necroskitter)."""
    def resolve(self, game, ctx):
        obj = getattr(ctx, "event_obj", None)
        if obj is not None and obj.zone == Zone.GRAVEYARD \
                and not obj.is_token:
            obj.controller = ctx.controller
            game.move_zone(obj, Zone.BATTLEFIELD)
            ctx.controller.stat("necroskitter_steals")


# ---------------------------------------------------------------- statics

def _grant_all_creatures(kw, controller_only=None, others=False):
    def continuous(game, source):
        def applies(g, obj, ch):
            if "Creature" not in ch.types:
                return False
            if controller_only is True \
                    and obj.controller is not source.controller:
                return False
            if others and obj is source:
                return False
            return True
        return [ContinuousEffect(
            layer=6, source=source, applies_to=applies,
            apply=lambda g, o, ch: ch.keywords.add(kw))]
    return StaticAbility(continuous=continuous, text=f"grant {kw}")


def _token_doubler(source_pred=None, creature_only=False, factor=2):
    """'If one or more tokens would be created ... instead' (rule 614.1c)."""
    def replacement(game, source):
        def matches(g, event):
            if event.data.get("controller") is not source.controller:
                return False
            if creature_only and "Creature" not in event.data["spec"].types:
                return False
            return True

        def replace(g, event):
            event.data["count"] *= factor
            return event

        return [Replacement(EventType.CREATE_TOKEN, matches=matches,
                            replace=replace, source=source)]
    return StaticAbility(replacement=replacement, text="token doubler")


def _counter_doubler():
    def replacement(game, source):
        def matches(g, event):
            if event.data.get("resolved"):
                return False
            obj = event.data.get("obj")
            return obj is not None \
                and obj.controller is source.controller

        def replace(g, event):
            event.data["count"] *= 2
            return event

        return [Replacement(EventType.PUT_COUNTERS, matches=matches,
                            replace=replace, source=source)]
    return StaticAbility(replacement=replacement, text="counter doubler")


# ---------------------------------------------------------------- registry

def _auntie_ool(ch, ref):
    """Ward-Blight 2 (SIMPLIFIED to Ward {2}); draw / drain on -1/-1."""
    NOTES[ch.name] = "Ward-Blight 2 simplified to Ward {2}"
    ch.keywords |= {"ward:2"}

    def effect(game, ctx):
        obj = ctx.event_obj
        if obj is None:
            return
        if obj.controller is ctx.controller:
            game.draw(ctx.controller, 1)
        else:
            game.lose_life(obj.controller, 1)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.PUT_COUNTERS,
                            condition=_counters_put_cond("-1/-1")),
        effect=Custom(effect),
        text="Whenever one or more -1/-1 counters are put on a creature..."))


def _blowfly(ch, ref):
    spec = TargetSpec(what="creature")

    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and "Creature" in obj.base.types
                and obj.lki_counters.get("-1/-1", 0) > 0)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=cond),
        effect=PutCounters("-1/-1", 1, "target"), targets=[spec],
        text="Whenever a creature with a -1/-1 counter on it dies, put a "
             "-1/-1 counter on target creature."))


def _necroskitter(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and "Creature" in obj.base.types
                and obj.controller is not source.controller
                and obj.lki_counters.get("-1/-1", 0) > 0)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=cond),
        effect=Reanimate(), optional=True,
        text="Whenever a creature an opponent controls with a -1/-1 "
             "counter on it dies, you may return that card to the "
             "battlefield under your control."))


def _hapatra(ch, ref):
    def dmg_cond(game, source, event):
        return (event.data.get("resolved") and event.data.get("combat")
                and event.data.get("source") is source
                and isinstance(event.data.get("target"), Player))

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DAMAGE, condition=dmg_cond),
        effect=PutCounters("-1/-1", 1, "target"),
        targets=[TargetSpec(what="creature")],
        text="combat damage to a player -> -1/-1 counter"))
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.PUT_COUNTERS,
                            condition=_counters_put_cond("-1/-1")),
        effect=CreateTokens(1, SNAKE),
        text="-1/-1 placed -> snake",
        intervening_if=None))


def _nest_of_scarabs(ch, ref):
    def cond(game, source, event):
        return (event.data.get("resolved")
                and event.data.get("kind") == "-1/-1"
                and event.data.get("obj") is not None)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.PUT_COUNTERS, condition=cond),
        effect=CreateTokens(lambda g, c: 1, INSECT),
        text="-1/-1 placed -> insect"))
    NOTES[ch.name] = "one Insect per counter event (not per counter)"


def _flourishing_defenses(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.PUT_COUNTERS,
                            condition=_counters_put_cond("-1/-1")),
        effect=CreateTokens(1, ELF), text="-1/-1 placed -> elf"))


def _obelisk_spider(ch, ref):
    ch.keywords.add("reach")
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.PUT_COUNTERS,
                            condition=_counters_put_cond("-1/-1")),
        effect=Sequence([LoseLife(1, "each_opponent"), GainLife(1)]),
        text="-1/-1 placed -> drain 1"))
    NOTES[ch.name] = "drain simplified to any -1/-1 event"


def _midnight_banshee(ch, ref):
    ch.keywords |= {"wither"}

    def effect(game, ctx):
        for obj in list(game.battlefield_objects()):
            c = obj.chars(game)
            if "Creature" in c.types and "B" not in c.colors \
                    and obj is not ctx.source:
                game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP,
                            condition=lambda g, s, e:
                            e.data.get("step") == "upkeep"),
        effect=Custom(effect),
        text="each upkeep: -1/-1 on each nonblack creature"))


def _carnifex_demon(ch, ref):
    ch.keywords.add("flying")
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=PutCounters("-1/-1", 2, "self"), text="enters with 2 -1/-1"))

    def effect(game, ctx):
        src = ctx.source
        if src.counters.get("-1/-1", 0) < 1:
            return
        game.remove_counters(src, "-1/-1", 1)
        for obj in list(game.battlefield_objects()):
            if obj is src:
                continue
            if "Creature" in obj.chars(game).types:
                game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(ActivatedAbility(
        mana_cost="{B}", effect=Custom(effect),
        text="{B}, remove -1/-1: -1/-1 on each other creature"))


def _soul_snuffers(ch, ref):
    ch.keywords.add("wither")

    def effect(game, ctx):
        for obj in list(game.battlefield_objects()):
            if "Creature" in obj.chars(game).types:
                game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=Custom(effect), text="ETB: -1/-1 on each creature"))


def _contagion_engine(ch, ref):
    def effect(game, ctx):
        t = ctx.target()
        if isinstance(t, Player):
            for obj in list(t.battlefield):
                if "Creature" in obj.chars(game).types:
                    game.put_counters(obj, "-1/-1", 1)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=Custom(effect), targets=[TargetSpec(what="player")],
        text="ETB: -1/-1 on each creature target player controls"))
    ch.abilities.append(ActivatedAbility(
        mana_cost="{4}", tap_cost=True, effect=Proliferate(2),
        text="{4},{T}: proliferate twice"))


def _contagion_clasp(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=PutCounters("-1/-1", 1, "target"),
        targets=[TargetSpec(what="creature")],
        text="ETB: -1/-1 on target creature"))
    ch.abilities.append(ActivatedAbility(
        mana_cost="{4}", tap_cost=True, effect=Proliferate(1),
        text="{4},{T}: proliferate"))


def _skinrender(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=PutCounters("-1/-1", 3, "target"),
        targets=[TargetSpec(what="creature", other=True)],
        text="ETB: three -1/-1 on target creature"))


def _yawgmoth(ch, ref):
    ch.keywords.add("hexproof")            # SIMPLIFIED: protection from Humans

    ch.abilities.append(ActivatedAbility(
        life_cost=1, sac_cost="another creature",
        effect=Sequence([PutCounters("-1/-1", 1, "target"), DrawCards(1)]),
        targets=[TargetSpec(what="creature", optional=True)],
        text="Pay 1 life, sacrifice another creature: -1/-1 + draw"))
    NOTES[ch.name] = "protection from Humans simplified to hexproof; " \
                     "{B}{B} discard mode omitted"


def _skullclamp(ch, ref):
    def continuous(game, source):
        def applies(g, obj, c):
            return obj is source.attached_to

        return [ContinuousEffect(
            layer=7, sublayer="c", source=source, applies_to=applies,
            apply=lambda g, o, c: (
                setattr(c, "power", (c.power or 0) + 1),
                setattr(c, "toughness", (c.toughness or 0) - 1)))]

    ch.abilities.append(StaticAbility(continuous=continuous,
                                      text="equipped gets +1/-1"))

    def died_cond(game, source, event):
        return event.data.get("obj") is source.attached_to \
            and source.attached_to is not None

    # note: attachment is cleared on zone change, so capture via lki:
    def died_cond2(game, source, event):
        obj = event.data.get("obj")
        return obj is not None and getattr(obj, "_clamped_by", None) is source

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=died_cond2),
        effect=DrawCards(2), text="equipped dies: draw 2"))

    def equip(game, ctx):
        t = ctx.target()
        if t is not None and t.zone == Zone.BATTLEFIELD:
            game.attach(ctx.source, t)
            t._clamped_by = ctx.source

    ch.abilities.append(ActivatedAbility(
        mana_cost="{1}", sorcery_only=True, effect=Custom(equip),
        targets=[TargetSpec(what="creature", controller="you")],
        text="Equip {1}"))


def _scorpion_god(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and "Creature" in obj.base.types
                and obj.lki_counters.get("-1/-1", 0) > 0)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=cond),
        effect=DrawCards(1),
        text="creature with -1/-1 dies: draw"))
    ch.abilities.append(ActivatedAbility(
        mana_cost="{1}{B}{R}", effect=PutCounters("-1/-1", 1, "target"),
        targets=[TargetSpec(what="creature")],
        text="{1}{B}{R}: -1/-1 on target creature"))
    NOTES[ch.name] = "return-to-hand-from-graveyard upkeep trigger omitted"


def _dusk_urchins(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ATTACKS),
        effect=PutCounters("-1/-1", 1, "self"),
        text="attacks: -1/-1 on itself"))

    def effect(game, ctx):
        game.draw(ctx.controller,
                  ctx.source.lki_counters.get("-1/-1", 0))

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES), effect=Custom(effect),
        text="dies: draw per -1/-1 counter"))
    NOTES[ch.name] = "blocks trigger folded into attacks only"


def _grave_titan(ch, ref):
    ch.keywords.add("deathtouch")
    two_zombies = CreateTokens(2, ZOMBIE)
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=two_zombies, text="ETB: two Zombies"))
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ATTACKS),
        effect=two_zombies, text="attacks: two Zombies"))


def _puppeteer_clique(ch, ref):
    ch.keywords |= {"flying", "persist"}

    def effect(game, ctx):
        best, bp = None, -1
        for opp in game.opponents(ctx.controller):
            for card in opp.graveyard:
                if "Creature" in card.base.types \
                        and (card.base.power or 0) > bp:
                    best, bp = card, card.base.power or 0
        if best is not None:
            best.controller = ctx.controller
            game.move_zone(best, Zone.BATTLEFIELD)
            ctx.controller.stat("grave_robs")

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=Custom(effect),
        text="ETB: raid an opposing graveyard"))
    NOTES[ch.name] = "stolen creature stays (haste/exile-at-end omitted)"


def _reassembling_skeleton(ch, ref):
    def effect(game, ctx):
        src = ctx.source
        if src.zone == Zone.GRAVEYARD:
            src.controller = ctx.controller
            game.move_zone(src, Zone.BATTLEFIELD,
                           to_battlefield_tapped=True)

    ab = ActivatedAbility(mana_cost="{1}{B}", effect=Custom(effect),
                          text="{1}{B}: return from graveyard tapped")
    ab.from_graveyard = True
    ch.abilities.append(ab)


def _quillspike(ch, ref):
    def effect(game, ctx):
        for obj in list(ctx.controller.battlefield):
            if obj.counters.get("-1/-1", 0):
                game.remove_counters(obj, "-1/-1", 1)
                break
        else:
            return
        PumpAll(0, 0)  # no-op placeholder
        from .layers import ContinuousEffect as CE
        src = ctx.source
        game.add_floating_effect(CE(
            layer=7, sublayer="c", source=src,
            applies_to=lambda g, o, c: o is src,
            apply=lambda g, o, c: (
                setattr(c, "power", (c.power or 0) + 3),
                setattr(c, "toughness", (c.toughness or 0) + 3)),
            duration="end_of_turn"))

    ch.abilities.append(ActivatedAbility(
        mana_cost="{B}", effect=Custom(effect),
        text="{B/G}, remove a -1/-1 counter: +3/+3"))
    NOTES[ch.name] = "{B/G} simplified to {B}; counter removed from any " \
                     "own creature"


def _everlasting_torment(ch, ref):
    def replacement(game, source):
        def matches(g, event):
            return True

        def replace(g, event):
            return None                            # no life gain (615)

        return [Replacement(EventType.GAIN_LIFE, matches=matches,
                            replace=replace, source=source)]

    ch.abilities.append(StaticAbility(replacement=replacement,
                                      text="players can't gain life"))
    ch.abilities.append(_grant_all_creatures("wither"))
    NOTES[ch.name] = "'damage can't be prevented' omitted"


def _kulrath_knight(ch, ref):
    ch.keywords |= {"flying", "wither"}

    def continuous(game, source):
        def applies(g, obj, c):
            return (obj.controller is not source.controller
                    and "Creature" in c.types and bool(obj.counters))

        return [ContinuousEffect(
            layer=6, source=source, applies_to=applies,
            apply=lambda g, o, c: c.keywords.add("shackled"))]

    ch.abilities.append(StaticAbility(
        continuous=continuous,
        text="opposing creatures with counters can't attack or block"))


def _massacre_girl(ch, ref):
    ch.keywords.add("menace")
    ch.abilities.append(_grant_all_creatures("wither", controller_only=True))
    NOTES[ch.name] = "card-draw clause omitted"


def _glissa(ch, ref):
    ch.keywords |= {"first strike", "deathtouch"}

    def cond(game, source, event):
        return (event.data.get("resolved") and event.data.get("combat")
                and event.data.get("source") is source
                and isinstance(event.data.get("target"), Player))

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DAMAGE, condition=cond),
        effect=DrawCards(1), text="combat damage to player: draw"))
    NOTES[ch.name] = "modal (counters/stun) simplified to draw"


def _fire_covenant(ch, ref):
    ch.abilities.append(SpellAbility(
        effect=Sequence([LoseLife(2, "you"), DealDamage(4, "divided")]),
        text="pay life: 4 damage divided"))
    NOTES[ch.name] = "X life / X damage fixed at 2 life for 4 damage"


def _black_suns_zenith(ch, ref):
    ch.abilities.append(SpellAbility(
        effect=PutCounters("-1/-1", "x", "each_creature"),
        text="X -1/-1 counters on each creature"))
    NOTES[ch.name] = "shuffle-back clause omitted"


def _chaos_warp(ch, ref):
    def effect(game, ctx):
        t = ctx.target()
        if isinstance(t, GameObject) and t.zone == Zone.BATTLEFIELD:
            owner = t.owner
            game.move_zone(t, Zone.LIBRARY)
            game.shuffle(owner)

    ch.abilities.append(SpellAbility(
        effect=Custom(effect), targets=[TargetSpec(what="permanent")],
        text="shuffle target permanent into library"))
    NOTES[ch.name] = "reveal/may-cast rider omitted"


def _cultivate(ch, ref):
    ch.abilities.append(SpellAbility(
        effect=Sequence([SearchLands(1, tapped=True),
                         SearchLands(1, to_hand=True)]),
        text="one basic tapped + one to hand"))


def _farewell(ch, ref):
    def effect(game, ctx):
        for obj in list(game.battlefield_objects()):
            c = obj.chars(game)
            if c.types & {"Creature", "Artifact"}:
                game.exile(obj)

    ch.abilities.append(SpellAbility(
        effect=Custom(effect),
        text="exile all creatures and artifacts"))
    NOTES[ch.name] = "modes fixed: creatures + artifacts, exiled"


def _austere_command(ch, ref):
    def effect(game, ctx):
        for obj in list(game.battlefield_objects()):
            c = obj.chars(game)
            if "Creature" in c.types and (c.power or 0) >= 3:
                game.destroy(obj)
            elif "Artifact" in c.types and obj.controller \
                    is not ctx.controller:
                game.destroy(obj)

    ch.abilities.append(SpellAbility(
        effect=Custom(effect),
        text="destroy big creatures + opposing artifacts"))
    NOTES[ch.name] = "modes fixed"


def _akromas_will(ch, ref):
    ch.abilities.append(SpellAbility(
        effect=Sequence([ProtectAll(), PumpAll(1, 1)]),
        text="protection + small pump"))
    NOTES[ch.name] = "both modes approximated"


def _spell_swindle(ch, ref):
    ch.abilities.append(SpellAbility(
        effect=Sequence([CounterSpell(), CreateTokens(3, TREASURE)]),
        targets=[TargetSpec(what="spell")],
        text="counter + treasures"))
    NOTES[ch.name] = "treasures fixed at 3 (mana value of countered spell)"


def _brasss_bounty(ch, ref):
    def count(game, ctx):
        return sum(1 for o in ctx.controller.battlefield
                   if "Land" in o.chars(game).types)

    ch.abilities.append(SpellAbility(
        effect=CreateTokens(count, TREASURE),
        text="a treasure per land"))


def _curse_of_opulence(ch, ref):
    def effect(game, ctx):
        game.create_tokens(ctx.controller, TREASURE, 1)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP,
                            condition=lambda g, s, e:
                            e.data.get("step") == "upkeep"
                            and e.data.get("player") is s.controller),
        effect=Custom(effect), text="upkeep: gold"))
    NOTES[ch.name] = "attack-the-cursed-player trigger simplified to " \
                     "one Treasure per own upkeep"


def _bootleggers_stash(ch, ref):
    ch.abilities.append(ActivatedAbility(
        tap_cost=True, effect=CreateTokens(1, TREASURE),
        text="{T}: Treasure"))
    NOTES[ch.name] = "grants-lands-the-ability simplified to itself"


def _treasure_vault(ch, ref):
    ch.abilities.append(ActivatedAbility(
        tap_cost=True, is_mana_ability=True, effect=AddMana(types=("C",)),
        text="{T}: Add {C}"))
    ch.abilities.append(ActivatedAbility(
        mana_cost="{4}", tap_cost=True, sac_cost="self",
        effect=CreateTokens(4, TREASURE),
        text="{X}{X},{T}, sac: X Treasures (X=4)"))
    NOTES[ch.name] = "X fixed at 4"


def _retrofitter_foundry(ch, ref):
    ch.abilities.append(ActivatedAbility(
        mana_cost="{2}", tap_cost=True, effect=CreateTokens(1, THOPTER),
        text="{2},{T}: Thopter"))
    NOTES[ch.name] = "untap/upgrade chain simplified"


def _academy_manufactor(ch, ref):
    def replacement(game, source):
        def matches(g, event):
            if event.data.get("controller") is not source.controller:
                return False
            return event.data["spec"].predefined in ("treasure", "clue",
                                                     "food")

        def replace(g, event):
            kinds = {"treasure": TREASURE, "clue": CLUE, "food": FOOD}
            have = event.data["spec"].predefined
            event.data["extra_specs"] = [
                v for k, v in kinds.items() if k != have]
            return event

        return [Replacement(EventType.CREATE_TOKEN, matches=matches,
                            replace=replace, source=source)]
    ch.abilities.append(StaticAbility(replacement=replacement,
                                      text="clue/food/treasure -> all three"))


def _mechanized_production(ch, ref):
    def effect(game, ctx):
        src = ctx.source
        target = src.attached_to
        if target is None or target.zone != Zone.BATTLEFIELD:
            return
        tch = target.chars(game)
        spec = TokenSpec(
            name=tch.name, power=target.base.power,
            toughness=target.base.toughness, colors=frozenset(tch.colors),
            types=frozenset(tch.types), subtypes=frozenset(tch.subtypes),
            predefined="treasure" if "Treasure" in tch.subtypes else "")
        game.create_tokens(ctx.controller, spec, 1)
        names = {}
        for o in ctx.controller.battlefield:
            c = o.chars(game)
            if "Artifact" in c.types and c.name:
                names[c.name] = names.get(c.name, 0) + 1
        if names and max(names.values()) >= 8:
            game.winner = ctx.controller
            game.game_over = True                  # alternate win condition
            ctx.controller.stat("mechanized_wins")

    def cond(game, source, event):
        return (event.data.get("step") == "upkeep"
                and event.data.get("player") is source.controller)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
        effect=Custom(effect), text="upkeep: copy enchanted artifact; "
                                    "win at 8 same-name artifacts"))
    ch.abilities.append(SpellAbility(
        effect=Noop("enchant artifact"), targets=[
            TargetSpec(what="artifact", controller="you")],
        text="enchant artifact you control"))


def _infinite_guideline_station(ch, ref):
    def count_multicolored(game, ctx):
        n = 0
        for o in ctx.controller.battlefield:
            if len(o.chars(game).colors) >= 2:
                n += 1
        return n

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=CreateTokens(count_multicolored, ROBOT),
        text="ETB: a Robot per multicolored permanent"))
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ATTACKS),
        effect=DrawCards(count_multicolored),
        text="attacks: draw per multicolored permanent"))

    def station(game, ctx):
        src = ctx.source
        best = None
        for o in ctx.controller.battlefield:
            c = o.chars(game)
            if "Creature" in c.types and not o.tapped and o is not src \
                    and not (o.entered_this_turn
                             and "haste" not in c.keywords):
                if best is None or (c.power or 0) > \
                        (best.chars(game).power or 0):
                    best = o
        if best is not None:
            game.tap(best)
            game.put_counters(src, "charge",
                              max(0, best.chars(game).power or 0))

    ch.abilities.append(ActivatedAbility(
        sorcery_only=True, effect=Custom(station),
        text="Station (rule 702.184)"))

    def continuous(game, source):
        def applies(g, obj, c):
            return obj is source and obj.counters.get("charge", 0) >= 12

        def add_type(g, o, c):
            c.types.add("Creature")

        def add_kw(g, o, c):
            c.keywords.add("flying")

        return [
            ContinuousEffect(layer=4, source=source, applies_to=applies,
                             apply=add_type),
            ContinuousEffect(layer=6, source=source, applies_to=applies,
                             apply=add_kw),
        ]

    ch.abilities.append(StaticAbility(
        continuous=continuous,
        text="12+ charge: artifact creature with flying"))


def _anim_pakal(ch, ref):
    def effect(game, ctx):
        src = ctx.source
        game.put_counters(src, "+1/+1", 1)
        n = src.counters.get("+1/+1", 0)
        game.create_tokens(ctx.controller, GNOME, n)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ATTACKS), effect=Custom(effect),
        text="attacks: +1/+1 counter, then Gnomes per counter"))
    NOTES[ch.name] = "Gnomes enter untapped, not attacking"


def _esix(ch, ref):
    ch.keywords.add("ward:2")

    def replacement(game, source):
        def matches(g, event):
            if event.data.get("controller") is not source.controller:
                return False
            if getattr(source, "_esix_turn", -1) == g.turn:
                return False
            return "Creature" in event.data["spec"].types

        def replace(g, event):
            source._esix_turn = g.turn
            me = source.controller
            best, bv = None, 0
            for o in me.battlefield:
                c = o.chars(g)
                if "Creature" in c.types:
                    v = (c.power or 0) + (c.toughness or 0)
                    if v > bv:
                        best, bv = o, v
            if best is not None and bv > 2 * ((event.data["spec"].power or 1)
                                              + (event.data["spec"].toughness
                                                 or 1)):
                c = best.chars(g)
                event.data["spec"] = TokenSpec(
                    name=c.name, power=best.base.power,
                    toughness=best.base.toughness,
                    colors=frozenset(c.colors), types=frozenset(c.types),
                    subtypes=frozenset(c.subtypes),
                    keywords=frozenset(best.base.keywords))
            return event

        return [Replacement(EventType.CREATE_TOKEN, matches=matches,
                            replace=replace, source=source)]

    ch.abilities.append(StaticAbility(
        replacement=replacement,
        text="first tokens each turn may copy a creature"))


def _adrix_and_nev(ch, ref):
    ch.keywords.add("ward:2")
    ch.abilities.append(_token_doubler())


def _doubling_season(ch, ref):
    ch.abilities.append(_token_doubler())
    ch.abilities.append(_counter_doubler())


def _ojer_taq(ch, ref):
    ch.abilities.append(_token_doubler(creature_only=True, factor=3))
    NOTES[ch.name] = "back face (Temple of Civilization) not modeled"


def _kaya_geist_hunter(ch, ref):
    ch.abilities.append(ActivatedAbility(
        loyalty_cost=1,
        effect=Custom(lambda game, ctx: game.add_floating_effect(
            ContinuousEffect(
                layer=6, source=ctx.source,
                applies_to=lambda g, o, c: o.controller is ctx.controller
                and "Creature" in c.types,
                apply=lambda g, o, c: c.keywords.add("deathtouch"),
                duration="end_of_turn"))),
        text="+1: your creatures gain deathtouch"))

    def minus2(game, ctx):
        src = ctx.source
        me = ctx.controller
        game.add_floating_effect(ContinuousEffect(
            layer=6, source=src, applies_to=lambda g, o, c: False,
            apply=lambda g, o, c: None, duration="end_of_turn"))
        rep = Replacement(
            EventType.CREATE_TOKEN,
            matches=lambda g, e: e.data.get("controller") is me,
            replace=_double_count, source=src, duration="floating")
        game.replacements.floating.append(rep)
        game._kaya_reps = getattr(game, "_kaya_reps", [])
        game._kaya_reps.append(rep)

    ch.abilities.append(ActivatedAbility(
        loyalty_cost=-2, effect=Custom(minus2),
        text="-2: token doubling until end of turn"))
    NOTES[ch.name] = "-2 doubling persists to end of game (cleanup " \
                     "of floating replacement simplified)"


def _double_count(g, event):
    event.data["count"] *= 2
    return event


def _saheeli_rai(ch, ref):
    ch.abilities.append(ActivatedAbility(
        loyalty_cost=1,
        effect=Sequence([Scry(1),
                         Custom(lambda game, ctx: [
                             game.deal_damage(ctx.source, p, 1)
                             for p in game.opponents(ctx.controller)])]),
        text="+1: scry 1, 1 damage to each opponent"))
    NOTES[ch.name] = "-2 copy-artifact and -7 omitted"


def _saheeli_sublime(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (event.data.get("player") is source.controller
                and obj is not None
                and "Creature" not in obj.base.types)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.CAST, condition=cond),
        effect=CreateTokens(1, SERVO),
        text="you cast a noncreature spell: Servo"))


def _sai(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (event.data.get("player") is source.controller
                and obj is not None and "Artifact" in obj.base.types)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.CAST, condition=cond),
        effect=CreateTokens(1, THOPTER),
        text="you cast an artifact spell: Thopter"))
    NOTES[ch.name] = "sacrifice-to-draw ability omitted"


def _thopter_spy_network(ch, ref):
    def cond(game, source, event):
        if event.data.get("step") != "upkeep" \
                or event.data.get("player") is not source.controller:
            return False
        return any("Artifact" in o.chars(game).types
                   for o in source.controller.battlefield)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
        effect=CreateTokens(1, THOPTER),
        text="upkeep (if you control an artifact): Thopter"))

    def dmg_cond(game, source, event):
        src = event.data.get("source")
        return (event.data.get("resolved") and event.data.get("combat")
                and isinstance(event.data.get("target"), Player)
                and isinstance(src, GameObject)
                and src.controller is source.controller
                and "Artifact" in src.chars(game).types)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DAMAGE, condition=dmg_cond),
        effect=DrawCards(1),
        text="artifact combat damage to a player: draw"))


def _bident(ch, ref):
    def dmg_cond(game, source, event):
        src = event.data.get("source")
        return (event.data.get("resolved") and event.data.get("combat")
                and isinstance(event.data.get("target"), Player)
                and isinstance(src, GameObject)
                and src.controller is source.controller
                and "Creature" in src.chars(game).types)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DAMAGE, condition=dmg_cond),
        effect=DrawCards(1),
        text="creature combat damage to a player: draw"))
    NOTES[ch.name] = "activated force-attack mode omitted"


def _mentor_of_the_meek(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and obj is not source
                and obj.controller is source.controller
                and "Creature" in obj.base.types
                and (obj.base.power or 0) <= 2)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD, condition=cond),
        effect=DrawCards(1),
        text="small creature enters: draw"))
    NOTES[ch.name] = "the {1} payment is waived (SIMPLIFIED)"


def _blood_artist(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return obj is not None and "Creature" in obj.base.types

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=cond),
        effect=Drain(1), text="any creature dies: drain 1"))
    NOTES[ch.name] = "drains each opponent instead of target player"


def _zulaport(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and "Creature" in obj.base.types
                and obj.controller is source.controller)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=cond),
        effect=Drain(1), text="own creature dies: drain 1"))


def _marionette_apprentice(ch, ref):
    def cond(game, source, event):
        obj = event.data.get("obj")
        return (obj is not None and obj.controller is source.controller
                and ("Artifact" in obj.base.types or obj.is_token))

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=cond),
        effect=Drain(1),
        text="own artifact/token dies: drain 1"))


def _shalai(ch, ref):
    ch.keywords.add("flying")

    def continuous(game, source):
        def applies(g, obj, c):
            return (obj.controller is source.controller
                    and obj is not source)

        return [ContinuousEffect(
            layer=6, source=source, applies_to=applies,
            apply=lambda g, o, c: c.keywords.add("hexproof"))]

    ch.abilities.append(StaticAbility(
        continuous=continuous, text="your other stuff has hexproof"))
    NOTES[ch.name] = "player hexproof + {4}{G}{G} pump omitted"


def _skyclave(ch, ref):
    def effect(game, ctx):
        t = ctx.target()
        if t is not None and t.zone == Zone.BATTLEFIELD:
            game.exile(t)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=Custom(effect),
        targets=[TargetSpec(what="nonland", controller="opponent")],
        text="ETB: exile target small nonland permanent"))
    NOTES[ch.name] = "mv<=4 restriction and Illusion give-back omitted"


def _restoration_angel(ch, ref):
    ch.keywords |= {"flash", "flying"}
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=Blink(), optional=True,
        targets=[TargetSpec(what="creature", controller="you", other=True,
                            optional=True)],
        text="ETB: you may blink another creature you control"))


def _conjurers_closet(ch, ref):
    def cond(game, source, event):
        return (event.data.get("step") == "end"
                and event.data.get("player") is source.controller)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
        effect=Blink(), optional=True,
        targets=[TargetSpec(what="creature", controller="you",
                            optional=True)],
        text="end step: blink a creature you control"))


def _reef_worm(ch, ref):
    fish = _reef_spec("Fish", 3, 3,
                      _reef_chain("Fish", 3, 3,
                                  ("Whale", 6, 6, ("Kraken", 9, 9, None))))
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES),
        effect=CreateTokens(1, fish),
        text="dies: Fish -> Whale -> Kraken"))


def _hangarback(ch, ref):
    ch.etb_x_counters = "+1/+1"

    def effect(game, ctx):
        n = ctx.source.lki_counters.get("+1/+1", 0)
        game.create_tokens(ctx.controller, THOPTER, n)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES), effect=Custom(effect),
        text="dies: a Thopter per +1/+1 counter"))


def _triplicate_titan(ch, ref):
    ch.keywords |= {"flying", "vigilance", "trample"}
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES),
        effect=CreateTokens(3, GOLEM),
        text="dies: three 4/4 Golems"))
    NOTES[ch.name] = "Golem keyword split simplified to flying on all"


def _myr_battlesphere(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=CreateTokens(4, MYR), text="ETB: four Myr"))
    NOTES[ch.name] = "attack pump/damage trigger omitted"


def _determined_iteration(ch, ref):
    def cond(game, source, event):
        return (event.data.get("step") == "combat_begin"
                and event.data.get("player") is source.controller)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
        effect=Populate(), text="begin combat: populate"))
    NOTES[ch.name] = "haste grant omitted"


def _growing_ranks(ch, ref):
    def cond(game, source, event):
        return (event.data.get("step") == "upkeep"
                and event.data.get("player") is source.controller)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
        effect=Populate(), text="upkeep: populate"))


def _extravagant_replication(ch, ref):
    def effect(game, ctx):
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
            spec = TokenSpec(name=c.name, power=best.base.power,
                             toughness=best.base.toughness,
                             colors=frozenset(c.colors),
                             types=frozenset(c.types),
                             subtypes=frozenset(c.subtypes),
                             keywords=frozenset(best.base.keywords))
            game.create_tokens(me, spec, 1)

    def cond(game, source, event):
        return (event.data.get("step") == "upkeep"
                and event.data.get("player") is source.controller)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.BEGIN_STEP, condition=cond),
        effect=Custom(effect),
        text="upkeep: token copy of your best creature"))


def _imprisoned_in_the_moon(ch, ref):
    def continuous(game, source):
        def applies(g, obj, c):
            return obj is source.attached_to

        def to_land(g, o, c):
            c.types = {"Land"}
            c.subtypes = set()
            c.supertypes -= {"Legendary"}

        def strip_abilities(g, o, c):
            c.abilities = [ActivatedAbility(
                tap_cost=True, is_mana_ability=True,
                effect=AddMana(types=("C",)), text="{T}: Add {C}")]
            c.keywords = set()

        def zero_pt(g, o, c):
            c.power = c.toughness = None

        return [
            ContinuousEffect(layer=4, source=source, applies_to=applies,
                             apply=to_land),
            ContinuousEffect(layer=6, source=source, applies_to=applies,
                             apply=strip_abilities),
            ContinuousEffect(layer=7, sublayer="b", source=source,
                             applies_to=applies, apply=zero_pt),
        ]

    ch.abilities.append(StaticAbility(
        continuous=continuous,
        text="enchanted permanent is a colorless land"))
    ch.abilities.append(SpellAbility(
        effect=Noop("enchant"), targets=[
            TargetSpec(what="creature_or_planeswalker")],
        text="enchant creature or planeswalker"))


def _banishing_light(ch, ref):
    def effect(game, ctx):
        t = ctx.target()
        if t is None or t.zone != Zone.BATTLEFIELD:
            return
        game.exile(t)
        if t.zone == Zone.EXILE:
            ctx.source._imprisoned = t

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=Custom(effect),
        targets=[TargetSpec(what="nonland", controller="opponent")],
        text="ETB: exile target nonland permanent"))

    def release_cond(game, source, event):
        return event.data.get("obj") is source

    def release(game, ctx):
        held = getattr(ctx.source, "_imprisoned", None)
        if held is not None and held.zone == Zone.EXILE:
            held.controller = held.owner
            game.move_zone(held, Zone.BATTLEFIELD)

    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.DIES, condition=release_cond),
        effect=Custom(release), text="leaves: return the exiled card"))


def _chromatic_lantern(ch, ref):
    ch.abilities.append(ActivatedAbility(
        tap_cost=True, is_mana_ability=True, effect=AddMana(any_color=True),
        text="{T}: Add one mana of any color"))
    NOTES[ch.name] = "lands-have-any-color static omitted"


def _coalition_relic(ch, ref):
    ch.abilities.append(ActivatedAbility(
        tap_cost=True, is_mana_ability=True, effect=AddMana(any_color=True),
        text="{T}: Add one mana of any color"))
    NOTES[ch.name] = "charge counter mode omitted"


def _channeler_initiate(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.ENTERS_BATTLEFIELD),
        effect=PutCounters("-1/-1", 3, "self"),
        text="enters with three -1/-1 counters"))
    ch.abilities.append(ActivatedAbility(
        tap_cost=True, is_mana_ability=True, effect=AddMana(any_color=True),
        text="{T}, remove a -1/-1 counter: any color (SIMPLIFIED: "
             "counter not removed)"))
    NOTES[ch.name] = "mana ability does not remove the -1/-1 counter"


def _devoted_druid(ch, ref):
    ch.abilities.append(ActivatedAbility(
        tap_cost=True, is_mana_ability=True, effect=AddMana(types=("G",)),
        text="{T}: Add {G}"))
    NOTES[ch.name] = "untap-with--1/-1 mode omitted"


def _evolution_sage(ch, ref):
    ch.abilities.append(TriggeredAbility(
        trigger=TriggerSpec(EventType.LAND_PLAYED,
                            condition=lambda g, s, e:
                            e.data.get("player") is s.controller),
        effect=Proliferate(), text="landfall: proliferate"))


def _heroic_reinforcements(ch, ref):
    ch.abilities.append(SpellAbility(
        effect=CreateTokens(2, _token("Soldier", 1, 1, "RW",
                                      subs=("Soldier",), kws=("haste",))),
        text="two hasty Soldiers"))
    NOTES[ch.name] = "+1/+1 until end of turn pump omitted"


def _swan_song(ch, ref):
    def effect(game, ctx):
        t = ctx.target()
        if t is not None:
            game.counter_spell(t)
            if t.controller is not ctx.controller:
                game.create_tokens(t.controller, _token(
                    "Bird", 2, 2, "U", subs=("Bird",), kws=("flying",)), 1)

    ch.abilities.append(SpellAbility(
        effect=Custom(effect), targets=[TargetSpec(what="spell")],
        text="counter; its controller gets a Bird"))


def _arcane_denial(ch, ref):
    def effect(game, ctx):
        t = ctx.target()
        if t is not None:
            other = t.controller
            game.counter_spell(t)
            game.draw(other, 2)
        game.draw(ctx.controller, 1)

    ch.abilities.append(SpellAbility(
        effect=Custom(effect), targets=[TargetSpec(what="spell")],
        text="counter; they draw 2, you draw 1 (draw timing simplified)"))


def _wayfarers_bauble(ch, ref):
    ch.abilities.append(ActivatedAbility(
        mana_cost="{2}", tap_cost=True, sac_cost="self",
        effect=SearchLands(1, tapped=True),
        text="{2},{T}, sac: basic land tapped"))


def _myriad_landscape(ch, ref):
    ch.abilities.append(ActivatedAbility(
        mana_cost="{2}", tap_cost=True, sac_cost="self",
        effect=SearchLands(2, tapped=True),
        text="{2},{T}, sac: two basic lands tapped"))
    NOTES[ch.name] = "'share a land type' restriction omitted"


def _persist_sorcery(ch, ref):
    def effect(game, ctx):
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

    ch.abilities.append(SpellAbility(
        effect=Custom(effect),
        text="return a creature from your graveyard (loses legendary "
             "rider omitted)"))
    NOTES[ch.name] = "returns your best creature; legendary clause omitted"


def _aberrant_return(ch, ref):
    _persist_sorcery(ch, ref)


def _grave_venerations(ch, ref):
    _persist_sorcery(ch, ref)
    NOTES[ch.name] = "approximated as graveyard recursion"


OVERRIDES = {
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


def apply_override(ch, ref) -> bool:
    fn = OVERRIDES.get(ref.name)
    if fn is None and " // " in ref.name:
        fn = OVERRIDES.get(ref.name.split(" // ")[0])
    if fn is None:
        return False
    fn(ch, ref)
    # land mana facts still apply (e.g. Treasure Vault, Myriad Landscape)
    if "Land" in ch.types and ref.behavior.get("land_colors") \
            and not any(getattr(a, "is_mana_ability", False)
                        for a in ch.abilities):
        colors = set(ref.behavior["land_colors"])
        any_c = colors >= set("WUBRG")
        from .effects import AddMana as _AM
        ch.abilities.append(ActivatedAbility(
            tap_cost=True, is_mana_ability=True,
            effect=_AM(types=tuple(c for c in colors if c in "WUBRGC")
                       if not any_c else (), any_color=any_c),
            text="{T}: Add mana."))
    return True
