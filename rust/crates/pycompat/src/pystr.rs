//! `str` methods whose edge cases differ from the Rust standard library:
//! `str.splitlines()` and `int(text)`.
//!
//! The deck and manifest readers split text the way Python does and parse
//! counts the way `int()` does, so a file that Python reads one way is not
//! read another way here.

use crate::pyrepr::repr_str;

/// Whether `c` ends a line for `str.splitlines()`.
///
/// Python splits on `\n`, `\r`, `\r\n`, `\v`, `\f`, `\x1c`, `\x1d`,
/// `\x1e`, `\x85`, `\u2028` and `\u2029` — not on `\n` alone like
/// [`str::lines`].
#[must_use]
pub fn is_line_boundary(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\u{0b}'
            | '\u{0c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// `text.splitlines()`: the lines without their terminators; a trailing
/// terminator does not produce an empty last line.
#[must_use]
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut iter = text.char_indices().peekable();
    while let Some((i, c)) = iter.next() {
        if !is_line_boundary(c) {
            continue;
        }
        lines.push(&text[start..i]);
        let mut end = i + c.len_utf8();
        if c == '\r'
            && let Some(&(j, '\n')) = iter.peek()
        {
            iter.next();
            end = j + 1;
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Whether `c` is whitespace for `str.strip()` and `int()`.
///
/// Python's notion (`Py_UNICODE_ISSPACE`) is the Unicode `White_Space`
/// property plus the ASCII separators `\x1c`..`\x1f`.
#[must_use]
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}

/// `text.strip()`.
#[must_use]
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `int(text)` in base 10: surrounding whitespace, an optional sign and
/// ASCII digits with single underscores between them.
///
/// # Errors
///
/// The `ValueError` message Python raises:
/// `invalid literal for int() with base 10: '<text>'`.
pub fn parse_int(text: &str) -> Result<i128, String> {
    let invalid = || format!("invalid literal for int() with base 10: {}", repr_str(text));
    let body = strip(text);
    let (negative, digits) = match body.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, body.strip_prefix('+').unwrap_or(body)),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') {
        return Err(invalid());
    }
    let mut value: i128 = 0;
    let mut previous_underscore = false;
    for c in digits.chars() {
        if c == '_' {
            if previous_underscore {
                return Err(invalid());
            }
            previous_underscore = true;
            continue;
        }
        previous_underscore = false;
        let digit = c.to_digit(10).ok_or_else(invalid)?;
        value = value
            .checked_mul(10)
            .and_then(|v| v.checked_add(i128::from(digit)))
            .ok_or_else(|| "int too large for this port (128 bits)".to_owned())?;
    }
    Ok(if negative { -value } else { value })
}

#[cfg(test)]
mod tests {
    use super::{parse_int, splitlines, strip};

    #[test]
    fn splitlines_like_python() {
        assert_eq!(splitlines("a\nb\r\nc\rd"), ["a", "b", "c", "d"]);
        assert_eq!(splitlines("a\n"), ["a"]);
        assert_eq!(splitlines("\n"), [""]);
        assert_eq!(splitlines(""), [] as [&str; 0]);
        assert_eq!(splitlines("x\u{2028}y\u{0c}z"), ["x", "y", "z"]);
        assert_eq!(splitlines("tail"), ["tail"]);
    }

    #[test]
    fn int_literals() {
        assert_eq!(parse_int(" 42 "), Ok(42));
        assert_eq!(parse_int("-7"), Ok(-7));
        assert_eq!(parse_int("+1_000"), Ok(1000));
        assert_eq!(
            parse_int("1.0"),
            Err("invalid literal for int() with base 10: '1.0'".to_owned())
        );
        assert!(parse_int("").is_err());
        assert!(parse_int("1__0").is_err());
        assert!(parse_int("_1").is_err());
        assert!(parse_int("1_").is_err());
        assert_eq!(strip("\u{1f} a \t"), "a");
    }
}
