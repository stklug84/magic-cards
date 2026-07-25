"""Default decision policy.

The rules engine enumerates *legal* options; the policy chooses among
them. This replaces the heuristic engine's scripted plays: here the AI can
only do what the CR machinery permits (priority windows, timing, costs).
"""

from __future__ import annotations

from dataclasses import dataclass

from .abilities import ActivatedAbility, SpellAbility
from .cr import rule
from .effects import CounterSpell
from .manasys import parse_cost
from .objects import GameObject, Player, Zone


@dataclass
class PolicyProfile:
    """Tunable decision knobs for DefaultPolicy (ported from the retired
    heuristic engine's AIProfile). Presets: default, aggressive, control.
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
    if name not in PROFILES:
        msg = f"unknown AI profile {name!r}; choose from {sorted(PROFILES)}"
        raise SystemExit(msg)
    return PROFILES[name]


def _threat(game, obj) -> float:
    """Threat assessment: graph :threatWeight hook + board impact."""
    ch = obj.chars(game)
    ref = obj.card_ref
    key = ref.behavior.get("key", 0) if ref is not None else 0
    pt = (ch.power or 0) + (ch.toughness or 0)
    return key * 2 + pt * 0.3 + (3 if obj.commander else 0)


class DefaultPolicy:
    def __init__(self, rng, profile: PolicyProfile | None = None):
        self.rng = rng
        self.profile = profile or PROFILES["default"]

    # ------------------------------------------------------- mulligans
    @rule("103.5")
    def keep_hand(self, game, player, hand, mulls) -> bool:
        lands = sum(1 for c in hand if "Land" in c.base.types)
        if mulls >= self.profile.max_mulligans:
            return True
        return (
            self.profile.mulligan_min_lands <= lands <= self.profile.mulligan_max_lands
        )

    def bottom_cards(self, game, player, hand, n):
        """London mulligan (rule 103.5c): put n cards on the bottom."""
        ranked = sorted(hand, key=self._keep_rank)
        return ranked[:n]

    def _keep_rank(self, card):
        mv = parse_cost(card.base.mana_cost).mv
        if "Land" in card.base.types:
            return 10
        return -abs(mv - 3)

    # ------------------------------------------------------- priority
    def choose_action(self, game, player):
        """Return ('cast', card, kwargs) | ('activate', obj, ab, kwargs) |
        ('land', card) | None (pass).
        """
        main = (
            player is game.active_player
            and not game.stack
            and game.phase in ("main1", "main2")
        )
        # respond to opposing spells with counterspells
        if game.stack:
            top = game.stack[-1]
            if top.controller is not player and top.is_spell:
                counter = self._find_counterspell(game, player)
                if (
                    counter is not None
                    and _spell_value(top) >= self.profile.counter_value_threshold
                ):
                    return counter
            return None
        if not main:
            return None
        # 1. land drop
        if player.lands_played < 1:
            lands = [c for c in player.hand if "Land" in c.base.types]
            if lands:
                return ("land", self._best_land(game, player, lands))
        # 2. commander
        cmd = player.commander_obj
        if cmd is not None and cmd.zone == Zone.COMMAND:
            cost = parse_cost(cmd.base.mana_cost).with_extra_generic(
                2 * player.commander_casts,
            )
            if game.can_pay_mana(player, cost):
                return ("cast", cmd, {"from_command": True})
        # 3. best castable spell (sorcery speed)
        reserve = self._reaction_reserve(game, player)
        castable = []
        for card in player.hand:
            ch = card.base
            if "Land" in ch.types:
                continue
            cost = parse_cost(ch.mana_cost)
            x = 0
            if cost.x_count:
                x = self._max_x(game, player, cost)
                if x < 2:
                    continue
            if game.can_pay_mana(player, cost.with_x(x)):
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
        if castable:
            castable.sort(key=lambda t: -t[0])
            value, card, x = castable[0]
            if value > 0:
                return ("cast", card, {"x": x})
        # 4. useful activated ability
        act = self._find_activation(game, player)
        if act is not None:
            return act
        # 5. cycle dead cards (rule 702.29): excess lands, or anything
        # when flooded and out of plays
        return self._find_cycling(game, player)

    def _find_cycling(self, game, player):
        cyclers = [
            (c, a)
            for c in player.hand
            for a in c.base.abilities
            if getattr(a, "from_hand", False)
        ]
        if not cyclers:
            return None
        lands_bf = sum(1 for o in player.battlefield if "Land" in o.base.types)
        for card, ab in cyclers:
            if not game.can_pay_mana(player, ab.cost):
                continue
            is_land = "Land" in card.base.types
            if is_land and lands_bf >= 6 and player.lands_played >= 1:
                return ("activate", card, ab, {})
            if not is_land and lands_bf >= 8:
                return ("activate", card, ab, {})
        return None

    def _reaction_reserve(self, game, player) -> int:
        """Mana value to keep available for a held counterspell
        (profile.hold_reactive_mana; the heuristic engine reserved from
        turn 4 onward).
        """
        if not self.profile.hold_reactive_mana or game.turn < 4:
            return 0
        best = 0
        for card in player.hand:
            if "Instant" in card.base.types and self._is_counterspell(card):
                mv = parse_cost(card.base.mana_cost).mv
                if best == 0 or mv < best:
                    best = mv
        return best

    @staticmethod
    def _board_power(game, player) -> int:
        return sum(
            o.chars(game).power or 0
            for o in player.battlefield
            if "Creature" in o.chars(game).types
        )

    @staticmethod
    def _board_threat(game, player) -> float:
        """Wipe-relevant board value: creature power plus token count and
        annotated key-permanent weights (politics beyond raw power).
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

    def player_threat(self, game, opp) -> float:
        """Archenemy assessment: board value, life lead, and how close
        the player is to a commander-damage kill.
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

    def _should_wipe(self, game, player) -> bool:
        """Cast board wipes only when this far behind on board value
        (profile.wipe_board_deficit); token swarms and key permanents
        count, not just raw power.
        """
        deficit = max(
            (self._board_threat(game, o) for o in game.opponents(player)),
            default=0.0,
        ) - self._board_threat(game, player)
        return deficit >= self.profile.wipe_board_deficit

    def _find_counterspell(self, game, player):
        for card in player.hand:
            ch = card.base
            if "Instant" not in ch.types:
                continue
            sa = next((a for a in ch.abilities if isinstance(a, SpellAbility)), None)
            if sa is None or not self._is_counterspell(card):
                continue
            if game.can_pay_mana(player, parse_cost(ch.mana_cost)):
                return ("cast", card, {})
        return None

    @staticmethod
    def _is_counterspell(card):
        sa = next((a for a in card.base.abilities if isinstance(a, SpellAbility)), None)
        if sa is None:
            return False
        eff = sa.effect
        parts = getattr(eff, "parts", [eff])
        return any(isinstance(p, CounterSpell) for p in parts)

    def _max_x(self, game, player, cost):
        for x in range(12, 0, -1):
            if game.can_pay_mana(player, cost.with_x(x)):
                return x
        return 0

    def _cast_value(self, game, player, card, x=0):
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

    def _best_land(self, game, player, lands):
        # prefer untapped lands producing colors we need
        def score(card):
            b = card.card_ref.behavior if card.card_ref else {}
            colors = b.get("land_colors") or set()
            tapped = bool(b.get("enters_tapped"))
            return (0 if tapped else 2) + len(colors)

        return max(lands, key=score)

    def _find_activation(self, game, player):
        for obj in player.battlefield:
            ch = obj.chars(game)
            for ab in ch.abilities:
                if (
                    not isinstance(ab, ActivatedAbility)
                    or ab.is_mana_ability
                    or ab.effect is None
                ):
                    continue
                if ab.loyalty_cost is not None:
                    if ("loyalty", obj.id) in game.activated_this_turn:
                        continue
                    if (
                        ab.loyalty_cost < 0
                        and obj.counters.get("loyalty", 0) <= -ab.loyalty_cost
                    ):
                        continue  # don't suicide walkers
                    return ("activate", obj, ab, {})
                if ab.once_per_turn and (obj.id, id(ab)) in game.activated_this_turn:
                    continue
                if ab.tap_cost and obj.tapped:
                    continue
                if ab.sac_cost:
                    continue  # engine sacs need intent
                if ab.cost.mv and not game.can_pay_mana(player, ab.cost):
                    continue
                if ab.cost.mv or ab.tap_cost:
                    if ab.targets and not any(
                        game.legal_targets(s, _ctx(player, obj)) for s in ab.targets
                    ):
                        continue
                    if game.phase == "main2" or not ab.cost.mv:
                        return ("activate", obj, ab, {})
        return None

    # ------------------------------------------------------- choices
    def choose_target(self, game, spec, legal, ctx, ability):
        me = ctx.controller
        harmful = spec.what != "player" and not _is_beneficial(ability)
        if spec.what in ("player", "opponent"):
            opps = [p for p in legal if p is not me]
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
            return max(pool, key=lambda t: _threat(game, t)) if pool else None
        pool = own or ([] if spec.optional else legal)
        return max(pool, key=lambda t: _threat(game, t)) if pool else None

    def order_triggers(self, game, triggers):
        return triggers

    def accept_optional(self, game, trigger):
        return True

    def choose_replacement(self, game, event, candidates):
        return candidates[0]

    def choose_mana_color(self, game, ctx, allowed):
        return allowed[0] if allowed else "C"

    def choose_discard(self, game, player):
        return max(player.hand, key=lambda c: parse_cost(c.base.mana_cost).mv)

    def choose_sacrifice(self, game, player, selector, exclude=None):
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

    def choose_legend(self, game, objs):
        return max(objs, key=lambda o: sum(o.counters.values()))

    def commander_to_command_zone(self, game, obj, to_zone):
        return True  # rule 903.9

    def pay_ward(self, game, item, n):
        return True

    def choose_bounce_land(self, game, lands):
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

    def divide_damage(self, game, ctx, total):
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

    def choose_proliferate(self, game, player):
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

    def choose_populate(self, game, tokens):
        return max(tokens, key=lambda o: o.base.power or 0) if tokens else None

    def scry(self, game, player, top):
        lands_bf = sum(1 for o in player.battlefield if "Land" in o.base.types)
        keep, bottom = [], []
        for card in top:
            is_land = "Land" in card.base.types
            if (is_land and lands_bf >= 6) or (not is_land and lands_bf < 3):
                bottom.append(card)
            else:
                keep.append(card)
        return keep, bottom

    def choose_tutor_card(self, game, player):
        if not player.library:
            return None
        return max(
            player.library,
            key=lambda c: c.card_ref.behavior.get("key", 0) if c.card_ref else 0,
        )

    # ------------------------------------------------------- combat
    @rule("508.1")
    def declare_attackers(self, game, player, candidates):
        picks = []
        for obj in candidates:
            ch = obj.chars(game)
            power = ch.power or 0
            if power <= 0:
                continue
            target = self._attack_target(game, player, obj)
            if target is None:
                continue
            picks.append((obj, target))
        return self._lookahead_filter(game, player, picks)

    def _lookahead_filter(self, game, player, picks):
        """1-ply lookahead: simulate each defender's blocks (using that
        defender's own policy) and drop attackers whose expected trade is
        bad for this profile's aggression.
        """
        if not picks:
            return picks
        by_def = {}
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
            blocked_by = {}
            for blocker, attacker in predicted:
                blocked_by.setdefault(attacker, []).append(blocker)
            tolerance = (self.profile.aggression - 1.0) * 3.0
            for obj, target in mine:
                score = self._attack_ev(game, obj, blocked_by.get(obj, []))
                if score >= -tolerance:
                    kept.append((obj, target))
        return kept

    @staticmethod
    def _attack_ev(game, attacker, blockers) -> float:
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

    def _attack_target(self, game, player, attacker):
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
            can_be_blocked = [b for b in blockers if _could_block(game, b, attacker)]
            danger = max((b.chars(game).power or 0 for b in can_be_blocked), default=0)
            tough = ch.toughness or 0
            evasive = not can_be_blocked
            lethal_range = opp.life <= (ch.power or 0) * 3
            profitable = (
                evasive
                or danger < tough * self.profile.aggression
                or ((ch.power or 0) >= 5 and self.profile.aggression >= 1.0)
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
    def declare_blockers(self, game, player, attackers, blockers):
        assignment = []
        free = list(blockers)
        threat_order = sorted(attackers, key=lambda a: -(a.chars(game).power or 0))
        incoming = sum(a.chars(game).power or 0 for a in attackers)
        must_chump = incoming >= player.life or player.life <= self.profile.race_life
        for a in threat_order:
            ach = a.chars(game)
            cands = [b for b in free if _could_block(game, b, a)]
            if "menace" in ach.keywords and len(cands) < 2:
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


def _could_block(game, blocker, attacker):
    from .combat import can_block

    return can_block(game, blocker, attacker)


def _ctx(player, source):
    from .effects import Ctx

    return Ctx(controller=player, source=source)


def _is_beneficial(ability):
    """Return whether a targeted effect helps its target.

    Beneficial effects (pump/blink) point at our own permanents; harmful
    ones at the opponent's.
    """
    from .effects import Blink, PutCounters

    eff = getattr(ability, "effect", None)
    parts = getattr(eff, "parts", [eff]) if eff is not None else []
    for p in parts:
        if isinstance(p, Blink):
            return True
        if isinstance(p, PutCounters) and p.kind == "+1/+1":
            return True
    return False


def _spell_value(item):
    ch = item.obj.base
    return parse_cost(ch.mana_cost).mv + item.x
