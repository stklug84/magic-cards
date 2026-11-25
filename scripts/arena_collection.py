#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.55.1,<2"]
# ///
"""Pull the MTG Arena collection from Untapped.gg into arena-collection.json.

The generator's second inventory, next to collection.csv: run this before
the pipeline whenever the Arena collection changed. ``uv run`` installs
Playwright from the metadata above; it attaches to the installed Google
Chrome and downloads no browser. See ``mtgarena.cli`` for the commands:

    uv run scripts/arena_collection.py login           # once: sign in, then Cmd+Q
    uv run scripts/arena_collection.py pull --dry-run  # fetch, resolve, show changes
    uv run scripts/arena_collection.py pull            # write the three files
    uv run scripts/arena_collection.py resolve         # offline: resolve anew
"""

from __future__ import annotations

import sys
from pathlib import Path

from mtgarena.cli import isolate_from_ambient_pythonpath, main

if __name__ == "__main__":
    isolate_from_ambient_pythonpath(Path(__file__).resolve())
    sys.exit(main())
