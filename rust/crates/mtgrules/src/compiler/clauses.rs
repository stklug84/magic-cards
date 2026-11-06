//! The effect-clause parser table and the effect-text parser.

use std::rc::Rc;
use std::sync::LazyLock;

use regex::Regex;

use super::targets::{TOKEN_RE, parse_token_clause, target_spec};
use super::{Num, note, num, parse_i64, re, short_name};
use crate::abilities::{TargetSpec, TokenSpec};
use crate::effects::Effect;
use crate::protocols::CountValue;
use crate::rule;

/// One effect sentence handed to the clause-parser table.
pub(crate) struct Clause<'a> {
    /// stripped, trailing dot removed, original case
    pub text: &'a str,
    /// lowercased form of `clause`
    pub low: String,
    /// the card name (self-reference detection)
    pub name: &'a str,
    /// growing target list of the whole effect text; handlers append
    /// their `TargetSpec` and index into it
    pub targets: &'a mut Vec<TargetSpec>,
}

impl Clause<'_> {
    fn short_name(&self) -> String {
        short_name(self.name)
    }

    /// Append `spec` and return its index.
    fn push_target(&mut self, spec: TargetSpec) -> usize {
        self.targets.push(spec);
        self.targets.len() - 1
    }
}

/// One entry of the clause-parser table.
pub(crate) type ClauseParser = fn(&mut Clause<'_>) -> Option<Effect>;

fn count(n: Num) -> CountValue {
    n.into()
}

fn p_create_creature_tokens(c: &mut Clause<'_>) -> Option<Effect> {
    let m = TOKEN_RE.captures(c.text)?;
    let (n, spec) = parse_token_clause(c.text, &m);
    Some(Effect::create_tokens(count(n), spec))
}

static TREASURES_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"create (a|one|two|three|x|\d+) (tapped )?treasure tokens?"));

fn p_create_treasures(c: &mut Clause<'_>) -> Option<Effect> {
    let m = TREASURES_RE.captures(&c.low)?;
    Some(Effect::CreateTokens {
        count: count(num(&m[1])),
        spec: Rc::new(TokenSpec::treasure()),
        controller: "you".to_owned(),
        tapped: if m.get(2).is_some() { Some(true) } else { None },
    })
}

static DRAW_RE: LazyLock<Regex> = LazyLock::new(|| re(r"draw (a|one|two|three|four|x|\d+) cards?"));

fn p_draw(c: &mut Clause<'_>) -> Option<Effect> {
    let m = DRAW_RE.captures(&c.low)?;
    let who = if c.low.contains("each player") {
        "each"
    } else {
        "you"
    };
    Some(Effect::DrawCards {
        count: count(num(&m[1])),
        who: who.to_owned(),
    })
}

static OPPONENT_LOSES_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"each opponent loses (a|one|two|three|x|\d+) life"));

fn p_each_opponent_loses(c: &mut Clause<'_>) -> Option<Effect> {
    let m = OPPONENT_LOSES_RE.captures(&c.low)?;
    let n = count(num(&m[1]));
    if c.low.contains("you gain") && (c.low.contains("that much") || c.low.contains("equal")) {
        return Some(Effect::Drain { amount: n });
    }
    Some(Effect::LoseLife {
        amount: n,
        who: "each_opponent".to_owned(),
    })
}

static GAIN_LIFE_RE: LazyLock<Regex> = LazyLock::new(|| re(r"you gain (\d+|x) life"));

fn p_gain_life(c: &mut Clause<'_>) -> Option<Effect> {
    let m = GAIN_LIFE_RE.captures(&c.low)?;
    Some(Effect::GainLife {
        amount: count(num(&m[1])),
    })
}

static LOSE_LIFE_RE: LazyLock<Regex> = LazyLock::new(|| re(r"you lose (\d+) life"));

fn p_lose_life(c: &mut Clause<'_>) -> Option<Effect> {
    let m = LOSE_LIFE_RE.captures(&c.low)?;
    Some(Effect::LoseLife {
        amount: CountValue::Fixed(parse_i64(&m[1])),
        who: "you".to_owned(),
    })
}

fn p_destroy_all_creatures(c: &mut Clause<'_>) -> Option<Effect> {
    c.low
        .contains("destroy all creatures")
        .then(|| Effect::Destroy {
            index: 0,
            all_of: "creatures".to_owned(),
        })
}

static DAMAGE_EACH_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"deals? (\d+|x) damage to each creature"));

fn p_damage_each_creature(c: &mut Clause<'_>) -> Option<Effect> {
    let m = DAMAGE_EACH_RE.captures(&c.low)?;
    Some(Effect::DealDamage {
        amount: count(num(&m[1])),
        to: "each_creature".to_owned(),
        index: 0,
    })
}

static DAMAGE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"deals? (\d+|x) damage(?:,| to| divided)"));

/// `... deals N damage to <target|divided ... among targets|each opponent>`
#[rule("601.2d")]
fn p_damage(c: &mut Clause<'_>) -> Option<Effect> {
    let m = DAMAGE_RE.captures(&c.low)?;
    let amount = count(num(&m[1]));
    if let Some(spec) = target_spec(c.text) {
        let index = c.push_target(spec);
        let to = if c.low.contains("divided") {
            "divided"
        } else {
            "target"
        };
        return Some(Effect::DealDamage {
            amount,
            to: to.to_owned(),
            index,
        });
    }
    if c.low.contains("each opponent") {
        return Some(Effect::LoseLife {
            amount,
            who: "each_opponent".to_owned(),
        });
    }
    None
}

/// `<verb> [up to two | any number of | one or two | another ...] target`
const COUNTED_TARGET: &str =
    r"(?:up to |any number of |one or two |one, two, or three )?(?:[a-z]+ )?(?:other )?target";

static DESTROY_TARGET_RE: LazyLock<Regex> =
    LazyLock::new(|| re(&format!(r"\bdestroy {COUNTED_TARGET}")));

fn p_destroy_target(c: &mut Clause<'_>) -> Option<Effect> {
    if !DESTROY_TARGET_RE.is_match(&c.low) {
        return None;
    }
    let spec = target_spec(c.text)?;
    let index = c.push_target(spec);
    Some(Effect::Destroy {
        index,
        all_of: String::new(),
    })
}

static EXILE_TARGET_RE: LazyLock<Regex> =
    LazyLock::new(|| re(&format!(r"\bexile {COUNTED_TARGET}")));

fn p_exile_target(c: &mut Clause<'_>) -> Option<Effect> {
    if !EXILE_TARGET_RE.is_match(&c.low) {
        return None;
    }
    let spec = target_spec(c.text)?;
    let index = c.push_target(spec);
    Some(Effect::ExileObj { index })
}

static COUNTER_SPELL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"counter target .*spell"));

fn p_counter_spell(c: &mut Clause<'_>) -> Option<Effect> {
    if !COUNTER_SPELL_RE.is_match(&c.low) {
        return None;
    }
    c.targets.push(TargetSpec::new("spell"));
    Some(Effect::CounterSpell)
}

static COPY_SPELL_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"copy target (?:instant(?: or sorcery)?|sorcery) spell"));

fn p_copy_spell(c: &mut Clause<'_>) -> Option<Effect> {
    if !COPY_SPELL_RE.is_match(&c.low) {
        return None;
    }
    let index = c.push_target(TargetSpec::new("spell"));
    Some(Effect::CopySpell { index })
}

static TAP_TARGET_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^tap (?:[a-z, ]+ )?target creatures?"));

fn p_tap_target(c: &mut Clause<'_>) -> Option<Effect> {
    if !TAP_TARGET_RE.is_match(&c.low) {
        return None;
    }
    let spec = target_spec(c.text)?;
    if spec.what != "creature" {
        return None;
    }
    let index = c.push_target(spec);
    Some(Effect::TapTarget { index })
}

fn p_rider_basic_land(c: &mut Clause<'_>) -> Option<Effect> {
    c.low
        .starts_with("its controller may search their library for a basic land card")
        .then_some(Effect::TargetControllerBasicLand { index: 0 })
}

fn p_rider_gain_power(c: &mut Clause<'_>) -> Option<Effect> {
    c.low
        .starts_with("its controller gains life equal to its power")
        .then_some(Effect::TargetControllerGainsPower { index: 0 })
}

fn p_rider_lose_mv(c: &mut Clause<'_>) -> Option<Effect> {
    c.low
        .starts_with("you lose life equal to that permanent's mana value")
        .then_some(Effect::LoseLifeTargetMV { index: 0 })
}

static PUT_LAND_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^you may put a land card from your hand(?: or graveyard)? onto the battlefield tapped")
});

fn p_put_land_from_hand(c: &mut Clause<'_>) -> Option<Effect> {
    // graveyard option simplified to hand-only
    PUT_LAND_RE
        .is_match(&c.low)
        .then_some(Effect::PutLandFromHand { tapped: true })
}

fn p_take_dead_creature(c: &mut Clause<'_>) -> Option<Effect> {
    c.low
        .starts_with("you may put that card onto the battlefield under your control")
        .then_some(Effect::TakeDeadCreature)
}

static PUT_COUNTERS_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"put (a|one|two|three|x|\d+) ([+-]1/[+-]1) counters? on"));

fn p_put_counters(c: &mut Clause<'_>) -> Option<Effect> {
    let m = PUT_COUNTERS_RE.captures(&c.low)?;
    let (n, kind) = (count(num(&m[1])), m[2].to_owned());
    let counters = |on: &str| Effect::PutCounters {
        kind: kind.clone(),
        count: n.clone(),
        on: on.to_owned(),
    };
    if c.low.contains("each creature you don't control")
        || c.low.contains("each creature your opponents control")
    {
        return Some(counters("each_opponent_creature"));
    }
    if c.low.contains("each creature") || c.low.contains("each other creature") {
        return Some(counters("each_creature"));
    }
    if c.low.contains("on it") || c.low.contains(&format!("on {}", c.short_name())) {
        return Some(counters("self"));
    }
    if let Some(spec) = target_spec(c.text) {
        c.targets.push(spec);
        return Some(counters("target"));
    }
    Some(counters("self"))
}

fn p_proliferate(c: &mut Clause<'_>) -> Option<Effect> {
    c.low.contains("proliferate").then(|| Effect::Proliferate {
        times: if c.low.contains("proliferate twice") {
            2
        } else {
            1
        },
    })
}

fn p_populate(c: &mut Clause<'_>) -> Option<Effect> {
    c.low
        .starts_with("populate")
        .then_some(Effect::Populate { times: 1 })
}

static SEARCH_LANDS_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"search your library for (?:up to )?(a|an|one|two|three|x)[^.]*?(land|plains|island|swamp|mountain|forest)",
    )
});

fn p_search_lands(c: &mut Clause<'_>) -> Option<Effect> {
    let m = SEARCH_LANDS_RE.captures(&c.low)?;
    Some(Effect::SearchLands {
        count: count(num(&m[1])),
        tapped: c.low.contains("tapped"),
        to_hand: c.low.contains("hand") && !c.low.contains("battlefield"),
        basic_only: c.low.contains("basic") || &m[2] != "land",
    })
}

static RETURN_OWN_LAND_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^return a land you control to (?:its|their) owner"));

fn p_return_own_land(c: &mut Clause<'_>) -> Option<Effect> {
    RETURN_OWN_LAND_RE
        .is_match(&c.low)
        .then_some(Effect::ReturnToHand {
            index: 0,
            self_land: true,
        })
}

static SCRY_RE: LazyLock<Regex> = LazyLock::new(|| re(r"scry (\d+)"));

fn p_scry(c: &mut Clause<'_>) -> Option<Effect> {
    let m = SCRY_RE.captures(&c.low)?;
    Some(Effect::Scry {
        count: CountValue::Fixed(parse_i64(&m[1])),
    })
}

static TUTOR_RE: LazyLock<Regex> = LazyLock::new(|| re(r"search your library for a card.*hand"));

fn p_tutor(c: &mut Clause<'_>) -> Option<Effect> {
    TUTOR_RE.is_match(&c.low).then_some(Effect::TutorAny)
}

static RETURN_TARGET_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"return target .* to (?:its owner's|their owners?') hands?"));

fn p_return_target_to_hand(c: &mut Clause<'_>) -> Option<Effect> {
    if !RETURN_TARGET_RE.is_match(&c.low) {
        return None;
    }
    let spec = target_spec(c.text)?;
    let index = c.push_target(spec);
    Some(Effect::ReturnToHand {
        index,
        self_land: false,
    })
}

static ENERGY_RE: LazyLock<Regex> = LazyLock::new(|| re(r"you get (\{e\})+"));

fn p_energy(c: &mut Clause<'_>) -> Option<Effect> {
    ENERGY_RE.is_match(&c.low).then(|| Effect::EnergyGain {
        count: CountValue::Fixed(i64::try_from(c.low.matches("{e}").count()).unwrap_or(0)),
    })
}

static PUMP_ALL_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"creatures you control get \+(\d+)/\+(\d+) until end of turn"));

fn p_pump_all(c: &mut Clause<'_>) -> Option<Effect> {
    let m = PUMP_ALL_RE.captures(&c.low)?;
    Some(Effect::PumpAll {
        power: parse_i64(&m[1]),
        toughness: parse_i64(&m[2]),
        tokens_only: false,
    })
}

fn p_protect_all(c: &mut Clause<'_>) -> Option<Effect> {
    (c.low.contains("gain hexproof and indestructible")
        || (c.low.contains("permanents you control gain") && c.low.contains("protection")))
    .then_some(Effect::ProtectAll)
}

fn p_sacrifice_self(c: &mut Clause<'_>) -> Option<Effect> {
    (c.low.starts_with("sacrifice ") && c.low.contains(&c.short_name()))
        .then_some(Effect::SacrificeSelf)
}

/// The ordered clause-parser table: the first handler to return an
/// effect wins.
pub(crate) const CLAUSE_PARSERS: &[ClauseParser] = &[
    p_create_creature_tokens,
    p_create_treasures,
    p_draw,
    p_each_opponent_loses,
    p_gain_life,
    p_lose_life,
    p_destroy_all_creatures,
    p_damage_each_creature,
    p_damage,
    p_destroy_target,
    p_exile_target,
    p_counter_spell,
    p_copy_spell,
    p_tap_target,
    p_rider_basic_land,
    p_rider_gain_power,
    p_rider_lose_mv,
    p_put_land_from_hand,
    p_take_dead_creature,
    p_put_counters,
    p_proliferate,
    p_populate,
    p_search_lands,
    p_return_own_land,
    p_scry,
    p_tutor,
    p_return_target_to_hand,
    p_energy,
    p_pump_all,
    p_protect_all,
    p_sacrifice_self,
];

/// One sentence -> `Effect` node (or `None` if unrecognized); a handler
/// that targets appends its `TargetSpec` to `targets`.
#[must_use]
pub fn parse_effect_clause(
    clause: &str,
    name: &str,
    targets: &mut Vec<TargetSpec>,
) -> Option<Effect> {
    let stripped = clause.trim().trim_end_matches('.');
    let low = stripped.to_lowercase();
    if low.is_empty() {
        return None;
    }
    let mut c = Clause {
        text: stripped,
        low,
        name,
        targets,
    };
    CLAUSE_PARSERS.iter().find_map(|parser| parser(&mut c))
}

/// Riders that are true no-ops in this engine (mechanics it never uses,
/// e.g. there is no regeneration), so they need no unknown-clause report.
static INERT_RIDERS: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)^(it can't be regenerated|cycling |evoke |compleated$|as long as you control|shuffle|activate only|you may choose new targets|do this only once each turn)",
    )
});
static SENTENCE_END_RE: LazyLock<Regex> = LazyLock::new(|| re(r"[.;]\s+"));
static GAIN_LOST_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^you gain life equal to the (?:life lost|total life lost)(?: this way)?")
});

/// Split `text` into sentences after each `.` or `;` followed by
/// whitespace (the Python `re.split(r"(?<=[.;])\s+", text)`).
fn sentences(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    for m in SENTENCE_END_RE.find_iter(text) {
        out.push(&text[start..=m.start()]);
        start = m.end();
    }
    out.push(&text[start..]);
    out
}

/// Full effect text -> `(Effect, targets)`.
#[must_use]
pub fn parse_effect_text(text: &str, name: &str) -> (Effect, Vec<TargetSpec>) {
    let mut targets = Vec::new();
    let mut parts: Vec<Effect> = Vec::new();
    for raw_sentence in sentences(text) {
        let sentence = raw_sentence.trim();
        if sentence.is_empty() {
            continue;
        }
        let low = sentence.to_lowercase();
        let low = low.trim_end_matches('.');
        // "You gain life equal to the life lost this way." merges the
        // preceding each-opponent life loss into a Drain (Exsanguinate)
        if GAIN_LOST_RE.is_match(low)
            && let Some(Effect::LoseLife { amount, who }) = parts.last()
            && who == "each_opponent"
        {
            let amount = amount.clone();
            parts.pop();
            parts.push(Effect::Drain { amount });
            continue;
        }
        if let Some(eff) = parse_effect_clause(sentence, name, &mut targets) {
            parts.push(eff);
        } else {
            if !INERT_RIDERS.is_match(sentence) {
                note(name, sentence);
            }
            parts.push(Effect::Noop {
                note: sentence.to_owned(),
            });
        }
    }
    let effect = match parts.len() {
        1 => parts.remove(0),
        _ => Effect::Sequence(parts),
    };
    (effect, targets)
}
