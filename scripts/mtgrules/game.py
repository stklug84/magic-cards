"""The game: zones, events, the stack, priority, and state-based actions.

Implements the CR machinery that the heuristic simulator lacks: a real
stack with priority passing (rules 117, 405, 601-608), state-based actions
(rule 704), the trigger queue (rule 603), and the Commander variant rules
(rule 903). Turn structure and combat live in turns.py / combat.py.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .abilities import (ActivatedAbility, SpellAbility, TokenSpec,
                        TriggeredAbility)
from .cr import rule, unsupported
from .effects import Ctx
from .events import Event, EventType
from .layers import ContinuousEffect, LayerSystem
from .manasys import ManaPool, parse_cost
from .objects import Characteristics, GameObject, Player, Zone
from .replacements import ReplacementEngine

unsupported("903.4", "deck color-identity legality is validated by the "
                     "knowledge-graph tooling, not at runtime")


@rule("405.1", "405.2")
@dataclass
class StackItem:
    """A spell or ability on the stack (rules 405.1-405.2)."""
    obj: object                         # GameObject (spell) or ability
    source: object                     # for abilities: their source object
    controller: object
    ability: object = None             # SpellAbility/Activated/Triggered
    targets: list = field(default_factory=list)
    x: int = 0
    is_spell: bool = False

    def __repr__(self):
        name = (self.obj.base.name if self.is_spell
                else f"ability of {self.source.base.name}")
        return f"<Stack:{name}>"


@dataclass
class PendingTrigger:
    ability: object
    source: object
    controller: object
    event: object


def _lname(x) -> str:
    """Loggable name for a Player, GameObject, or stack item."""
    if isinstance(x, Player):
        return x.name
    if isinstance(x, GameObject):
        return x.base.name or "(unnamed)"
    if isinstance(x, StackItem):
        return (x.obj.base.name if x.is_spell
                else f"ability of {x.source.base.name}")
    return str(x)


class Game:
    def __init__(self, players: list[Player], rng: random.Random,
                 policies: dict, turn_cap: int = 40, log=None):
        self.players = players
        self.rng = rng
        self.policies = policies
        self.turn_cap = turn_cap
        self.turn = 0
        self.active_idx = 0
        self.phase = "main1"
        #: (obj id, ability index) activated this turn (once-per-turn and
        #: loyalty tracking, rule 606.3)
        self.activated_this_turn: set = set()
        self.stack: list[StackItem] = []
        self.pending_triggers: list[PendingTrigger] = []
        self.tick = 0
        self.layers = LayerSystem(self)
        self.replacements = ReplacementEngine(self)
        self.log = log or (lambda *a, **k: None)
        self.game_over = False
        self.winner: Player | None = None
        self.unknown_clauses: dict[str, set] = {}
        for p in players:
            p.mana_pool = ManaPool()

    # ------------------------------------------------------------ helpers
    def bump(self):
        self.tick += 1

    @property
    def active_player(self) -> Player:
        return self.players[self.active_idx]

    @rule("102.2", "102.3")
    def opponents(self, player) -> list[Player]:
        return [p for p in self.players if p is not player and not p.lost]

    def alive(self) -> list[Player]:
        return [p for p in self.players if not p.lost]

    @rule("101.4")
    def players_apnap(self) -> list[Player]:
        """Active player, nonactive players in turn order (rule 101.4)."""
        n = len(self.players)
        order = [self.players[(self.active_idx + i) % n] for i in range(n)]
        return [p for p in order if not p.lost]

    def policy(self, player):
        return self.policies[player.name]

    def battlefield_objects(self):
        for p in self.players:
            yield from p.battlefield

    def commander_identity(self, player) -> set:
        obj = player.commander_obj
        if obj is None or obj.card_ref is None:
            return set("WUBRG")
        return set(obj.card_ref.color_identity)

    @rule("702.11")
    def cant_be_targeted(self, obj, ctx) -> bool:
        """Hexproof (rule 702.11): can't be targeted by opponents."""
        ch = obj.chars(self)
        return ("hexproof" in ch.keywords
                and ctx.controller is not obj.controller)

    # ------------------------------------------------------------ events
    def emit(self, event: Event):
        """Route an event through replacement effects (rule 614), then
        return the final event, or None if it was prevented."""
        return self.replacements.process(event)

    @rule("603.2", "603.3")
    def _queue_triggers(self, event: Event):
        """Collect triggered abilities that trigger off *event*."""
        watchers = []
        for obj in self.battlefield_objects():
            for ab in obj.chars(self).abilities:
                if isinstance(ab, TriggeredAbility):
                    watchers.append((obj, ab))
        # rule 603.6b-d: leave-the-battlefield / dies triggers of the
        # departing object itself look back in time
        obj = event.data.get("obj")
        if obj is not None and isinstance(obj, GameObject) \
                and obj.zone != Zone.BATTLEFIELD:
            for ab in obj.base.abilities:
                if isinstance(ab, TriggeredAbility):
                    watchers.append((obj, ab))
        seen = set()
        for source, ab in watchers:
            key = (source.id, id(ab))
            if key in seen:
                continue
            seen.add(key)
            if ab.trigger.matches(self, source, event):
                if ab.intervening_if and not ab.intervening_if(self, source):
                    continue                       # rule 603.4
                self.pending_triggers.append(PendingTrigger(
                    ab, source, source.controller, event))

    @rule("603.3b")
    def put_triggers_on_stack(self):
        """APNAP order; each player orders their own triggers."""
        if not self.pending_triggers:
            return False
        by_player = {}
        for t in self.pending_triggers:
            by_player.setdefault(t.controller, []).append(t)
        self.pending_triggers = []
        for p in self.players_apnap():
            mine = by_player.get(p, [])
            if len(mine) > 1:
                mine = self.policy(p).order_triggers(self, mine)
            for t in mine:
                if getattr(t.ability, "once_each_turn", False):
                    key = ("trig", t.source.id, id(t.ability))
                    if key in self.activated_this_turn:
                        continue                   # "only once each turn"
                    self.activated_this_turn.add(key)
                if t.ability.optional and not self.policy(p).accept_optional(
                        self, t):
                    continue
                targets = self._choose_targets(p, t.ability, t.source)
                if targets is None and t.ability.targets:
                    continue                       # no legal targets: fizzle
                self.stack.append(StackItem(
                    obj=t, source=t.source, controller=p,
                    ability=t.ability, targets=targets or []))
                self.log("trigger", who=p.name,
                         what=t.source.base.name)
        return True

    # ------------------------------------------------------------ zones
    @rule("400.1", "400.7", "903.9a", "704.5d")
    def move_zone(self, obj: GameObject, to_zone: str, *,
                  to_battlefield_tapped=False, counters=None,
                  pos: str = "top"):
        from_zone = obj.zone
        event = self.emit(Event(EventType.ZONE_CHANGE, {
            "obj": obj, "from": from_zone, "to": to_zone,
            "tapped": to_battlefield_tapped, "counters": dict(counters or {}),
            "controller": obj.controller}))
        if event is None:
            return None
        to_zone = event.data["to"]

        # rule 903.9a-b: a commander's owner may move it to the command
        # zone instead of graveyard/exile/hand/library
        if obj.commander and to_zone in (Zone.GRAVEYARD, Zone.EXILE,
                                         Zone.HAND, Zone.LIBRARY):
            if self.policy(obj.owner).commander_to_command_zone(
                    self, obj, to_zone):
                to_zone = Zone.COMMAND

        self._remove_from_zone(obj, from_zone)

        # rule 704.5d: a token anywhere but the battlefield ceases to exist
        if obj.is_token and to_zone != Zone.BATTLEFIELD:
            obj.zone = "ceased"
            self.bump()
            if from_zone == Zone.BATTLEFIELD and to_zone == Zone.GRAVEYARD:
                obj.controller.stat("tokens_killed")
                self.log("dies", who=obj.controller.name, card=_lname(obj),
                         token=True)
                self._fire_leave_battlefield(obj, event)
            return None

        obj.zone = to_zone
        holder = obj.controller if to_zone == Zone.BATTLEFIELD else obj.owner
        if to_zone == Zone.LIBRARY:
            if pos == "bottom":
                holder.library.append(obj)
            else:
                holder.library.insert(0, obj)
        else:
            holder.zone_list(to_zone).append(obj)

        if from_zone == Zone.BATTLEFIELD or to_zone == Zone.BATTLEFIELD:
            # last-known-information for leave-the-battlefield triggers
            # (rule 603.10a: they use the object's last existence)
            obj.lki_counters = dict(obj.counters)
            for att in list(obj.attachments):
                att.attached_to = None
            if obj.attached_to is not None \
                    and obj in obj.attached_to.attachments:
                obj.attached_to.attachments.remove(obj)
            obj.reset_battlefield_state()          # rule 400.7
        if to_zone == Zone.BATTLEFIELD:
            obj.entered_this_turn = True
            obj.tapped = bool(event.data.get("tapped"))
            for kind, n in event.data.get("counters", {}).items():
                obj.counters[kind] = obj.counters.get(kind, 0) + n
            self.bump()
            etb = Event(EventType.ENTERS_BATTLEFIELD, {"obj": obj})
            self._queue_triggers(etb)
        elif from_zone == Zone.BATTLEFIELD:
            self.bump()
            if to_zone == Zone.GRAVEYARD:
                self.log("dies", who=obj.controller.name, card=_lname(obj))
            self._fire_leave_battlefield(obj, event)
        else:
            self.bump()
        return obj

    def _fire_leave_battlefield(self, obj, zone_event):
        if zone_event.data["to"] == Zone.GRAVEYARD:
            self._queue_triggers(Event(EventType.DIES, {"obj": obj}))

    def _remove_from_zone(self, obj, zone):
        if zone == "ceased":
            return
        for p in self.players:
            lst = p.zone_list(zone) if zone != Zone.STACK else None
            if lst is not None and obj in lst:
                lst.remove(obj)
                return

    # ------------------------------------------------------------ actions
    @rule("111.2", "111.3")
    def create_tokens(self, controller, spec: TokenSpec, count: int, *,
                      source=None, tapped=None):
        event = self.emit(Event(EventType.CREATE_TOKEN, {
            "spec": spec, "count": count, "controller": controller,
            "source": source}))
        if event is None or event.data["count"] <= 0:
            return []
        made = []
        specs = [(event.data["spec"], event.data["count"])]
        for extra in event.data.get("extra_specs", []):
            specs.append((extra, event.data["count"]))
        for spec, count in specs:
            for _ in range(count):
                base = Characteristics(
                    name=spec.name, colors=set(spec.colors),
                    types=set(spec.types), subtypes=set(spec.subtypes),
                    power=spec.power, toughness=spec.toughness,
                    keywords=set(spec.keywords))
                if spec.predefined in ("treasure", "gold"):
                    base.abilities.append(ActivatedAbility(
                        tap_cost=True, sac_cost="self", is_mana_ability=True,
                        effect=_ADD_ANY_MANA,
                        text="{T}, Sacrifice: Add one mana of any color."))
                for factory in getattr(spec, "abilities", ()) or ():
                    base.abilities.append(factory())
                tok = GameObject(base, controller, is_token=True)
                tok.zone = Zone.BATTLEFIELD
                tok.controller = controller
                tok.tapped = spec.tapped if tapped is None else tapped
                tok.entered_this_turn = True
                controller.battlefield.append(tok)
                made.append(tok)
                controller.stat("tokens_created")
                if spec.predefined in ("treasure", "gold"):
                    controller.stat("treasures_made")
                self.log("token", who=controller.name, name=spec.name,
                         pt=(f"{spec.power}/{spec.toughness}"
                             if spec.power is not None else None))
                self.bump()
                self._queue_triggers(Event(EventType.ENTERS_BATTLEFIELD,
                                           {"obj": tok}))
        return made

    @rule("121.1", "121.4")
    def draw(self, player, n=1):
        for _ in range(n):
            event = self.emit(Event(EventType.DRAW, {"player": player}))
            if event is None:
                continue
            if not player.library:
                player.drew_from_empty = True      # rule 704.5b, lose later
                continue
            card = player.library.pop(0)
            card.zone = Zone.HAND
            player.hand.append(card)
            player.stat("cards_drawn")
            self.log("draw", who=player.name)
        self.bump()

    @rule("120.3", "119.3")
    def deal_damage(self, source, target, amount, *, combat=False):
        if amount <= 0:
            return
        event = self.emit(Event(EventType.DAMAGE, {
            "source": source, "target": target, "amount": amount,
            "combat": combat}))
        if event is None:
            return
        amount = event.data["amount"]
        src_ch = source.chars(self) if isinstance(source, GameObject) else None
        # damage triggers (e.g. "deals combat damage to a player")
        self._queue_triggers(Event(EventType.DAMAGE, {
            "source": source, "target": target, "amount": amount,
            "combat": combat, "resolved": True}))
        self.log("damage", src=_lname(source), target=_lname(target),
                 n=amount, combat=combat)
        if isinstance(target, Player):
            if src_ch is not None and "infect" in src_ch.keywords:
                # rule 702.90b: infect damage to a player is poison
                # counters instead of life loss
                self.add_poison(target, amount)
            else:
                # rule 120.3a: damage to a player causes life loss
                self.lose_life(target, amount, damage=True)
            if combat and src_ch is not None:
                # rule 702.164a toxic: combat damage also gives poison
                for kw in src_ch.keywords:
                    if kw.startswith("toxic:"):
                        self.add_poison(target, int(kw.split(":")[1]))
            if combat and isinstance(source, GameObject) and source.commander:
                # rules 903.10a / 704.6c: commander damage
                dealt = target.commander_damage.get(source.id, 0) + amount
                target.commander_damage[source.id] = dealt
        else:
            ch = target.chars(self)
            if "Planeswalker" in ch.types:
                # rule 120.3c: damage removes that many loyalty counters
                self.remove_counters(target, "loyalty", amount)
            else:
                if src_ch is not None and "wither" in src_ch.keywords:
                    # rule 702.80: wither deals damage as -1/-1 counters
                    self.put_counters(target, "-1/-1", amount)
                else:
                    target.damage += amount
                    if src_ch is not None and "deathtouch" in src_ch.keywords:
                        target.deathtouch_damage = True   # rule 704.5h
            self.bump()

    @rule("122.1", "702.90b")
    def add_poison(self, player, n):
        """Give a player poison counters (infect / toxic)."""
        if n <= 0:
            return
        player.poison += n
        player.stat("poison_received", n)
        self.log("poison", who=player.name, n=n, total=player.poison)
        self.bump()

    def gain_life(self, player, n):
        if n <= 0:
            return
        event = self.emit(Event(EventType.GAIN_LIFE,
                                {"player": player, "amount": n}))
        if event is None:
            return
        player.life += event.data["amount"]
        self.log("life", who=player.name, delta=event.data["amount"],
                 total=player.life)
        self.bump()

    def lose_life(self, player, n, damage=False):
        if n <= 0:
            return
        event = self.emit(Event(EventType.LOSE_LIFE,
                                {"player": player, "amount": n,
                                 "damage": damage}))
        if event is None:
            return
        player.life -= event.data["amount"]
        self.log("life", who=player.name, delta=-event.data["amount"],
                 total=player.life)
        self.bump()

    @rule("122.1", "122.6")
    def put_counters(self, obj, kind, n):
        if n <= 0 or obj.zone != Zone.BATTLEFIELD:
            return
        event = self.emit(Event(EventType.PUT_COUNTERS, {
            "obj": obj, "kind": kind, "count": n,
            "controller": obj.controller}))
        if event is None or event.data["count"] <= 0:
            return
        n = event.data["count"]
        obj.counters[kind] = obj.counters.get(kind, 0) + n
        obj.controller.stat("counters_received", n)
        self.bump()
        self._queue_triggers(Event(EventType.PUT_COUNTERS, {
            "obj": obj, "kind": kind, "count": n, "resolved": True}))

    def remove_counters(self, obj, kind, n):
        have = obj.counters.get(kind, 0)
        take = min(have, n)
        if take:
            obj.counters[kind] = have - take
            if not obj.counters[kind]:
                del obj.counters[kind]
            self.bump()

    @rule("702.87")
    def proliferate(self, player):
        """Choose any number of permanents/players with counters; give each
        one more counter of each kind already there (rule 702.87a)."""
        picks = self.policy(player).choose_proliferate(self, player)
        for obj in picks:
            for kind in list(obj.counters):
                self.put_counters(obj, kind, 1)
        player.stat("proliferates")

    @rule("701.34")
    def populate(self, player):
        tokens = [o for o in player.battlefield if o.is_token
                  and "Creature" in o.chars(self).types]
        if not tokens:
            return
        pick = self.policy(player).choose_populate(self, tokens)
        if pick is None:
            return
        ch = pick.chars(self)
        spec = TokenSpec(name=ch.name, power=pick.base.power,
                         toughness=pick.base.toughness,
                         colors=frozenset(ch.colors),
                         types=frozenset(ch.types),
                         subtypes=frozenset(ch.subtypes),
                         keywords=frozenset(pick.base.keywords))
        self.create_tokens(player, spec, 1)

    @rule("701.7", "701.7b")
    def destroy(self, obj):
        if obj.zone != Zone.BATTLEFIELD:
            return
        if "indestructible" in obj.chars(self).keywords:   # rule 702.12
            return
        event = self.emit(Event(EventType.DESTROY, {"obj": obj}))
        if event is None:
            return
        self.move_zone(obj, Zone.GRAVEYARD)

    @rule("701.9")
    def exile(self, obj):
        if obj.zone != Zone.BATTLEFIELD:
            return
        event = self.emit(Event(EventType.EXILE_OBJ, {"obj": obj}))
        if event is None:
            return
        self.move_zone(obj, Zone.EXILE)

    @rule("701.22")
    def sacrifice(self, player, obj):
        """Sacrifice can't be replaced and ignores indestructible
        (rules 701.22a-b)."""
        if obj.zone != Zone.BATTLEFIELD or obj.controller is not player:
            return
        self.emit(Event(EventType.SACRIFICE, {"obj": obj}))
        self.move_zone(obj, Zone.GRAVEYARD)

    def tap(self, obj):
        if not obj.tapped:
            obj.tapped = True
            self.bump()
            self._queue_triggers(Event(EventType.TAP, {"obj": obj}))

    def untap(self, obj):
        if obj.tapped:
            obj.tapped = False
            self.bump()
            self._queue_triggers(Event(EventType.UNTAP, {"obj": obj}))

    @rule("701.23")
    def search_lands(self, player, n, *, tapped=True, to_hand=False,
                     basic_only=True):
        found = 0
        for card in list(player.library):
            if found >= n:
                break
            ch = card.base
            if "Land" not in ch.types:
                continue
            if basic_only and "Basic" not in ch.supertypes:
                continue
            player.library.remove(card)
            if to_hand:
                card.zone = Zone.HAND
                player.hand.append(card)
            else:
                card.zone = Zone.BATTLEFIELD
                card.controller = player
                player.battlefield.append(card)
                card.reset_battlefield_state()
                card.entered_this_turn = True
                card.tapped = tapped
                self._queue_triggers(Event(EventType.ENTERS_BATTLEFIELD,
                                           {"obj": card}))
            found += 1
        self.shuffle(player)

    @rule("701.24")
    def shuffle(self, player):
        self.rng.shuffle(player.library)
        self.emit(Event(EventType.SHUFFLE, {"player": player}))
        self.bump()

    @rule("701.26")
    def scry(self, player, n):
        top = player.library[:n]
        keep, bottom = self.policy(player).scry(self, player, top)
        player.library = keep + player.library[n:] + bottom

    def tutor(self, player):
        pick = self.policy(player).choose_tutor_card(self, player)
        if pick is not None and pick in player.library:
            player.library.remove(pick)
            pick.zone = Zone.HAND
            player.hand.append(pick)
        self.shuffle(player)

    def blink(self, obj):
        owner_ctl = obj.controller
        if obj.is_token:
            self.move_zone(obj, Zone.EXILE)      # token ceases (704.5d)
            return
        moved = self.move_zone(obj, Zone.EXILE)
        if moved is not None and moved.zone == Zone.EXILE:
            moved.controller = owner_ctl
            self.move_zone(moved, Zone.BATTLEFIELD)

    @rule("701.5")
    def counter_spell(self, item: StackItem):
        if item in self.stack:
            if self._uncounterable(item):
                self.log("uncounterable", spell=_lname(item))
                return
            self.log("counter", spell=_lname(item),
                     who=item.controller.name)
            self.stack.remove(item)
            if item.is_spell and not item.obj.is_token:
                self.move_zone(item.obj, Zone.GRAVEYARD)
            item.controller.stat("spells_countered_against")
            self.bump()

    def _uncounterable(self, item: StackItem) -> bool:
        """'Spells you control can't be countered' statics (e.g. Chimil)."""
        for obj in item.controller.battlefield:
            for ab in obj.chars(self).abilities:
                if getattr(ab, "uncounterable_spells", False):
                    return True
        return False

    @rule("707.10", "707.10a", "704.5e")
    def copy_spell(self, item: StackItem, controller) -> StackItem | None:
        """Put a copy of a spell on the stack (rule 707.10). The copy keeps
        the original's targets and X; it is created as a token object so
        it ceases to exist in any zone but the stack (704.5e / 707.10a) and
        resolves to a token if it is a permanent spell."""
        if item not in self.stack or not item.is_spell:
            return None
        copy_obj = GameObject(item.obj.base.copy(), controller,
                              is_token=True, card_ref=item.obj.card_ref)
        copy_obj.is_copy = True
        copy_obj.zone = Zone.STACK
        copy_obj.controller = controller
        copy = StackItem(obj=copy_obj, source=copy_obj,
                         controller=controller, ability=item.ability,
                         targets=list(item.targets), x=item.x,
                         is_spell=True)
        self.stack.append(copy)
        controller.stat("spells_copied")
        self.log("copy", spell=_lname(item), who=controller.name)
        self.bump()
        return copy

    # ------------------------------------------------------------ mana
    @rule("605.1a", "605.3")
    def mana_sources(self, player):
        """Untapped permanents with activatable mana abilities."""
        out = []
        for obj in player.battlefield:
            if obj.tapped:
                continue
            ch = obj.chars(self)
            summoning_sick = ("Creature" in ch.types and obj.entered_this_turn
                              and "haste" not in ch.keywords)
            for ab in ch.abilities:
                if isinstance(ab, ActivatedAbility) and ab.is_mana_ability:
                    if ab.tap_cost and summoning_sick:
                        continue                    # rule 302.6
                    out.append((obj, ab))
                    break
        return out

    def mana_colors_of(self, obj, ab) -> frozenset:
        eff = ab.effect
        from .effects import AddMana, Sequence
        adds = ([eff] if isinstance(eff, AddMana) else
                [e for e in getattr(eff, "parts", []) if isinstance(e, AddMana)])
        colors = set()
        for a in adds:
            if a.any_color:
                colors |= set("WUBRG")
            elif a.commander_identity:
                colors |= self.commander_identity(obj.controller) or {"C"}
            colors |= set(a.types)
        return frozenset(colors - {"ANY"} or {"C"})

    @rule("601.2g", "601.2h")
    def can_pay_mana(self, player, cost) -> bool:
        return self._solve_mana(player, cost, commit=False)

    def pay_mana(self, player, cost) -> bool:
        return self._solve_mana(player, cost, commit=True)

    def _solve_mana(self, player, cost, commit) -> bool:
        """Greedy scarcity-first assignment of mana sources to pips, on top
        of whatever is already in the pool."""
        pool = dict(player.mana_pool.mana)
        avail = []
        for obj, ab in self.mana_sources(player):
            colors = self.mana_colors_of(obj, ab)
            amount = self._mana_amount(ab)
            avail.append((obj, ab, colors, amount))
        used = []

        def take(pred):
            best = None
            for i, (obj, ab, colors, amount) in enumerate(avail):
                if pred(colors):
                    if best is None or len(colors) < len(avail[best][2]):
                        best = i
            if best is None:
                return None
            return avail.pop(best)

        need_pips = []
        for color, n in cost.pips.items():
            need_pips += [color] * n
        need_pips += ["C"] * cost.colorless
        for color in need_pips:
            if pool.get(color, 0) > 0:
                pool[color] -= 1
                continue
            got = take(lambda cs, c=color: c in cs)
            if got is None:
                return False
            used.append((got[0], got[1], color))
            if len(got[2]) == 1 and got[3] > 1:
                # single-type multi-mana source (Sol Ring): surplus floats
                pool[color] = pool.get(color, 0) + got[3] - 1
        for opts in sorted(cost.hybrid, key=len):
            hit = None
            for c in opts:
                if pool.get(c, 0) > 0:
                    pool[c] -= 1
                    hit = True
                    break
            if hit:
                continue
            got = take(lambda cs: cs & opts)
            if got is None:
                return False
            color = next(iter(got[2] & opts))
            used.append((got[0], got[1], color))
        need = cost.generic
        for t in sorted(pool, key=lambda t: (t != "C", -pool[t])):
            take_n = min(need, pool[t])
            pool[t] -= take_n
            need -= take_n
        avail.sort(key=lambda e: (-e[3], len(e[2])))
        while need > 0 and avail:
            obj, ab, colors, amount = avail.pop(0)
            used.append((obj, ab, "C" if "C" in colors
                         else next(iter(colors))))
            need -= amount
        if need > 0:
            return False
        if commit:
            for obj, ab, color in used:
                self._activate_mana_ability(obj, ab, color)
            player.mana_pool.pay(cost)
        return True

    @staticmethod
    def _mana_amount(ab) -> int:
        from .effects import AddMana
        eff = ab.effect
        if isinstance(eff, AddMana):
            return max(1, len(eff.types)) if not (
                eff.any_color or eff.commander_identity) else 1
        return 1

    @rule("605.3b")
    def _activate_mana_ability(self, obj, ab, color):
        """Mana abilities resolve immediately, no stack (rule 605.3b)."""
        player = obj.controller
        if ab.tap_cost:
            self.tap(obj)
        if ab.sac_cost == "self":
            self.sacrifice(player, obj)
        from .effects import AddMana
        eff = ab.effect
        if isinstance(eff, AddMana) and not eff.any_color \
                and not eff.commander_identity and eff.types:
            for t in eff.types:
                player.mana_pool.add(t)
        else:
            player.mana_pool.add(color)

    # ------------------------------------------------------------ casting
    @rule("601.2", "601.2a", "601.2b", "601.2c", "601.2f",
          "601.2h", "601.2i", "903.8")
    def cast_spell(self, player, card: GameObject, *, x=0,
                   from_command=False):
        ch = card.base
        cost = parse_cost(ch.mana_cost).with_x(x)
        if card.commander:
            # rule 903.8: commander tax
            cost = cost.with_extra_generic(2 * player.commander_casts)
        # rule 601.2f cost-reduction statics on the spell itself
        # (e.g. "costs {1} less to cast for each creature")
        per_creature = getattr(ch, "cost_less_per_creature", 0)
        if per_creature:
            n = sum(1 for o in self.battlefield_objects()
                    if "Creature" in o.chars(self).types)
            cost = cost.reduced(per_creature * n)
        # rule 601.2b additional costs: verify they can be paid up front
        extra = getattr(ch, "additional_cost", "")
        extra_sac = None
        if extra == "sacrifice_creature":
            extra_sac = self.policy(player).choose_sacrifice(
                self, player, "creature")
            if extra_sac is None:
                return False
        elif extra == "discard_card":
            if not [c for c in player.hand if c is not card]:
                return False
        spell_ability = next(
            (a for a in ch.abilities if isinstance(a, SpellAbility)), None)
        targets = None
        t_specs = spell_ability.targets if spell_ability else []
        item = StackItem(obj=card, source=card, controller=player,
                         ability=spell_ability, x=x, is_spell=True)
        if t_specs:
            targets = self._choose_targets(player, spell_ability, card, x=x)
            if targets is None:
                return False                       # rule 601.2c: no targets
            item.targets = targets
        if not self.pay_mana(player, cost):
            return False                           # rule 601.2h
        # rule 601.2h: pay additional costs along with mana
        if extra_sac is not None:
            self.sacrifice(player, extra_sac)
        elif extra == "discard_card":
            pool = [c for c in player.hand if c is not card]
            pick = min(pool, key=lambda c: parse_cost(c.base.mana_cost).mv)
            self.move_zone(pick, Zone.GRAVEYARD)
            self.log("discard", who=player.name, card=pick.base.name)
        self._remove_from_zone(card, card.zone)
        card.zone = Zone.STACK
        self.stack.append(item)
        if card.commander and from_command:
            player.commander_casts += 1
        player.stat("spells_cast")
        player.cards_cast.append(card.base.name)
        self.log("cast", who=player.name, card=card.base.name,
                 x=x or None, commander=from_command or None)
        ref = card.card_ref
        if ref is not None:
            if ref.behavior.get("wipe"):
                player.stat("wipes_cast")
            elif ref.behavior.get("removal"):
                player.stat("removal_used")
        # rule 702.21 ward: cost tax handled as a trigger when targeted
        self._ward_check(item)
        self._queue_triggers(Event(EventType.CAST, {"obj": card,
                                                    "player": player}))
        self.bump()
        return True

    @rule("702.21")
    def _ward_check(self, item: StackItem):
        """Ward N: counter the targeting spell unless its controller pays
        (policy decides; paying requires available mana)."""
        for t in item.targets or []:
            if isinstance(t, GameObject) and t.zone == Zone.BATTLEFIELD \
                    and t.controller is not item.controller:
                ward = next((k for k in t.chars(self).keywords
                             if k.startswith("ward")), None)
                if not ward:
                    continue
                n = int(ward.split(":")[1]) if ":" in ward else 2
                cost = parse_cost("{%d}" % n)
                if self.can_pay_mana(item.controller, cost) and \
                        self.policy(item.controller).pay_ward(self, item, n):
                    self.pay_mana(item.controller, cost)
                else:
                    self.counter_spell(item)
                    return

    @rule("602.2", "602.5a", "606.3")
    def activate_ability(self, player, obj, ab: ActivatedAbility, *, x=0):
        if ab.from_hand:
            return self._activate_from_hand(player, obj, ab)
        ch = obj.chars(self)
        if ab.loyalty_cost is not None:
            # rule 606.3: one loyalty ability per permanent per turn,
            # only during a main phase with an empty stack
            if ("loyalty", obj.id) in self.activated_this_turn:
                return False
            if (player is not self.active_player or self.stack
                    or self.phase not in ("main1", "main2")):
                return False
        if ab.sorcery_only and (player is not self.active_player
                                or self.stack
                                or self.phase not in ("main1", "main2")):
            return False                           # rule 602.5d
        if ab.once_per_turn \
                and (obj.id, id(ab)) in self.activated_this_turn:
            return False
        if ab.tap_cost:
            if obj.tapped:
                return False
            if "Creature" in ch.types and obj.entered_this_turn \
                    and "haste" not in ch.keywords:
                return False                       # rule 302.6
        cost = ab.cost.with_x(x)
        # rule 601.2c (via 602.2b): choose targets before determining and
        # paying costs
        if ab.loyalty_cost is not None and ab.loyalty_cost < 0 \
                and obj.counters.get("loyalty", 0) < -ab.loyalty_cost:
            return False
        if ab.life_cost and player.life <= ab.life_cost:
            return False
        sac_pick = None
        if ab.sac_cost and ab.sac_cost != "self":
            sac_pick = self.policy(player).choose_sacrifice(
                self, player, ab.sac_cost, exclude=obj)
            if sac_pick is None:
                return False
        targets = self._choose_targets(player, ab, obj, x=x)
        if targets is None and ab.targets:
            return False
        # rules 601.2f-h: determine and pay costs
        if not self.pay_mana(player, cost):
            return False
        if ab.tap_cost:
            self.tap(obj)
        if ab.life_cost:
            self.lose_life(player, ab.life_cost)
        if ab.sac_cost == "self":
            self.sacrifice(player, obj)
        elif sac_pick is not None:
            self.sacrifice(player, sac_pick)
        if ab.loyalty_cost is not None:
            if ab.loyalty_cost >= 0:
                self.put_counters(obj, "loyalty", ab.loyalty_cost)
            else:
                self.remove_counters(obj, "loyalty", -ab.loyalty_cost)
        self.stack.append(StackItem(obj=ab, source=obj, controller=player,
                                    ability=ab, targets=targets or [], x=x))
        if ab.loyalty_cost is not None:
            self.activated_this_turn.add(("loyalty", obj.id))
        if ab.once_per_turn:
            self.activated_this_turn.add((obj.id, id(ab)))
        player.stat("abilities_activated")
        self.log("activate", who=player.name, source=_lname(obj))
        self.bump()
        return True

    @rule("702.29a")
    def _activate_from_hand(self, player, obj, ab) -> bool:
        """Cycling-style abilities: activated while the card is in the
        hand; discarding the card is part of the cost (rule 702.29a)."""
        if obj not in player.hand:
            return False
        if not self.pay_mana(player, ab.cost):
            return False
        self.move_zone(obj, Zone.GRAVEYARD)        # discard as a cost
        self.stack.append(StackItem(obj=ab, source=obj, controller=player,
                                    ability=ab, targets=[]))
        player.stat("cards_cycled")
        self.log("cycle", who=player.name, card=obj.base.name)
        self.bump()
        return True

    @rule("115.3", "601.2c")
    def _choose_targets(self, player, ability, source, x=0):
        targets = []
        ctx = Ctx(controller=player, source=source, x=x)
        for spec in ability.targets:
            legal = self.legal_targets(spec, ctx)
            if not legal:
                if spec.optional:
                    continue
                return None
            pick = self.policy(player).choose_target(self, spec, legal, ctx,
                                                     ability)
            if pick is None:
                if spec.optional:
                    continue
                return None
            targets.append(pick)
        return targets

    def legal_targets(self, spec, ctx):
        out = []
        if spec.what in ("player", "opponent"):
            out = [p for p in self.alive() if spec.legal(self, ctx, p)]
        elif spec.what == "spell":
            out = [i for i in self.stack if i.is_spell]
        else:
            out = [o for o in self.battlefield_objects()
                   if spec.legal(self, ctx, o)]
        return out

    def still_legal_target(self, target, ctx, index) -> bool:
        """Rule 608.2b: re-check target legality on resolution."""
        return True if not isinstance(target, GameObject) \
            else target.zone == Zone.BATTLEFIELD

    # ------------------------------------------------------------ stack
    @rule("608.2", "608.2b", "608.3", "608.2m")
    def resolve_top(self):
        item = self.stack.pop()
        if self._resolve_item(item):
            # logged on completion so observers snapshot the applied state
            self.log("resolve", what=_lname(item))

    def _resolve_item(self, item) -> bool:
        ctx = Ctx(controller=item.controller, source=item.source,
                  targets=item.targets, x=item.x)
        if isinstance(item.obj, PendingTrigger):
            ctx.event_obj = item.obj.event.data.get("obj")
        if item.targets:
            legal = [t for t in item.targets
                     if not isinstance(t, GameObject)
                     or t.zone == Zone.BATTLEFIELD]
            if not legal and not any(isinstance(t, Player)
                                     for t in item.targets):
                # rule 608.2b: all targets illegal -> doesn't resolve
                if item.is_spell:
                    self.move_zone(item.obj, Zone.GRAVEYARD)
                self.log("fizzle", what=_lname(item))
                return False
        if item.is_spell:
            card = item.obj
            ch = card.base
            if ch.types & {"Instant", "Sorcery"}:
                if item.ability is not None:
                    item.ability.effect.resolve(self, ctx)
                self.move_zone(card, Zone.GRAVEYARD)   # rule 608.2m
            else:
                # rule 608.3: permanent spell resolves to the battlefield
                counters = {}
                if "Planeswalker" in ch.types and ch.loyalty:
                    counters["loyalty"] = ch.loyalty   # rule 306.5b
                kind = getattr(ch, "etb_x_counters", None)
                if kind and item.x:
                    counters[kind] = item.x
                moved = self.move_zone(card, Zone.BATTLEFIELD,
                                       counters=counters)
                # rule 303.4: an Aura enters attached to its target
                if moved is not None and "Aura" in ch.subtypes \
                        and item.targets:
                    t = item.targets[0]
                    if isinstance(t, GameObject) \
                            and t.zone == Zone.BATTLEFIELD:
                        self.attach(moved, t)
                    else:
                        self.move_zone(moved, Zone.GRAVEYARD)
        else:
            ability = item.ability
            if isinstance(ability, TriggeredAbility) \
                    and ability.intervening_if \
                    and not ability.intervening_if(self, item.source):
                return False                        # rule 603.4 recheck
            if ability is not None and ability.effect is not None:
                ability.effect.resolve(self, ctx)
        self.bump()
        return True

    # ------------------------------------------------------ state-based
    @rule("704.3", "704.5", "704.5a", "704.5b", "704.5c", "704.5d",
          "704.5f", "704.5g", "704.5h", "704.5i", "704.5j",
          "704.5m", "704.5n", "704.5q", "704.6c", "702.12b")
    def check_state_based_actions(self) -> bool:
        """Perform all applicable SBAs; return True if any happened."""
        acted = False
        for p in self.alive():
            if p.life <= 0:                        # rule 704.5a
                self._lose(p, "life 0 or less (704.5a)")
                acted = True
            elif p.drew_from_empty:                # rule 704.5b
                self._lose(p, "drew from empty library (704.5b)")
                acted = True
            elif p.poison >= 10:                   # rule 704.5c
                self._lose(p, "ten or more poison counters (704.5c)")
                acted = True
            else:
                for cid, dmg in p.commander_damage.items():
                    if dmg >= 21:                  # rules 903.10a / 704.6c
                        self._lose(p, "21+ commander damage (903.10a)")
                        acted = True
                        break

        for obj in list(self.battlefield_objects()):
            ch = obj.chars(self)
            if "Creature" in ch.types:
                tough = ch.toughness or 0
                if tough <= 0:                     # rule 704.5f
                    self.move_zone(obj, Zone.GRAVEYARD)
                    acted = True
                    continue
                # rules 704.5g/h destroy; indestructible (702.12b) makes
                # the destruction do nothing, so it is not an action
                destructible = "indestructible" not in ch.keywords
                if obj.damage >= tough and destructible:   # rule 704.5g
                    self.destroy(obj)
                    acted = True
                    continue
                if obj.deathtouch_damage and obj.damage > 0 \
                        and destructible:                  # rule 704.5h
                    self.destroy(obj)
                    acted = True
                    continue
            if "Planeswalker" in ch.types \
                    and obj.counters.get("loyalty", 0) <= 0:  # rule 704.5i
                self.move_zone(obj, Zone.GRAVEYARD)
                acted = True
                continue
            # rule 704.5q: +1/+1 and -1/-1 counters annihilate
            plus, minus = obj.counters.get("+1/+1", 0), \
                obj.counters.get("-1/-1", 0)
            if plus and minus:
                n = min(plus, minus)
                self.remove_counters(obj, "+1/+1", n)
                self.remove_counters(obj, "-1/-1", n)
                acted = True

        # rules 704.5m / 704.5n: auras and equipment attachment checks
        for obj in list(self.battlefield_objects()):
            ch = obj.chars(self)
            if "Aura" in ch.subtypes and (
                    obj.attached_to is None
                    or obj.attached_to.zone != Zone.BATTLEFIELD):
                self.move_zone(obj, Zone.GRAVEYARD)     # rule 704.5m
                acted = True
            elif "Equipment" in ch.subtypes and obj.attached_to is not None \
                    and obj.attached_to.zone != Zone.BATTLEFIELD:
                obj.attached_to = None                  # rule 704.5n
                acted = True

        # rule 704.5j: legend rule
        for p in self.alive():
            named = {}
            for obj in p.battlefield:
                ch = obj.chars(self)
                if "Legendary" in ch.supertypes and ch.name:
                    named.setdefault(ch.name, []).append(obj)
            for name, objs in named.items():
                if len(objs) > 1:
                    keep = self.policy(p).choose_legend(self, objs)
                    for o in objs:
                        if o is not keep:
                            self.move_zone(o, Zone.GRAVEYARD)
                    acted = True
        return acted

    def _lose(self, player, reason):
        if player.lost:
            return
        player.lost = True
        player.lose_reason = reason
        self.log("player_loses", who=player.name, why=reason)
        # rule 104.2a: sole survivor wins
        alive = self.alive()
        if len(alive) == 1:
            self.winner = alive[0]
            self.game_over = True
        elif not alive:
            self.game_over = True

    # ------------------------------------------------------------ priority
    @rule("117.3", "117.4", "117.5", "405.5", "704.3")
    def priority_loop(self):
        """Give priority around the table until all players pass in
        succession; resolve the stack as needed (rules 117.4, 405.5)."""
        while True:
            # rule 704.3: SBAs + triggers before a player gets priority
            while True:
                acted = self.check_state_based_actions()
                placed = self.put_triggers_on_stack()
                if self.game_over:
                    return
                if not acted and not placed:
                    break
            passes = 0
            order = self.players_apnap()
            i = 0
            while passes < len(order):
                player = order[i % len(order)]
                i += 1
                if player.lost:
                    passes += 1
                    continue
                action = self.policy(player).choose_action(self, player)
                if action is None:
                    passes += 1
                    continue
                if self._perform(player, action):
                    passes = 0
                    # re-run SBAs/triggers between actions
                    while True:
                        acted = self.check_state_based_actions()
                        placed = self.put_triggers_on_stack()
                        if self.game_over:
                            return
                        if not acted and not placed:
                            break
                else:
                    passes += 1
            if self.stack:
                self.resolve_top()                 # rule 117.4
                if self.game_over:
                    return
                continue
            return                                 # step/phase ends

    def _perform(self, player, action) -> bool:
        kind = action[0]
        if kind == "cast":
            _, card, kwargs = action
            return self.cast_spell(player, card, **kwargs)
        if kind == "activate":
            _, obj, ab, kwargs = action
            return self.activate_ability(player, obj, ab, **kwargs)
        if kind == "land":
            return self.play_land(player, action[1])
        return False

    # ------------------------------------------------------------ lands
    @rule("116.2a", "305.1")
    def play_land(self, player, card) -> bool:
        """Playing a land is a special action, no stack (rule 116.2a)."""
        if player.lands_played >= 1:               # rule 305.2
            return False
        if player is not self.active_player or self.stack:
            return False
        if card not in player.hand:
            return False
        player.lands_played += 1
        tapped = any(getattr(ab, "enters_tapped", False)
                     for ab in card.base.abilities)
        self.move_zone(card, Zone.BATTLEFIELD, to_battlefield_tapped=tapped)
        self._queue_triggers(Event(EventType.LAND_PLAYED,
                                   {"obj": card, "player": player}))
        player.stat("lands_played")
        self.log("land", who=player.name, card=card.base.name,
                 tapped=tapped or None)
        return True

    def add_floating_effect(self, effect: ContinuousEffect):
        self.layers.add_floating(effect)

    @rule("303.4", "301.5")
    def attach(self, obj, target):
        if obj.attached_to is not None \
                and obj in obj.attached_to.attachments:
            obj.attached_to.attachments.remove(obj)
        obj.attached_to = target
        target.attachments.append(obj)
        self.bump()


from .effects import AddMana  # noqa: E402  (cycle-free tail import)

_ADD_ANY_MANA = AddMana(any_color=True)
