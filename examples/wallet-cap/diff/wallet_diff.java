// forge diff evaluator adapter (Java): the shipped Java wallet code, and the proved rule compiled to Java.
// The public class must be ForgeAdapter (default package). Inputs come from wallet_inputs.py. Test code, not proved.
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Function;

public class ForgeAdapter {
    public static Object reference(Map<String, Object> in) {
        Wallet.State s = Wallet.State.create_Wallet(ForgeMain.big(in.get("cap")), ForgeMain.big(in.get("spent")));
        dafny.Tuple2<Wallet.State, Wallet.Outcome> r = Wallet.__default.Spend(s, ForgeMain.big(in.get("amount")));
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("ok", r.dtor__1().is_Ok());
        m.put("spent", r.dtor__0().dtor_spent());
        return m;
    }

    public static Map<String, Function<Map<String, Object>, Object>> candidates() {
        Map<String, Function<Map<String, Object>, Object>> c = new LinkedHashMap<>();
        c.put("legacy-java", in -> {
            long[] r = legacyjava.LegacyWallet.spend(ForgeMain.lng(in.get("cap")), ForgeMain.lng(in.get("spent")),
                    ForgeMain.lng(in.get("amount")));
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("ok", r[0] == 1);
            m.put("spent", r[1]);
            return m;
        });
        return c;
    }
}
