"""``mtg-validate`` - run the knowledge-graph validators over graph roots.

Roots are scanned in order; the first one carrying a bundle manifest
supplies the ontology IRI. A downstream repository validates its own
graphs against a published bundle by naming both::

    mtg-validate --check ttl --check consistency .graph .

Inside a magic-cards checkout the repository root alone is enough::

    mtg-validate .

Exit code 0 when every selected check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from mtgvalidate import consistency, sparql, ttl
from mtgvalidate.context import ValidationContext

if TYPE_CHECKING:
    from collections.abc import Callable

#: check name -> runner, in execution order
CHECKS: dict[str, Callable[[ValidationContext], list[str]]] = {
    "ttl": ttl.run,
    "sparql": sparql.run,
    "consistency": consistency.run,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="mtg-validate",
        description="Validate TTL graphs and SPARQL queries under graph roots.",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        default=["."],
        help="graph roots to scan (default: the current directory)",
    )
    parser.add_argument(
        "--check",
        action="append",
        choices=sorted(CHECKS),
        dest="checks",
        help="check to run; repeatable (default: all)",
    )
    return parser.parse_args(argv)


def report(errors: list[str]) -> int:
    """Print the FAIL lines; return the process exit code."""
    if errors:
        unique = sorted(set(errors))
        print(f"\n{len(unique)} error(s):", file=sys.stderr)  # noqa: T201 - validator FAIL output
        for err in unique:
            print(f"FAIL {err}", file=sys.stderr)  # noqa: T201 - validator FAIL output
        return 1
    print("\nAll checks passed.")  # noqa: T201 - validator summary line
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the selected checks over the given roots and report."""
    args = parse_args(argv)
    try:
        ctx = ValidationContext.from_roots(args.roots)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201 - CLI error output
        return 1

    selected = args.checks or list(CHECKS)
    roots = " ".join(str(r) for r in ctx.roots)
    print(f"Graph roots: {roots}")  # noqa: T201 - validator progress line
    print(f"Ontology IRI: {ctx.ontology_iri}\n")  # noqa: T201 - validator progress line

    errors: list[str] = []
    for name, check in CHECKS.items():
        if name not in selected:
            continue
        print(f"--- {name} ---")  # noqa: T201 - validator section header
        try:
            errors.extend(check(ctx))
        except FileNotFoundError as exc:
            errors.append(f"{name}: {exc}")
        print()  # noqa: T201 - validator section spacing
    return report(errors)


if __name__ == "__main__":
    raise SystemExit(main())
