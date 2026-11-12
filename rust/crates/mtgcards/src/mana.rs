//! The mana-cost grammar: `{2}{G}{G}`, hybrid `{B/R}`, `{X}`.
//!
//! The data layer uses it for the mana value of custom cards
//! (`parse_cost(cost).mv()`); the rules engine has its own, richer mana
//! system (`mtgrules::manasys`). Only the parser of the Python
//! `mtgcards.mana` module is ported: its greedy `pay`/`can_pay` helpers
//! have no caller.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use indexmap::IndexMap;
use regex::Regex;

/// The five colours, in WUBRG order.
pub const COLORS: &str = "WUBRG";

static SYMBOL_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\{([^}]+)\}").expect("valid regex"));

/// A parsed mana cost: generic part, coloured pips, hybrid pips, X.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Cost {
    /// generic mana (`{2}`, plus one per `{C}` / `{S}` / colourless hybrid)
    pub generic: i64,
    /// colour -> pip count, in first-seen order
    pub pips: IndexMap<char, i64>,
    /// hybrid pips: the colours each one may be paid with
    pub hybrid: Vec<BTreeSet<char>>,
    /// whether the cost has `{X}`
    pub has_x: bool,
}

impl Cost {
    /// Mana value of the cost (X counts as zero).
    #[must_use]
    pub fn mv(&self) -> i64 {
        self.generic
            + self.pips.values().sum::<i64>()
            + i64::try_from(self.hybrid.len()).unwrap_or(i64::MAX)
    }

    /// The colours of the cost's coloured, hybrid and Phyrexian symbols:
    /// the card's colours when nothing else defines them (rule 202.2).
    #[must_use]
    pub fn colors(&self) -> BTreeSet<String> {
        self.pips
            .keys()
            .chain(self.hybrid.iter().flatten())
            .map(char::to_string)
            .collect()
    }
}

/// Parse a mana cost string like `{2}{G}{G}` into a [`Cost`].
///
/// Symbols: digits add to generic, `X` sets `has_x`, `A/B` is a hybrid pip
/// of its colour letters (generic +1 when it names no colour, e.g.
/// `{2/W}` with no colour would not occur but `{C/..}` style symbols do),
/// a colour letter is a pip, `C` and `S` are generic; anything else is
/// ignored.
#[must_use]
pub fn parse_cost(cost: &str) -> Cost {
    let mut c = Cost::default();
    for caps in SYMBOL_RE.captures_iter(cost) {
        let sym = &caps[1];
        if !sym.is_empty() && sym.chars().all(|ch| ch.is_ascii_digit()) {
            c.generic += sym.parse::<i64>().unwrap_or(0);
        } else if sym == "X" {
            c.has_x = true;
        } else if sym.contains('/') {
            let opts: BTreeSet<char> = sym
                .split('/')
                .filter_map(|s| {
                    let mut chars = s.chars();
                    match (chars.next(), chars.next()) {
                        (Some(ch), None) if COLORS.contains(ch) => Some(ch),
                        _ => None,
                    }
                })
                .collect();
            if opts.is_empty() {
                c.generic += 1;
            } else {
                c.hybrid.push(opts);
            }
        } else if let Some(ch) = single_color(sym) {
            *c.pips.entry(ch).or_insert(0) += 1;
        } else if sym == "C" || sym == "S" {
            c.generic += 1;
        }
    }
    c
}

/// The colour letter of a one-letter colour symbol.
fn single_color(sym: &str) -> Option<char> {
    let mut chars = sym.chars();
    match (chars.next(), chars.next()) {
        (Some(ch), None) if COLORS.contains(ch) => Some(ch),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::parse_cost;

    #[test]
    fn mana_values() {
        assert_eq!(parse_cost("{2}{G}{G}").mv(), 4);
        assert_eq!(parse_cost("{X}{R}").mv(), 1);
        assert!(parse_cost("{X}{R}").has_x);
        assert_eq!(parse_cost("{B/R}{W}").mv(), 2);
        assert_eq!(parse_cost("{2/W}").mv(), 1);
        assert_eq!(parse_cost("{C}{S}{Q}").mv(), 2);
        assert_eq!(parse_cost("").mv(), 0);
        let cost = parse_cost("{G}{U}{G}");
        assert_eq!(
            cost.pips.iter().collect::<Vec<_>>(),
            [(&'G', &2), (&'U', &1)]
        );
    }

    #[test]
    fn colours_of_the_symbols() {
        let colors = |cost: &str| parse_cost(cost).colors().into_iter().collect::<Vec<_>>();
        assert_eq!(colors("{2}{G}{G}"), ["G"]);
        // hybrid, Phyrexian and monocoloured hybrid symbols are coloured
        assert_eq!(colors("{W/U}{B/P}{2/R}"), ["B", "R", "U", "W"]);
        // generic, colourless, snow and X are not
        assert_eq!(colors("{X}{3}{C}{S}"), Vec::<String>::new());
        assert_eq!(colors(""), Vec::<String>::new());
    }
}
