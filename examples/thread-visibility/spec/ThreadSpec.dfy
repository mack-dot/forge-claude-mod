// Which items of a channel's thread may a viewer see? A synthetic rule for Forge's own tests: a reply shares the fate
// of every item above it in its reply chain, and the chain may contain cycles.
include "ThreadRules.dfy"

abstract module ThreadSpec {
  import opened R = ThreadRules

  datatype Opt<T> = None | Some(v: T)

  function Ids(items: seq<Item>): set<string> { set i | 0 <= i < |items| :: items[i].id }

  // The LAST item with this id (a map built from the items in order).
  function Find(items: seq<Item>, id: string): (r: Opt<Item>)
    ensures r.Some? ==> r.v.id == id && id in Ids(items)
    ensures r.None? ==> id !in Ids(items)
    decreases |items|
  {
    if |items| == 0 then None
    else if items[|items| - 1].id == id then Some(items[|items| - 1])
    else
      var r := Find(items[..|items| - 1], id);
      assert forall j :: 0 <= j < |items| - 1 ==> items[..|items| - 1][j] == items[j];
      r
  }

  // The ancestors of an item, nearest first. The walk ends at "", at an id with no item, or at an id already seen:
  // a cycle ends it, it never loops. Each step adds one more id of the thread to `seen`.
  function Chain(items: seq<Item>, ref: string, seen: set<string>): seq<Item>
    decreases Ids(items) - seen
  {
    if ref == "" || ref in seen then []
    else match Find(items, ref)
      case None => []
      case Some(p) => [p] + Chain(items, p.parent, seen + {ref})
  }
  function Ancestors(items: seq<Item>, it: Item): seq<Item> { Chain(items, it.parent, {it.id}) }

  // The ids that a valid takedown names.
  predicate Takes(items: seq<Item>, t: Item) {
    t.kind == Takedown && t.parent != "" && Find(items, t.parent).Some? && MayTakeDown(t.author, Find(items, t.parent).v)
  }
  function TakenDown(items: seq<Item>): set<string> { set t | t in items && Takes(items, t) :: t.parent }
  predicate Fine(items: seq<Item>, v: Viewer, it: Item) { it.id !in TakenDown(items) && !it.flagged && Member(v, it) }
  predicate Visible(v: Viewer, items: seq<Item>, it: Item) {
    it.kind == Post && Fine(items, v, it) && forall a :: a in Ancestors(items, it) ==> Fine(items, v, a)
  }

  method ShownTo(v: Viewer, items: seq<Item>) returns (out: seq<bool>)
    // V1: one answer per item, in order, and it is Visible.
    ensures |out| == |items| && forall i :: 0 <= i < |items| ==> out[i] == Visible(v, items, items[i])
    // V2: never visible to someone outside the item's channel.
    ensures forall i :: 0 <= i < |items| && out[i] ==> Member(v, items[i])
    // V3: a reply is visible only if no item above it is taken down, flagged, or in a channel the viewer is not in.
    ensures forall i, a :: 0 <= i < |items| && out[i] && a in Ancestors(items, items[i]) ==> Fine(items, v, a)
    // V4: a takedown item is never visible.
    ensures forall i :: 0 <= i < |items| && items[i].kind == Takedown ==> !out[i]
}
