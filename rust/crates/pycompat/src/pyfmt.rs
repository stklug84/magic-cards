//! The subset of Python's format-spec mini-language the reports use.
//!
//! `[[fill]align][sign][0][width][.precision][type]` for floats (`f`,
//! `g`, `e` or none), integers (`d` or none) and strings (`s` or none).
//! Width counts code points, like Python; numbers align right and
//! strings left by default; `0` zero-pads after the sign; `^` centres
//! with the extra space on the right.

use crate::pyfloat;

/// A parsed format spec.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Spec {
    fill: char,
    align: Option<char>,
    sign: char,
    zero: bool,
    width: usize,
    precision: Option<usize>,
    kind: Option<char>,
}

impl Spec {
    /// Parse a spec such as `"5.1f"`, `"<38s"`, `"+5.1f"`, `"08d"`.
    ///
    /// # Panics
    ///
    /// Panics on a spec outside the supported subset.
    #[must_use]
    pub fn parse(spec: &str) -> Self {
        let chars: Vec<char> = spec.chars().collect();
        let mut i = 0;
        let mut out = Spec {
            fill: ' ',
            sign: '-',
            ..Spec::default()
        };
        if chars.len() >= 2 && matches!(chars[1], '<' | '>' | '^' | '=') {
            out.fill = chars[0];
            out.align = Some(chars[1]);
            i = 2;
        } else if !chars.is_empty() && matches!(chars[0], '<' | '>' | '^' | '=') {
            out.align = Some(chars[0]);
            i = 1;
        }
        if i < chars.len() && matches!(chars[i], '+' | '-' | ' ') {
            out.sign = chars[i];
            i += 1;
        }
        if i < chars.len() && chars[i] == '0' {
            out.zero = true;
            i += 1;
        }
        let start = i;
        while i < chars.len() && chars[i].is_ascii_digit() {
            i += 1;
        }
        if i > start {
            out.width = chars[start..i]
                .iter()
                .collect::<String>()
                .parse()
                .unwrap_or(0);
        }
        if i < chars.len() && chars[i] == '.' {
            i += 1;
            let start = i;
            while i < chars.len() && chars[i].is_ascii_digit() {
                i += 1;
            }
            out.precision = Some(
                chars[start..i]
                    .iter()
                    .collect::<String>()
                    .parse()
                    .unwrap_or(0),
            );
        }
        if i < chars.len() {
            out.kind = Some(chars[i]);
            i += 1;
        }
        assert!(i == chars.len(), "unsupported format spec {spec:?}");
        out
    }

    fn pad(&self, body: &str, default_align: char) -> String {
        let len = body.chars().count();
        if len >= self.width {
            return body.to_owned();
        }
        let missing = self.width - len;
        match self.align.unwrap_or(default_align) {
            '<' => format!("{body}{}", self.fill.to_string().repeat(missing)),
            '^' => {
                let left = missing / 2;
                let fill = self.fill.to_string();
                format!("{}{body}{}", fill.repeat(left), fill.repeat(missing - left))
            }
            _ => format!("{}{body}", self.fill.to_string().repeat(missing)),
        }
    }

    /// Sign-aware padding of a number whose magnitude is `digits`.
    fn pad_number(&self, negative: bool, digits: &str) -> String {
        let sign = if negative {
            "-"
        } else {
            match self.sign {
                '+' => "+",
                ' ' => " ",
                _ => "",
            }
        };
        if self.zero && self.align.is_none() {
            let len = sign.chars().count() + digits.chars().count();
            let zeros = self.width.saturating_sub(len);
            return format!("{sign}{}{digits}", "0".repeat(zeros));
        }
        if self.align == Some('=') {
            let len = sign.chars().count() + digits.chars().count();
            let fill = self.fill.to_string().repeat(self.width.saturating_sub(len));
            return format!("{sign}{fill}{digits}");
        }
        self.pad(&format!("{sign}{digits}"), '>')
    }
}

/// `format(x, spec)` for a float.
///
/// # Panics
///
/// Panics on a spec or presentation type outside the supported subset.
#[must_use]
pub fn format_float(x: f64, spec: &str) -> String {
    let spec = Spec::parse(spec);
    let magnitude = x.abs();
    let digits = match spec.kind {
        Some('f' | 'F') => pyfloat::fixed(magnitude, spec.precision.unwrap_or(6)),
        Some('g' | 'G') => pyfloat::general(magnitude, spec.precision.unwrap_or(6)),
        Some('e' | 'E') => {
            let p = spec.precision.unwrap_or(6);
            let (mantissa, exp) = format!("{magnitude:.p$e}")
                .split_once('e')
                .map(|(m, e)| (m.to_owned(), e.parse::<i32>().unwrap_or(0)))
                .expect("Rust {:e} always has an exponent");
            let sign = if exp < 0 { '-' } else { '+' };
            format!("{mantissa}e{sign}{:02}", exp.abs())
        }
        None => match spec.precision {
            // "{:.3}" is the general format with that precision; a bare
            // width is str(x)
            Some(p) => pyfloat::general(magnitude, p),
            None => pyfloat::repr(magnitude),
        },
        Some(other) => panic!("unsupported float format type {other:?}"),
    };
    spec.pad_number(x.is_sign_negative() && !x.is_nan(), &digits)
}

/// `format(i, spec)` for an integer.
///
/// # Panics
///
/// Panics on a spec or presentation type outside the supported subset.
#[must_use]
pub fn format_int(i: i128, spec: &str) -> String {
    let spec = Spec::parse(spec);
    assert!(
        matches!(spec.kind, None | Some('d')),
        "unsupported int format spec {spec:?}"
    );
    spec.pad_number(i < 0, &i.unsigned_abs().to_string())
}

/// `format(s, spec)` for a string (precision truncates, as in Python).
///
/// # Panics
///
/// Panics on a spec or presentation type outside the supported subset.
#[must_use]
pub fn format_str(s: &str, spec: &str) -> String {
    let spec = Spec::parse(spec);
    assert!(
        matches!(spec.kind, None | Some('s')),
        "unsupported str format spec {spec:?}"
    );
    let body: String = match spec.precision {
        Some(p) => s.chars().take(p).collect(),
        None => s.to_owned(),
    };
    spec.pad(&body, '<')
}

#[cfg(test)]
mod tests {
    use super::{format_float, format_int, format_str};

    #[test]
    fn report_specs() {
        assert_eq!(format_float(25.0, "5.1f"), " 25.0");
        assert_eq!(format_float(-3.25, "+5.1f"), " -3.2");
        assert_eq!(format_float(3.25, "+5.1f"), " +3.2");
        assert_eq!(format_float(0.5, "17.1f"), "              0.5");
        assert_eq!(format_float(8.0, "g"), "8");
        assert_eq!(format_int(7, "4d"), "   7");
        assert_eq!(format_int(-12345, "08d"), "-0012345");
        assert_eq!(format_str("Fixture #1", "<14"), "Fixture #1    ");
        assert_eq!(format_str("Fixture #1", ">18s"), "        Fixture #1");
        assert_eq!(format_str("a", "^4"), " a  ");
        assert_eq!(format_str("ünï", "<5"), "ünï  ");
    }
}
