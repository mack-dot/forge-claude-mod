// The base rules of the thread-visibility check, in their own file. ThreadSpec.dfy includes this file, so an approval
// of the spec must cover this file too (a change here changes what the spec means).
module ThreadRules {
  datatype Kind = Post | Takedown
  // parent: the id of the item this one replies to; "" when it starts a thread. A takedown names its target as parent.
  datatype Item = Item(id: string, channel: string, author: string, kind: Kind, parent: string, flagged: bool)
  datatype Viewer = Viewer(user: string, channel: string)

  // R1: only members of the item's channel read it, and an empty channel name matches nothing.
  predicate Member(v: Viewer, it: Item) { v.channel != "" && v.channel == it.channel }
  // R2: an item is taken down only by its author or by the moderator account.
  predicate MayTakeDown(actor: string, target: Item) { actor != "" && (actor == target.author || actor == "mod") }
}
