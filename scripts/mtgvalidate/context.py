"""Shared validation context: graph roots, ontology IRI, file discovery.

Replaces the fixed ``ROOT``/``MC`` module constants the validators used
when they only ever ran inside this repository. A context is built from
one or more *graph roots*; a downstream repository typically passes two -
an unpacked graph bundle and its own checkout::

    ValidationContext.from_roots([".graph", "."])

The ontology IRI is taken from the first root carrying a bundle manifest
(see :mod:`mtgcards.graph`) and falls back to the canonical namespace for
a plain checkout, so the date-stamped IRI is no longer restated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rdflib import URIRef

from mtgcards.graph import DEFAULT_ONTOLOGY_IRI, load_manifest

if TYPE_CHECKING:
    from collections.abc import Sequence

#: directory names never scanned for graph or query files
SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    },
)


def _discover(roots: Sequence[Path], suffix: str) -> list[Path]:
    """Return every *suffix* file under *roots*, deduplicated and sorted.

    Roots may nest (a bundle unpacked inside a checkout), so files are
    deduplicated by resolved path to keep each one parsed exactly once.
    """
    seen: dict[Path, Path] = {}
    for root in roots:
        for path in root.rglob(f"*{suffix}"):
            if SKIP_DIRS.intersection(path.parts) or not path.is_file():
                continue
            seen.setdefault(path.resolve(), path)
    return sorted(seen.values())


@dataclass(frozen=True)
class ValidationContext:
    """Graph roots plus the ontology namespace the checks assert against."""

    #: graph roots, in precedence order (first wins for the manifest)
    roots: tuple[Path, ...]
    #: the ``mc:`` / empty-prefix namespace every graph file binds
    ontology_iri: str

    @classmethod
    def from_roots(cls, roots: Sequence[str | Path]) -> ValidationContext:
        """Build a context from *roots*, resolving the ontology IRI.

        Raises:
            FileNotFoundError: one of the roots does not exist.

        """
        resolved: list[Path] = []
        for entry in roots:
            path = Path(entry)
            if not path.is_dir():
                msg = f"{path}: graph root is not a directory"
                raise FileNotFoundError(msg)
            resolved.append(path)
        iri = DEFAULT_ONTOLOGY_IRI
        for path in resolved:
            manifest = load_manifest(path)
            if manifest is not None:
                iri = manifest.ontology_iri
                break
        return cls(roots=tuple(resolved), ontology_iri=iri)

    def ttl_files(self) -> list[Path]:
        """Return every Turtle file under the roots."""
        return _discover(self.roots, ".ttl")

    def rq_files(self) -> list[Path]:
        """Return every SPARQL query file under the roots."""
        return _discover(self.roots, ".rq")

    def find(self, name: str) -> Path | None:
        """Return the first root containing *name*, or None."""
        for root in self.roots:
            candidate = root / name
            if candidate.exists():
                return candidate
        return None

    def ref(self, local_name: str) -> URIRef:
        """Return the URIRef of *local_name* in the ontology namespace."""
        return URIRef(self.ontology_iri + local_name)

    def display(self, path: Path) -> str:
        """Return the shortest readable form of *path* for messages."""
        for root in self.roots:
            try:
                return path.relative_to(root).as_posix()
            except ValueError:
                continue
        return path.as_posix()
