//! A CR-grounded Magic: The Gathering rules engine for Commander.
//!
//! The Rust port of the Python `mtgrules` package. Module map (one module
//! per former Python module):
//!
//! - `cr`: Comprehensive Rules text parser and the `#[rule]` registry
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
//! Every rules-implementing item is annotated with `#[rule("…")]`; the
//! binaries validate those numbers against the Comprehensive Rules text
//! at startup.
//!
//! Modules land in milestones M4-M7; this is the workspace scaffold.
