include "../spec/ThreadSpec.dfy"

module Thread refines ThreadSpec {
  // The map by id, built once (a later item with the same id wins).
  method ById(items: seq<Item>) returns (m: map<string, Item>)
    ensures forall id :: Find(items, id) == (if id in m then Some(m[id]) else None)
  {
    m := map[];
    var i := 0;
    while i < |items|
      invariant 0 <= i <= |items|
      invariant forall id :: Find(items[..i], id) == (if id in m then Some(m[id]) else None)
    {
      assert items[..i + 1][..i] == items[..i];
      m := m[items[i].id := items[i]];
      i := i + 1;
    }
    assert items[..|items|] == items;
  }

  // The ancestors of `it`: a loop with a visited set.
  method Walk(m: map<string, Item>, items: seq<Item>, it: Item) returns (up: seq<Item>)
    requires forall id :: Find(items, id) == (if id in m then Some(m[id]) else None)
    ensures up == Ancestors(items, it)
  {
    var seen := {it.id};
    var ref := it.parent;
    up := [];
    while ref != "" && ref !in seen && ref in m
      invariant Ancestors(items, it) == up + Chain(items, ref, seen)
      decreases Ids(items) - seen
    {
      var p := m[ref];
      assert Find(items, ref) == Some(p);
      assert Chain(items, ref, seen) == [p] + Chain(items, p.parent, seen + {ref});
      up := up + [p];
      seen := seen + {ref};
      ref := p.parent;
    }
    assert ref != "" && ref !in seen ==> Find(items, ref).None?;
  }

  method Taken(m: map<string, Item>, items: seq<Item>) returns (taken: set<string>)
    requires forall id :: Find(items, id) == (if id in m then Some(m[id]) else None)
    ensures taken == TakenDown(items)
  {
    taken := {};
    var i := 0;
    while i < |items|
      invariant 0 <= i <= |items|
      invariant forall x :: x in taken <==> exists j :: 0 <= j < i && Takes(items, items[j]) && items[j].parent == x
    {
      var t := items[i];
      if t.kind == Takedown && t.parent != "" && t.parent in m && MayTakeDown(t.author, m[t.parent]) {
        taken := taken + {t.parent};
      }
      i := i + 1;
    }
    forall x | x in TakenDown(items) ensures x in taken {
      var t :| t in items && Takes(items, t) && t.parent == x;
      var j :| 0 <= j < |items| && items[j] == t;
    }
  }

  method ShownTo(v: Viewer, items: seq<Item>) returns (out: seq<bool>) {
    var m := ById(items);
    var taken := Taken(m, items);
    out := [];
    var i := 0;
    while i < |items|
      invariant 0 <= i <= |items| && |out| == i
      invariant forall j :: 0 <= j < i ==> out[j] == Visible(v, items, items[j])
    {
      var it := items[i];
      var ok := false;
      if it.kind == Post && it.id !in taken && !it.flagged && Member(v, it) {
        var up := Walk(m, items, it);
        ok := forall a :: a in up ==> a.id !in taken && !a.flagged && Member(v, a);
      }
      out := out + [ok];
      i := i + 1;
    }
  }
}
