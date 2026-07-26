"""R0 CR toolchain and R2 compiler conformance."""

from __future__ import annotations

import unittest
from pathlib import Path

from mtgrules.abilities import SpellAbility, TriggeredAbility
from mtgrules.compiler import UNKNOWN_CLAUSES, compile_card, parse_effect_text
from mtgrules.cr import RULE_IMPLEMENTATIONS, get_cr, rule
from mtgrules.effects import CreateTokens

REPO = Path(__file__).resolve().parent.parent.parent.parent

#: sanity floors for the parsed CR index (the real file is far larger)
_MIN_RULES = 3000
_MIN_GLOSSARY = 600
_MIN_EXAMPLES = 270


class TestCRToolchain(unittest.TestCase):
    """The CR parser, index, and @rule traceability decorator."""

    def test_parse_counts(self) -> None:
        """The parsed index has plausible rule/glossary/example counts."""
        cr = get_cr()
        self.assertGreater(len(cr.rules), _MIN_RULES)
        self.assertGreater(len(cr.glossary), _MIN_GLOSSARY)
        n_examples = sum(len(r.examples) for r in cr.rules.values())
        self.assertGreaterEqual(n_examples, _MIN_EXAMPLES)

    def test_known_rules_present(self) -> None:
        """Rules the engine relies on exist in the parsed CR."""
        cr = get_cr()
        for n in (
            "100.1",
            "117.4",
            "601.2",
            "613.8",
            "614.1",
            "704.5g",
            "702.19e",
            "903.10a",
        ):
            self.assertIn(n, cr)

    def test_rule_decorator_validates(self) -> None:
        """@rule rejects unknown rule numbers at import time."""
        with self.assertRaises(ValueError):

            @rule("999.99z")
            def bogus() -> None:
                """Never registered."""

    def test_engine_has_rule_annotations(self) -> None:
        """Importing the engine populates the @rule registry."""
        # Deferred: the imports exist purely for their registration side
        # effect inside this test. RUF100 is listed because PLC0415 is
        import mtgrules.combat  # noqa: PLC0415
        import mtgrules.game  # noqa: PLC0415
        import mtgrules.layers  # noqa: PLC0415
        import mtgrules.replacements  # noqa: PLC0415
        import mtgrules.turns  # noqa: F401, PLC0415

        for family in ("117", "601", "603", "704", "613", "614", "510"):
            self.assertTrue(
                any(n.startswith(family) for n in RULE_IMPLEMENTATIONS),
                f"no @rule annotations for CR family {family}",
            )

    def test_examples_lookup(self) -> None:
        """examples_under() finds worked examples for a rule family."""
        cr = get_cr()
        self.assertTrue(cr.examples_under("613"))


class _Ref:
    """Minimal CardData stand-in."""

    def __init__(
        self,
        name: str,
        oracle: str,
        types: tuple[str, ...] = ("Creature",),
        mana_cost: str = "{1}",
    ) -> None:
        """Fill the CardRef fields the compiler reads."""
        self.name = name
        self.oracle = oracle
        self.types = set(types)
        self.subtypes: set[str] = set()
        self.supertypes: set[str] = set()
        self.mana_cost = mana_cost
        self.power: int | None = 1
        self.toughness: int | None = 1
        self.loyalty: int | None = None
        self.color_identity: set[str] = set()
        self.behavior: dict[str, object] = {}
        self.keywords: set[str] = set()


class TestCompiler(unittest.TestCase):
    """The oracle-text compiler over synthetic and real cards."""

    def test_keyword_line(self) -> None:
        """A keywords-only line lands in Characteristics.keywords."""
        ch = compile_card(_Ref("T", "Flying, vigilance\nWard {2}"))
        self.assertIn("flying", ch.keywords)
        self.assertIn("vigilance", ch.keywords)
        self.assertIn("ward:2", ch.keywords)

    def test_etb_trigger(self) -> None:
        """An ETB draw line compiles to one TriggeredAbility."""
        ch = compile_card(_Ref("T", "When this creature enters, draw a card."))
        trig = [a for a in ch.abilities if isinstance(a, TriggeredAbility)]
        self.assertEqual(len(trig), 1)

    def test_removal_spell_with_target(self) -> None:
        """'Destroy target creature.' compiles with one target spec."""
        ch = compile_card(
            _Ref("Murder", "Destroy target creature.", types=("Instant",)),
        )
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        self.assertEqual(len(sa.targets), 1)
        self.assertEqual(sa.targets[0].what, "creature")

    def test_token_clause(self) -> None:
        """A create-tokens clause parses count, subtype, and color."""
        eff, _targets = parse_effect_text(
            "Create two 1/1 white Soldier creature tokens.",
            "T",
        )
        self.assertIsInstance(eff, CreateTokens)
        if not isinstance(eff, CreateTokens):  # pragma: no cover - narrowed
            raise TypeError(eff)
        self.assertEqual(eff.count, 2)
        self.assertEqual(eff.spec.subtypes, frozenset({"Soldier"}))
        self.assertEqual(eff.spec.colors, frozenset({"W"}))

    def test_unknown_clause_is_reported_not_dropped(self) -> None:
        """Uncompiled clauses land in UNKNOWN_CLAUSES."""
        UNKNOWN_CLAUSES.pop("Weirdo", None)
        compile_card(_Ref("Weirdo", "Whenever you flip a coin, untangle all webs."))
        self.assertIn("Weirdo", UNKNOWN_CLAUSES)

    def test_full_pool_compiles(self) -> None:
        """Every card of the repo deck pool compiles."""
        # Deferred: loading the knowledge graph is expensive and only
        # this test needs it. RUF100: PLC0415 is still globally ignored.
        from mtgcards.database import CardDatabase  # noqa: PLC0415
        from mtgcards.deck import load_deck  # noqa: PLC0415

        db = CardDatabase(REPO)
        names: set[str | None] = set()
        for f in (
            "strategies/station-swarm-counter-deck.txt",
            "strategies/blight-curse-deck.txt",
        ):
            d = load_deck(REPO / f)
            names.update(d.cards)
            names.add(d.commander)
        for n in names:
            if n is None:
                continue
            ch = compile_card(db.get(n))
            self.assertEqual(ch.name, db.get(n).name)


if __name__ == "__main__":
    unittest.main()
