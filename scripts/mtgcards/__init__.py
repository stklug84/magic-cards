"""mtgcards - the knowledge graph's data contract, as Python.

The simulator itself is the Rust workspace under ``rust/`` (the
``mtgcards`` crate reads the graph); this package keeps the two pieces
the graph tooling and the validators share with it:

  graph.py       GRAPH-MANIFEST.json: the graph-bundle contract
  behaviors.py   BEHAVIOR_KEYS, the whitelist of :behaviorKey values
  version.py     the tools version the manifest records
"""
