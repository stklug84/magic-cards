//! Graph-root discovery: `MTG_GRAPH_ROOT`, else the nearest ancestor of
//! the working directory holding `MagicCardsOntology.ttl` and `sets/`.
//! (The environment-dependent `graph_root()` itself is exercised through
//! the `mtg-cr` binary tests, process by process.)

mod common;

use mtgcards::graph::{GraphRootError, find_graph_root, is_graph_root};

#[test]
fn a_root_needs_the_ontology_and_the_sets_dir() {
    let tmp = tempfile::tempdir().expect("tempdir");
    assert!(!is_graph_root(tmp.path()));
    std::fs::write(tmp.path().join("MagicCardsOntology.ttl"), "").unwrap();
    assert!(!is_graph_root(tmp.path()));
    std::fs::create_dir(tmp.path().join("sets")).unwrap();
    assert!(is_graph_root(tmp.path()));
    // a file named sets is not a sets directory
    let other = tempfile::tempdir().expect("tempdir");
    std::fs::write(other.path().join("MagicCardsOntology.ttl"), "").unwrap();
    std::fs::write(other.path().join("sets"), "").unwrap();
    assert!(!is_graph_root(other.path()));
}

#[test]
fn the_nearest_ancestor_wins() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let outer = tmp.path().join("outer");
    let inner = outer.join("work").join("inner");
    for root in [&outer, &inner] {
        std::fs::create_dir_all(root.join("sets")).unwrap();
        std::fs::write(root.join("MagicCardsOntology.ttl"), "").unwrap();
    }
    let deep = inner.join("decks").join("x");
    std::fs::create_dir_all(&deep).unwrap();
    assert_eq!(find_graph_root(&deep).as_deref(), Some(inner.as_path()));
    assert_eq!(
        find_graph_root(&outer.join("work")).as_deref(),
        Some(outer.as_path())
    );
    assert_eq!(find_graph_root(&inner).as_deref(), Some(inner.as_path()));
    let unrelated = tempfile::tempdir().expect("tempdir");
    assert_eq!(find_graph_root(unrelated.path()), None);
}

#[test]
fn this_checkout_is_a_graph_root() {
    let repo = common::repo_root();
    assert!(is_graph_root(&repo), "{}", repo.display());
    assert_eq!(
        find_graph_root(&repo.join("rust").join("crates")).as_deref(),
        Some(repo.as_path())
    );
    let err = GraphRootError::NotFound.to_string();
    assert!(
        err.contains("MTG_GRAPH_ROOT") && err.contains("MagicCardsOntology.ttl"),
        "{err}"
    );
    assert_eq!(
        GraphRootError::EnvNotDir("/x".into()).to_string(),
        "MTG_GRAPH_ROOT=/x: not a directory"
    );
}
