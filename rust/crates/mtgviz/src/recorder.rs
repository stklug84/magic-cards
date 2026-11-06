//! Recorder: taps a game's log stream, emits schema records (the Python
//! `recorder.py`).
//!
//! Usage (wired by `mtgrules::adapter::run_game`):
//!
//! ```text
//! let recorder = Recorder::new(sink);        // sink: FnMut(Record)
//! run_game(..., Some(&shared(recorder)))     // attach() + on_event() + finish()
//! ```
//!
//! Snapshots are taken after state-changing events so the TUI board
//! stays current; pure log chatter (draw, trigger, ...) only appends to
//! the log.

use std::cell::RefCell;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::rc::Rc;

use mtgrules::Game;
use mtgrules::adapter::{Recorder as RecorderLike, SharedRecorder};
use mtgrules::objects::PlayerId;
use mtgrules::protocols::LogFields;
use pycompat::json::{Value, dumps};

use crate::schema::{self, Record};

/// Events after which the board snapshot is refreshed.
pub const SNAPSHOT_AFTER: &[&str] = &[
    "phase",
    "resolve",
    "land",
    "attack",
    "block",
    "dies",
    "token",
    "counter",
    "life",
    "player_loses",
    "fizzle",
    "cast",
    "activate",
];

/// Recorder output callback (JSONL writer, queue push, Vec push, ...).
pub type RecordSink = Box<dyn FnMut(Record)>;

/// Translates one game's log events into schema records for a sink.
pub struct Recorder {
    sink: RecordSink,
    attached: bool,
    seq: u64,
}

impl Recorder {
    /// Send all records of one game to `sink`.
    #[must_use]
    pub fn new(sink: RecordSink) -> Self {
        Recorder {
            sink,
            attached: false,
            seq: 0,
        }
    }

    /// A recorder appending every record to `records`.
    #[must_use]
    pub fn collecting(records: Rc<RefCell<Vec<Record>>>) -> Self {
        Recorder::new(Box::new(move |rec| records.borrow_mut().push(rec)))
    }

    /// The shared handle `run_game` takes.
    #[must_use]
    pub fn shared(self) -> SharedRecorder {
        Rc::new(RefCell::new(self))
    }
}

impl RecorderLike for Recorder {
    /// Bind the game and emit the opening snapshot (opening hands).
    fn attach(&mut self, game: &Game) {
        self.attached = true;
        self.seq += 1;
        (self.sink)(schema::snapshot(game, self.seq));
    }

    /// Record one engine log event (plus a snapshot when it mutates).
    fn on_event(&mut self, game: &Game, kind: &str, fields: &LogFields) {
        if !self.attached {
            return;
        }
        self.seq += 1;
        (self.sink)(schema::event(game, self.seq, kind, fields));
        if SNAPSHOT_AFTER.contains(&kind) {
            self.seq += 1;
            (self.sink)(schema::snapshot(game, self.seq));
        }
    }

    /// Emit the final snapshot and the game-footer record.
    fn finish(&mut self, game: &Game, winner: Option<PlayerId>, reason: &str) {
        self.seq += 1;
        (self.sink)(schema::snapshot(game, self.seq));
        let winner = winner.map(|w| game.player(w).name.clone());
        (self.sink)(schema::game_end(winner.as_deref(), reason, game.turn));
    }
}

/// Writes recorder streams for one or more games to a JSONL file.
#[derive(Clone)]
pub struct VizWriter {
    file: Rc<RefCell<BufWriter<std::fs::File>>>,
}

impl VizWriter {
    /// Open `path` for writing; [`close`](Self::close) flushes it.
    ///
    /// # Errors
    ///
    /// The file cannot be created.
    pub fn open(path: &Path) -> std::io::Result<Self> {
        Ok(VizWriter {
            file: Rc::new(RefCell::new(BufWriter::new(std::fs::File::create(path)?))),
        })
    }

    fn write_record(&self, rec: &Value) {
        let mut file = self.file.borrow_mut();
        // a failed write of a recording is reported once, at close
        let _ = writeln!(file, "{}", dumps(rec));
    }

    /// Write the game header and return the record sink for that game.
    pub fn game_sink(&self, game_no: u64, seed: i64) -> RecordSink {
        self.write_record(&schema::game_header(game_no, seed));
        let writer = self.clone();
        Box::new(move |rec: Record| writer.write_record(&rec))
    }

    /// A recorder for one game, writing through this file.
    #[must_use]
    pub fn recorder(&self, game_no: u64, seed: i64) -> SharedRecorder {
        Recorder::new(self.game_sink(game_no, seed)).shared()
    }

    /// Flush the JSONL file.
    ///
    /// # Errors
    ///
    /// The buffered records cannot be written.
    pub fn close(&self) -> std::io::Result<()> {
        self.file.borrow_mut().flush()
    }
}
