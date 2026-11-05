//! Self-play tuning of the AI policy profiles (the Python `selfplay.py`).
//!
//! Two modes (both play each pairing in both seatings to cancel the
//! play/draw advantage):
//!
//! - round-robin (default): every preset profile against every other on
//!   the given matchup; prints the win matrix.
//! - grid tuning (`--tune`): candidate profiles are variations of
//!   `--base` with the given knob values (cartesian product); each
//!   candidate plays against the plain base profile and candidates are
//!   ranked by pooled win rate.
//!
//! Seat 0 always takes the first turn: a pairing is played in both
//! seatings precisely so the play/draw advantage cancels, which a random
//! starting player (rule 103.1, what `mtg-simulate` does) would blur.

use std::fmt::Write as _;

use mtgcards::stats::wilson_ci;
use mtgcards::{CardDatabase, Deck};
use pycompat::json::Value;
use pycompat::pyfloat;
use pycompat::pyrepr::repr_str;
use pycompat::random::PyRandom;

use crate::adapter::{MatchOptions, run_game};
use crate::policy::{PROFILE_NAMES, PolicyProfile, get_profile};

/// How much self-play to run per pairing.
#[derive(Clone, Debug)]
pub struct SelfplaySettings {
    /// games per seed per seating
    pub games: u64,
    /// the seeds
    pub seeds: Vec<i64>,
    /// the turn cap
    pub turn_cap: u32,
}

/// Clone `deck` under a distinct seat name.
#[must_use]
pub fn clone_deck(deck: &Deck, suffix: &str) -> Deck {
    Deck {
        name: format!("{}{suffix}", deck.name),
        path: deck.path.clone(),
        cards: deck.cards.clone(),
        commander: deck.commander.clone(),
        fmt: deck.fmt.clone(),
        skipped: Vec::new(),
    }
}

/// The winner's name of a game record.
fn winner_of(record: &Value) -> Option<&str> {
    match record {
        Value::Dict(fields) => {
            fields
                .iter()
                .find(|(k, _)| k == "winner")
                .and_then(|(_, v)| match v {
                    Value::Str(s) => Some(s.as_str()),
                    _ => None,
                })
        }
        _ => None,
    }
}

/// Win rate of `prof_a` over `settings.games` per seed, both seatings:
/// `(wins, total)`.
pub fn pair_winrate(
    decks: &[Deck],
    db: &mut CardDatabase,
    prof_a: &PolicyProfile,
    prof_b: &PolicyProfile,
    settings: &SelfplaySettings,
) -> (u64, u64) {
    let (mut wins, mut total) = (0, 0);
    for &seed in &settings.seeds {
        for seating in 0..2_usize {
            let mut rng = PyRandom::new(i128::from(seed) * 2 + seating as i128);
            let profiles = if seating == 0 {
                vec![Some(prof_a.clone()), Some(prof_b.clone())]
            } else {
                vec![Some(prof_b.clone()), Some(prof_a.clone())]
            };
            let a_name = decks[seating].name.clone();
            let options = MatchOptions {
                turn_cap: settings.turn_cap,
                profiles,
                starting_player: Some(0), // the seatings cancel play/draw
            };
            for _ in 0..settings.games {
                let rec = run_game(decks, db, &mut rng, &options, None, None);
                total += 1;
                if winner_of(&rec) == Some(a_name.as_str()) {
                    wins += 1;
                }
            }
        }
    }
    (wins, total)
}

/// A knob value of a `--tune` axis.
#[derive(Clone, Debug, PartialEq)]
pub enum Knob {
    /// a float knob (`aggression`, `removal_key_threshold`)
    Float(f64),
    /// an int knob
    Int(i64),
    /// a bool knob (`hold_reactive_mana`)
    Bool(bool),
}

impl Knob {
    /// The Python `str()` of the value, as the candidate label shows it.
    fn label(&self) -> String {
        match self {
            Knob::Float(f) => pyfloat::repr(*f),
            Knob::Int(n) => n.to_string(),
            Knob::Bool(b) => if *b { "True" } else { "False" }.to_owned(),
        }
    }
}

const INT_KNOBS: &[&str] = &[
    "wipe_board_deficit",
    "counter_value_threshold",
    "mulligan_min_lands",
    "mulligan_max_lands",
    "max_mulligans",
    "race_life",
];
const BOOL_KNOBS: &[&str] = &["hold_reactive_mana"];
const FLOAT_KNOBS: &[&str] = &["aggression", "removal_key_threshold"];

/// Whether `knob` names a `PolicyProfile` field.
fn is_knob(knob: &str) -> bool {
    knob == "name"
        || INT_KNOBS.contains(&knob)
        || BOOL_KNOBS.contains(&knob)
        || FLOAT_KNOBS.contains(&knob)
}

/// One tuning candidate: `base` with the assignment's knobs applied.
#[must_use]
pub fn variant(base: &PolicyProfile, assignment: &[(String, Knob)]) -> PolicyProfile {
    let label: Vec<String> = assignment
        .iter()
        .map(|(k, v)| format!("{k}={}", v.label()))
        .collect();
    let mut cand = base.clone();
    cand.name = format!("{}[{}]", base.name, label.join(","));
    for (knob, value) in assignment {
        match (knob.as_str(), value) {
            ("aggression", Knob::Float(f)) => cand.aggression = *f,
            ("removal_key_threshold", Knob::Float(f)) => cand.removal_key_threshold = *f,
            ("hold_reactive_mana", Knob::Bool(b)) => cand.hold_reactive_mana = *b,
            ("wipe_board_deficit", Knob::Int(n)) => cand.wipe_board_deficit = *n,
            ("counter_value_threshold", Knob::Int(n)) => cand.counter_value_threshold = *n,
            ("mulligan_min_lands", Knob::Int(n)) => {
                cand.mulligan_min_lands = usize::try_from(*n).unwrap_or(0);
            }
            ("mulligan_max_lands", Knob::Int(n)) => {
                cand.mulligan_max_lands = usize::try_from(*n).unwrap_or(0);
            }
            ("max_mulligans", Knob::Int(n)) => {
                cand.max_mulligans = usize::try_from(*n).unwrap_or(0);
            }
            ("race_life", Knob::Int(n)) => cand.race_life = *n,
            _ => {}
        }
    }
    cand
}

/// `--tune KNOB=v1,v2 ...` -> the list of `{knob: value}` assignments
/// (the cartesian product of the axes).
///
/// # Errors
///
/// The Python `sys.exit` message when a spec is not `KNOB=v1,v2` with a
/// `PolicyProfile` field, or a value does not parse.
pub fn parse_tune(specs: &[String]) -> Result<Vec<Vec<(String, Knob)>>, String> {
    let mut axes: Vec<Vec<(String, Knob)>> = Vec::new();
    for spec in specs {
        let (knob, values) = spec.split_once('=').unwrap_or((spec.as_str(), ""));
        if values.is_empty() || !is_knob(knob) {
            return Err(format!(
                "--tune expects KNOB=v1,v2 with a PolicyProfile field, got {}",
                repr_str(spec)
            ));
        }
        let mut values_parsed = Vec::new();
        for v in values.split(',') {
            let parsed =
                if BOOL_KNOBS.contains(&knob) {
                    Knob::Bool(["1", "true", "yes"].contains(&v.to_lowercase().as_str()))
                } else if INT_KNOBS.contains(&knob) {
                    Knob::Int(i64::try_from(pycompat::pystr::parse_int(v)?).map_err(|_| {
                        format!("Python int too large to convert to C long: {}", repr_str(v))
                    })?)
                } else {
                    Knob::Float(v.trim().parse::<f64>().map_err(|_| {
                        format!("could not convert string to float: {}", repr_str(v))
                    })?)
                };
            values_parsed.push((knob.to_owned(), parsed));
        }
        axes.push(values_parsed);
    }
    // itertools.product: the last axis varies fastest
    let mut combos: Vec<Vec<(String, Knob)>> = vec![Vec::new()];
    for axis in axes {
        let mut next = Vec::new();
        for combo in &combos {
            for item in &axis {
                let mut c = combo.clone();
                c.push(item.clone());
                next.push(c);
            }
        }
        combos = next;
    }
    Ok(combos)
}

/// The lines of the preset round-robin report: every preset against
/// every other, the win matrix.
///
/// # Panics
///
/// Never: the preset names are the registered ones.
pub fn round_robin_lines(
    decks: &[Deck],
    db: &mut CardDatabase,
    settings: &SelfplaySettings,
) -> Vec<String> {
    let mut names: Vec<&str> = PROFILE_NAMES.to_vec();
    names.sort_unstable();
    let mut lines = vec![format!(
        "=== preset round-robin: {} vs {}, {} games x {} seed(s) x 2 seatings ===",
        decks[0].name,
        decks[1].name,
        settings.games,
        settings.seeds.len()
    )];
    let mut header = format!("{:<14}", "row wins vs");
    for n in &names {
        let _ = write!(header, "{n:>14}");
    }
    lines.push(header);
    for a in &names {
        let mut row = format!("{a:<14}");
        for b in &names {
            if a == b {
                let _ = write!(row, "{:>14}", "-");
                continue;
            }
            let prof_a = get_profile(a).expect("a preset");
            let prof_b = get_profile(b).expect("a preset");
            let (wins, total) = pair_winrate(decks, db, &prof_a, &prof_b, settings);
            let _ = write!(row, "{:>13.1}%", percent(wins, total));
        }
        lines.push(row);
    }
    lines
}

#[expect(clippy::cast_precision_loss, reason = "a percentage of game counts")]
fn percent(wins: u64, total: u64) -> f64 {
    100.0 * wins as f64 / total as f64
}

/// The lines of the grid-tuning report: `--tune` candidates against the
/// base profile, ranked by win rate.
///
/// # Errors
///
/// An unknown base preset or a malformed `--tune` spec.
pub fn grid_tune_lines(
    decks: &[Deck],
    db: &mut CardDatabase,
    settings: &SelfplaySettings,
    base_name: &str,
    tune_specs: &[String],
) -> Result<Vec<String>, String> {
    let base = get_profile(base_name)?;
    let assignments = parse_tune(tune_specs)?;
    let mut lines = vec![format!(
        "=== grid tuning vs '{base_name}': {} candidates, {} games x {} seed(s) x 2 seatings each ===",
        assignments.len(),
        settings.games,
        settings.seeds.len()
    )];
    let mut results: Vec<(f64, u64, u64, String)> = Vec::new();
    for assignment in &assignments {
        let cand = variant(&base, assignment);
        let (wins, total) = pair_winrate(decks, db, &cand, &base, settings);
        results.push((percent(wins, total) / 100.0, wins, total, cand.name));
    }
    // Python sorts the (rate, wins, total, name) tuples in reverse
    results.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(b.1.cmp(&a.1))
            .then(b.2.cmp(&a.2))
            .then(b.3.cmp(&a.3))
    });
    for (rate, wins, total, name) in &results {
        let (lo, hi) = wilson_ci(*wins, *total, 1.96);
        lines.push(format!(
            "  {:5.1} % ({wins:3}/{total}) CI {:.0}-{:.0} %  {name}",
            100.0 * rate,
            100.0 * lo,
            100.0 * hi
        ));
    }
    let best = &results[0];
    lines.push(format!("best: {} at {:.1} %", best.3, 100.0 * best.0));
    Ok(lines)
}
