//! `#[rule("601.2", "601.2h")]`: Comprehensive Rules traceability.
//!
//! The Python engine decorates every rules-implementing function with
//! `@rule(...)`, which validates the rule numbers against the CR text at
//! import time and fills a registry used by the coverage report. The Rust
//! counterpart is this attribute macro: it checks the rule-number syntax
//! at compile time and registers the annotated item (via a distributed
//! slice) so the binaries can validate the numbers against the newest
//! `MagicCompRules-*.txt` at startup and `mtg-cr coverage` can list them.
//!
//! The macro lands in milestone M4; this is the workspace scaffold.
