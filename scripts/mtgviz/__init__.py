"""mtgviz - game visualization for the mtgrules engine.

  schema.py    JSON-serializable event / snapshot records, engine protocols
  recorder.py  Recorder (attaches to a Game via the log tap) + VizWriter
  replay.py    JSONL loading, --replay entry point, replay app + fallback
  tui.py       rich-based rendering (view state, frames), plain fallback
  keys.py      raw-terminal keyboard input (stdlib)
  live.py      --watch: run one game and render it live

The TUI requires the optional 'rich' dependency (pip install rich);
--replay degrades to a plain-text step viewer without it.
"""
