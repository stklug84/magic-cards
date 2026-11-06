//! Developer tasks for the Rust workspace: `cargo xtask <task>`.
//!
//! The alias lives in `rust/.cargo/config.toml`. Tasks:
//!
//! - `vectors [--check]`: regenerate the CPython reference vectors the
//!   `pycompat` tests read (`scripts/parity/vectors.py`), or verify that
//!   the committed files are up to date;
//! - `help`.
//!
//! `parity` (compare the Rust and Python simulators level by level) and
//! `golden` (freeze expected outputs) arrive with the crates they test.
//!
//! The Python interpreter is `$PYTHON`, else `python3`.

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const USAGE: &str = "usage: cargo xtask <task>

tasks:
  vectors [--check]   regenerate the CPython reference vectors under
                      rust/crates/pycompat/tests/vectors (or check that
                      the committed files are current)
  help                print this message
";

/// The repository root: two levels above this crate's manifest.
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("rust/xtask lives two levels below the repository root")
        .to_path_buf()
}

fn python() -> String {
    std::env::var("PYTHON").unwrap_or_else(|_| "python3".to_owned())
}

/// Run `python -m parity.vectors <out_dir>` from `scripts/`.
fn generate_vectors(root: &Path, out_dir: &Path) -> Result<(), String> {
    let status = Command::new(python())
        .args(["-m", "parity.vectors"])
        .arg(out_dir)
        .current_dir(root.join("scripts"))
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .status()
        .map_err(|e| format!("cannot run {}: {e}", python()))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("vector generation failed: {status}"))
    }
}

fn vectors(check: bool) -> Result<(), String> {
    let root = repo_root();
    let committed = root.join("rust/crates/pycompat/tests/vectors");
    if !check {
        generate_vectors(&root, &committed)?;
        println!("vectors written to {}", committed.display());
        return Ok(());
    }
    let scratch = std::env::temp_dir().join(format!("pycompat-vectors-{}", std::process::id()));
    std::fs::create_dir_all(&scratch).map_err(|e| format!("{}: {e}", scratch.display()))?;
    let result =
        generate_vectors(&root, &scratch).and_then(|()| compare_dirs(&committed, &scratch));
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

/// Every file of `fresh` must exist in `committed` with identical bytes.
fn compare_dirs(committed: &Path, fresh: &Path) -> Result<(), String> {
    let mut stale = Vec::new();
    let entries = std::fs::read_dir(fresh).map_err(|e| format!("{}: {e}", fresh.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("{}: {e}", fresh.display()))?;
        let name = entry.file_name();
        let new_bytes =
            std::fs::read(entry.path()).map_err(|e| format!("{}: {e}", name.to_string_lossy()))?;
        let old_bytes = std::fs::read(committed.join(&name)).unwrap_or_default();
        if new_bytes != old_bytes {
            stale.push(name.to_string_lossy().into_owned());
        }
    }
    if stale.is_empty() {
        println!("vectors are current");
        Ok(())
    } else {
        Err(format!(
            "stale vectors: {} (run `cargo xtask vectors`)",
            stale.join(", ")
        ))
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        None | Some("help" | "--help" | "-h") => {
            print!("{USAGE}");
            Ok(())
        }
        Some("vectors") => vectors(args.iter().any(|a| a == "--check")),
        Some(other) => Err(format!("unknown task {other:?}\n{USAGE}")),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("xtask: {message}");
            ExitCode::from(2)
        }
    }
}
