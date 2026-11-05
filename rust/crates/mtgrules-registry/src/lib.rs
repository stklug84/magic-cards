//! The link-time registry behind `#[rule("…")]` and `unsupported!`.
//!
//! The two distributed slices live in their own crate on purpose: linkme
//! registers an element through a `macro_rules!` that the slice
//! declaration generates, and the `#[link_section]` attribute that macro
//! emits is subject to the `unsafe_code` lint only when the macro is
//! local to the crate being compiled. Declaring the slices here keeps
//! that macro external to every crate that registers rules - `mtgrules`
//! included - so the workspace-wide `forbid(unsafe_code)` stays in force
//! and no crate carries an `allow`. `mtgrules::cr` re-exports everything
//! in this crate; nothing depends on it directly.

/// One `#[rule(...)]` annotation.
#[derive(Debug)]
pub struct RuleImpl {
    /// the rule numbers in canonical (dot-less) form
    pub numbers: &'static [&'static str],
    /// `module::path::name` of the annotated item
    pub implementer: &'static str,
    /// the item kind: `fn`, `struct`, ...
    pub kind: &'static str,
    /// the source file of the annotation
    pub file: &'static str,
    /// the line of the annotation
    pub line: u32,
}

/// One `unsupported!(...)` declaration.
#[derive(Debug)]
pub struct Unsupported {
    /// the rule number (or family), canonical form
    pub number: &'static str,
    /// why it is out of scope
    pub reason: &'static str,
    /// the source file of the declaration
    pub file: &'static str,
    /// the line of the declaration
    pub line: u32,
}

/// Every `#[rule]` annotation of the linked program.
#[linkme::distributed_slice]
pub static RULE_IMPLEMENTATIONS: [RuleImpl];

/// Every `unsupported!` declaration of the linked program.
#[linkme::distributed_slice]
pub static UNSUPPORTED: [Unsupported];
