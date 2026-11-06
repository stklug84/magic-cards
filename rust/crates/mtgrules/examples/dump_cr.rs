//! Canonical dump of the parsed Comprehensive Rules index (parity L2).
//!
//! The Rust twin of `scripts/parity/dump_cr.py`: one JSON line per rule,
//! then one per glossary term, with texts as code-point length plus
//! FNV-1a 64-bit digest (the CR text is not redistributable, so the dump
//! never contains it). `cargo xtask parity` diffs the two.
//!
//! ```text
//! cargo run -p mtgrules --example dump_cr -- [--cr-file F | --root DIR] [--out FILE]
//! ```

use std::io::Write as _;
use std::path::PathBuf;
use std::process::ExitCode;

use mtgrules::cr::ComprehensiveRules;
use pycompat::json::{self, Value};

/// FNV-1a 64-bit digest of `text`'s UTF-8 bytes, as 16 hex digits.
fn fnv1a64(text: &str) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in text.bytes() {
        h ^= u64::from(byte);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{h:016x}")
}

fn digest(text: &str) -> [(&'static str, Value); 2] {
    [
        ("len", Value::from(text.chars().count())),
        ("fnv", Value::from(fnv1a64(text))),
    ]
}

fn dump_lines(cr: &ComprehensiveRules) -> Vec<String> {
    let summary = Value::dict([(
        "summary",
        Value::dict([
            ("file", Value::from(cr.file_name())),
            ("rules", Value::from(cr.rules.len())),
            ("examples", Value::from(cr.example_count())),
            ("glossary", Value::from(cr.glossary.len())),
        ]),
    )]);
    let mut lines = vec![json::dumps(&summary)];
    for (number, rule) in &cr.rules {
        let [len, fnv] = digest(&rule.text);
        let examples = rule
            .examples
            .iter()
            .map(|ex| Value::dict(digest(ex)))
            .collect();
        lines.push(json::dumps(&Value::dict([
            ("rule", Value::from(number.as_str())),
            len,
            fnv,
            ("examples", Value::List(examples)),
        ])));
    }
    for (term, definition) in &cr.glossary {
        let [len, fnv] = digest(definition);
        lines.push(json::dumps(&Value::dict([
            ("term", Value::from(term.as_str())),
            len,
            fnv,
        ])));
    }
    lines
}

fn run() -> Result<(), String> {
    let mut cr_file: Option<PathBuf> = None;
    let mut root: Option<PathBuf> = None;
    let mut out: Option<PathBuf> = None;
    let mut iter = std::env::args().skip(1);
    while let Some(flag) = iter.next() {
        let value = iter.next().ok_or_else(|| format!("{flag} needs a value"))?;
        match flag.as_str() {
            "--cr-file" => cr_file = Some(PathBuf::from(value)),
            "--root" => root = Some(PathBuf::from(value)),
            "--out" => out = Some(PathBuf::from(value)),
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    let cr = match (cr_file, root) {
        (Some(file), _) => ComprehensiveRules::load(&file),
        (None, Some(dir)) => ComprehensiveRules::load_newest(&dir),
        (None, None) => mtgrules::cr::get_cr().cloned(),
    }
    .map_err(|e| e.to_string())?;
    let mut text = dump_lines(&cr).join("\n");
    text.push('\n');
    match out {
        Some(path) => std::fs::write(&path, text).map_err(|e| format!("{}: {e}", path.display())),
        None => std::io::stdout()
            .write_all(text.as_bytes())
            .map_err(|e| e.to_string()),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("dump_cr: {message}");
            ExitCode::from(1)
        }
    }
}
