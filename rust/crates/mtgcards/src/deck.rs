//! Deck file parsing: txt decklists (here) and `.ttl` deck instance
//! graphs ([`crate::deck_ttl`]).
//!
//! Decklist format (one card per line, `//` comments, commander marked by
//! a `// Commander` section header before its line):
//!
//! ```text
//! 1 Sol Ring
//! 2 Island
//! // Commander
//! 1 Some Legendary Creature
//! ```
//!
//! The count may carry the `x` of Moxfield/Archidekt exports (`1x Sol
//! Ring`). Without a `// Commander` section the first card of the list is
//! the commander. Everything after `#` is stripped, as is a trailing
//! `(SET) collector-number [*F*]` printing suffix (set code in either
//! case). A line that is none of these is recorded in [`Deck::skipped`]
//! so callers can warn about it (fix C4).
//!
//! Lines are split like `str.splitlines()` and the file is read like
//! `Path.read_text()`, so a decklist reads the same here as in Python.

use std::path::Path;
use std::sync::LazyLock;

use indexmap::IndexMap;
use pycompat::pyio::{self, ReadTextError};
use pycompat::pypath::path_str;
use pycompat::pystr;
use regex::Regex;

/// A parsed decklist: expanded card names plus the commander.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Deck {
    /// the seat name: the file stem (made unique by the CLI when needed)
    pub name: String,
    /// the file the deck came from (`str(Path(path))`)
    pub path: String,
    /// card names, expanded (the 99)
    pub cards: Vec<String>,
    /// the commander, kept out of `cards`
    pub commander: Option<String>,
    /// source file format: `txt` or `ttl`
    pub fmt: String,
    /// txt lines the parser could not read: `(1-based line number, text)`
    pub skipped: Vec<(usize, String)>,
}

impl Deck {
    /// Total card count including the commander.
    #[must_use]
    pub fn size(&self) -> usize {
        self.cards.len() + usize::from(self.commander.as_deref().is_some_and(|c| !c.is_empty()))
    }
}

/// Errors of deck loading.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DeckError {
    /// The file could not be read (Python's `OSError` /
    /// `UnicodeDecodeError` text).
    #[error("{0}")]
    Read(ReadTextError),
    /// A `.ttl` deck was loaded without the knowledge graph's individuals.
    #[error("{0}: .ttl decks require the knowledge graph")]
    NeedsGraph(String),
    /// A `.ttl` deck references individuals the graph does not define.
    #[error("{0}")]
    Unresolved(String),
}

// Trailing printing suffix of exported lists, for example "(EOE) 219",
// "(PLST) SOM-144", "(LCI) 26 *F*" or, lowercased by some exporters,
// "(lci) 26".
static PRINTING_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\s+\([A-Za-z0-9]{2,6}\)\s+[A-Za-z0-9-]+\S*(\s+\*\w+\*)?$").expect("valid regex")
});
// "1", "1x", "4X"
static COUNT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(\d+)[xX]?$").expect("valid regex"));

/// The file stem of a deck path (Python `Path.stem`).
fn stem(path: &Path) -> String {
    path.file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Parse the deck file at `path`.
///
/// `.ttl` files are deck instance graphs and require `ind2name`, the
/// `{individual local name: card name}` map of the knowledge graph
/// (`CardDatabase::ind2name`); everything else is a txt decklist.
///
/// # Errors
///
/// See [`DeckError`].
pub fn load_deck(
    path: &Path,
    ind2name: Option<&IndexMap<String, String>>,
) -> Result<Deck, DeckError> {
    let is_ttl = path
        .extension()
        .is_some_and(|e| e.to_string_lossy().to_lowercase() == "ttl");
    if is_ttl {
        let Some(map) = ind2name else {
            return Err(DeckError::NeedsGraph(path_str(path)));
        };
        return crate::deck_ttl::load_deck_ttl(path, map);
    }
    let text = pyio::read_text(path).map_err(DeckError::Read)?;
    Ok(parse_decklist(&text, &stem(path), &path_str(path)))
}

/// Parse decklist text (see the module documentation).
#[must_use]
pub fn parse_decklist(text: &str, name: &str, path: &str) -> Deck {
    let mut deck = Deck {
        name: name.to_owned(),
        path: path.to_owned(),
        fmt: "txt".to_owned(),
        ..Deck::default()
    };
    let mut in_commander = false;
    for (lineno, raw) in pystr::splitlines(text).into_iter().enumerate() {
        let line = pystr::strip(raw.split('#').next().unwrap_or(""));
        if line.is_empty() {
            continue;
        }
        if line.starts_with("//") {
            // a section header like '// Commander' (exact word, not e.g.
            // '// Ramp & fixing (WUBRG commander)')
            let header = pystr::strip(line.trim_start_matches(['/', ' '])).to_lowercase();
            in_commander = header == "commander" || header == "commanders";
            continue;
        }
        let (count_str, rest) = line.split_once(' ').unwrap_or((line, ""));
        let rest = pystr::strip(rest);
        let count = COUNT_RE
            .captures(count_str)
            .and_then(|c| c[1].parse::<usize>().ok());
        let Some(count) = count.filter(|_| !rest.is_empty()) else {
            deck.skipped.push((lineno + 1, line.to_owned()));
            continue;
        };
        let mut count = count;
        // exported lists write double-faced cards as 'Front / Back'; the
        // card database uses the canonical 'Front // Back' form
        let card_name = PRINTING_RE.replace(rest, "").replace(" / ", " // ");
        if in_commander && deck.commander.is_none() {
            deck.commander = Some(card_name.clone());
            count = count.saturating_sub(1);
        }
        deck.cards.extend(std::iter::repeat_n(card_name, count));
    }
    if deck.commander.is_none() && !deck.cards.is_empty() {
        // no '// Commander' section: promote the first card of the list
        deck.commander = Some(deck.cards.remove(0));
    }
    deck
}

#[cfg(test)]
mod tests {
    use super::parse_decklist;

    #[test]
    fn counts_suffixes_and_sections() {
        let deck = parse_decklist(
            "# comment\n// Commander\n1x Pia Nalaar, Chief Mechanic\n2 Sol Ring (lci) 26\r\n4X Forest (LCI) 300 *F*\nSol Ring\n1 Delver of Secrets / Insectile Aberration\n",
            "d",
            "d.txt",
        );
        assert_eq!(
            deck.commander.as_deref(),
            Some("Pia Nalaar, Chief Mechanic")
        );
        assert_eq!(deck.cards[..2], ["Sol Ring", "Sol Ring"]);
        assert_eq!(deck.cards[2..6], ["Forest", "Forest", "Forest", "Forest"]);
        assert_eq!(deck.cards[6], "Delver of Secrets // Insectile Aberration");
        assert_eq!(deck.skipped, vec![(6, "Sol Ring".to_owned())]);
    }

    #[test]
    fn first_card_is_the_commander_without_a_section() {
        let deck = parse_decklist("1 Pia Nalaar, Chief Mechanic\n2 Sol Ring\n", "d", "d.txt");
        assert_eq!(
            deck.commander.as_deref(),
            Some("Pia Nalaar, Chief Mechanic")
        );
        assert_eq!(deck.cards, ["Sol Ring", "Sol Ring"]);
        assert_eq!(deck.size(), 3);
    }
}
