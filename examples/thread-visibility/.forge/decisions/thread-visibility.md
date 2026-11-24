# thread-visibility

## The rule, in the person's words

"A reply is visible only if every post above it in its thread is visible, and a reply chain that loops back on itself must never hang the app."

Code: `thread_app.py`, `visible_ids()` (a synthetic example for Forge's own tests).

## Confirmed defaults (fail closed)

- Only members of an item's channel see it; an empty channel name matches nothing.
- A takedown counts only from the post's author or the moderator account `mod`.
- The walk up the thread ends at a missing parent, at no parent, or at an id already seen (a cycle).
- A later item with the same id replaces an earlier one.

## Decisions

| Id | Question | Answer |
|---|---|---|
| D1 | p1 and p2 reply to each other; bo takes p1 down. | The walk ends; neither is visible. |
| D2 | A post replies to itself. | The walk ends; visible. |
| D3 | A reply to a reply to a post taken down by its author. | None of the three is visible. |
| D4 | A flagged post with a reply. | Neither is visible. |
| D5 | A reply in dev to a post in ops (viewer not in ops). | The reply is hidden too. |
| D6 | A reply whose parent is not in the thread. | Visible. |
| D7 | cy takes down bo's post. | No effect. |
| D8 | The moderator takes down bo's post. | Hidden. |
| D9 | Viewer and item both have the channel "". | Not visible. |

## Out of scope (not guaranteed)

- How the thread is loaded from storage and in what order.
- The wrapper `thread_wrapper.py` (it converts plain values) and Dafny's Python compiler; the build fuzzes the compiled code.
