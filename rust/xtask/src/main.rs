//! Developer tasks for the Rust workspace: `cargo xtask <task>`.
//!
//! The alias lives in `rust/.cargo/config.toml`. Tasks:
//!
//! - `vectors [--check]`: regenerate the reference vectors the `pycompat`
//!   tests (CPython semantics) and the `mtgrules` tests (the Python
//!   engine's semantics) read (`scripts/parity/vectors.py`), or verify
//!   that the committed files are up to date;
//! - `parity [--level N]`: compare the Rust and Python implementations
//!   level by level — L1 is `vectors --check`; L2 diffs the card-database
//!   dumps (`scripts/parity/dump_cards.py` against the `mtgcards`
//!   `dump_cards` example) over the checkout's graph and over a synthetic
//!   root with custom cards and an extra graph, and the Comprehensive
//!   Rules dumps (`scripts/parity/dump_cr.py` against the `mtgrules`
//!   `dump_cr` example) over the checkout's rules text when present and
//!   over a synthetic text;
//! - `help`.
//!
//! Higher parity levels (compiled abilities, `--viz-file` records, the
//! report) and `golden` (freeze expected outputs) arrive with the crates
//! they test.
//!
//! The Python interpreter is `$PYTHON`, else `python3`.

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const USAGE: &str = "usage: cargo xtask <task>

tasks:
  vectors [--check]   regenerate the reference vectors under
                      rust/crates/{pycompat,mtgrules}/tests/vectors (or
                      check that the committed files are current)
  parity [--level N]  compare the Rust and Python implementations:
                      L1 = vectors --check, L2 = card-database and
                      Comprehensive Rules dumps (default: every level)
  help                print this message
";

/// The repository root: two levels above this crate's manifest.
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("rust/xtask lives two levels below the repository root")
        .to_path_buf()
}

fn python() -> String {
    std::env::var("PYTHON").unwrap_or_else(|_| "python3".to_owned())
}

/// A `python -m parity.<module>` command running from `scripts/`.
fn python_module(root: &Path, module: &str) -> Command {
    let mut command = Command::new(python());
    command
        .args(["-m", &format!("parity.{module}")])
        .current_dir(root.join("scripts"))
        .env("PYTHONDONTWRITEBYTECODE", "1");
    command
}

fn run(mut command: Command, what: &str) -> Result<(), String> {
    let status = command
        .status()
        .map_err(|e| format!("cannot run {what}: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{what} failed: {status}"))
    }
}

/// The committed vector directories: the CPython vectors of `pycompat`
/// and the engine vectors of `mtgrules`, relative to the repository.
const VECTOR_DIRS: [&str; 2] = [
    "rust/crates/pycompat/tests/vectors",
    "rust/crates/mtgrules/tests/vectors",
];

/// Run `python -m parity.vectors <pycompat_dir> <mtgrules_dir>` from
/// `scripts/`.
fn generate_vectors(root: &Path, out_dirs: &[PathBuf]) -> Result<(), String> {
    let mut command = python_module(root, "vectors");
    command.args(out_dirs);
    run(command, "vector generation")
}

fn vectors(check: bool) -> Result<(), String> {
    let root = repo_root();
    let committed: Vec<PathBuf> = VECTOR_DIRS.iter().map(|d| root.join(d)).collect();
    if !check {
        generate_vectors(&root, &committed)?;
        for dir in &committed {
            println!("vectors written to {}", dir.display());
        }
        return Ok(());
    }
    let scratch = std::env::temp_dir().join(format!("mtg-vectors-{}", std::process::id()));
    let fresh: Vec<PathBuf> = VECTOR_DIRS
        .iter()
        .map(|d| {
            scratch
                .join(Path::new(d).file_name().unwrap_or_default())
                .join(d.replace('/', "-"))
        })
        .collect();
    for dir in &fresh {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let result = generate_vectors(&root, &fresh).and_then(|()| {
        committed
            .iter()
            .zip(&fresh)
            .try_for_each(|(old, new)| compare_dirs(old, new))
    });
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

/// Every file of `fresh` must exist in `committed` with identical bytes.
fn compare_dirs(committed: &Path, fresh: &Path) -> Result<(), String> {
    let mut stale = Vec::new();
    let entries = std::fs::read_dir(fresh).map_err(|e| format!("{}: {e}", fresh.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("{}: {e}", fresh.display()))?;
        let name = entry.file_name();
        let new_bytes =
            std::fs::read(entry.path()).map_err(|e| format!("{}: {e}", name.to_string_lossy()))?;
        let old_bytes = std::fs::read(committed.join(&name)).unwrap_or_default();
        if new_bytes != old_bytes {
            stale.push(name.to_string_lossy().into_owned());
        }
    }
    if stale.is_empty() {
        println!("vectors are current: {}", committed.display());
        Ok(())
    } else {
        Err(format!(
            "stale vectors: {} (run `cargo xtask vectors`)",
            stale.join(", ")
        ))
    }
}

// ------------------------------------------------------------------ parity

/// A graph root to dump on both sides, with the layers to add.
struct L2Scenario {
    label: String,
    root: PathBuf,
    custom_cards: Option<PathBuf>,
    extra_graphs: Vec<PathBuf>,
}

const FIXTURE_HEADER: &str = "\
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix :     <urn:parity#> .

";

/// Synthetic cards covering every derivation family: a rock, lands
/// (graph facts, fetch, tapped), removal, a wipe, tokens, an anthem,
/// proliferate/energy, a draw spell, ramp and a double-faced card.
const FIXTURE_SET: &str = r#":ParityRock rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Rock" ;
    :manaCost "{2}" ;
    :manaValue "2"^^xsd:nonNegativeInteger ;
    :hasCardType :Artifact ;
    :oracleText """{T}: Add one mana of any color."""@en .

:ParityDual rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Dual" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasCardType :Land ;
    :hasSubType :Swamp ,
                :Mountain ;
    :hasColorIdentity :Black ,
                      :Red ;
    :producesMana :Black ,
                  :Red ;
    :entersTapped "true"^^xsd:boolean ;
    :oracleText """({T}: Add {B} or {R}.)
Parity Dual enters tapped."""@en .

:ParityFetch rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Fetch" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasCardType :Land ;
    :isFetchLand "true"^^xsd:boolean ;
    :oracleText """{T}, Sacrifice Parity Fetch: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle."""@en .

:ParityBolt rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Bolt" ;
    :manaCost "{1}{R}" ;
    :manaValue "2"^^xsd:nonNegativeInteger ;
    :hasCardType :Instant ;
    :hasColorIdentity :Red ;
    :oracleText """Destroy target creature. Draw a card.
Flash"""@en .

:ParityWrath rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Wrath" ;
    :manaCost "{2}{W}{W}" ;
    :manaValue "4"^^xsd:nonNegativeInteger ;
    :hasCardType :Sorcery ;
    :hasColorIdentity :White ;
    :oracleText """Destroy all creatures. Parity Wrath deals 3 damage to each creature."""@en .

:ParityMaker rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Maker" ;
    :manaCost "{3}{G}" ;
    :manaValue "4"^^xsd:nonNegativeInteger ;
    :hasCardType :Creature ;
    :hasSubType :Elf ,
                :Druid ;
    :hasColorIdentity :Green ;
    :powerValue "2"^^xsd:integer ;
    :toughnessValue "3"^^xsd:integer ;
    :oracleText """Trample, vigilance
When Parity Maker enters, create two 1/1 green Elf Warrior creature tokens.
At the beginning of your upkeep, create a Treasure token. Proliferate twice. You get {E}{E}."""@en .

:ParityAnthem rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Anthem" ;
    :manaCost "{1}{W}" ;
    :manaValue "2"^^xsd:nonNegativeInteger ;
    :hasCardType :Enchantment ;
    :hasColorIdentity :White ;
    :oracleText """Creature tokens you control get +1/+1.
Populate. Counter target spell."""@en .

:ParityRamp rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Ramp" ;
    :manaCost "{1}{G}" ;
    :manaValue "2"^^xsd:nonNegativeInteger ;
    :hasCardType :Sorcery ;
    :hasColorIdentity :Green ;
    :oracleText """Search your library for up to two basic land cards, put them onto the battlefield tapped, then shuffle. Draw two cards."""@en .

:ParityFlip rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Flip // Parity Flop" ;
    :manaCost "{U}" ;
    :manaValue "1"^^xsd:nonNegativeInteger ;
    :hasCardType :Creature ;
    :hasSubType :Human ,
                :Wizard ;
    :hasColorIdentity :Blue ;
    :powerValue "1"^^xsd:integer ;
    :toughnessValue "1"^^xsd:integer ;
    :loyalty "3"^^xsd:nonNegativeInteger ;
    :oracleText """Flying
Whenever Parity Flip dies, create a 2/2 black Zombie creature token."""@en .
"#;

const FIXTURE_ANNOTATIONS: &str = r#"@prefix : <urn:parity#> .

<urn:parity:annotations#> rdf:type owl:Ontology .

:ParityRock :threatWeight "4"^^xsd:nonNegativeInteger ;
    :hasBehaviorHook [ :behaviorKey "rock_mana" ; :behaviorValue "2" ] ;
    :hasBehaviorHook [ :behaviorKey "wipe" ; :behaviorValue "{\"style\": \"damage\", \"dmg\": 3}" ] ;
    :hasBehaviorHook [ :behaviorKey "etb_tokens" ; :behaviorValue "[1, 2, 2, true]" ] .

:ParityFlip :threatWeight "7"^^xsd:nonNegativeInteger ;
    :hasBehaviorHook [ :behaviorKey "draw_per_turn" ; :behaviorValue "1" ] .
"#;

const FIXTURE_EXTRA_ANNOTATIONS: &str = r#"@prefix : <urn:parity#> .

:ParityExternal :threatWeight "2"^^xsd:nonNegativeInteger ;
    :hasBehaviorHook [ :behaviorKey "tutor" ; :behaviorValue "true" ] .

:ParityRock :hasBehaviorHook [ :behaviorKey "rock_colors" ; :behaviorValue "[\"U\"]" ] .
"#;

const FIXTURE_EXTRA_SET: &str = r#":ParityExternal rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity External" ;
    :manaCost "{B}" ;
    :manaValue "1"^^xsd:nonNegativeInteger ;
    :hasCardType :Sorcery ;
    :hasColorIdentity :Black ;
    :oracleText """Search your library for a card, put it into your hand, then shuffle."""@en .

:ParityRockReprint rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Parity Rock" ;
    :manaCost "{9}" ;
    :manaValue "9"^^xsd:nonNegativeInteger ;
    :hasCardType :Artifact ;
    :oracleText """Reprint text that must lose to the first definition."""@en .
"#;

const FIXTURE_CUSTOM: &str = r#"{
  "_comment": "custom layer: a new card and an override of a graph card",
  "Parity Custom": {
    "mana_cost": "{X}{B/R}{2}",
    "types": ["Instant"],
    "color_identity": ["B", "R"],
    "oracle": "Exile target permanent. Create a Treasure token.",
    "behavior": {"removal_targets": 2, "burst_tokens": [3, 1, 1, false], "anthem": {"boost": 2, "tokens_only": false, "art_only": false}}
  },
  "Parity Anthem": {
    "mana_cost": "{W}",
    "types": ["Enchantment", "Artifact"],
    "subtypes": ["Aura"],
    "power": null,
    "toughness": 4,
    "oracle": "Creatures you control get +2/+2.",
    "behavior": {"key": 5, "doubler": 1.5}
  }
}
"#;

const FIXTURE_MANIFEST: &str = r#"{"graph_schema": "1", "graph_version": "parity", "ontology_iri": "urn:parity#", "cr_version": "20260925", "requires_tools": ">=0.3.0", "sets": 1, "card_individuals": 9}"#;

fn write(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    }
    std::fs::write(path, text).map_err(|e| format!("{}: {e}", path.display()))
}

/// Build the synthetic graph root (and its extra-graph directory) under
/// `scratch`.
fn synthetic_scenario(scratch: &Path) -> Result<L2Scenario, String> {
    let root = scratch.join("root");
    let extra = scratch.join("extra");
    write(
        &root.join("sets/Parity.ttl"),
        &format!("{FIXTURE_HEADER}{FIXTURE_SET}"),
    )?;
    write(
        &root.join("MagicSimulationAnnotations.ttl"),
        FIXTURE_ANNOTATIONS,
    )?;
    write(&root.join("GRAPH-MANIFEST.json"), FIXTURE_MANIFEST)?;
    write(&root.join("custom.json"), FIXTURE_CUSTOM)?;
    write(
        &extra.join("MagicExternalCards.ttl"),
        &format!("{FIXTURE_HEADER}{FIXTURE_EXTRA_SET}"),
    )?;
    write(
        &extra.join("MagicSimulationAnnotations.ttl"),
        FIXTURE_EXTRA_ANNOTATIONS,
    )?;
    Ok(L2Scenario {
        label: "synthetic root (custom cards, extra graph, manifest)".to_owned(),
        root: root.clone(),
        custom_cards: Some(root.join("custom.json")),
        extra_graphs: vec![
            extra.join("MagicExternalCards.ttl"),
            extra.join("missing.ttl"),
        ],
    })
}

/// Run both dumps of `scenario` into `scratch` and compare them.
fn compare_dumps(repo: &Path, scratch: &Path, scenario: &L2Scenario) -> Result<(), String> {
    let py_out = scratch.join("python.jsonl");
    let rs_out = scratch.join("rust.jsonl");
    let mut py = python_module(repo, "dump_cards");
    py.arg("--root")
        .arg(&scenario.root)
        .arg("--out")
        .arg(&py_out);
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".to_owned());
    let mut rs = Command::new(cargo);
    rs.args([
        "run",
        "--quiet",
        "-p",
        "mtgcards",
        "--example",
        "dump_cards",
        "--",
    ])
    .arg("--root")
    .arg(&scenario.root)
    .arg("--out")
    .arg(&rs_out)
    .current_dir(repo.join("rust"));
    if let Some(custom) = &scenario.custom_cards {
        py.arg("--custom-cards").arg(custom);
        rs.arg("--custom-cards").arg(custom);
    }
    for graph in &scenario.extra_graphs {
        py.arg("--extra-graph").arg(graph);
        rs.arg("--extra-graph").arg(graph);
    }
    run(py, "the Python dump")?;
    run(rs, "the Rust dump")?;
    let py_text =
        std::fs::read_to_string(&py_out).map_err(|e| format!("{}: {e}", py_out.display()))?;
    let rs_text =
        std::fs::read_to_string(&rs_out).map_err(|e| format!("{}: {e}", rs_out.display()))?;
    diff_lines(&py_text, &rs_text)
        .map_err(|detail| format!("L2 [{}]: {detail}", scenario.label))?;
    println!(
        "L2 [{}]: {} lines identical",
        scenario.label,
        py_text.lines().count()
    );
    Ok(())
}

/// The first differing line of two dumps, or Ok when they are identical.
fn diff_lines(python_text: &str, rust_text: &str) -> Result<(), String> {
    if python_text == rust_text {
        return Ok(());
    }
    let clip = |s: &str| -> String {
        let mut out: String = s.chars().take(400).collect();
        if out.len() < s.len() {
            out.push_str("...");
        }
        out
    };
    let mut py_lines = python_text.lines();
    let mut rs_lines = rust_text.lines();
    let mut number = 0;
    loop {
        number += 1;
        match (py_lines.next(), rs_lines.next()) {
            (Some(p), Some(r)) if p == r => {}
            (Some(p), Some(r)) => {
                return Err(format!(
                    "dumps differ at line {number}\n  python: {}\n  rust:   {}",
                    clip(p),
                    clip(r)
                ));
            }
            (Some(p), None) => {
                return Err(format!("rust dump ends before line {number}: {}", clip(p)));
            }
            (None, Some(r)) => {
                return Err(format!(
                    "python dump ends before line {number}: {}",
                    clip(r)
                ));
            }
            (None, None) => return Err("dumps differ only in trailing bytes".to_owned()),
        }
    }
}

/// Synthetic text in the layout of the Comprehensive Rules file (not CR
/// text): contents lines before the body, examples, lettered sub-rules,
/// a two-line glossary definition, trailing whitespace and CRLF endings.
const FIXTURE_CR: &str = "Magic: The Gathering Comprehensive Rules\r\n\r\nThese rules are fake.\r\n\r\nContents\r\n\r\n1. Game Concepts\r\n100. General\r\n704. State-Based Actions\r\nGlossary\r\nCredits\r\n\r\n1. Game Concepts\r\n\r\n100. General\r\n\r\n100.1. These are the fake rules of a fake game.  \r\nExample: A worked example of 100.1.\r\n100.1a Sub-rule a of 100.1 with a — dash and “quotes”.\r\n100.1b \r\n100.2. Second rule.\r\n\r\n704. State-Based Actions\r\n\r\n704.5. The state-based actions are as follows:\r\n704.5g A fake creature dies.\r\nExample: One example for 704.5g.\r\nExample: Another example for 704.5g.\r\n704.5g A fake creature dies (duplicate, shorter wins not).\r\n\r\nGlossary\r\n\r\nAbility\r\nAn ability does things.\r\nOver two lines.\r\n\r\nAbandon\r\nTo abandon.\r\n\r\nLonely Term\r\n\r\nCredits\r\n\r\nSomeone.\r\n";

/// Diff the CR dumps of `cr_file` (the Python module's `--cr-file` against
/// the Rust example's).
fn compare_cr_dumps(
    repo: &Path,
    scratch: &Path,
    label: &str,
    cr_file: &Path,
) -> Result<(), String> {
    let py_out = scratch.join("python-cr.jsonl");
    let rs_out = scratch.join("rust-cr.jsonl");
    let mut py = python_module(repo, "dump_cr");
    py.arg("--cr-file").arg(cr_file).arg("--out").arg(&py_out);
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".to_owned());
    let mut rs = Command::new(cargo);
    rs.args([
        "run",
        "--quiet",
        "-p",
        "mtgrules",
        "--example",
        "dump_cr",
        "--",
    ])
    .arg("--cr-file")
    .arg(cr_file)
    .arg("--out")
    .arg(&rs_out)
    .current_dir(repo.join("rust"));
    run(py, "the Python CR dump")?;
    run(rs, "the Rust CR dump")?;
    let py_text =
        std::fs::read_to_string(&py_out).map_err(|e| format!("{}: {e}", py_out.display()))?;
    let rs_text =
        std::fs::read_to_string(&rs_out).map_err(|e| format!("{}: {e}", rs_out.display()))?;
    diff_lines(&py_text, &rs_text).map_err(|detail| format!("L2 CR [{label}]: {detail}"))?;
    println!(
        "L2 CR [{label}]: {} lines identical",
        py_text.lines().count()
    );
    Ok(())
}

/// The newest `MagicCompRules-*.txt` in `dir`, if any.
fn newest_cr_file(dir: &Path) -> Option<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
                n.starts_with("MagicCompRules-") && Path::new(n).extension() == Some("txt".as_ref())
            })
        })
        .collect();
    files.sort();
    files.pop()
}

fn parity_l2() -> Result<(), String> {
    let repo = repo_root();
    let scratch = std::env::temp_dir().join(format!("mtg-parity-l2-{}", std::process::id()));
    std::fs::create_dir_all(&scratch).map_err(|e| format!("{}: {e}", scratch.display()))?;
    let result = (|| {
        let mut scenarios = Vec::new();
        if repo.join("sets").is_dir() {
            scenarios.push(L2Scenario {
                label: "checkout graph".to_owned(),
                root: repo.clone(),
                custom_cards: None,
                extra_graphs: Vec::new(),
            });
        } else {
            println!("L2: no sets/ in the checkout, skipping the real graph");
        }
        scenarios.push(synthetic_scenario(&scratch)?);
        for (i, scenario) in scenarios.iter().enumerate() {
            let out = scratch.join(format!("scenario-{i}"));
            std::fs::create_dir_all(&out).map_err(|e| format!("{}: {e}", out.display()))?;
            compare_dumps(&repo, &out, scenario)?;
        }
        // the Comprehensive Rules parser: the real text when present (it
        // is fetched separately, never committed), always the synthetic one
        match newest_cr_file(&repo) {
            Some(file) => {
                let out = scratch.join("cr-real");
                std::fs::create_dir_all(&out).map_err(|e| format!("{}: {e}", out.display()))?;
                compare_cr_dumps(&repo, &out, "checkout rules text", &file)?;
            }
            None if std::env::var_os("MTG_REQUIRE_CR").is_some() => {
                return Err(
                    "MTG_REQUIRE_CR is set but no MagicCompRules-*.txt is in the checkout"
                        .to_owned(),
                );
            }
            None => {
                println!("L2 CR: no MagicCompRules-*.txt in the checkout, skipping the real text");
            }
        }
        let out = scratch.join("cr-synthetic");
        let file = out.join("MagicCompRules-20260101.txt");
        write(&file, FIXTURE_CR)?;
        compare_cr_dumps(&repo, &out, "synthetic rules text", &file)
    })();
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

fn parity(level: Option<u8>) -> Result<(), String> {
    match level {
        None => {
            vectors(true)?;
            parity_l2()
        }
        Some(1) => vectors(true),
        Some(2) => parity_l2(),
        Some(other) => Err(format!("parity level {other} is not implemented yet")),
    }
}

fn parse_level(args: &[String]) -> Result<Option<u8>, String> {
    let Some(pos) = args.iter().position(|a| a == "--level") else {
        return Ok(None);
    };
    let value = args.get(pos + 1).ok_or("--level needs a number")?;
    value
        .parse::<u8>()
        .map(Some)
        .map_err(|_| format!("invalid --level {value:?}"))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        None | Some("help" | "--help" | "-h") => {
            print!("{USAGE}");
            Ok(())
        }
        Some("vectors") => vectors(args.iter().any(|a| a == "--check")),
        Some("parity") => parse_level(&args).and_then(parity),
        Some(other) => Err(format!("unknown task {other:?}\n{USAGE}")),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("xtask: {message}");
            ExitCode::from(2)
        }
    }
}
