"""Download the MTG Arena collection from Untapped.gg with the Chrome browser.

Arena has no collection export and its log has not listed owned cards since
2021, so the only copy a browser can reach is the one the Untapped.gg
Companion (Windows / macOS, runs next to the game) syncs to the Untapped.gg
account.  This drives a real Chrome on a dedicated profile: ``login`` opens a
plain window to sign in once; ``probe`` and ``pull`` attach to that profile
over CDP, open the collection page and read the one response the page
requests itself.  The session cookie never leaves Chrome; the account ids are
cached outside the repository.

    uv run scripts/arena_collection.py login           # once: sign in, then Cmd+Q
    uv run scripts/arena_collection.py probe           # signed in? field names only
    uv run scripts/arena_collection.py pull --dry-run  # fetch, resolve, show changes
    uv run scripts/arena_collection.py pull            # write the three files
    uv run scripts/arena_collection.py export          # offline: rewrite CSV and TXT
    uv run scripts/arena_collection.py resolve         # offline: resolve anew

Each owned Arena card id (grpid) is resolved against Untapped's dump of
Arena's card database and Scryfall's bulk data (grpid == Scryfall arena_id),
both public and cached under ~/.cache/mtga-collection/catalog; a card the
lookups cannot place is picked by hand in arena-picks.json.  The files are
written to the repository root: arena-collection.json (the generator's
input), arena-collection.moxfield.csv and arena-collection.txt, all three
local and untracked like collection.csv.  They are sorted and carry no
timestamps or account data, so an unchanged collection gives no diff.  A
collection that shrank is refused unless --allow-shrink is given: Arena
collections only grow, so a smaller one means a stale or partial sync.

The Moxfield CSV is Moxfield's own collection format (Archidekt's importer
has a preset for it); ManaBox and other Arena-aware importers read the TXT.
Neither replaces collection.csv, the inventory of the paper collection: the
generator reads the JSON and writes MagicArenaCollection.ttl from it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgarena import render, untapped
from mtgarena.catalog import Catalog, Printing, load_catalog, load_picks
from mtgarena.chrome import (
    ChromeConfig,
    chrome_session,
    launch_chrome,
    profile_owner,
    recorded_chrome,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

REPO = Path(__file__).resolve().parents[2]
#: where the three files go: the repository root, next to collection.csv
OUT_DIR = REPO
#: printings picked by hand, tracked in the repository (no private data)
PICKS_NAME = "arena-picks.json"
#: notes list at most this many printings, then say how many more there are
NOTE_SAMPLE = 12
REJECTED_SAMPLE = 4
#: more copies than this of a non-basic printing is worth a note
PLAYSET = 4
#: set by the re-exec of isolate_from_ambient_pythonpath
ISOLATED_ENV = "_MTGA_COLLECTION_ISOLATED"


def say(*lines: str) -> None:
    """Write *lines* to standard output, one per line."""
    sys.stdout.write("".join(f"{line}\n" for line in lines))


def isolate_from_ambient_pythonpath(script: Path) -> None:
    """Re-exec *script* with the PYTHON* variables ignored if PYTHONPATH is set.

    A global PYTHONPATH of Homebrew site-packages shadows the environment uv
    builds for the script.
    """
    if os.environ.get(ISOLATED_ENV) or not os.environ.get("PYTHONPATH"):
        return
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    environment[ISOLATED_ENV] = "1"
    argv = [sys.executable, "-E", str(script), *sys.argv[1:]]
    os.execve(sys.executable, argv, environment)  # noqa: S606 - re-runs this very script without PYTHONPATH  # nosec B606


def _shown(path: Path) -> Path:
    """Return *path* relative to the repository when it lies inside it."""
    return path.relative_to(REPO) if path.is_relative_to(REPO) else path


# -------------------------------------------- the account, cached outside the repo


def account_file(cfg: ChromeConfig) -> Path:
    """Return the file caching the account ids, next to the Chrome profile."""
    return cfg.state_dir / "account.json"


def load_account_cache(path: Path) -> dict[str, Any] | None:
    """Return the cached account ids; None when there are none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("user_id") and data.get("player_id"):
        return data
    return None


def save_account_cache(
    path: Path,
    account: untapped.Account,
    players: list[untapped.Player] | None = None,
) -> None:
    """Cache the account ids (and the linked players) outside the repository."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    data = {
        "user_id": account.user_id,
        "player_id": account.player_id,
        "player_name": account.player_name,
        "players": [asdict(p) for p in players] if players else None,
    }
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _cached_player(
    cache: dict[str, Any],
    wanted: str,
) -> untapped.Account | None:
    """Return the cached account of the player --player names.

    None when the cache predates the player list: the page has to be asked.
    """
    players = [
        untapped.Player(str(p["player_id"]), p.get("player_name"))
        for p in cache.get("players") or []
        if p.get("player_id")
    ]
    if not players:
        return None
    player = untapped.choose_player(players, wanted)
    return untapped.Account(cache["user_id"], player.player_id, player.player_name)


def cached_account(
    args: argparse.Namespace,
    cfg: ChromeConfig,
) -> untapped.Account | None:
    """Return the account from --profile-url or the cache; None to ask the page."""
    cache = load_account_cache(account_file(cfg))
    if args.profile_url:
        parsed = untapped.parse_profile_url(args.profile_url)
        if not parsed:
            url = args.profile_url
            msg = f"--profile-url: no /profile/<user_id>/<player_id> in {url!r}"
            raise SystemExit(msg)
        user_id, player_id = parsed
        same = cache and cache.get("player_id") == player_id
        name = cache.get("player_name") if cache and same else None
        return untapped.Account(user_id, player_id, name)
    if not cache:
        return None
    if args.player:
        return _cached_player(cache, args.player)
    return untapped.Account(
        cache["user_id"], cache["player_id"], cache.get("player_name")
    )


# ---------------------------------------------------------------------- commands


def chrome_config(args: argparse.Namespace) -> ChromeConfig:
    """Return the Chrome configuration of the command line."""
    return ChromeConfig(
        binary=args.chrome_binary,
        state_dir=args.state_dir,
        profile_dir=args.profile_dir,
    )


def cmd_login(args: argparse.Namespace) -> int:
    """Open a plain Chrome on the tool's profile to sign in to Untapped.gg."""
    cfg = chrome_config(args)
    if profile_owner(cfg) or recorded_chrome(cfg):
        say(
            "A Chrome window on the tool's profile is already open; sign in there, "
            "then quit it with Cmd+Q.",
        )
        return 0
    proc = launch_chrome(cfg, untapped.ORIGIN, debug=False)
    say(
        "A separate Chrome window opened on the tool's own profile",
        f"({cfg.profile_dir}). Sign in to Untapped.gg there (the Sign-in button top",
        "right; Google / Discord / Twitch sign-in work in this window). Once your",
        "avatar shows, QUIT THAT CHROME WITH Cmd+Q -- Chrome only saves the login on",
        "a clean quit (not when the window is closed by this tool). Then run `probe`",
        "or `pull`.",
    )
    if args.wait:
        say("waiting for that Chrome to quit ...")
        proc.wait()
        say("Chrome quit; the login is saved if you were signed in.")
    return 0


def _account_on_page(
    page: Page,
    args: argparse.Namespace,
    cfg: ChromeConfig,
) -> tuple[untapped.Account, str]:
    """Discover the account on the page, else take --profile-url or the cache."""
    try:
        account, source = untapped.discover_account(page, args.player)
    except SystemExit:
        fallback = cached_account(args, cfg)
        if fallback is None:
            raise
        return fallback, "--profile-url" if args.profile_url else "cache"
    save_account_cache(account_file(cfg), account)
    return account, source or "page"


def _fetch(
    page: Page,
    account: untapped.Account,
    args: argparse.Namespace,
) -> tuple[object, untapped.ResponseInfo]:
    """Return the collection response: captured from the page, or fetched."""
    if args.fetch:
        return untapped.fetch_collection_direct(page, account)
    return untapped.fetch_collection(page, account)


def cmd_probe(args: argparse.Namespace) -> int:
    """Check the login and print the collection response's field names."""
    cfg = chrome_config(args)
    with chrome_session(cfg, untapped.ORIGIN) as page:
        account, source = _account_on_page(page, args, cfg)
        say(
            f"signed in: yes (account found via {source})",
            f"Arena account: {account.player_name or '?'} "
            f"(player id {account.player_id})",
        )
        payload, info = _fetch(page, account, args)
    say(f"collection response: HTTP {info['status']} ({info['how']}) {info['url']}")
    meta = untapped.collection_meta(payload)
    if meta:
        say(
            "fields next to the card list: "
            + ", ".join(f"{k}={v}" for k, v in meta.items())
        )
    shape = untapped.shape(payload)
    say(f"response shape: {json.dumps(shape, ensure_ascii=False)[:600]}")
    try:
        entries = untapped.validate_collection(payload)
    except ValueError as exc:
        say(f"validation: FAILED -- {exc}")
    else:
        cards = sum(e.quantity for e in entries)
        say(f"validation: ok -- {len(entries)} owned printings, {cards} cards")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    dump = cfg.state_dir / f"probe-{datetime.now(tz=UTC):%Y%m%d-%H%M%S}.json"
    record = {"info": info, "meta_keys": sorted(meta), "shape": shape}
    dump.write_text(json.dumps(record, indent=1), encoding="utf-8")
    say(f"field shape (names and types only) written to {dump}")
    return 0


def _sample(items: list[str], limit: int, separator: str) -> str:
    """Join the first *limit* items and say how many more there are."""
    shown = separator.join(items[:limit])
    return shown + (
        f"{separator}... ({len(items) - limit} more)" if len(items) > limit else ""
    )


def _resolution_notes(
    snapshot: render.Snapshot,
    printings: list[Printing],
    catalog: Catalog,
) -> list[str]:
    """Return the notes about what the resolution could not settle."""
    notes = []
    rejected = [
        (card, hit)
        for card in snapshot["cards"]
        if card["source"] != "scryfall"
        and (hit := catalog.rejected_arena_id(card["arena_id"])) is not None
    ]
    if rejected:
        shown = "; ".join(
            f"Arena {c['arena_name']!r} vs Scryfall {s.name!r} "
            f"({s.set.upper()} {s.collector_number})"
            for c, s in rejected[:REJECTED_SAMPLE]
        )
        more = "; ..." if len(rejected) > REJECTED_SAMPLE else ""
        notes.append(
            "note: Scryfall's arena_id entry names a different card than Arena for "
            f"{len(rejected)} printings (Scryfall data errors, not used): "
            f"{shown}{more}",
        )
    arena_only = [render.label(c) for c in snapshot["cards"] if c["source"] == "arena"]
    if arena_only:
        notes.append(
            "known to Arena only, no single Scryfall printing found (left out of the "
            f"Moxfield CSV unless picked in {PICKS_NAME}): "
            + _sample(arena_only, NOTE_SAMPLE, ", "),
        )
    owned = {c["arena_id"] for c in snapshot["cards"]}
    unused = [
        f"{p.arena_id} {p.name}"
        for p in sorted(catalog.picks.values(), key=lambda p: p.arena_id)
        if p.arena_id not in owned
    ]
    if unused:
        notes.append(
            f"note: {PICKS_NAME} picks printings for cards not in the collection: "
            + ", ".join(unused),
        )
    unknown = [
        str(c["arena_id"]) for c in snapshot["cards"] if c["source"] == "unknown"
    ]
    if unknown:
        notes.append(
            "grpids the Arena card dump does not know (left out of the CSV and TXT): "
            + ", ".join(unknown),
        )
    odd = sum(1 for p in printings if p.token or p.secondary)
    if odd:
        notes.append(
            f"note: {odd} entries are tokens or back faces; they stay in the JSON"
        )
    many = [
        render.label(c)
        for c in snapshot["cards"]
        if not c["basic"] and c["quantity"] > PLAYSET
    ]
    if many:
        notes.append(
            f"note: more than {PLAYSET} copies of {len(many)} non-basic printings, "
            f"e.g. {many[0]}",
        )
    return notes


def _use_picks(catalog: Catalog, args: argparse.Namespace) -> None:
    """Give the catalog the printings picked in the picks file.

    Checked before Chrome opens or anything is resolved: a pick that does
    not hold stops the run.
    """
    path = args.picks
    catalog.picks = load_picks(path)
    problems = catalog.pick_problems()
    if problems:
        msg = (
            f"{path}: picks that do not hold with the current card data:\n  "
            + "\n  ".join(
                problems,
            )
        )
        raise SystemExit(msg)
    if catalog.picks:
        plural = "s" if len(catalog.picks) > 1 else ""
        say(f"{len(catalog.picks)} printing{plural} picked by hand in {path.name}")


def _resolve_and_report(
    entries: list[untapped.Entry],
    catalog: Catalog,
    previous: render.Snapshot | None,
) -> tuple[render.Snapshot, list[str]]:
    """Resolve the owned cards; print the summary, the notes and the changes."""
    printings = [catalog.resolve(e.arena_id, e.quantity) for e in entries]
    snapshot = render.build_snapshot(printings)
    say(
        "",
        *render.summary_lines(snapshot),
        *_resolution_notes(snapshot, printings, catalog),
    )
    report = render.changes(snapshot, previous)
    say("", *report)
    return snapshot, report


def _write_outputs(
    args: argparse.Namespace,
    snapshot: render.Snapshot,
    report: list[str],
) -> int:
    """Write the three files (``pull`` and ``resolve``), unless --dry-run."""
    if args.dry_run:
        say(f"\ndry run: nothing written to {args.out_dir}")
        return 0
    written = render.write_outputs(
        args.out_dir,
        snapshot,
        include_basics=args.include_basics,
        exclude_digital=args.exclude_digital,
    )
    if not written:
        say(f"\n{args.out_dir}: unchanged")
        return 0
    if report == ["no changes"]:
        say(
            "(same cards and quantities; the files pick up refreshed card data -- "
            "names, sets, Scryfall ids)",
        )
    say(*(f"wrote {_shown(path)}" for path in written))
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Fetch the collection from Untapped.gg, resolve it, write the three files."""
    cfg = chrome_config(args)
    previous = render.load_snapshot(args.out_dir / render.SNAPSHOT_NAME)
    catalog = load_catalog(
        args.catalog_dir,
        scryfall=not args.no_scryfall,
        max_age=args.max_age * 3600,
    )
    if not args.no_scryfall:  # a pick names a Scryfall printing
        _use_picks(catalog, args)
    account = cached_account(args, cfg)
    with chrome_session(cfg, "about:blank" if account else untapped.ORIGIN) as page:
        if account is None:
            account, _ = _account_on_page(page, args, cfg)
        elif args.profile_url:
            save_account_cache(account_file(cfg), account)
        say(f"Arena account: {account.player_name or account.player_id}")
        payload, info = _fetch(page, account, args)
    say(f"collection response: HTTP {info['status']} ({info['how']})")
    meta = untapped.collection_meta(payload)
    if meta:
        say("response fields: " + ", ".join(f"{k}={v}" for k, v in meta.items()))
    try:
        entries = untapped.validate_collection(payload)
    except ValueError as exc:
        msg = f"collection response: {exc}; {untapped.RUN_PROBE}"
        raise SystemExit(msg) from None
    snapshot, report = _resolve_and_report(entries, catalog, previous)
    smaller = render.shrink(snapshot, previous)
    if smaller and not args.allow_shrink:
        msg = (
            f"\nrefusing to write: the collection shrank from {smaller[0]} to "
            f"{smaller[1]} cards. Arena collections only grow, so this looks like a "
            "stale or partial Companion sync. Run the Companion next to Arena and "
            "retry, or pass --allow-shrink if the drop is real."
        )
        raise SystemExit(msg)
    return _write_outputs(args, snapshot, report)


def snapshot_entries(snapshot: render.Snapshot) -> list[untapped.Entry]:
    """Return the owned (grpid, quantity) pairs a snapshot was built from.

    Checked like a collection response.
    """
    rows = [
        {"grpid": c.get("arena_id"), "quantity": c.get("quantity")}
        if isinstance(c, dict)
        else c
        for c in snapshot["cards"]
    ]
    try:
        return untapped.validate_collection({"cards": rows})
    except ValueError as exc:
        msg = f"{render.SNAPSHOT_NAME}: {exc}"
        raise SystemExit(msg) from None


def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve the snapshot's cards anew and rewrite the three files."""
    previous = render.load_snapshot(args.out_dir / render.SNAPSHOT_NAME)
    if previous is None:
        msg = f"{args.out_dir / render.SNAPSHOT_NAME} does not exist; run `pull` first"
        raise SystemExit(msg)
    entries = snapshot_entries(previous)
    catalog = load_catalog(args.catalog_dir, max_age=args.max_age * 3600)
    _use_picks(catalog, args)
    cards = sum(e.quantity for e in entries)
    say(
        f"{len(entries)} printings, {cards} cards from {render.SNAPSHOT_NAME} "
        "(nothing fetched from Untapped.gg)",
    )
    snapshot, report = _resolve_and_report(entries, catalog, previous)
    return _write_outputs(args, snapshot, report)


def cmd_export(args: argparse.Namespace) -> int:
    """Rewrite the CSV and the TXT from the snapshot as it is (offline)."""
    snapshot = render.load_snapshot(args.out_dir / render.SNAPSHOT_NAME)
    if snapshot is None:
        msg = f"{args.out_dir / render.SNAPSHOT_NAME} does not exist; run `pull` first"
        raise SystemExit(msg)
    say(*render.summary_lines(snapshot))
    written = render.write_outputs(
        args.out_dir,
        snapshot,
        include_basics=args.include_basics,
        exclude_digital=args.exclude_digital,
    )
    say(*(f"wrote {_shown(path)}" for path in written))
    if not written:
        say("CSV and TXT already match the snapshot")
    return 0


# -------------------------------------------------------------------------- main


def _add_account_args(parser: argparse.ArgumentParser) -> None:
    """Add the options that choose the Untapped.gg account."""
    parser.add_argument(
        "--profile-url",
        help="your Untapped.gg profile or collection URL (once, if the page hides "
        "the account)",
    )
    parser.add_argument(
        "--player",
        help="Arena player name or id when several are linked to the login",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="issue the collection request from the page instead of capturing the "
        "page's own",
    )


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    """Add the options that shape the CSV and TXT."""
    parser.add_argument(
        "--include-basics",
        action="store_true",
        help="keep basic lands in the CSV and TXT",
    )
    parser.add_argument(
        "--exclude-digital",
        action="store_true",
        help="leave digital-only printings (Alchemy, Arena-only sets) out of the "
        "Moxfield CSV, for importers that do not know them",
    )


def _add_card_data_args(parser: argparse.ArgumentParser) -> None:
    """Add the option that sets how old the cached card data may be."""
    parser.add_argument(
        "--max-age",
        type=float,
        default=6.0,
        help="hours before the cached card data is re-checked (default 6)",
    )


def _parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    defaults = ChromeConfig()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help="where the collection files go (default: the repository root)",
    )
    parser.add_argument(
        "--picks",
        type=Path,
        default=None,
        help=f"the printings picked by hand (default: <out-dir>/{PICKS_NAME})",
    )
    parser.add_argument("--chrome-binary", type=Path, default=defaults.binary)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=defaults.state_dir,
        help="Chrome profile, PID file, account and card-data cache (outside the repo)",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help="override the Chrome profile directory",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=None,
        help="card-data cache (default <state-dir>/catalog)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser(
        "login",
        help="open a plain Chrome window on the tool's profile to sign in",
    )
    p_login.add_argument("--wait", action="store_true", help="block until it exits")
    p_login.set_defaults(func=cmd_login)

    p_probe = sub.add_parser(
        "probe",
        help="check the login and print the response's field names (no values)",
    )
    _add_account_args(p_probe)
    p_probe.set_defaults(func=cmd_probe)

    p_pull = sub.add_parser("pull", help="fetch the collection and write the files")
    _add_account_args(p_pull)
    _add_output_args(p_pull)
    p_pull.add_argument("--dry-run", action="store_true", help="write nothing")
    p_pull.add_argument(
        "--allow-shrink",
        action="store_true",
        help="write even if the collection got smaller",
    )
    p_pull.add_argument(
        "--no-scryfall",
        action="store_true",
        help="skip Scryfall; names and sets from Arena's data only (no CSV rows)",
    )
    _add_card_data_args(p_pull)
    p_pull.set_defaults(func=cmd_pull)

    p_export = sub.add_parser(
        "export",
        help=f"offline: rewrite the CSV and TXT from {render.SNAPSHOT_NAME}",
    )
    _add_output_args(p_export)
    p_export.set_defaults(func=cmd_export)

    p_resolve = sub.add_parser(
        "resolve",
        help=f"resolve the cards of {render.SNAPSHOT_NAME} anew and rewrite the "
        "files (no Untapped.gg, no Chrome)",
    )
    _add_output_args(p_resolve)
    p_resolve.add_argument("--dry-run", action="store_true", help="write nothing")
    _add_card_data_args(p_resolve)
    p_resolve.set_defaults(func=cmd_resolve)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command; return the exit status."""
    args = _parser().parse_args(argv)
    if args.catalog_dir is None:
        args.catalog_dir = Path(args.state_dir).expanduser() / "catalog"
    if args.picks is None:
        args.picks = args.out_dir / PICKS_NAME
    status: int = args.func(args)
    return status
