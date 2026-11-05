//! Oracle text -> ability AST compiler (the Python `compiler.py`).
//!
//! Compiles each card's oracle text from the knowledge graph into
//! structured abilities ([`SpellAbility`] / [`ActivatedAbility`] /
//! [`TriggeredAbility`] / [`StaticAbility`]) executed by the rules
//! engine. The grammar covers the dominant templates of the deck pool;
//! complex cards get hand-written implementations in [`crate::overrides`].
//! Clauses neither compiled nor overridden are recorded as unknown
//! ([`Effect::Noop`]) and reported by the adapter - nothing is skipped
//! silently.
//!
//! Effect clauses are parsed by a table of per-pattern handlers
//! ([`clauses::CLAUSE_PARSERS`]): each handler owns one oracle template
//! and either returns its `Effect` node or `None` to let the next handler
//! try.

mod activated;
mod clauses;
mod targets;
mod triggers;

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::rc::Rc;
use std::sync::LazyLock;

use mtgcards::CardData;
use regex::Regex;

pub use activated::{parse_activated_line, parse_add_mana};
pub use clauses::{parse_effect_clause, parse_effect_text};
pub use targets::{parse_token_clause, target_spec};
pub use triggers::parse_trigger_line;

use crate::abilities::{
    ActivatedAbility, SpellAbility, StaticAbility, TriggerSpec, TriggeredAbility,
};
use crate::effects::{Effect, land_mana_ability};
use crate::events::EventType;
use crate::game::MoveOptions;
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, ObjId, Zone};
use crate::protocols::CountValue;
use crate::rule;

thread_local! {
    /// Filled by [`compile_card`]: card name -> the uncompiled clauses.
    static UNKNOWN_CLAUSES: RefCell<BTreeMap<String, BTreeSet<String>>> =
        const { RefCell::new(BTreeMap::new()) };
}

/// Record an uncompiled clause for the coverage report.
pub(crate) fn note(name: &str, clause: &str) {
    UNKNOWN_CLAUSES.with(|u| {
        u.borrow_mut()
            .entry(name.to_owned())
            .or_default()
            .insert(clause.to_owned());
    });
}

/// The uncompiled clauses recorded so far, by card name (sorted, as the
/// adapter reports them).
#[must_use]
pub fn unknown_clauses() -> BTreeMap<String, BTreeSet<String>> {
    UNKNOWN_CLAUSES.with(|u| u.borrow().clone())
}

/// Forget the recorded uncompiled clauses.
pub fn clear_unknown_clauses() {
    UNKNOWN_CLAUSES.with(|u| u.borrow_mut().clear());
}

/// The keywords the compiler recognises on a keyword line.
pub const KEYWORD_WORDS: &[&str] = &[
    "flying",
    "vigilance",
    "trample",
    "haste",
    "deathtouch",
    "lifelink",
    "menace",
    "reach",
    "defender",
    "indestructible",
    "hexproof",
    "flash",
    "first strike",
    "double strike",
    "wither",
    "infect",
    "plainswalk",
    "islandwalk",
    "swampwalk",
    "mountainwalk",
    "forestwalk",
    "fear",
    "intimidate",
    "persist",
    "exalted",
];

/// A count word of the oracle text: a number, or the literal `x`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Num {
    /// a literal count
    N(i64),
    /// the spell's X
    X,
}

impl From<Num> for CountValue {
    fn from(n: Num) -> Self {
        match n {
            Num::N(n) => CountValue::Fixed(n),
            Num::X => CountValue::X,
        }
    }
}

/// Parse a count word: a number, or the literal `x` (unknown words
/// count as one).
#[must_use]
pub fn num(word: &str) -> Num {
    match word.to_lowercase().as_str() {
        "a" | "an" | "one" => Num::N(1),
        "two" => Num::N(2),
        "three" => Num::N(3),
        "four" => Num::N(4),
        "five" => Num::N(5),
        "six" => Num::N(6),
        "seven" => Num::N(7),
        "eight" => Num::N(8),
        "nine" => Num::N(9),
        "ten" => Num::N(10),
        "x" => Num::X,
        w => {
            if !w.is_empty() && w.bytes().all(|b| b.is_ascii_digit()) {
                w.parse().map_or(Num::N(1), Num::N)
            } else {
                Num::N(1)
            }
        }
    }
}

/// The card's short (pre-comma) name, lowercased.
#[must_use]
pub(crate) fn short_name(name: &str) -> String {
    name.split_once(',')
        .map_or(name, |(head, _)| head)
        .to_lowercase()
}

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("the compiler's patterns are valid")
}

// ---------------------------------------------------------------- statics

static UNCOUNTERABLE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^spells you control can't be countered\.?$"));
static ANTHEM_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^(creatures?|creature tokens?|artifact creatures?) you control get \+(\d+)/\+(\d+)(?: and have ([a-z ]+?))?\.?$",
    )
});

/// Parse one static-ability oracle line into a [`StaticAbility`].
#[must_use]
pub fn parse_static_line(line: &str, _name: &str) -> Option<StaticAbility> {
    let low = line.to_lowercase();
    if UNCOUNTERABLE_RE.is_match(&low) {
        return Some(StaticAbility {
            text: line.to_owned(),
            uncounterable_spells: true,
            ..StaticAbility::default()
        });
    }
    let m = ANTHEM_RE.captures(&low)?;
    let granted: BTreeSet<String> = m
        .get(4)
        .map_or("", |g| g.as_str())
        .split(" and ")
        .map(str::trim)
        .filter(|k| KEYWORD_WORDS.contains(k))
        .map(str::to_owned)
        .collect();
    if m.get(4).is_some() && granted.is_empty() {
        return None; // unknown keyword grant
    }
    let boost = (parse_i64(&m[2]), parse_i64(&m[3]));
    Some(anthem_static(
        line,
        boost,
        granted,
        m[1].contains("token"),
        m[1].contains("artifact"),
    ))
}

/// Build the anthem [`StaticAbility`] for [`parse_static_line`].
fn anthem_static(
    line: &str,
    boost: (i64, i64),
    granted: BTreeSet<String>,
    tokens_only: bool,
    art_only: bool,
) -> StaticAbility {
    let (boost_p, boost_t) = boost;
    let granted = Rc::new(granted);
    let factory = move |game: &crate::Game, source: ObjId| {
        let me = game.obj(source).controller;
        let applies = move |g: &crate::Game, obj: ObjId, ch: &Characteristics| {
            let o = g.obj(obj);
            if o.controller != me || !ch.types.contains("Creature") {
                return false;
            }
            if tokens_only && !o.is_token {
                return false;
            }
            !art_only || ch.types.contains("Artifact")
        };
        let pump = move |_g: &crate::Game, _o: ObjId, ch: &mut Characteristics| {
            ch.power = Some(ch.power.unwrap_or(0) + boost_p);
            ch.toughness = Some(ch.toughness.unwrap_or(0) + boost_t);
        };
        let mut effects =
            vec![ContinuousEffect::new(7, "c", applies, pump).with_source(Some(source))];
        if !granted.is_empty() {
            let granted = Rc::clone(&granted);
            let grant = move |_g: &crate::Game, _o: ObjId, ch: &mut Characteristics| {
                ch.keywords.extend(granted.iter().cloned());
            };
            effects.push(ContinuousEffect::new(6, "", applies, grant).with_source(Some(source)));
        }
        effects
    };
    StaticAbility::continuous(Rc::new(factory), line)
}

// ---------------------------------------------------------------- keywords

static PAREN_TAIL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\s*\(.*\)$"));
static WARD_N_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^ward\s*\{(\d+)\}$"));
static WARD_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^ward\b"));
static TOXIC_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^toxic (\d+)$"));
static KEYWORD_SEP_RE: LazyLock<Regex> = LazyLock::new(|| re(r"[,;] "));

/// Parse a keywords-only line into the set of keyword strings; `None`
/// when any part is not a keyword.
#[must_use]
pub fn parse_keyword_line(line: &str) -> Option<BTreeSet<String>> {
    let mut got = BTreeSet::new();
    for part in KEYWORD_SEP_RE.split(line.trim_end_matches('.')) {
        let p = part.trim().to_lowercase();
        let p = PAREN_TAIL_RE.replace(&p, "").into_owned();
        if KEYWORD_WORDS.contains(&p.as_str()) {
            got.insert(p);
        } else if let Some(m) = WARD_N_RE.captures(&p) {
            got.insert(format!("ward:{}", &m[1]));
        } else if WARD_RE.is_match(&p) {
            got.insert("ward:2".to_owned());
        } else if let Some(m) = TOXIC_RE.captures(&p) {
            got.insert(format!("toxic:{}", &m[1]));
        } else if p == "station" {
            got.insert(p);
        } else {
            return None;
        }
    }
    if got.is_empty() { None } else { Some(got) }
}

// ---------------------------------------------------------------- compile

/// Parse a run of ASCII digits the patterns guarantee.
pub(crate) fn parse_i64(digits: &str) -> i64 {
    digits.parse().unwrap_or(0)
}

/// `CardData` (from the knowledge graph) -> compiled [`Characteristics`]
/// (rule 113.2). Overrides win per card.
#[rule("113.2")]
#[must_use]
pub fn compile_card(card: &CardData) -> Characteristics {
    let mut ch = Characteristics {
        name: card.name.clone(),
        mana_cost: card.mana_cost.clone(),
        supertypes: card.supertypes.clone(),
        types: card.types.clone(),
        subtypes: card.subtypes.clone(),
        power: card.power,
        toughness: card.toughness,
        loyalty: card.loyalty,
        ..Characteristics::default()
    };
    if !card.mana_cost.is_empty() {
        ch.colors = card
            .color_identity
            .iter()
            .filter(|c| ["W", "U", "B", "R", "G"].contains(&c.as_str()))
            .cloned()
            .collect();
    }

    if crate::overrides::apply_override(&mut ch, card) {
        return ch;
    }

    let is_spell = ch.types.contains("Instant") || ch.types.contains("Sorcery");
    let spell_clauses = compile_oracle_lines(&mut ch, card, is_spell);
    if is_spell {
        let text = spell_clauses.join(" ");
        let (effect, targets) = parse_effect_text(&text, &card.name);
        let mut ability = SpellAbility::new(effect);
        ability.targets = targets;
        ability.text = text;
        ch.abilities.push(ability.into());
    }

    apply_land_behavior(&mut ch, card);
    add_keyword_abilities(&mut ch);
    ch
}

static REMINDER_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\s*\([^)]*\)"));
static TAPLAND_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^this land enters (the battlefield )?tapped"));

/// Compile each oracle line onto `ch`; return the leftover spell clauses.
fn compile_oracle_lines(ch: &mut Characteristics, card: &CardData, is_spell: bool) -> Vec<String> {
    let name = &card.name;
    let mut spell_clauses = Vec::new();
    for raw_line in card.oracle.split('\n') {
        let line = REMINDER_RE.replace_all(raw_line, "");
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // taplands are modeled through the graph's :entersTapped fact
        // (conditional forms conservatively enter tapped, like the
        // heuristic engine)
        if TAPLAND_RE.is_match(line) {
            continue;
        }
        if parse_cost_modifier_line(ch, line) {
            continue;
        }
        if let Some(kws) = parse_keyword_line(line) {
            ch.keywords.extend(kws);
            continue;
        }
        // triggers before activated abilities: a trigger line may contain
        // a colon ("When ..., choose one: ...") and must never be read as
        // a cost-free activated ability (fix F5)
        if let Some(trig) = parse_trigger_line(line, name) {
            ch.abilities.push(trig.into());
            continue;
        }
        if let Some(ab) = parse_activated_line(line, name) {
            ch.abilities.push(ab.into());
            continue;
        }
        if let Some(stat) = parse_static_line(line, name) {
            ch.abilities.push(stat.into());
            continue;
        }
        if is_spell {
            spell_clauses.push(line.to_owned());
            continue;
        }
        note(name, line);
    }
    spell_clauses
}

static COST_LESS_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^this spell costs \{(\d+)\} less to cast for each creature on the battlefield\.?$")
});
static ADDITIONAL_COST_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)^as an additional cost to cast this spell, (sacrifice a creature|discard a card)\.?$")
});

/// Cost-modifier lines: rule 601.2f reductions, 601.2b extra costs.
#[rule("601.2b", "601.2f")]
fn parse_cost_modifier_line(ch: &mut Characteristics, line: &str) -> bool {
    if let Some(m) = COST_LESS_RE.captures(line) {
        ch.cost_less_per_creature = parse_i64(&m[1]); // rule 601.2f
        return true;
    }
    if let Some(m) = ADDITIONAL_COST_RE.captures(line) {
        // rule 601.2b
        ch.additional_cost = if m[1].contains("sacrifice") {
            "sacrifice_creature".to_owned()
        } else {
            "discard_card".to_owned()
        };
        return true;
    }
    false
}

/// Graph land facts -> mana abilities, tapped markers, fetches.
fn apply_land_behavior(ch: &mut Characteristics, card: &CardData) {
    if !ch.types.contains("Land") {
        return;
    }
    if let Some(colors) = card.behavior.land_colors()
        && !ch
            .abilities
            .iter()
            .any(|a| a.as_activated().is_some_and(|ab| ab.is_mana_ability))
    {
        ch.abilities.push(land_mana_ability(&colors).into());
    }
    if card.behavior.enters_tapped() {
        ch.abilities.push(
            StaticAbility {
                enters_tapped: true,
                text: "enters the battlefield tapped".to_owned(),
                ..StaticAbility::default()
            }
            .into(),
        );
    }
    if card.behavior.fetch_land() {
        ch.abilities.push(
            ActivatedAbility {
                tap_cost: true,
                sac_cost: "self".to_owned(),
                effect: Some(Effect::SearchLands {
                    count: CountValue::Fixed(1),
                    tapped: true,
                    to_hand: false,
                    basic_only: true,
                }),
                text: "{T}, Sacrifice: fetch a basic land.".to_owned(),
                ..ActivatedAbility::default()
            }
            .into(),
        );
    }
}

/// Keywords that carry rules baggage beyond combat checks.
#[rule("702.79", "702.83a", "702.83b")]
fn add_keyword_abilities(ch: &mut Characteristics) {
    if ch.keywords.contains("exalted") {
        // rule 702.83a: "Whenever a creature you control attacks alone,
        // that creature gets +1/+1 until end of turn." The ATTACKS event
        // carries "alone" (rule 702.83b, computed once all attackers are
        // declared).
        let attacks_alone = |game: &crate::Game, source: ObjId, event: &crate::events::Event| {
            event.data.alone == Some(true)
                && event
                    .data
                    .obj
                    .is_some_and(|a| game.obj(a).controller == game.obj(source).controller)
        };
        let exalted_pump = |game: &mut crate::Game, ctx: &crate::effects::Ctx| {
            let Some(target) = ctx.event_obj else {
                return;
            };
            if game.obj(target).zone != Zone::Battlefield {
                return;
            }
            let applies = move |_g: &crate::Game, obj: ObjId, _ch: &Characteristics| obj == target;
            let pump = |_g: &crate::Game, _o: ObjId, c: &mut Characteristics| {
                c.power = Some(c.power.unwrap_or(0) + 1);
                c.toughness = Some(c.toughness.unwrap_or(0) + 1);
            };
            game.add_floating_effect(
                ContinuousEffect::new(7, "c", applies, pump)
                    .with_source(ctx.source)
                    .until_end_of_turn(),
            );
        };
        let mut ability = TriggeredAbility::new(
            TriggerSpec::when(EventType::Attacks, Rc::new(attacks_alone)),
            Effect::custom("", exalted_pump),
        );
        "Exalted (rule 702.83): the lone attacker gets +1/+1".clone_into(&mut ability.text);
        ch.abilities.push(ability.into());
    }
    if ch.keywords.contains("persist") {
        let persist_return = |game: &mut crate::Game, ctx: &crate::effects::Ctx| {
            let Some(src) = ctx.source else {
                return;
            };
            let o = game.obj(src);
            if o.zone == Zone::Graveyard
                && !o.is_token
                && o.lki_counters.get("-1/-1").copied().unwrap_or(0) == 0
            {
                game.obj_mut(src).controller = ctx.controller;
                let mut options = MoveOptions::default();
                options.counters.insert("-1/-1".to_owned(), 1);
                game.move_zone_with(src, Zone::Battlefield, &options);
            }
        };
        let mut ability = TriggeredAbility::new(
            TriggerSpec::on(EventType::Dies),
            Effect::custom("", persist_return),
        );
        "Persist (rule 702.79): return with a -1/-1 counter".clone_into(&mut ability.text);
        ch.abilities.push(ability.into());
    }
}
