//! `mtg-selfplay`: self-play round-robin / grid tuning of the AI
//! profiles (the Python `mtgrules.selfplay`).
//!
//! With one deck file, a mirror match is played (the second seat gets a
//! '#2'-suffixed copy). Deck files are txt decklists or .ttl deck
//! instance graphs, like for `mtg-simulate`.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::Parser;
use mtgrules::selfplay::{SelfplaySettings, clone_deck, grid_tune_lines, round_robin_lines};
use mtgsim::cli::{Exit, deck_paths, load_decks, open_database};
use mtgsim::{EXIT_USAGE, load_cr, resolve_graph_root, startup_check};

const PROGRAM: &str = "mtgrules.selfplay";

#[derive(Parser)]
#[command(
    name = PROGRAM,
    version,
    about = "Self-play round-robin / grid tuning of AI profiles."
)]
struct Args {
    /// 1 deck (mirror) or 2 decks: txt decklists or .ttl deck graphs
    #[arg(required = true)]
    decks: Vec<String>,
    /// games per seed per seating
    #[arg(long, default_value_t = 10, allow_negative_numbers = true)]
    games: i64,
    /// the first seed
    #[arg(long, default_value_t = 42, allow_negative_numbers = true)]
    seed: i64,
    /// consecutive seeds starting at --seed
    #[arg(long, default_value_t = 1, allow_negative_numbers = true)]
    seeds: i64,
    /// the turn cap
    #[arg(long, default_value_t = 40, allow_negative_numbers = true)]
    turn_cap: i64,
    /// base preset for --tune
    #[arg(long, default_value = "default")]
    base: String,
    /// grid axis, repeatable (e.g. aggression=0.7,1.5)
    #[arg(long, value_name = "KNOB=v1,v2")]
    tune: Vec<String>,
    /// additional card TTL graph merged on top of sets/*.ttl (repeatable)
    #[arg(long, value_name = "FILE.ttl")]
    extra_cards: Vec<PathBuf>,
    /// the knowledge-graph root (default: MTG_GRAPH_ROOT, else the
    /// checkout above the working directory)
    #[arg(long, value_name = "DIR")]
    graph_root: Option<PathBuf>,
    /// the Comprehensive Rules text (default: the newest
    /// MagicCompRules-*.txt at the graph root)
    #[arg(long, value_name = "FILE")]
    cr_file: Option<PathBuf>,
}

fn exit(e: &Exit) -> ExitCode {
    if e.code == EXIT_USAGE {
        eprintln!("{PROGRAM}: error: {}", e.message);
    } else {
        eprintln!("{}", e.message);
    }
    ExitCode::from(e.code)
}

fn run(args: &Args) -> Result<(), Exit> {
    if args.games < 1 {
        return Err(Exit::usage("--games must be >= 1"));
    }
    if args.seeds < 1 {
        return Err(Exit::usage("--seeds must be >= 1"));
    }
    if args.turn_cap < 1 {
        return Err(Exit::usage("--turn-cap must be >= 1"));
    }
    let cr = load_cr(args.graph_root.as_deref(), args.cr_file.as_deref()).map_err(Exit::error)?;
    startup_check(&cr).map_err(Exit::error)?;
    let paths = deck_paths(&args.decks, 1, 2)?;
    let root = resolve_graph_root(args.graph_root.as_deref()).map_err(Exit::error)?;
    let mut db = open_database(&root, None, &args.extra_cards)?;
    let mut decks = load_decks(&paths, &db, &mut |w| eprintln!("{w}"))?;
    if decks.len() == 1 {
        let d = decks.remove(0);
        decks = vec![clone_deck(&d, "#1"), clone_deck(&d, "#2")];
    }
    let settings = SelfplaySettings {
        games: u64::try_from(args.games).unwrap_or(1),
        seeds: (0..args.seeds).map(|i| args.seed + i).collect(),
        turn_cap: u32::try_from(args.turn_cap).unwrap_or(u32::MAX),
    };
    let lines = if args.tune.is_empty() {
        round_robin_lines(&decks, &mut db, &settings)
    } else {
        grid_tune_lines(&decks, &mut db, &settings, &args.base, &args.tune).map_err(Exit::error)?
    };
    for line in lines {
        println!("{line}");
    }
    Ok(())
}

fn main() -> ExitCode {
    let args = Args::parse();
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => exit(&e),
    }
}
