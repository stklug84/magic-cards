"""mtgrules - a CR-grounded Magic: The Gathering rules engine.

Built against MagicCompRules-20260227.txt (parsed and indexed by cr.py;
every engine function is annotated with the rules it implements via
@rule). Card semantics come from the knowledge graph's oracle text
through compiler.py, with hand-written implementations in overrides.py
for cards beyond the grammar. See `python3 -m mtgrules.adapter --help`
(from scripts/) to run matches, and cr.coverage_report() for the
implemented/unsupported rule listing.

Modules:
  cr            CR text parser, @rule traceability, coverage report
  objects       game objects, characteristics, zones, players (1xx/2xx/4xx)
  events        event bus records for replacements and triggers
  manasys       mana pools and cost payment (106/118/202)
  layers        continuous effects, full layer system (611-613)
  replacements  replacement/prevention effects (614-616)
  abilities     ability and target models (112/113/115/602-605)
  effects       one-shot effect AST (610)
  game          stack, priority, casting, SBAs, commander (117/405/601-608/
                704/903)
  combat        combat phase (506-511)
  turns         turn structure (500-514)
  compiler      oracle text -> ability AST (R2)
  overrides     hand-written card implementations
  policy        default AI decision policy over engine-legal actions
  adapter       decklist match runner + model-coverage report
"""

__version__ = "0.1"
