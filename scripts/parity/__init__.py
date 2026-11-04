"""Parity tooling between the Python simulator and its Rust port.

Development-only helpers, not part of the released packages:

- ``vectors``: CPython reference vectors (random streams, json.dumps,
  number formatting, statistics, repr) that the Rust ``pycompat`` crate
  is tested against (parity level L1).

Run them from ``scripts/`` (``python3 -m parity.vectors``) or through
``cargo xtask vectors`` from ``rust/``.
"""
