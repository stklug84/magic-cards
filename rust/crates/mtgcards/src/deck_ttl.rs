//! Deck instance-graph (`.ttl`) parsing.
//!
//! Deck graphs reference card individuals; they do not embed card
//! characteristics. Contents come from the reified deck entries
//! (`:hasDeckEntry` -> `:entryCard` + `:quantity`), with the plain
//! `:hasCard` list as fallback (one copy each), and the commander from the
//! `:isCommanderOf` assertion. Individual local names resolve to card
//! names through the `{individual: card name}` map the graph loader
//! builds. Regex-based, like the Python `mtgcards/deck_ttl.py`.

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::LazyLock;

use indexmap::IndexMap;
use pycompat::pyio;
use pycompat::pypath::path_str;
use regex::Regex;

use crate::deck::{Deck, DeckError};

static COMMANDER_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r":(\w+)\s+:isCommanderOf\b").expect("valid regex"));
static ENTRY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#":entryCard\s+:(\w+)\s*;\s*:quantity\s+"(\d+)""#).expect("valid regex")
});
// the :hasCard object list, terminated by the next ';' or the final '.'
static HASCARD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s):hasCard\s+(.*?)[;.]").expect("valid regex"));
static IND_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r":(\w+)").expect("valid regex"));

/// How many unresolved individuals the error message lists before
/// eliding.
const UNRESOLVED_PREVIEW: usize = 8;

/// `(individual, copies)` pairs of a deck graph.
#[must_use]
pub fn entries(text: &str) -> Vec<(String, usize)> {
    let reified: Vec<(String, usize)> = ENTRY_RE
        .captures_iter(text)
        .map(|c| (c[1].to_owned(), c[2].parse().unwrap_or(0)))
        .collect();
    if !reified.is_empty() {
        return reified;
    }
    // no reified DeckEntry individuals: fall back to the :hasCard list
    HASCARD_RE
        .captures(text)
        .map(|m| {
            IND_RE
                .captures_iter(&m[1])
                .map(|c| (c[1].to_owned(), 1))
                .collect()
        })
        .unwrap_or_default()
}

/// Parse the deck instance graph at `path`.
///
/// # Errors
///
/// [`DeckError::Unresolved`] when the graph references individuals the
/// knowledge graph does not define; [`DeckError::Read`] on read errors.
pub fn load_deck_ttl(path: &Path, ind2name: &IndexMap<String, String>) -> Result<Deck, DeckError> {
    let text = pyio::read_text(path).map_err(DeckError::Read)?;
    let shown = path_str(path);
    let entries = entries(&text);
    let commander_ind = COMMANDER_RE.captures(&text).map(|c| c[1].to_owned());
    let mut referenced: BTreeSet<&str> = entries.iter().map(|(ind, _)| ind.as_str()).collect();
    if let Some(ind) = &commander_ind {
        referenced.insert(ind);
    }
    let unresolved: Vec<&str> = referenced
        .iter()
        .copied()
        .filter(|ind| !ind2name.contains_key(*ind))
        .collect();
    if !unresolved.is_empty() {
        let shown_inds = unresolved
            .iter()
            .take(UNRESOLVED_PREVIEW)
            .copied()
            .collect::<Vec<_>>();
        let more = if unresolved.len() > UNRESOLVED_PREVIEW {
            " ..."
        } else {
            ""
        };
        return Err(DeckError::Unresolved(format!(
            "{shown}: {} card individual(s) not found in the knowledge graph: {}{more}",
            unresolved.len(),
            shown_inds.join(", ")
        )));
    }
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut deck = Deck {
        name: stem,
        path: shown,
        fmt: "ttl".to_owned(),
        commander: commander_ind.as_ref().map(|ind| ind2name[ind].clone()),
        ..Deck::default()
    };
    let mut commander_taken = false;
    for (ind, qty) in entries {
        let mut count = qty;
        if !commander_taken && Some(&ind) == commander_ind.as_ref() {
            // the commander is listed among the deck entries; the Deck
            // model keeps it out of the 99 (deck.cards)
            count = count.saturating_sub(1);
            commander_taken = true;
        }
        deck.cards
            .extend(std::iter::repeat_n(ind2name[&ind].clone(), count));
    }
    Ok(deck)
}
