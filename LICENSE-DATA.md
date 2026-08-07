# Licensing

This repository combines original software, original knowledge modelling,
and data derived from Magic: The Gathering. Different parts carry different
licences. The root [`LICENSE`](LICENSE) file contains the MIT licence text
only; this file defines its scope and the licences of everything else.

## Scope map

| Path | Licence |
|------|---------|
| `scripts/**` (the `mtgcards`, `mtgrules`, `mtgviz` packages and all tooling) | [MIT](LICENSE) |
| `queries/**` (SPARQL query catalog) | [MIT](LICENSE) |
| `.github/**`, `pyproject.toml`, lint configurations | [MIT](LICENSE) |
| `MagicCardsOntology.ttl` | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `MagicCardSynergies.ttl` | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `MagicSimulationAnnotations.ttl` | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `sets/*.ttl`, `MagicCardIndividuals.ttl`, `MagicCardCollection.ttl` | No licence granted — see below |

## Ontology and synergy graphs (CC BY-SA 4.0)

The hand-authored knowledge modelling — the class/property schema in
`MagicCardsOntology.ttl`, the synergy relations in
`MagicCardSynergies.ttl`, and the simulation annotations in
`MagicSimulationAnnotations.ttl` — is original creative work by
Steffen Klug, licensed under the
[Creative Commons Attribution-ShareAlike 4.0 International licence](https://creativecommons.org/licenses/by-sa/4.0/)
(`CC-BY-SA-4.0`).

The CC BY-SA 4.0 grant covers the **modelling only**: the class
hierarchy, property definitions, axioms, synergy assertions, and
annotations. It does **not** — and cannot — cover the Wizards of the
Coast intellectual property referenced within those files (see the
carve-out below).

## Generated card data (no licence granted)

The per-set instance graphs (`sets/*.ttl`), their aggregator
(`MagicCardIndividuals.ttl`), and the collection inventory
(`MagicCardCollection.ttl`) are generated files containing verbatim
card names, oracle text, printed text, flavor text, and official
rulings. **No copyright licence is granted for these files.** They are
redistributed as unofficial Fan Content under the
[Wizards of the Coast Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy);
the underlying card data remains the property of Wizards of the Coast
LLC.

## Wizards of the Coast carve-out

Magic: The Gathering card names, mana symbols, oracle/printed/flavor
text, rulings, set names, and game terminology appearing **anywhere in
this repository — including within the CC BY-SA 4.0 licensed files —**
are the intellectual property of Wizards of the Coast LLC and/or their
respective owners and are **not** covered by the MIT or CC BY-SA 4.0
grants above.

This project is unofficial Fan Content permitted under the Fan Content
Policy. Not approved/endorsed by Wizards. Portions of the materials
used are property of Wizards of the Coast. © Wizards of the Coast LLC.

## Data source attribution

Card data was sourced from [Scryfall](https://scryfall.com) and
[Gatherer](https://gatherer.wizards.com) under their respective data
distribution policies. Scryfall data is provided free of charge for
non-commercial use; this repository does not use Scryfall card imagery.

The Magic: The Gathering Comprehensive Rules text consumed by the rules
engine is **not distributed** in this repository — it must be downloaded
from Wizards of the Coast directly (see the
[README](README.md#dependencies)).
