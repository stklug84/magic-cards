"""Canonical dump of the parsed Comprehensive Rules index (parity L2).

Parses a CR text file through :class:`mtgrules.cr.ComprehensiveRules`
exactly as the engine does and writes one JSON line per rule, then one
per glossary term, so the Rust ``mtgrules`` crate's parser
(``cargo run -p mtgrules --example dump_cr``) can be diffed against it by
``cargo xtask parity``.

The CR text is Wizards' and not redistributable, so the dump never
contains it: rule and example texts and glossary definitions appear as
their length in code points and their FNV-1a 64-bit digest (computed
over UTF-8 — the same few lines on both sides, no dependency). Rule
numbers and glossary headwords are kept in clear: they are what a diff
needs to point at.

Usage (from ``scripts/``)::

    python3 -m parity.dump_cr [--cr-file F | --root DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from mtgrules.cr import ComprehensiveRules

if TYPE_CHECKING:
    from collections.abc import Iterator

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK64 = (1 << 64) - 1


def fnv1a64(text: str) -> str:
    """FNV-1a 64-bit digest of *text*'s UTF-8 bytes, as 16 hex digits."""
    h = _FNV_OFFSET
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * _FNV_PRIME) & _MASK64
    return f"{h:016x}"


def dump_lines(cr: ComprehensiveRules) -> Iterator[str]:
    """Yield the summary line, one line per rule, one per glossary term."""
    yield json.dumps(
        {
            "summary": {
                "file": cr.path.name,
                "rules": len(cr.rules),
                "examples": sum(len(r.examples) for r in cr.rules.values()),
                "glossary": len(cr.glossary),
            },
        },
    )
    for number, rule in cr.rules.items():
        yield json.dumps(
            {
                "rule": number,
                "len": len(rule.text),
                "fnv": fnv1a64(rule.text),
                "examples": [
                    {"len": len(ex), "fnv": fnv1a64(ex)} for ex in rule.examples
                ],
            },
        )
    for term, definition in cr.glossary.items():
        yield json.dumps(
            {"term": term, "len": len(definition), "fnv": fnv1a64(definition)},
        )


def main(argv: list[str] | None = None) -> int:
    """Write the dump to stdout (or ``--out``); 0 on success."""
    parser = argparse.ArgumentParser(
        description="Canonical dump of the parsed Comprehensive Rules (parity L2).",
    )
    parser.add_argument("--cr-file", help="a Comprehensive Rules text file")
    parser.add_argument(
        "--root",
        help="graph root holding MagicCompRules-*.txt (default: the checkout)",
    )
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args(argv)
    if args.cr_file:
        cr = ComprehensiveRules(Path(args.cr_file))
    elif args.root:
        candidates = sorted(Path(args.root).glob("MagicCompRules-*.txt"))
        if not candidates:
            msg = f"no MagicCompRules-*.txt in {args.root}"
            raise FileNotFoundError(msg)
        cr = ComprehensiveRules(candidates[-1])
    else:
        cr = ComprehensiveRules()
    text = "\n".join(dump_lines(cr)) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
