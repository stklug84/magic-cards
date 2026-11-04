//! CPython's `random.Random`: MT19937 with CPython's seeding and derived
//! methods, so that `Random(seed).shuffle(...)` produces the same order
//! as the Python simulator for the same `--seed`.
//!
//! Reproduced from CPython's `Modules/_randommodule.c` and
//! `Lib/random.py` (3.12):
//!
//! - `seed(int)`: `init_by_array` over the little-endian 32-bit words of
//!   `abs(seed)` (at least one word, so seed 0 is the key `[0]`);
//! - `getrandbits(k)`: `genrand_uint32() >> (32 - k)` for `k <= 32`, else
//!   little-endian 32-bit words, the last one shifted;
//! - `random()`: `(a >> 5) * 2^26 + (b >> 6)` over `2^53`;
//! - `_randbelow(n)`: rejection sampling on `n.bit_length()` bits;
//! - `shuffle`: Fisher-Yates from the end with `_randbelow(i + 1)`;
//! - `choice`: `seq[_randbelow(len(seq))]`.

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// A CPython-compatible Mersenne Twister generator.
#[derive(Clone)]
pub struct PyRandom {
    mt: [u32; N],
    index: usize,
}

impl std::fmt::Debug for PyRandom {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PyRandom")
            .field("index", &self.index)
            .finish_non_exhaustive()
    }
}

impl PyRandom {
    /// `random.Random(seed)` for an integer seed (negative seeds behave
    /// like their absolute value, as in CPython).
    #[must_use]
    pub fn new(seed: i128) -> Self {
        Self::from_u128(seed.unsigned_abs())
    }

    /// `random.Random(seed)` for a non-negative seed of up to 128 bits.
    #[must_use]
    pub fn from_u128(seed: u128) -> Self {
        let mut key = Vec::with_capacity(4);
        let mut rest = seed;
        loop {
            #[expect(
                clippy::cast_possible_truncation,
                reason = "the low 32 bits are wanted"
            )]
            key.push(rest as u32);
            rest >>= 32;
            if rest == 0 {
                break;
            }
        }
        Self::from_key(&key)
    }

    /// `init_by_array`: seed from an array of 32-bit words.
    #[must_use]
    pub fn from_key(key: &[u32]) -> Self {
        let mut rng = Self::init_genrand(19_650_218);
        let key_len = key.len().max(1);
        let mut i = 1usize;
        let mut j = 0usize;
        for _ in 0..N.max(key_len) {
            let prev = rng.mt[i - 1] ^ (rng.mt[i - 1] >> 30);
            let word = key.get(j).copied().unwrap_or(0);
            #[expect(clippy::cast_possible_truncation, reason = "j < key_len fits in u32")]
            let j32 = j as u32;
            rng.mt[i] = (rng.mt[i] ^ prev.wrapping_mul(1_664_525))
                .wrapping_add(word)
                .wrapping_add(j32);
            i += 1;
            j += 1;
            if i >= N {
                rng.mt[0] = rng.mt[N - 1];
                i = 1;
            }
            if j >= key_len {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            let prev = rng.mt[i - 1] ^ (rng.mt[i - 1] >> 30);
            #[expect(clippy::cast_possible_truncation, reason = "i < N fits in u32")]
            let i32_ = i as u32;
            rng.mt[i] = (rng.mt[i] ^ prev.wrapping_mul(1_566_083_941)).wrapping_sub(i32_);
            i += 1;
            if i >= N {
                rng.mt[0] = rng.mt[N - 1];
                i = 1;
            }
        }
        rng.mt[0] = 0x8000_0000;
        rng.index = N;
        rng
    }

    fn init_genrand(s: u32) -> Self {
        let mut mt = [0u32; N];
        mt[0] = s;
        for i in 1..N {
            #[expect(clippy::cast_possible_truncation, reason = "i < N fits in u32")]
            let i32_ = i as u32;
            mt[i] = 1_812_433_253u32
                .wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30))
                .wrapping_add(i32_);
        }
        Self { mt, index: N }
    }

    fn twist(&mut self) {
        for kk in 0..N {
            let y = (self.mt[kk] & UPPER_MASK) | (self.mt[(kk + 1) % N] & LOWER_MASK);
            let mag = if y & 1 == 1 { MATRIX_A } else { 0 };
            self.mt[kk] = self.mt[(kk + M) % N] ^ (y >> 1) ^ mag;
        }
        self.index = 0;
    }

    /// `genrand_uint32`: the next raw 32-bit output.
    pub fn genrand_u32(&mut self) -> u32 {
        if self.index >= N {
            self.twist();
        }
        let mut y = self.mt[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// `getrandbits(k)` for `1 <= k <= 128`.
    ///
    /// # Panics
    ///
    /// Panics when `k` is 0 or above 128 (CPython raises `ValueError`
    /// for `k < 0`; 0 returns 0 there, but no engine code asks for it).
    pub fn getrandbits(&mut self, k: u32) -> u128 {
        assert!(
            (1..=128).contains(&k),
            "getrandbits: k must be in 1..=128, got {k}"
        );
        if k <= 32 {
            return u128::from(self.genrand_u32() >> (32 - k));
        }
        let mut value: u128 = 0;
        let mut remaining = k;
        let mut shift = 0u32;
        while remaining > 0 {
            let mut word = self.genrand_u32();
            if remaining < 32 {
                word >>= 32 - remaining;
            }
            value |= u128::from(word) << shift;
            shift += 32;
            remaining = remaining.saturating_sub(32);
        }
        value
    }

    /// `random()`: a float in `[0, 1)` with 53 random bits.
    pub fn random(&mut self) -> f64 {
        let a = f64::from(self.genrand_u32() >> 5);
        let b = f64::from(self.genrand_u32() >> 6);
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }

    /// `_randbelow(n)`: a uniform integer in `0..n` by rejection sampling.
    ///
    /// # Panics
    ///
    /// Panics when `n` is 0.
    pub fn randbelow(&mut self, n: u128) -> u128 {
        assert!(n > 0, "randbelow: n must be positive");
        let k = 128 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// `randrange(n)`: the same as [`Self::randbelow`] (CPython 3.12
    /// delegates the one-argument form to `_randbelow`).
    pub fn randrange(&mut self, n: u128) -> u128 {
        self.randbelow(n)
    }

    /// `shuffle(x)`: Fisher-Yates from the back, `j = _randbelow(i + 1)`.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            // randbelow(i + 1) <= i < len, so the narrowing is lossless
            let j = usize::try_from(self.randbelow(i as u128 + 1)).unwrap_or(i);
            items.swap(i, j);
        }
    }

    /// `choice(seq)`: a uniformly chosen element.
    ///
    /// # Panics
    ///
    /// Panics on an empty slice (CPython raises `IndexError`).
    pub fn choice<'a, T>(&mut self, items: &'a [T]) -> &'a T {
        assert!(!items.is_empty(), "choice: empty sequence");
        let idx = usize::try_from(self.randbelow(items.len() as u128)).unwrap_or(0);
        &items[idx]
    }
}

#[cfg(test)]
mod tests {
    use super::PyRandom;

    #[test]
    fn seed_zero_matches_cpython_first_output() {
        // random.Random(0).getrandbits(32) == 3626764237
        let mut rng = PyRandom::new(0);
        assert_eq!(rng.genrand_u32(), 3_626_764_237);
    }

    #[test]
    fn negative_seed_equals_its_absolute_value() {
        let mut a = PyRandom::new(-5);
        let mut b = PyRandom::new(5);
        assert_eq!(a.genrand_u32(), b.genrand_u32());
    }

    #[test]
    fn shuffle_is_a_permutation() {
        let mut rng = PyRandom::new(42);
        let mut items: Vec<u32> = (0..50).collect();
        rng.shuffle(&mut items);
        let mut sorted = items.clone();
        sorted.sort_unstable();
        assert_eq!(sorted, (0..50).collect::<Vec<_>>());
    }
}
