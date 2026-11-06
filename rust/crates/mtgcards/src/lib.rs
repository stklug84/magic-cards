//! Card data layer over the MTG knowledge graph.
//!
//! Ported from the simulator parts of the Python `mtgcards` package, which
//! it replaced (what remains of that package in `scripts/mtgcards/` is the
//! graph-bundle contract and the behavior-key whitelist the validators
//! share with this crate):
//!
//! - [`graph`]: the graph root's `GRAPH-MANIFEST.json` and the enforced
//!   `graph_schema` check;
//! - [`ttl_loader`], [`deck_ttl`], [`behavior`]: text-based readers over
//!   the generated Turtle (card individuals, deck instance graphs,
//!   `MagicSimulationAnnotations.ttl`);
//! - [`cards`]: the card record and the oracle-text derivation of
//!   behaviour keys; [`index`]: the name index with shared records;
//! - [`database`]: the layered, name-indexed card database;
//! - [`deck`]: txt decklists and the deck model;
//! - [`mana`]: the mana-cost grammar used for a card's mana value;
//! - [`stats`]: match records, the Wilson interval and the report;
//! - [`scryfall`]: the cached named-card client for txt decklists (its
//!   real HTTP transport is behind the `scryfall` feature; the client's
//!   logic is always available with an injected transport).
//!
//! **The TTL readers are regex-based, not RDF-based**, so the simulator
//! needs no RDF library. A layout change in `scripts/generate_individuals.py`
//! must be mirrored here (`ttl_loader`, `deck`, `behavior`) and covered by
//! `tests/ttl_loader.rs`; see AGENTS.md, "The trap: text-based readers".
//!
//! Sets of strings (types, subtypes, colours, keywords) are `BTreeSet`s:
//! Python's `set` has no observable order, and a sorted order is the
//! canonical one every report and dump uses. Files are read with
//! Python's text-mode semantics (`pycompat::pyio::read_text`) and paths
//! are shown as `str(Path(p))`, so messages match the Python ones.

pub mod behavior;
pub mod cards;
pub mod database;
pub mod deck;
pub mod deck_ttl;
pub mod graph;
pub mod index;
pub mod jsonval;
pub mod mana;
pub mod scryfall;
pub mod stats;
pub mod ttl_loader;

pub use cards::CardData;
pub use database::{CardDatabase, DatabaseError};
pub use deck::{Deck, DeckError, load_deck};
pub use graph::GraphSchemaError;
pub use index::{CardId, CardIndex};
