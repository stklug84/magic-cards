//! Card data model and oracle-text derivation.
//!
//! [`CardData`] carries printed characteristics (cost, types, P/T, oracle
//! text). [`derive_from_oracle`] extracts machine-usable features from the
//! oracle text: keywords, land mana colours, enters-tapped, token
//! creation, draw counts, removal/counterspell/wipe classification,
//! anthems, proliferate/populate — every regex of the Python
//! `mtgcards.cards` module, in the same order, with the same `setdefault`
//! merge (an existing behaviour entry always wins).
//!
//! Python slices the oracle text by code points (`trig[-120:]`); the
//! look-backs here count `char`s for the same reason.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use pycompat::json::Value;
use regex::Regex;

use crate::behavior::{Behavior, set_value};

/// The five colours, in WUBRG order.
pub const COLORS: &str = "WUBRG";

/// Keywords recognised in oracle text (line-leading or comma-listed).
pub const KEYWORDS: &[&str] = &[
    "flying",
    "trample",
    "deathtouch",
    "lifelink",
    "vigilance",
    "haste",
    "menace",
    "reach",
    "defender",
    "first strike",
    "double strike",
    "hexproof",
    "indestructible",
    "wither",
    "infect",
    "myriad",
    "populate",
    "proliferate",
    "flash",
];

/// Count words of oracle text (`"x"` counts as 2, as in the Python code).
#[must_use]
pub fn num(word: &str) -> i64 {
    let w = word.trim().to_lowercase();
    if !w.is_empty() && w.chars().all(|c| c.is_ascii_digit()) {
        return w.parse().unwrap_or(1);
    }
    match w.as_str() {
        "two" | "x" => 2,
        "three" => 3,
        "four" => 4,
        "five" => 5,
        "six" => 6,
        "seven" => 7,
        "eight" => 8,
        "nine" => 9,
        "ten" => 10,
        // "a", "an", "one" and anything unknown
        _ => 1,
    }
}

/// Basic land type -> the colour of mana it produces.
pub const BASIC_LAND_COLORS: &[(&str, &str)] = &[
    ("Plains", "W"),
    ("Island", "U"),
    ("Swamp", "B"),
    ("Mountain", "R"),
    ("Forest", "G"),
];

/// Printed characteristics plus derived/authored behaviour hooks.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CardData {
    /// the full card name (`Front // Back` for double-faced cards)
    pub name: String,
    /// the printed mana cost, e.g. `{2}{G}{G}` (empty for lands)
    pub mana_cost: String,
    /// the mana value
    pub mv: i64,
    /// card types
    pub types: BTreeSet<String>,
    /// supertypes
    pub supertypes: BTreeSet<String>,
    /// subtypes (ontology local names, e.g. `TimeLord`)
    pub subtypes: BTreeSet<String>,
    /// printed loyalty
    pub loyalty: Option<i64>,
    /// colour identity letters
    pub color_identity: BTreeSet<String>,
    /// printed power (None for `*` or no power)
    pub power: Option<i64>,
    /// printed toughness
    pub toughness: Option<i64>,
    /// oracle text (faces joined with `\n//\n`)
    pub oracle: String,
    /// keywords derived from the oracle text
    pub keywords: BTreeSet<String>,
    /// merged behaviour hooks
    pub behavior: Behavior,
    /// where the data came from: `graph`, `custom`, `stub`, `scryfall`
    pub source: String,
}

impl CardData {
    /// A card with only a name and a source; everything else empty.
    #[must_use]
    pub fn new(name: &str, source: &str) -> Self {
        Self {
            name: name.to_owned(),
            source: source.to_owned(),
            ..Self::default()
        }
    }

    /// Whether the type line contains Land.
    #[must_use]
    pub fn is_land(&self) -> bool {
        self.types.contains("Land")
    }

    /// Whether the type line contains Creature.
    #[must_use]
    pub fn is_creature(&self) -> bool {
        self.types.contains("Creature")
    }

    /// Whether the type line contains Artifact.
    #[must_use]
    pub fn is_artifact(&self) -> bool {
        self.types.contains("Artifact")
    }

    /// Whether the colour identity spans two or more colours.
    #[must_use]
    pub fn is_multicolored(&self) -> bool {
        self.color_identity.len() >= 2
    }

    /// The card as a JSON dict (sets as sorted lists), for dumps.
    #[must_use]
    pub fn to_value(&self) -> Value {
        let set = |s: &BTreeSet<String>| Value::List(s.iter().cloned().map(Value::Str).collect());
        let opt = |o: Option<i64>| o.map_or(Value::Null, |i| Value::Int(i128::from(i)));
        Value::dict([
            ("name", Value::from(self.name.as_str())),
            ("mana_cost", Value::from(self.mana_cost.as_str())),
            ("mv", Value::Int(i128::from(self.mv))),
            ("types", set(&self.types)),
            ("supertypes", set(&self.supertypes)),
            ("subtypes", set(&self.subtypes)),
            ("loyalty", opt(self.loyalty)),
            ("color_identity", set(&self.color_identity)),
            ("power", opt(self.power)),
            ("toughness", opt(self.toughness)),
            ("oracle", Value::from(self.oracle.as_str())),
            ("keywords", set(&self.keywords)),
            ("behavior", self.behavior.to_value()),
            ("source", Value::from(self.source.as_str())),
        ])
    }
}

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static KEYWORD_RES: LazyLock<Vec<(&'static str, Regex)>> = LazyLock::new(|| {
    KEYWORDS
        .iter()
        .map(|kw| {
            (
                *kw,
                re(&format!(r"(?:^|\n|, |; )({kw})(?:$|,|;| \(|\n| from)")),
            )
        })
        .collect()
});
static ADD_SYMBOL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"add [^.\n]*?\{([wubrg])\}"));
static OR_SYMBOL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\{([wubrg])\} or \{([wubrg])\}"));
static ROCK_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"\{t\}: add (\{[cwubrg]\}(?:\{[cwubrg]\})*)"));
static SYMBOL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\{([cwubrg])\}"));
static RAMP_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"search your librar(?:y|ies) for (?:up to )?(a|an|one|two|three)[^.]*land")
});
static DRAW_RE: LazyLock<Regex> = LazyLock::new(|| re(r"draw (a|an|one|two|three|four|x) cards?"));
static COUNTER_RE: LazyLock<Regex> = LazyLock::new(|| re(r"counter target [^.]*spell"));
static REMOVAL_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(destroy|exile) target (creature|permanent|artifact|enchantment|nonland permanent)")
});
static WIPE_DESTROY_RE: LazyLock<Regex> = LazyLock::new(|| re(r"destroy all creatures"));
static WIPE_DAMAGE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"deals (\d+|x) damage to each creature"));
static TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"create (a|an|one|two|three|four|five|x|\d+)(?: tapped)?[^.]*?(\d+)/(\d+)[^.]*?((?:artifact )?)creature tokens?",
    )
});
static TREASURE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"create (a|two|three|x)?[^.]{0,40}treasure token"));
static ANTHEM_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"(creatures?|creature tokens?) you control get \+(\d+)/\+(\d+)"));
static ENERGY_RE: LazyLock<Regex> = LazyLock::new(|| re(r"you get (\{e\})+"));

/// The last `n` characters of `s` (Python `s[-n:]`, by code point).
fn tail_chars(s: &str, n: usize) -> String {
    let count = s.chars().count();
    s.chars().skip(count.saturating_sub(n)).collect()
}

fn derive_keywords(card: &mut CardData, low: &str) {
    for (kw, regex) in KEYWORD_RES.iter() {
        if regex.is_match(low) {
            card.keywords.insert((*kw).to_owned());
        }
    }
}

fn add_to_set(b: &mut Behavior, key: &str, items: impl IntoIterator<Item = String>) {
    let mut set: BTreeSet<String> = match b.get(key) {
        Some(Value::List(existing)) => existing
            .iter()
            .filter_map(|v| {
                if let Value::Str(s) = v {
                    Some(s.clone())
                } else {
                    None
                }
            })
            .collect(),
        _ => BTreeSet::new(),
    };
    set.extend(items);
    b.insert(key, set_value(set));
}

fn derive_land(card: &CardData, low: &str, b: &mut Behavior) {
    // basic land types imply the matching mana ability (even off lands,
    // as in the Python code)
    for (sub, c) in BASIC_LAND_COLORS {
        if card.subtypes.contains(*sub) {
            add_to_set(b, "land_colors", [(*c).to_owned()]);
        }
    }
    if !card.is_land() {
        return;
    }
    if low.contains("enters the battlefield tapped") || low.contains("enters tapped") {
        b.insert("enters_tapped", Value::Bool(true));
    }
    if low.contains("any color") || low.contains("any one color") {
        b.insert(
            "land_colors",
            set_value(COLORS.chars().map(|c| c.to_string())),
        );
    } else {
        let mut colors: BTreeSet<String> = ADD_SYMBOL_RE
            .captures_iter(low)
            .map(|c| c[1].to_uppercase())
            .collect();
        for caps in OR_SYMBOL_RE.captures_iter(low) {
            colors.insert(caps[1].to_uppercase());
            colors.insert(caps[2].to_uppercase());
        }
        if !colors.is_empty() {
            add_to_set(b, "land_colors", colors);
        }
    }
    if low.contains("search your library for a basic land")
        || low.contains("search your library for a plains")
    {
        b.insert("fetch_land", Value::Bool(true));
    }
    let has_colors = b.get("land_colors").is_some_and(crate::behavior::truthy);
    let fetch = b.get("fetch_land").is_some_and(crate::behavior::truthy);
    if !has_colors && !fetch {
        b.insert("land_colors", set_value(["C"]));
    }
}

fn derive_mana_producers(card: &CardData, low: &str, b: &mut Behavior) {
    if let Some(m) = ROCK_RE.captures(low)
        && !card.is_land()
    {
        let syms: Vec<String> = SYMBOL_RE
            .captures_iter(&m[1])
            .map(|c| c[1].to_owned())
            .collect();
        b.insert(
            "rock_mana",
            Value::Int(i128::try_from(syms.len()).unwrap_or(0)),
        );
        let colors: BTreeSet<String> = syms
            .iter()
            .filter(|s| *s != "c")
            .map(|s| s.to_uppercase())
            .collect();
        b.insert(
            "rock_colors",
            if colors.is_empty() {
                set_value(["C"])
            } else {
                set_value(colors)
            },
        );
    }
    if !card.is_land()
        && (low.contains("add one mana of any color") || low.contains("mana of any color"))
    {
        b.set_default("rock_mana", Value::Int(1));
        b.insert(
            "rock_colors",
            set_value(COLORS.chars().map(|c| c.to_string())),
        );
    }
}

fn derive_ramp(card: &CardData, low: &str, b: &mut Behavior) {
    if !card.types.contains("Sorcery") && !card.types.contains("Instant") {
        return;
    }
    if let Some(m) = RAMP_RE.captures(low) {
        b.insert("ramp_lands", Value::Int(i128::from(num(&m[1]))));
    }
}

fn derive_draw(card: &CardData, low: &str, b: &mut Behavior) {
    if let Some(m) = DRAW_RE.captures(low)
        && !card.types.contains("Creature")
        && !card.types.contains("Land")
    {
        b.insert("draw_cards", Value::Int(i128::from(num(&m[1]))));
    }
}

fn derive_interaction(card: &CardData, low: &str, b: &mut Behavior) {
    if COUNTER_RE.is_match(low) {
        b.insert("counterspell", Value::Bool(true));
    }
    if !card.types.contains("Instant")
        && !card.types.contains("Sorcery")
        && !card.types.contains("Enchantment")
    {
        return;
    }
    if let Some(m) = REMOVAL_RE.captures(low) {
        // "all" in the 20 code points before the match means a wipe
        let before = &low[..m.get(0).map_or(0, |g| g.start())];
        if !tail_chars(before, 20).contains("all") {
            b.insert("removal", Value::Bool(true));
            b.insert("removal_exile", Value::Bool(&m[1] == "exile"));
            let scope = match &m[2] {
                "creature" => "creature",
                "artifact" | "enchantment" => "art_ench",
                _ => "any",
            };
            b.insert("removal_scope", Value::from(scope));
        }
    }
}

fn derive_wipes(low: &str, b: &mut Behavior) {
    if WIPE_DESTROY_RE.is_match(low) {
        b.insert("wipe", Value::dict([("style", Value::from("destroy"))]));
    }
    if let Some(m) = WIPE_DAMAGE_RE.captures(low) {
        let dmg = match num(&m[1]) {
            0 => 5,
            n => n,
        };
        b.insert(
            "wipe",
            Value::dict([
                ("style", Value::from("damage")),
                ("dmg", Value::Int(i128::from(dmg))),
            ]),
        );
    }
}

fn derive_tokens(card: &CardData, text: &str, low: &str, b: &mut Behavior) {
    for m in TOKEN_RE.captures_iter(low) {
        let whole = m.get(0).expect("match 0");
        let n = num(&m[1]);
        let p: i64 = m[2].parse().unwrap_or(0);
        let t: i64 = m[3].parse().unwrap_or(0);
        let art = m[4].contains("artifact") || whole.as_str().contains("thopter");
        // Python: text[: m.start()].lower() with m.start() a code-point
        // index into `low`; `low` and `text` share code-point positions
        let prefix_chars = low[..whole.start()].chars().count();
        let trig: String = text
            .chars()
            .take(prefix_chars)
            .collect::<String>()
            .to_lowercase();
        let entry = Value::List(vec![
            Value::Int(i128::from(n)),
            Value::Int(i128::from(p)),
            Value::Int(i128::from(t)),
            Value::Bool(art),
        ]);
        let last120 = tail_chars(&trig, 120);
        if last120.contains("when") && last120.contains("enters") {
            b.set_default("etb_tokens", entry);
        } else if tail_chars(&trig, 80).contains("dies") {
            b.set_default("death_tokens", entry);
        } else if last120.contains("at the beginning") {
            b.set_default("tokens_per_turn", Value::Int(i128::from(n)));
        } else if card.types.contains("Instant") || card.types.contains("Sorcery") {
            b.set_default("burst_tokens", entry);
        }
    }
    if TREASURE_RE.is_match(low) {
        if card.types.contains("Instant") || card.types.contains("Sorcery") {
            b.set_default("burst_treasures", Value::Int(2));
        } else {
            b.set_default("treasures_per_turn", Value::Int(1));
        }
    }
}

fn derive_anthem(card: &CardData, low: &str, b: &mut Behavior) {
    if let Some(m) = ANTHEM_RE.captures(low)
        && card.types.contains("Enchantment")
    {
        b.insert(
            "anthem",
            Value::dict([
                ("boost", Value::Int(m[3].parse::<i128>().unwrap_or(0))),
                ("tokens_only", Value::Bool(m[1].contains("token"))),
                ("art_only", Value::Bool(low.contains("artifact creature"))),
            ]),
        );
    }
}

fn derive_counter_systems(card: &CardData, low: &str, b: &mut Behavior) {
    if low.contains("proliferate") {
        let n = if low.contains("twice") {
            low.matches("proliferate twice").count() + 1
        } else {
            1
        };
        b.insert("proliferate", Value::Int(i128::try_from(n).unwrap_or(1)));
    }
    if low.contains("populate") && !card.types.contains("Creature") {
        b.set_default("populate_per_turn", Value::Int(1));
    }
    if low.contains("{e}") {
        let count = if ENERGY_RE.is_match(low) {
            low.matches("{e}").count()
        } else {
            1
        };
        b.insert(
            "energy_gain",
            Value::Int(i128::try_from(count).unwrap_or(1)),
        );
    }
}

/// Populate `card.keywords` and the derived behaviour keys from the
/// oracle text. Derived keys never overwrite existing entries.
pub fn derive_from_oracle(card: &mut CardData) {
    let text = card.oracle.clone();
    let low = text.to_lowercase();
    let mut b = Behavior::new();
    derive_keywords(card, &low);
    derive_land(card, &low, &mut b);
    derive_mana_producers(card, &low, &mut b);
    derive_ramp(card, &low, &mut b);
    derive_draw(card, &low, &mut b);
    derive_interaction(card, &low, &mut b);
    derive_wipes(&low, &mut b);
    derive_tokens(card, &text, &low, &mut b);
    derive_anthem(card, &low, &mut b);
    derive_counter_systems(card, &low, &mut b);
    for (k, v) in b.iter() {
        card.behavior.set_default(k, v.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::{CardData, derive_from_oracle, num};
    use pycompat::json::Value;

    #[test]
    fn count_words() {
        assert_eq!(num("two"), 2);
        assert_eq!(num("X"), 2);
        assert_eq!(num("7"), 7);
        assert_eq!(num("many"), 1);
    }

    #[test]
    fn dual_land_colours_and_tapped() {
        let mut card = CardData::new("Canyon Slough", "graph");
        card.types.insert("Land".into());
        card.oracle = "({T}: Add {B} or {R}.)\nThis land enters tapped.\nCycling {2}".into();
        derive_from_oracle(&mut card);
        let colors = card.behavior.land_colors().unwrap();
        assert_eq!(colors.into_iter().collect::<Vec<_>>(), ["B", "R"]);
        assert!(card.behavior.enters_tapped());
    }

    #[test]
    fn removal_and_wipe() {
        let mut card = CardData::new("Spell", "graph");
        card.types.insert("Instant".into());
        card.oracle = "Destroy target creature. Draw a card.".into();
        derive_from_oracle(&mut card);
        assert!(card.behavior.removal());
        assert_eq!(
            card.behavior.get("removal_scope"),
            Some(&Value::from("creature"))
        );
        assert_eq!(card.behavior.get("draw_cards"), Some(&Value::Int(1)));

        let mut wipe = CardData::new("Wipe", "graph");
        wipe.types.insert("Sorcery".into());
        wipe.oracle = "Destroy all creatures.".into();
        derive_from_oracle(&mut wipe);
        assert!(wipe.behavior.wipe());
        assert!(!wipe.behavior.removal());
    }

    #[test]
    fn existing_behaviour_wins() {
        let mut card = CardData::new("Forest", "graph");
        card.types.insert("Land".into());
        card.subtypes.insert("Forest".into());
        card.behavior.insert("land_colors", super::set_value(["G"]));
        card.oracle = "({T}: Add {G}.)".into();
        derive_from_oracle(&mut card);
        assert_eq!(card.behavior.iter().count(), 1);
    }
}
