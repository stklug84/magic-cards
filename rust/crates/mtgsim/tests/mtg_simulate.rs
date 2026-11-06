//! The `mtg-simulate` and `mtg-selfplay` command lines: port of the
//! Python `test_cli.py` and `test_selfplay.py` (the CLI halves), plus
//! the shared loaders. Games run over the checkout's knowledge graph; the
//! Comprehensive Rules text is the checkout's when present (the tests
//! skip with a notice otherwise, `MTG_REQUIRE_CR=1` makes that a
//! failure).

use std::path::{Path, PathBuf};

use assert_cmd::Command;
use mtgcards::CardDatabase;
use mtgcards::graph::GRAPH_SCHEMA;
use mtgrules::selfplay::{Knob, parse_tune};
use mtgsim::cli::{load_decks, parse_seeds};

const DECK: &str = "// Commander\n1 Pia Nalaar, Chief Mechanic\n1 Sol Ring\n10 Swamp\n";

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("repo root")
        .to_path_buf()
}

/// The checkout's Comprehensive Rules text, or `None` (skip).
fn cr_file() -> Option<PathBuf> {
    let found = mtgrules::cr::ComprehensiveRules::newest_in(&repo_root()).ok();
    if found.is_none() {
        assert!(
            std::env::var_os("MTG_REQUIRE_CR").is_none(),
            "MTG_REQUIRE_CR is set but no MagicCompRules-*.txt is in the checkout"
        );
        eprintln!("notice: skipped, no Comprehensive Rules text in the checkout");
    }
    found
}

fn binary(name: &str, cr: &Path) -> Command {
    let mut cmd = Command::cargo_bin(name).expect("binary");
    cmd.env_remove("MTG_GRAPH_ROOT");
    cmd.arg("--graph-root")
        .arg(repo_root())
        .arg("--cr-file")
        .arg(cr);
    cmd
}

fn write(dir: &Path, name: &str, text: &str) -> PathBuf {
    let path = dir.join(name);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).expect("parent dir");
    }
    std::fs::write(&path, text).expect("write");
    path
}

// ---- parse_seeds

#[test]
fn seeds_count_or_list() {
    assert_eq!(parse_seeds(42, "3").expect("a count"), vec![42, 43, 44]);
    assert_eq!(parse_seeds(42, "1,7,100").expect("a list"), vec![1, 7, 100]);
    assert_eq!(
        parse_seeds(42, "0").expect_err("too few"),
        "--seeds must be >= 1"
    );
    assert_eq!(
        parse_seeds(42, ",").expect_err("empty"),
        "--seeds needs at least one seed"
    );
    assert!(parse_seeds(42, "abc").is_err());
}

// ---- the simulate CLI

#[test]
fn bad_counts_are_usage_errors() {
    let Some(cr) = cr_file() else { return };
    let tmp = tempfile::tempdir().expect("tempdir");
    let deck = write(
        tmp.path(),
        "a.txt",
        "1 Pia Nalaar, Chief Mechanic\n1 Sol Ring\n",
    );
    for argv in [
        ["--seeds", "0"],
        ["--seeds", "abc"],
        ["--games", "0"],
        ["--turn-cap", "0"],
    ] {
        binary("mtg-simulate", &cr)
            .arg(&deck)
            .arg(&deck)
            .arg("--offline")
            .args(argv)
            .assert()
            .code(2);
    }
}

#[test]
fn incompatible_bundle_exits_cleanly() {
    let Some(cr) = cr_file() else { return };
    let tmp = tempfile::tempdir().expect("tempdir");
    write(
        tmp.path(),
        "GRAPH-MANIFEST.json",
        &format!(
            "{{\"graph_schema\": {}, \"graph_version\": \"2099-01-01\", \"requires_tools\": \">=9.9.9\"}}",
            GRAPH_SCHEMA + 1
        ),
    );
    let deck = write(
        tmp.path(),
        "a.txt",
        "1 Pia Nalaar, Chief Mechanic\n1 Sol Ring\n",
    );
    let mut cmd = Command::cargo_bin("mtg-simulate").expect("binary");
    cmd.env_remove("MTG_GRAPH_ROOT");
    let out = cmd
        .arg("--graph-root")
        .arg(tmp.path())
        .arg("--cr-file")
        .arg(&cr)
        .arg(&deck)
        .arg(&deck)
        .args(["--offline", "--games", "1"])
        .assert()
        .code(1);
    let err = String::from_utf8(out.get_output().stderr.clone()).unwrap();
    assert!(
        err.contains(&format!("graph_schema {}", GRAPH_SCHEMA + 1)),
        "{err}"
    );
}

#[test]
fn too_few_decks_and_missing_files_exit_1() {
    let Some(cr) = cr_file() else { return };
    let tmp = tempfile::tempdir().expect("tempdir");
    let deck = write(tmp.path(), "a.txt", DECK);
    let out = binary("mtg-simulate", &cr).arg(&deck).assert().code(1);
    let err = String::from_utf8(out.get_output().stderr.clone()).unwrap();
    assert_eq!(
        err,
        "pass 2-4 deck files (txt decklists or .ttl deck graphs)\n"
    );
    let out = binary("mtg-simulate", &cr)
        .arg(&deck)
        .arg(tmp.path().join("missing.txt"))
        .assert()
        .code(1);
    let err = String::from_utf8(out.get_output().stderr.clone()).unwrap();
    assert!(err.starts_with("deck file not found: "), "{err}");
}

#[test]
fn offline_match_reports_and_stubs_unknown_cards() {
    let Some(cr) = cr_file() else { return };
    let tmp = tempfile::tempdir().expect("tempdir");
    let a = write(tmp.path(), "a.txt", DECK);
    let b = write(
        tmp.path(),
        "b.txt",
        "// Commander\n1 Pia Nalaar, Chief Mechanic\n1 No Such Card Offline Xyz\n10 Swamp\n",
    );
    let log = tmp.path().join("games.jsonl");
    let out = binary("mtg-simulate", &cr)
        .arg(&a)
        .arg(&b)
        .args([
            "--offline",
            "--games",
            "2",
            "--turn-cap",
            "3",
            "--seed",
            "5",
            "--verbose",
            "--log-file",
        ])
        .arg(&log)
        .assert()
        .success();
    let text = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    let err = String::from_utf8(out.get_output().stderr.clone()).unwrap();
    assert!(
        text.contains(" mtgrules - 2 games, seed 5, 2 players"),
        "{text}"
    );
    assert!(text.contains("  s5 g  1 T 3 winner="), "{text}");
    assert!(text.contains(" card data sources: graph="), "{text}");
    assert!(text.contains(" per-game JSONL written to "), "{text}");
    assert!(
        err.contains(
            "warning: 1 unknown card(s) stubbed as inert 3-mana sorceries: No Such Card Offline Xyz"
        ),
        "{err}"
    );
    let records = std::fs::read_to_string(&log).expect("the JSONL");
    assert_eq!(records.lines().count(), 2);
    assert!(records.contains("\"seed\": 5"));
}

#[test]
fn skipped_decklist_lines_warn() {
    let db = CardDatabase::load(&repo_root(), None, &[]).expect("the graph loads");
    let tmp = tempfile::tempdir().expect("tempdir");
    let deck = write(
        tmp.path(),
        "sloppy.txt",
        "// Commander\n1 Pia Nalaar, Chief Mechanic\nSol Ring\n1 Forest\n",
    );
    let mut warnings = Vec::new();
    let decks = load_decks(&[deck], &db, &mut |w| warnings.push(w.to_owned())).expect("loads");
    assert_eq!(decks[0].cards, ["Forest"]);
    assert_eq!(warnings.len(), 1);
    assert!(warnings[0].contains("skipped 1 line(s)"), "{}", warnings[0]);
    assert!(warnings[0].contains("3: 'Sol Ring'"), "{}", warnings[0]);
}

// ---- parse_tune and the selfplay CLI

#[test]
fn tune_axes_and_types() {
    let combos = parse_tune(&[
        "aggression=0.7,1.5".to_owned(),
        "hold_reactive_mana=1,no".to_owned(),
    ])
    .expect("parses");
    assert_eq!(combos.len(), 4);
    assert_eq!(
        combos[0],
        vec![
            ("aggression".to_owned(), Knob::Float(0.7)),
            ("hold_reactive_mana".to_owned(), Knob::Bool(true)),
        ]
    );
    assert_eq!(
        parse_tune(&["race_life=10".to_owned()]).expect("parses"),
        vec![vec![("race_life".to_owned(), Knob::Int(10))]]
    );
}

#[test]
fn tune_unknown_knob_exits() {
    assert_eq!(
        parse_tune(&["swagger=1".to_owned()]).expect_err("not a knob"),
        "--tune expects KNOB=v1,v2 with a PolicyProfile field, got 'swagger=1'"
    );
}

#[test]
fn selfplay_zero_counts_are_usage_errors() {
    let Some(cr) = cr_file() else { return };
    let tmp = tempfile::tempdir().expect("tempdir");
    let deck = write(tmp.path(), "mirror.txt", DECK);
    for argv in [["--games", "0"], ["--seeds", "0"]] {
        binary("mtg-selfplay", &cr)
            .arg(&deck)
            .args(argv)
            .assert()
            .code(2);
    }
}

#[test]
fn selfplay_ttl_deck_graphs_load() {
    let Some(cr) = cr_file() else { return };
    let db = CardDatabase::load(&repo_root(), None, &[]).expect("the graph loads");
    let ind = |name: &str| -> String {
        db.ind2name
            .iter()
            .find(|(_, n)| n.as_str() == name)
            .map(|(i, _)| i.clone())
            .expect("an individual")
    };
    let (pia, ring, swamp) = (
        ind("Pia Nalaar, Chief Mechanic"),
        ind("Sol Ring"),
        ind("Swamp"),
    );
    let tmp = tempfile::tempdir().expect("tempdir");
    let deck = write(
        tmp.path(),
        "graph.ttl",
        &format!(
            ":{pia} :isCommanderOf :D .\n:D :hasDeckEntry [ :entryCard :{ring} ; :quantity \"1\" ] ,\n               [ :entryCard :{swamp} ; :quantity \"10\" ] .\n"
        ),
    );
    let out = binary("mtg-selfplay", &cr)
        .arg(&deck)
        .args(["--games", "1", "--turn-cap", "2"])
        .assert()
        .success();
    let text = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(
        text.contains("preset round-robin: graph#1 vs graph#2"),
        "{text}"
    );
}

#[test]
fn selfplay_same_file_name_gets_distinct_seats() {
    let Some(cr) = cr_file() else { return };
    let tmp = tempfile::tempdir().expect("tempdir");
    let a = write(tmp.path(), "a/deck.txt", DECK);
    let b = write(tmp.path(), "b/deck.txt", DECK);
    let out = binary("mtg-selfplay", &cr)
        .arg(&a)
        .arg(&b)
        .args(["--games", "1", "--turn-cap", "2"])
        .assert()
        .success();
    let text = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(text.contains("deck vs deck#2"), "{text}");
}
