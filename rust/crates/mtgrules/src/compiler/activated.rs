//! `[Cost]: [Effect]` oracle lines and the body of `Add ...` mana
//! abilities.

use std::sync::LazyLock;

use regex::Regex;

use super::clauses::parse_effect_text;
use super::{parse_i64, re, short_name};
use crate::abilities::ActivatedAbility;
use crate::effects::{AddMana, Effect};
use crate::protocols::CountValue;
use crate::rule;

const MINUS: char = '\u{2212}';

static AMOUNT_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^(one|two|three|four|five) mana of any"));
static SYMBOL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\{([^}]+)\}"));

/// Parse the body of an `Add ...` mana ability into an [`AddMana`]
/// (rules 106.1b, 106.2).
///
/// - `{B}{R}`: all listed symbols (two mana);
/// - `{B} or {R}` / `{W}, {U}, or {B}`: ONE mana of one of them;
/// - `one mana of any color`: one mana of any colour;
/// - `N mana of any one color`: N mana of one chosen colour;
/// - `... in your commander's color identity`: one mana of a colour of
///   the identity (never flagged as "any color", fix F6).
#[rule("106.1b", "106.2")]
#[must_use]
pub fn parse_add_mana(text: &str) -> AddMana {
    let low = text.to_lowercase();
    let cid = low.contains("commander's color identity");
    let any_color = !cid && (low.contains("any color") || low.contains("any one color"));
    let amount = AMOUNT_RE.captures(&low).map_or(1, |m| match &m[1] {
        "one" => 1,
        "two" => 2,
        "three" => 3,
        "four" => 4,
        _ => 5,
    });
    let symbols: Vec<String> = SYMBOL_RE
        .captures_iter(text)
        .map(|m| m[1].to_owned())
        .filter(|s| "WUBRGC".contains(s.as_str()))
        .collect();
    let choice = low.contains(" or ") && symbols.len() > 1;
    AddMana {
        types: symbols,
        any_color,
        commander_identity: cid,
        choice,
        amount,
    }
}

/// The cost components the engine can charge (rule 602.1, fix F5): a
/// run of mana symbols and/or `{T}`, a sacrifice, or a life payment.
/// Anything else ("Discard a card", `{Q}`, "Remove a counter", trigger
/// text that happens to contain a colon) makes the line unmodelled
/// instead of a cheaper-than-printed or cost-free ability.
static COST_COMPONENT_RES: LazyLock<[Regex; 3]> = LazyLock::new(|| {
    [
        re(r"(?i)^(?:\{(?:T|\d+|X|C|S|[WUBRG](?:/[WUBRGP])?|\d/[WUBRG])\})+$"),
        re(r"^sacrifice (?:a|an|another)?\s*[a-z' ]+$"),
        re(r"^pay \d+ life$"),
    ]
});

/// Whether every comma-separated component of `cost_part` is chargeable.
fn is_cost(cost_part: &str) -> bool {
    let parts: Vec<String> = cost_part
        .split(',')
        .map(|p| p.trim().to_lowercase())
        .collect();
    parts.iter().all(|p| !p.is_empty())
        && parts
            .iter()
            .all(|p| COST_COMPONENT_RES.iter().any(|r| r.is_match(p)))
}

static CYCLING_RE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^cycling ((?:\{[^}]+\})+)\.?$"));
static LOYALTY_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^([+\u{2212}]?\d+|0): (.+)$"));
static COST_LINE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^((?:\{[^}]+\})*(?:, )?[^:]*): (.+)$"));
static MANA_SYMBOLS_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\{[^}]+\}"));
static SACRIFICE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)sacrifice (a|an|another)?\s*([a-z' ]+)"));
static PAY_LIFE_RE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)pay (\d+) life"));
static ADD_RE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^add (.+)$"));

/// Parse one `[Cost]: [Effect]` oracle line (rule 602.1).
///
/// Only lines whose whole cost the engine can charge become activated
/// abilities (see `COST_COMPONENT_RES`); the others are reported as
/// unmodelled by the caller.
#[rule("602.1")]
#[must_use]
pub fn parse_activated_line(line: &str, name: &str) -> Option<ActivatedAbility> {
    if let Some(m) = CYCLING_RE.captures(line) {
        // rule 702.29a
        return Some(ActivatedAbility {
            mana_cost: m[1].to_owned(),
            from_hand: true,
            effect: Some(Effect::draw(CountValue::Fixed(1))),
            text: line.to_owned(),
            ..ActivatedAbility::default()
        });
    }
    if let Some(m) = LOYALTY_RE.captures(line) {
        // loyalty ability
        let n = parse_i64(&m[1].replace(MINUS, "-").replace('+', ""));
        let (effect, targets) = parse_effect_text(&m[2], name);
        return Some(ActivatedAbility {
            loyalty_cost: Some(n),
            effect: Some(effect),
            targets,
            text: line.to_owned(),
            ..ActivatedAbility::default()
        });
    }
    if !COST_LINE_RE.is_match(line) || !line.contains(':') {
        return None;
    }
    let (cost_part, body) = line.split_once(':')?;
    if !is_cost(cost_part) {
        return None;
    }
    let body = body.trim();
    let mana: String = MANA_SYMBOLS_RE
        .find_iter(&cost_part.replace("{T}", ""))
        .map(|m| m.as_str())
        .collect();
    let tap = cost_part.contains("{T}");
    let mut sac = String::new();
    if let Some(sm) = SACRIFICE_RE.captures(cost_part) {
        let target = sm[2].trim().to_lowercase();
        sac = if target.starts_with(&short_name(name))
            || ["this creature", "this artifact", "it"].contains(&target.as_str())
        {
            "self".to_owned()
        } else {
            target
        };
    }
    let life_cost = PAY_LIFE_RE
        .captures(cost_part)
        .map_or(0, |lm| parse_i64(&lm[1]));
    // mana ability?
    if let Some(am) = ADD_RE.captures(body)
        && tap
    {
        return Some(ActivatedAbility {
            mana_cost: mana,
            tap_cost: tap,
            sac_cost: sac,
            life_cost,
            is_mana_ability: true,
            effect: Some(Effect::AddMana(parse_add_mana(&am[1]))),
            text: line.to_owned(),
            ..ActivatedAbility::default()
        });
    }
    let (effect, targets) = parse_effect_text(body, name);
    let low = line.to_lowercase();
    let sorcery =
        low.contains("only as a sorcery") || low.contains("only any time you could cast a sorcery");
    Some(ActivatedAbility {
        mana_cost: mana,
        tap_cost: tap,
        sac_cost: sac,
        life_cost,
        effect: Some(effect),
        targets,
        sorcery_only: sorcery,
        text: line.to_owned(),
        ..ActivatedAbility::default()
    })
}
