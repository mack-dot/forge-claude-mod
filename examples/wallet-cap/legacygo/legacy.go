// Package legacygo is the wallet check as a Go service shipped it before Forge. Kept on purpose, with planted
// bugs, so that `forge_check.py diff` has something real to find. Do not fix it: the tests depend on it.
package legacygo

// Spend returns whether the spend is accepted and the new total spent.
func Spend(cap, spent, amount int64) (bool, int64) {
	if spent+amount < cap { // bug 1: reaching the cap exactly is rejected; bug 2: zero and negative pass;
		return true, spent + amount // bug 3: near 2^63 the int64 sum overflows and wraps around
	}
	return false, spent
}
