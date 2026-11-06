//! A CR-grounded Magic: The Gathering rules engine for Commander.
//!
//! The Rust port of the Python `mtgrules` package. Module map (one module
//! per former Python module):
//!
//! - [`cr`]: Comprehensive Rules text parser, the `#[rule]` registry, its
//!   startup validation and the coverage report
//! - `objects`, `events`, `stack`, `manasys`: game objects, event records,
//!   stack items, mana pools and cost payment
//! - `abilities`, `effects`: ability model and the one-shot effect AST
//! - `layers`, `replacements`: continuous effects (CR 613) and replacement
//!   effects (CR 614-616)
//! - `game`, `combat`, `turns`: the rules kernel, the combat phase and the
//!   turn structure
//! - `compiler`, `overrides`: oracle text to abilities, hand-written cards
//! - `policy`, `adapter`, `selfplay`: the AI, the match runner, tuning
//!
//! Every rules-implementing item is annotated with [`rule`]
//! (`#[rule("704.5g")]`); rules deliberately out of scope are declared
//! with [`unsupported!`]. The binaries validate those numbers against the
//! Comprehensive Rules text at startup ([`cr::check_registry`]).
//!
//! The engine modules land in milestones M5-M7.

// the #[rule] macro expands to `::mtgrules::...` paths; this lets the
// crate use its own macros
#[expect(
    unused_extern_crates,
    reason = "no engine module uses #[rule] yet (they land in M5)"
)]
extern crate self as mtgrules;

pub mod cr;

pub use mtgrules_macros::{rule, unsupported};

/// Re-exports the macros expand to; not part of the public API.
#[doc(hidden)]
pub mod __private {
    pub use linkme;
}
