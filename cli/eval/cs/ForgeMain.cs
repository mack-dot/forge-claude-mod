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
// {"__throws__": "<ExceptionType>"}. Test code: NOT proved.
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

    static object Call(Func<Dictionary<string, object>, object> f, JsonElement input) {
        try { return f((Dictionary<string, object>)Plain(input)); } // a fresh copy per call
        catch (Exception ex) { return new Dictionary<string, object> { ["__throws__"] = ex.GetType().Name }; }
    }

    public static void Main() {
        var cands = new SortedDictionary<string, Func<Dictionary<string, object>, object>>(ForgeAdapter.Candidates(), StringComparer.Ordinal);
        var stdout = new System.IO.StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = false };
        var hello = new StringBuilder("{\"candidates\":"); Write(hello, cands.Keys.ToList()); stdout.WriteLine(hello.Append('}'));
        stdout.Flush();
        var stdin = new System.IO.StreamReader(Console.OpenStandardInput(), Encoding.UTF8);
        string line;
        while ((line = stdin.ReadLine()) != null) {
            if (line.Trim().Length == 0) continue;
            using var doc = JsonDocument.Parse(line);
            var input = doc.RootElement.GetProperty("input");
            var answers = new Dictionary<string, object>();
            foreach (var kv in cands) answers[kv.Key] = Call(kv.Value, input);
            var b = new StringBuilder();
            Write(b, new Dictionary<string, object> { ["reference"] = Call(ForgeAdapter.Reference, input), ["candidates"] = answers });
            stdout.WriteLine(b.ToString());
            stdout.Flush();
        }
    }
}
