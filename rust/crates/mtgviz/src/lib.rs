//! Game visualization: recordings, replay and the live terminal UI.
//!
//! The Rust port of the Python `mtgviz` package: `schema` (the event and
//! snapshot records), `recorder` (attached to the engine's log stream;
//! the `--viz-file` JSONL writer, byte-identical to the Python output so
//! existing recordings replay), `replay` and `live` (the `--replay` and
//! `--watch` front-ends), `tui` (the ratatui frame) and `keys`.
//!
//! The recorder and schema land in milestone M7, the user interface in
//! M8; this is the workspace scaffold.
