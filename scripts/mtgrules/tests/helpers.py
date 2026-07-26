"""Test fixtures: minimal games with synthetic cards."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from mtgrules.game import Game
from mtgrules.objects import Characteristics, GameObject, Player, Zone
from mtgrules.policy import DefaultPolicy

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mtgrules.abilities import Ability

#: settle() gives up after this many SBA/trigger/resolve iterations
_SETTLE_GUARD = 200


def make_game(n_players: int = 2, seed: int = 7) -> Game:
    """Build a bare game with default policies and empty zones."""
    rng = random.Random(seed)
    players = [Player(f"p{i}") for i in range(n_players)]
    policies = {p.name: DefaultPolicy(rng) for p in players}
    return Game(players, rng, policies)


def creature(  # noqa: PLR0913 - test fixture: one keyword per Characteristics field, mirroring the dataclass
    game: Game,
    player: Player,
    name: str = "Bear",
    power: int = 2,
    toughness: int = 2,
    *,
    keywords: Iterable[str] = (),
    abilities: Iterable[Ability] = (),
    supertypes: Iterable[str] = (),
    subtypes: Iterable[str] = (),
    colors: Iterable[str] = (),
    types: Iterable[str] = ("Creature",),
    tapped: bool = False,
    entered_this_turn: bool = False,
) -> GameObject:
    """Put a synthetic creature onto the player's battlefield."""
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


def card_in_hand(  # noqa: PLR0913, PLR0917 - test fixture: one keyword per Characteristics field, mirroring the dataclass
    game: Game,
    player: Player,
    name: str = "Spell",
    mana_cost: str = "{1}",
    types: Iterable[str] = ("Sorcery",),
    abilities: Iterable[Ability] = (),
    power: int | None = None,
    toughness: int | None = None,
    subtypes: Iterable[str] = (),
) -> GameObject:
    """Put a synthetic card into the player's hand."""
    del game  # signature symmetry with creature()
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


def give_mana(player: Player, **mana: int) -> None:
    """Add floating mana to the player's pool, one keyword per type."""
    for t, n in mana.items():
        player.mana_pool.add(t, n)


def settle(game: Game) -> None:
    """Run SBAs + put triggers on stack + resolve everything."""
    guard = 0
    while True:
        guard += 1
        if guard >= _SETTLE_GUARD:
            msg = "settle() did not converge"
            raise AssertionError(msg)
        acted = game.check_state_based_actions()
        placed = game.put_triggers_on_stack()
        if game.stack:
            game.resolve_top()
            continue
        if not acted and not placed:
            return
