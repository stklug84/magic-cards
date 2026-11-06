"""Parity tooling between the Python simulator and its Rust port.

Development-only helpers, not part of the released packages:

- ``vectors``: CPython reference vectors (random streams, json.dumps,
  number formatting, statistics, repr, str semantics) that the Rust
  ``pycompat`` crate is tested against (parity level L1).
- ``dump_cards``: the canonical card-database dump the Rust ``mtgcards``
  crate must reproduce byte for byte (parity level L2).

Run them from ``scripts/`` (``python3 -m parity.vectors``,
``python3 -m parity.dump_cards --root ..``) or through
``cargo xtask vectors`` / ``cargo xtask parity`` from ``rust/``.
"""
