#!/usr/bin/env python3
"""Monte-Carlo Commander matchup simulator (thin CLI wrapper).

The implementation lives in the mtgsim package next to this script:

  mtgsim/cards.py       card model + oracle-text derivation
  mtgsim/ttl_loader.py  characteristics from the TTL knowledge graph
  mtgsim/database.py    graph -> fallback JSON -> custom JSON layering
  mtgsim/behaviors.py   hand-authored effect hooks
  mtgsim/mana.py        color-aware mana (pips, taplands, treasures)
  mtgsim/state.py       players, permanents, token groups
  mtgsim/effects.py     tokens, -1/-1 counters, proliferate/energy/populate
  mtgsim/combat.py      multi-block combat, evasion, commander damage
  mtgsim/ai.py          tunable decision profiles
  mtgsim/game.py        turn loop, reaction windows, London mulligan
  mtgsim/stats.py       Wilson CIs, card win-rate lift, JSONL export
  mtgsim/cli.py         argument parsing / reporting

Usage:
  python3 scripts/simulate_matchup.py DECK1 DECK2 [DECK3 DECK4] \
      [--games N] [--seed N] [--seeds N|LIST] [--verbose] \
      [--log-file out.jsonl] [--custom-cards file.json] \
      [--profile 1=aggressive] [--turn-cap N]

2-4 decklist files are required. --seeds pools statistics over multiple RNG
seeds (a count starting at --seed, or an explicit list like '1,7,100') and
reports per-seed win rates alongside the pooled totals.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgsim.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
