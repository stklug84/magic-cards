//! Developer tasks for the Rust workspace: `cargo xtask <task>`.
//!
//! The alias lives in `rust/.cargo/config.toml`. Tasks are added as the
//! milestones need them: `vectors` (regenerate the CPython reference
//! vectors), `parity` (compare the Rust and Python simulators level by
//! level) and `golden` (freeze expected outputs) arrive in M2.

use std::process::ExitCode;

const USAGE: &str = "usage: cargo xtask <task>

tasks:
  help    print this message
";

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        None | Some("help" | "--help" | "-h") => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some(other) => {
            eprintln!("xtask: unknown task {other:?}\n{USAGE}");
            ExitCode::from(2)
        }
    }
}
