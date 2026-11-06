//! Mana, mana pools, and cost payment (CR 106, 118, 202, 605).
//!
//! Mana exists in mana pools (rule 106.4) and empties at the end of each
//! step and phase (rule 500.4). Mana abilities (rule 605) resolve
//! immediately without using the stack (rule 605.3b).
//!
//! Every choice of a colour out of a set goes through [`mana_order`], so
//! results never depend on hash order (the Python fix F0); the pool's
//! payment solver mirrors the Python one step for step, including its
//! tie-breaks.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use indexmap::IndexMap;
use regex::Regex;

use crate::rule;

/// The five colours.
pub const COLORS: &str = "WUBRG";
/// Rule 106.1b: five colours + colorless.
pub const MANA_TYPES: &str = "WUBRGC";

static SYMBOL_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\{([^}]+)\}").expect("valid regex"));

/// The sort key of a mana type: its WUBRGC position, others last and
/// alphabetical.
fn mana_key(mana_type: &str) -> (usize, String) {
    let idx = if mana_type.chars().count() == 1 {
        MANA_TYPES.find(mana_type).unwrap_or(MANA_TYPES.len())
    } else {
        MANA_TYPES.len()
    };
    (idx, mana_type.to_owned())
}

/// `types` in the canonical WUBRG(C) order (rule 105.1). Types outside
/// WUBRGC sort last, alphabetically.
#[rule("105.1")]
#[must_use]
pub fn mana_order<I, S>(types: I) -> Vec<String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut out: Vec<String> = types.into_iter().map(|s| s.as_ref().to_owned()).collect();
    out.sort_by_cached_key(|t| mana_key(t));
    out
}

/// Split a land's `land_colors` into `(ordered types, any_color)`.
///
/// A land producing all five colours is an "any color" source; the
/// ordered list is then empty.
#[must_use]
pub fn land_mana_types<I, S>(colors: I) -> (Vec<String>, bool)
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let cs: BTreeSet<String> = colors.into_iter().map(|s| s.as_ref().to_owned()).collect();
    if COLORS.chars().all(|c| cs.contains(&c.to_string())) {
        return (Vec::new(), true);
    }
    // Python's `c in MANA_TYPES` is a substring test; kept as is
    let known = cs.iter().filter(|c| MANA_TYPES.contains(c.as_str()));
    (mana_order(known), false)
}

/// A parsed mana cost: generic, coloured pips, hybrid options, X (rules
/// 202.1, 107.4).
#[rule("202.1", "107.4")]
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Cost {
    /// generic mana
    pub generic: i64,
    /// colour -> count, in order of first appearance
    pub pips: IndexMap<String, i64>,
    /// hybrid pips: the colours each may be paid with
    pub hybrid: Vec<BTreeSet<String>>,
    /// `{C}` pips, rule 107.4c
    pub colorless: i64,
    /// rule 107.3
    pub x_count: i64,
}

impl Cost {
    /// Rule 202.3: mana value (X counts as 0 elsewhere than the stack).
    #[must_use]
    pub fn mv(&self) -> i64 {
        self.generic
            + self.pips.values().sum::<i64>()
            + i64::try_from(self.hybrid.len()).unwrap_or(i64::MAX)
            + self.colorless
    }

    /// This cost plus `n` extra generic mana (commander tax).
    #[must_use]
    pub fn with_extra_generic(&self, n: i64) -> Cost {
        Cost {
            generic: self.generic + n,
            ..self.clone()
        }
    }

    /// Cost reduction (rule 601.2f): only the generic part shrinks.
    #[must_use]
    pub fn reduced(&self, n: i64) -> Cost {
        Cost {
            generic: (self.generic - n).max(0),
            ..self.clone()
        }
    }

    /// Rule 601.2b/107.3: the chosen X becomes generic mana. The result
    /// carries no X of its own; it is the total cost.
    #[must_use]
    pub fn with_x(&self, x: i64) -> Cost {
        Cost {
            generic: self.generic + x * self.x_count,
            x_count: 0,
            ..self.clone()
        }
    }
}

/// Parse a `{2}{G}{G}` style mana-cost string (rule 202.1).
#[rule("202.1")]
#[must_use]
pub fn parse_cost(cost_str: &str) -> Cost {
    let mut c = Cost::default();
    for caps in SYMBOL_RE.captures_iter(cost_str) {
        let sym = &caps[1];
        if !sym.is_empty() && sym.chars().all(|ch| ch.is_ascii_digit()) {
            c.generic += sym.parse::<i64>().unwrap_or(0);
        } else if sym == "X" {
            c.x_count += 1;
        } else if sym == "C" {
            c.colorless += 1;
        } else if sym == "S" {
            // snow, rule 107.4g: any mana
            c.generic += 1;
        } else if sym.contains('/') {
            let opts: BTreeSet<String> = sym
                .split('/')
                .filter(|s| COLORS.contains(s)) // Python's substring `in`
                .map(str::to_owned)
                .collect();
            if opts.is_empty() {
                // {2/W} style, pay the generic side
                c.generic += 1;
            } else {
                c.hybrid.push(opts);
            }
        } else if COLORS.contains(sym) {
            *c.pips.entry(sym.to_owned()).or_insert(0) += 1;
        }
    }
    c
}

/// A player's mana pool: counts per mana type (rules 106.4, 500.4).
#[rule("106.4", "500.4")]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ManaPool {
    /// mana type -> amount; the six WUBRGC entries first, in that order
    pub mana: IndexMap<String, i64>,
}

impl Default for ManaPool {
    fn default() -> Self {
        Self::new()
    }
}

impl ManaPool {
    /// An empty pool (rule 106.4).
    #[must_use]
    pub fn new() -> Self {
        ManaPool {
            mana: MANA_TYPES.chars().map(|c| (c.to_string(), 0)).collect(),
        }
    }

    /// Add `n` mana of the given type to the pool.
    pub fn add(&mut self, mana_type: &str, n: i64) {
        *self.mana.entry(mana_type.to_owned()).or_insert(0) += n;
    }

    /// The amount of one type.
    #[must_use]
    pub fn get(&self, mana_type: &str) -> i64 {
        self.mana.get(mana_type).copied().unwrap_or(0)
    }

    /// Total floating mana across all types.
    #[must_use]
    pub fn total(&self) -> i64 {
        self.mana.values().sum()
    }

    /// Rule 500.4: pools empty at the end of each step and phase.
    pub fn empty(&mut self) {
        for amount in self.mana.values_mut() {
            *amount = 0;
        }
    }

    /// Whether the floating mana alone covers `cost` (rules 601.2g-h).
    #[rule("601.2g", "601.2h")]
    #[must_use]
    pub fn can_pay(&self, cost: &Cost) -> bool {
        self.solve(cost).is_some()
    }

    /// Pay `cost` from the pool; whether payment succeeded.
    pub fn pay(&mut self, cost: &Cost) -> bool {
        match self.solve(cost) {
            Some(pool) => {
                self.mana = pool;
                true
            }
            None => false,
        }
    }

    /// Find an exact payment for `cost` from the pool: coloured pips
    /// first, then hybrid (scarcity-first), then `{C}`, then generic from
    /// colorless first and the most plentiful types next. Returns the
    /// pool after payment.
    fn solve(&self, cost: &Cost) -> Option<IndexMap<String, i64>> {
        let mut pool = self.mana.clone();
        for (color, n) in &cost.pips {
            if pool.get(color).copied().unwrap_or(0) < *n {
                return None;
            }
            *pool.entry(color.clone()).or_insert(0) -= n;
        }
        // sorted(cost.hybrid, key=len): a stable sort by option count
        let mut hybrid: Vec<&BTreeSet<String>> = cost.hybrid.iter().collect();
        hybrid.sort_by_key(|opts| opts.len());
        for opts in hybrid {
            // the most plentiful option; ties go to the canonical first
            // colour (F0: never hash order) - Python's max() keeps the
            // first maximum of mana_order(opts)
            let ordered = mana_order(opts.iter());
            let mut pick = ordered.first()?.clone();
            for candidate in &ordered[1..] {
                if pool.get(candidate).copied().unwrap_or(0) > pool.get(&pick).copied().unwrap_or(0)
                {
                    pick.clone_from(candidate);
                }
            }
            if pool.get(&pick).copied().unwrap_or(0) < 1 {
                return None;
            }
            *pool.entry(pick).or_insert(0) -= 1;
        }
        if pool.get("C").copied().unwrap_or(0) < cost.colorless {
            return None;
        }
        *pool.entry("C".to_owned()).or_insert(0) -= cost.colorless;
        let mut need = cost.generic;
        // rule 106.1b: generic costs can be paid with any type; spend
        // colorless first, then the most plentiful types (a stable sort
        // over the pool's insertion order, like sorted() over the dict)
        let mut order: Vec<String> = pool.keys().cloned().collect();
        order.sort_by_key(|t| (t != "C", -pool[t]));
        for t in order {
            let take = need.min(pool[&t]);
            *pool.get_mut(&t).expect("key from the pool") -= take;
            need -= take;
        }
        if need > 0 {
            return None;
        }
        Some(pool)
    }
}

#[cfg(test)]
mod tests {
    use super::{Cost, ManaPool, land_mana_types, mana_order, parse_cost};

    #[test]
    fn canonical_order_and_land_types() {
        assert_eq!(
            mana_order(["G", "C", "W", "zz", "B"]),
            ["W", "B", "G", "C", "zz"]
        );
        assert_eq!(
            land_mana_types(["R", "B"]),
            (vec!["B".to_owned(), "R".to_owned()], false)
        );
        assert_eq!(land_mana_types(["W", "U", "B", "R", "G"]), (vec![], true));
        assert_eq!(land_mana_types(["C"]), (vec!["C".to_owned()], false));
        assert_eq!(land_mana_types(Vec::<&str>::new()), (vec![], false));
    }

    #[test]
    fn costs() {
        let c = parse_cost("{X}{X}{2}{G}{G}{B/R}{C}{S}{2/W}");
        // {2} + {S}; {2/W} keeps its W option and is a hybrid pip
        assert_eq!((c.generic, c.colorless, c.x_count), (3, 1, 2));
        assert_eq!(c.pips.get("G"), Some(&2));
        assert_eq!(c.hybrid.len(), 2);
        assert_eq!(c.mv(), 3 + 2 + 2 + 1);
        assert_eq!(c.with_x(3).generic, 9);
        assert_eq!(c.with_x(3).x_count, 0);
        assert_eq!(c.reduced(10).generic, 0);
        assert_eq!(c.with_extra_generic(2).generic, 5);
        assert_eq!(parse_cost(""), Cost::default());
    }

    #[test]
    fn pool_payment_order_and_tie_breaks() {
        let mut pool = ManaPool::new();
        pool.add("U", 1);
        pool.add("R", 1);
        pool.add("C", 1);
        // hybrid {U/R}: both have 1, the canonical first colour (U) pays
        assert!(pool.pay(&parse_cost("{U/R}")));
        assert_eq!((pool.get("U"), pool.get("R"), pool.get("C")), (0, 1, 1));
        // generic: colorless is spent before colours
        assert!(pool.pay(&parse_cost("{1}")));
        assert_eq!((pool.get("R"), pool.get("C")), (1, 0));
        assert!(!pool.can_pay(&parse_cost("{G}")));
        assert!(pool.can_pay(&parse_cost("{1}")));
        assert!(!pool.pay(&parse_cost("{C}")));
        assert_eq!(pool.total(), 1);
        pool.empty();
        assert_eq!(pool.total(), 0);
        assert_eq!(
            pool.mana.keys().map(String::as_str).collect::<String>(),
            "WUBRGC"
        );
    }

    #[test]
    fn generic_prefers_the_most_plentiful_colour() {
        let mut pool = ManaPool::new();
        pool.add("W", 1);
        pool.add("G", 3);
        assert!(pool.pay(&parse_cost("{2}")));
        assert_eq!((pool.get("W"), pool.get("G")), (1, 1));
    }
}
