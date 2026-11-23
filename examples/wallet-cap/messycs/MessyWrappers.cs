// Three C# wrappers around the proved wallet rule that take the RAW JSON body {cap, spent, amount} as a
// System.Text.Json.JsonElement, as a C# service gets it. THIS FILE IS NOT PROVED. Two are planted on purpose so that
// the messy fuzz of `forge_check.py diff` has something real to find. Do not fix them: the tests depend on these bugs.
//   SpendSafe     never throws: a value that is not a whole number, or 0 <= spent <= cap that does not hold, fails closed.
//   SpendThrows   bug: reads amount with GetDecimal() before it checks the type, so a null, missing or text amount throws.
//   SpendCoerces  bug: turns the text "5" into the number 5 (the proved rule never sees text).
using System.Collections.Generic;
using System.Globalization;
using System.Numerics;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MessyCs
{
    public static class MessyWrappers
    {
        public static Dictionary<string, object> Refused() => new() { ["ok"] = false, ["spent"] = null }; // fail closed

        static readonly Regex Digits = new Regex("^-?[0-9]+$");

        public static BigInteger? Whole(JsonElement raw, string field)
        {
            if (raw.ValueKind != JsonValueKind.Object || !raw.TryGetProperty(field, out var v) || v.ValueKind != JsonValueKind.Number)
                return null;
            var text = v.GetRawText();
            return Digits.IsMatch(text) ? BigInteger.Parse(text, CultureInfo.InvariantCulture) : null;
        }

        static Dictionary<string, object> Proved(BigInteger cap, BigInteger spent, BigInteger amount)
        {
            if (spent < 0 || spent > cap) return Refused();
            Wallet.__default.Spend(Wallet.State.create_Wallet(cap, spent), amount, out var next, out var outcome);
            return new() { ["ok"] = outcome.is_Ok, ["spent"] = next.dtor_spent };
        }

        public static Dictionary<string, object> SpendSafe(JsonElement raw)
        {
            var (cap, spent, amount) = (Whole(raw, "cap"), Whole(raw, "spent"), Whole(raw, "amount"));
            if (cap == null || spent == null || amount == null) return Refused();
            return Proved(cap.Value, spent.Value, amount.Value);
        }

        public static Dictionary<string, object> SpendThrows(JsonElement raw)
        {
            var (cap, spent) = (Whole(raw, "cap"), Whole(raw, "spent"));
            if (cap == null || spent == null) return Refused();
            var a = raw.GetProperty("amount").GetDecimal(); // bug: KeyNotFoundException when missing, InvalidOperationException for null or text
            if (a < 0) return new() { ["ok"] = false, ["spent"] = spent.Value }; // (what the proved rule says for a negative amount)
            var amount = Whole(raw, "amount");
            return amount == null ? Refused() : Proved(cap.Value, spent.Value, amount.Value);
        }

        public static Dictionary<string, object> SpendCoerces(JsonElement raw)
        {
            var (cap, spent, amount) = (Whole(raw, "cap"), Whole(raw, "spent"), Whole(raw, "amount"));
            if (raw.ValueKind == JsonValueKind.Object && raw.TryGetProperty("amount", out var t) && t.ValueKind == JsonValueKind.String
                && Digits.IsMatch(t.GetRawText().Trim('"')))
                amount = BigInteger.Parse(t.GetRawText().Trim('"'), CultureInfo.InvariantCulture); // bug
            if (cap == null || spent == null || amount == null) return Refused();
            return Proved(cap.Value, spent.Value, amount.Value);
        }
    }
}
