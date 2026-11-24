include "ThreadSpec.dfy"

abstract module ThreadExamples {
  import S : ThreadSpec

  const ANA := S.R.Viewer("ana", "dev")
  function P(id: string, author: string, parent: string): S.R.Item { S.R.Item(id, "dev", author, S.R.Post, parent, false) }
  function Down(id: string, author: string, target: string): S.R.Item { S.R.Item(id, "dev", author, S.R.Takedown, target, false) }

  // D1: p1 and p2 reply to each other (a cycle); bo takes his post p1 down. Does the walk end, and does ana see p2?
  // Answer: it ends; neither post is visible.
  method D1_CycleSharesTakedown() {
    var items := [P("p1", "bo", "p2"), P("p2", "bo", "p1"), Down("t1", "bo", "p1")];
    var out := S.ShownTo(ANA, items);
    assert S.Find(items, "p1") == S.Some(items[0]) && S.Find(items, "p2") == S.Some(items[1]);
    assert S.Ancestors(items, items[0]) == [items[1]] && S.Ancestors(items, items[1]) == [items[0]];
    assert S.Takes(items, items[2]) && "p1" in S.TakenDown(items);
    assert out == [false, false, false];
  }

  // D2: a post whose parent is its own id. Answer: the walk ends at once; it is visible.
  method D2_SelfLoop() {
    var items := [P("p1", "bo", "p1")];
    var out := S.ShownTo(ANA, items);
    assert !S.Takes(items, items[0]) && S.TakenDown(items) == {};
    assert out == [true];
  }

  // D3: a reply to a reply to a post that its author took down. Answer: none of the three is visible.
  method D3_DeepTakedown() {
    var items := [P("p1", "bo", ""), P("p2", "cy", "p1"), P("p3", "ana", "p2"), Down("t1", "bo", "p1")];
    var out := S.ShownTo(ANA, items);
    assert items[..3][..2] == items[..2] && items[..2][..1] == items[..1];
    assert S.Find(items[..2], "p2") == S.Some(items[1]) && S.Find(items[..3], "p2") == S.Some(items[1]);
    assert S.Find(items[..1], "p1") == S.Some(items[0]) && S.Find(items[..2], "p1") == S.Some(items[0]);
    assert S.Find(items[..3], "p1") == S.Some(items[0]);
    assert S.Find(items, "p1") == S.Some(items[0]) && S.Find(items, "p2") == S.Some(items[1]);
    assert S.Ancestors(items, items[1]) == [items[0]];
    assert S.Chain(items, "p1", {"p3"} + {"p2"}) == [items[0]];
    assert S.Chain(items, "p2", {"p3"}) == [items[1]] + S.Chain(items, "p1", {"p3"} + {"p2"});
    assert S.Takes(items, items[3]) && "p1" in S.TakenDown(items);
    assert out == [false, false, false, false];
  }

  // D4: a flagged post with a reply. Answer: neither is visible.
  method D4_FlaggedRoot() {
    var items := [S.R.Item("p1", "dev", "bo", S.R.Post, "", true), P("p2", "cy", "p1")];
    var out := S.ShownTo(ANA, items);
    assert S.Find(items, "p1") == S.Some(items[0]) && S.Ancestors(items, items[1]) == [items[0]];
    assert out == [false, false];
  }

  // D5: a reply in "dev" to a post in "ops", a channel ana is not in. Answer: the reply is hidden too.
  method D5_ParentInOtherChannel() {
    var items := [S.R.Item("p1", "ops", "bo", S.R.Post, "", false), P("p2", "cy", "p1")];
    var out := S.ShownTo(ANA, items);
    assert S.Find(items, "p1") == S.Some(items[0]) && S.Ancestors(items, items[1]) == [items[0]];
    assert out == [false, false];
  }

  // D6: a reply whose parent is not in the thread. Answer: the walk ends; the reply is visible.
  method D6_MissingParent() {
    var items := [P("p2", "cy", "gone")];
    var out := S.ShownTo(ANA, items);
    assert S.Find(items, "gone") == S.None && S.TakenDown(items) == {};
    assert out == [true];
  }

  // D7: cy tries to take down bo's post. Answer: no effect.
  method D7_TakedownByOther() {
    var items := [P("p1", "bo", ""), Down("t1", "cy", "p1")];
    var out := S.ShownTo(ANA, items);
    assert S.Find(items, "p1") == S.Some(items[0]) && !S.Takes(items, items[0]) && !S.Takes(items, items[1]);
    assert S.TakenDown(items) == {} && S.Ancestors(items, items[0]) == [];
    assert out == [true, false];
  }

  // D8: the moderator takes down bo's post. Answer: it is hidden.
  method D8_TakedownByModerator() {
    var items := [P("p1", "bo", ""), Down("t1", "mod", "p1")];
    var out := S.ShownTo(ANA, items);
    assert S.Find(items, "p1") == S.Some(items[0]) && S.Takes(items, items[1]) && "p1" in S.TakenDown(items);
    assert out == [false, false];
  }

  // D9: a viewer with the channel "" reads items whose channel is also "". Answer: nothing is visible.
  method D9_EmptyChannel() {
    var items := [S.R.Item("p1", "", "bo", S.R.Post, "", false)];
    var out := S.ShownTo(S.R.Viewer("ana", ""), items);
    assert out == [false];
  }
}
