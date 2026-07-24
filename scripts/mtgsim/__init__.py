"""mtgsim - modular Monte-Carlo Commander matchup simulator.

Layers:
  cards / ttl_loader   card data (oracle-driven from the TTL knowledge graph:
                       sets/*.ttl + MagicExternalCards.ttl; optional user
                       custom cards via --custom-cards)
  behaviors            effect hooks for cards whose behavior cannot be
                       derived from oracle text
  mana                 color-aware mana base (pips, taplands, treasures)
  state / effects      battlefield state, tokens, counters, proliferate,
                       energy, populate, drains
  combat               multi-block combat, evasion, trample, commander damage,
                       spacecraft station
  ai                   tunable decision profiles, threat assessment
  game                 turn structure, reaction windows, London mulligan,
                       2-4 player pods
  stats                Wilson CIs, per-card win correlation, JSONL logs
  cli                  command-line entry point
"""

__version__ = "2.0"
