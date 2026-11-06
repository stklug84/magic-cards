//! Every item kind #[rule] supports, plus unsupported!.
use mtgrules::{rule, unsupported};

#[rule("100.1")]
fn free() {}

#[rule("100.1", "117.")]
pub(crate) const unsafe fn qualified<T: Copy>(value: T) -> T {
    value
}

struct S;

impl S {
    #[rule("704.5g")]
    fn method(&self) {}

    #[rule("601.2")]
    #[inline]
    async fn later() {}
}

#[rule("613.1")]
#[derive(Debug)]
struct Layered;

#[rule("614.1")]
enum E { A }

#[rule("702.19e")]
trait Keyword {}

#[rule("903.10a")]
const LIMIT: u32 = 21;

#[rule("117.1")]
static mut COUNT: u32 = 0;

#[rule("400.1")]
type Zone = u8;

#[rule("500.1")]
mod turns {}

unsupported!("903.4", "validated elsewhere");

fn main() {
    free();
    let _: u8 = unsafe { qualified(1u8) };
    S.method();
    let _ = S::later();
    let _ = (Layered, E::A, LIMIT);
    let _: Zone = 0;
    unsafe { COUNT += 1 };
    // the engine registers its own rules; count this file's only
    let own = |file: &str| file.ends_with("ok.rs");
    assert_eq!(mtgrules::cr::RULE_IMPLEMENTATIONS.iter().filter(|r| own(r.file)).count(), 11);
    assert_eq!(mtgrules::cr::UNSUPPORTED.iter().filter(|u| own(u.file)).count(), 1);
}
