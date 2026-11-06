//! Game visualization: recordings, replay and the live terminal UI.
//!
//! Ported from the Python `mtgviz` package, which it replaced:
//!
//! - [`schema`]: the JSON event / snapshot records
//! - [`recorder`]: the recorder attached to the engine's log stream and
//!   the `--viz-file` JSONL writer (byte-identical to the Python output,
//!   so recordings made before the cutover replay unchanged)
//! - [`view`]: the cursor over a record stream, event formatting and the
//!   plain text frame
//! - [`replay`] and [`live`]: the `--replay` and `--watch` front-ends
//! - [`tui`] and [`keys`]: the ratatui frame and the keyboard
//!
//! Where the Python package rendered with `rich` and ran the engine on a
//! worker thread, this crate renders with ratatui and paces the engine
//! from the recorder sink on the main thread (the engine's arena is
//! single-threaded by design).

pub mod keys;
pub mod live;
pub mod recorder;
pub mod replay;
pub mod schema;
pub mod tui;
pub mod view;

pub use recorder::{Recorder, VizWriter};
pub use view::{ViewState, format_event};
