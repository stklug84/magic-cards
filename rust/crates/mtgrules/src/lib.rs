//! A CR-grounded Magic: The Gathering rules engine for Commander.
//!
//! The Rust port of the Python `mtgrules` package. Module map (one module
//! per former Python module):
//!
//! - [`cr`]: Comprehensive Rules text parser, the `#[rule]` registry, its
//!   startup validation and the coverage report
//! - [`objects`], [`events`], [`stack`], [`manasys`]: game objects, event
//!   records, stack items, mana pools and cost payment
//! - [`abilities`], [`effects`]: the ability model and the one-shot
//!   effect AST
//! - [`layers`], [`replacements`]: continuous effects (CR 613) and
//!   replacement effects (CR 614-616)
//! - [`game`]: the state, the kernel helpers and - in its submodules -
//!   the rules of play: zone changes, the trigger queue, the game
//!   actions, mana payment, casting, resolution, state-based actions and
//!   priority
//! - [`combat`], [`turns`]: the combat phase and the turn structure
//! - [`compiler`], [`overrides`]: oracle text to abilities, hand-written
//!   cards
//! - [`policy`], [`adapter`], [`selfplay`]: the decision policy and the
//!   AI, the match runner, tuning
//! - [`protocols`]: the closure signatures card behaviour is wired with
//!
//! Every rules-implementing item is annotated with [`rule`]
//! (`#[rule("704.5g")]`); rules deliberately out of scope are declared
//! with [`unsupported!`]. The binaries validate those numbers against the
//! Comprehensive Rules text at startup ([`cr::check_registry`]).
//!
//! Where the Python engine shares mutable objects by reference, this one
//! keeps an arena in the [`game::Game`] and passes handles
//! ([`objects::ObjId`], [`objects::PlayerId`]); closures capture handles
//! and receive the game when they run.

// the #[rule] macro expands to `::mtgrules::...` paths; this lets the
// crate use its own macros
extern crate self as mtgrules;

pub mod abilities;
pub mod adapter;
pub mod combat;
pub mod compiler;
pub mod cr;
pub mod effects;
pub mod events;
pub mod game;
pub mod layers;
pub mod manasys;
pub mod objects;
pub mod overrides;
pub mod policy;
pub mod protocols;
pub mod replacements;
pub mod selfplay;
pub mod stack;
pub mod turns;

pub use game::Game;
pub use mtgrules_macros::{rule, unsupported};

/// Re-exports the macros expand to; not part of the public API.
#[doc(hidden)]
pub mod __private {
    pub use linkme;
}
