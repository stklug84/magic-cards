//! CPython float formatting: `repr(x)`, `format(x, ".Nf")`, `format(x, "g")`.
//!
//! All three start from a correctly rounded decimal conversion, which
//! Rust's formatter provides: `{:e}` yields the shortest round-trip digits
//! (what Python's `repr` uses) and `{:.N}` / `{:.Ne}` are correctly rounded
//! with ties to even on the exact binary value, exactly like CPython's
//! `PyOS_double_to_string`. What differs is the presentation, reproduced
//! here: when `repr` switches to exponent notation (`1e+16`, `1e-05`), the
//! `.0` on integral values, the `%g` trailing-zero stripping and its
//! `e+XX` exponents, and the spellings `nan` / `inf`.

/// The shortest round-trip decimal digits of a finite, non-zero `x`
/// and the position of the decimal point: `x = 0.<digits> * 10^decpt`.
fn shortest_digits(x: f64) -> (String, i32) {
    let sci = format!("{:e}", x.abs());
    let (mantissa, exp) = sci
        .split_once('e')
        .expect("Rust {:e} always has an exponent");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let exp: i32 = exp.parse().expect("Rust {:e} exponent is an integer");
    (digits, exp + 1)
}

/// Digits of `x` rounded to `ndigits` significant digits (correctly
/// rounded, ties to even) and the decimal-point position.
fn rounded_digits(x: f64, ndigits: usize) -> (String, i32) {
    let sci = format!("{:.*e}", ndigits.saturating_sub(1), x.abs());
    let (mantissa, exp) = sci
        .split_once('e')
        .expect("Rust {:e} always has an exponent");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let exp: i32 = exp.parse().expect("Rust {:e} exponent is an integer");
    (digits, exp + 1)
}

/// Lay out `digits` (no dot) with the point after `decpt` of them.
fn fixed_layout(digits: &str, decpt: i32, add_dot_0: bool) -> String {
    if decpt <= 0 {
        let zeros = usize::try_from(-decpt).unwrap_or(0);
        format!("0.{}{digits}", "0".repeat(zeros))
    } else {
        let decpt = usize::try_from(decpt).unwrap_or(0);
        if decpt >= digits.len() {
            let tail = if add_dot_0 { ".0" } else { "" };
            format!("{digits}{}{tail}", "0".repeat(decpt - digits.len()))
        } else {
            format!("{}.{}", &digits[..decpt], &digits[decpt..])
        }
    }
}

/// Lay out `digits` in exponent notation (`d.ddde+XX`, at least two
/// exponent digits, as CPython does).
fn exponent_layout(digits: &str, decpt: i32) -> String {
    let exp = decpt - 1;
    let sign = if exp < 0 { '-' } else { '+' };
    let mantissa = if digits.len() > 1 {
        format!("{}.{}", &digits[..1], &digits[1..])
    } else {
        digits.to_owned()
    };
    format!("{mantissa}e{sign}{:02}", exp.abs())
}

/// `repr(x)` / `str(x)` for a float.
#[must_use]
pub fn repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_owned();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    if x == 0.0 {
        return format!("{sign}0.0");
    }
    let (digits, decpt) = shortest_digits(x);
    // CPython format_float_short, mode 'r': exponent form when
    // decpt <= -4 or decpt > 16
    let body = if (-3..=16).contains(&decpt) {
        fixed_layout(&digits, decpt, true)
    } else {
        exponent_layout(&digits, decpt)
    };
    format!("{sign}{body}")
}

/// `format(x, f".{precision}f")`.
#[must_use]
pub fn fixed(x: f64, precision: usize) -> String {
    if x.is_nan() {
        return "nan".to_owned();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    format!("{x:.precision$}")
}

/// `format(x, f".{precision}g")` (`precision` 0 counts as 1; `"g"` alone
/// is precision 6).
#[must_use]
pub fn general(x: f64, precision: usize) -> String {
    if x.is_nan() {
        return "nan".to_owned();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    if x == 0.0 {
        return format!("{sign}0");
    }
    let p = precision.max(1);
    let (digits, decpt) = rounded_digits(x, p);
    let exp = decpt - 1;
    let p_i32 = i32::try_from(p).unwrap_or(i32::MAX);
    let body = if (-4..p_i32).contains(&exp) {
        // fixed notation with p - 1 - exp decimals, trailing zeros removed
        strip_trailing_zeros(&fixed_layout(&digits, decpt, false))
    } else {
        let mantissa = strip_trailing_zeros(&fixed_layout(&digits, 1, false));
        let esign = if exp < 0 { '-' } else { '+' };
        format!("{mantissa}e{esign}{:02}", exp.abs())
    };
    format!("{sign}{body}")
}

fn strip_trailing_zeros(s: &str) -> String {
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        s.to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::{fixed, general, repr};

    #[test]
    fn repr_forms() {
        assert_eq!(repr(1.0), "1.0");
        assert_eq!(repr(0.1), "0.1");
        assert_eq!(repr(1e16), "1e+16");
        assert_eq!(repr(1e15), "1000000000000000.0");
        assert_eq!(repr(1e-5), "1e-05");
        assert_eq!(repr(0.0001), "0.0001");
        assert_eq!(repr(-0.0), "-0.0");
        assert_eq!(repr(123_456_789.123), "123456789.123");
        assert_eq!(repr(5e-324), "5e-324");
    }

    #[test]
    fn fixed_ties_to_even() {
        assert_eq!(fixed(0.125, 2), "0.12");
        assert_eq!(fixed(2.5, 0), "2");
        assert_eq!(fixed(-0.0, 1), "-0.0");
    }

    #[test]
    fn general_forms() {
        assert_eq!(general(1e16, 6), "1e+16");
        assert_eq!(general(123_456_789.123, 6), "1.23457e+08");
        assert_eq!(general(0.25, 6), "0.25");
        assert_eq!(general(8.0, 6), "8");
        assert_eq!(general(-0.0, 6), "-0");
        assert_eq!(general(2.5e-7, 6), "2.5e-07");
    }
}
