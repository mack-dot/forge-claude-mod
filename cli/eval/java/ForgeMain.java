// Forge diff evaluator for Java: the line protocol of cli/forge_diff.py. Forge compiles this file together with
// the compiled proved code, the person's adapter (class ForgeAdapter, default package) and the code under test.
//
// The adapter defines:
//   public static Object reference(Map<String, Object> in)
//   public static Map<String, Function<Map<String, Object>, Object>> candidates()
// JSON numbers arrive exactly: integers as java.math.BigInteger, others as java.math.BigDecimal. Helpers:
// ForgeMain.big(v) (BigInteger), ForgeMain.lng(v) (long; throws when the value does not fit, as a real caller
// would fail). Return Map / List / String / Boolean / numbers. An exception is reported as
// {"__throws__": "<ExceptionClass>"}.
//
// Messy inputs (the unproved wrapper that turns raw JSON into Dafny values): ForgeAdapter may also define
//   public static Map<String, Function<Object, Object>> wrappers()   (or public static Object wrapper(Object raw))
//   public static Object failClosed(Object raw)  // the spec's fail-closed answer; null when the proved rule takes it
// Each wrapper gets the RAW JSON value as this file parses it (Map, List, String, Boolean, BigInteger, BigDecimal,
// null) and calls the app's real wrapper with it. They are found by reflection, so both are optional.
// Test code: NOT proved.
import java.io.*;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Function;

public class ForgeMain {
    public static BigInteger big(Object v) {
        if (v instanceof BigInteger) return (BigInteger) v;
        if (v instanceof BigDecimal) return ((BigDecimal) v).toBigIntegerExact();
        throw new IllegalArgumentException("not an integer: " + v);
    }

    public static long lng(Object v) { return big(v).longValueExact(); }

    // ---- minimal JSON (RFC 8259 subset used by the protocol) ----
    static final class Reader {
        final String s; int i;
        Reader(String s) { this.s = s; }
        void ws() { while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) i++; }
        Object value() {
            ws();
            char c = s.charAt(i);
            if (c == '{') { i++; Map<String, Object> m = new LinkedHashMap<>(); ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) { ws(); String k = str(); ws(); i++; m.put(k, value()); ws();
                    if (s.charAt(i++) == '}') return m; } }
            if (c == '[') { i++; List<Object> l = new ArrayList<>(); ws();
                if (s.charAt(i) == ']') { i++; return l; }
                while (true) { l.add(value()); ws(); if (s.charAt(i++) == ']') return l; } }
            if (c == '"') return str();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            int j = i; while (j < s.length() && "+-0123456789.eE".indexOf(s.charAt(j)) >= 0) j++;
            String num = s.substring(i, j); i = j;
            return num.matches("-?\\d+") ? new BigInteger(num) : new BigDecimal(num);
        }
        String str() {
            StringBuilder b = new StringBuilder(); i++;
            while (true) { char c = s.charAt(i++);
                if (c == '"') return b.toString();
                if (c != '\\') { b.append(c); continue; }
                char e = s.charAt(i++);
                switch (e) { case 'n': b.append('\n'); break; case 't': b.append('\t'); break; case 'r': b.append('\r'); break;
                    case 'b': b.append('\b'); break; case 'f': b.append('\f'); break;
                    case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                    default: b.append(e); } }
        }
    }

    static void write(StringBuilder b, Object v) {
        if (v == null) b.append("null");
        else if (v instanceof Boolean || v instanceof BigInteger || v instanceof BigDecimal || v instanceof Integer
                || v instanceof Long || v instanceof Short || v instanceof Byte) b.append(v.toString());
        else if (v instanceof Double || v instanceof Float) b.append(v.toString());
        else if (v instanceof Map) { b.append('{'); boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) { if (!first) b.append(','); first = false;
                write(b, String.valueOf(e.getKey())); b.append(':'); write(b, e.getValue()); } b.append('}'); }
        else if (v instanceof Collection) { b.append('['); boolean first = true;
            for (Object e : (Collection<?>) v) { if (!first) b.append(','); first = false; write(b, e); } b.append(']'); }
        else { String s = v.toString(); b.append('"');
            for (char c : s.toCharArray()) {
                if (c == '"' || c == '\\') b.append('\\').append(c);
                else if (c < 0x20 || Character.isSurrogate(c)) b.append(String.format("\\u%04x", (int) c));
                else b.append(c); }
            b.append('"'); }
    }

    static Map<String, Object> thrown(Throwable t) {
        while (t instanceof java.lang.reflect.InvocationTargetException && t.getCause() != null) t = t.getCause();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("__throws__", t.getClass().getSimpleName());
        return m;
    }

    @SuppressWarnings("unchecked")
    static Object call(Function<Map<String, Object>, Object> f, String inputJson) {
        try {
            return f.apply((Map<String, Object>) new Reader(inputJson).value()); // a fresh copy per call
        } catch (Throwable t) {
            return thrown(t);
        }
    }

    static Object callRaw(Function<Object, Object> f, String inputJson) {
        try {
            return f.apply(new Reader(inputJson).value()); // a fresh copy per call
        } catch (Throwable t) {
            return thrown(t);
        }
    }

    static final Object NOT_GIVEN = new Object();

    @SuppressWarnings("unchecked")
    static Map<String, Function<Object, Object>> wrappers() {
        Map<String, Function<Object, Object>> w = new TreeMap<>();
        try {
            w.putAll((Map<String, Function<Object, Object>>) ForgeAdapter.class.getMethod("wrappers").invoke(null));
        } catch (NoSuchMethodException e) {
            try {
                java.lang.reflect.Method m = ForgeAdapter.class.getMethod("wrapper", Object.class);
                w.put("wrapper", raw -> { try { return m.invoke(null, raw); }
                    catch (java.lang.reflect.InvocationTargetException x) { throw sneaky(x.getCause()); }
                    catch (IllegalAccessException x) { throw new IllegalStateException(x); } });
            } catch (NoSuchMethodException e2) { /* no wrapper: no messy inputs */ }
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("ForgeAdapter.wrappers() failed", e);
        }
        return w;
    }

    static RuntimeException sneaky(Throwable t) {
        ForgeMain.<RuntimeException>sneakyThrow(t);
        return null;
    }

    @SuppressWarnings("unchecked")
    static <T extends Throwable> void sneakyThrow(Throwable t) throws T { throw (T) t; }

    static Function<Object, Object> failClosed() {
        try {
            java.lang.reflect.Method m = ForgeAdapter.class.getMethod("failClosed", Object.class);
            return raw -> { try { return m.invoke(null, raw); }
                catch (java.lang.reflect.InvocationTargetException x) { throw sneaky(x.getCause()); }
                catch (IllegalAccessException x) { throw new IllegalStateException(x); } };
        } catch (NoSuchMethodException e) {
            return raw -> null;
        }
    }

    public static void main(String[] args) throws IOException {
        Map<String, Function<Map<String, Object>, Object>> cands = new TreeMap<>(ForgeAdapter.candidates());
        Map<String, Function<Object, Object>> wraps = wrappers();
        Function<Object, Object> failClosed = failClosed();
        PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), false, "UTF-8");
        // The protocol owns the real stdout. Code under test that prints (System.out) writes to stderr instead:
        // before this, one println in the shipped code broke the line protocol (DIFF_ERROR).
        System.setOut(System.err);
        List<String> all = new ArrayList<>(cands.keySet());
        all.addAll(wraps.keySet());
        Map<String, Object> hello = new LinkedHashMap<>();
        hello.put("candidates", all);
        hello.put("messy", new ArrayList<>(wraps.keySet()));
        StringBuilder hb = new StringBuilder(); write(hb, hello);
        out.println(hb);
        out.flush();
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = in.readLine()) != null) {
            if (line.trim().isEmpty()) continue;
            Reader r = new Reader(line); r.ws(); r.i++; r.ws(); r.str(); r.ws(); r.i++; // {"input":
            int start = r.i; r.value(); String inputJson = line.substring(start, r.i);
            boolean messy = line.substring(r.i).matches("(?s)\\s*,\\s*\"messy\"\\s*:\\s*true\\s*}\\s*");
            Map<String, Object> answers = new LinkedHashMap<>();
            Map<String, Object> msg = new LinkedHashMap<>();
            if (messy) {
                Object ref = callRaw(failClosed, inputJson);
                msg.put("reference", ref != null ? ref : call(ForgeAdapter::reference, inputJson));
                for (Map.Entry<String, Function<Object, Object>> e : wraps.entrySet())
                    answers.put(e.getKey(), callRaw(e.getValue(), inputJson));
                msg.put("candidates", answers);
                StringBuilder b = new StringBuilder(); write(b, msg);
                out.println(b);
                out.flush();
                continue;
            }
            for (Map.Entry<String, Function<Map<String, Object>, Object>> e : cands.entrySet())
                answers.put(e.getKey(), call(e.getValue(), inputJson));
            for (Map.Entry<String, Function<Object, Object>> e : wraps.entrySet())
                answers.put(e.getKey(), callRaw(e.getValue(), inputJson));
            msg.put("reference", call(ForgeAdapter::reference, inputJson));
            msg.put("candidates", answers);
            StringBuilder b = new StringBuilder(); write(b, msg);
            out.println(b);
            out.flush();
        }
    }
}
