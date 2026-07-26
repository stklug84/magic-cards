"""The game: zones, events, the stack, priority, and state-based actions.

Implements the CR machinery that the heuristic simulator lacks: a real
stack with priority passing (rules 117, 405, 601-608), state-based actions
(rule 704), the trigger queue (rule 603), and the Commander variant rules
(rule 903). Turn structure and combat live in turns.py / combat.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict, cast

from mtgrules.abilities import (
    ActivatedAbility,
    SpellAbility,
    StaticAbility,
    TargetSpec,
    TokenSpec,
    TriggeredAbility,
)
from mtgrules.cr import rule, unsupported
from mtgrules.effects import AddMana, Ctx
from mtgrules.events import Event, EventType
from mtgrules.layers import LayerSystem
from mtgrules.manasys import ManaPool, parse_cost
from mtgrules.objects import Characteristics, GameObject, Player, Zone
from mtgrules.replacements import ReplacementEngine
from mtgrules.stack import PendingTrigger, StackItem

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Iterator
    from typing import Literal

    from mtgrules.layers import ContinuousEffect
    from mtgrules.manasys import Cost
    from mtgrules.policy import DefaultPolicy
    from mtgrules.protocols import LogFn
    from mtgrules.stack import Target

unsupported(
    "903.4",
    "deck color-identity legality is validated by the "
    "knowledge-graph tooling, not at runtime",
)

#: rule 704.5c: a player with ten or more poison counters loses
POISON_LOSS_THRESHOLD = 10
#: rules 903.10a / 704.6c: 21+ combat damage from one commander loses
COMMANDER_DAMAGE_LOSS = 21

_ADD_ANY_MANA = AddMana(any_color=True)


class CastOptions(TypedDict, total=False):
    """Keyword options of a ('cast', card, options) player action."""

    x: int
    from_command: bool


class ActivateOptions(TypedDict, total=False):
    """Keyword options of an ('activate', obj, ability, options) action."""

    x: int


#: the three legal player actions a policy can choose during priority
type CastAction = tuple[Literal["cast"], GameObject, CastOptions]
type ActivateAction = tuple[
    Literal["activate"],
    GameObject,
    ActivatedAbility,
    ActivateOptions,
]
type LandAction = tuple[Literal["land"], GameObject]
type Action = CastAction | ActivateAction | LandAction

#: an ability that can sit on the stack and resolve
type ResolvableAbility = SpellAbility | ActivatedAbility | TriggeredAbility


def _lname(x: object) -> str:
    """Loggable name for a Player, GameObject, or stack item."""
    if isinstance(x, Player):
        return x.name
    if isinstance(x, GameObject):
        return x.base.name or "(unnamed)"
    if isinstance(x, StackItem):
        if x.is_spell:
            return cast("GameObject", x.obj).base.name
        return f"ability of {x.source.base.name}"
    return str(x)


@dataclass
class _ManaPlan:
    """Working state of one greedy mana-payment search (rule 601.2g)."""

    pool: dict[str, int]
    #: untapped sources: (permanent, mana ability, producible colors, amount)
    avail: list[tuple[GameObject, ActivatedAbility, frozenset[str], int]]
    #: sources committed so far, with the mana type each will produce
    used: list[tuple[GameObject, ActivatedAbility, str]] = field(default_factory=list)

    def take(
        self,
        pred: Callable[[frozenset[str]], object],
    ) -> tuple[GameObject, ActivatedAbility, frozenset[str], int] | None:
        """Pop the matching source with the fewest producible colors."""
        best = None
        for i, (_obj, _ab, colors, _amount) in enumerate(self.avail):
            if pred(colors) and (
                best is None or len(colors) < len(self.avail[best][2])
            ):
                best = i
        if best is None:
            return None
        return self.avail.pop(best)

    def cover_pips(self, cost: Cost) -> bool:
        """Cover the colored and {C} pips of *cost* (rule 601.2g)."""
        need_pips: list[str] = []
        for color, n in cost.pips.items():
            need_pips += [color] * n
        need_pips += ["C"] * cost.colorless
        for color in need_pips:
            if self.pool.get(color, 0) > 0:
                self.pool[color] -= 1
                continue

            def has_color(cs: frozenset[str], c: str = color) -> bool:
                return c in cs

            got = self.take(has_color)
            if got is None:
                return False
            self.used.append((got[0], got[1], color))
            if len(got[2]) == 1 and got[3] > 1:
                # single-type multi-mana source (Sol Ring): surplus floats
                self.pool[color] = self.pool.get(color, 0) + got[3] - 1
        return True

    def cover_hybrid(self, cost: Cost) -> bool:
        """Cover the hybrid pips of *cost*, scarcest option first."""
        for opts in sorted(cost.hybrid, key=len):
            hit = False
            for c in opts:
                if self.pool.get(c, 0) > 0:
                    self.pool[c] -= 1
                    hit = True
                    break
            if hit:
                continue

            def overlaps(
                cs: frozenset[str],
                wanted: frozenset[str] = opts,
            ) -> frozenset[str]:
                return cs & wanted

            got = self.take(overlaps)
            if got is None:
                return False
            color = next(iter(got[2] & opts))
            self.used.append((got[0], got[1], color))
        return True

    def cover_generic(self, cost: Cost) -> bool:
        """Cover the generic part from floating mana, then sources."""
        need = cost.generic
        for t in sorted(self.pool, key=lambda t: (t != "C", -self.pool[t])):
            take_n = min(need, self.pool[t])
            self.pool[t] -= take_n
            need -= take_n
        self.avail.sort(key=lambda e: (-e[3], len(e[2])))
        while need > 0 and self.avail:
            obj, ab, colors, amount = self.avail.pop(0)
            self.used.append((obj, ab, "C" if "C" in colors else next(iter(colors))))
            need -= amount
        return need <= 0


class Game:
    """One game: players, zones, the stack, and the CR machinery."""

    def __init__(
        self,
        players: list[Player],
        rng: random.Random,
        policies: dict[str, DefaultPolicy],
        turn_cap: int = 40,
        log: LogFn | None = None,
    ) -> None:
        """Wire up the layer/replacement engines and fresh mana pools."""
        self.players = players
        self.rng = rng
        self.policies = policies
        self.turn_cap = turn_cap
        self.turn = 0
        self.active_idx = 0
        self.phase = "main1"
        #: (obj id, ability index) activated this turn (once-per-turn and
        #: loyalty tracking, rule 606.3)
        self.activated_this_turn: set[tuple[object, ...]] = set()
        self.stack: list[StackItem] = []
        self.pending_triggers: list[PendingTrigger] = []
        self.tick = 0
        self.layers = LayerSystem(self)
        self.replacements = ReplacementEngine(self)
        self.log: LogFn = log or (lambda *_args, **_kw: None)
        self.game_over = False
        self.winner: Player | None = None
        self.unknown_clauses: dict[str, set[str]] = {}
        #: scratch state owned by hand-written card implementations
        self.custom: dict[str, Any] = {}
        for p in players:
            p.mana_pool = ManaPool()

    # ------------------------------------------------------------ helpers
    def bump(self) -> None:
        """Invalidate the layer-system cache (state changed)."""
        self.tick += 1

    @property
    def active_player(self) -> Player:
        """The player whose turn it is (rule 102.1)."""
        return self.players[self.active_idx]

    @rule("102.2", "102.3")
    def opponents(self, player: Player) -> list[Player]:
        """Return the surviving opponents of *player* (rule 102.2)."""
        return [p for p in self.players if p is not player and not p.lost]

    def alive(self) -> list[Player]:
        """All players still in the game."""
        return [p for p in self.players if not p.lost]

    @rule("101.4")
    def players_apnap(self) -> list[Player]:
        """Active player, nonactive players in turn order (rule 101.4)."""
        n = len(self.players)
        order = [self.players[(self.active_idx + i) % n] for i in range(n)]
        return [p for p in order if not p.lost]

    def policy(self, player: Player) -> DefaultPolicy:
        """Return the decision policy that plays for *player*."""
        return self.policies[player.name]

    def battlefield_objects(self) -> Iterator[GameObject]:
        """Every permanent on any player's battlefield."""
        for p in self.players:
            yield from p.battlefield

    def commander_identity(self, player: Player) -> set[str]:
        """Return the commander's color identity (rule 903.4)."""
        obj = player.commander_obj
        if obj is None or obj.card_ref is None:
            return set("WUBRG")
        return set(obj.card_ref.color_identity)

    @rule("702.11")
    def cant_be_targeted(self, obj: GameObject, ctx: Ctx) -> bool:
        """Hexproof (rule 702.11): can't be targeted by opponents."""
        ch = obj.chars(self)
        return "hexproof" in ch.keywords and ctx.controller is not obj.controller

    # ------------------------------------------------------------ events
    def emit(self, event: Event) -> Event | None:
        """Route an event through replacement effects (rule 614).

        Returns the final event, or None if it was prevented.
        """
        return self.replacements.process(event)

    @rule("603.2", "603.3")
    def queue_triggers(self, event: Event) -> None:
        """Collect triggered abilities that trigger off *event*."""
        seen: set[tuple[int, int]] = set()
        for source, ab in self._trigger_watchers(event):
            key = (source.id, id(ab))
            if key in seen:
                continue
            seen.add(key)
            if ab.trigger.matches(self, source, event):
                if ab.intervening_if and not ab.intervening_if(self, source):
                    continue  # rule 603.4
                self.pending_triggers.append(
                    PendingTrigger(ab, source, source.controller, event),
                )

    @rule("603.6b")
    def _trigger_watchers(
        self,
        event: Event,
    ) -> list[tuple[GameObject, TriggeredAbility]]:
        """All (source, ability) pairs that watch for *event*."""
        watchers = [
            (obj, ab)
            for obj in self.battlefield_objects()
            for ab in obj.chars(self).abilities
            if isinstance(ab, TriggeredAbility)
        ]
        # rule 603.6b-d: leave-the-battlefield / dies triggers of the
        # departing object itself look back in time
        obj = event.data.get("obj")
        if isinstance(obj, GameObject) and obj.zone != Zone.BATTLEFIELD:
            watchers.extend(
                (obj, ab)
                for ab in obj.base.abilities
                if isinstance(ab, TriggeredAbility)
            )
        return watchers

    @rule("603.3b")
    def put_triggers_on_stack(self) -> bool:
        """APNAP order; each player orders their own triggers."""
        if not self.pending_triggers:
            return False
        by_player: dict[Player, list[PendingTrigger]] = {}
        for t in self.pending_triggers:
            by_player.setdefault(t.controller, []).append(t)
        self.pending_triggers = []
        for p in self.players_apnap():
            mine = by_player.get(p, [])
            if len(mine) > 1:
                mine = self.policy(p).order_triggers(self, mine)
            for t in mine:
                if t.ability.once_each_turn:
                    key = ("trig", t.source.id, id(t.ability))
                    if key in self.activated_this_turn:
                        continue  # "only once each turn"
                    self.activated_this_turn.add(key)
                if t.ability.optional and not self.policy(p).accept_optional(self, t):
                    continue
                targets = self._choose_targets(p, t.ability, t.source)
                if targets is None and t.ability.targets:
                    continue  # no legal targets: fizzle
                self.stack.append(
                    StackItem(
                        obj=t,
                        source=t.source,
                        controller=p,
                        ability=t.ability,
                        targets=targets or [],
                    ),
                )
                self.log("trigger", who=p.name, what=t.source.base.name)
        return True

    # ------------------------------------------------------------ zones
    @rule("400.1", "400.7", "903.9a", "704.5d")
    def move_zone(
        self,
        obj: GameObject,
        to_zone: str,
        *,
        to_battlefield_tapped: bool = False,
        counters: dict[str, int] | None = None,
        pos: str = "top",
    ) -> GameObject | None:
        """Move *obj* between zones through the replacement machinery.

        Returns the object, or None when the move was prevented or the
        object ceased to exist (tokens, rule 704.5d).
        """
        from_zone = obj.zone
        event = self.emit(
            Event(
                EventType.ZONE_CHANGE,
                {
                    "obj": obj,
                    "from": from_zone,
                    "to": to_zone,
                    "tapped": to_battlefield_tapped,
                    "counters": dict(counters or {}),
                    "controller": obj.controller,
                },
            ),
        )
        if event is None:
            return None
        to_zone = self._commander_redirect(obj, event.data["to"])
        self._remove_from_zone(obj, from_zone)

        # rule 704.5d: a token anywhere but the battlefield ceases to exist
        if obj.is_token and to_zone != Zone.BATTLEFIELD:
            self._token_ceases(obj, from_zone, to_zone, event)
            return None

        obj.zone = to_zone
        self._place_in_zone(obj, to_zone, pos)

        if Zone.BATTLEFIELD in (from_zone, to_zone):
            # last-known-information for leave-the-battlefield triggers
            # (rule 603.10a: they use the object's last existence)
            obj.lki_counters = dict(obj.counters)
            for att in list(obj.attachments):
                att.attached_to = None
            if obj.attached_to is not None and obj in obj.attached_to.attachments:
                obj.attached_to.attachments.remove(obj)
            obj.reset_battlefield_state()  # rule 400.7
        if to_zone == Zone.BATTLEFIELD:
            obj.entered_this_turn = True
            obj.tapped = bool(event.data.get("tapped"))
            for kind, n in event.data.get("counters", {}).items():
                obj.counters[kind] = obj.counters.get(kind, 0) + n
            self.bump()
            etb = Event(EventType.ENTERS_BATTLEFIELD, {"obj": obj})
            self.queue_triggers(etb)
        elif from_zone == Zone.BATTLEFIELD:
            self.bump()
            if to_zone == Zone.GRAVEYARD:
                self.log("dies", who=obj.controller.name, card=_lname(obj))
            self._fire_leave_battlefield(obj, event)
        else:
            self.bump()
        return obj

    @rule("903.9a")
    def _commander_redirect(self, obj: GameObject, to_zone: str) -> str:
        """Rule 903.9a-b: the owner may send a commander to the command zone."""
        if (
            obj.commander
            and to_zone in (Zone.GRAVEYARD, Zone.EXILE, Zone.HAND, Zone.LIBRARY)
            and self.policy(obj.owner).commander_to_command_zone(self, obj, to_zone)
        ):
            return Zone.COMMAND
        return to_zone

    @rule("704.5d")
    def _token_ceases(
        self,
        obj: GameObject,
        from_zone: str,
        to_zone: str,
        event: Event,
    ) -> None:
        """Cease a token that left the battlefield (rule 704.5d)."""
        obj.zone = "ceased"
        self.bump()
        if from_zone == Zone.BATTLEFIELD and to_zone == Zone.GRAVEYARD:
            obj.controller.stat("tokens_killed")
            self.log("dies", who=obj.controller.name, card=_lname(obj), token=True)
            self._fire_leave_battlefield(obj, event)

    def _place_in_zone(self, obj: GameObject, to_zone: str, pos: str) -> None:
        """Append *obj* to the destination zone's list."""
        holder = obj.controller if to_zone == Zone.BATTLEFIELD else obj.owner
        if to_zone == Zone.LIBRARY:
            if pos == "bottom":
                holder.library.append(obj)
            else:
                holder.library.insert(0, obj)
        else:
            holder.zone_list(to_zone).append(obj)

    def _fire_leave_battlefield(self, obj: GameObject, zone_event: Event) -> None:
        """Queue dies triggers when the destination was a graveyard."""
        if zone_event.data["to"] == Zone.GRAVEYARD:
            self.queue_triggers(Event(EventType.DIES, {"obj": obj}))

    def _remove_from_zone(self, obj: GameObject, zone: str) -> None:
        """Remove *obj* from whichever player's list holds it."""
        if zone == "ceased":
            return
        for p in self.players:
            lst = p.zone_list(zone) if zone != Zone.STACK else None
            if lst is not None and obj in lst:
                lst.remove(obj)
                return

    # ------------------------------------------------------------ actions
    @rule("111.2", "111.3")
    def create_tokens(
        self,
        controller: Player,
        spec: TokenSpec,
        count: int,
        *,
        source: GameObject | None = None,
        tapped: bool | None = None,
    ) -> list[GameObject]:
        """Create *count* tokens from *spec* (rule 111.2)."""
        event = self.emit(
            Event(
                EventType.CREATE_TOKEN,
                {
                    "spec": spec,
                    "count": count,
                    "controller": controller,
                    "source": source,
                },
            ),
        )
        if event is None or event.data["count"] <= 0:
            return []
        made: list[GameObject] = []
        specs: list[tuple[TokenSpec, int]] = [
            (event.data["spec"], event.data["count"]),
        ]
        specs.extend(
            (extra, event.data["count"]) for extra in event.data.get("extra_specs", [])
        )
        made.extend(
            self._make_token(controller, tok_spec, tapped=tapped)
            for tok_spec, tok_count in specs
            for _ in range(tok_count)
        )
        return made

    def _make_token(
        self,
        controller: Player,
        tok_spec: TokenSpec,
        *,
        tapped: bool | None,
    ) -> GameObject:
        """Build one token object and put it onto the battlefield."""
        base = Characteristics(
            name=tok_spec.name,
            colors=set(tok_spec.colors),
            types=set(tok_spec.types),
            subtypes=set(tok_spec.subtypes),
            power=tok_spec.power,
            toughness=tok_spec.toughness,
            keywords=set(tok_spec.keywords),
        )
        if tok_spec.predefined in ("treasure", "gold"):
            base.abilities.append(
                ActivatedAbility(
                    tap_cost=True,
                    sac_cost="self",
                    is_mana_ability=True,
                    effect=_ADD_ANY_MANA,
                    text="{T}, Sacrifice: Add one mana of any color.",
                ),
            )
        for factory in tok_spec.abilities:
            base.abilities.append(factory())
        tok = GameObject(base, controller, is_token=True)
        tok.zone = Zone.BATTLEFIELD
        tok.controller = controller
        tok.tapped = tok_spec.tapped if tapped is None else tapped
        tok.entered_this_turn = True
        controller.battlefield.append(tok)
        controller.stat("tokens_created")
        if tok_spec.predefined in ("treasure", "gold"):
            controller.stat("treasures_made")
        self.log(
            "token",
            who=controller.name,
            name=tok_spec.name,
            pt=(
                f"{tok_spec.power}/{tok_spec.toughness}"
                if tok_spec.power is not None
                else None
            ),
        )
        self.bump()
        self.queue_triggers(Event(EventType.ENTERS_BATTLEFIELD, {"obj": tok}))
        return tok

    @rule("121.1", "121.4")
    def draw(self, player: Player, n: int = 1) -> None:
        """Draw *n* cards, one draw event at a time (rule 121.2)."""
        for _ in range(n):
            event = self.emit(Event(EventType.DRAW, {"player": player}))
            if event is None:
                continue
            if not player.library:
                player.drew_from_empty = True  # rule 704.5b, lose later
                continue
            card = player.library.pop(0)
            card.zone = Zone.HAND
            player.hand.append(card)
            player.stat("cards_drawn")
            self.log("draw", who=player.name)
        self.bump()

    @rule("120.3", "119.3")
    def deal_damage(
        self,
        source: GameObject | Player | None,
        target: GameObject | Player,
        amount: int,
        *,
        combat: bool = False,
    ) -> None:
        """Deal damage from *source* to a player or permanent (rule 120)."""
        if amount <= 0:
            return
        event = self.emit(
            Event(
                EventType.DAMAGE,
                {
                    "source": source,
                    "target": target,
                    "amount": amount,
                    "combat": combat,
                },
            ),
        )
        if event is None:
            return
        amount = event.data["amount"]
        src_ch = source.chars(self) if isinstance(source, GameObject) else None
        # damage triggers (e.g. "deals combat damage to a player")
        self.queue_triggers(
            Event(
                EventType.DAMAGE,
                {
                    "source": source,
                    "target": target,
                    "amount": amount,
                    "combat": combat,
                    "resolved": True,
                },
            ),
        )
        self.log(
            "damage",
            src=_lname(source),
            target=_lname(target),
            n=amount,
            combat=combat,
        )
        if isinstance(target, Player):
            self._damage_player(source, src_ch, target, amount, combat=combat)
        else:
            self._damage_permanent(src_ch, target, amount)

    @rule("120.3a", "702.90b", "702.164a", "903.10a")
    def _damage_player(
        self,
        source: GameObject | Player | None,
        src_ch: Characteristics | None,
        target: Player,
        amount: int,
        *,
        combat: bool,
    ) -> None:
        """Damage to a player: life loss, poison, commander damage."""
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

    @rule("120.3c", "702.80")
    def _damage_permanent(
        self,
        src_ch: Characteristics | None,
        target: GameObject,
        amount: int,
    ) -> None:
        """Damage to a permanent: loyalty, wither, or marked damage."""
        ch = target.chars(self)
        if "Planeswalker" in ch.types:
            # rule 120.3c: damage removes that many loyalty counters
            self.remove_counters(target, "loyalty", amount)
        elif src_ch is not None and "wither" in src_ch.keywords:
            # rule 702.80: wither deals damage as -1/-1 counters
            self.put_counters(target, "-1/-1", amount)
        else:
            target.damage += amount
            if src_ch is not None and "deathtouch" in src_ch.keywords:
                target.deathtouch_damage = True  # rule 704.5h
        self.bump()

    @rule("122.1", "702.90b")
    def add_poison(self, player: Player, n: int) -> None:
        """Give a player poison counters (infect / toxic)."""
        if n <= 0:
            return
        player.poison += n
        player.stat("poison_received", n)
        self.log("poison", who=player.name, n=n, total=player.poison)
        self.bump()

    def gain_life(self, player: Player, n: int) -> None:
        """Give the player *n* life (rule 119.3), replaceable."""
        if n <= 0:
            return
        event = self.emit(Event(EventType.GAIN_LIFE, {"player": player, "amount": n}))
        if event is None:
            return
        player.life += event.data["amount"]
        self.log("life", who=player.name, delta=event.data["amount"], total=player.life)
        self.bump()

    def lose_life(self, player: Player, n: int, *, damage: bool = False) -> None:
        """Take *n* life from the player (rule 119.3), replaceable."""
        if n <= 0:
            return
        event = self.emit(
            Event(
                EventType.LOSE_LIFE,
                {"player": player, "amount": n, "damage": damage},
            ),
        )
        if event is None:
            return
        player.life -= event.data["amount"]
        self.log(
            "life",
            who=player.name,
            delta=-event.data["amount"],
            total=player.life,
        )
        self.bump()

    @rule("122.1", "122.6")
    def put_counters(self, obj: GameObject, kind: str, n: int) -> None:
        """Put *n* counters of *kind* on a battlefield object."""
        if n <= 0 or obj.zone != Zone.BATTLEFIELD:
            return
        event = self.emit(
            Event(
                EventType.PUT_COUNTERS,
                {"obj": obj, "kind": kind, "count": n, "controller": obj.controller},
            ),
        )
        if event is None or event.data["count"] <= 0:
            return
        n = event.data["count"]
        obj.counters[kind] = obj.counters.get(kind, 0) + n
        obj.controller.stat("counters_received", n)
        self.bump()
        self.queue_triggers(
            Event(
                EventType.PUT_COUNTERS,
                {"obj": obj, "kind": kind, "count": n, "resolved": True},
            ),
        )

    def remove_counters(self, obj: GameObject, kind: str, n: int) -> None:
        """Remove up to *n* counters of *kind* from the object."""
        have = obj.counters.get(kind, 0)
        take = min(have, n)
        if take:
            obj.counters[kind] = have - take
            if not obj.counters[kind]:
                del obj.counters[kind]
            self.bump()

    @rule("702.87")
    def proliferate(self, player: Player) -> None:
        """Proliferate for *player* (rule 702.87a).

        Choose any number of permanents/players with counters; give each
        one more counter of each kind already there.
        """
        picks = self.policy(player).choose_proliferate(self, player)
        for obj in picks:
            for kind in list(obj.counters):
                self.put_counters(obj, kind, 1)
        player.stat("proliferates")

    @rule("701.34")
    def populate(self, player: Player) -> None:
        """Populate: copy one of the player's creature tokens (701.34)."""
        tokens = [
            o
            for o in player.battlefield
            if o.is_token and "Creature" in o.chars(self).types
        ]
        if not tokens:
            return
        pick = self.policy(player).choose_populate(self, tokens)
        if pick is None:
            return
        ch = pick.chars(self)
        spec = TokenSpec(
            name=ch.name,
            power=pick.base.power,
            toughness=pick.base.toughness,
            colors=frozenset(ch.colors),
            types=frozenset(ch.types),
            subtypes=frozenset(ch.subtypes),
            keywords=frozenset(pick.base.keywords),
        )
        self.create_tokens(player, spec, 1)

    @rule("701.7", "701.7b")
    def destroy(self, obj: GameObject) -> None:
        """Destroy the permanent (rule 701.7); indestructible ignores it."""
        if obj.zone != Zone.BATTLEFIELD:
            return
        if "indestructible" in obj.chars(self).keywords:  # rule 702.12
            return
        event = self.emit(Event(EventType.DESTROY, {"obj": obj}))
        if event is None:
            return
        self.move_zone(obj, Zone.GRAVEYARD)

    @rule("701.9")
    def exile(self, obj: GameObject) -> None:
        """Exile the permanent (rule 701.9)."""
        if obj.zone != Zone.BATTLEFIELD:
            return
        event = self.emit(Event(EventType.EXILE_OBJ, {"obj": obj}))
        if event is None:
            return
        self.move_zone(obj, Zone.EXILE)

    @rule("701.22")
    def sacrifice(self, player: Player, obj: GameObject) -> None:
        """Sacrifice the permanent (rules 701.22a-b).

        Sacrifice can't be replaced and ignores indestructible.
        """
        if obj.zone != Zone.BATTLEFIELD or obj.controller is not player:
            return
        self.emit(Event(EventType.SACRIFICE, {"obj": obj}))
        self.move_zone(obj, Zone.GRAVEYARD)

    def tap(self, obj: GameObject) -> None:
        """Tap the permanent, firing tap triggers."""
        if not obj.tapped:
            obj.tapped = True
            self.bump()
            self.queue_triggers(Event(EventType.TAP, {"obj": obj}))

    def untap(self, obj: GameObject) -> None:
        """Untap the permanent, firing untap triggers."""
        if obj.tapped:
            obj.tapped = False
            self.bump()
            self.queue_triggers(Event(EventType.UNTAP, {"obj": obj}))

    @rule("701.23")
    def search_lands(
        self,
        player: Player,
        n: int,
        *,
        tapped: bool = True,
        to_hand: bool = False,
        basic_only: bool = True,
    ) -> None:
        """Search the library for up to *n* lands, then shuffle (701.23)."""
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
                self.queue_triggers(Event(EventType.ENTERS_BATTLEFIELD, {"obj": card}))
            found += 1
        self.shuffle(player)

    @rule("701.24")
    def shuffle(self, player: Player) -> None:
        """Shuffle the player's library (rule 701.24)."""
        self.rng.shuffle(player.library)
        self.emit(Event(EventType.SHUFFLE, {"player": player}))
        self.bump()

    @rule("701.26")
    def scry(self, player: Player, n: int) -> None:
        """Scry *n* via the player's policy (rule 701.26)."""
        top = player.library[:n]
        keep, bottom = self.policy(player).scry(self, player, top)
        player.library = keep + player.library[n:] + bottom

    def tutor(self, player: Player) -> None:
        """Search the library for any card to hand, then shuffle."""
        pick = self.policy(player).choose_tutor_card(self, player)
        if pick is not None and pick in player.library:
            player.library.remove(pick)
            pick.zone = Zone.HAND
            player.hand.append(pick)
        self.shuffle(player)

    def blink(self, obj: GameObject) -> None:
        """Exile the permanent and return it to the battlefield."""
        owner_ctl = obj.controller
        if obj.is_token:
            self.move_zone(obj, Zone.EXILE)  # token ceases (704.5d)
            return
        moved = self.move_zone(obj, Zone.EXILE)
        if moved is not None and moved.zone == Zone.EXILE:
            moved.controller = owner_ctl
            self.move_zone(moved, Zone.BATTLEFIELD)

    @rule("701.5")
    def counter_spell(self, item: StackItem) -> None:
        """Counter the spell on the stack (rule 701.5)."""
        if item in self.stack:
            if self._uncounterable(item):
                self.log("uncounterable", spell=_lname(item))
                return
            self.log("counter", spell=_lname(item), who=item.controller.name)
            self.stack.remove(item)
            if (
                item.is_spell
                and isinstance(item.obj, GameObject)
                and not item.obj.is_token
            ):
                self.move_zone(item.obj, Zone.GRAVEYARD)
            item.controller.stat("spells_countered_against")
            self.bump()

    def _uncounterable(self, item: StackItem) -> bool:
        """'Spells you control can't be countered' statics (e.g. Chimil)."""
        for obj in item.controller.battlefield:
            for ab in obj.chars(self).abilities:
                if isinstance(ab, StaticAbility) and ab.uncounterable_spells:
                    return True
        return False

    @rule("707.10", "707.10a", "704.5e")
    def copy_spell(self, item: StackItem, controller: Player) -> StackItem | None:
        """Put a copy of a spell on the stack (rule 707.10).

        The copy keeps the original's targets and X; it is created as a
        token object so it ceases to exist in any zone but the stack
        (704.5e / 707.10a) and resolves to a token if it is a permanent
        spell.
        """
        if (
            item not in self.stack
            or not item.is_spell
            or not isinstance(item.obj, GameObject)
        ):
            return None
        copy_obj = GameObject(
            item.obj.base.copy(),
            controller,
            is_token=True,
            card_ref=item.obj.card_ref,
        )
        copy_obj.is_copy = True
        copy_obj.zone = Zone.STACK
        copy_obj.controller = controller
        copy = StackItem(
            obj=copy_obj,
            source=copy_obj,
            controller=controller,
            ability=item.ability,
            targets=list(item.targets),
            x=item.x,
            is_spell=True,
        )
        self.stack.append(copy)
        controller.stat("spells_copied")
        self.log("copy", spell=_lname(item), who=controller.name)
        self.bump()
        return copy

    # ------------------------------------------------------------ mana
    @rule("605.1a", "605.3")
    def mana_sources(self, player: Player) -> list[tuple[GameObject, ActivatedAbility]]:
        """Untapped permanents with activatable mana abilities."""
        out: list[tuple[GameObject, ActivatedAbility]] = []
        for obj in player.battlefield:
            if obj.tapped:
                continue
            ch = obj.chars(self)
            summoning_sick = (
                "Creature" in ch.types
                and obj.entered_this_turn
                and "haste" not in ch.keywords
            )
            for ab in ch.abilities:
                if isinstance(ab, ActivatedAbility) and ab.is_mana_ability:
                    if ab.tap_cost and summoning_sick:
                        continue  # rule 302.6
                    out.append((obj, ab))
                    break
        return out

    def mana_colors_of(self, obj: GameObject, ab: ActivatedAbility) -> frozenset[str]:
        """Return the mana types this source's ability can produce."""
        eff = ab.effect
        adds = (
            [eff]
            if isinstance(eff, AddMana)
            else [e for e in getattr(eff, "parts", []) if isinstance(e, AddMana)]
        )
        colors: set[str] = set()
        for a in adds:
            if a.any_color:
                colors |= set("WUBRG")
            elif a.commander_identity:
                colors |= self.commander_identity(obj.controller) or {"C"}
            colors |= set(a.types)
        return frozenset(colors - {"ANY"} or {"C"})

    @rule("601.2g", "601.2h")
    def can_pay_mana(self, player: Player, cost: Cost) -> bool:
        """Whether pool plus untapped sources can cover *cost*."""
        return self._solve_mana(player, cost, commit=False)

    def pay_mana(self, player: Player, cost: Cost) -> bool:
        """Pay *cost*, activating mana abilities as needed (rule 601.2h)."""
        return self._solve_mana(player, cost, commit=True)

    def _solve_mana(self, player: Player, cost: Cost, *, commit: bool) -> bool:
        """Greedy scarcity-first assignment of mana sources to pips.

        Runs on top of whatever is already floating in the pool.
        """
        avail = [
            (obj, ab, self.mana_colors_of(obj, ab), self._mana_amount(ab))
            for obj, ab in self.mana_sources(player)
        ]
        plan = _ManaPlan(pool=dict(player.mana_pool.mana), avail=avail)
        if not (
            plan.cover_pips(cost)
            and plan.cover_hybrid(cost)
            and plan.cover_generic(cost)
        ):
            return False
        if commit:
            for obj, ab, color in plan.used:
                self._activate_mana_ability(obj, ab, color)
            player.mana_pool.pay(cost)
        return True

    @staticmethod
    def _mana_amount(ab: ActivatedAbility) -> int:
        """How much mana one activation of the ability produces."""
        eff = ab.effect
        if isinstance(eff, AddMana):
            return (
                max(1, len(eff.types))
                if not (eff.any_color or eff.commander_identity)
                else 1
            )
        return 1

    @rule("605.3b")
    def _activate_mana_ability(
        self,
        obj: GameObject,
        ab: ActivatedAbility,
        color: str,
    ) -> None:
        """Mana abilities resolve immediately, no stack (rule 605.3b)."""
        player = obj.controller
        if ab.tap_cost:
            self.tap(obj)
        if ab.sac_cost == "self":
            self.sacrifice(player, obj)
        eff = ab.effect
        if (
            isinstance(eff, AddMana)
            and not eff.any_color
            and not eff.commander_identity
            and eff.types
        ):
            for t in eff.types:
                player.mana_pool.add(t)
        else:
            player.mana_pool.add(color)

    # ------------------------------------------------------------ casting
    @rule("601.2", "601.2a", "601.2b", "601.2c", "601.2f", "601.2h", "601.2i", "903.8")
    def cast_spell(
        self,
        player: Player,
        card: GameObject,
        *,
        x: int = 0,
        from_command: bool = False,
    ) -> bool:
        """Cast a spell through the rule 601.2 process; False if illegal."""
        ch = card.base
        cost = self._spell_cost(player, card, x)
        # rule 601.2b additional costs: verify they can be paid up front
        extra = ch.additional_cost
        extra_sac = None
        if extra == "sacrifice_creature":
            extra_sac = self.policy(player).choose_sacrifice(self, player, "creature")
            if extra_sac is None:
                return False
        elif extra == "discard_card" and not [c for c in player.hand if c is not card]:
            return False
        spell_ability = next(
            (a for a in ch.abilities if isinstance(a, SpellAbility)),
            None,
        )
        item = StackItem(
            obj=card,
            source=card,
            controller=player,
            ability=spell_ability,
            x=x,
            is_spell=True,
        )
        if spell_ability is not None and spell_ability.targets:
            targets = self._choose_targets(player, spell_ability, card, x=x)
            if targets is None:
                return False  # rule 601.2c: no targets
            item.targets = targets
        if not self.pay_mana(player, cost):
            return False  # rule 601.2h
        self._pay_additional_cost(player, card, extra, extra_sac)
        self._remove_from_zone(card, card.zone)
        card.zone = Zone.STACK
        self.stack.append(item)
        self._record_cast(player, card, x=x, from_command=from_command)
        # rule 702.21 ward: cost tax handled as a trigger when targeted
        self._ward_check(item)
        self.queue_triggers(Event(EventType.CAST, {"obj": card, "player": player}))
        self.bump()
        return True

    @rule("601.2f", "903.8")
    def _spell_cost(self, player: Player, card: GameObject, x: int) -> Cost:
        """Total mana cost: X, commander tax, and cost-reduction statics."""
        ch = card.base
        cost = parse_cost(ch.mana_cost).with_x(x)
        if card.commander:
            # rule 903.8: commander tax
            cost = cost.with_extra_generic(2 * player.commander_casts)
        # rule 601.2f cost-reduction statics on the spell itself
        # (e.g. "costs {1} less to cast for each creature")
        if ch.cost_less_per_creature:
            n = sum(
                1
                for o in self.battlefield_objects()
                if "Creature" in o.chars(self).types
            )
            cost = cost.reduced(ch.cost_less_per_creature * n)
        return cost

    @rule("601.2b", "601.2h")
    def _pay_additional_cost(
        self,
        player: Player,
        card: GameObject,
        extra: str,
        extra_sac: GameObject | None,
    ) -> None:
        """Rule 601.2h: pay additional costs along with the mana."""
        if extra_sac is not None:
            self.sacrifice(player, extra_sac)
        elif extra == "discard_card":
            pool = [c for c in player.hand if c is not card]
            pick = min(pool, key=lambda c: parse_cost(c.base.mana_cost).mv)
            self.move_zone(pick, Zone.GRAVEYARD)
            self.log("discard", who=player.name, card=pick.base.name)

    def _record_cast(
        self,
        player: Player,
        card: GameObject,
        *,
        x: int,
        from_command: bool,
    ) -> None:
        """Casting bookkeeping: tax counter, stats, and the log line."""
        if card.commander and from_command:
            player.commander_casts += 1
        player.stat("spells_cast")
        player.cards_cast.append(card.base.name)
        self.log(
            "cast",
            who=player.name,
            card=card.base.name,
            x=x or None,
            commander=from_command or None,
        )
        ref = card.card_ref
        if ref is not None:
            if ref.behavior.get("wipe"):
                player.stat("wipes_cast")
            elif ref.behavior.get("removal"):
                player.stat("removal_used")

    @rule("702.21")
    def _ward_check(self, item: StackItem) -> None:
        """Ward N: counter the targeting spell unless its controller pays.

        The policy decides; paying requires available mana.
        """
        for t in item.targets or []:
            if (
                isinstance(t, GameObject)
                and t.zone == Zone.BATTLEFIELD
                and t.controller is not item.controller
            ):
                ward = next(
                    (k for k in t.chars(self).keywords if k.startswith("ward")),
                    None,
                )
                if not ward:
                    continue
                n = int(ward.split(":")[1]) if ":" in ward else 2
                cost = parse_cost(f"{{{n}}}")
                if self.can_pay_mana(item.controller, cost) and self.policy(
                    item.controller,
                ).pay_ward(self, item, n):
                    self.pay_mana(item.controller, cost)
                else:
                    self.counter_spell(item)
                    return

    @rule("602.2", "602.5a", "606.3")
    def activate_ability(
        self,
        player: Player,
        obj: GameObject,
        ab: ActivatedAbility,
        *,
        x: int = 0,
    ) -> bool:
        """Activate an ability (rule 602.2); False when illegal/unpaid."""
        if ab.from_hand:
            return self._activate_from_hand(player, obj, ab)
        if not self._activation_legal(player, obj, ab):
            return False
        cost = ab.cost.with_x(x)
        sac_pick = None
        if ab.sac_cost and ab.sac_cost != "self":
            sac_pick = self.policy(player).choose_sacrifice(
                self,
                player,
                ab.sac_cost,
                exclude=obj,
            )
            if sac_pick is None:
                return False
        # rule 601.2c (via 602.2b): choose targets before determining and
        # paying costs
        targets = self._choose_targets(player, ab, obj, x=x)
        if targets is None and ab.targets:
            return False
        # rules 601.2f-h: determine and pay costs
        if not self.pay_mana(player, cost):
            return False
        self._pay_activation_costs(player, obj, ab, sac_pick)
        self.stack.append(
            StackItem(
                obj=ab,
                source=obj,
                controller=player,
                ability=ab,
                targets=targets or [],
                x=x,
            ),
        )
        self._record_activation(player, obj, ab)
        return True

    @rule("602.5a", "602.5d", "606.3", "302.6")
    def _activation_legal(
        self,
        player: Player,
        obj: GameObject,
        ab: ActivatedAbility,
    ) -> bool:
        """Check timing, once-per-turn, tap, loyalty, and life legality."""
        ch = obj.chars(self)
        sorcery_window = (
            player is self.active_player
            and not self.stack
            and self.phase in ("main1", "main2")
        )
        if ab.loyalty_cost is not None and (
            # rule 606.3: one loyalty ability per permanent per turn,
            # only during a main phase with an empty stack
            ("loyalty", obj.id) in self.activated_this_turn
            or not sorcery_window
            or (
                ab.loyalty_cost < 0
                and obj.counters.get("loyalty", 0) < -ab.loyalty_cost
            )
        ):
            return False
        if ab.sorcery_only and not sorcery_window:
            return False  # rule 602.5d
        if ab.once_per_turn and (obj.id, id(ab)) in self.activated_this_turn:
            return False
        if ab.tap_cost:
            if obj.tapped:
                return False
            if (
                "Creature" in ch.types
                and obj.entered_this_turn
                and "haste" not in ch.keywords
            ):
                return False  # rule 302.6
        return not (ab.life_cost and player.life <= ab.life_cost)

    @rule("601.2h", "606.3")
    def _pay_activation_costs(
        self,
        player: Player,
        obj: GameObject,
        ab: ActivatedAbility,
        sac_pick: GameObject | None,
    ) -> None:
        """Pay the non-mana activation costs (tap, life, sac, loyalty)."""
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

    def _record_activation(
        self,
        player: Player,
        obj: GameObject,
        ab: ActivatedAbility,
    ) -> None:
        """Once-per-turn bookkeeping, stats, and the log line."""
        if ab.loyalty_cost is not None:
            self.activated_this_turn.add(("loyalty", obj.id))
        if ab.once_per_turn:
            self.activated_this_turn.add((obj.id, id(ab)))
        player.stat("abilities_activated")
        self.log("activate", who=player.name, source=_lname(obj))
        self.bump()

    @rule("702.29a")
    def _activate_from_hand(
        self,
        player: Player,
        obj: GameObject,
        ab: ActivatedAbility,
    ) -> bool:
        """Cycling-style abilities (rule 702.29a).

        Activated while the card is in the hand; discarding the card is
        part of the cost.
        """
        if obj not in player.hand:
            return False
        if not self.pay_mana(player, ab.cost):
            return False
        self.move_zone(obj, Zone.GRAVEYARD)  # discard as a cost
        self.stack.append(
            StackItem(obj=ab, source=obj, controller=player, ability=ab, targets=[]),
        )
        player.stat("cards_cycled")
        self.log("cycle", who=player.name, card=obj.base.name)
        self.bump()
        return True

    @rule("115.3", "601.2c")
    def _choose_targets(
        self,
        player: Player,
        ability: ResolvableAbility,
        source: GameObject,
        x: int = 0,
    ) -> list[Target] | None:
        """Choose all targets of the ability, or None when impossible."""
        targets: list[Target] = []
        ctx = Ctx(controller=player, source=source, x=x)
        for spec in ability.targets:
            legal = self.legal_targets(spec, ctx)
            if not legal:
                if spec.optional:
                    continue
                return None
            pick = self.policy(player).choose_target(self, spec, legal, ctx, ability)
            if pick is None:
                if spec.optional:
                    continue
                return None
            targets.append(pick)
        return targets

    def legal_targets(self, spec: TargetSpec, ctx: Ctx) -> list[Target]:
        """All currently legal targets for one TargetSpec (rule 115.4)."""
        if spec.what in ("player", "opponent"):
            return [p for p in self.alive() if spec.legal(self, ctx, p)]
        if spec.what == "spell":
            return [i for i in self.stack if i.is_spell]
        return [o for o in self.battlefield_objects() if spec.legal(self, ctx, o)]

    def still_legal_target(self, target: Target, _ctx: Ctx, _index: int) -> bool:
        """Rule 608.2b: re-check target legality on resolution."""
        return (
            True
            if not isinstance(target, GameObject)
            else target.zone == Zone.BATTLEFIELD
        )

    # ------------------------------------------------------------ stack
    @rule("608.2", "608.2b", "608.3", "608.2m")
    def resolve_top(self) -> None:
        """Resolve the top object of the stack (rule 608.1)."""
        item = self.stack.pop()
        if self._resolve_item(item):
            # logged on completion so observers snapshot the applied state
            self.log("resolve", what=_lname(item))

    def _resolve_item(self, item: StackItem) -> bool:
        """Resolve one stack item; False when it fizzles (rule 608.2b)."""
        ctx = Ctx(
            controller=item.controller,
            source=item.source,
            targets=item.targets,
            x=item.x,
        )
        if isinstance(item.obj, PendingTrigger):
            ctx.event_obj = item.obj.event.data.get("obj")
        if item.targets:
            legal = [
                t
                for t in item.targets
                if not isinstance(t, GameObject) or t.zone == Zone.BATTLEFIELD
            ]
            if not legal and not any(isinstance(t, Player) for t in item.targets):
                # rule 608.2b: all targets illegal -> doesn't resolve
                if item.is_spell and isinstance(item.obj, GameObject):
                    self.move_zone(item.obj, Zone.GRAVEYARD)
                self.log("fizzle", what=_lname(item))
                return False
        if item.is_spell:
            self._resolve_spell(item, ctx)
        else:
            ability = item.ability
            if (
                isinstance(ability, TriggeredAbility)
                and ability.intervening_if
                and not ability.intervening_if(self, item.source)
            ):
                return False  # rule 603.4 recheck
            if ability is not None and ability.effect is not None:
                ability.effect.resolve(self, ctx)
        self.bump()
        return True

    @rule("608.2m", "608.3", "303.4", "306.5b")
    def _resolve_spell(self, item: StackItem, ctx: Ctx) -> None:
        """Resolve a spell: run instructions or enter the battlefield."""
        card = cast("GameObject", item.obj)
        ch = card.base
        if ch.types & {"Instant", "Sorcery"}:
            if item.ability is not None and item.ability.effect is not None:
                item.ability.effect.resolve(self, ctx)
            self.move_zone(card, Zone.GRAVEYARD)  # rule 608.2m
            return
        # rule 608.3: permanent spell resolves to the battlefield
        counters: dict[str, int] = {}
        if "Planeswalker" in ch.types and ch.loyalty:
            counters["loyalty"] = ch.loyalty  # rule 306.5b
        if ch.etb_x_counters and item.x:
            counters[ch.etb_x_counters] = item.x
        moved = self.move_zone(card, Zone.BATTLEFIELD, counters=counters)
        # rule 303.4: an Aura enters attached to its target
        if moved is not None and "Aura" in ch.subtypes and item.targets:
            t = item.targets[0]
            if isinstance(t, GameObject) and t.zone == Zone.BATTLEFIELD:
                self.attach(moved, t)
            else:
                self.move_zone(moved, Zone.GRAVEYARD)

    # ------------------------------------------------------ state-based
    @rule("704.3", "704.5")
    def check_state_based_actions(self) -> bool:
        """Perform all applicable SBAs; return True if any happened."""
        acted = self._sba_players()
        acted = self._sba_permanents() or acted
        acted = self._sba_attachments() or acted
        return self._sba_legend_rule() or acted

    @rule("704.5a", "704.5b", "704.5c", "704.6c")
    def _sba_players(self) -> bool:
        """Player-loss SBAs: life, empty draw, poison, commander damage."""
        acted = False
        for p in self.alive():
            if p.life <= 0:  # rule 704.5a
                self._lose(p, "life 0 or less (704.5a)")
                acted = True
            elif p.drew_from_empty:  # rule 704.5b
                self._lose(p, "drew from empty library (704.5b)")
                acted = True
            elif p.poison >= POISON_LOSS_THRESHOLD:  # rule 704.5c
                self._lose(p, "ten or more poison counters (704.5c)")
                acted = True
            else:
                for dmg in p.commander_damage.values():
                    if dmg >= COMMANDER_DAMAGE_LOSS:  # rules 903.10a / 704.6c
                        self._lose(p, "21+ commander damage (903.10a)")
                        acted = True
                        break
        return acted

    @rule("704.5f", "704.5g", "704.5h", "704.5i", "704.5q", "702.12b")
    def _sba_permanents(self) -> bool:
        """Creature/planeswalker death and counter-annihilation SBAs."""
        acted = False
        for obj in list(self.battlefield_objects()):
            ch = obj.chars(self)
            if "Creature" in ch.types:
                tough = ch.toughness or 0
                if tough <= 0:  # rule 704.5f
                    self.move_zone(obj, Zone.GRAVEYARD)
                    acted = True
                    continue
                # rules 704.5g/h destroy; indestructible (702.12b) makes
                # the destruction do nothing, so it is not an action
                destructible = "indestructible" not in ch.keywords
                if obj.damage >= tough and destructible:  # rule 704.5g
                    self.destroy(obj)
                    acted = True
                    continue
                if (
                    obj.deathtouch_damage and obj.damage > 0 and destructible
                ):  # rule 704.5h
                    self.destroy(obj)
                    acted = True
                    continue
            if (
                "Planeswalker" in ch.types and obj.counters.get("loyalty", 0) <= 0
            ):  # rule 704.5i
                self.move_zone(obj, Zone.GRAVEYARD)
                acted = True
                continue
            # rule 704.5q: +1/+1 and -1/-1 counters annihilate
            plus, minus = obj.counters.get("+1/+1", 0), obj.counters.get("-1/-1", 0)
            if plus and minus:
                n = min(plus, minus)
                self.remove_counters(obj, "+1/+1", n)
                self.remove_counters(obj, "-1/-1", n)
                acted = True
        return acted

    @rule("704.5m", "704.5n")
    def _sba_attachments(self) -> bool:
        """Aura and Equipment attachment SBAs (rules 704.5m/n)."""
        acted = False
        for obj in list(self.battlefield_objects()):
            ch = obj.chars(self)
            if "Aura" in ch.subtypes and (
                obj.attached_to is None or obj.attached_to.zone != Zone.BATTLEFIELD
            ):
                self.move_zone(obj, Zone.GRAVEYARD)  # rule 704.5m
                acted = True
            elif (
                "Equipment" in ch.subtypes
                and obj.attached_to is not None
                and obj.attached_to.zone != Zone.BATTLEFIELD
            ):
                obj.attached_to = None  # rule 704.5n
                acted = True
        return acted

    @rule("704.5j")
    def _sba_legend_rule(self) -> bool:
        """Apply the legend rule (rule 704.5j)."""
        acted = False
        for p in self.alive():
            named: dict[str, list[GameObject]] = {}
            for obj in p.battlefield:
                ch = obj.chars(self)
                if "Legendary" in ch.supertypes and ch.name:
                    named.setdefault(ch.name, []).append(obj)
            for objs in named.values():
                if len(objs) > 1:
                    keep = self.policy(p).choose_legend(self, objs)
                    for o in objs:
                        if o is not keep:
                            self.move_zone(o, Zone.GRAVEYARD)
                    acted = True
        return acted

    def _lose(self, player: Player, reason: str) -> None:
        """Eliminate the player; end the game on a sole survivor."""
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
    @rule("704.3")
    def _sba_trigger_cycle(self) -> None:
        """Rule 704.3: repeat SBAs plus trigger placement until quiescent."""
        while True:
            acted = self.check_state_based_actions()
            placed = self.put_triggers_on_stack()
            if self.game_over or (not acted and not placed):
                return

    @rule("117.3", "117.4", "117.5", "405.5", "704.3")
    def priority_loop(self) -> None:
        """Give priority around the table until all players pass.

        Resolve the stack as needed (rules 117.4, 405.5); the loop ends
        when the stack is empty and everyone passed in succession.
        """
        while True:
            # rule 704.3: SBAs + triggers before a player gets priority
            self._sba_trigger_cycle()
            if self.game_over:
                return
            self._priority_round()
            if self.game_over:
                return
            if self.stack:
                self.resolve_top()  # rule 117.4
                if self.game_over:
                    return
                continue
            return  # step/phase ends

    @rule("117.3", "117.4")
    def _priority_round(self) -> None:
        """One round of priority: actions until all players pass."""
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
                self._sba_trigger_cycle()
                if self.game_over:
                    return
            else:
                passes += 1

    def _perform(self, player: Player, action: Action) -> bool:
        """Perform one policy-chosen action; False when it was illegal."""
        if action[0] == "cast":
            _, card, cast_opts = action
            return self.cast_spell(player, card, **cast_opts)
        if action[0] == "activate":
            _, obj, ab, act_opts = action
            return self.activate_ability(player, obj, ab, **act_opts)
        _, card = action
        return self.play_land(player, card)

    # ------------------------------------------------------------ lands
    @rule("116.2a", "305.1")
    def play_land(self, player: Player, card: GameObject) -> bool:
        """Playing a land is a special action, no stack (rule 116.2a)."""
        if player.lands_played >= 1:  # rule 305.2
            return False
        if player is not self.active_player or self.stack:
            return False
        if card not in player.hand:
            return False
        player.lands_played += 1
        tapped = any(
            isinstance(ab, StaticAbility) and ab.enters_tapped
            for ab in card.base.abilities
        )
        self.move_zone(card, Zone.BATTLEFIELD, to_battlefield_tapped=tapped)
        self.queue_triggers(
            Event(EventType.LAND_PLAYED, {"obj": card, "player": player}),
        )
        player.stat("lands_played")
        self.log("land", who=player.name, card=card.base.name, tapped=tapped or None)
        return True

    def add_floating_effect(self, effect: ContinuousEffect) -> None:
        """Register a floating continuous effect (rule 611.2)."""
        self.layers.add_floating(effect)

    @rule("303.4", "301.5")
    def attach(self, obj: GameObject, target: GameObject) -> None:
        """Attach an Aura/Equipment to its new target."""
        if obj.attached_to is not None and obj in obj.attached_to.attachments:
            obj.attached_to.attachments.remove(obj)
        obj.attached_to = target
        target.attachments.append(obj)
        self.bump()
