"""Fix F0: results must not depend on ``PYTHONHASHSEED``.

The engine used to iterate ``set``/``frozenset`` values of colour strings
(hybrid payment, generic payment, land mana abilities). CPython randomises
``str`` hashing per process, so the same ``--seed`` produced different
games in different processes. Colours now always iterate in the canonical
WUBRG(C) order (rule 105.1), guarded from two sides:

- unit tests on the canonical-order helper and on every payment path that
  chooses a colour from a set, and
- an end-to-end check that replays the same seeded games in subprocesses
  started with different ``PYTHONHASHSEED`` values and compares their
  recorded event streams byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess  # nosec B404 - spawns our own interpreter on a fixed module
import sys
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgrules.abilities import ActivatedAbility
from mtgrules.compiler import compile_card
from mtgrules.effects import AddMana
from mtgrules.game import _ManaPlan
from mtgrules.manasys import ManaPool, mana_order, parse_cost
from mtgrules.tests.helpers import FakeRef, creature, make_game

if TYPE_CHECKING:
    from mtgcards.database import CardDatabase
    from mtgcards.deck import Deck

_SCRIPTS = Path(__file__).resolve().parent.parent.parent
_REPO = _SCRIPTS.parent

#: hash seeds the end-to-end check replays the same games under
_HASH_SEEDS = ("0", "1", "3", "5")
_GAMES = 3
_TURN_CAP = 20
_SEED = 7


# ------------------------------------------------------------- unit tests


class TestCanonicalOrder(unittest.TestCase):
    """Every place that picks a colour from a set uses WUBRG(C) order."""

    def test_mana_order_is_wubrgc(self) -> None:
        """Sets come out in white, blue, black, red, green, colorless order."""
        self.assertEqual(mana_order({"G", "C", "W", "B"}), ["W", "B", "G", "C"])
        self.assertEqual(mana_order(frozenset("RUBGW")), list("WUBRG"))
        self.assertEqual(mana_order([]), [])

    def test_unknown_types_sort_last_alphabetically(self) -> None:
        """Anything outside WUBRGC follows, in a stable alphabetical order."""
        self.assertEqual(mana_order({"ANY", "C", "R", "A"}), ["R", "C", "A", "ANY"])

    def test_land_mana_ability_lists_colours_canonically(self) -> None:
        """A tri-land's compiled mana ability lists its colours in order."""
        ref = FakeRef(
            "Tri Land",
            types={"Land"},
            mana_cost="",
            power=None,
            toughness=None,
            behavior={"land_colors": {"R", "B", "U"}},
        )
        ch = compile_card(ref)
        mana = [
            a
            for a in ch.abilities
            if isinstance(a, ActivatedAbility) and a.is_mana_ability
        ]
        self.assertEqual(len(mana), 1)
        effect = mana[0].effect
        if not isinstance(effect, AddMana):
            self.fail(f"land mana ability has no AddMana effect: {effect!r}")
        self.assertEqual(effect.types, ("U", "B", "R"))

    def test_hybrid_payment_from_pool_prefers_canonical_colour_on_ties(self) -> None:
        """{W/U} paid from a pool holding one W and one U spends the W."""
        pool = ManaPool()
        pool.add("U")
        pool.add("W")
        self.assertTrue(pool.pay(parse_cost("{W/U}")))
        self.assertEqual((pool.mana["W"], pool.mana["U"]), (0, 1))

    def test_hybrid_payment_from_source_picks_canonical_colour(self) -> None:
        """A dual source covering {W/U} is tapped for W, never for U."""
        game = make_game()
        land = creature(game, game.players[0], "Dual", types=("Land",))
        ab = ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(types=("U", "W")),
        )
        plan = _ManaPlan(pool={}, avail=[(land, ab, frozenset("UW"), 1, None)])
        self.assertTrue(plan.cover_hybrid(parse_cost("{W/U}")))
        self.assertEqual([u[2] for u in plan.used], ["W"])

    def test_generic_payment_from_source_picks_canonical_colour(self) -> None:
        """A dual source tapped for generic mana produces its first colour."""
        game = make_game()
        land = creature(game, game.players[0], "Dual", types=("Land",))
        ab = ActivatedAbility(
            tap_cost=True,
            is_mana_ability=True,
            effect=AddMana(types=("G", "R")),
        )
        plan = _ManaPlan(pool={}, avail=[(land, ab, frozenset("GR"), 1, None)])
        self.assertTrue(plan.cover_generic(parse_cost("{1}")))
        self.assertEqual([u[2] for u in plan.used], ["R"])


# ------------------------------------------------- end-to-end hash check


def _fixture_decks(db: CardDatabase) -> list[Deck]:
    """Two mirror decks built deterministically from the public graph.

    Dual/tri lands, hybrid-cost spells, and cheap creatures: the cards whose
    mana handling iterated colour sets before F0.
    """
    # Deferred: only the subprocess driver needs the deck model.
    from mtgcards.deck import Deck  # noqa: PLC0415

    cards = {c.name: c for c in db.index.values()}

    def pick(pred: Any, n: int) -> list[str]:  # noqa: ANN401 - predicate over CardData
        return sorted(name for name, c in cards.items() if pred(c))[:n]

    duals = pick(
        lambda c: c.is_land and len(c.behavior.get("land_colors", ())) >= 2,  # noqa: PLR2004
        20,
    )
    hybrid = pick(lambda c: "/" in c.mana_cost and "P" not in c.mana_cost, 19)
    creatures = pick(
        lambda c: c.is_creature and 0 < c.mv <= 3 and "Legendary" not in c.supertypes,  # noqa: PLR2004
        40,
    )
    commander = pick(
        lambda c: c.is_creature and "Legendary" in c.supertypes and c.mv <= 4,  # noqa: PLR2004
        1,
    )[0]
    basics = [
        *["Plains"] * 4,
        *["Island"] * 3,
        *["Swamp"] * 3,
        *["Mountain"] * 3,
        *["Forest"] * 3,
    ]
    pool = [*duals, *basics, *hybrid, *creatures, "Sol Ring", "Arcane Signet"]
    return [
        Deck(
            name=f"Hash #{i}",
            path=f"hash-{i}.txt",
            cards=list(pool),
            commander=commander,
        )
        for i in (1, 2)
    ]


def stream_digest(
    seed: int = _SEED,
    games: int = _GAMES,
    turn_cap: int = _TURN_CAP,
) -> str:
    """SHA-256 over the recorded streams of *games* games sharing one RNG."""
    # Deferred: the engine and the card graph are only needed by the driver.
    from mtgcards.database import CardDatabase  # noqa: PLC0415
    from mtgrules.adapter import MatchOptions, run_game  # noqa: PLC0415
    from mtgviz.recorder import Recorder  # noqa: PLC0415

    db = CardDatabase(_REPO)
    decks = _fixture_decks(db)
    rng = random.Random(seed)
    digest = hashlib.sha256()
    for _ in range(games):
        records: list[dict[str, Any]] = []
        rec = run_game(
            decks,
            db,
            rng,
            MatchOptions(turn_cap=turn_cap),
            recorder=Recorder(records.append),
        )
        digest.update(json.dumps(records, default=str).encode())
        digest.update(json.dumps(rec, default=str).encode())
    return digest.hexdigest()


def main() -> None:
    """Subprocess entry point: print the stream digest."""
    print(stream_digest())  # noqa: T201 - the subprocess protocol is stdout


def _digest_under_hash_seed(hash_seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONDONTWRITEBYTECODE": "1"}
    driver = "from mtgrules.tests import test_determinism as t; t.main()"
    proc = subprocess.run(  # noqa: S603 - fixed argv: our own interpreter and module  # nosec B603
        [sys.executable, "-c", driver],
        cwd=_SCRIPTS,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.stdout.strip()


class TestHashSeedIndependence(unittest.TestCase):
    """The same seeded games produce identical streams in every process."""

    def test_streams_identical_across_hash_seeds(self) -> None:
        """Replaying under several PYTHONHASHSEED values gives one digest."""
        digests = {hs: _digest_under_hash_seed(hs) for hs in _HASH_SEEDS}
        self.assertEqual(
            len(set(digests.values())),
            1,
            f"streams differ across PYTHONHASHSEED values: {digests}",
        )


if __name__ == "__main__":
    main()
