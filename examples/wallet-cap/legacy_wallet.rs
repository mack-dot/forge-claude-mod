// The wallet check as a Rust service shipped it before Forge. Kept on purpose, with planted bugs, so that
// `forge_check.py diff` has something real to find. Do not fix it: the tests depend on it.

/// Returns whether the spend is accepted and the new total spent.
pub fn spend(cap: u64, spent: u64, amount: i64) -> (bool, u64) {
    // bug 1: a zero amount is accepted; bug 2: near 2^64 the u64 sum overflows (a panic in a debug build)
    if amount >= 0 && spent + amount as u64 <= cap {
        (true, spent + amount as u64)
    } else {
        (false, spent)
    }
}
