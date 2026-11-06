//! Shared command-line plumbing for the simulator binaries.
//!
//! The binaries (`mtg-cr` here; `mtg-simulate`, `mtg-selfplay` and
//! `mtg-graph` arrive with the engine in M7) share:
//!
//! - the graph root: `--graph-root DIR`, else `MTG_GRAPH_ROOT`, else the
//!   nearest parent of the working directory holding
//!   `MagicCardsOntology.ttl` and `sets/` ([`mtgcards::graph::graph_root`]);
//! - the Comprehensive Rules text: the newest `MagicCompRules-*.txt` at
//!   the graph root, and the startup validation of every `#[rule]`
//!   annotation against it ([`startup_check`]) — skipped only for
//!   `--help`, `--version` and replay-only runs, which never touch the
//!   rules;
//! - the exit-code conventions: 0 success, 1 a reported error, 2 a usage
//!   error (clap's default).

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use mtgrules::cr::{ComprehensiveRules, check_registry};

/// Exit code of a reported error (a message on stderr, no traceback).
pub const EXIT_ERROR: u8 = 1;
/// Exit code of a usage error.
pub const EXIT_USAGE: u8 = 2;

/// The graph root of this run: the `--graph-root` option, else the
/// process default.
///
/// # Errors
///
/// A message naming the option and the environment variable.
pub fn resolve_graph_root(option: Option<&Path>) -> Result<PathBuf, String> {
    match option {
        Some(dir) if dir.is_dir() => Ok(dir.to_path_buf()),
        Some(dir) => Err(format!("--graph-root {}: not a directory", dir.display())),
        None => mtgcards::graph::graph_root().map_err(|e| e.to_string()),
    }
}

/// Load the Comprehensive Rules: `--cr-file FILE` when given, else the
/// newest `MagicCompRules-*.txt` at the graph root.
///
/// # Errors
///
/// A message when neither is available or the file cannot be read.
pub fn load_cr(
    graph_root: Option<&Path>,
    cr_file: Option<&Path>,
) -> Result<ComprehensiveRules, String> {
    if let Some(file) = cr_file {
        return ComprehensiveRules::load(file).map_err(|e| e.to_string());
    }
    let root = resolve_graph_root(graph_root)?;
    ComprehensiveRules::load_newest(&root).map_err(|e| {
        format!(
            "{e} (looked in {}; set MTG_GRAPH_ROOT or pass --cr-file)",
            root.display()
        )
    })
}

/// Validate the `#[rule]` registry of this binary against `cr`: what the
/// Python decorators do at import time. Every binary that runs the engine
/// calls this before doing anything else.
///
/// # Errors
///
/// The unknown numbers, one per line.
pub fn startup_check(cr: &ComprehensiveRules) -> Result<(), String> {
    check_registry(cr).map_err(|e| e.to_string())
}

/// Print `message` to stderr prefixed with the program name and return
/// the error exit code.
#[must_use]
pub fn fail(program: &str, message: &str) -> ExitCode {
    eprintln!("{program}: {message}");
    ExitCode::from(EXIT_ERROR)
}
