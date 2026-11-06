//! Comprehensive Rules toolchain.
//!
//! Parses the Magic: The Gathering Comprehensive Rules text file
//! (`MagicCompRules-*.txt`) into a machine-usable index:
//!
//! - numbered rules (`100.`, `100.1.`, `100.1a`, `704.5g`, ...)
//! - worked examples (`Example: ...` passages, attached to their rule)
//! - the glossary (term -> definition)
//!
//! and hosts the registry the [`rule`](macro@crate::rule) attribute fills:
//! every rules-implementing item records the CR rules it implements, the
//! binaries validate those numbers against the text at startup
//! ([`check_registry`]) and [`coverage_report`] lists implemented,
//! unsupported and unclaimed rules so nothing is skipped silently.
//!
//! The parser is the Python `mtgrules.cr` parser line for line: the same
//! regex, the same table-of-contents handling (the longer body text wins
//! over the contents line), the same glossary splitting. The CR text is
//! normative prose for humans and is never interpreted here — only
//! indexed for traceability, tests and error messages. It is not
//! redistributable: the file is gitignored and fetched separately.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, OnceLock};

use indexmap::IndexMap;
use pycompat::pyio::{self, ReadTextError};
use pycompat::pyrepr::repr_str;
use pycompat::pystr;
use regex::Regex;

/// `^(\d{3}(?:\.\d+[a-z]?)?)\.?\s+(.*)$`, the Python `_RULE_RE`.
static RULE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(\d{3}(?:\.\d+[a-z]?)?)\.?\s+(.*)$").expect("valid regex"));

/// The file-name pattern of a CR text file at a graph root.
pub const CR_FILE_PREFIX: &str = "MagicCompRules-";
/// Its extension (gitignored: the text is not redistributable).
pub const CR_FILE_SUFFIX: &str = ".txt";

/// One numbered CR rule with its attached worked examples.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Rule {
    /// `704.5g` (no trailing dot)
    pub number: String,
    /// the normative text after the number
    pub text: String,
    /// the `Example:` passages following the rule
    pub examples: Vec<String>,
}

/// Errors of loading the CR text.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum CrError {
    /// No `MagicCompRules-*.txt` at the graph root (the Python
    /// `FileNotFoundError` text).
    #[error("no MagicCompRules-*.txt in repo")]
    NotFound,
    /// The file could not be read.
    #[error("{0}")]
    Read(ReadTextError),
    /// No graph root to look in.
    #[error("{0}")]
    NoRoot(#[from] mtgcards::graph::GraphRootError),
}

/// Index over one CR text file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ComprehensiveRules {
    /// the parsed file
    pub path: PathBuf,
    /// rule number -> rule, in order of first appearance
    pub rules: IndexMap<String, Rule>,
    /// glossary term -> definition, in file order
    pub glossary: IndexMap<String, String>,
}

impl ComprehensiveRules {
    /// The newest `MagicCompRules-*.txt` at `root` (the last in sorted
    /// order, like the Python `sorted(glob(...))[-1]`).
    ///
    /// # Errors
    ///
    /// [`CrError::NotFound`] when there is none.
    pub fn newest_in(root: &Path) -> Result<PathBuf, CrError> {
        let mut candidates: Vec<PathBuf> = std::fs::read_dir(root)
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| {
                        p.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
                            n.starts_with(CR_FILE_PREFIX) && n.ends_with(CR_FILE_SUFFIX)
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        candidates.sort_by(|a, b| a.as_os_str().cmp(b.as_os_str()));
        candidates.pop().ok_or(CrError::NotFound)
    }

    /// Parse the newest CR file at `root`.
    ///
    /// # Errors
    ///
    /// See [`CrError`].
    pub fn load_newest(root: &Path) -> Result<Self, CrError> {
        Self::load(&Self::newest_in(root)?)
    }

    /// Parse the CR file at `path`.
    ///
    /// # Errors
    ///
    /// [`CrError::Read`] when the file cannot be read.
    pub fn load(path: &Path) -> Result<Self, CrError> {
        let text = pyio::read_text(path).map_err(CrError::Read)?;
        Ok(Self::parse(&text, path.to_path_buf()))
    }

    /// Parse CR text (the contents of `path`).
    #[must_use]
    pub fn parse(text: &str, path: PathBuf) -> Self {
        let lines = pystr::splitlines(text);
        // the rules body runs from "1. Game Concepts" up to the second
        // "Glossary" line (the first is the table of contents entry)
        let glossary_hits: Vec<usize> = lines
            .iter()
            .enumerate()
            .filter(|(_, l)| pystr::strip(l) == "Glossary")
            .map(|(i, _)| i)
            .collect();
        let body_end = glossary_hits.get(1).copied().unwrap_or(lines.len());
        let mut cr = Self {
            path,
            ..Self::default()
        };
        cr.parse_rules(&lines[..body_end]);
        if body_end < lines.len() {
            cr.parse_glossary(&lines[body_end + 1..]);
        }
        cr
    }

    /// Index the numbered rules and attach their examples.
    fn parse_rules(&mut self, lines: &[&str]) {
        let mut current: Option<String> = None;
        for raw in lines {
            let line = pystr::strip(raw);
            if line.is_empty() {
                continue;
            }
            if let Some(example) = line.strip_prefix("Example: ") {
                if let Some(number) = &current
                    && let Some(rule) = self.rules.get_mut(number)
                {
                    rule.examples.push(example.to_owned());
                }
                continue;
            }
            let Some(caps) = RULE_RE.captures(line) else {
                continue;
            };
            let (number, text) = (&caps[1], &caps[2]);
            let existing = self.rules.get(number);
            if existing.is_some() && text.is_empty() {
                continue;
            }
            let longer =
                existing.is_none_or(|rule| text.chars().count() > rule.text.chars().count());
            if longer {
                // table-of-contents lines ("704. State-Based Actions")
                // precede the body; the longer body text wins
                let examples = existing.map(|r| r.examples.clone()).unwrap_or_default();
                self.rules.insert(
                    number.to_owned(),
                    Rule {
                        number: number.to_owned(),
                        text: text.to_owned(),
                        examples,
                    },
                );
            }
            current = Some(number.to_owned());
        }
    }

    /// Index the glossary: a term line followed by definition lines.
    fn parse_glossary(&mut self, lines: &[&str]) {
        let mut term: Option<String> = None;
        let mut buf: Vec<&str> = Vec::new();
        for raw in lines {
            let line = pystr::strip(raw);
            if line == "Credits" {
                break;
            }
            if line.is_empty() {
                if let Some(t) = term.take()
                    && !buf.is_empty()
                {
                    self.glossary.insert(t, buf.join(" "));
                }
                term = None;
                buf.clear();
                continue;
            }
            if term.is_none() {
                term = Some(line.to_owned());
            } else {
                buf.push(line);
            }
        }
        if let Some(t) = term
            && !buf.is_empty()
        {
            self.glossary.insert(t, buf.join(" "));
        }
    }

    // -- lookups ---------------------------------------------------------

    /// Whether the CR defines the given rule number (with or without a
    /// trailing dot).
    #[must_use]
    pub fn contains(&self, number: &str) -> bool {
        self.rules.contains_key(number.trim_end_matches('.'))
    }

    /// The rule with the given number (with or without a trailing dot).
    #[must_use]
    pub fn get(&self, number: &str) -> Option<&Rule> {
        self.rules.get(number.trim_end_matches('.'))
    }

    /// The normative text of the given rule.
    #[must_use]
    pub fn text(&self, number: &str) -> Option<&str> {
        self.get(number).map(|r| r.text.as_str())
    }

    /// Every rule number in sorted (code point) order.
    #[must_use]
    pub fn sorted_numbers(&self) -> Vec<&str> {
        let mut numbers: Vec<&str> = self.rules.keys().map(String::as_str).collect();
        numbers.sort_unstable();
        numbers
    }

    /// All `(rule number, example text)` pairs under a rule prefix.
    #[must_use]
    pub fn examples_under(&self, prefix: &str) -> Vec<(&str, &str)> {
        let base = prefix.trim_end_matches('.');
        let dotted = format!("{base}.");
        let mut out = Vec::new();
        for num in self.sorted_numbers() {
            let lettered = num
                .strip_prefix(base)
                .and_then(|rest| rest.chars().next())
                .is_some_and(|c| c.is_ascii_lowercase());
            if num == prefix || num.starts_with(&dotted) || lettered {
                let rule = &self.rules[num];
                out.extend(
                    rule.examples
                        .iter()
                        .map(|ex| (rule.number.as_str(), ex.as_str())),
                );
            }
        }
        out
    }

    /// The number of worked examples over all rules.
    #[must_use]
    pub fn example_count(&self) -> usize {
        self.rules.values().map(|r| r.examples.len()).sum()
    }

    /// The file name of the parsed CR (`cr.path.name`).
    #[must_use]
    pub fn file_name(&self) -> String {
        self.path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    }

    /// `"{file}: {n} rules, {m} examples, {k} glossary terms"`, the first
    /// line of `python -m mtgrules.cr`.
    #[must_use]
    pub fn summary_line(&self) -> String {
        format!(
            "{}: {} rules, {} examples, {} glossary terms",
            self.file_name(),
            self.rules.len(),
            self.example_count(),
            self.glossary.len()
        )
    }
}

// ---------------------------------------------------------------- registry

pub use mtgrules_registry::{RULE_IMPLEMENTATIONS, RuleImpl, UNSUPPORTED, Unsupported};

/// Rule number -> implementers (`module::path::name`), sorted; the Python
/// `RULE_IMPLEMENTATIONS` dict.
#[must_use]
pub fn implementations() -> BTreeMap<String, Vec<String>> {
    let mut map: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for entry in RULE_IMPLEMENTATIONS {
        for number in entry.numbers {
            map.entry(number.trim_end_matches('.').to_owned())
                .or_default()
                .push(entry.implementer.to_owned());
        }
    }
    for implementers in map.values_mut() {
        implementers.sort_unstable();
    }
    map
}

/// Rule number -> reason; the Python `UNSUPPORTED` dict. With two
/// declarations for one number the one earlier in `(file, line)` order
/// wins (the link order of the slice is not meaningful).
#[must_use]
pub fn unsupported_rules() -> BTreeMap<String, String> {
    let mut entries: Vec<&Unsupported> = UNSUPPORTED.iter().collect();
    entries.sort_by_key(|e| (e.file, e.line));
    let mut map = BTreeMap::new();
    for entry in entries {
        map.entry(entry.number.trim_end_matches('.').to_owned())
            .or_insert_with(|| entry.reason.to_owned());
    }
    map
}

/// A registered rule number the CR text does not define.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownRule {
    /// the number as registered
    pub number: String,
    /// `module::path::name`, or `unsupported!` for a declaration
    pub implementer: String,
    /// where it was written
    pub file: String,
    /// the line
    pub line: u32,
}

impl std::fmt::Display for UnknownRule {
    /// The Python messages (`@rule: unknown CR rule '999.99z'`,
    /// `unsupported(): unknown CR rule '999.99z'`) plus the location.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let number = repr_str(&self.number);
        if self.implementer == UNSUPPORTED_IMPLEMENTER {
            write!(
                f,
                "unsupported(): unknown CR rule {number} ({}:{})",
                self.file, self.line
            )
        } else {
            write!(
                f,
                "@rule: unknown CR rule {number} ({}, {}:{})",
                self.implementer, self.file, self.line
            )
        }
    }
}

/// The `implementer` of an [`UnknownRule`] that came from `unsupported!`.
pub const UNSUPPORTED_IMPLEMENTER: &str = "unsupported!";

/// Every registered rule number the CR does not define.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub struct RegistryError(pub Vec<UnknownRule>);

impl std::fmt::Display for RegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        for (i, unknown) in self.0.iter().enumerate() {
            if i > 0 {
                writeln!(f)?;
            }
            write!(f, "{unknown}")?;
        }
        Ok(())
    }
}

/// Validate every `#[rule]` number and `unsupported!` declaration against
/// `cr` — what the Python decorator does at import time, done once at
/// startup here.
///
/// # Errors
///
/// [`RegistryError`] listing every unknown number with its implementer
/// and source location, in `(file, line)` order.
pub fn check_registry(cr: &ComprehensiveRules) -> Result<(), RegistryError> {
    let mut unknown = Vec::new();
    for entry in RULE_IMPLEMENTATIONS {
        for number in entry.numbers {
            if !cr.contains(number) {
                unknown.push(UnknownRule {
                    number: (*number).to_owned(),
                    implementer: entry.implementer.to_owned(),
                    file: entry.file.to_owned(),
                    line: entry.line,
                });
            }
        }
    }
    for entry in UNSUPPORTED {
        if !cr.contains(entry.number) {
            unknown.push(UnknownRule {
                number: entry.number.to_owned(),
                implementer: UNSUPPORTED_IMPLEMENTER.to_owned(),
                file: entry.file.to_owned(),
                line: entry.line,
            });
        }
    }
    unknown.sort_by(|a, b| (&a.file, a.line, &a.number).cmp(&(&b.file, b.line, &b.number)));
    if unknown.is_empty() {
        Ok(())
    } else {
        Err(RegistryError(unknown))
    }
}

/// Human-readable implemented / unsupported / unclaimed report.
///
/// `prefixes` restricts the report to rule families the engine claims
/// (e.g. `["117", "601", "704"]`). For each top-level rule in a claimed
/// family, sub-rules are grouped as implemented (with implementers),
/// declared unsupported (with reason), or unclaimed. The layout is the
/// Python `coverage_report`'s; implementers are `module::path::name`.
#[must_use]
pub fn coverage_report(cr: &ComprehensiveRules, prefixes: &[&str]) -> String {
    let implementations = implementations();
    let unsupported = unsupported_rules();
    let mut lines = vec![
        format!(
            "CR file: {}  ({} rules parsed)",
            cr.file_name(),
            cr.rules.len()
        ),
        format!("implemented rule annotations: {}", implementations.len()),
        format!("declared unsupported: {}", unsupported.len()),
        String::new(),
    ];
    let sorted = cr.sorted_numbers();
    for prefix in prefixes {
        let dotted = format!("{prefix}.");
        let nums: Vec<&str> = sorted
            .iter()
            .copied()
            .filter(|n| *n == *prefix || n.starts_with(&dotted))
            .collect();
        let implemented: Vec<&str> = nums
            .iter()
            .copied()
            .filter(|n| implementations.contains_key(*n))
            .collect();
        let unsup: Vec<&str> = nums
            .iter()
            .copied()
            .filter(|n| unsupported.contains_key(*n))
            .collect();
        let rest: Vec<&str> = nums
            .iter()
            .copied()
            .filter(|n| !implementations.contains_key(*n) && !unsupported.contains_key(*n))
            .collect();
        let title = cr
            .rules
            .get(*prefix)
            .map(|r| r.text.as_str())
            .unwrap_or_default();
        lines.push(format!(
            "--- {prefix}. {title} [{} implemented / {} unsupported / {} unclaimed of {}]",
            implemented.len(),
            unsup.len(),
            rest.len(),
            nums.len()
        ));
        lines.extend(
            implemented
                .iter()
                .map(|n| format!("  OK   {n}  <- {}", implementations[*n].join(", "))),
        );
        lines.extend(
            unsup
                .iter()
                .map(|n| format!("  SKIP {n}  ({})", unsupported[*n])),
        );
        lines.extend(rest.iter().map(|n| format!("  ..   {n}")));
        lines.push(String::new());
    }
    lines.join("\n")
}

/// Rule families the engine claims to cover (used by the coverage report).
pub const CLAIMED_FAMILIES: &[&str] = &[
    "117", "302", "400", "405", "500", "502", "503", "504", "505", "508", "509", "510", "513",
    "514", "601", "602", "603", "604", "605", "606", "608", "611", "613", "614", "615", "616",
    "701", "702", "704", "903",
];

/// The families `python -m mtgrules.cr coverage` reports, in its order.
pub const COVERAGE_FAMILIES: &[&str] = &[
    "117", "601", "602", "603", "605", "606", "608", "611", "613", "614", "616", "704", "903",
    "510", "514", "502", "508", "509", "405", "116", "122",
];

/// The rules `python -m mtgrules.cr` prints as a smoke test.
pub const PROBES: &[&str] = &["100.1", "117.1", "601.2", "613.1", "704.5g", "903.10a"];

/// The probe lines of `python -m mtgrules.cr`: `"  {probe}: {text[:90]}..."`.
#[must_use]
pub fn probe_lines(cr: &ComprehensiveRules) -> Vec<String> {
    PROBES
        .iter()
        .map(|probe| {
            let head: String = cr
                .text(probe)
                .unwrap_or_default()
                .chars()
                .take(90)
                .collect();
            format!("  {probe}: {head}...")
        })
        .collect()
}

// ------------------------------------------------------------ process cache

static CR: OnceLock<Result<ComprehensiveRules, CrError>> = OnceLock::new();

/// The process-wide CR index: the newest CR file at the graph root
/// (`MTG_GRAPH_ROOT`, else the nearest checkout above the working
/// directory), parsed once and cached — the Python `get_cr()`.
///
/// # Errors
///
/// [`CrError`] when there is no graph root or no CR file in it; the same
/// error is returned on every call.
pub fn get_cr() -> Result<&'static ComprehensiveRules, CrError> {
    CR.get_or_init(|| {
        let root = mtgcards::graph::graph_root()?;
        ComprehensiveRules::load_newest(&root)
    })
    .as_ref()
    .map_err(Clone::clone)
}

#[cfg(test)]
mod tests {
    use super::{ComprehensiveRules, coverage_report};
    use std::path::PathBuf;

    /// Synthetic text in the CR layout (not CR text).
    const TEXT: &str = "\
Magic: The Gathering Comprehensive Rules

Contents

1. Game Concepts
100. General
704. State-Based Actions
Glossary
Credits

1. Game Concepts

100. General

100.1. These are the fake rules of a fake game.
Example: A worked example of 100.1.
100.1a Sub-rule a of 100.1.
100.2. Second rule.

704. State-Based Actions

704.5. The state-based actions are as follows:
704.5g A fake creature dies.
Example: One example for 704.5g.
Example: Another example for 704.5g.

Glossary

Ability
An ability does things.
Over two lines.

Abandon
To abandon.

Credits

Someone.
";

    fn parse() -> ComprehensiveRules {
        ComprehensiveRules::parse(TEXT, PathBuf::from("MagicCompRules-20260101.txt"))
    }

    #[test]
    fn body_text_wins_over_contents_and_examples_attach() {
        let cr = parse();
        assert_eq!(cr.text("100"), Some("General"));
        assert_eq!(cr.text("704."), Some("State-Based Actions"));
        assert_eq!(
            cr.text("100.1"),
            Some("These are the fake rules of a fake game.")
        );
        assert_eq!(
            cr.get("100.1").unwrap().examples,
            vec!["A worked example of 100.1."]
        );
        assert_eq!(cr.get("704.5g").unwrap().examples.len(), 2);
        assert!(cr.contains("100.1a"));
        assert!(!cr.contains("999.1"));
        assert_eq!(cr.rules.len(), 7);
        assert_eq!(cr.example_count(), 3);
    }

    #[test]
    fn glossary_and_examples_under() {
        let cr = parse();
        assert_eq!(cr.glossary.len(), 2);
        assert_eq!(
            cr.glossary["Ability"],
            "An ability does things. Over two lines."
        );
        assert_eq!(
            cr.examples_under("704"),
            vec![
                ("704.5g", "One example for 704.5g."),
                ("704.5g", "Another example for 704.5g.")
            ]
        );
        assert_eq!(cr.examples_under("100.1").len(), 1);
        assert_eq!(
            cr.summary_line(),
            "MagicCompRules-20260101.txt: 7 rules, 3 examples, 2 glossary terms"
        );
    }

    #[test]
    fn coverage_layout() {
        let cr = parse();
        let report = coverage_report(&cr, &["704"]);
        assert!(report.starts_with("CR file: MagicCompRules-20260101.txt  (7 rules parsed)\n"));
        assert!(
            report.contains("--- 704. State-Based Actions ["),
            "{report}"
        );
        // the synthetic "704." contents line is a rule the engine never
        // claims; 704.5g is implemented by the state-based actions
        assert!(report.contains("  ..   704\n"), "{report}");
        assert!(report.contains("  OK   704.5g  <- "), "{report}");
    }
}
