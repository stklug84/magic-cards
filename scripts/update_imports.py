#!/usr/bin/env python3
"""Rewrite the owl:imports block of MagicCardIndividuals.ttl.

Consumes /tmp/imports.json as written by scripts/generate_individuals.py
generate (one entry per emitted sets/*.ttl file with its ontology URN and
individual count) and replaces every per-set import line in the aggregator
ontology:

    owl:imports <urn:...> ;  # sets/<File>.ttl (<n> individuals)

The MagicCardsOntology import and all surrounding content are preserved.
SubTypeSupplement stays first (mirroring the generator's ordering); the
remaining imports are sorted by file name.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/generate_individuals.py generate
  python3 scripts/update_imports.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "MagicCardIndividuals.ttl"
# Interchange contract with scripts/generate_individuals.py: both sides
# resolve the platform temp dir (/tmp on the Linux CI runners).
IMPORTS_JSON = Path(tempfile.gettempdir()) / "imports.json"

# The whole import block is one object list under a single owl:imports
# predicate: the ontology first, then one object per generated set file,
# each carrying a trailing comment naming its file. Every line but the last
# ends in ','. The block is rewritten wholesale, so this regex has to match
# the ontology line too.
ONTOLOGY_IRI = "urn:stklug84:MagicCardsOntology:2026-02-27#"
#: continuation indent aligning objects under the first one
IMPORT_PAD = " " * len("    owl:imports ")
IMPORT_LINE_RE = re.compile(
    rf"^(?:    owl:imports |{IMPORT_PAD})<[^>]+> [,;]"
    r"(?:  # sets/\S+\.ttl \(\d+ individuals\))?$",
)
SUPPLEMENT = "SubTypeSupplement.ttl"


def import_lines(entries: list[dict[str, str | int]]) -> list[str]:
    """Render the aggregator's imports as one Turtle object list."""
    supplement = [e for e in entries if e["file"] == SUPPLEMENT]
    sets = sorted(
        (e for e in entries if e["file"] != SUPPLEMENT),
        key=lambda e: e["file"],
    )
    objects = [(ONTOLOGY_IRI, "")]
    objects += [
        (str(e["urn"]), f"  # sets/{e['file']} ({e['cards']} individuals)")
        for e in supplement + sets
    ]
    return [
        f"{'    owl:imports ' if i == 0 else IMPORT_PAD}<{urn}> "
        f"{';' if i == len(objects) - 1 else ','}{comment}"
        for i, (urn, comment) in enumerate(objects)
    ]


def main() -> int:
    """Rewrite the import block; return a process exit code."""
    if not IMPORTS_JSON.exists():
        print(  # noqa: T201 - pipeline progress/error output
            f"ERROR: {IMPORTS_JSON} not found - run "
            "'generate_individuals.py generate' first",
            file=sys.stderr,
        )
        return 1
    entries = json.loads(IMPORTS_JSON.read_text())

    lines = MASTER.read_text().splitlines()
    kept: list[str] = []
    inserted = False
    for line in lines:
        if IMPORT_LINE_RE.match(line):
            if not inserted:
                kept.extend(import_lines(entries))
                inserted = True
            continue
        kept.append(line)

    if not inserted:
        # no import block yet: hang a fresh one off the ontology header
        try:
            anchor = kept.index("    rdf:type owl:Ontology ;")
        except ValueError:
            print(  # noqa: T201 - pipeline progress/error output
                f"ERROR: no import block found in {MASTER.name}",
                file=sys.stderr,
            )
            return 1
        kept[anchor + 1 : anchor + 1] = import_lines(entries)

    MASTER.write_text("\n".join(kept) + "\n")
    print(f"{MASTER.name}: {len(entries)} imports written")  # noqa: T201 - pipeline progress/error output
    return 0


if __name__ == "__main__":
    sys.exit(main())
