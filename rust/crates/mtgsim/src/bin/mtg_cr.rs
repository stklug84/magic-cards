//! `mtg-cr`: the Comprehensive Rules toolchain on the command line.
//!
//! Without a subcommand it does what `python -m mtgrules.cr` does: one
//! summary line (`<file>: N rules, M examples, K glossary terms`) and a
//! few probe rules. `coverage` is `python -m mtgrules.cr coverage`: the
//! implemented / unsupported / unclaimed report over the claimed rule
//! families. `check`, `show`, `examples` and `glossary` are lookups the
//! Python module never had a front end for.
//!
//! The CR text is not redistributable: it is the newest
//! `MagicCompRules-*.txt` at the graph root (`--graph-root`,
//! `MTG_GRAPH_ROOT`, or the checkout above the working directory) or the
//! file named by `--cr-file`. `--help` and `--version` never read it.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use mtgrules::cr::{self, ComprehensiveRules};

const PROGRAM: &str = "mtg-cr";

#[derive(Parser)]
#[command(name = PROGRAM, version, about = "Comprehensive Rules index, #[rule] registry check and coverage report")]
struct Cli {
    /// Graph root holding MagicCompRules-*.txt (default: MTG_GRAPH_ROOT,
    /// else the magic-cards checkout above the working directory)
    #[arg(long, value_name = "DIR", global = true)]
    graph_root: Option<PathBuf>,

    /// A specific Comprehensive Rules text file (overrides the graph root)
    #[arg(long, value_name = "FILE", global = true)]
    cr_file: Option<PathBuf>,

    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Implemented / unsupported / unclaimed report per rule family
    /// (default: the families the engine claims)
    Coverage {
        /// Rule families, e.g. 117 601 704
        families: Vec<String>,
    },
    /// Validate every #[rule] annotation and unsupported! declaration of
    /// this binary against the CR text (exit 1 on an unknown number)
    Check,
    /// Print the text (and worked examples) of rules
    Show {
        /// Rule numbers, e.g. 704.5g
        #[arg(required = true)]
        numbers: Vec<String>,
    },
    /// Print the worked examples under a rule prefix, e.g. 613
    Examples {
        /// A rule number or family
        prefix: String,
    },
    /// Print a glossary definition
    Glossary {
        /// The term (case-insensitive)
        term: Vec<String>,
    },
}

fn show(cr: &ComprehensiveRules, numbers: &[String]) -> Result<Vec<String>, String> {
    let mut lines = Vec::new();
    for number in numbers {
        let rule = cr
            .get(number)
            .ok_or_else(|| format!("unknown CR rule {number:?}"))?;
        lines.push(format!("{}. {}", rule.number, rule.text));
        lines.extend(rule.examples.iter().map(|ex| format!("  Example: {ex}")));
    }
    Ok(lines)
}

fn glossary(cr: &ComprehensiveRules, words: &[String]) -> Result<Vec<String>, String> {
    let term = words.join(" ");
    if term.is_empty() {
        return Err("glossary needs a term".to_owned());
    }
    let wanted = term.to_lowercase();
    let found = cr
        .glossary
        .iter()
        .find(|(t, _)| t.to_lowercase() == wanted)
        .ok_or_else(|| format!("no glossary entry {term:?}"))?;
    Ok(vec![found.0.clone(), found.1.clone()])
}

fn run(cli: &Cli) -> Result<Vec<String>, String> {
    let cr = mtgsim::load_cr(cli.graph_root.as_deref(), cli.cr_file.as_deref())?;
    match &cli.command {
        None => {
            let mut lines = vec![cr.summary_line()];
            lines.extend(cr::probe_lines(&cr));
            Ok(lines)
        }
        Some(Command::Coverage { families }) => {
            let families: Vec<&str> = if families.is_empty() {
                cr::COVERAGE_FAMILIES.to_vec()
            } else {
                families.iter().map(String::as_str).collect()
            };
            Ok(vec![cr.summary_line(), cr::coverage_report(&cr, &families)])
        }
        Some(Command::Check) => {
            mtgsim::startup_check(&cr)?;
            Ok(vec![format!(
                "{}: {} rule annotations and {} unsupported declarations resolve",
                cr.file_name(),
                cr::RULE_IMPLEMENTATIONS.len(),
                cr::UNSUPPORTED.len()
            )])
        }
        Some(Command::Show { numbers }) => show(&cr, numbers),
        Some(Command::Examples { prefix }) => {
            let examples = cr.examples_under(prefix);
            if examples.is_empty() {
                return Err(format!("no worked examples under {prefix:?}"));
            }
            Ok(examples
                .iter()
                .map(|(n, ex)| format!("{n}: {ex}"))
                .collect())
        }
        Some(Command::Glossary { term }) => glossary(&cr, term),
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match run(&cli) {
        Ok(lines) => {
            for line in lines {
                println!("{line}");
            }
            ExitCode::SUCCESS
        }
        Err(message) => mtgsim::fail(PROGRAM, &message),
    }
}
