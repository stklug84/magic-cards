//! Canonical dump of the card database (parity level L2).
//!
//! The Rust twin of `scripts/parity/dump_cards.py`: loads a graph root
//! through [`CardDatabase`] and writes one JSON line per index entry, in
//! index order, with the same shape and the same `json.dumps` bytes.
//! `cargo xtask parity` runs both and diffs them.
//!
//! ```text
//! cargo run -p mtgcards --example dump_cards -- --root ../.. [--custom-cards F] [--extra-graph G]...
//! ```

use std::io::Write as _;
use std::path::PathBuf;
use std::process::ExitCode;

use mtgcards::CardDatabase;
use pycompat::json::{self, Value};

struct Args {
    root: PathBuf,
    custom_cards: Option<PathBuf>,
    extra_graphs: Vec<PathBuf>,
    out: Option<PathBuf>,
}

fn parse_args() -> Result<Args, String> {
    let mut args = Args {
        root: PathBuf::from(".."),
        custom_cards: None,
        extra_graphs: Vec::new(),
        out: None,
    };
    let mut iter = std::env::args().skip(1);
    while let Some(flag) = iter.next() {
        let mut value = || iter.next().ok_or_else(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--root" => args.root = PathBuf::from(value()?),
            "--custom-cards" => args.custom_cards = Some(PathBuf::from(value()?)),
            "--extra-graph" => args.extra_graphs.push(PathBuf::from(value()?)),
            "--out" => args.out = Some(PathBuf::from(value()?)),
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    Ok(args)
}

/// The summary line, then one line per index entry.
fn dump_lines(db: &CardDatabase) -> Vec<String> {
    let unique = db.index.unique_ids();
    let ordinal = |id| {
        unique
            .iter()
            .position(|u| *u == id)
            .expect("every record has an ordinal")
    };
    let summary = Value::dict([(
        "summary",
        Value::dict([
            ("names", Value::from(db.index.len())),
            ("records", Value::from(unique.len())),
            (
                "ind2name",
                Value::Dict(
                    db.ind2name
                        .iter()
                        .map(|(k, v)| (k.clone(), Value::from(v.as_str())))
                        .collect(),
                ),
            ),
            (
                "stubbed",
                Value::List(db.stubbed.iter().map(|s| Value::from(s.as_str())).collect()),
            ),
        ]),
    )]);
    let mut lines = vec![json::dumps(&summary)];
    for name in db.index.names() {
        let id = db.index.id_of(name).expect("indexed name");
        let line = Value::dict([
            ("key", Value::from(name)),
            ("record", Value::from(ordinal(id))),
            ("card", db.index.record(id).to_value()),
        ]);
        lines.push(json::dumps(&line));
    }
    lines
}

fn run() -> Result<(), String> {
    let args = parse_args()?;
    let db = CardDatabase::load(&args.root, args.custom_cards.as_deref(), &args.extra_graphs)
        .map_err(|e| e.to_string())?;
    let mut text = dump_lines(&db).join("\n");
    text.push('\n');
    match args.out {
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
            eprintln!("dump_cards: {message}");
            ExitCode::from(1)
        }
    }
}
