//! Card data layer over the MTG knowledge graph.
//!
//! Replaces the simulator parts of the Python `mtgcards` package:
//! `graph` (manifest and `graph_schema` check), `ttl_loader` and
//! `deck_ttl` (text-based readers over the generated Turtle), `behaviors`
//! (`MagicSimulationAnnotations.ttl` hooks and threat weights), `cards`
//! (card record and oracle-text derivation), `database`, `deck` (txt
//! decklists), `stats` (match statistics) and, behind the `scryfall`
//! feature, the Scryfall client.
//!
//! **The TTL readers are regex-based, not RDF-based** — exactly like their
//! Python counterparts. A layout change in `scripts/generate_individuals.py`
//! must be mirrored here and in `scripts/mtgcards/ttl_loader.py`; see
//! AGENTS.md, "The trap: text-based readers".
//!
//! Modules land in milestone M3; this is the workspace scaffold.
