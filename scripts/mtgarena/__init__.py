"""The MTG Arena collection, read from Untapped.gg: the generator's second inventory.

MTG Arena has no collection export. The Untapped.gg Companion syncs the
collection to the Untapped.gg account, and ``scripts/arena_collection.py``
reads it from there through a signed-in Chrome, resolves every owned Arena
printing to its Scryfall printing and writes ``arena-collection.json`` (the
input ``scripts/generate_individuals.py`` turns into card individuals and
``MagicArenaCollection.ttl``) with a Moxfield CSV and an Arena-format list.

Modules: ``catalog`` (card data and resolution), ``untapped`` (the site),
``chrome`` (the browser), ``render`` (the files) and ``cli`` (the commands).
Repository tooling only: the package is not part of the magic-cards-tools
wheel, and only the live commands need Playwright (``uv run`` installs it
from the script's inline metadata).
"""
