//! Card characteristics from the Scryfall API (for txt decklists).
//!
//! Named-card lookup (`GET /cards/named?exact=NAME`) with a persistent
//! JSON file cache in the platform temp dir, shared with the Python
//! client (same path, same `json.dumps` layout). Failures degrade
//! gracefully: [`ScryfallClient::fetch`] returns `None` on network errors
//! or unknown names and callers fall back to the knowledge graph or the
//! inert stub, so the simulator keeps working offline. A network-level
//! error trips a circuit breaker that stops further fetch attempts for
//! the process lifetime.
//!
//! The HTTP transport and the clock are injected, so the client's cache
//! and retry logic is tested without a network. The real transport
//! ([`http_json`], `ureq` over rustls) is behind the `scryfall` feature.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::Duration;

use indexmap::IndexMap;
use pycompat::json::Value;
use pycompat::pyio;
use pycompat::pyurl::quote;

use crate::cards::CardData;
use crate::jsonval::{from_serde, int_or_none, py_str, truthy};

/// The API base URL.
pub const API: &str = "https://api.scryfall.com";
/// The `User-Agent` header value.
pub const USER_AGENT: &str = "stklug84-matchup-simulator/1.0";
/// Courtesy delay between live API requests (Scryfall asks for 50-100ms;
/// 150ms matches the `generate_individuals.py` fetcher).
pub const RATE_LIMIT: Duration = Duration::from_millis(150);
/// Base sleep before retrying a rate-limited request (doubles per retry).
pub const BACKOFF: Duration = Duration::from_secs(1);
/// Request timeout of the real transport.
pub const TIMEOUT: Duration = Duration::from_secs(60);
const HTTP_NOT_FOUND: u16 = 404;
const HTTP_TOO_MANY: u16 = 429;
const MAX_ATTEMPTS: u32 = 3;

/// CR 205.4c supertypes (Scryfall `type_line` words left of the em dash).
pub const SUPERTYPES: &[&str] = &[
    "Basic",
    "Elite",
    "Host",
    "Legendary",
    "Ongoing",
    "Snow",
    "World",
];

/// `tempfile.gettempdir() / "scryfall_cache" / "named.json"`.
#[must_use]
pub fn default_cache_file() -> PathBuf {
    std::env::temp_dir()
        .join("scryfall_cache")
        .join("named.json")
}

/// A failed HTTP lookup, classified the way the Python client classifies
/// `urllib` exceptions.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum HttpError {
    /// `HTTPError`: the server answered with an error status.
    #[error("HTTP Error {0}")]
    Status(u16),
    /// `URLError` / `TimeoutError` / `OSError`: no usable answer.
    #[error("{0}")]
    Network(String),
    /// A URL that is not `https://` (the Python `ValueError`).
    #[error("refusing to fetch non-https URL: {0}")]
    Refused(String),
}

/// A JSON GET: `(url) -> payload`.
pub type HttpFn = Box<dyn FnMut(&str) -> Result<serde_json::Value, HttpError>>;
/// A sleep: `(duration)`.
pub type SleepFn = Box<dyn FnMut(Duration)>;

/// Refuse any scheme other than https.
///
/// # Errors
///
/// [`HttpError::Refused`] for a non-https URL.
pub fn check_https(url: &str) -> Result<(), HttpError> {
    if url.starts_with("https://") {
        Ok(())
    } else {
        Err(HttpError::Refused(url.to_owned()))
    }
}

/// GET an https URL and decode the JSON payload (the real transport).
///
/// # Errors
///
/// [`HttpError`]: an HTTP error status, a network error or a refused URL.
#[cfg(feature = "scryfall")]
pub fn http_json(url: &str) -> Result<serde_json::Value, HttpError> {
    check_https(url)?;
    let config = ureq::Agent::config_builder()
        .timeout_global(Some(TIMEOUT))
        .build();
    let agent = ureq::Agent::new_with_config(config);
    let response = agent
        .get(url)
        .header("User-Agent", USER_AGENT)
        .header("Accept", "application/json")
        .call()
        .map_err(|e| match e {
            ureq::Error::StatusCode(code) => HttpError::Status(code),
            other => HttpError::Network(other.to_string()),
        })?;
    response
        .into_body()
        .read_json::<serde_json::Value>()
        .map_err(|e| HttpError::Network(e.to_string()))
}

/// Cached named-card lookups against the Scryfall API.
pub struct ScryfallClient {
    cache_file: PathBuf,
    http: HttpFn,
    sleep: SleepFn,
    // name -> card JSON, or None for a cached 'unknown name' miss
    cache: IndexMap<String, Option<serde_json::Value>>,
    dirty: bool,
    /// set once a network-level error or persistent rate limiting was
    /// seen; suppresses all further live requests
    pub offline: bool,
    /// delay before every live request
    pub rate_limit: Duration,
    /// base retry delay for 429 answers
    pub backoff: Duration,
}

impl std::fmt::Debug for ScryfallClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ScryfallClient")
            .field("cache_file", &self.cache_file)
            .field("cached", &self.cache.len())
            .field("offline", &self.offline)
            .finish_non_exhaustive()
    }
}

impl ScryfallClient {
    /// Load the name cache at `cache_file`; `http` performs the lookups.
    ///
    /// An unreadable or malformed cache file starts an empty cache.
    pub fn new(cache_file: &Path, http: HttpFn) -> Self {
        let cache = pyio::read_text(cache_file)
            .ok()
            .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
            .and_then(|v| match v {
                serde_json::Value::Object(map) => Some(map),
                _ => None,
            })
            .map(|map| {
                map.into_iter()
                    .map(|(k, v)| (k, if v.is_null() { None } else { Some(v) }))
                    .collect::<IndexMap<_, _>>()
            })
            .unwrap_or_default();
        Self {
            cache_file: cache_file.to_path_buf(),
            http,
            sleep: Box::new(std::thread::sleep),
            cache,
            dirty: false,
            offline: false,
            rate_limit: RATE_LIMIT,
            backoff: BACKOFF,
        }
    }

    /// The client of the binaries: the default cache file and the real
    /// transport.
    #[cfg(feature = "scryfall")]
    #[must_use]
    pub fn live() -> Self {
        Self::new(&default_cache_file(), Box::new(http_json))
    }

    /// Replace the clock (tests pass a recorder instead of sleeping).
    pub fn set_sleep(&mut self, sleep: SleepFn) {
        self.sleep = sleep;
    }

    /// The path of the cache file.
    #[must_use]
    pub fn cache_file(&self) -> &Path {
        &self.cache_file
    }

    /// Whether `name` has a cache entry (a hit or a cached miss).
    #[must_use]
    pub fn is_cached(&self, name: &str) -> bool {
        self.cache.contains_key(name)
    }

    /// The Scryfall card JSON for `name`, or `None` on failure.
    ///
    /// Cache hits (including cached 'unknown name' misses) never touch
    /// the network. Rate-limited requests (429) are retried with
    /// exponential backoff. A network-level error - or persistent rate
    /// limiting - sets `offline` and suppresses all further live requests.
    pub fn fetch(&mut self, name: &str) -> Option<serde_json::Value> {
        if let Some(cached) = self.cache.get(name) {
            return cached.clone();
        }
        if self.offline {
            return None;
        }
        let url = format!("{API}/cards/named?exact={}", quote(name));
        let data = self.request(&url, name);
        if let Some(data) = &data {
            self.cache.insert(name.to_owned(), Some(data.clone()));
            self.dirty = true;
        }
        data
    }

    /// Perform the live lookup with 429 backoff; `None` on failure.
    fn request(&mut self, url: &str, name: &str) -> Option<serde_json::Value> {
        for attempt in 0..MAX_ATTEMPTS {
            (self.sleep)(self.rate_limit);
            match (self.http)(url) {
                Ok(payload) => return Some(payload),
                Err(HttpError::Status(HTTP_NOT_FOUND)) => {
                    // unknown card name: a definitive, cacheable answer
                    self.cache.insert(name.to_owned(), None);
                    self.dirty = true;
                    return None;
                }
                Err(HttpError::Status(HTTP_TOO_MANY)) if attempt < MAX_ATTEMPTS - 1 => {
                    (self.sleep)(self.backoff * 2u32.pow(attempt));
                }
                Err(HttpError::Status(HTTP_TOO_MANY)) => {
                    // still throttled after backoff: the server asked us
                    // to stop, so stop for the rest of the process
                    self.offline = true;
                    return None;
                }
                Err(HttpError::Status(_)) => return None,
                Err(HttpError::Network(_) | HttpError::Refused(_)) => {
                    self.offline = true;
                    return None;
                }
            }
        }
        None
    }

    /// Persist newly fetched entries to the cache file
    /// (`json.dumps(cache)`, so the Python client reads it unchanged).
    ///
    /// # Errors
    ///
    /// The I/O error of creating the directory or writing the file.
    pub fn save(&mut self) -> std::io::Result<()> {
        if !self.dirty {
            return Ok(());
        }
        if let Some(parent) = self.cache_file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let dict = Value::Dict(
            self.cache
                .iter()
                .map(|(k, v)| (k.clone(), v.as_ref().map_or(Value::Null, from_serde)))
                .collect(),
        );
        std::fs::write(&self.cache_file, pycompat::json::dumps(&dict))?;
        self.dirty = false;
        Ok(())
    }
}

/// `str(x or "")`: the string of a value, empty for a falsy one.
fn str_or_empty(value: Option<&Value>) -> String {
    match value {
        Some(v) if truthy(v) => py_str(v),
        _ => String::new(),
    }
}

/// The card's colours as the generator writes `:hasColor`: the card-level
/// `colors`, else the union of the faces' `colors` (Scryfall gives a
/// transforming or modal double-faced card its colours per face only).
fn colors_of(data: &Value) -> BTreeSet<String> {
    if let Some(Value::List(colors)) = data.get("colors") {
        return colors.iter().map(py_str).collect();
    }
    let Some(Value::List(faces)) = data.get("card_faces") else {
        return BTreeSet::new();
    };
    faces
        .iter()
        .filter_map(|face| match face.get("colors") {
            Some(Value::List(colors)) => Some(colors),
            _ => None,
        })
        .flatten()
        .map(py_str)
        .collect()
}

/// Build a [`CardData`] from one Scryfall card JSON object.
///
/// Multi-faced cards keep the canonical `Front // Back` name but take
/// their playable characteristics from the front face, mirroring the
/// front-face aliasing of the TTL graph loader. Their colours span every
/// face, as `:hasColor` does in the graph, so a card loads with the same
/// colours from either source.
#[must_use]
pub fn card_from_scryfall(data: &serde_json::Value) -> CardData {
    let data = from_serde(data);
    let mut face = &data;
    if let Some(Value::List(faces)) = data.get("card_faces")
        && let Some(first) = faces.first()
        && data.get("oracle_text").is_none()
    {
        face = first;
    }
    let name = data.get("name").map_or_else(String::new, py_str);
    let mut card = CardData::new(&name, "scryfall");
    card.mana_cost = str_or_empty(face.get("mana_cost"));
    card.mv = data
        .get("cmc")
        .filter(|v| truthy(v))
        .and_then(int_or_none)
        .unwrap_or(0);
    let type_line = {
        let own = str_or_empty(face.get("type_line"));
        if own.is_empty() {
            str_or_empty(data.get("type_line"))
        } else {
            own
        }
    };
    let (left, right) = type_line
        .split_once('\u{2014}')
        .unwrap_or((type_line.as_str(), ""));
    for word in left.split_whitespace() {
        if SUPERTYPES.contains(&word) {
            card.supertypes.insert(word.to_owned());
        } else {
            card.types.insert(word.to_owned());
        }
    }
    card.subtypes = right.split_whitespace().map(str::to_owned).collect();
    card.power = face.get("power").and_then(int_or_none);
    card.toughness = face.get("toughness").and_then(int_or_none);
    card.loyalty = face.get("loyalty").and_then(int_or_none);
    card.colors = colors_of(&data);
    if let Some(Value::List(ci)) = data.get("color_identity") {
        card.color_identity = ci.iter().map(py_str).collect();
    }
    card.oracle = str_or_empty(face.get("oracle_text"));
    card
}

/// The cache as JSON data (for tests and tooling).
#[must_use]
pub fn cache_to_serde(client: &ScryfallClient) -> serde_json::Value {
    serde_json::Value::Object(
        client
            .cache
            .iter()
            .map(|(k, v)| (k.clone(), v.clone().unwrap_or(serde_json::Value::Null)))
            .collect(),
    )
}
