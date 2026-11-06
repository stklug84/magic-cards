//! `statistics.mean`, `statistics.median` and `statistics.stdev` as
//! CPython computes them.
//!
//! CPython does not do floating-point arithmetic here: every input float
//! is converted to its exact rational value, the mean and the sum of
//! squared deviations are exact fractions, and only the final result is
//! rounded - the mean by a correctly rounded division, the standard
//! deviation by `_float_sqrt_of_frac` (an integer square root with
//! round-to-odd, then one correctly rounded division). A plain `f64`
//! implementation differs in the last bit often enough to flip a `.1f`
//! digit in the per-seed spread, so the exact algorithm is reproduced
//! with big integers.

use num_bigint::BigInt;
use num_integer::Integer;
use num_traits::{One, Signed, ToPrimitive, Zero};

const FLOAT_MANT_DIG: i64 = 53;
/// CPython's `statistics._sqrt_bit_width`: `2 * sys.float_info.mant_dig + 3`.
const SQRT_BIT_WIDTH: i64 = 2 * FLOAT_MANT_DIG + 3;

/// An exact fraction (always normalised: positive denominator, reduced).
#[derive(Clone, Debug, PartialEq, Eq)]
struct Frac {
    num: BigInt,
    den: BigInt,
}

impl Frac {
    fn new(num: BigInt, den: BigInt) -> Self {
        debug_assert!(!den.is_zero());
        let (num, den) = if den.is_negative() {
            (-num, -den)
        } else {
            (num, den)
        };
        let g = num.gcd(&den);
        if g.is_one() || g.is_zero() {
            Frac { num, den }
        } else {
            Frac {
                num: num / &g,
                den: den / g,
            }
        }
    }

    fn from_int(i: i64) -> Self {
        Frac {
            num: BigInt::from(i),
            den: BigInt::one(),
        }
    }

    /// The exact value of a finite float (`float.as_integer_ratio()`).
    fn from_f64(x: f64) -> Self {
        assert!(x.is_finite(), "statistics over a non-finite value");
        if x == 0.0 {
            return Frac::from_int(0);
        }
        let bits = x.to_bits();
        let sign = if bits >> 63 == 0 { 1 } else { -1 };
        let exponent = i64::try_from((bits >> 52) & 0x7ff).expect("11 bits fit");
        let fraction = bits & 0x000f_ffff_ffff_ffff;
        let (mantissa, exp) = if exponent == 0 {
            (fraction, -1074)
        } else {
            (fraction | 0x0010_0000_0000_0000, exponent - 1075)
        };
        let m = BigInt::from(mantissa) * sign;
        if exp >= 0 {
            Frac::new(
                m << usize::try_from(exp).expect("non-negative"),
                BigInt::one(),
            )
        } else {
            Frac::new(m, BigInt::one() << usize::try_from(-exp).expect("positive"))
        }
    }

    fn add(&self, other: &Frac) -> Frac {
        Frac::new(
            &self.num * &other.den + &other.num * &self.den,
            &self.den * &other.den,
        )
    }

    fn sub(&self, other: &Frac) -> Frac {
        Frac::new(
            &self.num * &other.den - &other.num * &self.den,
            &self.den * &other.den,
        )
    }

    fn mul(&self, other: &Frac) -> Frac {
        Frac::new(&self.num * &other.num, &self.den * &other.den)
    }

    fn div_int(&self, n: i64) -> Frac {
        Frac::new(self.num.clone(), &self.den * BigInt::from(n))
    }

    /// `int.__truediv__`: a correctly rounded float of `num / den`.
    fn to_f64(&self) -> f64 {
        int_div_to_f64(&self.num, &self.den)
    }
}

/// Correctly rounded `num / den` (both integers, `den > 0`), by the
/// round-to-odd trick: compute 55 significant bits of the quotient, set
/// the lowest bit when the division is inexact, and let the one final
/// conversion to `f64` round.
fn int_div_to_f64(num: &BigInt, den: &BigInt) -> f64 {
    if num.is_zero() {
        return 0.0;
    }
    let negative = num.is_negative();
    let num = num.abs();
    let num_bits = i64::try_from(num.bits()).expect("bit length fits");
    let den_bits = i64::try_from(den.bits()).expect("bit length fits");
    // shift so that the integer quotient has 55 or 56 bits
    let shift = FLOAT_MANT_DIG + 3 - (num_bits - den_bits);
    let (scaled_num, scaled_den) = if shift >= 0 {
        (
            num << usize::try_from(shift).expect("non-negative"),
            den.clone(),
        )
    } else {
        (num, den << usize::try_from(-shift).expect("positive"))
    };
    let (q, r) = scaled_num.div_rem(&scaled_den);
    let q = if r.is_zero() { q } else { q | BigInt::one() };
    let q_f64 = q.to_f64().expect("a ~56-bit integer converts");
    let value = q_f64 * 2f64.powi(i32::try_from(-shift).expect("shift fits in i32"));
    if negative { -value } else { value }
}

/// `statistics.mean(data)` for floats (exact, then one rounding).
///
/// # Panics
///
/// Panics on empty input (CPython raises `StatisticsError`).
#[must_use]
pub fn mean(data: &[f64]) -> f64 {
    assert!(!data.is_empty(), "mean requires at least one data point");
    let total = data
        .iter()
        .fold(Frac::from_int(0), |acc, &x| acc.add(&Frac::from_f64(x)));
    total
        .div_int(i64::try_from(data.len()).expect("length fits"))
        .to_f64()
}

/// `statistics.median(data)`.
///
/// # Panics
///
/// Panics on empty input (CPython raises `StatisticsError`).
#[must_use]
pub fn median(data: &[f64]) -> f64 {
    assert!(!data.is_empty(), "median requires at least one data point");
    let mut sorted = data.to_vec();
    sorted.sort_by(f64::total_cmp);
    let n = sorted.len();
    // CPython: (data[i - 1] + data[i]) / 2; the float midpoint is that
    // same operation for every finite, non-overflowing pair
    if n % 2 == 1 {
        sorted[n / 2]
    } else {
        f64::midpoint(sorted[n / 2 - 1], sorted[n / 2])
    }
}

/// `statistics.stdev(data)`: the sample standard deviation.
///
/// # Panics
///
/// Panics with fewer than two data points (CPython raises
/// `StatisticsError`).
#[must_use]
pub fn stdev(data: &[f64]) -> f64 {
    assert!(data.len() >= 2, "stdev requires at least two data points");
    let n = i64::try_from(data.len()).expect("length fits");
    let exact: Vec<Frac> = data.iter().map(|&x| Frac::from_f64(x)).collect();
    let total = exact.iter().fold(Frac::from_int(0), |acc, x| acc.add(x));
    let c = total.div_int(n);
    let ss = exact.iter().fold(Frac::from_int(0), |acc, x| {
        let d = x.sub(&c);
        acc.add(&d.mul(&d))
    });
    let mss = ss.div_int(n - 1);
    float_sqrt_of_frac(&mss.num, &mss.den)
}

/// CPython's `_float_sqrt_of_frac(n, m)`: the correctly rounded square
/// root of `n / m`.
fn float_sqrt_of_frac(n: &BigInt, m: &BigInt) -> f64 {
    if n.is_zero() {
        return 0.0;
    }
    let n_bits = i64::try_from(n.bits()).expect("bit length fits");
    let m_bits = i64::try_from(m.bits()).expect("bit length fits");
    let q = (n_bits - m_bits - SQRT_BIT_WIDTH).div_euclid(2);
    if q >= 0 {
        let shift = usize::try_from(q).expect("non-negative");
        let numerator = integer_sqrt_of_frac_rto(n, &(m << (2 * shift))) << shift;
        int_div_to_f64(&numerator, &BigInt::one())
    } else {
        let shift = usize::try_from(-q).expect("positive");
        let numerator = integer_sqrt_of_frac_rto(&(n << (2 * shift)), m);
        int_div_to_f64(&numerator, &(BigInt::one() << shift))
    }
}

/// CPython's `_integer_sqrt_of_frac_rto(n, m)`: `isqrt(n // m)` with
/// round-to-odd.
fn integer_sqrt_of_frac_rto(n: &BigInt, m: &BigInt) -> BigInt {
    let a = (n / m).sqrt();
    if &a * &a * m == *n {
        a
    } else {
        a | BigInt::one()
    }
}

#[cfg(test)]
mod tests {
    use super::{Frac, mean, median, stdev};

    #[test]
    fn exact_float_ratio() {
        let f = Frac::from_f64(0.1);
        assert_eq!(f.num.to_string(), "3602879701896397");
        assert_eq!(f.den.to_string(), "36028797018963968");
    }

    #[test]
    fn mean_and_median() {
        assert_eq!(mean(&[0.3, 0.5]), 0.4);
        assert_eq!(median(&[12.0, 19.0]), 15.5);
        assert_eq!(median(&[20.0, 18.0, 21.0]), 20.0);
    }

    #[test]
    fn stdev_of_two_rates() {
        // statistics.stdev([0.3, 0.5]) == 0.1414213562373095
        assert_eq!(stdev(&[0.3, 0.5]), 0.141_421_356_237_309_5);
    }
}
