"""txt decklist parsing (fix C4).

Exported lists come in more shapes than ``N Card Name``: Moxfield and
Archidekt write ``1x Card Name``, set codes in the printing suffix may be
lowercase, and a line the parser cannot read must not vanish silently -
it is recorded on the deck so the CLI can warn about it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtgcards.deck import load_deck


def _write(text: str) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".txt",
        delete=False,
        encoding="utf-8",
    ) as fh:
        fh.write(text)
    return Path(fh.name)


class TestCountForms(unittest.TestCase):
    """Every count spelling exported lists use."""

    def test_plain_and_x_suffixed_counts(self) -> None:
        """'1 X', '1x X' and '4X X' all expand to the stated copies."""
        path = _write(
            "// Commander\n1x Pia Nalaar, Chief Mechanic\n2 Sol Ring\n4X Forest\n",
        )
        self.addCleanup(path.unlink)
        deck = load_deck(path)
        self.assertEqual(deck.commander, "Pia Nalaar, Chief Mechanic")
        self.assertEqual(deck.cards, ["Sol Ring", "Sol Ring", *["Forest"] * 4])
        self.assertEqual(deck.skipped, [])

    def test_unparsable_lines_are_recorded_not_dropped(self) -> None:
        """Lines without a leading count are kept as (line number, text)."""
        path = _write(
            "// Commander\n"
            "1 Pia Nalaar, Chief Mechanic\n"
            "Sol Ring\n"  # no count
            "3\n"  # count without a name
            "two Forest\n"  # a word, not a number
            "1 Island\n",
        )
        self.addCleanup(path.unlink)
        deck = load_deck(path)
        self.assertEqual(deck.cards, ["Island"])
        self.assertEqual(deck.skipped, [(3, "Sol Ring"), (4, "3"), (5, "two Forest")])

    def test_comments_and_headers_are_not_skipped_lines(self) -> None:
        """'#' comments, blank lines and '//' headers are structure, not noise."""
        path = _write(
            "# exported by hand\n\n// Commander\n1 Pia Nalaar, Chief Mechanic\n"
            "// Mana rocks\n1 Sol Ring   # :SolRingAetherdriftCommander57\n",
        )
        self.addCleanup(path.unlink)
        deck = load_deck(path)
        self.assertEqual(deck.cards, ["Sol Ring"])
        self.assertEqual(deck.skipped, [])


class TestPrintingSuffix(unittest.TestCase):
    """The trailing '(SET) number [*F*]' of exported lists is stripped."""

    def test_lowercase_set_code(self) -> None:
        """'(lci) 26' is a printing suffix just like '(LCI) 26'."""
        path = _write(
            "1 Pia Nalaar, Chief Mechanic\n"
            "1 Sol Ring (lci) 26\n"
            "1 Forest (LCI) 300 *F*\n",
        )
        self.addCleanup(path.unlink)
        deck = load_deck(path)
        self.assertEqual(deck.cards, ["Sol Ring", "Forest"])

    def test_double_faced_card_separator(self) -> None:
        """Exported 'Front / Back' becomes the canonical 'Front // Back'."""
        path = _write(
            "1 Pia Nalaar, Chief Mechanic\n"
            "1 Delver of Secrets / Insectile Aberration\n",
        )
        self.addCleanup(path.unlink)
        deck = load_deck(path)
        self.assertEqual(deck.cards, ["Delver of Secrets // Insectile Aberration"])

    def test_first_card_is_the_commander_without_a_section(self) -> None:
        """Without '// Commander' the first listed card is promoted."""
        path = _write("1 Pia Nalaar, Chief Mechanic\n2 Sol Ring\n")
        self.addCleanup(path.unlink)
        deck = load_deck(path)
        self.assertEqual(deck.commander, "Pia Nalaar, Chief Mechanic")
        self.assertEqual(deck.cards, ["Sol Ring", "Sol Ring"])
        self.assertEqual(deck.size, 3)
