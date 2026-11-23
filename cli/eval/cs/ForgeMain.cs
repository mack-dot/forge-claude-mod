// Forge diff evaluator for C#: the line protocol of cli/forge_diff.py. Forge builds this file together with the
// compiled proved code (one .cs file with the Dafny runtime), the person's adapter (static class ForgeAdapter) and
// the code under test, in a small project in .forge/cache/.
//
// The adapter defines:
//   public static object Reference(Dictionary<string, object> input)
//   public static Dictionary<string, Func<Dictionary<string, object>, object>> Candidates()
// JSON numbers arrive exactly: integers as System.Numerics.BigInteger, others as decimal. Helpers:
// ForgeMain.Big(v) (BigInteger), ForgeMain.Lng(v) (long; throws OverflowException when the value does not fit,
// as a real caller would fail). Return Dictionary / List / string / bool / numbers. An exception is reported as
// {"__throws__": "<ExceptionType>"}.
//
// Messy inputs (the unproved wrapper that turns raw JSON into Dafny values): ForgeAdapter may also define
//   public static Dictionary<string, Func<JsonElement, object>> Wrappers()   (or public static object Wrapper(JsonElement raw))
//   public static object FailClosed(JsonElement raw)  // the spec's fail-closed answer; null when the proved rule takes it
// Each wrapper gets the RAW System.Text.Json.JsonElement and calls the app's real wrapper with it. They are found
// by reflection, so both are optional. Test code: NOT proved.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Numerics;
using System.Text;
using System.Text.Json;

public static class ForgeMain {
    public static BigInteger Big(object v) => v is BigInteger b ? b : throw new ArgumentException("not an integer: " + v);
    public static long Lng(object v) => (long)Big(v); // OverflowException when it does not fit

    static object Plain(JsonElement e) {
        switch (e.ValueKind) {
            case JsonValueKind.Object: return e.EnumerateObject().ToDictionary(p => p.Name, p => Plain(p.Value));
            case JsonValueKind.Array: return e.EnumerateArray().Select(Plain).ToList();
            case JsonValueKind.String: return e.GetString();
            case JsonValueKind.True: return true;
            case JsonValueKind.False: return false;
            case JsonValueKind.Null: return null;
            default:
                var raw = e.GetRawText();
                return raw.All(c => char.IsDigit(c) || c == '-') ? BigInteger.Parse(raw, CultureInfo.InvariantCulture)
                                                                 : (object)decimal.Parse(raw, NumberStyles.Float, CultureInfo.InvariantCulture);
        }
    }

    static void Write(StringBuilder b, object v) {
        switch (v) {
            case null: b.Append("null"); break;
            case bool x: b.Append(x ? "true" : "false"); break;
            case string s: b.Append(JsonSerializer.Serialize(s)); break;
            case BigInteger n: b.Append(n.ToString(CultureInfo.InvariantCulture)); break;
            case IDictionary d:
                b.Append('{'); var first = true;
                foreach (DictionaryEntry kv in d) { if (!first) b.Append(','); first = false; Write(b, kv.Key.ToString()); b.Append(':'); Write(b, kv.Value); }
                b.Append('}'); break;
            case IEnumerable l when v is not string:
                b.Append('['); var f = true;
                foreach (var x in l) { if (!f) b.Append(','); f = false; Write(b, x); }
                b.Append(']'); break;
            case IFormattable n: b.Append(n.ToString(null, CultureInfo.InvariantCulture)); break;
            default: Write(b, v.ToString()); break;
        }
    }

    static Dictionary<string, object> Thrown(Exception ex) {
        while (ex is System.Reflection.TargetInvocationException && ex.InnerException != null) ex = ex.InnerException;
        return new Dictionary<string, object> { ["__throws__"] = ex.GetType().Name };
    }

    static object Call(Func<Dictionary<string, object>, object> f, JsonElement input) {
        try { return f((Dictionary<string, object>)Plain(input)); } // a fresh copy per call
        catch (Exception ex) { return Thrown(ex); }
    }

    static object CallRaw(Func<JsonElement, object> f, JsonElement input) {
        try { return f(input.Clone()); }
        catch (Exception ex) { return Thrown(ex); }
    }

    static SortedDictionary<string, Func<JsonElement, object>> Wrappers() {
        var t = typeof(ForgeAdapter);
        var all = new SortedDictionary<string, Func<JsonElement, object>>(StringComparer.Ordinal);
        var many = t.GetMethod("Wrappers", Type.EmptyTypes);
        if (many != null) {
            foreach (var kv in (IDictionary<string, Func<JsonElement, object>>)many.Invoke(null, null)) all[kv.Key] = kv.Value;
            return all;
        }
        var one = t.GetMethod("Wrapper", new[] { typeof(JsonElement) });
        if (one != null) all["wrapper"] = (Func<JsonElement, object>)Delegate.CreateDelegate(typeof(Func<JsonElement, object>), one);
        return all;
    }

    static Func<JsonElement, object> FailClosed() {
        var m = typeof(ForgeAdapter).GetMethod("FailClosed", new[] { typeof(JsonElement) });
        return m == null ? (_ => null) : (Func<JsonElement, object>)Delegate.CreateDelegate(typeof(Func<JsonElement, object>), m);
    }

    public static void Main() {
        var cands = new SortedDictionary<string, Func<Dictionary<string, object>, object>>(ForgeAdapter.Candidates(), StringComparer.Ordinal);
        var stdout = new System.IO.StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = false };
        var wraps = Wrappers();
        var failClosed = FailClosed();
        var hello = new StringBuilder();
        Write(hello, new Dictionary<string, object> { ["candidates"] = cands.Keys.Concat(wraps.Keys).ToList(), ["messy"] = wraps.Keys.ToList() });
        stdout.WriteLine(hello);
        stdout.Flush();
        var stdin = new System.IO.StreamReader(Console.OpenStandardInput(), Encoding.UTF8);
        string line;
        while ((line = stdin.ReadLine()) != null) {
            if (line.Trim().Length == 0) continue;
            JsonDocument doc;
            try { doc = JsonDocument.Parse(line); }
            catch (Exception ex) { // not judged: no JsonElement can hold this input
                var nb = new StringBuilder();
                Write(nb, new Dictionary<string, object> { ["reference"] = Thrown(ex), ["candidates"] = new Dictionary<string, object>() });
                stdout.WriteLine(nb.ToString()); stdout.Flush(); continue;
            }
            using var _doc = doc;
            var input = doc.RootElement.GetProperty("input");
            var answers = new Dictionary<string, object>();
            if (doc.RootElement.TryGetProperty("messy", out var mf) && mf.ValueKind == JsonValueKind.True) {
                var reference = CallRaw(failClosed, input) ?? Call(ForgeAdapter.Reference, input);
                foreach (var kv in wraps) answers[kv.Key] = CallRaw(kv.Value, input);
                var mb = new StringBuilder();
                Write(mb, new Dictionary<string, object> { ["reference"] = reference, ["candidates"] = answers });
                stdout.WriteLine(mb.ToString()); stdout.Flush(); continue;
            }
            foreach (var kv in cands) answers[kv.Key] = Call(kv.Value, input);
            foreach (var kv in wraps) answers[kv.Key] = CallRaw(kv.Value, input);
            var b = new StringBuilder();
            Write(b, new Dictionary<string, object> { ["reference"] = Call(ForgeAdapter.Reference, input), ["candidates"] = answers });
            stdout.WriteLine(b.ToString());
            stdout.Flush();
        }
    }
}
