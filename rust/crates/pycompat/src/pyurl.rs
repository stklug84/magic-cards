//! `urllib.parse.quote`: the percent-encoding of the Scryfall lookup URL.
//!
//! The cache file of the Scryfall client is shared with the Python client
//! and keyed by card name, not URL, but the request URL itself appears in
//! logs and tests (`Llanowar%20Elves`), so the encoding is the Python
//! one: UTF-8 bytes, `A-Za-z0-9_.-~` and `/` kept, everything else as
//! `%XX` in upper case.

use std::fmt::Write as _;

/// `urllib.parse.quote(text)` with the default `safe="/"`.
#[must_use]
pub fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-' | b'~' | b'/') {
            out.push(char::from(byte));
        } else {
            let _ = write!(out, "%{byte:02X}");
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::quote;

    #[test]
    fn quote_like_urllib() {
        assert_eq!(quote("Llanowar Elves"), "Llanowar%20Elves");
        assert_eq!(
            quote("Pia Nalaar, Chief Mechanic"),
            "Pia%20Nalaar%2C%20Chief%20Mechanic"
        );
        assert_eq!(quote("Lim-Dûl's Vault"), "Lim-D%C3%BBl%27s%20Vault");
        assert_eq!(quote("a/b_c.d~e"), "a/b_c.d~e");
    }
}
