//! Shared engine type vocabulary: the callback signatures.
//!
//! The engine wires card behaviour through small closures (layer-system
//! predicates, trigger conditions, replacement matchers, ability
//! factories). The Python module pins their signatures as type aliases
//! for the type checker; here they are the `Rc<dyn Fn>` types every
//! closure is stored as. A closure captures handles ([`ObjId`],
//! [`PlayerId`]), never references into the game, and receives the game
//! when it runs.
//!
//! The Python `CardRef` protocol (the slice of a card the engine reads) is
//! simply [`mtgcards::CardData`] here, behind an `Rc`.

use std::rc::Rc;

use crate::effects::Ctx;
use crate::events::Event;
use crate::game::Game;
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, ObjId};
use crate::replacements::Replacement;

/// `applies_to(game, obj, chars)` predicate of a continuous effect (rule
/// 611.1: which objects the effect applies to).
pub type CharPredicate = Rc<dyn Fn(&Game, ObjId, &Characteristics) -> bool>;

/// `apply(game, obj, chars)` mutator of a continuous effect: modifies the
/// working characteristics in place (rule 613.1).
pub type CharMutator = Rc<dyn Fn(&Game, ObjId, &mut Characteristics)>;

/// `condition(game, source, event)` of a trigger spec (rule 603.1); true
/// means the ability triggers off the event.
pub type TriggerCondition = Rc<dyn Fn(&Game, ObjId, &Event) -> bool>;

/// Rule 603.4 intervening-"if" clause: `pred(game, source)`.
pub type InterveningIf = Rc<dyn Fn(&Game, ObjId) -> bool>;

/// `matches(game, event)` predicate of a replacement effect (rule 614.1).
pub type EventMatcher = Rc<dyn Fn(&Game, &Event) -> bool>;

/// `replace(game, event)` of a replacement effect: returns the modified
/// event, or `None` when a prevention effect removes it (rules 614/615).
pub type EventReplacer = Rc<dyn Fn(&mut Game, Event) -> Option<Event>>;

/// Static-ability factory producing continuous effects for its source
/// permanent (rule 604.2): `factory(game, source)`.
pub type ContinuousFactory = Rc<dyn Fn(&Game, ObjId) -> Vec<ContinuousEffect>>;

/// Static-ability factory producing replacement effects for its source
/// permanent (rule 614.7a): `factory(game, source)`.
pub type ReplacementFactory = Rc<dyn Fn(&Game, ObjId) -> Vec<Replacement>>;

/// A count computed at resolution: `f(game, ctx)`.
pub type CountFn = Rc<dyn Fn(&Game, &Ctx) -> i64>;

/// A count expression in an effect node: a plain number, the chosen X
/// (rule 107.3), or `f(game, ctx)`.
#[derive(Clone)]
pub enum CountValue {
    /// a fixed count
    Fixed(i64),
    /// the chosen X
    X,
    /// computed at resolution
    Dynamic(CountFn),
}

impl std::fmt::Debug for CountValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CountValue::Fixed(n) => write!(f, "{n}"),
            CountValue::X => f.write_str("x"),
            CountValue::Dynamic(_) => f.write_str("<dynamic>"),
        }
    }
}

impl From<i64> for CountValue {
    fn from(n: i64) -> Self {
        CountValue::Fixed(n)
    }
}

/// A hand-written one-shot effect body: `fn(game, ctx)` (rule 610.1).
pub type OneShot = Rc<dyn Fn(&mut Game, &Ctx)>;

/// A value of an engine log field.
#[derive(Clone, Debug, PartialEq)]
pub enum LogValue {
    /// `None`
    Null,
    /// a flag
    Bool(bool),
    /// a number
    Int(i64),
    /// a name
    Str(String),
}

impl From<&str> for LogValue {
    fn from(s: &str) -> Self {
        LogValue::Str(s.to_owned())
    }
}

impl From<String> for LogValue {
    fn from(s: String) -> Self {
        LogValue::Str(s)
    }
}

impl From<i64> for LogValue {
    fn from(n: i64) -> Self {
        LogValue::Int(n)
    }
}

impl From<bool> for LogValue {
    fn from(b: bool) -> Self {
        LogValue::Bool(b)
    }
}

impl From<&String> for LogValue {
    fn from(s: &String) -> Self {
        LogValue::Str(s.clone())
    }
}

/// A Python `x or None` field: `None` is logged as `null`.
impl<T: Into<LogValue>> From<Option<T>> for LogValue {
    fn from(value: Option<T>) -> Self {
        value.map_or(LogValue::Null, Into::into)
    }
}

/// The fields of one log event, in keyword order.
pub type LogFields = Vec<(&'static str, LogValue)>;

/// Engine log sink: `log(event, **fields)` (adapter fan-out, recorders).
/// The sink also receives the game, which the Python recorder keeps a
/// reference to from `attach`.
pub type LogFn = Box<dyn FnMut(&Game, &str, LogFields)>;
