// forge diff adapter (C#) for the messy fuzz: the C# wrappers that take the raw JsonElement, and the proved rule.
// The class must be the static class ForgeAdapter (global namespace). Inputs come from wallet_inputs.py.
// Test code, not proved.
using System;
using System.Collections.Generic;
using System.Text.Json;
using MessyCs;

public static class ForgeAdapter
{
    public static object Reference(Dictionary<string, object> i)
    {
        Wallet.__default.Spend(Wallet.State.create_Wallet(ForgeMain.Big(i["cap"]), ForgeMain.Big(i["spent"])),
                               ForgeMain.Big(i["amount"]), out var next, out var outcome);
        return new Dictionary<string, object> { ["ok"] = outcome.is_Ok, ["spent"] = next.dtor_spent };
    }

    // No plain candidates here: the shipped legacy code has its own entry (wallet_diff.cs).
    public static Dictionary<string, Func<Dictionary<string, object>, object>> Candidates() => new();

    // The spec's answer for a value the proved rule does not take: refused, nothing spent. null: the rule takes it.
    public static object FailClosed(JsonElement raw)
    {
        var (cap, spent, amount) = (MessyWrappers.Whole(raw, "cap"), MessyWrappers.Whole(raw, "spent"), MessyWrappers.Whole(raw, "amount"));
        return cap != null && spent != null && amount != null && spent.Value >= 0 && spent.Value <= cap.Value ? null : MessyWrappers.Refused();
    }

    // Each calls the wrapper with the raw value: no conversion here.
    public static Dictionary<string, Func<JsonElement, object>> Wrappers() => new()
    {
        ["wrapper-safe"] = MessyWrappers.SpendSafe,
        ["wrapper-throws"] = MessyWrappers.SpendThrows,
        ["wrapper-coerces"] = MessyWrappers.SpendCoerces,
    };
}
