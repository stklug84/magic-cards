#!/usr/bin/env python3
"""Commander matchup simulator (thin CLI wrapper).

The implementation is the CR-grounded rules engine next to this script:

  mtgcards/    shared card data layer (TTL knowledge graph -> CardData,
               decklist parsing, behavior hooks, statistics)
  mtgrules/    the rules engine: stack & priority (117/405/601-608),
               state-based actions (704), the layer system (613),
               replacement effects (614-616), Commander rules (903),
               oracle-text compiler + ~80 hand-written card overrides,
               tunable AI policy profiles
  mtgviz/      game visualization: event recorder, JSONL replay,
               rich-based TUI (live --watch and --replay modes)

Usage:
  python3 scripts/simulate_matchup.py DECK1 DECK2 [DECK3 DECK4] \
      [--games N] [--seed N] [--seeds N|LIST] [--verbose] \
      [--log-file out.jsonl] [--custom-cards file.json] \
      [--profile 1=aggressive] [--turn-cap N] \
      [--watch] [--viz-file game.jsonl]
  python3 scripts/simulate_matchup.py --replay game.jsonl [--game N]

2-4 decklist files are required. --seeds pools statistics over multiple RNG
seeds (a count starting at --seed, or an explicit list like '1,7,100') and
reports per-seed win rates alongside the pooled totals. --watch renders a
single live game in a TUI (requires 'rich'); --viz-file records games for
later TUI replay via --replay.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgrules.cli import main

if __name__ == "__main__":
    main()
