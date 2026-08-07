"""Card database assembly from the TTL knowledge graph.

Resolution order (later layers override earlier ones):
  1. TTL knowledge graph (sets/*.ttl plus any extra card graphs, e.g. an
     out-of-collection MagicExternalCards.ttl) - the single source of
     card characteristics
  2. custom cards JSON (opt-in via --custom-cards) - unreleased/unverified
     cards only; no default file is loaded
  3. Scryfall API (resolve_scryfall) - cards of txt decklists, fetched by
     name with a local cache; failures fall back to the layers above
Then oracle-text derivation and behavior hooks are applied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgcards.behaviors import BEHAVIOR_KEYS, apply_behaviors, load_annotations
from mtgcards.cards import CardData, derive_from_oracle
from mtgcards.mana import parse_cost
from mtgcards.scryfall import ScryfallClient, card_from_scryfall
from mtgcards.ttl_loader import load_graph_cards

if TYPE_CHECKING:
    from collections.abc import Iterable


def _card_from_json(name: str, entry: dict[str, Any], source: str) -> CardData:
    """Build a CardData from one --custom-cards JSON entry."""
    card = CardData(name=name, source=source)
    card.mana_cost = entry.get("mana_cost", "")
    card.types = set(entry.get("types", []))
    card.subtypes = set(entry.get("subtypes", []))
    card.power = entry.get("power")
    card.toughness = entry.get("toughness")
    card.oracle = entry.get("oracle", "")
    card.color_identity = set(entry.get("color_identity", []))
    card.mv = parse_cost(card.mana_cost).mv
    for k, v in entry.get("behavior", {}).items():
        if k not in BEHAVIOR_KEYS:
            msg = (
                f"custom card {name!r}: unknown behavior key {k!r} "
                f"(see behaviors.BEHAVIOR_KEYS)"
            )
            raise ValueError(
                msg,
            )
        card.behavior[k] = (
            tuple(v)
            if isinstance(v, list)
            and k in ("etb_tokens", "death_tokens", "burst_tokens")
            else v
        )
    return card


class CardDatabase:
    """Name-indexed card data, layered from graph, custom JSON, and hooks."""

    def __init__(
        self,
        repo_root: str | Path,
        custom_cards_path: str | Path | None = None,
        extra_graphs: Iterable[str | Path] = (),
    ) -> None:
        """Load every layer and derive behavior for each unique card.

        *extra_graphs* are additional card TTL graphs merged on top of
        sets/*.ttl - e.g. an out-of-collection MagicExternalCards.ttl
        kept in a separate repository (missing files are skipped). A
        MagicSimulationAnnotations.ttl sitting next to an extra graph is
        loaded on top of the repo-root annotations.
        """
        self.index: dict[str, CardData] = {}
        #: {individual local name: card name} for every card individual in
        #: the graph; deck instance graphs (.ttl decks) resolve through it
        self.ind2name: dict[str, str] = {}
        ind2name = self.ind2name
        hooks_by_name: dict[str, dict[str, Any]] = {}
        sets_dir = Path(repo_root) / "sets"
        if sets_dir.is_dir():
            # out-of-collection cards referenced by deck graphs (a local
            # MagicExternalCards.ttl is honored when present)
            extras = (Path(repo_root) / "MagicExternalCards.ttl", *extra_graphs)
            self.index.update(load_graph_cards(sets_dir, extras, ind2name))
            # simulation annotations (behavior hooks, threat weights),
            # merged with any annotation file next to an extra card graph
            hooks_by_name = load_annotations(Path(repo_root), ind2name)
            seen_dirs = {Path(repo_root).resolve()}
            for extra in extras:
                extra_dir = Path(extra).resolve().parent
                if extra_dir in seen_dirs or not Path(extra).exists():
                    continue
                seen_dirs.add(extra_dir)
                for name, hooks in load_annotations(extra_dir, ind2name).items():
                    hooks_by_name.setdefault(name, {}).update(hooks)
        # custom layer (opt-in, overrides the graph)
        if custom_cards_path:
            custom = Path(custom_cards_path)
            for name, entry in json.loads(custom.read_text()).items():
                if name.startswith("_"):
                    continue
                self.index[name] = _card_from_json(name, entry, "custom")
        # derivation + hooks (dedupe DFC aliases by id)
        seen: set[int] = set()
        for card in self.index.values():
            if id(card) in seen:
                continue
            seen.add(id(card))
            derive_from_oracle(card)
            apply_behaviors(card, hooks_by_name)
        self._hooks_by_name = hooks_by_name
        self.stubbed: list[str] = []

    def resolve_scryfall(
        self,
        names: Iterable[str],
        client: ScryfallClient | None = None,
    ) -> list[str]:
        """Fetch *names* from Scryfall, overriding the graph entries.

        Fetched cards go through the same oracle derivation + behavior
        hook pipeline as graph cards. Returns the names that could not be
        fetched (offline / unknown name); lookups for those fall back to
        the knowledge graph entry or, failing that, the inert stub.
        """
        client = client if client is not None else ScryfallClient()
        failed: list[str] = []
        for name in names:
            data = client.fetch(name)
            if data is None:
                failed.append(name)
                continue
            card = card_from_scryfall(data)
            derive_from_oracle(card)
            apply_behaviors(card, self._hooks_by_name)
            self.index[card.name] = card
            # front face of double-faced cards, plus the queried alias
            if " // " in card.name:
                self.index[card.name.split(" // ")[0]] = card
            self.index[name] = card
        client.save()
        return failed

    def get(self, name: str) -> CardData:
        """Look up a card by (front-face) name, stubbing unknown cards."""
        card = self.index.get(name)
        if card is None and " // " in name:
            card = self.index.get(name.split(" // ", maxsplit=1)[0])
        if card is None:
            # unknown card: inert stub so arbitrary decks never crash
            card = CardData(
                name=name,
                mana_cost="{3}",
                mv=3,
                types={"Sorcery"},
                source="stub",
            )
            self.index[name] = card
            self.stubbed.append(name)
        return card
