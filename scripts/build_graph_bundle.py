#!/usr/bin/env python3
"""Build the distributable knowledge-graph bundle.

The bundle is the data half of the published interface (the
``magic-cards-tools`` wheel is the code half): a versioned, immutable
tarball that downstream repositories unpack and point ``MTG_GRAPH_ROOT``
at, instead of checking out this repository.

It carries only what a consumer actually reads:

  * ``sets/*.ttl``                  card individuals (the simulator's data)
  * ``MagicSimulationAnnotations.ttl``  behavior hooks / threat weights
  * ``MagicCardsOntology.ttl``      TBox, required by the validators
  * ``MagicCardIndividuals.ttl``    owl:imports aggregator over sets/
  * ``MagicCardCollection.ttl``     inventory, for collection-scoped queries
  * ``queries/``                    the reusable SPARQL catalog
  * ``GRAPH-MANIFEST.json``         the contract (see mtgcards.graph)

``MagicCardIndividuals.ttl`` is included even though the simulator never
reads it (ttl_loader globs ``sets/`` directly): MagicCardCollection.ttl
imports it, so leaving it out makes the bundle fail its own owl:imports
resolution check.

Deliberately excluded: ``MagicCardSynergies.ttl`` (no consumer),
``collection.csv`` (private, untracked) and ``MagicCompRules-*.txt`` (not
redistributable; consumers fetch it from Wizards using the manifest's
``cr_version``).

Usage:
  python3 scripts/build_graph_bundle.py --graph-version 2026-08-09
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgcards.graph import GRAPH_SCHEMA, MANIFEST_FILE, GraphManifest

#: repository root (this script lives in scripts/)
ROOT = Path(__file__).resolve().parent.parent

#: distribution name prefix of the produced tarball
BUNDLE_NAME = "magic-cards-graph"

#: files and directories copied into the bundle, in bundle order
BUNDLE_PATHS = (
    "MagicCardsOntology.ttl",
    "MagicCardIndividuals.ttl",
    "MagicCardCollection.ttl",
    "MagicSimulationAnnotations.ttl",
    "sets",
    "queries",
)

_ONTOLOGY_RE = re.compile(r"<(urn:[^>]+)>\s+(?:rdf:type|a)\s+owl:Ontology")
_CARD_NAME_RE = re.compile(r":cardName\s+\"")
_CR_RE = re.compile(r"^MagicCompRules-(\d+)\.txt$")


def ontology_iri(root: Path) -> str:
    """Return the ontology IRI declared by MagicCardsOntology.ttl."""
    path = root / "MagicCardsOntology.ttl"
    match = _ONTOLOGY_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        msg = f"{path}: no owl:Ontology declaration found"
        raise ValueError(msg)
    return match.group(1)


def cr_version(root: Path) -> str:
    """Return the Comprehensive Rules edition present in *root*, if any.

    The CR text is gitignored and not redistributed; it is only used here
    to record which edition the simulation annotations were authored
    against. Returns an empty string when no CR file is present.
    """
    editions = sorted(
        match.group(1)
        for path in root.glob("MagicCompRules-*.txt")
        if (match := _CR_RE.match(path.name))
    )
    return editions[-1] if editions else ""


def graph_counts(root: Path) -> tuple[int, int]:
    """Return (number of per-set graphs, number of card individuals)."""
    set_files = sorted((root / "sets").glob("*.ttl"))
    cards = sum(
        len(_CARD_NAME_RE.findall(path.read_text(encoding="utf-8")))
        for path in set_files
    )
    return len(set_files), cards


def project_version(root: Path) -> str:
    """Return the magic-cards-tools version from pyproject.toml."""
    with (root / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    project = data["project"]
    return str(project["version"])


def build_manifest(root: Path, graph_version: str) -> GraphManifest:
    """Assemble the bundle manifest for the graph in *root*.

    ``requires_tools`` records a lower bound only: a bundle that is *too
    new* for a consumer is caught by the ``graph_schema`` check in
    mtgcards.graph, so an upper bound would add nothing but staleness.
    """
    sets, cards = graph_counts(root)
    return GraphManifest(
        graph_schema=GRAPH_SCHEMA,
        graph_version=graph_version,
        ontology_iri=ontology_iri(root),
        cr_version=cr_version(root),
        requires_tools=f">={project_version(root)}",
        sets=sets,
        card_individuals=cards,
    )


def bundle_members(root: Path) -> list[tuple[Path, str]]:
    """Return (source path, bundle-relative path) for every bundled file."""
    members: list[tuple[Path, str]] = []
    for entry in BUNDLE_PATHS:
        path = root / entry
        if path.is_dir():
            members.extend(
                (child, child.relative_to(root).as_posix())
                for child in sorted(path.rglob("*"))
                if child.is_file()
            )
        elif path.is_file():
            members.append((path, entry))
        else:
            msg = f"{path}: bundle member not found"
            raise FileNotFoundError(msg)
    return members


def write_bundle(root: Path, out_dir: Path, manifest: GraphManifest) -> Path:
    """Write the manifest and the gzipped tarball; return the tarball path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{BUNDLE_NAME}-{manifest.graph_version}"
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(
        json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    archive = out_dir / f"{stem}.tar.gz"
    # a fixed mtime keeps the tarball byte-stable for a given graph
    stamp = int(datetime.now(tz=UTC).timestamp())

    def reset(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = stamp
        return info

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(manifest_path, arcname=f"{stem}/{MANIFEST_FILE}", filter=reset)
        for source, rel in bundle_members(root):
            tar.add(source, arcname=f"{stem}/{rel}", filter=reset)
    return archive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="build_graph_bundle",
        description="Build the distributable knowledge-graph bundle.",
    )
    parser.add_argument(
        "--graph-version",
        default=datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        help="bundle content version (default: today, UTC)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="knowledge-graph root to bundle (default: this repository)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="output directory (default: dist/)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build the bundle and report what it contains."""
    args = parse_args(argv)
    manifest = build_manifest(args.root, args.graph_version)
    archive = write_bundle(args.root, args.out, manifest)
    size_mb = archive.stat().st_size / 1024 / 1024
    # T201: this script's program output, consumed by the release workflow
    print(  # noqa: T201
        f"{archive}: {manifest.sets} sets, "
        f"{manifest.card_individuals} card individuals, "
        f"graph_schema {manifest.graph_schema}, "
        f"CR {manifest.cr_version or 'unknown'} "
        f"({size_mb:.1f} MiB)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
