//! Scryfall client, JSON-to-CardData mapping, and database resolution
//! (port of the Python `mtgcards/tests/test_scryfall.py`).

mod common;

use std::cell::RefCell;
use std::collections::BTreeSet;
use std::rc::Rc;
use std::time::Duration;

use mtgcards::behavior::set_value;
use mtgcards::database::CardDatabase;
use mtgcards::scryfall::{
    HttpError, HttpFn, ScryfallClient, cache_to_serde, card_from_scryfall, check_https,
};
use pycompat::json::Value;
use serde_json::json;

fn llanowar() -> serde_json::Value {
    json!({
        "name": "Llanowar Elves",
        "mana_cost": "{G}",
        "cmc": 1.0,
        "type_line": "Creature \u{2014} Elf Druid",
        "oracle_text": "{T}: Add {G}.",
        "power": "1",
        "toughness": "1",
        "colors": ["G"],
        "color_identity": ["G"],
    })
}

fn delver() -> serde_json::Value {
    json!({
        "name": "Delver of Secrets // Insectile Aberration",
        "cmc": 1.0,
        "type_line": "Creature \u{2014} Human Wizard // Creature \u{2014} Human Insect",
        "color_identity": ["U"],
        "card_faces": [
            {
                "name": "Delver of Secrets",
                "mana_cost": "{U}",
                "type_line": "Creature \u{2014} Human Wizard",
                "oracle_text": "At the beginning of your upkeep, look at the top card of your library.",
                "power": "1",
                "toughness": "1",
            },
            {
                "name": "Insectile Aberration",
                "mana_cost": "",
                "type_line": "Creature \u{2014} Human Insect",
                "oracle_text": "Flying",
                "power": "3",
                "toughness": "2",
            },
        ],
    })
}

fn vraska() -> serde_json::Value {
    json!({
        "name": "Vraska, Betrayal's Sting",
        "mana_cost": "{4}{B}{B}",
        "cmc": 6.0,
        "type_line": "Legendary Planeswalker \u{2014} Vraska",
        "oracle_text": "Compleated",
        "loyalty": "6",
        "color_identity": ["B"],
    })
}

fn tarmogoyf() -> serde_json::Value {
    json!({
        "name": "Tarmogoyf",
        "mana_cost": "{1}{G}",
        "cmc": 2.0,
        "type_line": "Creature \u{2014} Lhurgoyf",
        "oracle_text": "Tarmogoyf's power is equal to ...",
        "power": "*",
        "toughness": "1+*",
        "color_identity": ["G"],
    })
}

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

/// What the recording transport and clock saw.
type Recorded<T> = Rc<RefCell<Vec<T>>>;

/// A client over a recording transport and a non-sleeping clock.
fn make_client(
    cache: &std::path::Path,
    mut http: impl FnMut(&str) -> Result<serde_json::Value, HttpError> + 'static,
) -> (ScryfallClient, Recorded<String>, Recorded<Duration>) {
    let calls = Rc::new(RefCell::new(Vec::new()));
    let sleeps = Rc::new(RefCell::new(Vec::new()));
    let recorded = Rc::clone(&calls);
    let transport: HttpFn = Box::new(move |url: &str| {
        recorded.borrow_mut().push(url.to_owned());
        http(url)
    });
    let mut client = ScryfallClient::new(cache, transport);
    let slept = Rc::clone(&sleeps);
    client.set_sleep(Box::new(move |d| slept.borrow_mut().push(d)));
    (client, calls, sleeps)
}

// ---- TestCardFromScryfall: mapping Scryfall card JSON onto CardData

#[test]
fn creature() {
    let card = card_from_scryfall(&llanowar());
    assert_eq!(card.name, "Llanowar Elves");
    assert_eq!(card.mana_cost, "{G}");
    assert_eq!(card.mv, 1);
    assert_eq!(card.types, set(&["Creature"]));
    assert_eq!(card.subtypes, set(&["Elf", "Druid"]));
    assert_eq!((card.power, card.toughness), (Some(1), Some(1)));
    assert_eq!(card.colors, set(&["G"]));
    assert_eq!(card.color_identity, set(&["G"]));
    assert_eq!(card.source, "scryfall");
}

#[test]
fn double_faced_uses_front_face() {
    let card = card_from_scryfall(&delver());
    assert_eq!(card.name, "Delver of Secrets // Insectile Aberration");
    assert_eq!(card.mana_cost, "{U}");
    assert_eq!(card.subtypes, set(&["Human", "Wizard"]));
    assert_eq!((card.power, card.toughness), (Some(1), Some(1)));
    assert!(card.oracle.contains("upkeep"));
}

#[test]
fn planeswalker_and_supertype() {
    let card = card_from_scryfall(&vraska());
    assert_eq!(card.supertypes, set(&["Legendary"]));
    assert_eq!(card.types, set(&["Planeswalker"]));
    assert_eq!(card.loyalty, Some(6));
}

#[test]
fn star_power_toughness() {
    let card = card_from_scryfall(&tarmogoyf());
    assert_eq!(card.power, None);
    assert_eq!(card.toughness, None);
}

#[test]
fn colours_are_not_the_colour_identity() {
    let signet = card_from_scryfall(&json!({
        "name": "Azorius Signet",
        "mana_cost": "{2}",
        "cmc": 2.0,
        "type_line": "Artifact",
        "oracle_text": "{1}, {T}: Add {W}{U}.",
        "colors": [],
        "color_identity": ["U", "W"],
    }));
    assert!(signet.colors.is_empty());
    assert_eq!(signet.color_identity, set(&["W", "U"]));
    // a transforming card has its colours per face: the card spans both,
    // like `:hasColor` in the graph
    let scholar = card_from_scryfall(&json!({
        "name": "Civilized Scholar // Homicidal Brute",
        "cmc": 3.0,
        "type_line": "Creature \u{2014} Human Advisor // Creature \u{2014} Human Mutant",
        "color_identity": ["R", "U"],
        "card_faces": [
            {
                "name": "Civilized Scholar",
                "mana_cost": "{2}{U}",
                "type_line": "Creature \u{2014} Human Advisor",
                "oracle_text": "{T}: Draw a card, then discard a card.",
                "colors": ["U"],
                "power": "0",
                "toughness": "1",
            },
            {
                "name": "Homicidal Brute",
                "mana_cost": "",
                "type_line": "Creature \u{2014} Human Mutant",
                "oracle_text": "At the beginning of your end step, transform this creature.",
                "colors": ["R"],
                "color_indicator": ["R"],
                "power": "5",
                "toughness": "1",
            },
        ],
    }));
    assert_eq!(scholar.colors, set(&["U", "R"]));
    assert_eq!(scholar.mana_cost, "{2}{U}");
}

// ---- TestScryfallClient: cache behaviour and failure handling of the client

#[test]
fn fetch_caches_and_persists() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let cache = tmp.path().join("named.json"); // not yet created
    let (mut client, calls, sleeps) = make_client(&cache, |_| Ok(llanowar()));
    assert_eq!(client.fetch("Llanowar Elves"), Some(llanowar()));
    assert_eq!(client.fetch("Llanowar Elves"), Some(llanowar()));
    assert_eq!(calls.borrow().len(), 1);
    assert!(calls.borrow()[0].contains("Llanowar%20Elves"));
    assert_eq!(
        calls.borrow()[0],
        "https://api.scryfall.com/cards/named?exact=Llanowar%20Elves"
    );
    assert_eq!(*sleeps.borrow(), vec![Duration::from_millis(150)]);
    client.save().expect("save");
    // the cache file is json.dumps(cache): Python's separators and float repr
    let text = std::fs::read_to_string(&cache).expect("cache written");
    assert!(
        text.starts_with(
            r#"{"Llanowar Elves": {"name": "Llanowar Elves", "mana_cost": "{G}", "cmc": 1.0, "#
        ),
        "{text}"
    );

    // a fresh client reads the persisted entry without the network
    let (mut reloaded, calls, _) = make_client(&cache, |url| panic!("unexpected fetch: {url}"));
    assert_eq!(reloaded.fetch("Llanowar Elves"), Some(llanowar()));
    assert!(calls.borrow().is_empty());
}

#[test]
fn unknown_name_cached_as_miss() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let cache = tmp.path().join("named.json");
    let (mut client, calls, _) = make_client(&cache, |_| Err(HttpError::Status(404)));
    assert_eq!(client.fetch("No Such Card"), None);
    assert_eq!(client.fetch("No Such Card"), None);
    assert_eq!(calls.borrow().len(), 1);
    assert!(!client.offline);
    assert!(client.is_cached("No Such Card"));
    client.save().expect("save");
    assert_eq!(
        std::fs::read_to_string(&cache).expect("cache"),
        r#"{"No Such Card": null}"#
    );
    // the miss survives a reload as a miss, not as a retry
    let (mut reloaded, calls, _) = make_client(&cache, |_| Ok(llanowar()));
    assert_eq!(reloaded.fetch("No Such Card"), None);
    assert!(calls.borrow().is_empty());
}

#[test]
fn rate_limit_retries_then_trips_breaker() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let (mut client, calls, sleeps) = make_client(&tmp.path().join("named.json"), |_| {
        Err(HttpError::Status(429))
    });
    assert_eq!(client.fetch("Sol Ring"), None);
    assert_eq!(calls.borrow().len(), 3); // initial try + 2 retries
    assert!(client.offline);
    // rate limit before each attempt, backoff 1s then 2s between them
    assert_eq!(
        *sleeps.borrow(),
        vec![
            Duration::from_millis(150),
            Duration::from_secs(1),
            Duration::from_millis(150),
            Duration::from_secs(2),
            Duration::from_millis(150),
        ]
    );
}

#[test]
fn rate_limit_recovers_within_retries() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let state = Rc::new(RefCell::new(0));
    let counter = Rc::clone(&state);
    let (mut client, _, _) = make_client(&tmp.path().join("named.json"), move |_| {
        *counter.borrow_mut() += 1;
        if *counter.borrow() == 1 {
            Err(HttpError::Status(429))
        } else {
            Ok(llanowar())
        }
    });
    client.backoff = Duration::ZERO;
    assert_eq!(client.fetch("Llanowar Elves"), Some(llanowar()));
    assert!(!client.offline);
}

#[test]
fn network_error_trips_circuit_breaker() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let (mut client, calls, _) = make_client(&tmp.path().join("named.json"), |_| {
        Err(HttpError::Network("no network".to_owned()))
    });
    assert_eq!(client.fetch("Sol Ring"), None);
    assert!(client.offline);
    assert_eq!(client.fetch("Arcane Signet"), None);
    assert_eq!(calls.borrow().len(), 1);
    assert_eq!(cache_to_serde(&client), json!({}));
}

#[test]
fn rejects_non_https() {
    let err = check_https("http://api.scryfall.com/cards/named?exact=x").expect_err("http");
    assert_eq!(
        err.to_string(),
        "refusing to fetch non-https URL: http://api.scryfall.com/cards/named?exact=x"
    );
    assert!(check_https("https://api.scryfall.com/cards/named?exact=x").is_ok());
}

#[test]
fn a_corrupt_cache_file_starts_empty() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let cache = common::write(tmp.path(), "named.json", "{not json");
    let (mut client, calls, _) = make_client(&cache, |_| Ok(llanowar()));
    assert_eq!(client.fetch("Llanowar Elves"), Some(llanowar()));
    assert_eq!(calls.borrow().len(), 1);
}

// ---- TestDatabaseResolveScryfall: CardDatabase.resolve_scryfall layering and fallback

#[test]
fn resolve_overrides_and_reports_failures() {
    let mut db =
        CardDatabase::load(&common::repo_root(), None, &[]).expect("the repo's graph loads");
    let tmp = tempfile::tempdir().expect("tempdir");
    let (mut client, _, _) = make_client(&tmp.path().join("named.json"), |url| {
        if url.contains("Llanowar%20Elves") {
            Ok(llanowar())
        } else {
            Err(HttpError::Status(404))
        }
    });
    let failed = db
        .resolve_scryfall(
            &["Llanowar Elves".to_owned(), "No Such Card".to_owned()],
            &mut client,
        )
        .expect("resolve");
    assert_eq!(failed, vec!["No Such Card".to_owned()]);
    let card = db.get("Llanowar Elves");
    assert_eq!(card.source, "scryfall");
    // oracle derivation ran on the fetched card (mana dork ability)
    assert_eq!(card.behavior.get("rock_mana"), Some(&Value::Int(1)));
    assert_eq!(card.behavior.get("rock_colors"), Some(&set_value(["G"])));
    // the failed name falls back to graph or stub via db.get
    let fallback = db.get("No Such Card");
    assert!(["graph", "stub"].contains(&fallback.source.as_str()));
    // the cache was saved by resolve_scryfall
    assert!(tmp.path().join("named.json").is_file());
}

#[test]
fn resolved_dfc_is_aliased_three_ways() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let mut db = CardDatabase::load(tmp.path(), None, &[]).expect("empty root");
    let (mut client, _, _) = make_client(&tmp.path().join("cache").join("named.json"), |_| {
        Ok(delver())
    });
    let failed = db
        .resolve_scryfall(&["Delver of Secrets".to_owned()], &mut client)
        .expect("resolve");
    assert_eq!(failed, Vec::<String>::new());
    assert!(db.index.same_record(
        "Delver of Secrets",
        "Delver of Secrets // Insectile Aberration"
    ));
    assert_eq!(db.index.len(), 2);
    assert_eq!(
        db.lookup("Delver of Secrets").expect("card").mana_cost,
        "{U}"
    );
}
