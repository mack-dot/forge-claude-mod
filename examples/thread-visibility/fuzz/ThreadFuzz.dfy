// Fuzz harness for the compiled thread-visibility check: 2000 pseudo-random threads (linear congruential generator,
// no bit operations) of up to 7 items over a small id alphabet, so cycles, self-replies and missing parents are common.
// It repeats the ensures of the spec as `expect` and prints one line per thread.
include "../impl/Thread.dfy"

module ThreadFuzzMain {
  import T = Thread
  import R = ThreadRules

  const IDS: seq<string> := ["a", "b", "c", "d", "e", "f", "g"]
  const PARENTS: seq<string> := ["", "a", "b", "c", "d", "e", "f", "g", "x"]
  const AUTHORS: seq<string> := ["ana", "bo", "mod", ""]
  const CHANNELS: seq<string> := ["dev", "dev", "dev", "ops", ""]
  const VIEWERS: seq<R.Viewer> := [R.Viewer("ana", "dev"), R.Viewer("bo", "dev"), R.Viewer("ana", "ops"), R.Viewer("ana", "")]

  method Pick(seed: nat, n: nat) returns (s: nat, k: nat)
    requires n > 0
    ensures k < n
  {
    s := (seed * 1103515245 + 12345) % 2147483648;
    k := (s / 65536) % n;
  }

  method Main() {
    var seed: nat := 7;
    var t := 0;
    while t < 2000 {
      var n; seed, n := Pick(seed, |IDS| + 1);
      var items: seq<R.Item> := [];
      var i := 0;
      while i < n {
        var k1, k2, k3, k4, k5, k6;
        seed, k1 := Pick(seed, |IDS|);
        seed, k2 := Pick(seed, |PARENTS|);
        seed, k3 := Pick(seed, |AUTHORS|);
        seed, k4 := Pick(seed, |CHANNELS|);
        seed, k5 := Pick(seed, 4);
        seed, k6 := Pick(seed, 6);
        var id := if t % 5 == 0 then IDS[k1] else IDS[i];
        items := items + [R.Item(id, CHANNELS[k4], AUTHORS[k3], if k5 == 0 then R.Takedown else R.Post, PARENTS[k2], k6 == 0)];
        i := i + 1;
      }
      var vk; seed, vk := Pick(seed, |VIEWERS|);
      var v := VIEWERS[vk];
      var out := T.ShownTo(v, items);
      expect |out| == |items|, "length";
      var j := 0;
      while j < |items| {
        expect out[j] == T.Visible(v, items, items[j]), "visible";
        if out[j] {
          expect R.Member(v, items[j]) && items[j].kind == R.Post, "member";
          expect forall a :: a in T.Ancestors(items, items[j]) ==> T.Fine(items, v, a), "ancestors";
        }
        j := j + 1;
      }
      print t, " ", out, "\n";
      t := t + 1;
    }
  }
}
