// Fuzz harness for the subscription-machine check. Dafny verifies it, then it is compiled and run in every
// build target; each target must print exactly the same lines. It drives random multi-event traces through Step.
// `expect` is a runtime check in the compiled code: a failure stops the run. No bitwise operations (Dafny 4.11's
// JavaScript backend miscompiles them on wide bit-vectors).
include "../impl/Subscription.dfy"

module FuzzMain {
  import S = Subscription

  function Next(seed: nat): nat { (seed * 6364136223846793005 + 1442695040888963407) % 18446744073709551616 }

  function KindOf(n: nat): S.Kind {
    if n % 5 == 0 then S.Start else if n % 5 == 1 then S.Renew else if n % 5 == 2 then S.Pause
    else if n % 5 == 3 then S.Resume else S.Cancel
  }

  function StatusName(st: S.Status): string {
    match st
    case New => "New" case Active => "Active" case Paused => "Paused" case Canceled => "Canceled"
  }

  method Main() {
    var seed: nat := 20261123;
    var count := 0;
    for t := 0 to 300 {
      var s := S.Init();
      var now := 0;
      var id := 0;
      for k := 0 to 12
        invariant S.Valid(s)
      {
        seed := Next(seed);
        var kind := KindOf(seed / 7);
        seed := Next(seed);
        // Mostly the next id, sometimes the same id again (a retry), sometimes an older one (a replay).
        var r := seed % 10;
        var eid := if r < 7 then id + 1 else if r < 9 then id else (if id > 0 then id - 1 else 0);
        seed := Next(seed);
        var len := (seed / 11) % 21;
        seed := Next(seed);
        now := now + (seed / 13) % 9;
        var e := S.Event(kind, eid, len);
        var res := S.Step(s, e, now);
        expect res.Ok? || eid > s.lastId || res.why == S.Duplicate || (eid < 1 && res.why == S.BadInput), "M2";
        if res.Ok? {
          expect res.next.lastId == eid, "M3";
          s := res.next;
          id := eid;
        }
        count := count + 1;
      }
      print t, " ", StatusName(s.status), " ", s.expiresAt, " ", s.left, " ", s.lastId, " ", s.renewals, "\n";
    }
    print "steps ", count, "\n";
  }
}
