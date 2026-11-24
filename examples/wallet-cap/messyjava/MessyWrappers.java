// Three Java wrappers around the proved wallet rule that take the RAW JSON body {cap, spent, amount} as a parsed JSON
// value (Map, List, String, Boolean, BigInteger, BigDecimal, null), as a Java service gets it. THIS FILE IS NOT
// PROVED. Two are planted on purpose so that the messy fuzz of `forge_check.py diff` has something real to find. Do
// not fix them: the tests depend on these bugs.
//   spendSafe     never throws: a value that is not a whole number, or 0 <= spent <= cap that does not hold, fails closed.
//   spendThrows   bug: casts amount to a number before it checks the type, so a null, missing or text amount throws.
//   spendCoerces  bug: turns the text "5" into the number 5 (the proved rule never sees text).
package messyjava;

import java.math.BigInteger;
import java.util.LinkedHashMap;
import java.util.Map;

public final class MessyWrappers {
    private MessyWrappers() {}

    public static Map<String, Object> refused() { // fail closed: nothing spent, no new state
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("ok", false);
        m.put("spent", null);
        return m;
    }

    public static BigInteger whole(Object v) {
        return v instanceof BigInteger ? (BigInteger) v : null;
    }

    static Map<String, Object> proved(BigInteger cap, BigInteger spent, BigInteger amount) {
        if (spent.signum() < 0 || spent.compareTo(cap) > 0) return refused();
        dafny.Tuple2<Wallet.State, Wallet.Outcome> r = Wallet.__default.Spend(Wallet.State.create_Wallet(cap, spent), amount);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("ok", r.dtor__1().is_Ok());
        m.put("spent", r.dtor__0().dtor_spent());
        return m;
    }

    static Object field(Object raw, String k) {
        return raw instanceof Map ? ((Map<?, ?>) raw).get(k) : null;
    }

    public static Map<String, Object> spendSafe(Object raw) {
        BigInteger cap = whole(field(raw, "cap")), spent = whole(field(raw, "spent")), amount = whole(field(raw, "amount"));
        if (cap == null || spent == null || amount == null) return refused();
        return proved(cap, spent, amount);
    }

    public static Map<String, Object> spendThrows(Object raw) {
        BigInteger cap = whole(field(raw, "cap")), spent = whole(field(raw, "spent"));
        if (cap == null || spent == null) return refused();
        Number a = (Number) field(raw, "amount"); // bug: ClassCastException for text, booleans, lists, maps
        if (a.doubleValue() < 0) { // bug: NullPointerException when amount is null or missing
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("ok", false); // (what the proved rule says for a negative amount)
            m.put("spent", spent);
            return m;
        }
        BigInteger amount = whole(a);
        return amount == null ? refused() : proved(cap, spent, amount);
    }

    public static Map<String, Object> spendCoerces(Object raw) {
        Object amount = field(raw, "amount");
        if (amount instanceof String && ((String) amount).matches("-?[0-9]+")) amount = new BigInteger((String) amount); // bug
        BigInteger cap = whole(field(raw, "cap")), spent = whole(field(raw, "spent")), a = whole(amount);
        if (cap == null || spent == null || a == null) return refused();
        return proved(cap, spent, a);
    }
}
