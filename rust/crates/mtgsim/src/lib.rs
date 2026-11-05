//! Shared command-line plumbing for the simulator binaries.
//!
//! The binaries (`mtg-simulate`, `mtg-selfplay`, `mtg-cr`, `mtg-graph`)
//! are added in milestone M7, together with the graph-root discovery
//! (`MTG_GRAPH_ROOT`, else the nearest parent of the working directory
//! holding `MagicCardsOntology.ttl` and `sets/`) and the exit-code
//! conventions (1 for a reported error, 2 for a usage error). This is the
//! workspace scaffold.
