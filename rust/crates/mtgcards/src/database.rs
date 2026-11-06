//! Card database assembly from the TTL knowledge graph.
//!
//! Resolution order (later layers override earlier ones):
//!
//! 1. TTL knowledge graph (`sets/*.ttl` plus any extra card graphs, e.g.
//!    an out-of-collection `MagicExternalCards.ttl`) — the single source
//!    of card characteristics;
//! 2. custom cards JSON (opt-in via `--custom-cards`) — unreleased /
//!    unverified cards only; no default file is loaded;
//! 3. Scryfall API ([`CardDatabase::resolve_scryfall`]) — cards of txt
//!    decklists, fetched by name with a local cache; failures fall back
//!    to the layers above.
//!
//! Then oracle-text derivation and behaviour hooks are applied, once per
//! distinct record (double-faced aliases share one record).

use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use pycompat::json::Value;
use pycompat::pyio::{self, ReadTextError};
use pycompat::pypath::path_str;
use pycompat::pyrepr::repr_str;

use crate::behavior::{
    self, Behavior, TUPLE_KEYS, apply_behaviors, is_behavior_key, load_annotations,
};
use crate::cards::{CardData, derive_from_oracle};
use crate::graph::{self, GraphManifest, GraphSchemaError};
use crate::index::{CardId, CardIndex};
use crate::jsonval::{from_serde, int_or_none, type_name};
use crate::mana::parse_cost;
use crate::scryfall::{ScryfallClient, card_from_scryfall};
use crate::ttl_loader::{self, LoadReport};

/// The out-of-collection card graph honoured at a graph root.
pub const EXTERNAL_CARDS_FILE: &str = "MagicExternalCards.ttl";

/// Errors of database construction and Scryfall resolution.
///
/// Each variant renders the message of the Python exception it stands
/// for; the CLI maps them to exit codes.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DatabaseError {
    /// `GraphSchemaError`: the manifest is unreadable or incompatible.
    #[error("{0}")]
    Schema(#[from] GraphSchemaError),
    /// `OSError` while reading a card graph.
    #[error("{0}")]
    Graph(#[from] ttl_loader::LoadError),
    /// `ValueError` from the annotation reader.
    #[error("{0}")]
    Annotations(#[from] behavior::AnnotationError),
    /// `OSError` while reading the custom-cards file.
    #[error("{0}")]
    CustomRead(ReadTextError),
    /// `ValueError`: invalid custom-cards JSON or a bad card definition.
    #[error("{0}")]
    CustomValue(String),
    /// `TypeError`: the custom-cards file is not a JSON object.
    #[error("{0}")]
    CustomType(String),
    /// `OSError` while saving the Scryfall cache.
    #[error("{0}")]
    CacheWrite(String),
}

/// A string-set field of a custom-card entry (`set(entry.get(key, []))`).
fn string_set(
    name: &str,
    entry: &Value,
    key: &str,
) -> Result<std::collections::BTreeSet<String>, String> {
    match entry.get(key) {
        None => Ok(std::collections::BTreeSet::new()),
        Some(Value::List(items)) => items
            .iter()
            .map(|v| match v {
                Value::Str(s) => Ok(s.clone()),
                other => Err(format!(
                    "custom card {}: {key} must be a list of strings, got {}",
                    repr_str(name),
                    type_name(other)
                )),
            })
            .collect(),
        Some(other) => Err(format!(
            "custom card {}: {key} must be a list of strings, got {}",
            repr_str(name),
            type_name(other)
        )),
    }
}

/// A string field of a custom-card entry (`entry.get(key, "")`).
fn string_field(name: &str, entry: &Value, key: &str) -> Result<String, String> {
    match entry.get(key) {
        None => Ok(String::new()),
        Some(Value::Str(s)) => Ok(s.clone()),
        Some(other) => Err(format!(
            "custom card {}: {key} must be a string, got {}",
            repr_str(name),
            type_name(other)
        )),
    }
}

/// An optional integer field of a custom-card entry (`entry.get(key)`).
fn int_field(name: &str, entry: &Value, key: &str) -> Result<Option<i64>, String> {
    match entry.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(v @ Value::Int(_)) => Ok(int_or_none(v)),
        Some(other) => Err(format!(
            "custom card {}: {key} must be an integer or null, got {}",
            repr_str(name),
            type_name(other)
        )),
    }
}

/// Build a [`CardData`] from one `--custom-cards` JSON entry.
///
/// # Errors
///
/// The Python `ValueError` text for an unknown behaviour key; a clear
/// message for a field of the wrong type (where Python would fail
/// later, or not at all).
pub fn card_from_json(name: &str, entry: &Value, source: &str) -> Result<CardData, String> {
    if !matches!(entry, Value::Dict(_)) {
        return Err(format!(
            "custom card {}: expected a JSON object, got {}",
            repr_str(name),
            type_name(entry)
        ));
    }
    let mut card = CardData::new(name, source);
    card.mana_cost = string_field(name, entry, "mana_cost")?;
    card.types = string_set(name, entry, "types")?;
    card.subtypes = string_set(name, entry, "subtypes")?;
    card.power = int_field(name, entry, "power")?;
    card.toughness = int_field(name, entry, "toughness")?;
    card.oracle = string_field(name, entry, "oracle")?;
    card.color_identity = string_set(name, entry, "color_identity")?;
    card.mv = parse_cost(&card.mana_cost).mv();
    match entry.get("behavior") {
        None => {}
        Some(Value::Dict(pairs)) => {
            for (k, v) in pairs {
                if !is_behavior_key(k) {
                    return Err(format!(
                        "custom card {}: unknown behavior key {} (see behaviors.BEHAVIOR_KEYS)",
                        repr_str(name),
                        repr_str(k)
                    ));
                }
                // tuple keys become tuples in Python; lists on this side
                let _ = TUPLE_KEYS.contains(&k.as_str());
                card.behavior.insert(k.clone(), v.clone());
            }
        }
        Some(other) => {
            return Err(format!(
                "custom card {}: behavior must be a JSON object, got {}",
                repr_str(name),
                type_name(other)
            ));
        }
    }
    Ok(card)
}

/// Read a `--custom-cards` JSON file: `{card name: definition}`.
///
/// Keys starting with `_` are comments and skipped. Every error names
/// the file so the CLI can report it without a traceback.
///
/// # Errors
///
/// [`DatabaseError::CustomRead`], [`DatabaseError::CustomValue`] or
/// [`DatabaseError::CustomType`].
pub fn load_custom_cards(path: &Path) -> Result<CardIndex, DatabaseError> {
    let shown = path_str(path);
    let text = pyio::read_text(path).map_err(DatabaseError::CustomRead)?;
    let parsed: serde_json::Value = serde_json::from_str(&text).map_err(|e| {
        DatabaseError::CustomValue(format!("{shown}: invalid custom-cards JSON: {e}"))
    })?;
    let entries = from_serde(&parsed);
    let Value::Dict(pairs) = &entries else {
        return Err(DatabaseError::CustomType(format!(
            "{shown}: expected a JSON object of card definitions"
        )));
    };
    let mut index = CardIndex::new();
    for (name, entry) in pairs {
        if name.starts_with('_') {
            continue;
        }
        let card = card_from_json(name, entry, "custom")
            .map_err(|msg| DatabaseError::CustomValue(format!("{shown}: {msg}")))?;
        index.insert(name.clone(), card);
    }
    Ok(index)
}

/// Name-indexed card data, layered from graph, custom JSON and hooks.
#[derive(Clone, Debug, Default)]
pub struct CardDatabase {
    /// declared contract of the graph root (`None` for a plain checkout)
    pub manifest: Option<GraphManifest>,
    /// the name index (aliases share records)
    pub index: CardIndex,
    /// `{individual local name: card name}` for every card individual in
    /// the graph; deck instance graphs (`.ttl` decks) resolve through it
    pub ind2name: IndexMap<String, String>,
    /// the hand-authored hooks by card name (applied to Scryfall results
    /// too)
    pub hooks_by_name: IndexMap<String, Behavior>,
    /// names `get` had to stub, in order of first request
    pub stubbed: Vec<String>,
    /// what the graph loader noticed that Python would pass over
    pub report: LoadReport,
}

impl CardDatabase {
    /// Load every layer and derive behaviour for each unique card.
    ///
    /// `root` is a graph root: a magic-cards checkout or an unpacked
    /// graph bundle. A bundle's `GRAPH-MANIFEST.json` is checked for
    /// compatibility first; a checkout carries no manifest and is always
    /// accepted.
    ///
    /// `extra_graphs` are additional card TTL graphs merged on top of
    /// `sets/*.ttl` (missing files are skipped). A
    /// `MagicSimulationAnnotations.ttl` sitting next to an extra graph
    /// is loaded on top of the root's annotations.
    ///
    /// # Errors
    ///
    /// See [`DatabaseError`].
    pub fn load(
        root: &Path,
        custom_cards: Option<&Path>,
        extra_graphs: &[PathBuf],
    ) -> Result<Self, DatabaseError> {
        let manifest = graph::load_manifest(root)?;
        graph::check_compatible(manifest.as_ref(), root)?;
        let mut db = CardDatabase {
            manifest,
            ..CardDatabase::default()
        };
        let sets_dir = root.join("sets");
        if sets_dir.is_dir() {
            // out-of-collection cards referenced by deck graphs (a local
            // MagicExternalCards.ttl is honoured when present)
            let mut extras = vec![root.join(EXTERNAL_CARDS_FILE)];
            extras.extend(extra_graphs.iter().cloned());
            let loaded =
                ttl_loader::load_graph_cards(&sets_dir, &extras, &mut db.ind2name, &mut db.report)?;
            db.index.update(loaded);
            // simulation annotations (behaviour hooks, threat weights),
            // merged with any annotation file next to an extra card graph
            db.hooks_by_name = load_annotations(root, &db.ind2name)?;
            let mut seen_dirs = vec![canonical(root)];
            for extra in &extras {
                if !extra.exists() {
                    continue;
                }
                let extra_dir = canonical(extra)
                    .parent()
                    .map(Path::to_path_buf)
                    .unwrap_or_default();
                if seen_dirs.contains(&extra_dir) {
                    continue;
                }
                seen_dirs.push(extra_dir.clone());
                for (name, hooks) in load_annotations(&extra_dir, &db.ind2name)? {
                    db.hooks_by_name.entry(name).or_default().update(&hooks);
                }
            }
        }
        // custom layer (opt-in, overrides the graph)
        if let Some(path) = custom_cards {
            db.index.update(load_custom_cards(path)?);
        }
        // derivation + hooks, once per record (DFC aliases share one)
        for id in db.index.unique_ids() {
            db.derive(id);
        }
        Ok(db)
    }

    fn derive(&mut self, id: CardId) {
        let card = self.index.record_mut(id);
        derive_from_oracle(card);
        apply_behaviors(card, &self.hooks_by_name);
    }

    /// Fetch `names` from Scryfall, overriding the graph entries.
    ///
    /// Fetched cards go through the same oracle derivation + behaviour
    /// hook pipeline as graph cards. Returns the names that could not be
    /// fetched (offline / unknown name); lookups for those fall back to
    /// the knowledge-graph entry or, failing that, the inert stub.
    ///
    /// # Errors
    ///
    /// [`DatabaseError::CacheWrite`] when the client's cache cannot be
    /// saved.
    pub fn resolve_scryfall(
        &mut self,
        names: &[String],
        client: &mut ScryfallClient,
    ) -> Result<Vec<String>, DatabaseError> {
        let mut failed = Vec::new();
        for name in names {
            let Some(data) = client.fetch(name) else {
                failed.push(name.clone());
                continue;
            };
            let card = card_from_scryfall(&data);
            let card_name = card.name.clone();
            let id = self.index.insert(card_name.clone(), card);
            self.derive(id);
            // front face of double-faced cards, plus the queried alias
            if let Some((front, _)) = card_name.split_once(" // ") {
                self.index.alias(front, id);
            }
            self.index.alias(name.clone(), id);
        }
        client.save().map_err(|e| {
            DatabaseError::CacheWrite(pyio::os_error(&e, client.cache_file()).to_string())
        })?;
        Ok(failed)
    }

    /// Look a card up by (front-face) name without stubbing.
    #[must_use]
    pub fn lookup(&self, name: &str) -> Option<&CardData> {
        let id = self.lookup_id(name)?;
        Some(self.index.record(id))
    }

    fn lookup_id(&self, name: &str) -> Option<CardId> {
        self.index.id_of(name).or_else(|| {
            name.split_once(" // ")
                .and_then(|(front, _)| self.index.id_of(front))
        })
    }

    /// Look a card up by (front-face) name, stubbing unknown cards.
    ///
    /// An unknown card becomes an inert `{3}` sorcery so arbitrary decks
    /// never crash; its name is appended to `stubbed`.
    pub fn get(&mut self, name: &str) -> &CardData {
        let id = if let Some(id) = self.lookup_id(name) {
            id
        } else {
            // unknown card: inert stub so arbitrary decks never crash
            let mut card = CardData {
                mana_cost: "{3}".to_owned(),
                mv: 3,
                ..CardData::new(name, "stub")
            };
            card.types.insert("Sorcery".to_owned());
            self.stubbed.push(name.to_owned());
            self.index.insert(name, card)
        };
        self.index.record(id)
    }

    /// Every card as a JSON dict, by name in index order (for dumps).
    #[must_use]
    pub fn to_values(&self) -> Vec<(String, Value)> {
        self.index
            .iter()
            .map(|(name, card)| (name.to_owned(), card.to_value()))
            .collect()
    }
}

/// `Path.resolve()`: absolute, symlinks followed (the path itself when
/// that fails).
fn canonical(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}
