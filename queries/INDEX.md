# Query Index

Catalog of all SPARQL queries in this directory. Deck-scoped queries cover all decks in the dataset by default and carry a commented-out `VALUES ?deck` line to optionally restrict to specific deck(s); other parameters use editable `VALUES` clauses, so each query runs as-is.

See [`README.md`](README.md) for prefix conventions, running instructions, and the synergy-traversal pattern.

---

## 01 — Deck Inventory

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_list_all_decks.rq`](01_deck_inventory/01_list_all_decks.rq) | Enumerate every deck individual in the dataset | none |
| 2 | [`02_cards_in_deck.rq`](01_deck_inventory/02_cards_in_deck.rq) | Full list of cards in a deck with quantities | `?deck` (optional filter) |
| 3 | [`03_deck_total_count.rq`](01_deck_inventory/03_deck_total_count.rq) | Total card count and unique-card count (legal Commander = 100) | `?deck` (optional filter) |
| 4 | [`04_commander_of_deck.rq`](01_deck_inventory/04_commander_of_deck.rq) | Identify the commander(s) of a deck | `?deck` (optional filter) |
| 5 | [`05_mana_curve.rq`](01_deck_inventory/05_mana_curve.rq) | Mana curve histogram (non-land cards grouped by mana value) | `?deck` (optional filter) |
| 6 | [`06_color_distribution.rq`](01_deck_inventory/06_color_distribution.rq) | Cards per color (multi-color counted per color) | `?deck` (optional filter) |
| 7 | [`07_card_type_breakdown.rq`](01_deck_inventory/07_card_type_breakdown.rq) | Cards per primary card type | `?deck` (optional filter) |
| 8 | [`08_basic_land_count.rq`](01_deck_inventory/08_basic_land_count.rq) | Basic lands grouped by basic-land subtype | `?deck` (optional filter) |

## 02 — Card Metadata

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_card_full_details.rq`](02_card_metadata/01_card_full_details.rq) | All attributes of a single card | `?card` |
| 2 | [`02_cards_in_deck_by_subtype.rq`](02_card_metadata/02_cards_in_deck_by_subtype.rq) | Cards in a deck with a given subtype | `?deck` (optional filter), `?subtype` |
| 3 | [`03_cards_in_deck_by_artist.rq`](02_card_metadata/03_cards_in_deck_by_artist.rq) | Cards in a deck by a specific artist | `?deck` (optional filter), `?artist` |
| 4 | [`04_cards_in_deck_from_set.rq`](02_card_metadata/04_cards_in_deck_from_set.rq) | Cards in a deck from a given set | `?deck` (optional filter), `?set` |
| 5 | [`05_cards_in_deck_with_keyword.rq`](02_card_metadata/05_cards_in_deck_with_keyword.rq) | Cards in a deck with a given keyword ability | `?deck` (optional filter), `?keyword` |
| 6 | [`06_legendary_creatures_in_deck.rq`](02_card_metadata/06_legendary_creatures_in_deck.rq) | Every legendary creature in a deck | `?deck` (optional filter) |
| 7 | [`07_cards_in_deck_under_mana_value.rq`](02_card_metadata/07_cards_in_deck_under_mana_value.rq) | Non-land cards at or below a mana-value cap | `?deck` (optional filter), `?maxMV` |

## 03 — Synergies

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_all_synergies_of_card.rq`](03_synergies/01_all_synergies_of_card.rq) | All cards related to a given card via any synergy edge | `?card` |
| 2 | [`02_amplifiers_of_card.rq`](03_synergies/02_amplifiers_of_card.rq) | Cards that amplify a given card's triggered abilities | `?card` |
| 3 | [`03_enablers_of_card.rq`](03_synergies/03_enablers_of_card.rq) | Cards that enable a given card's value | `?card` |
| 4 | [`04_mutual_synergies_in_deck.rq`](03_synergies/04_mutual_synergies_in_deck.rq) | Synergy pairs where both cards are in the deck | `?deck` (optional filter) |
| 5 | [`05_cards_in_deck_without_synergy.rq`](03_synergies/05_cards_in_deck_without_synergy.rq) | Cards in a deck with no synergy edges (candidates to swap) | `?deck` (optional filter) |
| 6 | [`06_synergy_count_per_card_in_deck.rq`](03_synergies/06_synergy_count_per_card_in_deck.rq) | Per-card synergy-partner count (find combo hubs) | `?deck` (optional filter) |
| 7 | [`07_top_synergy_hubs_collection.rq`](03_synergies/07_top_synergy_hubs_collection.rq) | Top synergies collection-wide: best-connected cards | result size (LIMIT) |

## 04 — Legality

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_card_legality_in_format.rq`](04_legality/01_card_legality_in_format.rq) | Legality status of one card in one format | `?card`, `?format` |
| 2 | [`02_banned_cards_in_deck.rq`](04_legality/02_banned_cards_in_deck.rq) | Cards in a deck banned in any tracked format | `?deck` (optional filter) |
| 3 | [`03_cards_in_deck_legal_in_all_formats.rq`](04_legality/03_cards_in_deck_legal_in_all_formats.rq) | Cards legal in every tracked format | `?deck` (optional filter) |
| 4 | [`04_cards_in_deck_with_split_legality.rq`](04_legality/04_cards_in_deck_with_split_legality.rq) | Cards legal in some formats but not others | `?deck` (optional filter) |

## 05 — Sets & Printings

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_all_sets_in_deck.rq`](05_sets_and_printings/01_all_sets_in_deck.rq) | Sets represented in the deck with card counts | `?deck` (optional filter) |
| 2 | [`02_cards_by_rarity_in_deck.rq`](05_sets_and_printings/02_cards_by_rarity_in_deck.rq) | Card distribution by rarity | `?deck` (optional filter) |
| 3 | [`03_set_release_timeline.rq`](05_sets_and_printings/03_set_release_timeline.rq) | Contributing sets ordered by release date | `?deck` (optional filter) |
| 4 | [`04_mythics_and_rares_in_deck.rq`](05_sets_and_printings/04_mythics_and_rares_in_deck.rq) | All Rares + Mythics in the deck | `?deck` (optional filter) |
| 5 | [`05_printings_per_card.rq`](05_sets_and_printings/05_printings_per_card.rq) | Tabular list of every card with all collected printings, sets and physical copy counts | none |

## 06 — Rulings

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_rulings_for_card.rq`](06_rulings/01_rulings_for_card.rq) | All rulings on a specific card | `?card` |
| 2 | [`02_cards_in_deck_with_rulings.rq`](06_rulings/02_cards_in_deck_with_rulings.rq) | Deck cards that have rulings, with counts | `?deck` (optional filter) |
| 3 | [`03_recent_rulings_in_deck.rq`](06_rulings/03_recent_rulings_in_deck.rq) | Recent rulings on deck cards since a cutoff date | `?deck` (optional filter), `?cutoff` |
| 4 | [`04_rulings_mentioning_keyword.rq`](06_rulings/04_rulings_mentioning_keyword.rq) | Rulings whose text contains a substring | `?keyword` |

## 07 — Graph Patterns

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_construct_synergy_subgraph.rq`](07_graph_patterns/01_construct_synergy_subgraph.rq) | CONSTRUCT a clean synergy graph for export | `?deck` (optional filter) |
| 2 | [`02_construct_deck_skeleton.rq`](07_graph_patterns/02_construct_deck_skeleton.rq) | CONSTRUCT a slim deck view for visualization | `?deck` (optional filter) |
| 3 | [`03_describe_card.rq`](07_graph_patterns/03_describe_card.rq) | DESCRIBE everything stored about a card | `?card` |
| 4 | [`04_ask_card_in_deck.rq`](07_graph_patterns/04_ask_card_in_deck.rq) | ASK boolean: is card C in deck D? | `?deck` (optional filter), `?card` |
| 5 | [`05_two_hop_synergy_neighborhood.rq`](07_graph_patterns/05_two_hop_synergy_neighborhood.rq) | Cards within two synergy hops of a seed | `?seed` |
| 6 | [`06_construct_quantity_view.rq`](07_graph_patterns/06_construct_quantity_view.rq) | CONSTRUCT a flat `card -> quantity` view | `?deck` (optional filter) |

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
| 1 | [`01_commander_color_identity_check.rq`](09_compound/01_commander_color_identity_check.rq) | Find cards violating commander color identity | `?deck` (optional filter) |
| 2 | [`02_top_synergy_hubs_per_deck.rq`](09_compound/02_top_synergy_hubs_per_deck.rq) | Top-N cards by synergy-partner count | `?deck` (optional filter) |
| 3 | [`03_compare_two_decks.rq`](09_compound/03_compare_two_decks.rq) | Set-diff two decks (A-only / B-only / both) | `?deckA`, `?deckB` |
| 4 | [`04_artifact_creature_synergies.rq`](09_compound/04_artifact_creature_synergies.rq) | Artifact Creatures with in-deck synergy partners | `?deck` (optional filter) |

## 10 — Collection

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_collection_size.rq`](10_collection/01_collection_size.rq) | Physical card count, distinct printings and distinct card names | `?collection` |
| 2 | [`02_copies_per_card.rq`](10_collection/02_copies_per_card.rq) | Tabular copies-per-card view with per-printing quantities | `?collection` |
| 3 | [`03_foil_cards.rq`](10_collection/03_foil_cards.rq) | All foil / etched-foil cards with quantities and sets | `?collection` |
| 4 | [`04_most_duplicated_cards.rq`](10_collection/04_most_duplicated_cards.rq) | Cards owned in the most physical copies | `?collection`, `?minCopies` |
| 5 | [`05_collection_value.rq`](10_collection/05_collection_value.rq) | Recorded purchase value and price coverage | `?collection` |

## 11 — Mechanic Synergies

Synergy lists per Magic game mechanic: each query returns every synergy
relation (with type and direction) involving cards whose rules text uses the
mechanic. `01` is the generic parameterized template for mechanics without a
dedicated query.

| # | File | Use Case | Parameters |
|---|------|----------|------------|
| 1 | [`01_generic_mechanic_synergies.rq`](11_mechanic_synergies/01_generic_mechanic_synergies.rq) | Synergy list for any mechanic via regex | `?pattern` |
| 2 | [`02_energy_synergies.rq`](11_mechanic_synergies/02_energy_synergies.rq) | Energy ({E} counters) | none |
| 3 | [`03_token_synergies.rq`](11_mechanic_synergies/03_token_synergies.rq) | Token creation | none |
| 4 | [`04_counters_proliferate_synergies.rq`](11_mechanic_synergies/04_counters_proliferate_synergies.rq) | +1/+1 counters and proliferate | none |
| 5 | [`05_etb_blink_synergies.rq`](11_mechanic_synergies/05_etb_blink_synergies.rq) | ETB triggers and blink | none |
| 6 | [`06_sacrifice_death_synergies.rq`](11_mechanic_synergies/06_sacrifice_death_synergies.rq) | Sacrifice and death triggers | none |
| 7 | [`07_graveyard_recursion_synergies.rq`](11_mechanic_synergies/07_graveyard_recursion_synergies.rq) | Graveyard interaction and recursion | none |
| 8 | [`08_landfall_synergies.rq`](11_mechanic_synergies/08_landfall_synergies.rq) | Landfall | none |
| 9 | [`09_combat_attack_synergies.rq`](11_mechanic_synergies/09_combat_attack_synergies.rq) | Attack triggers and extra combats | none |
| 10 | [`10_lifegain_synergies.rq`](11_mechanic_synergies/10_lifegain_synergies.rq) | Life gain | none |
| 11 | [`11_artifact_treasure_synergies.rq`](11_mechanic_synergies/11_artifact_treasure_synergies.rq) | Artifacts-matter and Treasure | none |
| 12 | [`12_equipment_aura_synergies.rq`](11_mechanic_synergies/12_equipment_aura_synergies.rq) | Equipment and Auras | none |
| 13 | [`13_vehicle_crew_synergies.rq`](11_mechanic_synergies/13_vehicle_crew_synergies.rq) | Vehicles and crewing | none |
| 14 | [`14_spellslinger_cast_synergies.rq`](11_mechanic_synergies/14_spellslinger_cast_synergies.rq) | Cast triggers (spellslinger) | none |
| 15 | [`15_mill_discard_synergies.rq`](11_mechanic_synergies/15_mill_discard_synergies.rq) | Mill and discard | none |
| 16 | [`16_copy_synergies.rq`](11_mechanic_synergies/16_copy_synergies.rq) | Copying permanents and spells | none |
