"""Test fixtures: minimal games with synthetic cards."""

from __future__ import annotations

import random

from ..game import Game
from ..objects import Characteristics, GameObject, Player, Zone
from ..policy import DefaultPolicy


def make_game(n_players=2, seed=7):
    rng = random.Random(seed)
    players = [Player(f"p{i}") for i in range(n_players)]
    policies = {p.name: DefaultPolicy(rng) for p in players}
    return Game(players, rng, policies)


def creature(
    game,
    player,
    name="Bear",
    power=2,
    toughness=2,
    *,
    keywords=(),
    abilities=(),
    supertypes=(),
    subtypes=(),
    colors=(),
    types=("Creature",),
    tapped=False,
    entered_this_turn=False,
):
    base = Characteristics(
        name=name,
        types=set(types),
        supertypes=set(supertypes),
        subtypes=set(subtypes),
        colors=set(colors),
        power=power,
        toughness=toughness,
        keywords=set(keywords),
        abilities=list(abilities),
    )
    obj = GameObject(base, player)
    obj.zone = Zone.BATTLEFIELD
    obj.controller = player
    obj.tapped = tapped
    obj.entered_this_turn = entered_this_turn
    player.battlefield.append(obj)
    game.bump()
    return obj


def card_in_hand(
    game,
    player,
    name="Spell",
    mana_cost="{1}",
    types=("Sorcery",),
    abilities=(),
    power=None,
    toughness=None,
    subtypes=(),
):
    base = Characteristics(
        name=name,
        mana_cost=mana_cost,
        types=set(types),
        subtypes=set(subtypes),
        power=power,
        toughness=toughness,
        abilities=list(abilities),
    )
    obj = GameObject(base, player)
    obj.zone = Zone.HAND
    player.hand.append(obj)
    return obj


def give_mana(player, **mana):
    for t, n in mana.items():
        player.mana_pool.add(t, n)


def settle(game):
    """Run SBAs + put triggers on stack + resolve everything."""
    guard = 0
    while True:
        guard += 1
        if guard >= 200:
            msg = "settle() did not converge"
            raise AssertionError(msg)
        acted = game.check_state_based_actions()
        placed = game.put_triggers_on_stack()
        if game.stack:
            game.resolve_top()
            continue
        if not acted and not placed:
            return
