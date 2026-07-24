"""mtgviz - game visualization for the mtgrules engine.

  schema.py    JSON-serializable event / snapshot records
  recorder.py  Recorder (attaches to a Game via the log tap) + VizWriter
  replay.py    JSONL loading and --replay entry point
  tui.py       rich-based TUI renderer (replay + live), plain fallback
  keys.py      raw-terminal keyboard input (stdlib)
  live.py      --watch: run one game and render it live

The TUI requires the optional 'rich' dependency (pip install rich);
--replay degrades to a plain-text step viewer without it.
"""
