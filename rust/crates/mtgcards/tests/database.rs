//! `CardDatabase`: manifest and custom-card errors (port of the Python
//! `test_database_errors.py`, fix C3), the layering of graph, custom
//! cards and annotations on synthetic roots, and the real knowledge
//! graph of this checkout.

mod common;

use std::path::Path;

use mtgcards::behavior::set_value;
use mtgcards::database::{CardDatabase, DatabaseError};
use mtgcards::graph::{GRAPH_SCHEMA, load_manifest};
use pycompat::json::Value;

const MANIFEST: &str = r#"{"graph_schema": 1, "graph_version": "2026-10-16", "ontology_iri": "urn:stklug84:MagicCardsOntology:2026-02-27#", "cr_version": "20260925", "requires_tools": ">=0.2.0", "sets": 0, "card_individuals": 0}"#;

fn load(root: &Path) -> Result<CardDatabase, DatabaseError> {
    CardDatabase::load(root, None, &[])
}

// ---- TestManifestErrors: load_manifest() reports every malformed manifest with its path

#[test]
fn invalid_json_is_a_graph_schema_error() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let manifest = common::write(tmp.path(), "GRAPH-MANIFEST.json", "{not json");
    let err = load_manifest(tmp.path()).expect_err("invalid json");
    assert!(err.0.contains(&manifest.display().to_string()), "{err}");
    assert!(err.0.contains("cannot read the graph manifest"), "{err}");
}

#[test]
fn wrong_field_type_is_a_graph_schema_error() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let manifest = common::write(
        tmp.path(),
        "GRAPH-MANIFEST.json",
        &MANIFEST.replace(r#""graph_schema": 1"#, r#""graph_schema": "1.0""#),
    );
    let err = load_manifest(tmp.path()).expect_err("'1.0' is not an int");
    assert!(err.0.contains(&manifest.display().to_string()), "{err}");
    assert!(
        err.0
            .ends_with("malformed graph manifest: invalid literal for int() with base 10: '1.0'"),
        "{err}"
    );
}

#[test]
fn non_object_is_a_graph_schema_error() {
    let tmp = tempfile::tempdir().expect("tempdir");
    common::write(tmp.path(), "GRAPH-MANIFEST.json", "[1, 2]");
    let err = load_manifest(tmp.path()).expect_err("array");
    assert!(err.0.ends_with("expected a JSON object, got list"), "{err}");
}

#[test]
fn incompatible_schema_fails_database_construction() {
    let tmp = tempfile::tempdir().expect("tempdir");
    common::write(
        tmp.path(),
        "GRAPH-MANIFEST.json",
        &MANIFEST.replace(
            r#""graph_schema": 1"#,
            &format!(r#""graph_schema": {}"#, GRAPH_SCHEMA + 1),
        ),
    );
    let err = load(tmp.path()).expect_err("incompatible");
    assert!(matches!(err, DatabaseError::Schema(_)));
    let msg = err.to_string();
    assert!(
        msg.contains(&format!("graph_schema {}", GRAPH_SCHEMA + 1)),
        "{msg}"
    );
    assert!(
        msg.contains(&format!("supports graph_schema {GRAPH_SCHEMA}")),
        "{msg}"
    );
    assert!(msg.contains("(graph version 2026-10-16)"), "{msg}");
    assert!(msg.contains("requires tools >=0.2.0"), "{msg}");
}

#[test]
fn valid_manifest_loads() {
    let tmp = tempfile::tempdir().expect("tempdir");
    common::write(tmp.path(), "GRAPH-MANIFEST.json", MANIFEST);
    let db = load(tmp.path()).expect("db");
    let manifest = db.manifest.expect("manifest was not loaded");
    assert_eq!(manifest.graph_version, "2026-10-16");
    assert_eq!(manifest.cr_version, "20260925");
    assert_eq!(manifest.graph_schema, 1);
    assert!(db.index.is_empty());
}

#[test]
fn a_root_without_a_manifest_is_accepted() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let db = load(tmp.path()).expect("db");
    assert!(db.manifest.is_none());
    assert!(db.hooks_by_name.is_empty());
}

// ---- TestCustomCardErrors: the --custom-cards JSON layer reports its own file on errors

#[test]
fn invalid_custom_json_names_the_file() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let custom = common::write(tmp.path(), "custom.json", "{oops");
    let err = CardDatabase::load(tmp.path(), Some(&custom), &[]).expect_err("invalid");
    assert!(matches!(err, DatabaseError::CustomValue(_)));
    let msg = err.to_string();
    assert!(
        msg.starts_with(&format!(
            "{}: invalid custom-cards JSON: ",
            custom.display()
        )),
        "{msg}"
    );
}

#[test]
fn unknown_behavior_key_names_the_card() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let custom = common::write(
        tmp.path(),
        "custom.json",
        r#"{"Weirdo": {"mana_cost": "{1}", "behavior": {"bogus": 1}}}"#,
    );
    let err = CardDatabase::load(tmp.path(), Some(&custom), &[]).expect_err("bogus key");
    assert!(matches!(err, DatabaseError::CustomValue(_)));
    let msg = err.to_string();
    assert!(
        msg.contains(
            "custom card 'Weirdo': unknown behavior key 'bogus' (see behaviors.BEHAVIOR_KEYS)"
        ),
        "{msg}"
    );
}

#[test]
fn non_object_custom_json_is_rejected() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let custom = common::write(tmp.path(), "custom.json", "[1, 2]");
    let err = CardDatabase::load(tmp.path(), Some(&custom), &[]).expect_err("array");
    assert!(matches!(err, DatabaseError::CustomType(_)));
    assert_eq!(
        err.to_string(),
        format!(
            "{}: expected a JSON object of card definitions",
            custom.display()
        )
    );
}

#[test]
fn missing_custom_file_is_an_os_error() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let err = CardDatabase::load(tmp.path(), Some(&tmp.path().join("none.json")), &[])
        .expect_err("missing");
    assert!(matches!(err, DatabaseError::CustomRead(_)));
    assert!(
        err.to_string()
            .starts_with("[Errno 2] No such file or directory: '"),
        "{err}"
    );
}

#[test]
fn custom_field_of_the_wrong_type_is_reported() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let custom = common::write(
        tmp.path(),
        "custom.json",
        r#"{"Weirdo": {"types": "Creature"}}"#,
    );
    let err = CardDatabase::load(tmp.path(), Some(&custom), &[]).expect_err("types is not a list");
    assert!(
        err.to_string()
            .contains("custom card 'Weirdo': types must be a list of strings, got str"),
        "{err}"
    );
}

// ---- Layering on a synthetic graph root

const SET: &str = r#":SolRingTest rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Sol Ring" ;
    :manaCost "{1}" ;
    :manaValue "1"^^xsd:nonNegativeInteger ;
    :hasCardType :Artifact ;
    :oracleText """{T}: Add {C}{C}."""@en .

:ForestTest rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Forest" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasSuperType :Basic ;
    :hasCardType :Land ;
    :hasSubType :Forest ;
    :producesMana :Green ;
    :oracleText """({T}: Add {G}.)"""@en .

:DelverTest rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Delver of Secrets // Insectile Aberration" ;
    :manaCost "{U}" ;
    :manaValue "1"^^xsd:nonNegativeInteger ;
    :hasCardType :Creature ;
    :hasSubType :Human ,
                :Wizard ;
    :hasColorIdentity :Blue ;
    :powerValue "1"^^xsd:integer ;
    :toughnessValue "1"^^xsd:integer ;
    :oracleText """At the beginning of your upkeep, look at the top card of your library."""@en .
"#;

const ANNOTATIONS: &str = r#"@prefix : <urn:test#> .

:Ontology rdf:type owl:Ontology .

:SolRingTest :threatWeight "3"^^xsd:nonNegativeInteger ;
    :hasBehaviorHook [ :behaviorKey "rock_mana" ; :behaviorValue "2" ] ;
    :hasBehaviorHook [ :behaviorKey "wipe" ; :behaviorValue "{\"style\": \"damage\", \"dmg\": 3}" ] .
"#;

fn synthetic_root() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().expect("tempdir");
    common::write(
        tmp.path(),
        "sets/Test.ttl",
        &format!("{}{SET}", common::TTL_HEADER),
    );
    tmp
}

#[test]
fn graph_derivation_and_dfc_alias() {
    let root = synthetic_root();
    let mut db = load(root.path()).expect("db");
    assert_eq!(db.ind2name.len(), 3);
    // oracle derivation ran once per record
    let sol = db.lookup("Sol Ring").expect("Sol Ring");
    assert_eq!(sol.behavior.get("rock_mana"), Some(&Value::Int(2)));
    assert_eq!(sol.behavior.get("rock_colors"), Some(&set_value(["C"])));
    // the graph land fact wins over the oracle fallback (one key, set once)
    let forest = db.lookup("Forest").expect("Forest");
    assert_eq!(forest.behavior.get("land_colors"), Some(&set_value(["G"])));
    assert_eq!(forest.behavior.len(), 1);
    // the front face is the same record; `get` of the full name falls back to it
    assert!(db.index.same_record(
        "Delver of Secrets",
        "Delver of Secrets // Insectile Aberration"
    ));
    let via_full = db.get("Delver of Secrets // Something Else").name.clone();
    assert_eq!(via_full, "Delver of Secrets // Insectile Aberration");
    assert_eq!(db.stubbed, Vec::<String>::new());
    assert_eq!(db.report.unindexed_individuals, Vec::new());
}

#[test]
fn annotations_win_over_derivation_and_need_known_subjects() {
    let root = synthetic_root();
    common::write(root.path(), "MagicSimulationAnnotations.ttl", ANNOTATIONS);
    let db = load(root.path()).expect("db");
    let sol = db.lookup("Sol Ring").expect("Sol Ring");
    assert_eq!(sol.behavior.key(), 3);
    assert_eq!(sol.behavior.get("rock_mana"), Some(&Value::Int(2)));
    assert!(sol.behavior.wipe());
    assert_eq!(
        sol.behavior.get("wipe").and_then(|w| w.get("dmg")),
        Some(&Value::Int(3))
    );
    // key order: derived keys first, hooks appended / overwritten in place
    let keys: Vec<&str> = sol.behavior.iter().map(|(k, _)| k).collect();
    assert_eq!(keys, ["rock_mana", "rock_colors", "key", "wipe"]);

    common::write(
        root.path(),
        "MagicSimulationAnnotations.ttl",
        &ANNOTATIONS.replace(
            ":SolRingTest :threatWeight",
            ":NoSuchIndividual :threatWeight",
        ),
    );
    let err = load(root.path()).expect_err("unknown subject");
    assert_eq!(
        err.to_string(),
        "MagicSimulationAnnotations.ttl: subject :NoSuchIndividual is not a card individual known to the card graphs"
    );

    common::write(
        root.path(),
        "MagicSimulationAnnotations.ttl",
        &ANNOTATIONS.replace(r#":behaviorKey "wipe""#, r#":behaviorKey "bogus""#),
    );
    let err = load(root.path()).expect_err("unknown key");
    assert_eq!(
        err.to_string(),
        "MagicSimulationAnnotations.ttl: :SolRingTest uses unknown behavior key 'bogus' (see BEHAVIOR_KEYS)"
    );
}

#[test]
fn custom_cards_override_the_graph_and_keep_their_behaviour() {
    let root = synthetic_root();
    let custom = common::write(
        root.path(),
        "custom.json",
        r#"{"_comment": "ignored", "Sol Ring": {"mana_cost": "{2}{G/U}", "types": ["Artifact", "Artifact"], "oracle": "Draw two cards.", "behavior": {"draw_cards": 5, "etb_tokens": [1, 2, 2, false]}}, "New Card": {"mana_cost": "{X}{R}", "types": ["Instant"], "oracle": "Destroy target creature.", "power": null}}"#,
    );
    let db = CardDatabase::load(root.path(), Some(&custom), &[]).expect("db");
    let sol = db.lookup("Sol Ring").expect("Sol Ring");
    assert_eq!(sol.source, "custom");
    assert_eq!(sol.mv, 3);
    assert_eq!(sol.types.len(), 1);
    // the custom behaviour wins over the oracle derivation (draw_cards 2)
    assert_eq!(sol.behavior.get("draw_cards"), Some(&Value::Int(5)));
    assert_eq!(
        sol.behavior.get("etb_tokens"),
        Some(&Value::List(vec![
            Value::Int(1),
            Value::Int(2),
            Value::Int(2),
            Value::Bool(false)
        ]))
    );
    let new = db.lookup("New Card").expect("New Card");
    assert_eq!((new.mv, new.power), (1, None));
    assert!(new.behavior.removal());
    assert!(!db.index.contains("_comment"));
    // the graph record is still there under the DFC alias, untouched
    assert_eq!(db.lookup("Forest").expect("Forest").source, "graph");
}

#[test]
fn extra_graphs_bring_their_own_annotations() {
    let root = synthetic_root();
    let extra_dir = tempfile::tempdir().expect("tempdir");
    let extra = common::write(
        extra_dir.path(),
        "MagicExternalCards.ttl",
        &format!(
            "{}{}",
            common::TTL_HEADER,
            SET.replace("SolRingTest", "SolRingExternal")
                .replace("ForestTest", "ForestExternal")
                .replace("DelverTest", "DelverExternal")
        ),
    );
    common::write(
        extra_dir.path(),
        "MagicSimulationAnnotations.ttl",
        &ANNOTATIONS.replace("SolRingTest", "SolRingExternal"),
    );
    let db = CardDatabase::load(root.path(), None, &[extra, root.path().join("missing.ttl")])
        .expect("db");
    // the first definition (sets/) wins, every individual is indexed
    assert_eq!(db.ind2name.len(), 6);
    assert_eq!(db.ind2name["SolRingExternal"], "Sol Ring");
    // the annotation next to the extra graph was merged by card name
    assert_eq!(db.lookup("Sol Ring").expect("Sol Ring").behavior.key(), 3);
}

#[test]
fn unknown_cards_are_stubbed_once() {
    let root = synthetic_root();
    let mut db = load(root.path()).expect("db");
    assert!(db.lookup("Nope").is_none());
    let stub = db.get("Nope");
    assert_eq!(
        (stub.source.as_str(), stub.mana_cost.as_str(), stub.mv),
        ("stub", "{3}", 3)
    );
    assert!(stub.types.contains("Sorcery"));
    let again = db.get("Nope");
    assert_eq!(again.source, "stub");
    assert_eq!(db.stubbed, vec!["Nope".to_owned()]);
    assert!(db.lookup("Nope").is_some());
}

// ---- The real knowledge graph of this checkout

#[test]
fn real_graph_loads_with_python_counts() {
    let mut db = load(&common::repo_root()).expect("the repo's graph loads");
    assert!(db.manifest.is_none());
    assert!(db.index.len() >= 1500, "{} names", db.index.len());
    assert!(db.ind2name.len() >= db.index.len());
    assert!(
        db.report.unindexed_individuals.is_empty(),
        "{:?}",
        db.report.unindexed_individuals
    );
    assert_eq!(db.stubbed, Vec::<String>::new());

    let sol = db.get("Sol Ring");
    assert_eq!(
        (sol.mana_cost.as_str(), sol.mv, sol.source.as_str()),
        ("{1}", 1, "graph")
    );
    assert_eq!(sol.behavior.get("rock_mana"), Some(&Value::Int(2)));
    assert_eq!(sol.behavior.get("rock_colors"), Some(&set_value(["C"])));

    let slough = db.get("Canyon Slough");
    assert_eq!(
        slough.behavior.get("land_colors"),
        Some(&set_value(["B", "R"]))
    );
    assert!(slough.behavior.enters_tapped());
    assert_eq!(
        slough.subtypes.iter().collect::<Vec<_>>(),
        ["Mountain", "Swamp"]
    );

    let signet = db.get("Arcane Signet");
    assert_eq!(
        signet.behavior.get("rock_colors"),
        Some(&set_value(["W", "U", "B", "R", "G"]))
    );

    let full = "Liliana, Heretical Healer // Liliana, Defiant Necromancer";
    assert!(db.index.same_record("Liliana, Heretical Healer", full));
    assert_eq!(
        db.get(full).types.iter().collect::<Vec<_>>(),
        ["Creature", "Planeswalker"]
    );
    assert_eq!(db.stubbed, Vec::<String>::new());
}
