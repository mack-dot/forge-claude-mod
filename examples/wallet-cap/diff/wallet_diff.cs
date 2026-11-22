// forge diff evaluator adapter (C#): the shipped C# wallet code, and the proved rule compiled to C#.
// The class must be the static class ForgeAdapter (global namespace). Inputs come from wallet_inputs.py.
// Test code, not proved.
using System;
using System.Collections.Generic;

public static class ForgeAdapter
{
    public static object Reference(Dictionary<string, object> i)
    {
        Wallet.__default.Spend(Wallet.State.create_Wallet(ForgeMain.Big(i["cap"]), ForgeMain.Big(i["spent"])),
                               ForgeMain.Big(i["amount"]), out var next, out var outcome);
        return new Dictionary<string, object> { ["ok"] = outcome.is_Ok, ["spent"] = next.dtor_spent };
    }

    public static Dictionary<string, Func<Dictionary<string, object>, object>> Candidates() => new()
    {
        ["legacy-cs"] = i =>
        {
            var (ok, spent) = LegacyCs.LegacyWallet.Spend(ForgeMain.Lng(i["cap"]), ForgeMain.Lng(i["spent"]), ForgeMain.Lng(i["amount"]));
            return new Dictionary<string, object> { ["ok"] = ok, ["spent"] = spent };
        },
    };
}
