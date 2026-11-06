//! `mtg-simulate`: the CR-grounded Commander matchup simulator (the
//! Python `mtgrules.cli`, `simulate_matchup`).
//!
//! 2-4 deck files are required: txt decklists (card data fetched from the
//! Scryfall API, falling back to the knowledge graph offline; `--offline`
//! skips the lookups entirely) or `.ttl` deck instance graphs
//! (knowledge-graph content only, no network access). Statistics can be
//! pooled over multiple RNG seeds (`--seeds`) to separate matchup signal
//! from seed variance.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::Parser;
use mtgcards::CardDatabase;
use mtgcards::scryfall::ScryfallClient;
use mtgsim::cli::{
    Exit, MAX_DECKS, MIN_DECKS, RunSettings, audit_unknown_cards, deck_paths, final_report_lines,
    load_decks, open_database, parse_profiles, parse_seeds, run_matches, scryfall_warning,
    txt_card_names,
};
use mtgsim::{EXIT_USAGE, load_cr, resolve_graph_root, startup_check};

const PROGRAM: &str = "simulate_matchup";

#[derive(Parser)]
#[command(
    name = PROGRAM,
    version,
    about = "CR-grounded Commander matchup simulator (2-4 decks).",
    after_help = "AI profiles: aggressive, control, default"
)]
struct Args {
    /// 2-4 deck files: txt decklists ('N Card Name' lines, // comments,
    /// commander under a '// Commander' section header; card data fetched
    /// from the Scryfall API) or .ttl deck instance graphs (card data
    /// from the knowledge graph only)
    decks: Vec<String>,
    /// games per seed
    #[arg(long, default_value_t = 20, allow_negative_numbers = true)]
    games: i64,
    /// base RNG seed
    #[arg(long, default_value_t = 42, allow_negative_numbers = true)]
    seed: i64,
    /// number of consecutive seeds starting at --seed, or an explicit
    /// comma-separated list (e.g. '5' or '1,7,100'); statistics are
    /// pooled and reported per seed
    #[arg(
        long,
        default_value = "1",
        value_name = "N|LIST",
        allow_hyphen_values = true
    )]
    seeds: String,
    /// the turn cap
    #[arg(long, default_value_t = 40, allow_negative_numbers = true)]
    turn_cap: i64,
    /// one-line log per game
    #[arg(long)]
    verbose: bool,
    /// write per-game records (JSONL)
    #[arg(long, value_name = "PATH")]
    log_file: Option<PathBuf>,
    /// JSON file overriding card definitions for unreleased/unverified
    /// cards (opt-in; by default all card data comes from the knowledge
    /// graph)
    #[arg(long, value_name = "PATH")]
    custom_cards: Option<PathBuf>,
    /// skip the Scryfall lookups for txt decklists and resolve all cards
    /// from the knowledge graph (hermetic runs, e.g. CI)
    #[arg(long)]
    offline: bool,
    /// additional card TTL graph merged on top of sets/*.ttl
    /// (repeatable), e.g. an out-of-collection MagicExternalCards.ttl
    /// kept next to private deck graphs
    #[arg(long, value_name = "FILE.ttl")]
    extra_cards: Vec<PathBuf>,
    /// AI profile per seat, e.g. 1=aggressive
    #[arg(long, value_name = "SEAT=NAME")]
    profile: Vec<String>,
    /// watch a single game live in the TUI (implies --games 1)
    #[arg(long)]
    watch: bool,
    /// record per-game visualization events + snapshots (JSONL) for
    /// --replay
    #[arg(long, value_name = "PATH")]
    viz_file: Option<PathBuf>,
    /// replay a recorded --viz-file in the TUI (deck arguments are not
    /// needed)
    #[arg(long, value_name = "PATH")]
    replay: Option<PathBuf>,
    /// game number to replay from the --replay file (1-based)
    #[arg(
        long,
        default_value_t = 1,
        value_name = "N",
        allow_negative_numbers = true
    )]
    game: i64,
    /// the knowledge-graph root (default: MTG_GRAPH_ROOT, else the
    /// checkout above the working directory)
    #[arg(long, value_name = "DIR")]
    graph_root: Option<PathBuf>,
    /// the Comprehensive Rules text (default: the newest
    /// MagicCompRules-*.txt at the graph root)
    #[arg(long, value_name = "FILE")]
    cr_file: Option<PathBuf>,
}

/// A usage error in argparse's words: exit 2.
fn usage_error(message: &str) -> ExitCode {
    eprintln!("{PROGRAM}: error: {message}");
    ExitCode::from(EXIT_USAGE)
}

fn exit(e: &Exit) -> ExitCode {
    if e.code == EXIT_USAGE {
        return usage_error(&e.message);
    }
    eprintln!("{}", e.message);
    ExitCode::from(e.code)
}

fn run(args: &Args) -> Result<(), Exit> {
    if let Some(path) = &args.replay {
        return mtgviz::replay::replay_file(path, args.game).map_err(Exit::error);
    }

    // argument validation argparse cannot express: usage errors, exit 2
    let seeds = parse_seeds(args.seed, &args.seeds).map_err(|err| {
        Exit::usage(format!(
            "invalid --seeds value {}: {err}",
            pycompat::pyrepr::repr_str(&args.seeds)
        ))
    })?;
    if args.games < 1 {
        return Err(Exit::usage("--games must be >= 1"));
    }
    if args.turn_cap < 1 {
        return Err(Exit::usage("--turn-cap must be >= 1"));
    }
    let games = u64::try_from(args.games).unwrap_or(1);
    let turn_cap = u32::try_from(args.turn_cap).unwrap_or(u32::MAX);

    // the #[rule] registry against the Comprehensive Rules, like the
    // Python decorators at import time
    let cr = load_cr(args.graph_root.as_deref(), args.cr_file.as_deref()).map_err(Exit::error)?;
    startup_check(&cr).map_err(Exit::error)?;

    let paths = deck_paths(&args.decks, MIN_DECKS, MAX_DECKS)?;
    let root = resolve_graph_root(args.graph_root.as_deref()).map_err(Exit::error)?;
    let mut db: CardDatabase =
        open_database(&root, args.custom_cards.as_deref(), &args.extra_cards)?;
    let decks = load_decks(&paths, &db, &mut |w| eprintln!("{w}"))?;
    if !args.offline {
        let names = txt_card_names(&decks);
        if !names.is_empty() {
            let mut client = ScryfallClient::live();
            let failed = db
                .resolve_scryfall(&names, &mut client)
                .map_err(|e| Exit::error(format!("cannot load card data: {e}")))?;
            if !failed.is_empty() {
                eprintln!("{}", scryfall_warning(&failed));
            }
        }
    }
    let profiles = parse_profiles(&args.profile, decks.len())?;
    if let Some(warning) = audit_unknown_cards(&decks, &mut db) {
        eprintln!("{warning}");
    }

    if args.watch {
        let options = mtgviz::live::WatchOptions {
            turn_cap,
            profiles: profiles.clone(),
            viz_path: args.viz_file.clone(),
        };
        mtgviz::live::watch_game(&decks, &mut db, seeds[0], &options).map_err(Exit::error)?;
        return Ok(());
    }

    let settings = RunSettings {
        seeds: &seeds,
        games,
        turn_cap,
        profiles: &profiles,
        verbose: args.verbose,
    };
    let viz_writer = match &args.viz_file {
        Some(path) => Some(
            mtgviz::VizWriter::open(path)
                .map_err(|e| Exit::error(pycompat::pyio::os_error(&e, path).to_string()))?,
        ),
        None => None,
    };
    let mut recorder_for =
        |game_no: u64, seed: i64| viz_writer.as_ref().map(|w| w.recorder(game_no, seed));
    let agg = run_matches(&decks, &mut db, &settings, &mut recorder_for, &mut |line| {
        println!("{line}");
    });
    if let (Some(writer), Some(path)) = (&viz_writer, &args.viz_file) {
        writer
            .close()
            .map_err(|e| Exit::error(pycompat::pyio::os_error(&e, path).to_string()))?;
        println!(
            " viz recording written to {} (replay with --replay {})",
            path.display(),
            path.display()
        );
    }
    let agg = agg.ok_or_else(|| Exit::error("no games were played (check --games)"))?;
    for line in final_report_lines(&decks, &mut db, &seeds, games, &agg) {
        println!("{line}");
    }
    if let Some(path) = &args.log_file {
        agg.write_jsonl(path)
            .map_err(|e| Exit::error(pycompat::pyio::os_error(&e, path).to_string()))?;
        println!(" per-game JSONL written to {}", path.display());
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
