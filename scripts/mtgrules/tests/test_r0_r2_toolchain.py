"""R0 CR toolchain and R2 compiler conformance."""

import sys
import unittest
from pathlib import Path

from ..cr import get_cr, rule, RULE_IMPLEMENTATIONS
from ..compiler import compile_card, parse_effect_text
from ..abilities import SpellAbility, TriggeredAbility

REPO = Path(__file__).resolve().parent.parent.parent.parent


class TestCRToolchain(unittest.TestCase):
    def test_parse_counts(self):
        cr = get_cr()
        self.assertGreater(len(cr.rules), 3000)
        self.assertGreater(len(cr.glossary), 600)
        n_examples = sum(len(r.examples) for r in cr.rules.values())
        self.assertGreaterEqual(n_examples, 270)

    def test_known_rules_present(self):
        cr = get_cr()
        for n in ("100.1", "117.4", "601.2", "613.8", "614.1", "704.5g",
                  "702.19e", "903.10a"):
            self.assertIn(n, cr)

    def test_rule_decorator_validates(self):
        with self.assertRaises(ValueError):
            @rule("999.99z")
            def bogus():
                pass

    def test_engine_has_rule_annotations(self):
        # importing the engine modules populates the registry
        import mtgrules.game  # noqa: F401
        import mtgrules.combat  # noqa: F401
        import mtgrules.layers  # noqa: F401
        import mtgrules.replacements  # noqa: F401
        import mtgrules.turns  # noqa: F401
        for family in ("117", "601", "603", "704", "613", "614", "510"):
            self.assertTrue(
                any(n.startswith(family) for n in RULE_IMPLEMENTATIONS),
                f"no @rule annotations for CR family {family}")

    def test_examples_lookup(self):
        cr = get_cr()
        self.assertTrue(cr.examples_under("613"))


class _Ref:
    """Minimal CardData stand-in."""
    def __init__(self, name, oracle, types=("Creature",), mana_cost="{1}",
                 power=1, toughness=1):
        self.name = name
        self.oracle = oracle
        self.types = set(types)
        self.subtypes = set()
        self.supertypes = set()
        self.mana_cost = mana_cost
        self.power = power
        self.toughness = toughness
        self.loyalty = None
        self.color_identity = set()
        self.behavior = {}
        self.keywords = set()


class TestCompiler(unittest.TestCase):
    def test_keyword_line(self):
        ch = compile_card(_Ref("T", "Flying, vigilance\nWard {2}"))
        self.assertIn("flying", ch.keywords)
        self.assertIn("vigilance", ch.keywords)
        self.assertIn("ward:2", ch.keywords)

    def test_etb_trigger(self):
        ch = compile_card(_Ref(
            "T", "When this creature enters, draw a card."))
        trig = [a for a in ch.abilities if isinstance(a, TriggeredAbility)]
        self.assertEqual(len(trig), 1)

    def test_removal_spell_with_target(self):
        ch = compile_card(_Ref("Murder", "Destroy target creature.",
                               types=("Instant",)))
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        self.assertEqual(len(sa.targets), 1)
        self.assertEqual(sa.targets[0].what, "creature")

    def test_token_clause(self):
        eff, targets = parse_effect_text(
            "Create two 1/1 white Soldier creature tokens.", "T")
        from ..effects import CreateTokens
        self.assertIsInstance(eff, CreateTokens)
        self.assertEqual(eff.count, 2)
        self.assertEqual(eff.spec.subtypes, frozenset({"Soldier"}))
        self.assertEqual(eff.spec.colors, frozenset({"W"}))

    def test_unknown_clause_is_reported_not_dropped(self):
        from .. import compiler
        compiler.UNKNOWN_CLAUSES.pop("Weirdo", None)
        compile_card(_Ref("Weirdo",
                          "Whenever you flip a coin, untangle all webs."))
        self.assertIn("Weirdo", compiler.UNKNOWN_CLAUSES)

    def test_full_pool_compiles(self):
        sys.path.insert(0, str(REPO / "scripts"))
        from mtgcards.database import CardDatabase
        from mtgcards.deck import load_deck
        db = CardDatabase(REPO)
        names = set()
        for f in ("strategies/station-swarm-counter-deck.txt",
                  "strategies/blight-curse-deck.txt"):
            d = load_deck(REPO / f)
            names.update(d.cards)
            names.add(d.commander)
        for n in names:
            ch = compile_card(db.get(n))
            self.assertEqual(ch.name, db.get(n).name)


if __name__ == "__main__":
    unittest.main()
