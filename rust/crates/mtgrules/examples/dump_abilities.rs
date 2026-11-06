//! Canonical dump of the compiled abilities.
//!
//! Loads a graph root through `CardDatabase`, compiles every distinct
//! card record with `compile_card` (overrides included) and writes one
//! JSON line per record, in index order, describing the resulting
//! characteristics structurally: every ability's kind, costs, targets and
//! effect tree, the keywords and cost modifiers, the clauses the compiler
//! could not read and the override's note (closures reduced to their
//! presence, dynamic counts to the tag `"dynamic"`). Dump before and
//! after a change to the compiler or an override and diff the two files:
//! the diff is exactly the cards whose compiled behaviour changed.
//!
//! ```text
//! cargo run -p mtgrules --example dump_abilities -- --root ../.. [--custom-cards F] [--extra-graph G]...
//! ```

use std::collections::BTreeSet;
use std::io::Write as _;
use std::path::PathBuf;
use std::process::ExitCode;

use mtgcards::CardDatabase;
use mtgrules::abilities::{Ability, TargetSpec, TokenSpec};
use mtgrules::compiler;
use mtgrules::effects::{AddMana, Effect};
use mtgrules::objects::Characteristics;
use mtgrules::overrides;
use mtgrules::protocols::CountValue;
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

fn sorted(set: &BTreeSet<String>) -> Value {
    Value::List(set.iter().map(|s| Value::from(s.as_str())).collect())
}

fn opt_int(n: Option<i64>) -> Value {
    n.map_or(Value::Null, Value::from)
}

fn opt_str(s: Option<&str>) -> Value {
    s.map_or(Value::Null, Value::from)
}

fn count(c: &CountValue) -> Value {
    match c {
        CountValue::Fixed(n) => Value::from(*n),
        CountValue::X => Value::from("x"),
        CountValue::Dynamic(_) => Value::from("dynamic"),
    }
}

fn token(spec: &TokenSpec) -> Value {
    Value::dict([
        ("name", Value::from(spec.name.as_str())),
        ("power", opt_int(spec.power)),
        ("toughness", opt_int(spec.toughness)),
        ("colors", sorted(&spec.colors)),
        ("types", sorted(&spec.types)),
        ("subtypes", sorted(&spec.subtypes)),
        ("keywords", sorted(&spec.keywords)),
        ("tapped", Value::from(spec.tapped)),
        ("predefined", Value::from(spec.predefined.as_str())),
        ("abilities", Value::from(spec.abilities.len())),
    ])
}

fn add_mana(a: &AddMana) -> Vec<(&'static str, Value)> {
    vec![
        (
            "types",
            Value::List(a.types.iter().map(|t| Value::from(t.as_str())).collect()),
        ),
        ("any_color", Value::from(a.any_color)),
        ("commander_identity", Value::from(a.commander_identity)),
        ("choice", Value::from(a.choice)),
        ("amount", Value::from(a.amount)),
    ]
}

fn index(i: usize) -> (&'static str, Value) {
    ("index", Value::from(i))
}

fn string(key: &'static str, s: &str) -> (&'static str, Value) {
    (key, Value::from(s))
}

/// The effect node: its Python class name, then its fields in the
/// Python dataclass order.
fn effect_tree(effect: &Effect) -> Value {
    let fields: Vec<(&'static str, Value)> = match effect {
        Effect::Sequence(parts) => vec![(
            "parts",
            Value::List(parts.iter().map(effect_tree).collect()),
        )],
        Effect::Custom { note, .. } => vec![("fn", Value::from(true)), string("note", note)],
        Effect::CreateTokens {
            count: c,
            spec,
            controller,
            tapped,
        } => vec![
            ("count", count(c)),
            ("spec", token(spec)),
            string("controller", controller),
            ("tapped", tapped.map_or(Value::Null, Value::from)),
        ],
        Effect::DrawCards { count: c, who } => vec![("count", count(c)), string("who", who)],
        Effect::GainLife { amount } | Effect::Drain { amount } => vec![("amount", count(amount))],
        Effect::LoseLife { amount, who } => vec![("amount", count(amount)), string("who", who)],
        Effect::DealDamage {
            amount,
            to,
            index: i,
        } => vec![("amount", count(amount)), string("to", to), index(*i)],
        Effect::Destroy { index: i, all_of } => vec![index(*i), string("all_of", all_of)],
        Effect::ExileObj { index: i }
        | Effect::TargetControllerBasicLand { index: i }
        | Effect::TargetControllerGainsPower { index: i }
        | Effect::LoseLifeTargetMV { index: i }
        | Effect::CopySpell { index: i }
        | Effect::TapTarget { index: i }
        | Effect::Blink { index: i } => vec![index(*i)],
        Effect::SacrificeSelf
        | Effect::TakeDeadCreature
        | Effect::CounterSpell
        | Effect::ProtectAll
        | Effect::TutorAny => Vec::new(),
        Effect::ReturnToHand {
            index: i,
            self_land,
        } => {
            vec![index(*i), ("self_land", Value::from(*self_land))]
        }
        Effect::PutCounters { kind, count: c, on } => {
            vec![string("kind", kind), ("count", count(c)), string("on", on)]
        }
        Effect::Proliferate { times } | Effect::Populate { times } => {
            vec![("times", Value::from(*times))]
        }
        Effect::SearchLands {
            count: c,
            tapped,
            to_hand,
            basic_only,
        } => vec![
            ("count", count(c)),
            ("tapped", Value::from(*tapped)),
            ("to_hand", Value::from(*to_hand)),
            ("basic_only", Value::from(*basic_only)),
        ],
        Effect::PutLandFromHand { tapped } => vec![("tapped", Value::from(*tapped))],
        Effect::AddMana(a) => add_mana(a),
        Effect::PumpAll {
            power,
            toughness,
            tokens_only,
        } => vec![
            ("power", Value::from(*power)),
            ("toughness", Value::from(*toughness)),
            ("tokens_only", Value::from(*tokens_only)),
        ],
        Effect::EnergyGain { count: c } | Effect::Scry { count: c } => vec![("count", count(c))],
        Effect::Untap { on } => vec![string("on", on)],
        Effect::Noop { note } => vec![string("note", note)],
    };
    let mut node = vec![("node", Value::from(effect.node_name()))];
    node.extend(fields);
    Value::dict(node)
}

fn target(spec: &TargetSpec) -> Value {
    Value::dict([
        ("what", Value::from(spec.what.as_str())),
        ("controller", Value::from(spec.controller.as_str())),
        ("count", Value::from(spec.count)),
        ("optional", Value::from(spec.optional)),
        ("other", Value::from(spec.other)),
        ("min_count", opt_int(spec.min_count)),
    ])
}

fn targets(specs: &[TargetSpec]) -> Value {
    Value::List(specs.iter().map(target).collect())
}

/// One ability as a flat record with a `kind` tag.
fn ability_record(ability: &Ability) -> Value {
    match ability {
        Ability::Activated(a) => Value::dict([
            ("kind", Value::from("activated")),
            ("mana_cost", Value::from(a.mana_cost.as_str())),
            ("tap_cost", Value::from(a.tap_cost)),
            ("sac_cost", Value::from(a.sac_cost.as_str())),
            ("life_cost", Value::from(a.life_cost)),
            ("loyalty_cost", opt_int(a.loyalty_cost)),
            ("effect", a.effect.as_ref().map_or(Value::Null, effect_tree)),
            ("targets", targets(&a.targets)),
            ("is_mana_ability", Value::from(a.is_mana_ability)),
            ("sorcery_only", Value::from(a.sorcery_only)),
            ("once_per_turn", Value::from(a.once_per_turn)),
            ("from_hand", Value::from(a.from_hand)),
            ("from_graveyard", Value::from(a.from_graveyard)),
            ("text", Value::from(a.text.as_str())),
        ]),
        Ability::Triggered(t) => Value::dict([
            ("kind", Value::from("triggered")),
            ("event", Value::from(t.trigger.event.as_str())),
            ("condition", Value::from(t.trigger.condition.is_some())),
            ("step", Value::from(t.trigger.step.as_str())),
            ("effect", effect_tree(&t.effect)),
            ("targets", targets(&t.targets)),
            ("intervening_if", Value::from(t.intervening_if.is_some())),
            ("text", Value::from(t.text.as_str())),
            ("optional", Value::from(t.optional)),
            ("once_each_turn", Value::from(t.once_each_turn)),
        ]),
        Ability::Spell(s) => Value::dict([
            ("kind", Value::from("spell")),
            ("effect", effect_tree(&s.effect)),
            ("targets", targets(&s.targets)),
            ("text", Value::from(s.text.as_str())),
        ]),
        Ability::Static(s) => Value::dict([
            ("kind", Value::from("static")),
            ("continuous", Value::from(s.continuous.is_some())),
            ("replacement", Value::from(s.replacement.is_some())),
            ("text", Value::from(s.text.as_str())),
            ("uncounterable_spells", Value::from(s.uncounterable_spells)),
            ("enters_tapped", Value::from(s.enters_tapped)),
        ]),
    }
}

/// The compiled characteristics of one card.
fn card_record(ch: &Characteristics) -> Value {
    let unknown = compiler::unknown_clauses();
    let notes = overrides::notes();
    Value::dict([
        ("name", Value::from(ch.name.as_str())),
        ("mana_cost", Value::from(ch.mana_cost.as_str())),
        ("colors", sorted(&ch.colors)),
        ("supertypes", sorted(&ch.supertypes)),
        ("types", sorted(&ch.types)),
        ("subtypes", sorted(&ch.subtypes)),
        ("power", opt_int(ch.power)),
        ("toughness", opt_int(ch.toughness)),
        ("loyalty", opt_int(ch.loyalty)),
        ("keywords", sorted(&ch.keywords)),
        (
            "cost_less_per_creature",
            Value::from(ch.cost_less_per_creature),
        ),
        ("additional_cost", Value::from(ch.additional_cost.as_str())),
        ("etb_x_counters", Value::from(ch.etb_x_counters.as_str())),
        (
            "abilities",
            Value::List(ch.abilities.iter().map(ability_record).collect()),
        ),
        (
            "unknown",
            unknown.get(&ch.name).map_or_else(
                || Value::List(Vec::new()),
                |clauses| Value::List(clauses.iter().map(|c| Value::from(c.as_str())).collect()),
            ),
        ),
        ("note", opt_str(notes.get(&ch.name).map(String::as_str))),
    ])
}

/// One line per distinct card record, in index order.
fn dump_lines(db: &CardDatabase) -> Vec<String> {
    db.index
        .unique_ids()
        .into_iter()
        .map(|id| {
            compiler::clear_unknown_clauses();
            let ch = compiler::compile_card(db.index.record(id));
            json::dumps(&card_record(&ch))
        })
        .collect()
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
            .lock()
            .write_all(text.as_bytes())
            .map_err(|e| e.to_string()),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("dump_abilities: {message}");
            ExitCode::FAILURE
        }
    }
}
