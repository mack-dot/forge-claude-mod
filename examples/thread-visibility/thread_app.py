"""The thread code shipped before Forge (a synthetic example). Forge's differential fuzz compares it with the proved
rule. Not proved."""


def may_take_down(actor, target):
    return actor != '' and (actor == target['author'] or actor == 'mod')


def visible_ids(viewer, items):
    """The ids of the items `viewer` may see, in thread order."""
    by_id = {it['id']: it for it in items}

    def ancestors(it):
        out, seen, ref = [], {it['id']}, it['parent']
        while ref and ref in by_id and ref not in seen:
            parent = by_id[ref]
            out.append(parent)
            seen.add(ref)
            ref = parent['parent']
        return out

    taken = {t['parent'] for t in items
             if t['kind'] == 'takedown' and t['parent'] and t['parent'] in by_id and may_take_down(t['author'], by_id[t['parent']])}

    def fine(it):
        return it['id'] not in taken and not it['flagged'] and viewer['channel'] != '' and viewer['channel'] == it['channel']

    return [it['id'] for it in items if it['kind'] == 'post' and fine(it) and all(fine(a) for a in ancestors(it))]
