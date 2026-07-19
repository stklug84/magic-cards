# Query Index

Catalog of all SPARQL queries in this directory. Every query is parameterized via `VALUES` clauses; defaults target the Saheeli, Radiant Creator commander deck so each runs as-is.

See [`README.md`](README.md) for prefix conventions, running instructions, and the synergy-traversal pattern.

---

## 01 — Deck Inventory

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_list_all_decks.rq`](01_deck_inventory/01_list_all_decks.rq) | Enumerate every deck individual in the dataset | none |
| 2 | [`02_cards_in_deck.rq`](01_deck_inventory/02_cards_in_deck.rq) | Full list of cards in a deck with quantities | `?deck` |
| 3 | [`03_deck_total_count.rq`](01_deck_inventory/03_deck_total_count.rq) | Total card count and unique-card count (legal Commander = 100) | `?deck` |
| 4 | [`04_commander_of_deck.rq`](01_deck_inventory/04_commander_of_deck.rq) | Identify the commander(s) of a deck | `?deck` |
| 5 | [`05_mana_curve.rq`](01_deck_inventory/05_mana_curve.rq) | Mana curve histogram (non-land cards grouped by mana value) | `?deck` |
| 6 | [`06_color_distribution.rq`](01_deck_inventory/06_color_distribution.rq) | Cards per color (multi-color counted per color) | `?deck` |
| 7 | [`07_card_type_breakdown.rq`](01_deck_inventory/07_card_type_breakdown.rq) | Cards per primary card type | `?deck` |
| 8 | [`08_basic_land_count.rq`](01_deck_inventory/08_basic_land_count.rq) | Basic lands grouped by basic-land subtype | `?deck` |

## 02 — Card Metadata

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_card_full_details.rq`](02_card_metadata/01_card_full_details.rq) | All attributes of a single card | `?card` |
| 2 | [`02_cards_in_deck_by_subtype.rq`](02_card_metadata/02_cards_in_deck_by_subtype.rq) | Cards in a deck with a given subtype | `?deck`, `?subtype` |
| 3 | [`03_cards_in_deck_by_artist.rq`](02_card_metadata/03_cards_in_deck_by_artist.rq) | Cards in a deck by a specific artist | `?deck`, `?artist` |
| 4 | [`04_cards_in_deck_from_set.rq`](02_card_metadata/04_cards_in_deck_from_set.rq) | Cards in a deck from a given set | `?deck`, `?set` |
| 5 | [`05_cards_in_deck_with_keyword.rq`](02_card_metadata/05_cards_in_deck_with_keyword.rq) | Cards in a deck with a given keyword ability | `?deck`, `?keyword` |
| 6 | [`06_legendary_creatures_in_deck.rq`](02_card_metadata/06_legendary_creatures_in_deck.rq) | Every legendary creature in a deck | `?deck` |
| 7 | [`07_cards_in_deck_under_mana_value.rq`](02_card_metadata/07_cards_in_deck_under_mana_value.rq) | Non-land cards at or below a mana-value cap | `?deck`, `?maxMV` |

## 03 — Synergies

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_all_synergies_of_card.rq`](03_synergies/01_all_synergies_of_card.rq) | All cards related to a given card via any synergy edge | `?card` |
| 2 | [`02_amplifiers_of_card.rq`](03_synergies/02_amplifiers_of_card.rq) | Cards that amplify a given card's triggered abilities | `?card` |
| 3 | [`03_enablers_of_card.rq`](03_synergies/03_enablers_of_card.rq) | Cards that enable a given card's value | `?card` |
| 4 | [`04_mutual_synergies_in_deck.rq`](03_synergies/04_mutual_synergies_in_deck.rq) | Synergy pairs where both cards are in the deck | `?deck` |
| 5 | [`05_cards_in_deck_without_synergy.rq`](03_synergies/05_cards_in_deck_without_synergy.rq) | Cards in a deck with no synergy edges (candidates to swap) | `?deck` |
| 6 | [`06_synergy_count_per_card_in_deck.rq`](03_synergies/06_synergy_count_per_card_in_deck.rq) | Per-card synergy-partner count (find combo hubs) | `?deck` |

## 04 — Legality

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_card_legality_in_format.rq`](04_legality/01_card_legality_in_format.rq) | Legality status of one card in one format | `?card`, `?format` |
| 2 | [`02_banned_cards_in_deck.rq`](04_legality/02_banned_cards_in_deck.rq) | Cards in a deck banned in any tracked format | `?deck` |
| 3 | [`03_cards_in_deck_legal_in_all_formats.rq`](04_legality/03_cards_in_deck_legal_in_all_formats.rq) | Cards legal in every tracked format | `?deck` |
| 4 | [`04_cards_in_deck_with_split_legality.rq`](04_legality/04_cards_in_deck_with_split_legality.rq) | Cards legal in some formats but not others | `?deck` |

## 05 — Sets & Printings

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_all_sets_in_deck.rq`](05_sets_and_printings/01_all_sets_in_deck.rq) | Sets represented in the deck with card counts | `?deck` |
| 2 | [`02_cards_by_rarity_in_deck.rq`](05_sets_and_printings/02_cards_by_rarity_in_deck.rq) | Card distribution by rarity | `?deck` |
| 3 | [`03_set_release_timeline.rq`](05_sets_and_printings/03_set_release_timeline.rq) | Contributing sets ordered by release date | `?deck` |
| 4 | [`04_mythics_and_rares_in_deck.rq`](05_sets_and_printings/04_mythics_and_rares_in_deck.rq) | All Rares + Mythics in the deck | `?deck` |
| 5 | [`05_printings_per_card.rq`](05_sets_and_printings/05_printings_per_card.rq) | Tabular list of every card with all collected printings and their sets | none |

## 06 — Rulings

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_rulings_for_card.rq`](06_rulings/01_rulings_for_card.rq) | All rulings on a specific card | `?card` |
| 2 | [`02_cards_in_deck_with_rulings.rq`](06_rulings/02_cards_in_deck_with_rulings.rq) | Deck cards that have rulings, with counts | `?deck` |
| 3 | [`03_recent_rulings_in_deck.rq`](06_rulings/03_recent_rulings_in_deck.rq) | Recent rulings on deck cards since a cutoff date | `?deck`, `?cutoff` |
| 4 | [`04_rulings_mentioning_keyword.rq`](06_rulings/04_rulings_mentioning_keyword.rq) | Rulings whose text contains a substring | `?keyword` |

## 07 — Graph Patterns

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_construct_synergy_subgraph.rq`](07_graph_patterns/01_construct_synergy_subgraph.rq) | CONSTRUCT a clean synergy graph for export | `?deck` |
| 2 | [`02_construct_deck_skeleton.rq`](07_graph_patterns/02_construct_deck_skeleton.rq) | CONSTRUCT a slim deck view for visualization | `?deck` |
| 3 | [`03_describe_card.rq`](07_graph_patterns/03_describe_card.rq) | DESCRIBE everything stored about a card | `?card` |
| 4 | [`04_ask_card_in_deck.rq`](07_graph_patterns/04_ask_card_in_deck.rq) | ASK boolean: is card C in deck D? | `?deck`, `?card` |
| 5 | [`05_two_hop_synergy_neighborhood.rq`](07_graph_patterns/05_two_hop_synergy_neighborhood.rq) | Cards within two synergy hops of a seed | `?seed` |
| 6 | [`06_construct_quantity_view.rq`](07_graph_patterns/06_construct_quantity_view.rq) | CONSTRUCT a flat `card -> quantity` view | `?deck` |

## 08 — Ontology Introspection

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_all_decks.rq`](08_ontology_introspection/01_all_decks.rq) | Every deck individual in the dataset | none |
| 2 | [`02_all_card_types.rq`](08_ontology_introspection/02_all_card_types.rq) | All CardType individuals defined in the ontology | none |
| 3 | [`03_all_keyword_abilities.rq`](08_ontology_introspection/03_all_keyword_abilities.rq) | All KeywordAbility individuals with rules text | none |
| 4 | [`04_all_formats_and_statuses.rq`](08_ontology_introspection/04_all_formats_and_statuses.rq) | Every Format and every Legality individual | none |
| 5 | [`05_ontology_property_usage.rq`](08_ontology_introspection/05_ontology_property_usage.rq) | Triple count per `mc:` property | none |

## 09 — Compound

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_commander_color_identity_check.rq`](09_compound/01_commander_color_identity_check.rq) | Find cards violating commander color identity | `?deck` |
| 2 | [`02_top_synergy_hubs_per_deck.rq`](09_compound/02_top_synergy_hubs_per_deck.rq) | Top-N cards by synergy-partner count | `?deck` |
| 3 | [`03_compare_two_decks.rq`](09_compound/03_compare_two_decks.rq) | Set-diff two decks (A-only / B-only / both) | `?deckA`, `?deckB` |
| 4 | [`04_artifact_creature_synergies.rq`](09_compound/04_artifact_creature_synergies.rq) | Artifact Creatures with in-deck synergy partners | `?deck` |
