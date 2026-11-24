// The wallet check as a C# service shipped it before Forge. Kept on purpose, with planted bugs, so that
// `forge_check.py diff` has something real to find. Do not fix it: the tests depend on it.
namespace LegacyCs
{
    public static class LegacyWallet
    {
        /// <summary>Returns whether the spend is accepted and the new total spent.</summary>
        public static (bool, long) Spend(long cap, long spent, long amount)
        {
            // bug 1: a zero amount is accepted; bug 2: near 2^63 the long sum wraps around (unchecked arithmetic);
            // bug 3: reaching the cap exactly is rejected (< instead of <=)
            if (amount >= 0 && spent + amount < cap) return (true, spent + amount);
            return (false, spent);
        }
    }
}
