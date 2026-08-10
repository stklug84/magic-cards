"""mtgvalidate - knowledge-graph validators, runnable against any root.

The checks that guard this repository's own graph, packaged so a
downstream repository (e.g. a private deck repo) can run them over its
own TTL files against a published graph bundle:

  ttl          Turtle syntax, prefix/header conventions, owl:imports
               resolution
  sparql       SPARQL 1.1 syntax, canonical mc: prefix, term existence
  consistency  cross-file graph checks: undefined terms, dangling refs,
               card-entry shape, Commander deck totals (CR 903.5a),
               behavior hooks, synergy domains

Every check takes a :class:`~mtgvalidate.context.ValidationContext`
carrying one or more graph roots and the ontology IRI, rather than
assuming a fixed repository layout. The console script ``mtg-validate``
wires them together.

Unlike mtgcards/mtgrules/mtgviz this package requires rdflib; install it
with the ``validate`` extra::

    pip install "magic-cards-tools[validate]"
"""

from __future__ import annotations

from mtgvalidate.context import ValidationContext

__all__ = ["ValidationContext"]
