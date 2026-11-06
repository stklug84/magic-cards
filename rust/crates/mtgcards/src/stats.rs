//! Result aggregation and reporting.
//!
//! Wilson confidence intervals, per-card win-rate lift, per-seed win-rate
//! spread, per-game stat averages, and JSONL export of the raw records.
//!
//! Game records are the JSON dicts the match runner produces
//! (`{"winner", "seed", "turns", "reason", "players": {name: {stat: n,
//! "cards_cast": [...]}}}`), kept as [`Value`]s so `write_jsonl` and the
//! report render byte for byte what the Python `Aggregator` renders: every
//! number goes through [`pycompat::pyfmt`] and [`pycompat::stats`].

use std::io::Write as _;
use std::path::Path;

use indexmap::IndexMap;
use pycompat::json::{self, Value};
use pycompat::pyfmt::{format_float, format_int, format_str};
use pycompat::stats;

/// 95% Wilson score interval for a binomial proportion.
#[must_use]
pub fn wilson_ci(wins: u64, n: u64, z: f64) -> (f64, f64) {
    if n == 0 {
        return (0.0, 0.0);
    }
    #[expect(clippy::cast_precision_loss, reason = "game counts are small integers")]
    let (wins_f, n_f, two_n, four_n_n) =
        (wins as f64, n as f64, (2 * n) as f64, (4 * n * n) as f64);
    let p = wins_f / n_f;
    let denom = 1.0 + z * z / n_f;
    let centre = (p + z * z / two_n) / denom;
    let margin = z * (p * (1.0 - p) / n_f + z * z / four_n_n).sqrt() / denom;
    // Python's max(0.0, x) / min(1.0, y) return the first argument on ties
    let lo = centre - margin;
    let hi = centre + margin;
    (
        if lo > 0.0 { lo } else { 0.0 },
        if hi < 1.0 { hi } else { 1.0 },
    )
}

/// The default `z` of the report (95%).
pub const Z_95: f64 = 1.96;

/// The per-game statistics the report lists, in order; a row is shown
/// when any player has a non-zero average.
pub const STAT_KEYS: &[&str] = &[
    "life",
    "mulligans",
    "lands_played",
    "spells_cast",
    "cards_drawn",
    "cards_cycled",
    "abilities_activated",
    "tokens_created",
    "tokens_killed",
    "treasures_made",
    "counters_received",
    "proliferates",
    "attacks",
    "combat_damage",
    "poison_received",
    "drain",
    "drained_taken",
    "counterspells_used",
    "spells_countered_against",
    "spells_copied",
    "removal_used",
    "wipes_cast",
    "necroskitter_steals",
    "grave_robs",
    "mechanized_wins",
];

/// Collects per-game records and renders the matchup report.
#[derive(Clone, Debug, Default)]
pub struct Aggregator {
    /// the seat order used by every report column
    pub names: Vec<String>,
    /// the raw game records
    pub records: Vec<Value>,
}

/// A numeric JSON value as `f64` (ints exactly, like Python's exact
/// statistics over ints).
fn as_f64(value: &Value) -> Option<f64> {
    match value {
        #[expect(clippy::cast_precision_loss, reason = "statistics are small integers")]
        Value::Int(i) => Some(*i as f64),
        Value::Float(f) => Some(*f),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        _ => None,
    }
}

/// Python `s[:n]` by code points.
fn head_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

#[expect(
    clippy::cast_precision_loss,
    reason = "game and card counts are small integers"
)]
fn ratio(num: u64, den: u64) -> f64 {
    num as f64 / den as f64
}

/// The seed part of the report header: one seed, a consecutive range
/// `(a..b)` or the explicit comma-separated list.
fn seed_description(seeds: &[i64]) -> String {
    match seeds {
        [] => "0 seeds ()".to_owned(),
        [only] => only.to_string(),
        [first, .., last] => {
            let consecutive = seeds
                .iter()
                .enumerate()
                .all(|(i, s)| i64::try_from(i).is_ok_and(|i| *s == first + i));
            if consecutive {
                format!("{} seeds ({first}..{last})", seeds.len())
            } else {
                let all = seeds
                    .iter()
                    .map(ToString::to_string)
                    .collect::<Vec<_>>()
                    .join(",");
                format!("{} seeds ({all})", seeds.len())
            }
        }
    }
}

impl Aggregator {
    /// Remember the seat order used by every report column.
    #[must_use]
    pub fn new(player_names: Vec<String>) -> Self {
        Self {
            names: player_names,
            records: Vec::new(),
        }
    }

    /// Append one game record (the match runner's output).
    pub fn add(&mut self, record: Value) {
        self.records.push(record);
    }

    // ---- basic -----------------------------------------------------------

    /// Count wins of `name`, optionally restricted to one seed.
    #[must_use]
    pub fn wins(&self, name: &str, seed: Option<i64>) -> u64 {
        let wanted = seed.map(Value::from);
        self.records
            .iter()
            .filter(|r| r.get("winner") == Some(&Value::Str(name.to_owned())))
            .filter(|r| wanted.as_ref().is_none_or(|s| r.get("seed") == Some(s)))
            .count() as u64
    }

    /// Per-game average of one player statistic (0 when absent).
    #[must_use]
    pub fn stat_avg(&self, name: &str, key: &str) -> f64 {
        let vals: Vec<f64> = self
            .records
            .iter()
            .map(|r| {
                r.get("players")
                    .and_then(|p| p.get(name))
                    .and_then(|p| p.get(key))
                    .and_then(as_f64)
                    .unwrap_or(0.0)
            })
            .collect();
        if vals.is_empty() {
            0.0
        } else {
            stats::mean(&vals)
        }
    }

    // ---- per-card win correlation -----------------------------------------

    /// Win rate in games where a card was cast vs the deck's overall:
    /// `(card, cast_games, winrate, lift)`, by descending `|lift|`.
    #[must_use]
    pub fn card_lift(&self, name: &str, min_games: u64) -> Vec<(String, u64, f64, f64)> {
        let overall = ratio(self.wins(name, None), (self.records.len() as u64).max(1));
        let mut cast_games: IndexMap<String, u64> = IndexMap::new();
        let mut cast_wins: IndexMap<String, u64> = IndexMap::new();
        for r in &self.records {
            let won = r.get("winner") == Some(&Value::Str(name.to_owned()));
            let cast = r
                .get("players")
                .and_then(|p| p.get(name))
                .and_then(|p| p.get("cards_cast"));
            let Some(Value::List(cards)) = cast else {
                continue;
            };
            for card in cards {
                let Value::Str(card) = card else {
                    continue;
                };
                *cast_games.entry(card.clone()).or_insert(0) += 1;
                if won {
                    *cast_wins.entry(card.clone()).or_insert(0) += 1;
                }
            }
        }
        let mut rows: Vec<(String, u64, f64, f64)> = cast_games
            .into_iter()
            .filter(|(_, n)| *n >= min_games)
            .map(|(card, n)| {
                let wr = ratio(cast_wins.get(&card).copied().unwrap_or(0), n);
                (card, n, wr, wr - overall)
            })
            .collect();
        // stable, by -abs(lift)
        rows.sort_by(|a, b| {
            b.3.abs()
                .partial_cmp(&a.3.abs())
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        rows
    }

    // ---- per-seed summary ---------------------------------------------------

    /// Per-seed win rates + between-seed spread for each player.
    #[must_use]
    pub fn seed_lines(&self, seeds: &[i64], games_per_seed: u64, width: usize) -> Vec<String> {
        let mut lines = vec![
            "-".repeat(width),
            format!(" per-seed win rates ({games_per_seed} games each)"),
        ];
        let mut header = format!("   {}", format_str("seed", "<8s"));
        for nm in &self.names {
            header.push_str(&format_str(&head_chars(nm, 16), ">18s"));
        }
        lines.push(header);
        let mut rates: Vec<Vec<f64>> = vec![Vec::new(); self.names.len()];
        for &seed in seeds {
            let mut row = format!("   {}", format_int(i128::from(seed), "<8d"));
            for (i, nm) in self.names.iter().enumerate() {
                let r = ratio(self.wins(nm, Some(seed)), games_per_seed);
                rates[i].push(r);
                row.push_str(&format_float(100.0 * r, "17.1f"));
                row.push('%');
            }
            lines.push(row);
        }
        let mut row = format!("   {}", format_str("std", "<8s"));
        for player_rates in &rates {
            let sd = if player_rates.len() > 1 {
                stats::stdev(player_rates)
            } else {
                0.0
            };
            row.push_str(&format_float(100.0 * sd, "16.1f"));
            row.push_str("pp");
        }
        lines.push(row);
        lines
    }

    // ---- report ------------------------------------------------------------

    /// Render the full matchup report as one printable string.
    #[must_use]
    pub fn report(&self, seeds: &[i64], games_per_seed: u64) -> String {
        let n = self.records.len() as u64;
        let width = 72;
        let mut lines: Vec<String> = vec![
            "=".repeat(width),
            format!(
                " mtgrules - {n} games, seed {}, {} players",
                seed_description(seeds),
                self.names.len()
            ),
            "=".repeat(width),
        ];
        lines.extend(self.win_lines(n));
        if seeds.len() > 1 {
            lines.extend(self.seed_lines(seeds, games_per_seed, width));
        }
        lines.push("-".repeat(width));
        lines.extend(self.length_and_ending_lines());
        lines.push("-".repeat(width));
        lines.extend(self.stat_lines());
        lines.push("-".repeat(width));
        lines.extend(self.lift_lines());
        lines.push("=".repeat(width));
        lines.join("\n")
    }

    /// One line per player (wins, win rate, Wilson CI), plus the draws.
    fn win_lines(&self, n: u64) -> Vec<String> {
        let pct = |count: u64| if n == 0 { 0.0 } else { ratio(100 * count, n) };
        let mut lines: Vec<String> = self
            .names
            .iter()
            .map(|name| {
                let w = self.wins(name, None);
                let (lo, hi) = wilson_ci(w, n, Z_95);
                format!(
                    " {} {} wins ({} %, 95% CI {}-{} %)",
                    format_str(name, "<38s"),
                    format_int(i128::from(w), "4d"),
                    format_float(pct(w), "5.1f"),
                    format_float(100.0 * lo, ".0f"),
                    format_float(100.0 * hi, ".0f"),
                )
            })
            .collect();
        let draws = self
            .records
            .iter()
            .filter(|r| r.get("winner") == Some(&Value::from("draw")))
            .count() as u64;
        if draws > 0 {
            lines.push(format!(
                " {} {}       ({} %)",
                format_str("draws", "<38s"),
                format_int(i128::from(draws), "4d"),
                format_float(pct(draws), "5.1f"),
            ));
        }
        lines
    }

    /// The game-length line and the endings line.
    fn length_and_ending_lines(&self) -> Vec<String> {
        let turns: Vec<f64> = self
            .records
            .iter()
            .filter_map(|r| r.get("turns").and_then(as_f64))
            .collect();
        let (avg, median) = if turns.is_empty() {
            (0.0, 0.0)
        } else {
            (stats::mean(&turns), stats::median(&turns))
        };
        let mut reasons: IndexMap<String, u64> = IndexMap::new();
        for r in &self.records {
            let reason = r
                .get("reason")
                .map_or_else(|| "None".to_owned(), crate::jsonval::py_str);
            *reasons.entry(reason).or_insert(0) += 1;
        }
        let mut reason_rows: Vec<(String, u64)> = reasons.into_iter().collect();
        // stable: ties keep their first-seen order, like sorted()
        reason_rows.sort_by_key(|row| std::cmp::Reverse(row.1));
        vec![
            format!(
                " game length: avg {} turns, median {}",
                format_float(avg, ".1f"),
                format_float(median, ".0f")
            ),
            format!(
                " endings: {}",
                reason_rows
                    .iter()
                    .map(|(k, v)| format!("{k}={v}"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        ]
    }

    /// The per-game statistic table (only rows with a non-zero average).
    fn stat_lines(&self) -> Vec<String> {
        let mut header = " per-game avg          ".to_owned();
        for nm in &self.names {
            header.push_str(&format_str(&head_chars(nm, 16), ">18s"));
        }
        let mut lines = vec![header];
        for k in STAT_KEYS {
            if !self.names.iter().any(|nm| self.stat_avg(nm, k) != 0.0) {
                continue;
            }
            let mut row = format!("   {}", format_str(k, "<20s"));
            for nm in &self.names {
                row.push_str(&format_float(self.stat_avg(nm, k), "18.2f"));
            }
            lines.push(row);
        }
        lines
    }

    /// The card win-rate lift section (min 8 casts, top 6 by |lift|).
    fn lift_lines(&self) -> Vec<String> {
        let mut lines = vec![" card win-rate lift (min 8 casts, top 6 by |lift|)".to_owned()];
        for nm in &self.names {
            let rows = self.card_lift(nm, 8);
            lines.push(format!("  {nm}:"));
            if rows.is_empty() {
                lines.push("    (not enough samples)".to_owned());
            }
            for (card, cnt, wr, lift) in rows.iter().take(6) {
                lines.push(format!(
                    "    {} cast {}x  wr {} %  lift {} pp",
                    format_str(card, "<38s"),
                    format_int(i128::from(*cnt), "3d"),
                    format_float(100.0 * wr, "5.1f"),
                    format_float(100.0 * lift, "+5.1f"),
                ));
            }
        }
        lines
    }

    /// Write every raw game record to `path`, one JSON object per line
    /// (`json.dumps(record)`).
    ///
    /// # Errors
    ///
    /// The I/O error of creating or writing the file.
    pub fn write_jsonl(&self, path: &Path) -> std::io::Result<()> {
        let mut out = std::io::BufWriter::new(std::fs::File::create(path)?);
        for r in &self.records {
            out.write_all(json::dumps(r).as_bytes())?;
            out.write_all(b"\n")?;
        }
        out.flush()
    }
}

#[cfg(test)]
mod tests {
    use super::{Aggregator, wilson_ci};
    use pycompat::json::Value;

    fn record(winner: &str, seed: i64, turns: i64, life: i64, cast: &[&str]) -> Value {
        let player = |l: i64, c: &[&str]| {
            Value::dict([
                ("life", Value::from(l)),
                (
                    "cards_cast",
                    Value::List(c.iter().map(|s| Value::from(*s)).collect()),
                ),
            ])
        };
        Value::dict([
            ("winner", Value::from(winner)),
            ("seed", Value::from(seed)),
            ("turns", Value::from(turns)),
            ("reason", Value::from("life")),
            (
                "players",
                Value::dict([("A", player(life, cast)), ("B", player(0, &[]))]),
            ),
        ])
    }

    #[test]
    fn wilson_matches_python_formula() {
        assert_eq!(wilson_ci(0, 0, 1.96), (0.0, 0.0));
        let (lo, hi) = wilson_ci(7, 10, 1.96);
        // statistics from scipy-free formula: 0.3967.. / 0.8922..
        assert!((lo - 0.396_84).abs() < 1e-4, "{lo}");
        assert!((hi - 0.892_16).abs() < 1e-4, "{hi}");
        assert_eq!(wilson_ci(10, 10, 1.96).1, 1.0);
    }

    #[test]
    fn report_shape() {
        let mut agg = Aggregator::new(vec!["A".into(), "B".into()]);
        agg.add(record("A", 1, 12, 30, &["Sol Ring"]));
        agg.add(record("B", 1, 19, 0, &[]));
        agg.add(record("A", 2, 20, 10, &["Sol Ring"]));
        agg.add(record("draw", 2, 18, 5, &[]));
        let text = agg.report(&[1, 2], 2);
        assert!(text.starts_with(&"=".repeat(72)), "{text}");
        assert!(text.contains(" mtgrules - 4 games, seed 2 seeds (1..2), 2 players"));
        assert!(text.contains(
            " A                                         2 wins ( 50.0 %, 95% CI 15-85 %)"
        ));
        assert!(text.contains(" draws                                     1       ( 25.0 %)"));
        assert!(text.contains(" game length: avg 17.2 turns, median 18"));
        assert!(text.contains(" endings: life=4"));
        assert!(text.contains("   life                             11.25              0.00"));
        assert!(text.contains("    (not enough samples)"));
        assert_eq!(agg.wins("A", Some(1)), 1);
        assert_eq!(
            agg.card_lift("A", 1),
            vec![("Sol Ring".to_owned(), 2, 1.0, 0.5)]
        );
    }
}
