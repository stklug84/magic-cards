# SPARQL Query Catalog

Reusable SPARQL 1.1 queries for the **Magic Cards Ontology** (`MagicCardsOntology.ttl`) and any instance graph
that conforms to it. The reference dataset is the card collection in `sets/*.ttl` (aggregated by
`MagicCardIndividuals.ttl`) together with the inventory graph `MagicCardCollection.ttl`, the deck graph
`decks/SaheeliRadiantCreator.ttl` and the synergy graph `MagicCardSynergies.ttl`.

Every query is a standalone `.rq` file with:

- a header comment describing **what it does**, plus
- a `# PARAMETERS:` section identifying every parameter you can edit to retarget the query. Deck-scoped
  queries cover **all decks** by default and carry an optional, commented-out `VALUES ?deck { … }` filter you
  can uncomment to restrict them; other parameters (card, keyword, format, …) remain `VALUES`-based, so every
  query runs as-is.

See [`INDEX.md`](INDEX.md) for the full catalog.

## Directory layout

```text
queries/
├── README.md                      this file
├── INDEX.md                       catalog of all queries with use cases & parameters
├── 01_deck_inventory/             list / count / aggregate over a deck
├── 02_card_metadata/              attribute-driven lookups (artist, set, subtype, …)
├── 03_synergies/                  card-to-card relationships
├── 04_legality/                   per-format banned / legal / restricted
├── 05_sets_and_printings/         set composition and rarity views
├── 06_rulings/                    official ruling text and dates
├── 07_graph_patterns/             CONSTRUCT / DESCRIBE / ASK / property paths
├── 08_ontology_introspection/     "what's in the schema" queries (no parameters)
├── 09_compound/                   multi-criteria queries combining several axes
├── 10_collection/                 physical inventory: copies, finishes, value
└── 11_mechanic_synergies/         synergy lists per game mechanic (energy, tokens, …)
```

## Conventions

Every query starts with the same prefix. Deck-scoped queries cover all decks by default, project the deck's
label first, and carry a commented-out `VALUES ?deck` filter:

```sparql
PREFIX mc: <urn:stklug84:MagicCardsOntology:2026-02-27#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?deckName ?cardName WHERE {
  # To restrict to specific deck(s), uncomment and edit:
  # VALUES ?deck { mc:SaheeliRadiantCreatorDeck }

  OPTIONAL { ?deck rdfs:label ?deckName }

  ?deck mc:hasCard ?c .
  ?c mc:cardName ?cardName .
}
ORDER BY ?deckName ?cardName
```

To restrict the query to one deck, uncomment and edit the `VALUES` line:

```sparql
VALUES ?deck { mc:SomeOtherDeck }
```

Restricting to multiple decks is fine too:

```sparql
VALUES ?deck { mc:DeckA mc:DeckB mc:DeckC }
```

Other parameters (card, keyword, format, …) remain active `VALUES` clauses that you edit in place.

The single `mc:` prefix covers everything; no `deck:` or default prefix is needed because deck individuals
live in the ontology namespace by import.

## Synergy traversal

Synergy queries (sections 03, 07 and 09) walk the directional synergy edges using **SPARQL 1.1 property paths**:

```sparql
?card ( mc:hasSynergyWith
      | ^mc:hasSynergyWith
      | mc:amplifies
      | ^mc:amplifies
      | mc:isAmplifiedBy
      | ^mc:isAmplifiedBy
      | mc:enables
      | ^mc:enables
      | mc:isEnabledBy
      | ^mc:isEnabledBy ) ?other .
```

This pattern means "any synergy edge in either direction." It does **not** require OWL reasoning, so it works
directly against an unmaterialized graph in any SPARQL 1.1 engine (rdflib, Apache Jena, GraphDB, Stardog,
Blazegraph, Virtuoso).

If you have OWL-RL closure available, the explicit `:isAmplifiedBy` / `:isEnabledBy` and reverse
`:hasSynergyWith` triples are already materialized as inverses, so a simpler `?card mc:hasSynergyWith ?other`
pattern is sufficient.

## Running the queries

Most SPARQL engines do **not** follow `owl:imports` automatically, so load the ontology, the per-set instance
files, the deck graph and the synergy graph explicitly.

### rdflib (Python)

```python
from pathlib import Path
from rdflib import Graph

g = Graph()
for ttl in ["MagicCardsOntology.ttl", "MagicCardSynergies.ttl",
            "MagicCardCollection.ttl",
            *Path("sets").glob("*.ttl"), *Path("decks").glob("*.ttl")]:
    g.parse(ttl, format="turtle")

query = Path("queries/01_deck_inventory/01_list_all_decks.rq").read_text()
for row in g.query(query):
    print(row)
```

### Apache Jena `arq`

```bash
arq --data MagicCardsOntology.ttl \
    --data MagicCardSynergies.ttl \
    --data MagicCardCollection.ttl \
    $(printf -- '--data %s ' sets/*.ttl decks/*.ttl) \
    --query queries/01_deck_inventory/01_list_all_decks.rq
```

### GraphDB / Stardog / Blazegraph

Load all TTL files into a single repository / database, then paste the contents of any `.rq` file into the
Workbench SPARQL editor.

## Notes on the data model

A few patterns recur across queries:

- **Deck membership** is asserted directly: `?deck mc:hasCard ?c` (inverse
  `mc:isInDeck`). Copy counts are reified as `DeckEntry` individuals —
  `?deck mc:hasDeckEntry ?e . ?e mc:entryCard ?c ; mc:quantity ?n .` — and
  several queries (`01/02`, `01/03`, `01/05`, `01/08`, `07/06`) are written
  against them. The Saheeli deck asserts one entry per unique card
  (quantity 1 except the basic lands: 8 Island, 2 Forest, 2 Mountain;
  total = 100).
- **Physical inventory** is reified analogously in
  `MagicCardCollection.ttl`: `mc:MagicCardCollection mc:hasCollectionEntry ?e .
  ?e mc:entryCard ?c ; mc:quantity ?n ; mc:hasFinish ?f ; mc:hasCondition ?cond .`
  with optional `mc:purchasePrice`. One `CollectionEntry` corresponds to one
  acquisition lot from `collection.csv`; a printing collected in several
  finishes or lots has several entries, so always aggregate with `SUM(?n)`.
  `DeckEntry` and `CollectionEntry` share the `mc:entryCard` / `mc:quantity`
  properties via their common superclass `mc:CardEntry` — restrict by class
  (`?e rdf:type mc:CollectionEntry`) or traverse from the container when the
  distinction matters. Every card referenced by the Saheeli deck is an
  inventoried printing from `collection.csv`, so all deck cards are backed by
  collection entries.
- **Colors and color identity**: cards assert both `mc:hasColor` and
  `mc:hasColorIdentity`. Colorless cards are recognizable by the absence of
  any `mc:hasColorIdentity` values.
- **Basic lands** in the Saheeli deck are distinct printing-specific Card individuals named
  `mc:ForestAetherdrift291`, `mc:IslandAetherdrift282`, `mc:MountainAetherdrift288` (from the
  `sets/Aetherdrift.ttl` set graph, set code `DFT`) — the bare `mc:Forest` / `mc:Island` / `mc:Mountain`
  IRIs are reserved for the basic-land *subtype* individuals in the main ontology.
- **Legality** is reified as a `LegalityMapping` per (card, format):
  `?card mc:hasLegality ?lm . ?lm mc:inFormat ?fmt ; mc:hasLegalityStatus ?status .`
- **Rulings** are reified as `Ruling` individuals with `mc:rulingDate` and `mc:rulingText`.
