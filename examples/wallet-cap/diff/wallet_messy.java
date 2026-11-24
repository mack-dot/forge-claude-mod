// forge diff adapter (Java) for the messy fuzz: the Java wrappers that take the raw JSON value, and the proved rule.
// The public class must be ForgeAdapter (default package). Inputs come from wallet_inputs.py. Test code, not proved.
import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Function;
import messyjava.MessyWrappers;

public class ForgeAdapter {
    public static Object reference(Map<String, Object> in) {
        Wallet.State s = Wallet.State.create_Wallet(ForgeMain.big(in.get("cap")), ForgeMain.big(in.get("spent")));
        dafny.Tuple2<Wallet.State, Wallet.Outcome> r = Wallet.__default.Spend(s, ForgeMain.big(in.get("amount")));
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("ok", r.dtor__1().is_Ok());
        m.put("spent", r.dtor__0().dtor_spent());
        return m;
    }

    // No plain candidates here: the shipped legacy code has its own entry (wallet_diff.java).
    public static Map<String, Function<Map<String, Object>, Object>> candidates() {
        return new LinkedHashMap<>();
    }

    // The spec's answer for a value the proved rule does not take: refused, nothing spent. null: the rule takes it.
    public static Object failClosed(Object raw) {
        Map<?, ?> m = raw instanceof Map ? (Map<?, ?>) raw : Map.of();
        BigInteger cap = MessyWrappers.whole(m.get("cap")), spent = MessyWrappers.whole(m.get("spent"));
        boolean takes = cap != null && spent != null && MessyWrappers.whole(m.get("amount")) != null
                && spent.signum() >= 0 && spent.compareTo(cap) <= 0;
        return takes ? null : MessyWrappers.refused();
    }

    // Each calls the wrapper with the raw value: no conversion here.
    public static Map<String, Function<Object, Object>> wrappers() {
        Map<String, Function<Object, Object>> w = new LinkedHashMap<>();
        w.put("wrapper-safe", MessyWrappers::spendSafe);
        w.put("wrapper-throws", MessyWrappers::spendThrows);
        w.put("wrapper-coerces", MessyWrappers::spendCoerces);
        return w;
    }
}
