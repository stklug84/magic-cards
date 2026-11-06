//! `str(pathlib.Path(p))`: the normalised spelling Python gives a path.
//!
//! `pathlib` collapses repeated separators and `.` components and drops a
//! trailing separator (`decks//./a.ttl/` → `decks/a.ttl`) but keeps `..`
//! and, on POSIX, exactly two leading slashes. Deck names and every
//! message that embeds a path go through this so they read as Python
//! wrote them.

use std::path::Path;

/// `str(PurePosixPath(text))`.
#[must_use]
pub fn posix_path_str(text: &str) -> String {
    let root = if text.starts_with("//") && !text.starts_with("///") {
        "//"
    } else if text.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<&str> = text
        .split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect();
    let joined = parts.join("/");
    if root.is_empty() && joined.is_empty() {
        ".".to_owned()
    } else {
        format!("{root}{joined}")
    }
}

/// `str(Path(path))` for the running platform.
#[must_use]
pub fn path_str(path: &Path) -> String {
    let text = path.to_string_lossy();
    if cfg!(windows) {
        // WindowsPath normalises differently; keep the spelling as given
        text.into_owned()
    } else {
        posix_path_str(&text)
    }
}

#[cfg(test)]
mod tests {
    use super::posix_path_str;

    #[test]
    fn pathlib_normalisation() {
        assert_eq!(posix_path_str("decks//./a.ttl/"), "decks/a.ttl");
        assert_eq!(posix_path_str("./a"), "a");
        assert_eq!(posix_path_str(""), ".");
        assert_eq!(posix_path_str("."), ".");
        assert_eq!(posix_path_str(".."), "..");
        assert_eq!(posix_path_str("/tmp//x/"), "/tmp/x");
        assert_eq!(posix_path_str("//net/share"), "//net/share");
        assert_eq!(posix_path_str("///a"), "/a");
        assert_eq!(posix_path_str("/"), "/");
    }
}
