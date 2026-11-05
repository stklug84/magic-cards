//! The matchup simulator's command line (the Python `mtgrules.cli`):
//! argument validation, the shared deck and card-data loaders, the game
//! loop over seeds and the final report.
//!
//! Exit codes follow the Python module: usage errors (what `argparse`
//! rejects and the count checks) exit 2, data errors (`sys.exit(msg)`)
//! exit 1 with the message on stderr; the stdout report is byte for byte
//! the Python one. The usage text itself is clap's, not argparse's.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use mtgcards::stats::Aggregator;
use mtgcards::{CardDatabase, Deck, load_deck};
use mtgrules::adapter::{MatchOptions, SharedRecorder, model_coverage_lines, run_game};
use mtgrules::policy::{PolicyProfile, get_profile};
use pycompat::json::Value;
use pycompat::pyrepr::repr_str;
use pycompat::random::PyRandom;

/// A match takes 2-4 decklists (Commander pod sizes).
pub const MIN_DECKS: usize = 2;
/// A match takes 2-4 decklists (Commander pod sizes).
pub const MAX_DECKS: usize = 4;
/// How many stubbed card names the warning lists before eliding.
const STUB_PREVIEW: usize = 8;

/// A clean exit: the message for stderr and the status.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Exit {
    /// what to print (without the program prefix)
    pub message: String,
    /// the exit status: 2 for usage errors, 1 for data errors
    pub code: u8,
}

impl Exit {
    /// A usage error (`parser.error(...)`): exit 2.
    #[must_use]
    pub fn usage(message: impl Into<String>) -> Self {
        Exit {
            message: message.into(),
            code: crate::EXIT_USAGE,
        }
    }

    /// A data error (`sys.exit(message)`): exit 1.
    #[must_use]
    pub fn error(message: impl Into<String>) -> Self {
        Exit {
            message: message.into(),
            code: crate::EXIT_ERROR,
        }
    }
}

/// Parse the `--seeds` specification into a seed list.
///
/// Accepts a count (`5` -> base..base+4) or an explicit comma-separated
/// list (`1,7,100`).
///
/// # Errors
///
/// The Python `ValueError` text for anything else, including a count
/// below 1 and an empty list.
pub fn parse_seeds(base_seed: i64, spec: &str) -> Result<Vec<i64>, String> {
    let int = |s: &str| -> Result<i64, String> {
        i64::try_from(pycompat::pystr::parse_int(s)?)
            .map_err(|_| format!("Python int too large to convert to C long: {}", repr_str(s)))
    };
    if spec.contains(',') {
        let seeds: Vec<i64> = spec
            .split(',')
            .filter(|s| !s.trim().is_empty())
            .map(int)
            .collect::<Result<_, _>>()?;
        if seeds.is_empty() {
            return Err("--seeds needs at least one seed".to_owned());
        }
        return Ok(seeds);
    }
    let count = int(spec)?;
    if count < 1 {
        return Err("--seeds must be >= 1".to_owned());
    }
    Ok((0..count).map(|i| base_seed + i).collect())
}

/// Resolve `--profile SEAT=NAME` options into a per-seat profile list.
///
/// # Errors
///
/// A malformed spec, a seat out of range, or an unknown preset.
pub fn parse_profiles(
    specs: &[String],
    n_decks: usize,
) -> Result<Vec<Option<PolicyProfile>>, Exit> {
    let mut profiles: Vec<Option<PolicyProfile>> = vec![None; n_decks];
    for spec in specs {
        let (seat, name) = spec.split_once('=').unwrap_or((spec.as_str(), ""));
        let Ok(number) = pycompat::pystr::parse_int(seat) else {
            return Err(Exit::error(format!(
                "--profile expects SEAT=NAME, got {}",
                repr_str(spec)
            )));
        };
        let idx = number - 1;
        if idx < 0 || idx >= n_decks as i128 {
            return Err(Exit::error(format!("--profile seat {seat} out of range")));
        }
        let idx = usize::try_from(idx).unwrap_or(0);
        profiles[idx] = Some(get_profile(name).map_err(Exit::error)?);
    }
    Ok(profiles)
}

/// Validate the deck file arguments before any expensive loading.
///
/// # Errors
///
/// Not 2-4 files, or a file that does not exist.
pub fn deck_paths(decks: &[String], min: usize, max: usize) -> Result<Vec<PathBuf>, Exit> {
    let paths: Vec<PathBuf> = decks.iter().map(PathBuf::from).collect();
    if !(min..=max).contains(&paths.len()) {
        let what = if min == 1 {
            "pass 1 (mirror) or 2 deck files (txt decklists or .ttl deck graphs)"
        } else {
            "pass 2-4 deck files (txt decklists or .ttl deck graphs)"
        };
        return Err(Exit::error(what));
    }
    for p in &paths {
        if !p.exists() {
            return Err(Exit::error(format!("deck file not found: {}", p.display())));
        }
    }
    Ok(paths)
}

/// Load the card data, turning every data error into a clean exit.
///
/// Broken or incompatible `GRAPH-MANIFEST.json` files, unreadable or
/// malformed `--custom-cards` JSON and annotation files with unknown
/// behavior keys all carry their path in the message. Shared with
/// `mtg-selfplay`.
///
/// # Errors
///
/// `cannot load card data: <reason>` (exit 1).
pub fn open_database(
    root: &Path,
    custom_cards: Option<&Path>,
    extra_graphs: &[PathBuf],
) -> Result<CardDatabase, Exit> {
    CardDatabase::load(root, custom_cards, extra_graphs)
        .map_err(|err| Exit::error(format!("cannot load card data: {err}")))
}

/// Load and validate the deck files named on the command line.
///
/// Decks that share a name (the same file twice, or two files with the
/// same stem) get `#2`, `#3` ... suffixes so every seat is distinct;
/// every deck needs a commander. Skipped decklist lines are reported on
/// stderr through `warn`. Shared with `mtg-selfplay`.
///
/// # Errors
///
/// A deck that does not parse, or one without a commander.
pub fn load_decks(
    paths: &[PathBuf],
    db: &CardDatabase,
    warn: &mut dyn FnMut(&str),
) -> Result<Vec<Deck>, Exit> {
    let mut decks = Vec::with_capacity(paths.len());
    for p in paths {
        decks.push(load_deck(p, Some(&db.ind2name)).map_err(|e| Exit::error(e.to_string()))?);
    }
    for d in &decks {
        if !d.skipped.is_empty() {
            let preview: Vec<String> = d
                .skipped
                .iter()
                .take(STUB_PREVIEW)
                .map(|(n, text)| format!("{n}: {}", repr_str(text)))
                .collect();
            let more = if d.skipped.len() > STUB_PREVIEW {
                " ..."
            } else {
                ""
            };
            warn(&format!(
                "warning: {}: skipped {} line(s) that are not 'COUNT Card Name' (line: text): {}{more}",
                d.path,
                d.skipped.len(),
                preview.join(", ")
            ));
        }
    }
    let mut seen: Vec<(String, usize)> = Vec::new();
    for d in &mut decks {
        // same list twice -> unique seats
        let n = if let Some((_, n)) = seen.iter_mut().find(|(name, _)| *name == d.name) {
            *n += 1;
            *n
        } else {
            seen.push((d.name.clone(), 1));
            1
        };
        if n > 1 {
            d.name = format!("{}#{n}", d.name);
        }
    }
    for d in &decks {
        if d.commander.is_none() {
            let hint = if d.fmt == "ttl" {
                "no ':isCommanderOf' assertion found"
            } else {
                "no '// Commander' section found"
            };
            return Err(Exit::error(format!("{}: {hint}", d.path)));
        }
    }
    Ok(decks)
}

/// The distinct card names of the txt decklists (sorted), for the
/// Scryfall lookups.
#[must_use]
pub fn txt_card_names(decks: &[Deck]) -> Vec<String> {
    let names: BTreeSet<String> = decks
        .iter()
        .filter(|d| d.fmt == "txt")
        .flat_map(|d| d.cards.iter().cloned().chain(d.commander.clone()))
        .collect();
    names.into_iter().collect()
}

/// The warning for Scryfall lookups that fell back to the graph.
#[must_use]
pub fn scryfall_warning(failed: &[String]) -> String {
    let shown: Vec<&str> = failed
        .iter()
        .take(STUB_PREVIEW)
        .map(String::as_str)
        .collect();
    let more = if failed.len() > STUB_PREVIEW {
        " ..."
    } else {
        ""
    };
    format!(
        "warning: Scryfall lookup failed for {} card(s); falling back to the knowledge graph: {}{more}",
        failed.len(),
        shown.join(", ")
    )
}

/// Resolve every card once and return the stub warning, if any.
pub fn audit_unknown_cards(decks: &[Deck], db: &mut CardDatabase) -> Option<String> {
    for d in decks {
        let names: BTreeSet<&str> = d
            .cards
            .iter()
            .map(String::as_str)
            .chain(d.commander.as_deref())
            .collect();
        for c in names {
            db.get(c);
        }
    }
    if db.stubbed.is_empty() {
        return None;
    }
    let distinct: BTreeSet<&str> = db.stubbed.iter().map(String::as_str).collect();
    let shown: Vec<&str> = distinct.iter().take(STUB_PREVIEW).copied().collect();
    let more = if distinct.len() > STUB_PREVIEW {
        " ..."
    } else {
        ""
    };
    Some(format!(
        "warning: {} unknown card(s) stubbed as inert 3-mana sorceries: {}{more}",
        db.stubbed.len(),
        shown.join(", ")
    ))
}

/// A field of a record dict.
fn field<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    match record {
        Value::Dict(fields) => fields.iter().find(|(k, _)| k == key).map(|(_, v)| v),
        _ => None,
    }
}

/// `--verbose`: the one-line summary of a finished game.
#[must_use]
pub fn game_line(seed: i64, g: u64, rec: &Value) -> String {
    let Some(Value::Dict(players)) = field(rec, "players") else {
        return String::new();
    };
    let lifes: Vec<String> = players
        .iter()
        .map(|(n, d)| {
            let short: String = n.split('#').next().unwrap_or(n).chars().take(12).collect();
            let life = field(d, "life").map_or_else(String::new, mtgcards::jsonval::py_str);
            format!("{short}:{life}")
        })
        .collect();
    let turns = field(rec, "turns").map_or_else(String::new, mtgcards::jsonval::py_str);
    let winner: String = field(rec, "winner")
        .map_or_else(String::new, mtgcards::jsonval::py_str)
        .chars()
        .take(24)
        .collect();
    let reason = field(rec, "reason").map_or_else(String::new, mtgcards::jsonval::py_str);
    format!(
        "  s{seed} g{:>3} T{turns:>2} winner={winner:<24} ({reason}) {}",
        g + 1,
        lifes.join(" ")
    )
}

/// The knobs of one simulation run.
pub struct RunSettings<'a> {
    /// the seeds, in order
    pub seeds: &'a [i64],
    /// games per seed
    pub games: u64,
    /// the turn cap
    pub turn_cap: u32,
    /// per-seat profiles
    pub profiles: &'a [Option<PolicyProfile>],
    /// `--verbose`: a line per game through `out`
    pub verbose: bool,
}

/// Run all requested games; the pooled statistics (`None` when no game
/// was played). `recorder_for(game_no, seed)` supplies the optional viz
/// recorder of each game.
pub fn run_matches(
    decks: &[Deck],
    db: &mut CardDatabase,
    settings: &RunSettings<'_>,
    recorder_for: &mut dyn FnMut(u64, i64) -> Option<SharedRecorder>,
    out: &mut dyn FnMut(&str),
) -> Option<Aggregator> {
    let mut agg: Option<Aggregator> = None;
    let mut game_no = 0;
    for &seed in settings.seeds {
        let mut rng = PyRandom::new(i128::from(seed));
        for g in 0..settings.games {
            game_no += 1;
            let recorder = recorder_for(game_no, seed);
            let options = MatchOptions {
                turn_cap: settings.turn_cap,
                profiles: settings.profiles.to_vec(),
                starting_player: None,
            };
            let mut rec = run_game(decks, db, &mut rng, &options, None, recorder.as_ref());
            if let Value::Dict(fields) = &mut rec {
                fields.push(("seed".to_owned(), Value::from(seed)));
            }
            let agg = agg.get_or_insert_with(|| {
                let names = match field(&rec, "players") {
                    Some(Value::Dict(players)) => players.iter().map(|(n, _)| n.clone()).collect(),
                    _ => Vec::new(),
                };
                Aggregator::new(names)
            });
            if settings.verbose {
                out(&game_line(seed, g, &rec));
            }
            agg.add(rec);
        }
    }
    agg
}

/// The lines of the final report: the pooled statistics, the card data
/// sources and the model-coverage report.
pub fn final_report_lines(
    decks: &[Deck],
    db: &mut CardDatabase,
    seeds: &[i64],
    games: u64,
    agg: &Aggregator,
) -> Vec<String> {
    let mut lines: Vec<String> = agg
        .report(seeds, games)
        .lines()
        .map(str::to_owned)
        .collect();
    let mut counts: BTreeSet<(String, i64)> = BTreeSet::new();
    let mut by_source: Vec<(String, i64)> = Vec::new();
    for d in decks {
        let names: BTreeSet<&str> = d
            .cards
            .iter()
            .map(String::as_str)
            .chain(d.commander.as_deref())
            .collect();
        for c in names {
            let source = db.get(c).source.clone();
            match by_source.iter_mut().find(|(s, _)| *s == source) {
                Some((_, n)) => *n += 1,
                None => by_source.push((source, 1)),
            }
        }
    }
    counts.extend(by_source);
    let shown: Vec<String> = counts.iter().map(|(k, v)| format!("{k}={v}")).collect();
    lines.push(format!(" card data sources: {}", shown.join(", ")));
    lines.extend(model_coverage_lines(decks));
    lines
}
