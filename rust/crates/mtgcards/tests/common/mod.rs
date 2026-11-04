//! Shared helpers of the `mtgcards` integration tests.

#![allow(dead_code, reason = "each test binary uses a subset of the helpers")]

use std::path::{Path, PathBuf};

/// The magic-cards checkout: three levels above this crate's manifest.
pub(crate) fn repo_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .ancestors()
        .nth(3)
        .map(Path::to_path_buf)
        .expect("rust/crates/mtgcards sits three levels below the repo root")
}

/// Write `text` under `dir/name` and return the path.
pub(crate) fn write(dir: &Path, name: &str, text: &str) -> PathBuf {
    let path = dir.join(name);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).expect("create parent dir");
    }
    std::fs::write(&path, text).expect("write test file");
    path
}

/// The Turtle prefix header every synthetic set file starts with.
pub(crate) const TTL_HEADER: &str = "\
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix :     <urn:test#> .

";
