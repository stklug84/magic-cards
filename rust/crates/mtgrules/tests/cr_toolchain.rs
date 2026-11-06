//! R0 CR toolchain (port of the Python `test_r0_r2_toolchain.py`
//! `TestCRToolchain`) and the `#[rule]` / `unsupported!` registry.
//!
//! Tests that read the Comprehensive Rules text skip with a notice when
//! no `MagicCompRules-*.txt` is present (the text is not redistributable
//! and is fetched separately); `MTG_REQUIRE_CR=1` — set in CI — turns
//! that skip into a failure.

use mtgrules::cr::{
    self, COVERAGE_FAMILIES, ComprehensiveRules, CrError, RULE_IMPLEMENTATIONS, UNSUPPORTED,
    check_registry, coverage_report, get_cr, implementations, probe_lines, unsupported_rules,
};
use mtgrules::{rule, unsupported};

// ---- registrations exercised by the tests below -----------------------

/// A free function implementing two rules (one written with a dot).
#[rule("100.1", "117.")]
fn free_function() -> u32 {
    1
}

struct Kernel;

impl Kernel {
    /// A method: the attribute sits inside an impl block.
    #[rule("704.5g")]
    const fn method(&self) -> u32 {
        let Kernel = self;
        2
    }

    #[rule("601.2")]
    pub(crate) fn public_method() -> u32 {
        3
    }
}

/// A struct: registered at module level.
#[rule("613.1")]
#[derive(Debug, Default)]
struct LayerSystem;

/// An enum with generics and attributes in between.
#[allow(dead_code, reason = "registered for its #[rule] annotation")]
#[rule("614.1", "614.1a")]
enum Replacement<T> {
    Skip(T),
}

unsupported!(
    "903.4",
    "deck legality is validated by the knowledge-graph tooling"
);

// ---- helpers -----------------------------------------------------------

/// The parsed CR, or `None` (after a notice) when the text is absent and
/// not required.
fn cr_or_skip(test: &str) -> Option<&'static ComprehensiveRules> {
    match get_cr() {
        Ok(cr) => Some(cr),
        Err(err) => {
            assert!(
                std::env::var_os("MTG_REQUIRE_CR").is_none(),
                "MTG_REQUIRE_CR is set but the CR text is unavailable: {err}"
            );
            eprintln!("notice: {test}: skipped, no Comprehensive Rules text ({err})");
            None
        }
    }
}

/// sanity floors for the parsed CR index (the real file is far larger)
const MIN_RULES: usize = 3000;
const MIN_GLOSSARY: usize = 600;
const MIN_EXAMPLES: usize = 270;

// ---- TestCRToolchain ---------------------------------------------------

#[test]
fn parse_counts() {
    let Some(cr) = cr_or_skip("parse_counts") else {
        return;
    };
    assert!(cr.rules.len() > MIN_RULES, "{} rules", cr.rules.len());
    assert!(
        cr.glossary.len() > MIN_GLOSSARY,
        "{} glossary terms",
        cr.glossary.len()
    );
    assert!(
        cr.example_count() >= MIN_EXAMPLES,
        "{} examples",
        cr.example_count()
    );
    assert!(cr.summary_line().contains(" rules, "));
}

#[test]
fn known_rules_present() {
    let Some(cr) = cr_or_skip("known_rules_present") else {
        return;
    };
    for n in [
        "100.1", "117.4", "601.2", "613.8", "614.1", "704.5g", "702.19e", "903.10a",
    ] {
        assert!(cr.contains(n), "{n} missing from the CR index");
        assert!(
            cr.contains(&format!("{n}.")),
            "{n}. (trailing dot) must resolve too"
        );
    }
    assert!(!cr.contains("999.99z"));
    assert_eq!(probe_lines(cr).len(), 6);
    assert!(probe_lines(cr)[0].starts_with("  100.1: "));
}

#[test]
fn rule_registry_validates_against_the_text() {
    let Some(cr) = cr_or_skip("rule_registry_validates_against_the_text") else {
        return;
    };
    // every number this test file registered exists in the real CR
    check_registry(cr).unwrap_or_else(|err| panic!("registry has unknown rules:\n{err}"));
    // ...and a CR without them reports each one with its location
    let empty = ComprehensiveRules::parse("", std::path::PathBuf::from("MagicCompRules-0.txt"));
    let err = check_registry(&empty).expect_err("nothing is defined in an empty CR");
    let text = err.to_string();
    assert!(text.contains("@rule: unknown CR rule '704.5g' ("), "{text}");
    assert!(text.contains("::method, "), "{text}");
    assert!(
        text.contains("unsupported(): unknown CR rule '903.4' ("),
        "{text}"
    );
    assert!(text.contains("cr_toolchain.rs:"), "{text}");
    assert!(err.0.len() >= 8, "{}", err.0.len());
}

#[test]
fn registry_has_the_annotations_of_this_file() {
    let implementations = implementations();
    for family in ["100", "117", "601", "613", "614", "704"] {
        assert!(
            implementations.keys().any(|n| n.starts_with(family)),
            "no #[rule] annotations for CR family {family}"
        );
    }
    // numbers are stored without the trailing dot, as in Python
    assert!(implementations.contains_key("117"));
    assert!(!implementations.keys().any(|n| n.ends_with('.')));
    let method = &implementations["704.5g"];
    assert_eq!(method.len(), 1);
    assert!(method[0].ends_with("::method"), "{method:?}");
    assert!(method[0].starts_with("cr_toolchain"), "{method:?}");
    assert_eq!(implementations["614.1a"][0], implementations["614.1"][0]);

    let entry = RULE_IMPLEMENTATIONS
        .iter()
        .find(|e| e.numbers.contains(&"613.1"))
        .expect("struct entry");
    assert_eq!(
        (entry.kind, entry.implementer),
        ("struct", "cr_toolchain::LayerSystem")
    );
    assert!(entry.file.ends_with("cr_toolchain.rs"));
    assert!(entry.line > 0);

    let unsupported = unsupported_rules();
    assert_eq!(
        unsupported.get("903.4").map(String::as_str),
        Some("deck legality is validated by the knowledge-graph tooling")
    );
    assert_eq!(
        UNSUPPORTED.iter().filter(|u| u.number == "903.4").count(),
        1
    );
    // the annotated items still work
    assert_eq!(
        free_function() + Kernel.method() + Kernel::public_method(),
        6
    );
    let _ = LayerSystem;
    let _ = Replacement::Skip(0);
}

#[test]
fn examples_lookup() {
    let Some(cr) = cr_or_skip("examples_lookup") else {
        return;
    };
    assert_ne!(cr.examples_under("613"), [] as [(&str, &str); 0]);
    assert!(
        cr.examples_under("613")
            .iter()
            .all(|(n, _)| n.starts_with("613"))
    );
}

#[test]
fn coverage_report_lists_this_files_annotations() {
    let Some(cr) = cr_or_skip("coverage_report_lists_this_files_annotations") else {
        return;
    };
    let report = coverage_report(cr, &["704", "903"]);
    assert!(
        report.starts_with(&format!("CR file: {}  (", cr.file_name())),
        "{report}"
    );
    // a method registers as module::name (the impl type is not visible to
    // the attribute); the file:line is in the registry entry
    assert!(
        report.contains("  OK   704.5g  <- cr_toolchain::method\n"),
        "{report}"
    );
    assert!(
        report
            .contains("  SKIP 903.4  (deck legality is validated by the knowledge-graph tooling)"),
        "{report}"
    );
    assert!(report.contains("  ..   704.5h\n"), "{report}");
    assert_ne!(coverage_report(cr, COVERAGE_FAMILIES), "");
}

#[test]
fn missing_cr_file_is_reported() {
    let tmp = tempfile::tempdir().expect("tempdir");
    assert_eq!(
        ComprehensiveRules::newest_in(tmp.path()),
        Err(CrError::NotFound)
    );
    assert_eq!(
        CrError::NotFound.to_string(),
        "no MagicCompRules-*.txt in repo"
    );
    std::fs::write(
        tmp.path().join("MagicCompRules-20260101.txt"),
        "100. General\n100.1. One.\n",
    )
    .unwrap();
    std::fs::write(
        tmp.path().join("MagicCompRules-20260925.txt"),
        "100. General\n100.2. Two.\n",
    )
    .unwrap();
    std::fs::write(tmp.path().join("MagicCompRules-notes.md"), "ignored").unwrap();
    let newest = ComprehensiveRules::newest_in(tmp.path()).expect("newest");
    assert!(newest.ends_with("MagicCompRules-20260925.txt"));
    let cr = ComprehensiveRules::load_newest(tmp.path()).expect("load");
    assert!(cr.contains("100.2") && !cr.contains("100.1"));
    assert_eq!(cr.file_name(), "MagicCompRules-20260925.txt");
    assert!(cr::PROBES.contains(&"704.5g"));
}
