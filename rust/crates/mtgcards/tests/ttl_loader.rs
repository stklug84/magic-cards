//! Card-characteristic extraction from set TTL blocks (port of the Python
//! `mtgcards/tests/test_ttl_loader.py`).
//!
//! The generator emits multi-valued predicates as Turtle object lists,
//! with the objects of one predicate separated by `,` across several
//! lines. A regex that captured a single object per predicate would
//! silently keep only the first one, so cards would quietly lose
//! subtypes, colours, colour identity and land mana colours. These tests
//! pin the object-list parsing and keep the legacy repeated-predicate
//! form loading, so an older graph bundle still works. They also pin that
//! the MTG Arena lines of a printing on Arena (`:isAvailableOn`,
//! `:arenaName`, ...) leave the extracted characteristics untouched, and
//! that `:hasColor` and `:hasColorIdentity`, whose names share a prefix,
//! are read apart.

mod common;

use std::collections::BTreeSet;

use indexmap::IndexMap;
use mtgcards::CardIndex;
use mtgcards::behavior::set_value;
use mtgcards::ttl_loader::{LoadReport, load_graph_cards};

/// object-list form: the style the generator emits
const OBJECT_LIST: &str = r#":PiaNalaarChiefMechanic rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Pia Nalaar, Chief Mechanic" ;
    :manaCost "{G}{U}{R}" ;
    :manaValue "3"^^xsd:nonNegativeInteger ;
    :hasSuperType :Legendary ;
    :hasCardType :Artifact ,
                 :Creature ;
    :hasSubType :Human ,
                :Artificer ;
    :hasColor :Blue ,
              :Red ,
              :Green ;
    :hasColorIdentity :Blue ,
                      :Red ,
                      :Green ;
    :powerValue "2"^^xsd:integer ;
    :toughnessValue "4"^^xsd:integer ;
    :oracleText """Vehicles you control have haste."""@en .
"#;

/// legacy form: one predicate line per object
const REPEATED: &str = r#":PiaNalaarChiefMechanic rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Pia Nalaar, Chief Mechanic" ;
    :manaValue "3"^^xsd:nonNegativeInteger ;
    :hasSuperType :Legendary ;
    :hasCardType :Artifact ;
    :hasCardType :Creature ;
    :hasSubType :Human ;
    :hasSubType :Artificer ;
    :hasColor :Blue ;
    :hasColor :Red ;
    :hasColor :Green ;
    :hasColorIdentity :Blue ;
    :hasColorIdentity :Red ;
    :hasColorIdentity :Green ;
    :oracleText """Vehicles you control have haste."""@en .
"#;

const LAND: &str = r#":AetherHub rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Aether Hub" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasCardType :Land ;
    :producesMana :White ,
                  :Blue ,
                  :Black ,
                  :Red ,
                  :Green ;
    :entersTapped "true"^^xsd:boolean ;
    :oracleText """{T}: Add {C}."""@en .
"#;

/// an MTG Arena printing of a transforming card: the platform object list
/// and the Arena fields sit between the card facts and the legalities, and
/// `:arenaName` holds the front face only
const ARENA_DFC: &str = r#":LunarchVeteranInnistradMidnightHunt27 rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Lunarch Veteran // Luminous Phantom" ;
    :manaCost "{W}" ;
    :manaValue "1"^^xsd:nonNegativeInteger ;
    :hasCardType :Creature ;
    :hasSubType :Human ,
                :Cleric ,
                :Spirit ;
    :hasColorIdentity :White ;
    :powerValue "1"^^xsd:integer ;
    :toughnessValue "1"^^xsd:integer ;
    :oracleText """Whenever another creature enters, you gain 1 life."""@en ;
    :isAvailableOn :PaperMagic ,
                   :MTGArena ,
                   :MagicOnline ;
    :arenaId "78354"^^xsd:positiveInteger ;
    :arenaName "Lunarch Veteran" ;
    :arenaSetCode "MID" ;
    :hasLegality
      [ rdf:type :LegalityMapping ; :inFormat :Historic ; :hasLegalityStatus :Legal ] .
"#;

const SINGLE: &str = r#":Forest rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Forest" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasSuperType :Basic ;
    :hasCardType :Land ;
    :hasSubType :Forest ;
    :producesMana :Green ;
    :oracleText """({T}: Add {G}.)"""@en .
"#;

/// a colour identity wider than the card's colour: the flashback cost
/// adds black
const WIDER_IDENTITY: &str = r#":LingeringSouls rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Lingering Souls" ;
    :manaCost "{2}{W}" ;
    :manaValue "3"^^xsd:nonNegativeInteger ;
    :hasCardType :Sorcery ;
    :hasRarity :Uncommon ;
    :hasColor :White ;
    :hasColorIdentity :White ,
                      :Black ;
    :oracleText """Create two 1/1 white Spirit creature tokens with flying.
Flashback {1}{B}"""@en .
"#;

/// a colourless card with a coloured identity: no `:hasColor` at all
const COLORLESS: &str = r#":AzoriusSignet rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Azorius Signet" ;
    :manaCost "{2}" ;
    :manaValue "2"^^xsd:nonNegativeInteger ;
    :hasCardType :Artifact ;
    :hasRarity :Uncommon ;
    :hasColorIdentity :White ,
                      :Blue ;
    :producesMana :White ,
                  :Blue ;
    :oracleText """{1}, {T}: Add {W}{U}."""@en .
"#;

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

/// Write `blocks` as one set file and load it through `load_graph_cards`.
fn load(blocks: &[&str]) -> (CardIndex, IndexMap<String, String>, LoadReport) {
    let tmp = tempfile::tempdir().expect("tempdir");
    common::write(
        tmp.path(),
        "TestSet.ttl",
        &format!("{}{}", common::TTL_HEADER, blocks.join("\n")),
    );
    let mut ind2name = IndexMap::new();
    let mut report = LoadReport::default();
    let index = load_graph_cards(tmp.path(), &[], &mut ind2name, &mut report).expect("load");
    (index, ind2name, report)
}

// ---- TestObjectLists: multi-valued predicates survive the object-list encoding

#[test]
fn object_list_keeps_every_object() {
    let (index, ..) = load(&[OBJECT_LIST]);
    let card = index.get("Pia Nalaar, Chief Mechanic").expect("card");
    assert_eq!(card.types, set(&["Artifact", "Creature"]));
    assert_eq!(card.subtypes, set(&["Human", "Artificer"]));
    assert_eq!(card.colors, set(&["U", "R", "G"]));
    assert!(card.is_multicolored());
    assert_eq!(card.color_identity, set(&["U", "R", "G"]));
    assert_eq!(card.supertypes, set(&["Legendary"]));
}

#[test]
fn repeated_predicate_form_still_loads() {
    let (new, ..) = load(&[OBJECT_LIST]);
    let (old, ..) = load(&[REPEATED]);
    let new = new.get("Pia Nalaar, Chief Mechanic").expect("card");
    let old = old.get("Pia Nalaar, Chief Mechanic").expect("card");
    assert_eq!(old.types, new.types);
    assert_eq!(old.subtypes, new.subtypes);
    assert_eq!(old.colors, new.colors);
    assert_eq!(old.color_identity, new.color_identity);
    assert_eq!(old.supertypes, new.supertypes);
}

#[test]
fn single_object_predicate() {
    let (index, ..) = load(&[SINGLE]);
    let card = index.get("Forest").expect("card");
    assert_eq!(card.types, set(&["Land"]));
    assert_eq!(card.subtypes, set(&["Forest"]));
    assert_eq!(card.supertypes, set(&["Basic"]));
    assert_eq!(card.behavior.get("land_colors"), Some(&set_value(["G"])));
}

#[test]
fn scalar_fields_still_parse() {
    let (index, ..) = load(&[OBJECT_LIST]);
    let card = index.get("Pia Nalaar, Chief Mechanic").expect("card");
    assert_eq!(card.mana_cost, "{G}{U}{R}");
    assert_eq!(card.mv, 3);
    assert_eq!(card.power, Some(2));
    assert_eq!(card.toughness, Some(4));
    assert!(card.oracle.contains("haste"));
}

// ---- Colours: `:hasColor` and `:hasColorIdentity` are read apart

#[test]
fn colour_and_colour_identity_are_separate_facts() {
    let (index, ..) = load(&[WIDER_IDENTITY, COLORLESS]);
    let souls = index.get("Lingering Souls").expect("card");
    assert_eq!(souls.colors, set(&["W"]));
    assert_eq!(souls.color_identity, set(&["W", "B"]));
    assert!(!souls.is_multicolored());
    let signet = index.get("Azorius Signet").expect("card");
    assert!(signet.colors.is_empty());
    assert_eq!(signet.color_identity, set(&["W", "U"]));
}

// ---- TestArenaFields: the MTG Arena lines of a card block leave the card facts intact

#[test]
fn arena_name_does_not_replace_the_card_name() {
    let (index, ind2name, report) = load(&[ARENA_DFC]);
    let full = index
        .get("Lunarch Veteran // Luminous Phantom")
        .expect("card");
    assert_eq!(full.name, "Lunarch Veteran // Luminous Phantom");
    // the Python test's assertIs: both names resolve to one record
    assert!(index.same_record("Lunarch Veteran", "Lunarch Veteran // Luminous Phantom"));
    assert_eq!(
        ind2name
            .get("LunarchVeteranInnistradMidnightHunt27")
            .map(String::as_str),
        Some("Lunarch Veteran // Luminous Phantom")
    );
    assert_eq!(report.unindexed_individuals, Vec::new());
}

#[test]
fn platform_list_does_not_leak_into_characteristics() {
    let (index, ..) = load(&[ARENA_DFC]);
    let card = index
        .get("Lunarch Veteran // Luminous Phantom")
        .expect("card");
    assert_eq!(card.types, set(&["Creature"]));
    assert_eq!(card.subtypes, set(&["Human", "Cleric", "Spirit"]));
    assert_eq!(card.color_identity, set(&["W"]));
    assert!(card.supertypes.is_empty());
    assert_eq!((card.mana_cost.as_str(), card.mv), ("{W}", 1));
    assert_eq!((card.power, card.toughness), (Some(1), Some(1)));
    assert!(card.oracle.contains("gain 1 life"));
}

// ---- TestLandFacts: graph-authored land facts read through the object-list form

#[test]
fn produces_mana_list() {
    let (index, ..) = load(&[LAND]);
    let card = index.get("Aether Hub").expect("card");
    assert_eq!(
        card.behavior.get("land_colors"),
        Some(&set_value(["W", "U", "B", "R", "G"]))
    );
    assert!(card.behavior.enters_tapped());
}

// ---- Rust-only hardening

#[test]
fn first_printing_wins_and_every_individual_is_indexed() {
    let second = SINGLE
        .replace(":Forest rdf:type", ":ForestOtherSet rdf:type")
        .replace(
            r#":oracleText """({T}: Add {G}.)"""@en ."#,
            r#":oracleText """Second printing."""@en ."#,
        );
    let (index, ind2name, _) = load(&[SINGLE, &second]);
    assert_eq!(index.len(), 1);
    assert!(index.get("Forest").expect("card").oracle.contains("{G}"));
    assert_eq!(ind2name.len(), 2);
    assert_eq!(ind2name["ForestOtherSet"], "Forest");
}

#[test]
fn a_block_without_an_individual_line_is_reported() {
    let odd = "    :cardName \"Odd One\" ;\n    :hasCardType :Instant .\n";
    let (index, ind2name, report) = load(&[odd]);
    assert!(index.contains("Odd One"));
    assert!(ind2name.is_empty());
    assert_eq!(report.unindexed_individuals.len(), 1);
    assert_eq!(report.unindexed_individuals[0].1, "Odd One");
}

#[test]
fn crlf_files_read_like_python_text_mode() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let text = format!("{}{}", common::TTL_HEADER, OBJECT_LIST).replace('\n', "\r\n");
    common::write(tmp.path(), "Crlf.ttl", &text);
    let mut ind2name = IndexMap::new();
    let mut report = LoadReport::default();
    let index = load_graph_cards(tmp.path(), &[], &mut ind2name, &mut report).expect("load");
    let card = index.get("Pia Nalaar, Chief Mechanic").expect("card");
    assert_eq!(card.oracle, "Vehicles you control have haste.");
    assert_eq!(card.types, set(&["Artifact", "Creature"]));
}
