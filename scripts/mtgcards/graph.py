"""Graph-bundle manifest: the knowledge-graph data contract.

The simulator reads card data from a *graph root* (``MTG_GRAPH_ROOT``),
which is either a magic-cards checkout or an unpacked **graph bundle**
published as a release artifact. A bundle carries ``GRAPH-MANIFEST.json``
declaring the graph schema version, the ontology IRI, the Comprehensive
Rules version its annotations were authored against, and the tools range
that can read it.

Only ``graph_schema`` is enforced. It versions the predicate vocabulary
that :mod:`mtgcards.ttl_loader`, :mod:`mtgcards.deck_ttl` and
:mod:`mtgcards.behaviors` extract, independently of which cards the graph
contains: bump it when that vocabulary changes, not when a set is added.
``requires_tools`` is informational only - enforcing a PEP 440 specifier
would need the ``packaging`` distribution, and these packages are
deliberately stdlib-only.

A graph root without a manifest (a plain checkout) is accepted unchanged,
so in-repo use is unaffected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mtgcards.version import DISTRIBUTION, tools_version

#: manifest file name at the top level of a graph bundle
MANIFEST_FILE = "GRAPH-MANIFEST.json"

#: schema version this release of the tooling writes
GRAPH_SCHEMA = 1

#: schema versions this release of the tooling can read
SUPPORTED_GRAPH_SCHEMA = frozenset({1})

#: ontology namespace assumed when a graph root carries no manifest
DEFAULT_ONTOLOGY_IRI = "urn:stklug84:MagicCardsOntology:2026-02-27#"


class GraphSchemaError(RuntimeError):
    """A graph bundle's schema is not supported by this tooling."""


@dataclass(frozen=True)
class GraphManifest:
    """Declared contract of one graph bundle."""

    #: predicate-vocabulary version (the enforced compatibility key)
    graph_schema: int
    #: content version of the bundle, e.g. "2026-08-09"
    graph_version: str
    #: ontology namespace every graph file binds as the empty prefix
    ontology_iri: str
    #: Comprehensive Rules edition, e.g. "20260619" (informational: the
    #: CR text is not redistributable and is fetched separately)
    cr_version: str
    #: tools range that can read this bundle (informational, see module
    #: docstring)
    requires_tools: str
    #: number of per-set card graphs in the bundle
    sets: int
    #: number of card individuals across those graphs
    card_individuals: int

    @classmethod
    def from_json(cls, data: dict[str, object]) -> GraphManifest:
        """Build a manifest from parsed ``GRAPH-MANIFEST.json`` data."""
        return cls(
            graph_schema=int(str(data.get("graph_schema", 0))),
            graph_version=str(data.get("graph_version", "")),
            ontology_iri=str(data.get("ontology_iri", DEFAULT_ONTOLOGY_IRI)),
            cr_version=str(data.get("cr_version", "")),
            requires_tools=str(data.get("requires_tools", "")),
            sets=int(str(data.get("sets", 0))),
            card_individuals=int(str(data.get("card_individuals", 0))),
        )

    def to_json(self) -> dict[str, object]:
        """Return the manifest as JSON-serializable data."""
        return {
            "graph_schema": self.graph_schema,
            "graph_version": self.graph_version,
            "ontology_iri": self.ontology_iri,
            "cr_version": self.cr_version,
            "requires_tools": self.requires_tools,
            "sets": self.sets,
            "card_individuals": self.card_individuals,
        }


def load_manifest(root: str | Path) -> GraphManifest | None:
    """Return the manifest of the graph root at *root*, if it has one.

    Returns None for a plain magic-cards checkout, which carries no
    manifest and is always considered compatible.
    """
    path = Path(root) / MANIFEST_FILE
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{path}: expected a JSON object, got {type(data).__name__}"
        raise GraphSchemaError(msg)
    return GraphManifest.from_json(data)


def check_compatible(manifest: GraphManifest | None, root: str | Path) -> None:
    """Raise :class:`GraphSchemaError` if *manifest* cannot be read.

    A None *manifest* (unversioned checkout) is always accepted.
    """
    if manifest is None:
        return
    if manifest.graph_schema in SUPPORTED_GRAPH_SCHEMA:
        return
    supported = ", ".join(str(v) for v in sorted(SUPPORTED_GRAPH_SCHEMA))
    requires = manifest.requires_tools or "(unspecified)"
    msg = (
        f"graph bundle at {root} declares graph_schema "
        f"{manifest.graph_schema} (graph version "
        f"{manifest.graph_version or 'unknown'}), but {DISTRIBUTION} "
        f"{tools_version()} supports graph_schema {supported}. "
        f"That bundle requires tools {requires}: upgrade {DISTRIBUTION}, "
        f"or pin a graph bundle matching this release."
    )
    raise GraphSchemaError(msg)


def ontology_iri(root: str | Path) -> str:
    """Return the ontology namespace declared by the graph root at *root*.

    Falls back to :data:`DEFAULT_ONTOLOGY_IRI` for an unversioned
    checkout.
    """
    manifest = load_manifest(root)
    return manifest.ontology_iri if manifest else DEFAULT_ONTOLOGY_IRI
