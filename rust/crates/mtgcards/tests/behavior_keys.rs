//! The Rust `BEHAVIOR_KEYS` list and the Python `BEHAVIOR_KEYS` set must
//! stay identical while both implementations exist: the annotation file
//! and `--custom-cards` JSON are validated against them, so a key one
//! side accepts and the other rejects would make the same graph load on
//! one simulator and fail on the other.

mod common;

use std::collections::BTreeSet;

use mtgcards::behavior::{BEHAVIOR_KEYS, TUPLE_KEYS, is_behavior_key};
use mtgcards::cards::KEYWORDS;

/// The `NAME = {...}` set literal of a Python module, as its string items.
fn python_set_literal(source: &str, name: &str) -> BTreeSet<String> {
    let start = source
        .find(&format!("\n{name} = {{"))
        .unwrap_or_else(|| panic!("{name} literal"));
    let body = &source[start..];
    // the literals hold plain strings, so the first '}' closes the set
    let end = body.find('}').expect("closing brace");
    let item = regex::Regex::new(r#""([A-Za-z_][A-Za-z0-9_]*)""#).expect("regex");
    item.captures_iter(&body[..end])
        .map(|c| c[1].to_owned())
        .collect()
}

#[test]
fn behavior_keys_match_the_python_set() {
    let source = std::fs::read_to_string(common::repo_root().join("scripts/mtgcards/behaviors.py"))
        .expect("behaviors.py");
    let python = python_set_literal(&source, "BEHAVIOR_KEYS");
    let rust: BTreeSet<String> = BEHAVIOR_KEYS.iter().map(|k| (*k).to_owned()).collect();
    assert_eq!(rust, python);
    assert_eq!(
        BEHAVIOR_KEYS.len(),
        rust.len(),
        "duplicate key in BEHAVIOR_KEYS"
    );
    assert_eq!(
        python_set_literal(&source, "_TUPLE_KEYS"),
        TUPLE_KEYS.iter().map(|k| (*k).to_owned()).collect()
    );
    assert!(is_behavior_key("key"));
    assert!(!is_behavior_key("bogus"));
}

#[test]
fn oracle_keywords_match_the_python_tuple() {
    let source = std::fs::read_to_string(common::repo_root().join("scripts/mtgcards/cards.py"))
        .expect("cards.py");
    let start = source.find("\nKEYWORDS = (").expect("KEYWORDS literal");
    let body = &source[start..];
    let end = body.find("\n)").expect("closing paren");
    let item = regex::Regex::new(r#""([a-z ]+)""#).expect("regex");
    let python: Vec<String> = item
        .captures_iter(&body[..end])
        .map(|c| c[1].to_owned())
        .collect();
    let rust: Vec<String> = KEYWORDS.iter().map(|k| (*k).to_owned()).collect();
    assert_eq!(rust, python);
}
