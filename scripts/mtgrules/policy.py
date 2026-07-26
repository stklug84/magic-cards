"""Default decision policy.

The rules engine enumerates *legal* options; the policy chooses among
them. This replaces the heuristic engine's scripted plays: here the AI can
only do what the CR machinery permits (priority windows, timing, costs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mtgrules.abilities import ActivatedAbility, SpellAbility
from mtgrules.combat import can_block
from mtgrules.cr import rule
from mtgrules.effects import Blink, CounterSpell, Ctx, PutCounters
from mtgrules.manasys import parse_cost
from mtgrules.objects import GameObject, Player, Zone

if TYPE_CHECKING:
    import random

    from mtgrules.abilities import TargetSpec
    from mtgrules.events import Event
    from mtgrules.game import (
        Action,
        ActivateAction,
        CastAction,
        Game,
        LandAction,
        ResolvableAbility,
    )
    from mtgrules.manasys import Cost
    from mtgrules.replacements import Replacement
    from mtgrules.stack import PendingTrigger, StackItem, Target

#: X spells are held until X is at least this large
_MIN_WORTHWHILE_X = 2
#: land count where excess lands become cycling fodder
_FLOODED_LANDS = 6
#: land count where anything spare becomes cycling fodder
_VERY_FLOODED_LANDS = 8
#: hold reactive counterspell mana from this turn on
_HOLD_REACTIVE_FROM_TURN = 4
#: creatures at least this big attack regardless of blockers
_BIG_ATTACKER_POWER = 5
#: rule 702.111b menace: can't be blocked by exactly one creature
_MENACE_MIN_BLOCKERS = 2
#: scry: battlefield land counts steering land keeps/bottoms
_SCRY_ENOUGH_LANDS = 6
_SCRY_NEED_LANDS = 3


@dataclass
class PolicyProfile:
    """Tunable decision knobs for DefaultPolicy.

    Ported from the retired heuristic engine's AIProfile. Presets:
    default, aggressive, control.
    """

    name: str = "default"
    aggression: float = 1.0  # >1 attacks into worse boards
    hold_reactive_mana: bool = True
    wipe_board_deficit: int = 6  # cast wipes when this far behind in power
    removal_key_threshold: float = 2.0  # min threat worth a removal spell
    counter_value_threshold: int = 3  # min spell mv worth countering
    mulligan_min_lands: int = 2
    mulligan_max_lands: int = 5
    max_mulligans: int = 3
    race_life: int = 14  # below this life, chump-block freely


PROFILES = {
    "default": PolicyProfile(),
    "aggressive": PolicyProfile(
        name="aggressive",
        aggression=1.5,
        hold_reactive_mana=False,
        wipe_board_deficit=9,
        counter_value_threshold=4,
        race_life=10,
    ),
    "control": PolicyProfile(
        name="control",
        aggression=0.7,
        hold_reactive_mana=True,
        wipe_board_deficit=4,
        removal_key_threshold=1.5,
        counter_value_threshold=2,
    ),
}


def get_profile(name: str) -> PolicyProfile:
    """Look up a preset profile; exit with a message when unknown."""
    if name not in PROFILES:
        msg = f"unknown AI profile {name!r}; choose from {sorted(PROFILES)}"
        raise SystemExit(msg)
    return PROFILES[name]


def _threat(game: Game, obj: GameObject) -> float:
    """Threat assessment: graph :threatWeight hook + board impact."""
    ch = obj.chars(game)
    ref = obj.card_ref
    key = ref.behavior.get("key", 0) if ref is not None else 0
    pt = (ch.power or 0) + (ch.toughness or 0)
    return key * 2 + pt * 0.3 + (3 if obj.commander else 0)


class DefaultPolicy:
    """The built-in decision policy over engine-legal actions."""

    def __init__(
        self,
        rng: random.Random,
        profile: PolicyProfile | None = None,
    ) -> None:
        """Bind the RNG and the (default) knob profile."""
        self.rng = rng
        self.profile = profile or PROFILES["default"]

    # ------------------------------------------------------- mulligans
    @rule("103.5")
    def keep_hand(
        self,
        _game: Game,
        _player: Player,
        hand: list[GameObject],
        mulls: int,
    ) -> bool:
        """Keep hands whose land count is inside the profile's window."""
        lands = sum(1 for c in hand if "Land" in c.base.types)
        if mulls >= self.profile.max_mulligans:
            return True
        return (
            self.profile.mulligan_min_lands <= lands <= self.profile.mulligan_max_lands
        )

    def bottom_cards(
        self,
        _game: Game,
        _player: Player,
        hand: list[GameObject],
        n: int,
    ) -> list[GameObject]:
        """London mulligan (rule 103.5c): put n cards on the bottom."""
        ranked = sorted(hand, key=self._keep_rank)
        return ranked[:n]

    def _keep_rank(self, card: GameObject) -> float:
        """Keep priority of one card during mulligan bottoming."""
        mv = parse_cost(card.base.mana_cost).mv
        if "Land" in card.base.types:
            return 10
        return -abs(mv - 3)

    # ------------------------------------------------------- priority
    def choose_action(self, game: Game, player: Player) -> Action | None:
        """Choose one legal action, or None to pass priority.

        Returns ('cast', card, kwargs) | ('activate', obj, ab, kwargs) |
        ('land', card) | None (pass).
        """
        main = (
            player is game.active_player
            and not game.stack
            and game.phase in ("main1", "main2")
        )
        # respond to opposing spells with counterspells
        if game.stack:
            return self._respond_to_stack(game, player)
        if not main:
            return None
        # 1. land drop
        if player.lands_played < 1:
            lands = [c for c in player.hand if "Land" in c.base.types]
            if lands:
                land_act: LandAction = ("land", self.best_land(game, player, lands))
                return land_act
        # 2. commander
        cmd = player.commander_obj
        if cmd is not None and cmd.zone == Zone.COMMAND:
            cost = parse_cost(cmd.base.mana_cost).with_extra_generic(
                2 * player.commander_casts,
            )
            if game.can_pay_mana(player, cost):
                cmd_act: CastAction = ("cast", cmd, {"from_command": True})
                return cmd_act
        # 3. best castable spell (sorcery speed)
        castable = self._castable_spells(game, player)
        if castable:
            castable.sort(key=lambda t: -t[0])
            value, card, x = castable[0]
            if value > 0:
                cast_act: CastAction = ("cast", card, {"x": x})
                return cast_act
        # 4. useful activated ability; else 5. cycle dead cards
        # (rule 702.29): excess lands, or anything when flooded and out
        # of plays
        return self._find_activation(game, player) or self._find_cycling(
            game,
            player,
        )

    def _respond_to_stack(self, game: Game, player: Player) -> Action | None:
        """Instant-speed response: counter a worthwhile opposing spell."""
        top = game.stack[-1]
        if top.controller is not player and top.is_spell:
            counter = self._find_counterspell(game, player)
            if (
                counter is not None
                and _spell_value(top) >= self.profile.counter_value_threshold
            ):
                return counter
        return None

    def _castable_spells(
        self,
        game: Game,
        player: Player,
    ) -> list[tuple[float, GameObject, int]]:
        """All castable hand spells as (value, card, x) candidates."""
        reserve = self._reaction_reserve(game, player)
        castable: list[tuple[float, GameObject, int]] = []
        for card in player.hand:
            ch = card.base
            if "Land" in ch.types:
                continue
            cost = parse_cost(ch.mana_cost)
            x = 0
            if cost.x_count:
                x = self._max_x(game, player, cost)
                if x < _MIN_WORTHWHILE_X:
                    continue
            if not game.can_pay_mana(player, cost.with_x(x)):
                continue
            sa = next(
                (a for a in ch.abilities if isinstance(a, SpellAbility)),
                None,
            )
            if (
                sa is not None
                and sa.targets
                and not any(
                    game.legal_targets(s, _ctx(player, card)) for s in sa.targets
                )
            ):
                continue
            if self._is_counterspell(card):
                continue  # hold for responses
            if reserve and not game.can_pay_mana(
                player,
                cost.with_x(x).with_extra_generic(reserve),
            ):
                continue  # keep reaction mana up
            castable.append((self._cast_value(game, player, card, x), card, x))
        return castable

    def _find_cycling(self, game: Game, player: Player) -> Action | None:
        """Cycle a dead card when flooded (rule 702.29)."""
        cyclers = [
            (c, a)
            for c in player.hand
            for a in c.base.abilities
            if isinstance(a, ActivatedAbility) and a.from_hand
        ]
        if not cyclers:
            return None
        lands_bf = sum(1 for o in player.battlefield if "Land" in o.base.types)
        for card, ab in cyclers:
            if not game.can_pay_mana(player, ab.cost):
                continue
            is_land = "Land" in card.base.types
            cycle_act: ActivateAction = ("activate", card, ab, {})
            if is_land and lands_bf >= _FLOODED_LANDS and player.lands_played >= 1:
                return cycle_act
            if not is_land and lands_bf >= _VERY_FLOODED_LANDS:
                return cycle_act
        return None

    def _reaction_reserve(self, game: Game, player: Player) -> int:
        """Mana value to keep available for a held counterspell.

        Controlled by profile.hold_reactive_mana; the heuristic engine
        reserved from turn 4 onward.
        """
        if not self.profile.hold_reactive_mana or game.turn < _HOLD_REACTIVE_FROM_TURN:
            return 0
        best = 0
        for card in player.hand:
            if "Instant" in card.base.types and self._is_counterspell(card):
                mv = parse_cost(card.base.mana_cost).mv
                if best == 0 or mv < best:
                    best = mv
        return best

    @staticmethod
    def _board_power(game: Game, player: Player) -> int:
        """Total creature power on the player's battlefield."""
        return sum(
            o.chars(game).power or 0
            for o in player.battlefield
            if "Creature" in o.chars(game).types
        )

    @staticmethod
    def _board_threat(game: Game, player: Player) -> float:
        """Wipe-relevant board value.

        Creature power plus token count and annotated key-permanent
        weights (politics beyond raw power).
        """
        power = tokens = keys = 0.0
        for o in player.battlefield:
            ref = o.card_ref
            if ref is not None:
                keys += ref.behavior.get("key", 0)
            if "Creature" not in o.base.types:
                continue
            ch = o.chars(game)
            power += ch.power or 0
            if o.is_token:
                tokens += 1
        return power + tokens * 0.5 + keys * 1.5

    def player_threat(self, game: Game, opp: Player) -> float:
        """Archenemy assessment of one opponent.

        Board value, life lead, and how close the player is to a
        commander-damage kill.
        """
        threat = self._board_threat(game, opp)
        threat += max(0, opp.life - 30) * 0.15
        cmd = opp.commander_obj
        if cmd is not None:
            best = max(
                (
                    p.commander_damage.get(cmd.id, 0)
                    for p in game.players
                    if p is not opp
                ),
                default=0,
            )
            threat += best * 0.3  # 21-damage clock
        return threat

    def _should_wipe(self, game: Game, player: Player) -> bool:
        """Whether a board wipe is worth casting now.

        Cast board wipes only when this far behind on board value
        (profile.wipe_board_deficit); token swarms and key permanents
        count, not just raw power.
        """
        deficit = max(
            (self._board_threat(game, o) for o in game.opponents(player)),
            default=0.0,
        ) - self._board_threat(game, player)
        return deficit >= self.profile.wipe_board_deficit

    def _find_counterspell(self, game: Game, player: Player) -> Action | None:
        """Find a castable counterspell in hand, as a cast action."""
        for card in player.hand:
            ch = card.base
            if "Instant" not in ch.types:
                continue
            sa = next((a for a in ch.abilities if isinstance(a, SpellAbility)), None)
            if sa is None or not self._is_counterspell(card):
                continue
            if game.can_pay_mana(player, parse_cost(ch.mana_cost)):
                counter_act: CastAction = ("cast", card, {})
                return counter_act
        return None

    @staticmethod
    def _is_counterspell(card: GameObject) -> bool:
        """Whether the card's spell ability counters a spell."""
        sa = next((a for a in card.base.abilities if isinstance(a, SpellAbility)), None)
        if sa is None:
            return False
        eff = sa.effect
        parts = getattr(eff, "parts", [eff])
        return any(isinstance(p, CounterSpell) for p in parts)

    def _max_x(self, game: Game, player: Player, cost: Cost) -> int:
        """Return the largest payable X (searched from 12 down)."""
        for x in range(12, 0, -1):
            if game.can_pay_mana(player, cost.with_x(x)):
                return x
        return 0

    def _cast_value(
        self,
        game: Game,
        player: Player,
        card: GameObject,
        x: int = 0,
    ) -> float:
        """Heuristic value of casting *card* now; negative = hold it."""
        ch = card.base
        ref = card.card_ref
        mv = parse_cost(ch.mana_cost).mv + x
        v = 1.0 + mv * 0.3
        if ref is not None:
            v += ref.behavior.get("key", 0) * 0.5
            if ref.behavior.get("wipe"):
                if not self._should_wipe(game, player):
                    return -1  # hold the wipe
                v += 3.0
        if ch.types & {"Creature", "Planeswalker"}:
            v += 1.5
        # removal/wipes only when there are worthwhile targets
        sa = next((a for a in ch.abilities if isinstance(a, SpellAbility)), None)
        if sa is not None and sa.targets:
            best = 0.0
            for spec in sa.targets:
                legal = game.legal_targets(spec, _ctx(player, card))
                enemy = [
                    t
                    for t in legal
                    if isinstance(t, GameObject) and t.controller is not player
                ]
                if enemy:
                    best = max(best, *(_threat(game, t) for t in enemy))
            if best < self.profile.removal_key_threshold:
                return -1
            v += best * 0.4
        return v

    def best_land(
        self,
        _game: Game,
        _player: Player,
        lands: list[GameObject],
    ) -> GameObject:
        """Pick the best land drop: untapped and color-rich first."""

        # prefer untapped lands producing colors we need
        def score(card: GameObject) -> int:
            b = card.card_ref.behavior if card.card_ref else {}
            colors = b.get("land_colors") or set()
            tapped = bool(b.get("enters_tapped"))
            return (0 if tapped else 2) + len(colors)

        return max(lands, key=score)

    def _find_activation(self, game: Game, player: Player) -> Action | None:
        """Find a useful non-mana activated ability to activate."""
        for obj in player.battlefield:
            ch = obj.chars(game)
            for ab in ch.abilities:
                if (
                    not isinstance(ab, ActivatedAbility)
                    or ab.is_mana_ability
                    or ab.effect is None
                ):
                    continue
                action = self._activation_action(game, player, obj, ab)
                if action is not None:
                    return action
        return None

    @staticmethod
    def _loyalty_activation(
        game: Game,
        obj: GameObject,
        ab: ActivatedAbility,
    ) -> Action | None:
        """Whether a loyalty ability is worth activating now."""
        if ("loyalty", obj.id) in game.activated_this_turn:
            return None
        if ab.loyalty_cost is not None and (
            ab.loyalty_cost < 0 and obj.counters.get("loyalty", 0) <= -ab.loyalty_cost
        ):
            return None  # don't suicide walkers
        loyalty_act: ActivateAction = ("activate", obj, ab, {})
        return loyalty_act

    def _activation_action(
        self,
        game: Game,
        player: Player,
        obj: GameObject,
        ab: ActivatedAbility,
    ) -> Action | None:
        """Whether one ability is worth (and legal) activating now."""
        if ab.loyalty_cost is not None:
            return self._loyalty_activation(game, obj, ab)
        if (
            (ab.once_per_turn and (obj.id, id(ab)) in game.activated_this_turn)
            or (ab.tap_cost and obj.tapped)
            or bool(ab.sac_cost)  # engine sacs need intent
            or (ab.cost.mv and not game.can_pay_mana(player, ab.cost))
        ):
            return None
        if ab.cost.mv or ab.tap_cost:
            if ab.targets and not any(
                game.legal_targets(s, _ctx(player, obj)) for s in ab.targets
            ):
                return None
            if game.phase == "main2" or not ab.cost.mv:
                utility_act: ActivateAction = ("activate", obj, ab, {})
                return utility_act
        return None

    # ------------------------------------------------------- choices
    def choose_target(
        self,
        game: Game,
        spec: TargetSpec,
        legal: list[Target],
        ctx: Ctx,
        ability: ResolvableAbility,
    ) -> Target | None:
        """Pick one target: harm the enemy's best, help our own best."""
        me = ctx.controller
        harmful = spec.what != "player" and not _is_beneficial(ability)
        if spec.what in ("player", "opponent"):
            opps = [p for p in legal if isinstance(p, Player) and p is not me]
            return (
                min(opps, key=lambda p: p.life)
                if opps
                else (None if spec.optional else legal[0])
            )
        if spec.what == "spell":
            return legal[-1] if legal else None
        enemy = [
            t for t in legal if isinstance(t, GameObject) and t.controller is not me
        ]
        own = [t for t in legal if isinstance(t, GameObject) and t.controller is me]
        if harmful:
            pool = enemy or ([] if spec.optional else legal)
            return max(pool, key=lambda t: _obj_threat(game, t)) if pool else None
        pool = own or ([] if spec.optional else legal)
        return max(pool, key=lambda t: _obj_threat(game, t)) if pool else None

    def order_triggers(
        self,
        _game: Game,
        triggers: list[PendingTrigger],
    ) -> list[PendingTrigger]:
        """Order the player's own simultaneous triggers (rule 603.3b)."""
        return triggers

    def accept_optional(self, _game: Game, _trigger: PendingTrigger) -> bool:
        """Whether to take a 'you may' trigger (always yes)."""
        return True

    def choose_replacement(
        self,
        _game: Game,
        _event: Event,
        candidates: list[Replacement],
    ) -> Replacement:
        """Order applicable replacement effects (rule 616.1)."""
        return candidates[0]

    def choose_mana_color(self, _game: Game, _ctx: Ctx, allowed: str) -> str:
        """Pick the color an any-color source produces."""
        return allowed[0] if allowed else "C"

    def choose_discard(self, _game: Game, player: Player) -> GameObject:
        """Pick the cleanup-step discard (highest mana value)."""
        return max(player.hand, key=lambda c: parse_cost(c.base.mana_cost).mv)

    def choose_sacrifice(
        self,
        game: Game,
        player: Player,
        selector: str,
        exclude: GameObject | None = None,
    ) -> GameObject | None:
        """Pick the cheapest permanent matching a sacrifice selector."""
        pool = []
        for o in player.battlefield:
            if o is exclude:
                continue
            ch = o.chars(game)
            if "creature" in selector and "Creature" not in ch.types:
                continue
            if "artifact" in selector and "Artifact" not in ch.types:
                continue
            pool.append(o)
        if not pool:
            return None
        return min(pool, key=lambda o: _threat(game, o))

    def choose_legend(self, _game: Game, objs: list[GameObject]) -> GameObject:
        """Legend rule keep choice: most counters survive (rule 704.5j)."""
        return max(objs, key=lambda o: sum(o.counters.values()))

    def commander_to_command_zone(
        self,
        _game: Game,
        _obj: GameObject,
        _to_zone: str,
    ) -> bool:
        """Send a dying commander to the command zone (rule 903.9)."""
        return True

    def pay_ward(self, _game: Game, _item: StackItem, _n: int) -> bool:
        """Pay ward taxes whenever the mana is available (702.21)."""
        return True

    def choose_bounce_land(
        self,
        _game: Game,
        lands: list[GameObject],
    ) -> GameObject | None:
        """Pick the least color-rich land to return to hand."""
        if not lands:
            return None
        return min(
            lands,
            key=lambda o: len(
                (o.card_ref.behavior.get("land_colors") or set())
                if o.card_ref
                else set(),
            ),
        )

    def divide_damage(
        self,
        game: Game,
        ctx: Ctx,
        total: int,
    ) -> list[tuple[GameObject, int]]:
        """Divide damage lethally across the scariest enemy creatures."""
        enemies = sorted(
            (
                o
                for o in game.battlefield_objects()
                if o.controller is not ctx.controller
                and "Creature" in o.chars(game).types
            ),
            key=lambda o: -_threat(game, o),
        )
        out = []
        for o in enemies:
            if total <= 0:
                break
            need = max(1, (o.chars(game).toughness or 1) - o.damage)
            deal = min(total, need)
            out.append((o, deal))
            total -= deal
        return out

    def choose_proliferate(self, game: Game, player: Player) -> list[GameObject]:
        """Pick proliferate recipients that help us / hurt opponents."""
        picks = []
        for o in game.battlefield_objects():
            if not o.counters:
                continue
            good = (
                o.counters.get("+1/+1", 0)
                + o.counters.get("loyalty", 0)
                + o.counters.get("charge", 0)
            )
            bad = o.counters.get("-1/-1", 0)
            if (o.controller is player and good > bad) or (
                o.controller is not player and bad > good
            ):
                picks.append(o)
        return picks

    def choose_populate(
        self,
        _game: Game,
        tokens: list[GameObject],
    ) -> GameObject | None:
        """Pick the biggest creature token to copy (rule 701.34)."""
        return max(tokens, key=lambda o: o.base.power or 0) if tokens else None

    def scry(
        self,
        _game: Game,
        player: Player,
        top: list[GameObject],
    ) -> tuple[list[GameObject], list[GameObject]]:
        """Scry choice: (keep on top, bottom) based on land needs."""
        lands_bf = sum(1 for o in player.battlefield if "Land" in o.base.types)
        keep, bottom = [], []
        for card in top:
            is_land = "Land" in card.base.types
            if (is_land and lands_bf >= _SCRY_ENOUGH_LANDS) or (
                not is_land and lands_bf < _SCRY_NEED_LANDS
            ):
                bottom.append(card)
            else:
                keep.append(card)
        return keep, bottom

    def choose_tutor_card(self, _game: Game, player: Player) -> GameObject | None:
        """Tutor pick: the highest key-weight card in the library."""
        if not player.library:
            return None
        return max(
            player.library,
            key=lambda c: c.card_ref.behavior.get("key", 0) if c.card_ref else 0,
        )

    # ------------------------------------------------------- combat
    @rule("508.1")
    def declare_attackers(
        self,
        game: Game,
        player: Player,
        candidates: list[GameObject],
    ) -> list[tuple[GameObject, Player | GameObject]]:
        """Choose the attack: per-creature targets plus a lookahead."""
        picks: list[tuple[GameObject, Player | GameObject]] = []
        for obj in candidates:
            ch = obj.chars(game)
            power = ch.power or 0
            if power <= 0:
                continue
            target = self.attack_target(game, player, obj)
            if target is None:
                continue
            picks.append((obj, target))
        return self._lookahead_filter(game, player, picks)

    def _lookahead_filter(
        self,
        game: Game,
        _player: Player,
        picks: list[tuple[GameObject, Player | GameObject]],
    ) -> list[tuple[GameObject, Player | GameObject]]:
        """1-ply lookahead over the declared attack.

        Simulate each defender's blocks (using that defender's own
        policy) and drop attackers whose expected trade is bad for this
        profile's aggression.
        """
        if not picks:
            return picks
        by_def: dict[Player, list[tuple[GameObject, Player | GameObject]]] = {}
        for obj, target in picks:
            dfn = target if isinstance(target, Player) else target.controller
            by_def.setdefault(dfn, []).append((obj, target))
        kept = []
        for dfn, mine in by_def.items():
            attackers = [a for a, _ in mine]
            # lethal alpha strike: send everything, no second thoughts
            incoming = sum(a.chars(game).power or 0 for a in attackers)
            if incoming >= dfn.life:
                kept.extend(mine)
                continue
            blockers = [
                o
                for o in dfn.battlefield
                if "Creature" in o.chars(game).types and not o.tapped
            ]
            predicted = game.policy(dfn).declare_blockers(
                game,
                dfn,
                attackers,
                blockers,
            )
            blocked_by: dict[GameObject, list[GameObject]] = {}
            for blocker, attacker in predicted:
                blocked_by.setdefault(attacker, []).append(blocker)
            tolerance = (self.profile.aggression - 1.0) * 3.0
            for obj, target in mine:
                score = self._attack_ev(game, obj, blocked_by.get(obj, []))
                if score >= -tolerance:
                    kept.append((obj, target))
        return kept

    @staticmethod
    def _attack_ev(
        game: Game,
        attacker: GameObject,
        blockers: list[GameObject],
    ) -> float:
        """Estimate the expected value of one attack against a predicted block."""
        ach = attacker.chars(game)
        power, tough = ach.power or 0, ach.toughness or 0
        if not blockers:
            return power + (2 if attacker.commander else 0)
        incoming = sum(b.chars(game).power or 0 for b in blockers)
        dies = (incoming >= tough and "indestructible" not in ach.keywords) or any(
            "deathtouch" in b.chars(game).keywords for b in blockers
        )
        kills = 0.0
        remaining = power
        for b in sorted(blockers, key=lambda b: b.chars(game).toughness or 0):
            bch = b.chars(game)
            btough = bch.toughness or 0
            if remaining >= btough or "deathtouch" in ach.keywords:
                kills += _threat(game, b)
                remaining -= btough
        overflow = max(0, remaining) if "trample" in ach.keywords else 0
        return kills + overflow - (_threat(game, attacker) if dies else 0)

    def attack_target(
        self,
        game: Game,
        player: Player,
        attacker: GameObject,
    ) -> Player | None:
        """Pick which opponent this creature attacks, or None to hold."""
        ch = attacker.chars(game)
        opps = game.opponents(player)
        threats = {o.name: self.player_threat(game, o) for o in opps}
        top_threat = max(threats.values(), default=0.0)
        best, best_score = None, None
        for opp in opps:
            blockers = [
                o
                for o in opp.battlefield
                if "Creature" in o.chars(game).types and not o.tapped
            ]
            can_be_blocked = [b for b in blockers if can_block(game, b, attacker)]
            danger = max((b.chars(game).power or 0 for b in can_be_blocked), default=0)
            tough = ch.toughness or 0
            evasive = not can_be_blocked
            lethal_range = opp.life <= (ch.power or 0) * 3
            profitable = (
                evasive
                or danger < tough * self.profile.aggression
                or (
                    (ch.power or 0) >= _BIG_ATTACKER_POWER
                    and self.profile.aggression >= 1.0
                )
                or attacker.commander
            )
            if not profitable:
                continue
            # kingmaker avoidance (pods): don't farm the weakest player
            # while a real archenemy is developing
            if (
                len(opps) > 1
                and not lethal_range
                and threats[opp.name] < 0.4 * top_threat
            ):
                continue
            grudge = player.grudges.get(opp.name, 0)
            score = (
                (100 if evasive else 0)
                + threats[opp.name] * 0.8
                + min(grudge, 10) * 0.5
                - opp.life * 0.2
                - len(can_be_blocked) * 3
            )
            if best_score is None or score > best_score:
                best, best_score = opp, score
        return best

    @rule("509.1")
    def declare_blockers(
        self,
        game: Game,
        player: Player,
        attackers: list[GameObject],
        blockers: list[GameObject],
    ) -> list[tuple[GameObject, GameObject]]:
        """Choose blocks: trades, safe blocks, and racing chumps."""
        assignment: list[tuple[GameObject, GameObject]] = []
        free = list(blockers)
        threat_order = sorted(attackers, key=lambda a: -(a.chars(game).power or 0))
        incoming = sum(a.chars(game).power or 0 for a in attackers)
        must_chump = incoming >= player.life or player.life <= self.profile.race_life
        for a in threat_order:
            ach = a.chars(game)
            cands = [b for b in free if can_block(game, b, a)]
            if "menace" in ach.keywords and len(cands) < _MENACE_MIN_BLOCKERS:
                continue
            pick = None
            for b in sorted(cands, key=lambda b: -(b.chars(game).power or 0)):
                bch = b.chars(game)
                kills = (bch.power or 0) >= (
                    ach.toughness or 0
                ) or "deathtouch" in bch.keywords
                survives = (ach.power or 0) < (
                    bch.toughness or 0
                ) and "deathtouch" not in ach.keywords
                if kills or survives or must_chump:
                    pick = b
                    break
            if pick is not None:
                if "menace" in ach.keywords:
                    others = [b for b in cands if b is not pick]
                    if not others:
                        continue
                    assignment.append((pick, a))
                    assignment.append((others[0], a))
                    free.remove(pick)
                    free.remove(others[0])
                else:
                    assignment.append((pick, a))
                    free.remove(pick)
        return assignment


def _obj_threat(game: Game, target: Target) -> float:
    """Threat of a target for max() keys (players/spells rank zero)."""
    if isinstance(target, GameObject):
        return _threat(game, target)
    return 0.0


def _ctx(player: Player, source: GameObject) -> Ctx:
    """Build a minimal resolution context for legality probing."""
    return Ctx(controller=player, source=source)


def _is_beneficial(ability: ResolvableAbility) -> bool:
    """Return whether a targeted effect helps its target.

    Beneficial effects (pump/blink) point at our own permanents; harmful
    ones at the opponent's.
    """
    eff = ability.effect
    parts = getattr(eff, "parts", [eff]) if eff is not None else []
    for p in parts:
        if isinstance(p, Blink):
            return True
        if isinstance(p, PutCounters) and p.kind == "+1/+1":
            return True
    return False


def _spell_value(item: StackItem) -> int:
    """Rate a stack spell by mana value including X."""
    if isinstance(item.obj, GameObject):
        return parse_cost(item.obj.base.mana_cost).mv + item.x
    return item.x
