//! `Path.read_text(encoding="utf-8")` and the text of the errors it
//! raises.
//!
//! Python's text mode translates `\r\n` and lone `\r` to `\n` (universal
//! newlines), so a CRLF deck list or oracle text reads the same on every
//! platform; [`read_text`] does the same. Its errors render like
//! `str(OSError)` (`[Errno 2] No such file or directory: 'x'`) and
//! `str(UnicodeDecodeError)` (`'utf-8' codec can't decode byte 0xff in
//! position 3: invalid start byte`), so messages that embed `{err}` match
//! the Python ones.

use std::fmt;
use std::io;
use std::path::Path;

use crate::pyrepr::repr_str;

/// The failure of a text read, rendered like the Python exception.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReadTextError {
    /// An `OSError`: `[Errno N] strerror: 'path'`.
    Os {
        /// `errno`, when the error came from the OS
        code: Option<i32>,
        /// the OS description (`strerror`)
        strerror: String,
        /// the path, as given
        path: String,
    },
    /// A `UnicodeDecodeError` of the strict UTF-8 codec.
    Decode {
        /// first invalid byte offset
        start: usize,
        /// one past the last invalid byte
        end: usize,
        /// the first invalid byte
        byte: u8,
        /// `invalid start byte`, `invalid continuation byte` or
        /// `unexpected end of data`
        reason: &'static str,
    },
}

impl fmt::Display for ReadTextError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ReadTextError::Os {
                code,
                strerror,
                path,
            } => match code {
                Some(code) => write!(f, "[Errno {code}] {strerror}: {}", repr_str(path)),
                None => write!(f, "{strerror}: {}", repr_str(path)),
            },
            ReadTextError::Decode {
                start,
                end,
                byte,
                reason,
            } => {
                if end - start > 1 {
                    write!(
                        f,
                        "'utf-8' codec can't decode bytes in position {start}-{}: {reason}",
                        end - 1
                    )
                } else {
                    write!(
                        f,
                        "'utf-8' codec can't decode byte {byte:#04x} in position {start}: {reason}"
                    )
                }
            }
        }
    }
}

impl std::error::Error for ReadTextError {}

/// `str(OSError)` for an I/O error raised on `path`.
#[must_use]
pub fn os_error(err: &io::Error, path: &Path) -> ReadTextError {
    let text = err.to_string();
    let strerror = match err.raw_os_error() {
        // std renders OS errors as "<strerror> (os error N)"
        Some(code) => text
            .strip_suffix(&format!(" (os error {code})"))
            .unwrap_or(&text)
            .to_owned(),
        None => text,
    };
    ReadTextError::Os {
        code: err.raw_os_error(),
        strerror,
        path: path.display().to_string(),
    }
}

/// `str(UnicodeDecodeError)` for `bytes` failing strict UTF-8 decoding.
fn decode_error(bytes: &[u8], err: &std::str::Utf8Error) -> ReadTextError {
    let start = err.valid_up_to();
    let byte = bytes[start];
    let (end, reason) = match err.error_len() {
        None => (bytes.len(), "unexpected end of data"),
        Some(len) => {
            // a byte that can start a sequence (C2..F4) failed on what
            // followed; anything else cannot start one
            let reason = if (0xC2..=0xF4).contains(&byte) {
                "invalid continuation byte"
            } else {
                "invalid start byte"
            };
            (start + len, reason)
        }
    };
    ReadTextError::Decode {
        start,
        end,
        byte,
        reason,
    }
}

/// Python's universal-newline translation: `\r\n` and `\r` become `\n`.
#[must_use]
pub fn universal_newlines(text: &str) -> String {
    if !text.contains('\r') {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\r' {
            if chars.peek() == Some(&'\n') {
                chars.next();
            }
            out.push('\n');
        } else {
            out.push(c);
        }
    }
    out
}

/// `Path(path).read_text(encoding="utf-8")`.
///
/// # Errors
///
/// [`ReadTextError`], rendered like the Python exception.
pub fn read_text(path: &Path) -> Result<String, ReadTextError> {
    let bytes = std::fs::read(path).map_err(|e| os_error(&e, path))?;
    let text = std::str::from_utf8(&bytes).map_err(|e| decode_error(&bytes, &e))?;
    Ok(universal_newlines(text))
}

#[cfg(test)]
mod tests {
    use super::{ReadTextError, decode_error, read_text, universal_newlines};

    fn decode(bytes: &[u8]) -> String {
        let err = std::str::from_utf8(bytes).expect_err("invalid");
        decode_error(bytes, &err).to_string()
    }

    #[test]
    fn newline_translation() {
        assert_eq!(universal_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
        assert_eq!(universal_newlines("plain"), "plain");
    }

    #[test]
    fn decode_errors_read_like_python() {
        assert_eq!(
            decode(b"ab\xff"),
            "'utf-8' codec can't decode byte 0xff in position 2: invalid start byte"
        );
        assert_eq!(
            decode(b"\xe2\x28\xa1"),
            "'utf-8' codec can't decode byte 0xe2 in position 0: invalid continuation byte"
        );
        assert_eq!(
            decode(b"x\xe2\x82"),
            "'utf-8' codec can't decode bytes in position 1-2: unexpected end of data"
        );
        assert_eq!(
            decode(b"\xe2\x82\x28"),
            "'utf-8' codec can't decode bytes in position 0-1: invalid continuation byte"
        );
    }

    #[test]
    fn missing_file_is_an_os_error() {
        let dir = std::env::temp_dir().join("pycompat-pyio-missing");
        let err = read_text(&dir.join("nope.txt")).expect_err("missing");
        match &err {
            ReadTextError::Os {
                code: Some(2),
                strerror,
                ..
            } => {
                assert_eq!(strerror, "No such file or directory");
            }
            other => panic!("unexpected {other:?}"),
        }
        assert!(
            err.to_string()
                .starts_with("[Errno 2] No such file or directory: '")
        );
    }
}
