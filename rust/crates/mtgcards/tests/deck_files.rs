//! Deck file parsing: txt decklists (fix C4) and `.ttl` deck instance
//! graphs, plus the `load_deck` dispatch (ports of the Python
//! `test_deck_txt.py` and `test_deck_ttl.py`).

mod common;

use indexmap::IndexMap;
use mtgcards::deck::{DeckError, load_deck};
use mtgcards::deck_ttl::load_deck_ttl;

fn ind2name() -> IndexMap<String, String> {
    [
        ("AuntieOolCursewretch", "Auntie Ool, Cursewretch"),
        ("SolRingAetherdriftCommander57", "Sol Ring"),
        ("SwampDoctorWhoCommander200", "Swamp"),
        ("ArcaneSignetAetherdriftCommander52", "Arcane Signet"),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v.to_owned()))
    .collect()
}

const DECK_WITH_ENTRIES: &str = r#"@prefix : <urn:test#> .

:TestDeck rdf:type :CommanderDeck ;
    :hasCard :AuntieOolCursewretch , :SolRingAetherdriftCommander57 ,
        :SwampDoctorWhoCommander200 ;
    :hasDeckEntry :E1 , :E2 , :E3 .

:AuntieOolCursewretch :isCommanderOf :TestDeck .

:E1 rdf:type :DeckEntry ;
    :entryCard :AuntieOolCursewretch ;
    :quantity "1"^^xsd:positiveInteger .

:E2 rdf:type :DeckEntry ;
    :entryCard :SolRingAetherdriftCommander57 ;
    :quantity "1"^^xsd:positiveInteger .

:E3 rdf:type :DeckEntry ;
    :entryCard :SwampDoctorWhoCommander200 ;
    :quantity "3"^^xsd:positiveInteger .
"#;

const DECK_HASCARD_ONLY: &str = r#":TestDeck rdf:type :CommanderDeck ;
    :hasCard
  :SolRingAetherdriftCommander57 ,
  :SwampDoctorWhoCommander200 ;
    rdfs:label "x" .

:SolRingAetherdriftCommander57 :isCommanderOf :TestDeck .
"#;

fn strings(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

// ---- TestCountForms: every count spelling exported lists use

#[test]
fn plain_and_x_suffixed_counts() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "// Commander\n1x Pia Nalaar, Chief Mechanic\n2 Sol Ring\n4X Forest\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(
        deck.commander.as_deref(),
        Some("Pia Nalaar, Chief Mechanic")
    );
    assert_eq!(
        deck.cards,
        strings(&[
            "Sol Ring", "Sol Ring", "Forest", "Forest", "Forest", "Forest"
        ])
    );
    assert_eq!(deck.skipped, Vec::new());
    assert_eq!(deck.name, "deck");
    assert_eq!(deck.fmt, "txt");
}

#[test]
fn unparsable_lines_are_recorded_not_dropped() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "// Commander\n1 Pia Nalaar, Chief Mechanic\nSol Ring\n3\ntwo Forest\n1 Island\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(deck.cards, strings(&["Island"]));
    assert_eq!(
        deck.skipped,
        vec![
            (3, "Sol Ring".to_owned()),
            (4, "3".to_owned()),
            (5, "two Forest".to_owned())
        ]
    );
}

#[test]
fn comments_and_headers_are_not_skipped_lines() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "# exported by hand\n\n// Commander\n1 Pia Nalaar, Chief Mechanic\n// Mana rocks\n1 Sol Ring   # :SolRingAetherdriftCommander57\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(deck.cards, strings(&["Sol Ring"]));
    assert_eq!(deck.skipped, Vec::new());
}

// ---- TestPrintingSuffix: the trailing '(SET) number [*F*]' of exported lists is stripped

#[test]
fn lowercase_set_code() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "1 Pia Nalaar, Chief Mechanic\n1 Sol Ring (lci) 26\n1 Forest (LCI) 300 *F*\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(deck.cards, strings(&["Sol Ring", "Forest"]));
}

#[test]
fn double_faced_card_separator() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "1 Pia Nalaar, Chief Mechanic\n1 Delver of Secrets / Insectile Aberration\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(
        deck.cards,
        strings(&["Delver of Secrets // Insectile Aberration"])
    );
}

#[test]
fn first_card_is_the_commander_without_a_section() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "1 Pia Nalaar, Chief Mechanic\n2 Sol Ring\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(
        deck.commander.as_deref(),
        Some("Pia Nalaar, Chief Mechanic")
    );
    assert_eq!(deck.cards, strings(&["Sol Ring", "Sol Ring"]));
    assert_eq!(deck.size(), 3);
}

// ---- TestDeckTTL: load_deck_ttl on synthetic deck graphs

#[test]
fn entries_quantities_and_commander() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(tmp.path(), "TestDeck.ttl", DECK_WITH_ENTRIES);
    let deck = load_deck_ttl(&path, &ind2name()).expect("deck");
    assert_eq!(deck.commander.as_deref(), Some("Auntie Ool, Cursewretch"));
    assert_eq!(deck.fmt, "ttl");
    let mut distinct: Vec<&str> = deck.cards.iter().map(String::as_str).collect();
    distinct.sort_unstable();
    distinct.dedup();
    assert_eq!(distinct, ["Sol Ring", "Swamp"]);
    assert_eq!(deck.cards.iter().filter(|c| *c == "Swamp").count(), 3);
    assert_eq!(
        deck.cards
            .iter()
            .filter(|c| *c == "Auntie Ool, Cursewretch")
            .count(),
        0
    );
    assert_eq!(deck.size(), 5);
    assert_eq!(deck.name, "TestDeck");
}

#[test]
fn hascard_fallback() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(tmp.path(), "deck.ttl", DECK_HASCARD_ONLY);
    let deck = load_deck_ttl(&path, &ind2name()).expect("deck");
    assert_eq!(deck.commander.as_deref(), Some("Sol Ring"));
    assert_eq!(deck.cards, strings(&["Swamp"]));
}

#[test]
fn unresolved_individual_raises() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let broken = DECK_WITH_ENTRIES.replace("SolRingAetherdriftCommander57", "NoSuchCardXyz");
    let path = common::write(tmp.path(), "deck.ttl", &broken);
    let err = load_deck_ttl(&path, &ind2name()).expect_err("unresolved");
    assert!(matches!(err, DeckError::Unresolved(_)));
    let msg = err.to_string();
    assert!(msg.contains("NoSuchCardXyz"), "{msg}");
    assert!(
        msg.contains("1 card individual(s) not found in the knowledge graph"),
        "{msg}"
    );
}

// ---- TestLoadDeckDispatch: load_deck routes by file suffix

#[test]
fn ttl_requires_ind2name() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(tmp.path(), "deck.ttl", DECK_WITH_ENTRIES);
    let err = load_deck(&path, None).expect_err("needs graph");
    assert!(matches!(err, DeckError::NeedsGraph(_)));
    assert!(
        err.to_string()
            .ends_with(".ttl decks require the knowledge graph")
    );
}

#[test]
fn ttl_dispatch() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(tmp.path(), "deck.TTL", DECK_WITH_ENTRIES);
    let map = ind2name();
    let deck = load_deck(&path, Some(&map)).expect("deck");
    assert_eq!(deck.fmt, "ttl");
    assert_eq!(deck.commander.as_deref(), Some("Auntie Ool, Cursewretch"));
}

#[test]
fn txt_unaffected() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = common::write(
        tmp.path(),
        "deck.txt",
        "// Commander\n1 Pia Nalaar, Chief Mechanic\n// Main\n3 Swamp\n",
    );
    let deck = load_deck(&path, None).expect("deck");
    assert_eq!(deck.fmt, "txt");
    assert_eq!(
        deck.commander.as_deref(),
        Some("Pia Nalaar, Chief Mechanic")
    );
    assert_eq!(deck.size(), 4);
}

// ---- Rust-only hardening

#[test]
fn missing_file_reads_like_an_os_error() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let err = load_deck(&tmp.path().join("nope.txt"), None).expect_err("missing");
    let msg = err.to_string();
    assert!(
        msg.starts_with("[Errno 2] No such file or directory: '"),
        "{msg}"
    );
    assert!(msg.ends_with("nope.txt'"), "{msg}");
}

#[test]
fn deck_path_is_the_pathlib_spelling() {
    let tmp = tempfile::tempdir().expect("tempdir");
    common::write(tmp.path(), "d/deck.txt", "1 Sol Ring\n");
    let odd = tmp.path().join("d").join(".").join("deck.txt");
    let deck = load_deck(&odd, None).expect("deck");
    assert_eq!(
        deck.path,
        tmp.path().join("d").join("deck.txt").display().to_string()
    );
}
