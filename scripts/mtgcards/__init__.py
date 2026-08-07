"""mtgcards - shared card data layer for the rules engine.

Card characteristics come from the TTL knowledge graph (sets/*.ttl plus
optional extra card graphs), enriched by oracle-text derivation and
hand-authored behavior hooks (MagicSimulationAnnotations.ttl).

  cards.py       card model + oracle-text derivation
  ttl_loader.py  characteristics from the TTL knowledge graph
  database.py    graph -> custom JSON layering
  behaviors.py   hand-authored effect hooks
  mana.py        cost parsing (pips, hybrid, X)
  deck.py        decklist parsing ('N Card Name', '// Commander')
  stats.py       Wilson CIs, card win-rate lift, JSONL export
"""
