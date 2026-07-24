"""Token creation, -1/-1 counter mechanics, proliferate/energy/populate,
aristocrat drains, deaths and theft."""

from __future__ import annotations

from .state import Permanent, TokenGroup, TokenProto


# ---------------------------------------------------------------------------
# drains & life
# ---------------------------------------------------------------------------

def drain(game, pl, amount, key="drain"):
    """pl drains `amount` from each opponent (aristocrat style caps to 1
    target in duels via each-opponent semantics)."""
    if amount <= 0:
        return
    gained = 0
    for opp in game.opponents_of(pl):
        opp.life -= amount
        gained += amount
        game.stat(opp, "drained_taken", amount)
    if not game.no_lifegain(pl):
        pl.life += min(gained, amount * 2)
    game.stat(pl, key, amount)


def on_deaths(game, pl, count=1):
    """count creatures of pl died: trigger own drain engines and opposing
    Blood Artist style triggers."""
    if count <= 0:
        return
    own = pl.drain_engines() * count
    if own:
        drain(game, pl, own)
    for opp in game.opponents_of(pl):
        ba = opp.blood_artists() * count
        if ba:
            drain(game, opp, ba)


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------

def create_tokens(game, pl, n, p, t, artifact=False, name="token",
                  keywords=frozenset(), apply_mult=True, is_creature=True):
    if n <= 0:
        return 0
    if apply_mult:
        n *= pl.token_multiplier(creature=is_creature)
    proto = TokenProto(p, t, name, artifact, frozenset(keywords))
    for g in pl.tokens:
        if g.proto == proto and g.wounded == 0:
            g.n += n
            break
    else:
        pl.tokens.append(TokenGroup(proto, n))
    game.stat(pl, "tokens_created", n)
    dr = min(pl.bsum("draw_on_tokens"), 2)
    if dr:
        pl.draw(dr)
    return n


def create_treasures(game, pl, n):
    if n <= 0:
        return
    mult = 1
    for perm in pl.battlefield:
        if perm.b("doubler"):
            mult *= perm.b("doubler")
        if perm.b("manufactor"):
            mult *= 2
    pl.treasures += n * min(mult, 8)
    game.stat(pl, "treasures_made", n * min(mult, 8))


def populate(game, pl):
    """Copy the best creature token."""
    best = None
    for g in pl.tokens:
        if best is None or g.proto.p > best.proto.p:
            best = g
    if best:
        create_tokens(game, pl, 1, best.proto.p, best.proto.t,
                      best.proto.artifact, best.proto.name,
                      best.proto.keywords)


def kill_tokens(game, pl, group, n):
    n = min(n, group.n)
    if n <= 0:
        return 0
    group.n -= n
    game.stat(pl, "tokens_killed", n)
    on_deaths(game, pl, n)
    return n


# ---------------------------------------------------------------------------
# nontoken deaths, theft, recursion hooks
# ---------------------------------------------------------------------------

def kill_permanent(game, pl, perm: Permanent, exile=False, to_zone=True):
    if perm not in pl.battlefield:
        return
    pl.battlefield.remove(perm)
    was_creature = perm.card.is_creature
    if was_creature:
        on_deaths(game, pl, 1)
        dt = perm.b("death_tokens")
        if dt:
            n, p, t, art = dt
            create_tokens(game, pl, n, p, t, art,
                          name=perm.name + "-spawn", apply_mult=True)
            if perm.b("reef"):
                # Reef Worm chain approximated: spawn remembers next stage
                create_tokens(game, pl, 0, 6, 6, False, apply_mult=False)
    # commander goes back to the command zone unless locked away
    if perm.is_commander:
        pl.cmd_in_zone = True
        pl.cmd_tax += 2
        return
    # Necroskitter: opponents steal nontoken creatures dying with -1/-1
    if was_creature and not exile and perm.minus > 0:
        for opp in game.opponents_of(pl):
            if opp.has("steal") and not opp.eliminated:
                stolen = Permanent(perm.card)
                stolen.summoning_sick = True
                opp.battlefield.append(stolen)
                game.stat(opp, "necroskitter_steals", 1)
                game.log("steal", player=opp.name, card=perm.name)
                return
    if perm.b("rebuild") and not exile:      # Reassembling Skeleton
        pl.hand.append(perm.name)
        return
    (pl.exile if exile else pl.grave).append(perm.name)


# ---------------------------------------------------------------------------
# -1/-1 counters
# ---------------------------------------------------------------------------

def _blowfly_chain(game, src, victim, initial_deaths):
    """Blowfly Infestation: deaths with counters spray more counters."""
    if not src.has("chain") or initial_deaths <= 0:
        return
    extra = initial_deaths
    guard = 0
    while extra > 0 and guard < 50:
        guard += 1
        target = None
        for g in victim.tokens:
            anthem = victim.anthem_for(g.proto.artifact)
            if g.n > 0 and g.proto.t + anthem - g.wounded <= 1:
                target = g
                break
        if target is None:
            for g in victim.tokens:
                if g.n > 0:
                    g.wounded += 1
                    extra -= 1
                    break
            else:
                break
            continue
        kill_tokens(game, victim, target, 1)
        game.stat(victim, "blowfly_chain_kills", 1)
        # a new death keeps the chain going at parity; decrement to converge
        extra -= 1
    victim.tokens = [g for g in victim.tokens if g.n > 0]


def mass_counters(game, src, victim, n):
    """Put n -1/-1 counters on each creature victim controls."""
    if n <= 0:
        return 0
    placed = 0
    deaths = 0
    for g in list(victim.tokens):
        anthem = victim.anthem_for(g.proto.artifact)
        placed += g.n * n
        if g.proto.t + anthem - g.wounded <= n:
            deaths += kill_tokens(game, victim, g, g.n)
        else:
            g.wounded += n
    for perm in list(victim.creatures()):
        perm.minus += n
        placed += n
        if perm.eff_t() <= 0:
            kill_permanent(game, victim, perm)
            deaths += 1
    victim.tokens = [g for g in victim.tokens if g.n > 0]
    _blowfly_chain(game, src, victim, deaths)
    game.stat(src, "counters_placed", placed)
    token_payoffs(game, src, placed)
    return placed


def targeted_counters(game, src, victim, shots):
    """Single -1/-1 counters aimed at nontoken creatures first (to set up
    Necroskitter / Kulrath), then tokens."""
    placed = 0
    hexproof = victim.has("hexproof_grant")
    for _ in range(shots):
        target = None
        if not hexproof:
            cands = [p for p in victim.creatures()]
            cands.sort(key=lambda p: -p.eff_p())
            target = cands[0] if cands else None
        else:
            target = next((p for p in victim.creatures()
                           if p.b("hexproof_grant")), None)
        if target is not None:
            target.minus += 1
            placed += 1
            if target.eff_t() <= 0:
                kill_permanent(game, victim, target)
                _blowfly_chain(game, src, victim, 1)
            continue
        for g in victim.tokens:
            anthem = victim.anthem_for(g.proto.artifact)
            placed += 1
            if g.proto.t + anthem - g.wounded <= 1:
                kill_tokens(game, victim, g, 1)
                _blowfly_chain(game, src, victim, 1)
            else:
                g.wounded += 1
            break
    victim.tokens = [g for g in victim.tokens if g.n > 0]
    game.stat(src, "counters_placed", placed)
    token_payoffs(game, src, placed)
    return placed


def proliferate(game, pl, times=1):
    """Add a counter of each kind already present (chooses only what helps
    pl: opposing -1/-1 counters, own station/+1 counters)."""
    for _ in range(times):
        acted = False
        for opp in game.opponents_of(pl):
            for perm in opp.creatures():
                if perm.minus > 0:
                    perm.minus += 1
                    acted = True
                    if perm.eff_t() <= 0:
                        kill_permanent(game, opp, perm)
            for g in opp.tokens:
                if g.wounded > 0:
                    anthem = opp.anthem_for(g.proto.artifact)
                    if g.proto.t + anthem - g.wounded <= 1:
                        kill_tokens(game, opp, g, 1)
                    else:
                        g.wounded += 1
                    acted = True
            opp.tokens = [g for g in opp.tokens if g.n > 0]
        for perm in pl.battlefield:
            if perm.station > 0:
                perm.station += 1
                acted = True
        if acted:
            game.stat(pl, "proliferates", 1)


def token_payoffs(game, pl, counters_placed):
    """Hapatra / Nest of Scarabs / Flourishing Defenses / Obelisk Spider."""
    if counters_placed <= 0:
        return
    engines = sum(1 for p in pl.battlefield if p.b("token_per_counter"))
    if engines:
        create_tokens(game, pl, min(counters_placed * engines, 10), 1, 1,
                      False, name="snake", keywords=frozenset({"deathtouch"}))
    if any(p.b("drain_on_counters") for p in pl.battlefield):
        drain(game, pl, min(counters_placed, 3))


# ---------------------------------------------------------------------------
# energy
# ---------------------------------------------------------------------------

def gain_energy(game, pl, n):
    pl.energy += n
    game.stat(pl, "energy_gained", n)


def spend_energy(game, pl):
    """Whirler Virtuoso style: 3 energy -> thopter, while payoffs exist."""
    if not pl.has("energy_thopter"):
        return
    while pl.energy >= 3:
        pl.energy -= 3
        create_tokens(game, pl, 1, 1, 1, True, name="thopter",
                      keywords=frozenset({"flying"}))
