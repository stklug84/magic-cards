//! The Rust `BEHAVIOR_KEYS` list and the `BEHAVIOR_KEYS` set of the
//! Python graph tooling (`scripts/mtgcards/behaviors.py`) must stay
//! identical: the simulator loads the annotation file and `--custom-cards`
//! JSON against the former, the graph validators (`mtgvalidate`) check
//! the annotation file against the latter, so a key one side accepts and
//! the other rejects would make a graph validate and then fail to load.

mod common;

use std::collections::BTreeSet;

use mtgcards::behavior::{BEHAVIOR_KEYS, TUPLE_KEYS, is_behavior_key};

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
