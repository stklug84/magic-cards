//! Token clauses and `[count] target <what>` phrases.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use regex::{Captures, Regex};

use super::{KEYWORD_WORDS, Num, num, re};
use crate::abilities::{ANY_NUMBER, TargetSpec, TokenSpec};
use crate::rule;

pub(crate) static TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)create (a|an|one|two|three|four|five|x|\d+)(?P<tapped> tapped)?[^.]*?(?P<p>\d+)/(?P<t>\d+) (?P<colors>[a-z ]*?)(?P<art>artifact )?creature tokens?(?P<kw> with [a-z ]+)?",
    )
});
static SUBTYPE_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(\d+/\d+ [a-z ]*?)([A-Z][a-z]+(?: [A-Z][a-z]+)?)(?: artifact)? creature token")
});

const COLOR_WORDS: &[(&str, &str)] = &[
    ("white", "W"),
    ("blue", "U"),
    ("black", "B"),
    ("red", "R"),
    ("green", "G"),
];

/// Extract `(count, TokenSpec)` from a [`TOKEN_RE`] match over `clause`.
#[must_use]
pub fn parse_token_clause(clause: &str, m: &Captures<'_>) -> (Num, TokenSpec) {
    let count = num(&m[1]);
    let color_text = m.name("colors").map_or("", |g| g.as_str());
    let colors: BTreeSet<String> = COLOR_WORDS
        .iter()
        .filter(|(w, _)| color_text.contains(w))
        .map(|(_, c)| (*c).to_owned())
        .collect();
    let kw_text = m.name("kw").map_or("", |g| g.as_str());
    let keywords: BTreeSet<String> = KEYWORD_WORDS
        .iter()
        .filter(|k| kw_text.contains(*k))
        .map(|k| (*k).to_owned())
        .collect();
    // subtype: last capitalized word before "creature token" if present
    let start = m.get(0).map_or(0, |g| g.start());
    let subtypes: BTreeSet<String> = SUBTYPE_RE
        .captures(&clause[start..])
        .map(|sub| sub[2].split(' ').map(str::to_owned).collect())
        .unwrap_or_default();
    let mut types: BTreeSet<String> = BTreeSet::from(["Creature".to_owned()]);
    if m.name("art").is_some() {
        types.insert("Artifact".to_owned());
    }
    let name = if subtypes.is_empty() {
        "Token".to_owned()
    } else {
        subtypes.iter().cloned().collect::<Vec<_>>().join(" ")
    };
    let spec = TokenSpec {
        name,
        power: Some(super::parse_i64(&m["p"])),
        toughness: Some(super::parse_i64(&m["t"])),
        colors,
        types,
        subtypes,
        keywords,
        tapped: m.name("tapped").is_some(),
        ..TokenSpec::default()
    };
    (count, spec)
}

static TARGET_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)(?:(?P<upto>up to )?(?P<n>one|two|three|four|five|six|x|\d+) |(?P<anynum>any number of )|(?P<oneortwo>one or two )|(?P<onethree>one, two, or three )|(?P<any>any ))?(?P<other>other )?targets?(?: (?P<what>[a-z' ]+?))?(?: an opponent controls| you control| you don't control)?(?:$|[.,;])",
    )
});

/// `target <what>` phrase -> `TargetSpec.what` (first match wins).
const WHAT_MAPPING: &[(&str, &str)] = &[
    ("creature or planeswalker", "creature_or_planeswalker"),
    ("creature or player", "any"),
    ("artifact or enchantment", "art_ench"),
    ("creature or enchantment", "cre_ench"),
    ("nonland permanent", "nonland"),
    ("permanent", "permanent"),
    ("creature", "creature"),
    ("artifact", "artifact"),
    ("enchantment", "enchantment"),
    ("planeswalker", "creature_or_planeswalker"),
    ("player", "player"),
    ("opponent", "opponent"),
    ("spell", "spell"),
    ("land", "land"),
];

/// Parse one `[count] target <what>` phrase into a [`TargetSpec`].
///
/// Counts (fix F11): "up to N" (0..N), "N" (exactly N), "any number of"
/// (0..any), "one or two" (1..2), "one, two, or three" (1..3); "any
/// target" and a bare "targets" are creatures, planeswalkers or players.
#[rule("601.2c", "115.3")]
#[must_use]
pub fn target_spec(text: &str) -> Option<TargetSpec> {
    let m = TARGET_RE.captures(text)?;
    let what_raw = m
        .name("what")
        .map_or("", |g| g.as_str())
        .trim()
        .to_lowercase();
    let what = if m.name("any").is_some() || what_raw.is_empty() {
        "any"
    } else {
        WHAT_MAPPING
            .iter()
            .find(|(k, _)| what_raw.contains(k))
            .map(|(_, v)| *v)?
    };
    let controller = if text.contains("an opponent controls") {
        "opponent"
    } else if text.contains("you control") {
        "you"
    } else if text.contains("you don't control") {
        "opponent"
    } else {
        "any"
    };
    let (mut count, mut minimum) = (1, 1);
    if let Some(n) = m.name("n") {
        count = match num(n.as_str()) {
            Num::N(n) => n,
            Num::X => ANY_NUMBER,
        };
        minimum = if m.name("upto").is_some() { 0 } else { count };
    } else if m.name("anynum").is_some() {
        (count, minimum) = (ANY_NUMBER, 0);
    } else if m.name("oneortwo").is_some() {
        (count, minimum) = (2, 1);
    } else if m.name("onethree").is_some() {
        (count, minimum) = (3, 1);
    }
    Some(TargetSpec {
        what: what.to_owned(),
        controller: controller.to_owned(),
        other: m.name("other").is_some(),
        optional: minimum == 0,
        count,
        min_count: Some(minimum),
    })
}
