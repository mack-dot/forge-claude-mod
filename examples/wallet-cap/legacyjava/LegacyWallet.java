// The wallet check as a Java service shipped it before Forge. Kept on purpose, with planted bugs, so that
// `forge_check.py diff` has something real to find. Do not fix it: the tests depend on it.
package legacyjava;

public final class LegacyWallet {
    private LegacyWallet() {}

    /** Returns {1 or 0 for accepted, new total spent}. */
    public static long[] spend(long cap, long spent, long amount) {
        // bug 1: reaching the cap exactly is rejected; bug 2: near 2^63 the long sum overflows and wraps around
        if (amount > 0 && spent + amount < cap) return new long[] {1, spent + amount};
        return new long[] {0, spent};
    }
}
