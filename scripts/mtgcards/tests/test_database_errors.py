"""Card-data loading errors carry their path (fix C3).

A graph root with a broken or incompatible ``GRAPH-MANIFEST.json``, or a
``--custom-cards`` file that is not valid JSON, used to escape
``mtg-simulate`` as a traceback. Every such condition now surfaces as a
:class:`GraphSchemaError`/``ValueError``/``TypeError`` naming the
offending file; ``mtgrules.tests.test_cli`` checks that the CLI turns
them into a one-line message with exit code 1.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mtgcards.database import CardDatabase
from mtgcards.graph import GRAPH_SCHEMA, GraphSchemaError, load_manifest

_MANIFEST = {
    "graph_schema": GRAPH_SCHEMA,
    "graph_version": "2026-10-16",
    "ontology_iri": "urn:stklug84:MagicCardsOntology:2026-02-27#",
    "cr_version": "20260925",
    "requires_tools": ">=0.2.0",
    "sets": 0,
    "card_individuals": 0,
}


class _GraphRoot(unittest.TestCase):
    """A temporary graph root with no sets/ (every card would be a stub)."""

    def setUp(self) -> None:
        """Create the root; tests write their own manifest into it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.manifest = self.root / "GRAPH-MANIFEST.json"

    def write_manifest(self, text: str) -> None:
        """Write *text* verbatim as the root's manifest."""
        self.manifest.write_text(text, encoding="utf-8")


class TestManifestErrors(_GraphRoot):
    """load_manifest() reports every malformed manifest with its path."""

    def test_invalid_json_is_a_graph_schema_error(self) -> None:
        """A syntax error names the file instead of leaking JSONDecodeError."""
        self.write_manifest("{not json")
        with self.assertRaises(GraphSchemaError) as cm:
            load_manifest(self.root)
        self.assertIn(str(self.manifest), str(cm.exception))

    def test_wrong_field_type_is_a_graph_schema_error(self) -> None:
        """'graph_schema': '1.0' cannot be read as an integer."""
        self.write_manifest(json.dumps({**_MANIFEST, "graph_schema": "1.0"}))
        with self.assertRaises(GraphSchemaError) as cm:
            load_manifest(self.root)
        self.assertIn(str(self.manifest), str(cm.exception))

    def test_non_object_is_a_graph_schema_error(self) -> None:
        """A JSON array is not a manifest."""
        self.write_manifest("[1, 2]")
        with self.assertRaises(GraphSchemaError):
            load_manifest(self.root)

    def test_incompatible_schema_fails_database_construction(self) -> None:
        """A bundle from a newer tooling generation is refused with both versions."""
        self.write_manifest(json.dumps({**_MANIFEST, "graph_schema": GRAPH_SCHEMA + 1}))
        with self.assertRaises(GraphSchemaError) as cm:
            CardDatabase(self.root)
        self.assertIn(f"graph_schema {GRAPH_SCHEMA + 1}", str(cm.exception))
        self.assertIn(f"supports graph_schema {GRAPH_SCHEMA}", str(cm.exception))

    def test_valid_manifest_loads(self) -> None:
        """The happy path still works and exposes the manifest."""
        self.write_manifest(json.dumps(_MANIFEST))
        db = CardDatabase(self.root)
        if db.manifest is None:
            self.fail("manifest was not loaded")
        self.assertEqual(db.manifest.graph_version, "2026-10-16")


class TestCustomCardErrors(_GraphRoot):
    """The --custom-cards JSON layer reports its own file on errors."""

    def test_invalid_custom_json_names_the_file(self) -> None:
        """A syntax error in the custom file is a ValueError with its path."""
        custom = self.root / "custom.json"
        custom.write_text("{oops", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            CardDatabase(self.root, custom)
        self.assertIn(str(custom), str(cm.exception))

    def test_unknown_behavior_key_names_the_card(self) -> None:
        """An unknown behavior key is rejected with the card's name."""
        custom = self.root / "custom.json"
        custom.write_text(
            json.dumps({"Weirdo": {"mana_cost": "{1}", "behavior": {"bogus": 1}}}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as cm:
            CardDatabase(self.root, custom)
        self.assertIn("Weirdo", str(cm.exception))
        self.assertIn("bogus", str(cm.exception))

    def test_non_object_custom_json_is_rejected(self) -> None:
        """A top-level JSON array is not a card-definition file."""
        custom = self.root / "custom.json"
        custom.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(TypeError) as cm:
            CardDatabase(self.root, custom)
        self.assertIn(str(custom), str(cm.exception))
