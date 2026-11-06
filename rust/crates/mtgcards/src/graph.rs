//! Graph-bundle manifest: the knowledge-graph data contract.
//!
//! The simulator reads card data from a *graph root* (`MTG_GRAPH_ROOT`):
//! a magic-cards checkout or an unpacked graph bundle. A bundle carries
//! `GRAPH-MANIFEST.json`; only its `graph_schema` is enforced — it
//! versions the predicate vocabulary that [`crate::ttl_loader`],
//! [`crate::deck_ttl`] and [`crate::behavior`] extract. `requires_tools`
//! is informational. A root without a manifest (a plain checkout) is
//! accepted unchanged.
//!
//! Field coercion follows the Python reader: `int(str(value))` for the
//! numbers (so `"1.0"` is malformed and `true` reads as `"True"`),
//! `str(value)` for the strings. The one message that cannot be identical
//! is the JSON decoder's own text inside "cannot read the graph manifest".

use std::path::{Path, PathBuf};

use pycompat::json::Value;
use pycompat::pyio;
use pycompat::pyrepr;
use pycompat::pystr;

use crate::behavior::from_serde;

/// Manifest file name at the top level of a graph bundle.
pub const MANIFEST_FILE: &str = "GRAPH-MANIFEST.json";

/// Schema version this release of the tooling writes.
pub const GRAPH_SCHEMA: i64 = 1;

/// Schema versions this release of the tooling can read (sorted).
pub const SUPPORTED_GRAPH_SCHEMA: &[i64] = &[1];

/// Ontology namespace assumed when a graph root carries no manifest.
pub const DEFAULT_ONTOLOGY_IRI: &str = "urn:stklug84:MagicCardsOntology:2026-02-27#";

/// Name of the distribution these tools are published as.
pub const DISTRIBUTION: &str = "magic-cards-tools";

/// The tools version (the Cargo workspace version, kept equal to the
/// Python distribution's).
#[must_use]
pub fn tools_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// A graph bundle's manifest cannot be read or is not supported.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct GraphSchemaError(pub String);

/// Declared contract of one graph bundle.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GraphManifest {
    /// predicate-vocabulary version (the enforced compatibility key)
    pub graph_schema: i64,
    /// content version of the bundle, e.g. "2026-08-09"
    pub graph_version: String,
    /// ontology namespace every graph file binds as the empty prefix
    pub ontology_iri: String,
    /// Comprehensive Rules edition (informational)
    pub cr_version: String,
    /// tools range that can read this bundle (informational)
    pub requires_tools: String,
    /// number of per-set card graphs in the bundle
    pub sets: i64,
    /// number of card individuals across those graphs
    pub card_individuals: i64,
}

/// Python's `str(value)` of a decoded JSON value.
fn py_str(value: &Value) -> String {
    match value {
        Value::Str(s) => s.clone(),
        other => pyrepr::repr(other),
    }
}

/// `int(str(data.get(key, default)))`.
fn int_field(data: &Value, key: &str, default: i64) -> Result<i64, String> {
    let Some(value) = data.get(key) else {
        return Ok(default);
    };
    let parsed = pystr::parse_int(&py_str(value))?;
    i64::try_from(parsed).map_err(|_| format!("{key} out of range"))
}

/// `str(data.get(key, default))`.
fn str_field(data: &Value, key: &str, default: &str) -> String {
    data.get(key).map_or_else(|| default.to_owned(), py_str)
}

impl GraphManifest {
    /// Build a manifest from parsed `GRAPH-MANIFEST.json` data (a dict).
    ///
    /// # Errors
    ///
    /// The `ValueError` text of a field that does not read as an integer.
    pub fn from_json(data: &Value) -> Result<Self, String> {
        Ok(Self {
            graph_schema: int_field(data, "graph_schema", 0)?,
            graph_version: str_field(data, "graph_version", ""),
            ontology_iri: str_field(data, "ontology_iri", DEFAULT_ONTOLOGY_IRI),
            cr_version: str_field(data, "cr_version", ""),
            requires_tools: str_field(data, "requires_tools", ""),
            sets: int_field(data, "sets", 0)?,
            card_individuals: int_field(data, "card_individuals", 0)?,
        })
    }

    /// The manifest as JSON data (the shape the bundle builder writes).
    #[must_use]
    pub fn to_json(&self) -> Value {
        Value::dict([
            ("graph_schema", Value::from(self.graph_schema)),
            ("graph_version", Value::from(self.graph_version.as_str())),
            ("ontology_iri", Value::from(self.ontology_iri.as_str())),
            ("cr_version", Value::from(self.cr_version.as_str())),
            ("requires_tools", Value::from(self.requires_tools.as_str())),
            ("sets", Value::from(self.sets)),
            ("card_individuals", Value::from(self.card_individuals)),
        ])
    }
}

/// Python's `type(value).__name__` for a decoded JSON value.
fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Int(_) => "int",
        Value::Float(_) => "float",
        Value::Str(_) => "str",
        Value::List(_) => "list",
        Value::Dict(_) => "dict",
    }
}

/// The manifest of the graph root at `root`, if it has one.
///
/// Returns `None` for a plain magic-cards checkout, which carries no
/// manifest and is always considered compatible.
///
/// # Errors
///
/// An unreadable, non-object or malformed manifest, naming the file.
pub fn load_manifest(root: &Path) -> Result<Option<GraphManifest>, GraphSchemaError> {
    let path = root.join(MANIFEST_FILE);
    if !path.is_file() {
        return Ok(None);
    }
    let shown = pycompat::pypath::path_str(&path);
    let text = pyio::read_text(&path)
        .map_err(|e| GraphSchemaError(format!("{shown}: cannot read the graph manifest: {e}")))?;
    let parsed: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| GraphSchemaError(format!("{shown}: cannot read the graph manifest: {e}")))?;
    let data = from_serde(&parsed);
    if !matches!(data, Value::Dict(_)) {
        return Err(GraphSchemaError(format!(
            "{shown}: expected a JSON object, got {}",
            type_name(&data)
        )));
    }
    GraphManifest::from_json(&data)
        .map(Some)
        .map_err(|e| GraphSchemaError(format!("{shown}: malformed graph manifest: {e}")))
}

/// Refuse a manifest whose `graph_schema` this tooling cannot read.
///
/// A `None` manifest (unversioned checkout) is always accepted.
///
/// # Errors
///
/// [`GraphSchemaError`] naming both versions.
pub fn check_compatible(
    manifest: Option<&GraphManifest>,
    root: &Path,
) -> Result<(), GraphSchemaError> {
    let Some(manifest) = manifest else {
        return Ok(());
    };
    if SUPPORTED_GRAPH_SCHEMA.contains(&manifest.graph_schema) {
        return Ok(());
    }
    let supported = SUPPORTED_GRAPH_SCHEMA
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    let version = if manifest.graph_version.is_empty() {
        "unknown"
    } else {
        &manifest.graph_version
    };
    let requires = if manifest.requires_tools.is_empty() {
        "(unspecified)"
    } else {
        &manifest.requires_tools
    };
    Err(GraphSchemaError(format!(
        "graph bundle at {} declares graph_schema {} (graph version {version}), but \
         {DISTRIBUTION} {} supports graph_schema {supported}. That bundle requires tools \
         {requires}: upgrade {DISTRIBUTION}, or pin a graph bundle matching this release.",
        pycompat::pypath::path_str(root),
        manifest.graph_schema,
        tools_version(),
    )))
}

/// The ontology namespace declared by the graph root at `root`
/// ([`DEFAULT_ONTOLOGY_IRI`] for an unversioned checkout).
///
/// # Errors
///
/// See [`load_manifest`].
pub fn ontology_iri(root: &Path) -> Result<String, GraphSchemaError> {
    Ok(load_manifest(root)?.map_or_else(|| DEFAULT_ONTOLOGY_IRI.to_owned(), |m| m.ontology_iri))
}

// ------------------------------------------------------------- graph root

/// The environment variable naming the graph root (a checkout or an
/// unpacked bundle); the Python packages read the same one.
pub const GRAPH_ROOT_ENV: &str = "MTG_GRAPH_ROOT";

/// The schema file every graph root carries (checkouts and bundles).
pub const ONTOLOGY_FILE: &str = "MagicCardsOntology.ttl";

/// Whether `dir` looks like a graph root: it holds [`ONTOLOGY_FILE`] and
/// a `sets/` directory.
#[must_use]
pub fn is_graph_root(dir: &Path) -> bool {
    dir.join(ONTOLOGY_FILE).is_file() && dir.join("sets").is_dir()
}

/// The nearest graph root at or above `start`.
#[must_use]
pub fn find_graph_root(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|dir| is_graph_root(dir))
        .map(Path::to_path_buf)
}

/// No usable graph root.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum GraphRootError {
    /// `MTG_GRAPH_ROOT` is set but does not name a directory.
    #[error("{GRAPH_ROOT_ENV}={0}: not a directory")]
    EnvNotDir(String),
    /// Neither the variable nor the working directory's ancestors give one.
    #[error(
        "no graph root: set {GRAPH_ROOT_ENV} to a magic-cards checkout or an unpacked graph \
         bundle, or run inside one (a directory holding {ONTOLOGY_FILE} and sets/)"
    )]
    NotFound,
}

/// The graph root of this process: `MTG_GRAPH_ROOT` when set (the Python
/// packages honour the same variable), else the nearest ancestor of the
/// working directory that [`is_graph_root`].
///
/// # Errors
///
/// See [`GraphRootError`].
pub fn graph_root() -> Result<PathBuf, GraphRootError> {
    if let Some(value) = std::env::var_os(GRAPH_ROOT_ENV)
        && !value.is_empty()
    {
        let dir = PathBuf::from(&value);
        return if dir.is_dir() {
            Ok(dir)
        } else {
            Err(GraphRootError::EnvNotDir(
                value.to_string_lossy().into_owned(),
            ))
        };
    }
    let cwd = std::env::current_dir().map_err(|_| GraphRootError::NotFound)?;
    find_graph_root(&cwd).ok_or(GraphRootError::NotFound)
}

#[cfg(test)]
mod tests {
    use super::{GraphManifest, check_compatible};
    use pycompat::json::Value;

    #[test]
    fn python_coercions() {
        let data = Value::dict([
            ("graph_schema", Value::from("2")),
            ("graph_version", Value::from(1.5)),
            ("sets", Value::Bool(true)),
        ]);
        let err = GraphManifest::from_json(&data).expect_err("True is not an int literal");
        assert_eq!(err, "invalid literal for int() with base 10: 'True'");

        let data = Value::dict([
            ("graph_schema", Value::from("2")),
            ("cr_version", Value::from(1.5)),
        ]);
        let manifest = GraphManifest::from_json(&data).unwrap();
        assert_eq!(
            (manifest.graph_schema, manifest.cr_version.as_str()),
            (2, "1.5")
        );
        let err = check_compatible(Some(&manifest), std::path::Path::new("bundle")).unwrap_err();
        assert!(
            err.0
                .contains("declares graph_schema 2 (graph version unknown)")
        );
        assert!(err.0.contains("requires tools (unspecified)"));
    }
}
