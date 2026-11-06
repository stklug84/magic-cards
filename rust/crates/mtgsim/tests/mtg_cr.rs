//! `mtg-cr` end to end: graph-root discovery, the CR-file override, the
//! subcommands and the exit codes. A synthetic CR text (not CR text) in
//! a temporary graph root keeps the tests independent of the
//! non-redistributable file; one test uses the real file when present.

use std::path::{Path, PathBuf};

use assert_cmd::Command;

const SYNTHETIC_CR: &str = "\
Magic: The Gathering Comprehensive Rules

Contents

1. Game Concepts
100. General
Glossary
Credits

1. Game Concepts

100. General

100.1. A fake rule.
Example: A worked example.
100.1a A fake sub-rule.

Glossary

Flying
A fake keyword.

Credits

Nobody.
";

fn mtg_cr() -> Command {
    let mut cmd = Command::cargo_bin("mtg-cr").expect("mtg-cr binary");
    cmd.env_remove("MTG_GRAPH_ROOT");
    cmd
}

/// A graph root (ontology file + sets/) holding two CR files; the newer
/// one is the synthetic text.
fn synthetic_root() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().expect("tempdir");
    std::fs::write(tmp.path().join("MagicCardsOntology.ttl"), "# schema\n").unwrap();
    std::fs::create_dir(tmp.path().join("sets")).unwrap();
    std::fs::write(
        tmp.path().join("MagicCompRules-20200101.txt"),
        "100. Old\n100.9. Old rule.\n",
    )
    .unwrap();
    std::fs::write(tmp.path().join("MagicCompRules-20260101.txt"), SYNTHETIC_CR).unwrap();
    tmp
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("repo root")
        .to_path_buf()
}

#[test]
fn summary_and_probes_from_the_newest_file_at_the_graph_root() {
    let root = synthetic_root();
    let out = mtg_cr()
        .arg("--graph-root")
        .arg(root.path())
        .assert()
        .success();
    let text = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(
        text.starts_with("MagicCompRules-20260101.txt: 3 rules, 1 examples, 1 glossary terms\n"),
        "{text}"
    );
    // probes of rules the synthetic text lacks print an empty head
    assert!(text.contains("\n  100.1: A fake rule....\n"), "{text}");
    assert!(text.contains("\n  704.5g: ...\n"), "{text}");
}

#[test]
fn graph_root_comes_from_the_environment_or_the_working_directory() {
    let root = synthetic_root();
    let nested = root.path().join("decks").join("deep");
    std::fs::create_dir_all(&nested).unwrap();
    mtg_cr()
        .current_dir(&nested)
        .assert()
        .success()
        .stdout(predicates::str::starts_with(
            "MagicCompRules-20260101.txt: 3 rules",
        ));
    mtg_cr()
        .current_dir(std::env::temp_dir())
        .env("MTG_GRAPH_ROOT", root.path())
        .assert()
        .success()
        .stdout(predicates::str::starts_with(
            "MagicCompRules-20260101.txt: 3 rules",
        ));
    mtg_cr()
        .env("MTG_GRAPH_ROOT", root.path().join("nope"))
        .assert()
        .code(1)
        .stderr(predicates::str::contains("MTG_GRAPH_ROOT="))
        .stderr(predicates::str::contains("not a directory"));
}

#[test]
fn no_graph_root_and_no_cr_file_are_reported_with_exit_1() {
    let empty = tempfile::tempdir().expect("tempdir");
    mtg_cr()
        .current_dir(empty.path())
        .assert()
        .code(1)
        .stderr(predicates::str::starts_with(
            "mtg-cr: no graph root: set MTG_GRAPH_ROOT",
        ));
    let rootless = tempfile::tempdir().expect("tempdir");
    std::fs::write(rootless.path().join("MagicCardsOntology.ttl"), "").unwrap();
    std::fs::create_dir(rootless.path().join("sets")).unwrap();
    mtg_cr()
        .arg("--graph-root")
        .arg(rootless.path())
        .assert()
        .code(1)
        .stderr(predicates::str::contains(
            "no MagicCompRules-*.txt in repo (looked in ",
        ))
        .stderr(predicates::str::contains("pass --cr-file"));
    mtg_cr()
        .arg("--graph-root")
        .arg(rootless.path().join("missing"))
        .assert()
        .code(1)
        .stderr(predicates::str::contains("--graph-root"))
        .stderr(predicates::str::contains("not a directory"));
}

#[test]
fn cr_file_override_and_lookups() {
    let root = synthetic_root();
    let file = root.path().join("MagicCompRules-20260101.txt");
    let base = || {
        let mut cmd = mtg_cr();
        cmd.arg("--cr-file").arg(&file);
        cmd
    };
    base()
        .args(["show", "100.1", "100.1a"])
        .assert()
        .success()
        .stdout("100.1. A fake rule.\n  Example: A worked example.\n100.1a. A fake sub-rule.\n");
    base()
        .args(["show", "999.1"])
        .assert()
        .code(1)
        .stderr("mtg-cr: unknown CR rule \"999.1\"\n");
    base()
        .args(["examples", "100"])
        .assert()
        .success()
        .stdout("100.1: A worked example.\n");
    base()
        .args(["examples", "700"])
        .assert()
        .code(1)
        .stderr(predicates::str::contains("no worked examples under"));
    base()
        .args(["glossary", "flying"])
        .assert()
        .success()
        .stdout("Flying\nA fake keyword.\n");
    base()
        .args(["glossary", "trample"])
        .assert()
        .code(1)
        .stderr(predicates::str::contains("no glossary entry"));
    base()
        .args(["glossary"])
        .assert()
        .code(1)
        .stderr(predicates::str::contains("needs a term"));
}

#[test]
fn coverage_and_check_over_the_synthetic_text() {
    let root = synthetic_root();
    let out = mtg_cr()
        .arg("--graph-root")
        .arg(root.path())
        .args(["coverage", "100"])
        .assert()
        .success();
    let text = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(
        text.contains("\nCR file: MagicCompRules-20260101.txt  (3 rules parsed)\n"),
        "{text}"
    );
    assert!(
        text.contains("--- 100. General [0 implemented / 0 unsupported / 3 unclaimed of 3]\n"),
        "{text}"
    );
    // the engine's #[rule] annotations do not resolve against a text of
    // three rules: every unknown number is listed with its implementer
    // and location, exit 1
    mtg_cr()
        .arg("--graph-root")
        .arg(root.path())
        .arg("check")
        .assert()
        .code(1)
        .stderr(predicates::str::starts_with(
            "mtg-cr: @rule: unknown CR rule '",
        ))
        .stderr(predicates::str::contains("(mtgrules::"))
        .stderr(predicates::str::contains("crates/mtgrules/src/"))
        .stderr(predicates::str::contains(".rs:"));
}

#[test]
fn help_and_version_never_need_the_rules_text() {
    let empty = tempfile::tempdir().expect("tempdir");
    mtg_cr()
        .current_dir(empty.path())
        .arg("--version")
        .assert()
        .success()
        .stdout("mtg-cr 0.3.0\n");
    mtg_cr()
        .current_dir(empty.path())
        .arg("--help")
        .assert()
        .success()
        .stdout(predicates::str::contains("coverage"))
        .stdout(predicates::str::contains("--graph-root"));
    mtg_cr()
        .current_dir(empty.path())
        .arg("--bogus")
        .assert()
        .code(2);
    mtg_cr()
        .current_dir(empty.path())
        .arg("show")
        .assert()
        .code(2);
}

#[test]
fn real_rules_text_when_present() {
    let repo = repo_root();
    let has_cr = std::fs::read_dir(&repo).is_ok_and(|entries| {
        entries.filter_map(Result::ok).any(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            name.starts_with("MagicCompRules-")
                && Path::new(&name).extension() == Some("txt".as_ref())
        })
    });
    if !has_cr {
        assert!(
            std::env::var_os("MTG_REQUIRE_CR").is_none(),
            "MTG_REQUIRE_CR is set but no MagicCompRules-*.txt is in the checkout"
        );
        eprintln!("notice: real_rules_text_when_present: skipped, no Comprehensive Rules text");
        return;
    }
    let out = mtg_cr().arg("--graph-root").arg(&repo).assert().success();
    let text = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(
        text.contains(" rules, ") && text.contains(" glossary terms\n"),
        "{text}"
    );
    assert!(
        text.contains("\n  704.5g: If a creature has toughness greater than 0"),
        "{text}"
    );
    mtg_cr()
        .arg("--graph-root")
        .arg(&repo)
        .args(["show", "903.10a"])
        .assert()
        .success();
    // every #[rule] annotation of the engine resolves against the real text
    mtg_cr()
        .arg("--graph-root")
        .arg(&repo)
        .arg("check")
        .assert()
        .success()
        .stdout(predicates::str::contains(" rule annotations and "))
        .stdout(predicates::str::contains(" resolve\n"));
}
