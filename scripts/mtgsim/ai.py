"""Tunable AI profiles: casting priorities, threat assessment, mana holding,
attack/wipe thresholds, mulligan policy."""

from __future__ import annotations

from dataclasses import dataclass

from .mana import parse_cost


@dataclass
class AIProfile:
    name: str = "default"
    aggression: float = 1.0        # >1 attacks into worse boards
    hold_reactive_mana: bool = True
    wipe_board_deficit: int = 6    # cast wipes when this far behind
    removal_key_threshold: int = 3  # min 'key' weight worth a removal spell
    mulligan_min_lands: int = 3
    mulligan_max_lands: int = 5
    max_mulligans: int = 3
    race_life: int = 14            # below this life, chump-block freely

    # ------------- threat assessment ----------------------------------
    def choose_attack_target(self, pl, opps):
        """Attack the biggest threat that we can actually damage."""
        if not opps:
            return None
        def score(o):
            openness = max(1, 20 - o.total_power())
            return o.board_threat() * 0.5 + openness + (40 - o.life) * 0.3
        return max(opps, key=score)

    def choose_effect_target(self, pl, opps):
        """Harmful non-combat effects hit the biggest board."""
        if not opps:
            return None
        return max(opps, key=lambda o: o.board_threat())

    def should_attack(self, pl, dfn, attackers, wall):
        total = sum(u.power for u in attackers)
        if dfn.life <= total:                      # lethal range: go
            return True
        excess = len(attackers) - len(wall)
        wall_pow = sum(u.power for u in wall)
        return (excess >= 1 or total > wall_pow) and \
            total * self.aggression > wall_pow * 0.6

    def should_wipe(self, pl, game):
        best_opp = max((o for o in game.opponents_of(pl) if not o.eliminated),
                       key=lambda o: o.total_power(), default=None)
        if best_opp is None:
            return False
        return best_opp.total_power() - pl.total_power() \
            >= self.wipe_board_deficit

    # ------------- mana holding ----------------------------------------
    def reserve_for_reaction(self, pl):
        """Mana value to keep untapped for counterspells/protection."""
        if not self.hold_reactive_mana:
            return 0
        best = 0
        for name in pl.hand:
            b = pl.card(name).behavior
            if b.get("counterspell") or b.get("protect"):
                best = max(best, 0) if best else 0
                cost = parse_cost(pl.card(name).mana_cost).mv
                if best == 0 or cost < best:
                    best = cost
        return best

    # ------------- mulligan ----------------------------------------------
    def keep_score(self, pl, hand):
        lands = [c for c in hand if pl.card(c).is_land]
        n = len(lands)
        if n < self.mulligan_min_lands - 1 or n > self.mulligan_max_lands + 1:
            return 0
        score = 3 - abs(4 - n)
        early = sum(1 for c in hand
                    if not pl.card(c).is_land and pl.card(c).mv <= 3)
        score += min(early, 3)
        engines = sum(1 for c in hand
                      if pl.card(c).b("tokens_per_turn")
                      or pl.card(c).b("doubler")
                      or pl.card(c).b("mass_counters")
                      or pl.card(c).b("token_per_counter"))
        score += engines
        return score

    def bottom_priority(self, pl, hand, keep_size):
        """Order hand worst-first for London bottoming."""
        lands = [c for c in hand if pl.card(c).is_land]
        def badness(c):
            card = pl.card(c)
            if card.is_land:
                return 5 - len(lands)          # excess lands go first
            return card.mv                      # expensive spells next
        return sorted(hand, key=badness, reverse=True)


PROFILES = {
    "default": AIProfile(),
    "aggressive": AIProfile(name="aggressive", aggression=1.5,
                            hold_reactive_mana=False, wipe_board_deficit=9,
                            race_life=10),
    "control": AIProfile(name="control", aggression=0.7,
                         hold_reactive_mana=True, wipe_board_deficit=4,
                         removal_key_threshold=3),
}


def get_profile(name: str) -> AIProfile:
    if name not in PROFILES:
        raise SystemExit(f"unknown AI profile {name!r}; "
                         f"choose from {sorted(PROFILES)}")
    return PROFILES[name]
