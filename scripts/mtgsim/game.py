"""Game engine: London mulligan, turn structure, casting with reaction
windows, upkeep engines, win conditions. Supports 2-4 player pods."""

from __future__ import annotations

from . import combat, effects
from .mana import can_pay, parse_cost, pay, potential
from .state import MAX_HAND, Permanent, PlayerState


class Game:
    def __init__(self, decks, db, rng, profiles, turn_cap=25,
                 log_events=False):
        self.db = db
        self.rng = rng
        self.turn_cap = turn_cap
        self.turn = 0
        self.events = [] if log_events else None
        self.players = []
        for i, deck in enumerate(decks):
            seat = f"{deck.name}#{i+1}" if sum(
                1 for d in decks if d.name == deck.name) > 1 else deck.name
            self.players.append(PlayerState(seat, deck, db, rng,
                                            profiles[i]))
        self.stats = {p.name: {} for p in self.players}
        self.winner = None
        self.reason = ""

    # ------------- infrastructure --------------------------------------
    def opponents_of(self, pl):
        return [p for p in self.players if p is not pl and not p.eliminated]

    def alive(self):
        return [p for p in self.players if not p.eliminated]

    def stat(self, pl, key, n=1):
        d = self.stats[pl.name]
        d[key] = d.get(key, 0) + n

    def log(self, event, **kw):
        if self.events is not None:
            self.events.append(dict(turn=self.turn, event=event, **kw))

    def no_lifegain(self, pl):
        return any("can't gain life" in (perm.card.oracle or "").lower()
                   for p in self.players for perm in p.battlefield)

    def check_eliminations(self):
        for p in self.players:
            if not p.eliminated and p.life <= 0:
                p.eliminated = True
                self.log("eliminated", player=p.name, life=p.life)

    # ------------- mulligan ----------------------------------------------
    def london_mulligan(self, pl):
        prof = pl.profile
        best_hand, best_score = None, -1
        for attempt in range(prof.max_mulligans + 1):
            pl.library.extend(pl.hand)
            pl.hand = []
            self.rng.shuffle(pl.library)
            pl.draw(7)
            score = prof.keep_score(pl, pl.hand)
            keep_size = 7 - attempt
            if score >= 4 or attempt == prof.max_mulligans:
                order = prof.bottom_priority(pl, pl.hand, keep_size)
                for c in order[:7 - keep_size]:
                    pl.hand.remove(c)
                    pl.library.insert(0, c)
                pl.mulligans = attempt
                self.stat(pl, "mulligans", attempt)
                return

    # ------------- reaction windows ---------------------------------------
    def reaction_counterspell(self, caster, spell_name, weight):
        """Opponents may counter a key spell (wipe or key weight >= 7)."""
        for opp in self.opponents_of(caster):
            for name in list(opp.hand):
                b = opp.card(name).behavior
                if not b.get("counterspell"):
                    continue
                cost = parse_cost(opp.card(name).mana_cost)
                if can_pay(cost, opp.sources(), opp.treasures):
                    res = pay(cost, opp.sources(), opp.treasures)
                    if res:
                        opp.treasures -= res[1]
                    opp.hand.remove(name)
                    opp.grave.append(name)
                    opp.cards_cast.add(name)
                    if b.get("burst_treasures"):
                        effects.create_treasures(self, opp, 3)
                    self.stat(opp, "counterspells_used")
                    self.log("counter", player=opp.name, spell=spell_name)
                    return True
        return False

    def reaction_protect(self, defender):
        """Heroic Intervention / Akroma's Will vs damage/destroy wipes."""
        for name in list(defender.hand):
            b = defender.card(name).behavior
            if not b.get("protect"):
                continue
            cost = parse_cost(defender.card(name).mana_cost)
            if can_pay(cost, defender.sources(), defender.treasures):
                res = pay(cost, defender.sources(), defender.treasures)
                if res:
                    defender.treasures -= res[1]
                defender.hand.remove(name)
                defender.grave.append(name)
                defender.cards_cast.add(name)
                self.stat(defender, "protection_saves")
                return True
        return False

    # ------------- targeted removal -----------------------------------------
    def best_removal_target(self, pl, scope, threshold):
        """(owner, permanent|'commander'|group) with highest key weight."""
        best = None
        for opp in self.opponents_of(pl):
            hexproof = opp.has("hexproof_grant")
            for perm in opp.battlefield:
                # threat = combo weight or raw combat size
                weight = max(perm.b("key", 0),
                             perm.eff_p() if perm.card.is_creature else 0)
                if weight < threshold:
                    continue
                is_creature = perm.card.is_creature
                if scope == "creature" and not is_creature:
                    continue
                if scope == "art_ench" and is_creature:
                    continue
                if scope == "cre_ench" and perm.card.is_artifact:
                    continue
                if hexproof and is_creature \
                        and not perm.b("hexproof_grant"):
                    continue
                if best is None or weight > best[2]:
                    best = (opp, perm, weight)
        return best

    def do_removal(self, pl, card):
        b = card.behavior
        scope = b.get("removal_scope", "any")
        targets = b.get("removal_targets", 1)
        acted = False
        for _ in range(targets):
            hit = self.best_removal_target(
                pl, scope, pl.profile.removal_key_threshold)
            if hit is None:
                break
            opp, perm, _ = hit
            exile = b.get("removal_exile", False)
            if perm.is_commander and b.get("removal_lock"):
                opp.battlefield.remove(perm)
                opp.cmd_locked = True
                self.stat(pl, "commander_locks")
                self.log("commander_lock", player=opp.name, by=card.name)
            else:
                effects.kill_permanent(self, opp, perm, exile=exile)
            self.stat(pl, "removal_used")
            acted = True
        return acted

    # ------------- wipes -------------------------------------------------
    def do_wipe(self, pl, card):
        w = card.behavior["wipe"]
        style = w.get("style")
        self.stat(pl, "wipes_cast")
        self.log("wipe", player=pl.name, card=card.name, style=style)
        if style == "counters":
            # Black Sun's Zenith style: EACH creature, own board included
            x = w.get("x", 3)
            for victim in self.players:
                if not victim.eliminated:
                    effects.mass_counters(self, pl, victim, x)
            return
        if style == "damage":
            dmg = w.get("dmg", 5)
            if w.get("divided"):
                opp = pl.profile.choose_effect_target(
                    pl, self.opponents_of(pl))
                if opp is None:
                    return
                budget = dmg
                for g in sorted(opp.tokens, key=lambda g: g.proto.t):
                    per = max(1, g.proto.t +
                              opp.anthem_for(g.proto.artifact))
                    k = min(g.n, budget // per)
                    effects.kill_tokens(self, opp, g, k)
                    budget -= k * per
                opp.tokens = [g for g in opp.tokens if g.n > 0]
                return
            for victim in self.players:
                if victim.eliminated:
                    continue
                if victim is not pl and self.reaction_protect(victim):
                    continue
                for g in list(victim.tokens):
                    if g.proto.t + victim.anthem_for(g.proto.artifact) <= dmg:
                        effects.kill_tokens(self, victim, g, g.n)
                victim.tokens = [g for g in victim.tokens if g.n > 0]
                for perm in list(victim.creatures()):
                    if perm.eff_t() <= dmg and \
                            "indestructible" not in perm.keywords():
                        effects.kill_permanent(self, victim, perm)
            return
        # select wipes (Farewell / Austere Command): hit opponents' boards,
        # spare own tokens
        exile = w.get("exile", False)
        for opp in self.opponents_of(pl):
            if self.reaction_protect(opp) and not exile:
                continue
            for perm in list(opp.battlefield):
                if perm.card.is_creature or perm.b("key", 0) >= 5:
                    effects.kill_permanent(self, opp, perm, exile=exile)
            for g in list(opp.tokens):
                effects.kill_tokens(self, opp, g, g.n)
            opp.tokens = []

    # ------------- casting ----------------------------------------------
    def _cast_priority(self, pl, name):
        card = pl.card(name)
        b = card.behavior
        dev = potential(pl.sources(), pl.treasures)
        if card.is_land:
            return 99
        # reactive spells are held for reaction windows - check FIRST so a
        # counterspell that also draws is not cast as a draw spell
        if b.get("counterspell") or b.get("protect"):
            return 97
        if b.get("rock_mana") or b.get("ramp_lands"):
            return 0 if dev < 7 else 6
        if b.get("tutor"):
            return 1
        if b.get("wipe"):
            # one-sided ('select') wipes fire on opposing board size alone;
            # symmetric wipes only when behind on board
            if b["wipe"].get("style") == "select":
                worst = max((o.total_power()
                             for o in self.opponents_of(pl)), default=0)
                return 1 if worst >= 8 else 98
            return 1 if pl.profile.should_wipe(pl, self) else 98
        if b.get("removal"):
            hit = self.best_removal_target(
                pl, b.get("removal_scope", "any"),
                pl.profile.removal_key_threshold)
            return 1 if hit else 98
        if b.get("doubler") or b.get("tokens_per_turn") \
                or b.get("mass_counters") or b.get("token_per_counter") \
                or b.get("chain") or b.get("steal") or b.get("clamp") \
                or b.get("populate_per_turn") or b.get("replicate") \
                or b.get("mechanized_wincon") or b.get("creature_token_mult"):
            return 2
        if b.get("anthem"):
            return 3
        if card.is_creature:
            return 4
        if b.get("draw_cards"):
            return 5
        if b.get("burst_drain"):
            return 5 if dev >= 8 else 96
        if b.get("burst_tokens") or b.get("burst_treasures"):
            return 5
        if b.get("recursion"):
            return 6
        return 90 if card.source == "stub" else 7

    def _choose_land(self, pl):
        lands = [c for c in pl.hand if pl.card(c).is_land]
        if not lands:
            return None
        have = set()
        for s in pl.sources():
            have |= set(s.colors)
        # pips needed across hand
        need = {}
        for c in pl.hand:
            for color, k in parse_cost(pl.card(c).mana_cost).pips.items():
                need[color] = need.get(color, 0) + k
        def score(name):
            colors = pl.card(name).b("land_colors") or {"C"}
            gain = sum(need.get(c, 0) for c in colors if c not in have)
            untapped = 0 if pl.card(name).b("enters_tapped") else 1
            return (gain, len(colors), untapped)
        return max(lands, key=score)

    def resolve_permanent_etb(self, pl, perm):
        b = perm.card.behavior
        if b.get("etb_tokens"):
            n, p, t, art = b["etb_tokens"]
            effects.create_tokens(self, pl, n, p, t, art,
                                  name=perm.name + "-tok")
        if b.get("igs"):
            multicolor = sum(1 for x in pl.battlefield
                             if x.card.is_multicolored)
            effects.create_tokens(self, pl, max(2, multicolor), 3, 3, True,
                                  name="robot")
        if b.get("etb_counter_wipe"):
            for opp in self.opponents_of(pl):
                effects.mass_counters(self, pl, opp, b["etb_counter_wipe"])
            if b.get("counter_wipe_self"):   # 'each creature' hits own board
                effects.mass_counters(self, pl, pl, b["etb_counter_wipe"])
        if b.get("etb_target_counters"):
            opp = pl.profile.choose_effect_target(pl, self.opponents_of(pl))
            if opp:
                effects.targeted_counters(self, pl, opp, 1)
        if b.get("etb_removal"):
            spec = b["etb_removal"]
            hit = self.best_removal_target(pl, spec.get("scope", "any"), 3)
            if hit:
                effects.kill_permanent(self, hit[0], hit[1],
                                       exile=spec.get("exile", False))
                self.stat(pl, "removal_used")
        if b.get("blink_on_etb"):
            for x in pl.battlefield:
                x.minus = 0
            self.stat(pl, "blink_resets")
        if b.get("grave_rob"):
            for opp in self.opponents_of(pl):
                dead = [n for n in opp.grave
                        if opp.card(n).is_creature]
                if dead:
                    opp.grave.remove(dead[0])
                    self.stat(pl, "grave_robs")
                    break
        if b.get("energy_gain"):
            effects.gain_energy(self, pl, b["energy_gain"])
        if b.get("proliferate") and not perm.card.is_land:
            effects.proliferate(self, pl, b["proliferate"])
        if "Spacecraft" in perm.card.subtypes:
            perm.station = 0

    def resolve_spell(self, pl, name, x_value=0):
        card = pl.card(name)
        b = card.behavior
        pl.cards_cast.add(name)
        types = card.types
        if "Creature" in types or "Artifact" in types \
                or "Enchantment" in types or "Planeswalker" in types \
                or "Battle" in types:
            perm = Permanent(card)
            pl.battlefield.append(perm)
            pl.make_rock_source(perm)
            self.resolve_permanent_etb(pl, perm)
            return
        # instants / sorceries
        if b.get("ramp_lands"):
            basics = [c for c in pl.library if pl.card(c).is_land
                      and not pl.card(c).b("enters_tapped")]
            for _ in range(b["ramp_lands"]):
                if basics:
                    land = basics.pop()
                    pl.library.remove(land)
                    perm = Permanent(pl.card(land))
                    pl.battlefield.append(perm)
                    pl.make_land_source(perm)
        if b.get("draw_cards"):
            pl.draw(b["draw_cards"])
        if b.get("burst_treasures"):
            n = b["burst_treasures"]
            if n == "lands":
                n = sum(1 for p in pl.battlefield if p.card.is_land)
            effects.create_treasures(self, pl, n)
        if b.get("burst_tokens"):
            n, p, t, art = b["burst_tokens"]
            effects.create_tokens(self, pl, n, p, t, art, name="burst")
        if b.get("removal"):
            self.do_removal(pl, card)
        if b.get("wipe"):
            self.do_wipe(pl, card)
        if b.get("burst_drain"):
            effects.drain(self, pl, max(0, x_value))
        if b.get("tutor"):
            for want in b["tutor"]:
                if want in pl.library and not any(
                        p.name == want for p in pl.battlefield):
                    pl.library.remove(want)
                    pl.hand.append(want)
                    break
        if b.get("recursion"):
            dead = [n for n in pl.grave if pl.card(n).is_creature]
            if dead:
                best = max(dead, key=lambda n: pl.card(n).power or 0)
                pl.grave.remove(best)
                perm = Permanent(pl.card(best))
                pl.battlefield.append(perm)
        pl.grave.append(name)

    def main_phase(self, pl, second=False):
        # land drop
        if not pl.land_played:
            land = self._choose_land(pl)
            if land:
                pl.hand.remove(land)
                perm = Permanent(pl.card(land))
                pl.battlefield.append(perm)
                pl.make_land_source(perm)
                if pl.card(land).b("energy_gain"):
                    effects.gain_energy(self, pl,
                                        pl.card(land).b("energy_gain"))
                pl.land_played = True
        # commander
        if pl.cmd_in_zone and not pl.cmd_locked:
            cost = pl.commander_cost()
            if cost and can_pay(cost, pl.sources(), pl.treasures):
                res = pay(cost, pl.sources(), pl.treasures)
                if res:
                    pl.treasures -= res[1]
                    pl.cmd_in_zone = False
                    card = pl.card(pl.commander)
                    perm = Permanent(card, is_commander=True)
                    pl.battlefield.append(perm)
                    pl.cards_cast.add(pl.commander)
                    self.resolve_permanent_etb(pl, perm)
                    self.log("commander", player=pl.name, card=pl.commander)
        # spells, priority order, respecting reactive mana reserve
        # (mana holding kicks in once the board is developed)
        reserve = pl.profile.reserve_for_reaction(pl) if self.turn >= 4 else 0
        progress = True
        guard = 0
        while progress and guard < 30:
            guard += 1
            progress = False
            hand = sorted(pl.hand, key=lambda c: (self._cast_priority(pl, c),
                                                  pl.card(c).mv))
            for name in hand:
                prio = self._cast_priority(pl, name)
                if prio >= 90:
                    continue
                card = pl.card(name)
                cost = parse_cost(card.mana_cost)
                x_value = 0
                if cost.has_x:
                    x_value = max(0, potential(pl.sources(), pl.treasures)
                                  - cost.mv - reserve)
                    if card.b("burst_drain") and x_value < 4:
                        continue
                    if card.b("wipe") and x_value < 2:
                        continue
                avail = potential(pl.sources(), pl.treasures)
                # honor the reactive-mana reserve only for low-impact spells;
                # board development always takes priority
                if reserve and prio >= 5 \
                        and avail - (cost.mv + x_value) < reserve:
                    continue
                if not can_pay(cost, pl.sources(), pl.treasures, x_value):
                    continue
                # reaction window for key spells
                weight = card.b("key", 0)
                if (card.b("wipe") or weight >= 7):
                    if self.reaction_counterspell(pl, name, weight):
                        pl.hand.remove(name)
                        pl.grave.append(name)
                        progress = True
                        break
                res = pay(cost, pl.sources(), pl.treasures, x_value)
                if not res:
                    continue
                pl.treasures -= res[1]
                pl.hand.remove(name)
                self.log("cast", player=pl.name, card=name) \
                    if card.b("key", 0) >= 5 or card.b("wipe") else None
                if card.b("wipe") and card.b("wipe").get("style") \
                        == "counters":
                    card.behavior["wipe"]["x"] = max(2, x_value) \
                        if parse_cost(card.mana_cost).has_x else \
                        card.behavior["wipe"].get("x", 3)
                self.resolve_spell(pl, name, x_value)
                self.check_eliminations()
                progress = True
                break

    # ------------- upkeep engines -----------------------------------------
    def upkeep(self, pl):
        # win check: Mechanized Production
        if pl.has("mechanized_wincon"):
            for g in pl.tokens:
                if g.proto.artifact and g.n >= 8:
                    return "mechanized"
            if pl.treasures >= 8:
                return "mechanized"
        # token engines
        n = pl.bsum("tokens_per_turn")
        if n:
            effects.create_tokens(self, pl, n, 1, 1, True, name="thopter",
                                  keywords=frozenset({"flying"}))
        for _ in range(int(pl.bsum("populate_per_turn"))):
            effects.populate(self, pl)
        if pl.has("replicate") and pl.tokens:
            effects.populate(self, pl)
        if pl.has("esix") and pl.tokens:
            best = max([p.eff_p() for p in pl.creatures()] + [3])
            g = pl.tokens[0]
            if g.n > 0:
                g.n -= 1
                effects.create_tokens(self, pl, 1, best, best, False,
                                      name="fractal", apply_mult=False)
        t = pl.bsum("treasures_per_turn")
        if t:
            effects.create_treasures(self, pl, t)
        # closet blink: reset counters, re-trigger best ETB
        closet = pl.has("closet")
        if closet and pl.battlefield:
            target = max((p for p in pl.battlefield
                          if p.card.is_creature or p.b("igs")),
                         key=lambda p: (p.minus,
                                        bool(p.b("etb_tokens")
                                             or p.b("igs"))),
                         default=None)
            if target is not None:
                target.minus = 0
                if target.b("etb_tokens") or target.b("igs"):
                    self.resolve_permanent_etb(pl, target)
                self.stat(pl, "blink_resets")
        # counter engines
        singles = int(pl.bsum("single_counters"))
        mass = int(pl.bsum("mass_counters"))
        mass_self = int(sum(p.b("mass_counters", 0) for p in pl.battlefield
                            if p.b("mass_self")))
        prolif = int(pl.bsum("proliferate"))
        opps = self.opponents_of(pl)
        if opps and (singles or mass or prolif):
            victim = pl.profile.choose_effect_target(pl, opps)
            if singles:
                effects.targeted_counters(self, pl, victim, singles)
            if mass:
                effects.mass_counters(self, pl, victim, mass)
            if mass_self:   # 'each (nonblack) creature' hits own tokens too
                for g in list(pl.tokens):
                    anthem = pl.anthem_for(g.proto.artifact)
                    if g.proto.t + anthem - g.wounded <= mass_self:
                        effects.kill_tokens(self, pl, g, g.n)
                    else:
                        g.wounded += mass_self
                pl.tokens = [g for g in pl.tokens if g.n > 0]
            if prolif:
                effects.proliferate(self, pl, prolif)
        # Yawgmoth: sac a token -> draw + counter
        if pl.has("yawgmoth") and pl.tokens and opps:
            g = pl.tokens[0]
            effects.kill_tokens(self, pl, g, 1)
            pl.tokens = [x for x in pl.tokens if x.n > 0]
            pl.draw(1)
            victim = pl.profile.choose_effect_target(pl, opps)
            effects.targeted_counters(self, pl, victim, 1)
        # Skullclamp: sac smallest token for cards
        if pl.has("clamp") and pl.tokens:
            g = min(pl.tokens, key=lambda g: g.proto.p)
            effects.kill_tokens(self, pl, g, 1)
            pl.tokens = [x for x in pl.tokens if x.n > 0]
            pl.draw(2)
        # station: charge spacecraft by tapping creatures (abstracted)
        for perm in pl.battlefield:
            if "Spacecraft" in perm.card.subtypes:
                perm.station += min(3, len(pl.creatures()) +
                                    min(2, pl.token_count()))
        # draw engines & energy
        d = min(int(pl.bsum("draw_per_turn")), 2)
        if d:
            pl.draw(d)
        effects.spend_energy(self, pl)
        self.check_eliminations()
        return None

    # ------------- turn/game loop ------------------------------------------
    def end_step(self, pl):
        while len(pl.hand) > MAX_HAND:
            order = pl.profile.bottom_priority(pl, pl.hand, MAX_HAND)
            pl.hand.remove(order[0])
            pl.grave.append(order[0])

    def untap(self, pl):
        pl.land_played = False
        for perm in pl.battlefield:
            perm.summoning_sick = False
            if perm.source:
                perm.source.tapped = False
        for g in pl.tokens:
            g.summoning_sick = False

    def game_over(self):
        alive = self.alive()
        if len(alive) <= 1:
            self.winner = alive[0] if alive else None
            self.reason = self.reason or "last standing"
            return True
        return False

    def run(self):
        for pl in self.players:
            self.london_mulligan(pl)
        for self.turn in range(1, self.turn_cap + 1):
            for i, pl in enumerate(self.players):
                if pl.eliminated:
                    continue
                self.untap(pl)
                res = self.upkeep(pl)
                if res == "mechanized":
                    pl_alive = [p for p in self.players if p is not pl]
                    for o in pl_alive:
                        o.eliminated = True
                    self.winner, self.reason = pl, "mechanized production"
                    return self.record()
                if not (self.turn == 1 and i == 0):
                    pl.draw()
                self.main_phase(pl)
                if self.turn >= 2:
                    combat.combat_phase(self, pl)
                self.main_phase(pl, second=True)
                self.end_step(pl)
                self.check_eliminations()
                if self.game_over():
                    return self.record()
        # turn cap: highest life among the living
        alive = self.alive()
        alive.sort(key=lambda p: -p.life)
        if len(alive) >= 2 and alive[0].life == alive[1].life:
            self.winner, self.reason = None, "draw at turn cap"
        else:
            self.winner = alive[0] if alive else None
            self.reason = "turn-cap life lead"
        return self.record()

    def record(self):
        return {
            "winner": self.winner.name if self.winner else "draw",
            "reason": self.reason,
            "turns": self.turn,
            "players": {
                p.name: {
                    "life": p.life,
                    "eliminated": p.eliminated,
                    "mulligans": p.mulligans,
                    "cards_cast": sorted(p.cards_cast),
                    **self.stats[p.name],
                } for p in self.players
            },
            "events": self.events,
        }
