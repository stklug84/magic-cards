//! Behaviour hooks: the per-card simulation annotations.
//!
//! Oracle text cannot be executed, so cards whose effect matters to the
//! simulation carry explicit behaviour hooks. They come from three layers
//! (`database` applies them in this order): graph land facts, oracle-text
//! derivation (never overwriting an existing key) and the hand-authored
//! `MagicSimulationAnnotations.ttl` (`:hasBehaviorHook` / `:threatWeight`),
//! which always wins.
//!
//! A [`Behavior`] is an insertion-ordered map (a Python `dict`) of
//! [`Value`]s. Python sets become sorted lists, tuples become lists; the
//! keys the engine reads have typed accessors here, the rest is kept so
//! dumps and the parity comparison see everything.

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::LazyLock;

use indexmap::IndexMap;
use pycompat::json::Value;
use pycompat::pyio::{self, ReadTextError};
use pycompat::pyrepr::repr_str;
use regex::Regex;

use crate::cards::CardData;
pub use crate::jsonval::{from_serde, truthy};

/// Every key a `:behaviorKey`, a `--custom-cards` behaviour or an oracle
/// derivation may use (the Python `BEHAVIOR_KEYS` set; a test checks the
/// two lists agree).
pub const BEHAVIOR_KEYS: &[&str] = &[
    "rock_mana",
    "rock_colors",
    "ramp_lands",
    "fetch_land",
    "enters_tapped",
    "land_colors",
    "treasures_per_turn",
    "burst_treasures",
    "draw_cards",
    "draw_per_turn",
    "draw_on_attack",
    "draw_on_tokens",
    "removal",
    "removal_scope",
    "removal_exile",
    "removal_lock",
    "removal_targets",
    "mass_self",
    "counter_wipe_self",
    "igs",
    "counterspell",
    "protect",
    "wipe",
    "tokens_per_turn",
    "etb_tokens",
    "death_tokens",
    "burst_tokens",
    "populate_per_turn",
    "replicate",
    "doubler",
    "creature_token_mult",
    "esix",
    "anim",
    "reef",
    "mechanized_wincon",
    "mass_counters",
    "single_counters",
    "etb_counter_wipe",
    "etb_target_counters",
    "proliferate",
    "token_per_counter",
    "drain_on_counters",
    "chain",
    "kulrath_lock",
    "steal",
    "grave_rob",
    "yawgmoth",
    "clamp",
    "drain_own",
    "drain_any",
    "burst_drain",
    "hexproof_grant",
    "etb_removal",
    "blink_on_etb",
    "closet",
    "station",
    "rebuild",
    "tutor",
    "recursion",
    "energy_gain",
    "energy_thopter",
    "manufactor",
    "key",
    "anthem",
];

/// Hook values the engine indexes positionally (Python tuples; lists
/// on this side).
pub const TUPLE_KEYS: &[&str] = &["etb_tokens", "death_tokens", "burst_tokens"];

/// The annotation file name at a graph root.
pub const ANNOTATIONS_FILE: &str = "MagicSimulationAnnotations.ttl";

/// Whether `key` is a known behaviour key.
#[must_use]
pub fn is_behavior_key(key: &str) -> bool {
    BEHAVIOR_KEYS.contains(&key)
}

/// A card's behaviour hooks: an insertion-ordered `{key: value}` map.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Behavior(IndexMap<String, Value>);

impl Behavior {
    /// An empty map.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// The value under `key`.
    #[must_use]
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.0.get(key)
    }

    /// Whether `key` is present.
    #[must_use]
    pub fn contains(&self, key: &str) -> bool {
        self.0.contains_key(key)
    }

    /// `dict[key] = value` (keeps the position of an existing key).
    pub fn insert(&mut self, key: impl Into<String>, value: Value) {
        self.0.insert(key.into(), value);
    }

    /// `dict.setdefault(key, value)`: insert only when absent.
    pub fn set_default(&mut self, key: impl Into<String>, value: Value) {
        self.0.entry(key.into()).or_insert(value);
    }

    /// `dict.update(other)`.
    pub fn update(&mut self, other: &Behavior) {
        for (k, v) in &other.0 {
            self.0.insert(k.clone(), v.clone());
        }
    }

    /// The entries in insertion order.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &Value)> {
        self.0.iter().map(|(k, v)| (k.as_str(), v))
    }

    /// Number of keys.
    #[must_use]
    pub fn len(&self) -> usize {
        self.0.len()
    }

    /// Whether no key is set.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// The whole map as a JSON dict (for dumps).
    #[must_use]
    pub fn to_value(&self) -> Value {
        Value::Dict(self.0.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
    }

    // ---- the keys the engine reads -------------------------------------

    /// `land_colors`: the mana colours a land produces (`W`..`G`, `C`).
    #[must_use]
    pub fn land_colors(&self) -> Option<BTreeSet<String>> {
        self.get("land_colors").map(string_set)
    }

    /// `enters_tapped`.
    #[must_use]
    pub fn enters_tapped(&self) -> bool {
        self.get("enters_tapped").is_some_and(truthy)
    }

    /// `fetch_land`.
    #[must_use]
    pub fn fetch_land(&self) -> bool {
        self.get("fetch_land").is_some_and(truthy)
    }

    /// `key`: the threat weight (0 when absent or not a number).
    #[must_use]
    pub fn key(&self) -> i64 {
        self.get("key")
            .and_then(crate::jsonval::int_or_none)
            .unwrap_or(0)
    }

    /// `wipe`: whether the card is a board wipe.
    #[must_use]
    pub fn wipe(&self) -> bool {
        self.get("wipe").is_some_and(truthy)
    }

    /// `removal`: whether the card is targeted removal.
    #[must_use]
    pub fn removal(&self) -> bool {
        self.get("removal").is_some_and(truthy)
    }
}

/// A Python `set[str]` as a hook value: a sorted list of unique strings.
#[must_use]
pub fn set_value<I: IntoIterator<Item = S>, S: Into<String>>(items: I) -> Value {
    let set: BTreeSet<String> = items.into_iter().map(Into::into).collect();
    Value::List(set.into_iter().map(Value::Str).collect())
}

/// The strings of a set-valued hook.
fn string_set(value: &Value) -> BTreeSet<String> {
    match value {
        Value::List(items) => items
            .iter()
            .filter_map(|v| match v {
                Value::Str(s) => Some(s.clone()),
                _ => None,
            })
            .collect(),
        Value::Str(s) => BTreeSet::from([s.clone()]),
        _ => BTreeSet::new(),
    }
}

static THREAT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#":threatWeight "(\d+)""#).expect("valid regex"));
static HOOK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#":behaviorKey "([^"]+)" ; :behaviorValue "((?:[^"\\]|\\.)*)""#)
        .expect("valid regex")
});
static SUBJECT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^:(\w+)").expect("valid regex"));
static BLOCK_SPLIT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\n\s*\n").expect("valid regex"));

/// Errors of the annotation reader (the Python `ValueError` texts).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum AnnotationError {
    /// The file could not be read.
    #[error("{0}")]
    Read(ReadTextError),
    /// A subject is not a card individual of the card graphs.
    #[error("{ANNOTATIONS_FILE}: subject :{0} is not a card individual known to the card graphs")]
    UnknownSubject(String),
    /// A `:behaviorKey` outside [`BEHAVIOR_KEYS`].
    #[error(
        "{ANNOTATIONS_FILE}: :{subject} uses unknown behavior key {} (see BEHAVIOR_KEYS)",
        repr_str(key)
    )]
    UnknownKey {
        /// the annotated individual
        subject: String,
        /// the offending key
        key: String,
    },
    /// A `:behaviorValue` that is not valid JSON (Python lets the
    /// decoder's own error escape; this names the subject and key).
    #[error(
        "{ANNOTATIONS_FILE}: :{subject} has an unreadable :behaviorValue for {}: {reason}",
        repr_str(key)
    )]
    BadValue {
        /// the annotated individual
        subject: String,
        /// the hook key
        key: String,
        /// the JSON error
        reason: String,
    },
}

/// Decode one `:behaviorValue` literal (`\"` and `\\` are Turtle escapes).
///
/// # Errors
///
/// The JSON decoder's message.
pub fn decode_hook(raw: &str) -> Result<Value, String> {
    let unescaped = raw.replace("\\\"", "\"").replace("\\\\", "\\");
    let parsed: serde_json::Value = serde_json::from_str(&unescaped).map_err(|e| e.to_string())?;
    Ok(from_serde(&parsed))
}

/// Parse `<root>/MagicSimulationAnnotations.ttl` into `{card name: hooks}`.
///
/// `ind2name` maps card individual local names to card names (from
/// [`crate::ttl_loader::load_graph_cards`]); a subject that does not
/// resolve is an error, keeping the annotation graph and the card graphs
/// consistent. A missing file yields an empty map.
///
/// # Errors
///
/// See [`AnnotationError`].
pub fn load_annotations(
    root: &Path,
    ind2name: &IndexMap<String, String>,
) -> Result<IndexMap<String, Behavior>, AnnotationError> {
    let path = root.join(ANNOTATIONS_FILE);
    let mut hooks_by_name: IndexMap<String, Behavior> = IndexMap::new();
    if !path.exists() {
        return Ok(hooks_by_name);
    }
    let text = pyio::read_text(&path).map_err(AnnotationError::Read)?;
    for raw_block in BLOCK_SPLIT.split(&text) {
        let block = pycompat::pystr::strip(raw_block);
        if !block.starts_with(':') || block.contains("owl:Ontology") {
            continue;
        }
        let Some(subject) = SUBJECT_RE.captures(block).map(|c| c[1].to_owned()) else {
            continue;
        };
        let Some(name) = ind2name.get(&subject) else {
            return Err(AnnotationError::UnknownSubject(subject));
        };
        let hooks = hooks_by_name.entry(name.clone()).or_default();
        if let Some(tw) = THREAT_RE.captures(block) {
            hooks.insert("key", Value::Int(tw[1].parse().unwrap_or(0)));
        }
        for caps in HOOK_RE.captures_iter(block) {
            let key = &caps[1];
            if !is_behavior_key(key) {
                return Err(AnnotationError::UnknownKey {
                    subject,
                    key: key.to_owned(),
                });
            }
            let value = decode_hook(&caps[2]).map_err(|reason| AnnotationError::BadValue {
                subject: subject.clone(),
                key: key.to_owned(),
                reason,
            })?;
            hooks.insert(key, value);
        }
    }
    Ok(hooks_by_name)
}

/// Merge the hand-authored hooks of a card over its derived behaviour.
///
/// Hooks are looked up by the full name, then by the front face of a
/// `Front // Back` name.
pub fn apply_behaviors(card: &mut CardData, hooks_by_name: &IndexMap<String, Behavior>) {
    let mut hooks = hooks_by_name.get(&card.name).filter(|h| !h.is_empty());
    if hooks.is_none()
        && let Some((front, _)) = card.name.split_once(" // ")
    {
        hooks = hooks_by_name.get(front);
    }
    if let Some(hooks) = hooks {
        card.behavior.update(hooks);
    }
}

#[cfg(test)]
mod tests {
    use super::{Behavior, decode_hook, set_value, truthy};
    use pycompat::json::Value;

    #[test]
    fn setdefault_keeps_existing_and_order() {
        let mut b = Behavior::new();
        b.insert("land_colors", set_value(["R", "B"]));
        b.set_default("land_colors", set_value(["G"]));
        b.set_default("enters_tapped", Value::Bool(true));
        assert_eq!(
            b.land_colors().unwrap().into_iter().collect::<Vec<_>>(),
            ["B", "R"]
        );
        assert_eq!(
            b.iter().map(|(k, _)| k).collect::<Vec<_>>(),
            ["land_colors", "enters_tapped"]
        );
        assert!(b.enters_tapped());
    }

    #[test]
    fn hook_values_decode_with_turtle_escapes() {
        let v = decode_hook(r#"{\"style\": \"damage\", \"dmg\": 3}"#).unwrap();
        assert_eq!(v.get("dmg"), Some(&Value::Int(3)));
        assert!(truthy(&v));
        assert!(!truthy(&Value::Int(0)));
    }
}
