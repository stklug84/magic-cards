//! A name index over shared card records.
//!
//! The Python data layer is a `dict[str, CardData]` in which several
//! names may point at the *same* object: a double-faced card is indexed
//! under its full name and its front face, a Scryfall result under its
//! name, its front face and the name that was looked up. Behaviour
//! derivation runs once per object (`id()`-deduplicated) and a mutation
//! through one name is visible through the others. [`CardIndex`] keeps
//! those semantics with an arena of records and a name → record map.

use indexmap::IndexMap;

use crate::cards::CardData;

/// Handle of a record in a [`CardIndex`] (Python's `id(card)`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct CardId(usize);

/// Name-indexed card records with shared (aliased) entries.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CardIndex {
    records: Vec<CardData>,
    by_name: IndexMap<String, CardId>,
}

impl CardIndex {
    /// An empty index.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// `index[name] = CardData(...)`: a new record under `name`.
    pub fn insert(&mut self, name: impl Into<String>, card: CardData) -> CardId {
        let id = CardId(self.records.len());
        self.records.push(card);
        self.by_name.insert(name.into(), id);
        id
    }

    /// `index[name] = index[other]`: another name for an existing record.
    ///
    /// # Panics
    ///
    /// Panics on a handle from another index.
    pub fn alias(&mut self, name: impl Into<String>, id: CardId) {
        assert!(id.0 < self.records.len(), "card handle from another index");
        self.by_name.insert(name.into(), id);
    }

    /// `index.setdefault(name, index[other])`.
    ///
    /// # Panics
    ///
    /// Panics on a handle from another index.
    pub fn alias_if_absent(&mut self, name: impl Into<String>, id: CardId) {
        assert!(id.0 < self.records.len(), "card handle from another index");
        self.by_name.entry(name.into()).or_insert(id);
    }

    /// `name in index`.
    #[must_use]
    pub fn contains(&self, name: &str) -> bool {
        self.by_name.contains_key(name)
    }

    /// The handle of the record under `name`.
    #[must_use]
    pub fn id_of(&self, name: &str) -> Option<CardId> {
        self.by_name.get(name).copied()
    }

    /// `index.get(name)`.
    #[must_use]
    pub fn get(&self, name: &str) -> Option<&CardData> {
        self.by_name.get(name).map(|id| &self.records[id.0])
    }

    /// The record under `name`, mutably.
    pub fn get_mut(&mut self, name: &str) -> Option<&mut CardData> {
        let id = *self.by_name.get(name)?;
        Some(&mut self.records[id.0])
    }

    /// The record behind a handle.
    #[must_use]
    pub fn record(&self, id: CardId) -> &CardData {
        &self.records[id.0]
    }

    /// The record behind a handle, mutably.
    pub fn record_mut(&mut self, id: CardId) -> &mut CardData {
        &mut self.records[id.0]
    }

    /// Whether two names resolve to the same record (`a is b`).
    #[must_use]
    pub fn same_record(&self, a: &str, b: &str) -> bool {
        matches!((self.id_of(a), self.id_of(b)), (Some(x), Some(y)) if x == y)
    }

    /// The indexed names, in insertion order (`index.keys()`).
    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.by_name.keys().map(String::as_str)
    }

    /// `(name, record)` pairs in insertion order (`index.items()`).
    pub fn iter(&self) -> impl Iterator<Item = (&str, &CardData)> {
        self.by_name
            .iter()
            .map(|(name, id)| (name.as_str(), &self.records[id.0]))
    }

    /// Number of names.
    #[must_use]
    pub fn len(&self) -> usize {
        self.by_name.len()
    }

    /// Whether no name is indexed.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.by_name.is_empty()
    }

    /// Every distinct record once, in the order of its first name
    /// (the `id()`-deduplicated walk over `index.values()`).
    #[must_use]
    pub fn unique_ids(&self) -> Vec<CardId> {
        let mut seen = vec![false; self.records.len()];
        let mut ids = Vec::new();
        for id in self.by_name.values() {
            if !seen[id.0] {
                seen[id.0] = true;
                ids.push(*id);
            }
        }
        ids
    }

    /// `index.update(other)`: every name of `other` now resolves to the
    /// record it had there (aliases within `other` stay aliases).
    pub fn update(&mut self, other: CardIndex) {
        let offset = self.records.len();
        self.records.extend(other.records);
        for (name, id) in other.by_name {
            self.by_name.insert(name, CardId(id.0 + offset));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::CardIndex;
    use crate::cards::CardData;

    #[test]
    fn aliases_share_one_record() {
        let mut index = CardIndex::new();
        let id = index.insert("A // B", CardData::new("A // B", "graph"));
        index.alias_if_absent("A", id);
        let other = index.insert("other", CardData::new("other", "graph"));
        index.alias_if_absent("A", other);
        assert!(index.same_record("A", "A // B"));
        index.get_mut("A").unwrap().mv = 7;
        assert_eq!(index.get("A // B").unwrap().mv, 7);
        assert_eq!(index.names().collect::<Vec<_>>(), ["A // B", "A", "other"]);
        assert_eq!(index.unique_ids().len(), 2);
    }

    #[test]
    fn update_keeps_the_other_sides_aliases() {
        let mut base = CardIndex::new();
        base.insert("X", CardData::new("X", "graph"));
        let mut other = CardIndex::new();
        let id = other.insert("X", CardData::new("X", "custom"));
        other.alias("Y", id);
        base.update(other);
        assert_eq!(base.get("X").unwrap().source, "custom");
        assert!(base.same_record("X", "Y"));
        assert_eq!(base.len(), 2);
    }
}
