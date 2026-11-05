//! Card characteristics from the TTL knowledge graph.
//!
//! Reads `sets/*.ttl` plus optional extra card graphs with regex-based
//! extraction (no RDF library) of the fields the simulator needs: name,
//! mana cost, mana value, types, subtypes, colour identity, power /
//! toughness, loyalty, oracle text and the graph-authored land facts.
//! Results are indexed by full card name and, for double-faced cards, by
//! the front-face name as well.
//!
//! Multi-valued predicates are read as Turtle object lists (see
//! [`objects`]). Anything that changes how the generator lays out those
//! predicates has to be mirrored here **and** in the Python
//! `mtgcards/ttl_loader.py`, because both parse text rather than RDF.
//!
//! The regexes are the Python ones verbatim; the only addition (Rust-only
//! hardening) is [`LoadReport`], which records card blocks whose
//! individual line could not be read, where the Python reader silently
//! leaves the individual out of `ind2name`.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use indexmap::IndexMap;
use pycompat::json::Value;
use pycompat::pyio::{self, ReadTextError};
use regex::Regex;

use crate::behavior::set_value;
use crate::cards::CardData;
use crate::index::CardIndex;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static NAME_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":cardName "([^"]+)""#));
static COST_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":manaCost "([^"]*)""#));
static MV_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":manaValue "(\d+)""#));
static POWER_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":powerValue "(-?\d+)""#));
static TOUGH_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":toughnessValue "(-?\d+)""#));
static LOYALTY_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":loyalty "(\d+)""#));
static TAPPED_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":entersTapped "true""#));
static FETCHLAND_RE: LazyLock<Regex> = LazyLock::new(|| re(r#":isFetchLand "true""#));
static ORACLE_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?s):oracleText\s+(?:"""(.*?)"""|"([^"]*)")"#));
static INDIVIDUAL_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^:(\w+) rdf:type"));
static BLOCK_SPLIT: LazyLock<Regex> = LazyLock::new(|| re(r"\n\s*\n"));

/// The object run of a multi-valued predicate: `:A ,\n  :B ;` up to the
/// `;` or `.` that ends the predicate.
const OBJECT_RUN: &str = r"\s+((?::\w+\s*,\s*)*:\w+)\s*[;.]";

static TYPE_RE: LazyLock<Regex> = LazyLock::new(|| re(&format!(":hasCardType{OBJECT_RUN}")));
static SUPER_RE: LazyLock<Regex> = LazyLock::new(|| re(&format!(":hasSuperType{OBJECT_RUN}")));
static SUB_RE: LazyLock<Regex> = LazyLock::new(|| re(&format!(":hasSubType{OBJECT_RUN}")));
static CI_RE: LazyLock<Regex> = LazyLock::new(|| re(&format!(":hasColorIdentity{OBJECT_RUN}")));
static PRODUCES_RE: LazyLock<Regex> = LazyLock::new(|| re(&format!(":producesMana{OBJECT_RUN}")));

/// Colour individual local name -> colour letter.
#[must_use]
pub fn color_letter(individual: &str) -> Option<&'static str> {
    match individual {
        "White" => Some("W"),
        "Blue" => Some("U"),
        "Black" => Some("B"),
        "Red" => Some("R"),
        "Green" => Some("G"),
        _ => None,
    }
}

/// Subtype words a concatenated individual (`:SpacecraftPlanar`) implies.
const SUBTYPE_WORDS: &[&str] = &[
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    "Spacecraft",
    "Saga",
    "Equipment",
    "Vehicle",
    "Gate",
    "Treasure",
    "Food",
    "Clue",
];

/// Every object local name asserted for a list predicate in `block`:
/// both the object-list form and a predicate repeated across lines.
#[must_use]
pub fn objects(regex: &Regex, block: &str) -> Vec<String> {
    let mut names = Vec::new();
    for caps in regex.captures_iter(block) {
        for part in caps[1].split(',') {
            let name = part.trim().trim_start_matches(':');
            if !name.is_empty() {
                names.push(name.to_owned());
            }
        }
    }
    names
}

/// Per-individual blocks of a Turtle file (blank-line separated) that
/// carry a `:cardName`.
pub fn card_blocks(text: &str) -> impl Iterator<Item = &str> {
    BLOCK_SPLIT
        .split(text)
        .filter(|block| block.contains(":cardName"))
}

fn int_field(regex: &Regex, block: &str) -> Option<i64> {
    regex.captures(block).and_then(|c| c[1].parse().ok())
}

/// One card's printed characteristics from its TTL block.
#[must_use]
pub fn parse_card(name: &str, block: &str) -> CardData {
    let mut card = CardData::new(name, "graph");
    card.mana_cost = COST_RE
        .captures(block)
        .map(|c| c[1].to_owned())
        .unwrap_or_default();
    card.mv = int_field(&MV_RE, block).unwrap_or(0);
    card.power = int_field(&POWER_RE, block);
    card.toughness = int_field(&TOUGH_RE, block);
    card.types = objects(&TYPE_RE, block).into_iter().collect();
    card.supertypes = objects(&SUPER_RE, block).into_iter().collect();
    card.loyalty = int_field(&LOYALTY_RE, block);
    card.subtypes = objects(&SUB_RE, block).into_iter().collect();
    // normalise concatenated subtype individuals (:SpacecraftPlanar)
    let extra: BTreeSet<String> = card
        .subtypes
        .iter()
        .flat_map(|s| {
            SUBTYPE_WORDS
                .iter()
                .filter(move |word| s != *word && s.starts_with(*word))
                .map(|word| (*word).to_owned())
        })
        .collect();
    card.subtypes.extend(extra);
    card.color_identity = objects(&CI_RE, block)
        .iter()
        .filter_map(|c| color_letter(c))
        .map(str::to_owned)
        .collect();
    if let Some(caps) = ORACLE_RE.captures(block) {
        card.oracle = caps
            .get(1)
            .or_else(|| caps.get(2))
            .map(|m| m.as_str().to_owned())
            .unwrap_or_default();
    }
    apply_land_facts(&mut card, block);
    card
}

/// Graph-authored land facts: `:producesMana`, `:entersTapped`,
/// `:isFetchLand` (authoritative for lands; the oracle derivation is a
/// fallback).
fn apply_land_facts(card: &mut CardData, block: &str) {
    if !card.is_land() {
        return;
    }
    let produced = objects(&PRODUCES_RE, block);
    if !produced.is_empty() {
        card.behavior.insert(
            "land_colors",
            set_value(
                produced
                    .iter()
                    .map(|c| color_letter(c).unwrap_or("C").to_owned()),
            ),
        );
    }
    if TAPPED_RE.is_match(block) {
        card.behavior.insert("enters_tapped", Value::Bool(true));
    }
    if FETCHLAND_RE.is_match(block) {
        card.behavior.insert("fetch_land", Value::Bool(true));
    }
}

/// What a graph load found: the cards and the issues the Python reader
/// would have passed over silently.
#[derive(Clone, Debug, Default)]
pub struct LoadReport {
    /// card blocks whose subject line is not `:Local rdf:type ...` and
    /// therefore have no `ind2name` entry: `(file, card name)`
    pub unindexed_individuals: Vec<(PathBuf, String)>,
}

/// Errors of the graph loader.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum LoadError {
    /// A TTL file could not be read (`str(OSError)` /
    /// `str(UnicodeDecodeError)` text).
    #[error("{0}")]
    Read(ReadTextError),
}

/// The `*.ttl` files of `sets_dir` in sorted order (Python
/// `sorted(Path(sets_dir).glob("*.ttl"))`), then the existing
/// `extra_files`.
fn graph_files(sets_dir: &Path, extra_files: &[PathBuf]) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = match std::fs::read_dir(sets_dir) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|ext| ext == "ttl"))
            .collect(),
        Err(_) => Vec::new(),
    };
    // Python sorts Path objects by their parts; within one directory that
    // is the byte order of the file names
    files.sort_by(|a, b| a.as_os_str().cmp(b.as_os_str()));
    files.extend(extra_files.iter().filter(|f| f.exists()).cloned());
    files
}

/// Load every card of the graph into a [`CardIndex`].
///
/// Reads every `*.ttl` of `sets_dir` in sorted order plus any existing
/// `extra_files`. Inventoried printings win: the first definition of a
/// card name is kept. `ind2name` is filled with `{individual local name:
/// card name}` for every card individual encountered (all printings).
/// Double-faced cards are also indexed under their front-face name — the
/// same record, as in Python.
///
/// # Errors
///
/// A file that cannot be read or decoded.
pub fn load_graph_cards(
    sets_dir: &Path,
    extra_files: &[PathBuf],
    ind2name: &mut IndexMap<String, String>,
    report: &mut LoadReport,
) -> Result<CardIndex, LoadError> {
    let mut index = CardIndex::new();
    for file in graph_files(sets_dir, extra_files) {
        let text = pyio::read_text(&file).map_err(LoadError::Read)?;
        for block in card_blocks(&text) {
            let Some(name) = NAME_RE.captures(block).map(|c| c[1].to_owned()) else {
                continue;
            };
            match INDIVIDUAL_RE.captures(block.trim_start()) {
                Some(caps) => {
                    ind2name.insert(caps[1].to_owned(), name.clone());
                }
                None => report
                    .unindexed_individuals
                    .push((file.clone(), name.clone())),
            }
            if index.contains(&name) {
                continue;
            }
            let card = parse_card(&name, block);
            let front = name
                .split(" // ")
                .next()
                .filter(|f| *f != name)
                .map(str::to_owned);
            let id = index.insert(name, card);
            if let Some(front) = front {
                // front face of double-faced cards: the same record
                index.alias_if_absent(front, id);
            }
        }
    }
    Ok(index)
}

#[cfg(test)]
mod tests {
    use super::{SUB_RE, card_blocks, objects, parse_card};

    const BLOCK: &str = r#":Tester rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Tester" ;
    :manaCost "{1}{U}" ;
    :manaValue "2"^^xsd:nonNegativeInteger ;
    :hasCardType :Artifact ,
                 :Creature ;
    :hasSubType :Human ,
                :Artificer ;
    :hasColorIdentity :Blue ;
    :powerValue "2"^^xsd:integer ;
    :toughnessValue "3"^^xsd:integer ;
    :oracleText """Flying
Second line."""@en ."#;

    #[test]
    fn object_lists_and_fields() {
        let card = parse_card("Tester", BLOCK);
        assert_eq!(
            card.types.iter().collect::<Vec<_>>(),
            ["Artifact", "Creature"]
        );
        assert_eq!(objects(&SUB_RE, BLOCK), ["Human", "Artificer"]);
        assert_eq!(card.color_identity.iter().collect::<Vec<_>>(), ["U"]);
        assert_eq!((card.power, card.toughness, card.mv), (Some(2), Some(3), 2));
        assert_eq!(card.oracle, "Flying\nSecond line.");
    }

    #[test]
    fn blocks_need_a_card_name() {
        let text = format!("@prefix : <urn:x#> .\n\n{BLOCK}\n\n:Other rdf:type :Thing .\n");
        assert_eq!(card_blocks(&text).count(), 1);
    }
}
