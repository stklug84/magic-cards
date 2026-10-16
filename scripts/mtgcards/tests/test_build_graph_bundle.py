"""Byte-stability of the knowledge-graph bundle.

release-graph.yml publishes whatever one build produced, so nothing in
the pipeline notices when two builds of the same tree differ - yet the
release docs promise a byte-stable tarball, and that promise is what lets
anyone compare a published bundle against a rebuild. These tests pin the
normalization that makes it true: member metadata and the gzip header
derive from the graph version, never from the clock or the checkout.

The builder is a script next to the packages in scripts/, so this module
only runs from a checkout (``python3 -m unittest discover -s
mtgcards/tests -t .`` in scripts/); from an installed wheel it skips.
"""

from __future__ import annotations

import importlib
import os
import struct
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def _import_builder() -> ModuleType | None:
    """Return build_graph_bundle when it is importable, else None."""
    try:
        return importlib.import_module("build_graph_bundle")
    except ModuleNotFoundError:
        return None


_BUILDER = _import_builder()

_GRAPH_VERSION = "2026-10-16"
_STAMP = int(datetime(2026, 10, 16, tzinfo=UTC).timestamp())

#: a different mtime for every source file on the second build
_OTHER_MTIME = int(datetime(2001, 2, 3, tzinfo=UTC).timestamp())
#: a group-writable mode a checkout with a relaxed umask would carry
_OTHER_MODE = 0o664

#: byte offset and layout of the MTIME field in a gzip header (RFC 1952)
_GZIP_MTIME = slice(4, 8)
#: FLG bit set when the header embeds the original file name
_GZIP_FNAME = 0x08

_ONTOLOGY = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
<urn:test:MagicCardsOntology#> rdf:type owl:Ontology .
"""

_SET = """\
:Forest :cardName "Forest" .
:Island :cardName "Island" .
"""

_PYPROJECT = """\
[project]
name = "magic-cards-tools"
version = "0.2.0"
"""


def _write_graph(root: Path) -> list[Path]:
    """Lay out the smallest tree the builder accepts; return its files."""
    files = {
        "MagicCardsOntology.ttl": _ONTOLOGY,
        "MagicCardIndividuals.ttl": "# aggregator\n",
        "MagicCardCollection.ttl": "# inventory\n",
        "MagicSimulationAnnotations.ttl": "# hooks\n",
        "sets/TestSet.ttl": _SET,
        "queries/00_example.rq": "SELECT * WHERE { ?s ?p ?o }\n",
        "pyproject.toml": _PYPROJECT,
    }
    written: list[Path] = []
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


class TestBundleStability(unittest.TestCase):
    """The tarball depends on the tree and the graph version alone."""

    def setUp(self) -> None:
        """Build the same synthetic graph twice under different conditions."""
        if _BUILDER is None:
            self.skipTest("build_graph_bundle.py is only importable from scripts/")
        self.builder = _BUILDER
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "graph"
        sources = _write_graph(self.root)
        manifest = self.builder.build_manifest(self.root, _GRAPH_VERSION)
        self.manifest = manifest

        self.first = self.builder.write_bundle(
            self.root, Path(tmp.name) / "first", manifest
        )
        # a second checkout: other mtimes, a relaxed umask
        for path in sources:
            os.utime(path, (_OTHER_MTIME, _OTHER_MTIME))
            path.chmod(_OTHER_MODE)
        self.second = self.builder.write_bundle(
            self.root, Path(tmp.name) / "second", manifest
        )

    def test_manifest_counts_the_synthetic_graph(self) -> None:
        """The manifest sees one set file with two card individuals."""
        self.assertEqual(self.manifest.sets, 1)
        self.assertEqual(self.manifest.card_individuals, 2)
        self.assertEqual(self.manifest.requires_tools, ">=0.2.0")

    def test_two_builds_are_byte_identical(self) -> None:
        """Touching and chmod-ing every source does not change a byte."""
        self.assertEqual(self.first.read_bytes(), self.second.read_bytes())

    def test_members_are_normalized(self) -> None:
        """Owner, mode and mtime of every member come from the builder."""
        with tarfile.open(self.first) as tar:
            members = tar.getmembers()
        self.assertTrue(members)
        for info in members:
            with self.subTest(member=info.name):
                self.assertEqual((info.uid, info.gid), (0, 0))
                self.assertEqual((info.uname, info.gname), ("root", "root"))
                self.assertEqual(info.mode, self.builder.MEMBER_MODE)
                self.assertEqual(info.mtime, _STAMP)

    def test_gzip_header_is_pinned(self) -> None:
        """The gzip header carries the graph date and no file name."""
        header = self.first.read_bytes()[:10]
        (mtime,) = struct.unpack("<I", header[_GZIP_MTIME])
        self.assertEqual(mtime, _STAMP)
        self.assertEqual(header[3] & _GZIP_FNAME, 0)

    def test_stamp_is_midnight_utc_of_the_graph_date(self) -> None:
        """bundle_stamp maps the version date to its UTC midnight."""
        self.assertEqual(self.builder.bundle_stamp(_GRAPH_VERSION), _STAMP)

    def test_stamp_rejects_a_version_that_is_not_a_date(self) -> None:
        """A tag that release-graph.yml would reject is rejected here too."""
        with self.assertRaises(ValueError):
            self.builder.bundle_stamp("latest")


if __name__ == "__main__":
    unittest.main()
