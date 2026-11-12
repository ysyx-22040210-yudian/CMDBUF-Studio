"""Exact minimum-cardinality set cover over the hit bins of a VDB regression."""
from itertools import islice
from time import monotonic


popcount = getattr(int, 'bit_count', lambda value: bin(value).count('1'))


def union(masks):
    result = 0
    for mask in masks:
        result |= mask
    return result


def bits(mask):
    while mask:
        bit = mask & -mask
        yield bit
        mask ^= bit


def distinct(rows, remaining):
    profiles = {}
    for index, mask in rows:
        reduced = mask & remaining
        if reduced:
            profiles.setdefault(reduced, index)
    return [(index, mask) for mask, index in profiles.items()]


def minimum_cover(masks, seconds=0, progress=None):
    """Return a feasible set and bounds. A time limit may end proof early."""
    start = monotonic()
    target = remaining = union(masks)
    rows = distinct(enumerate(masks), remaining)
    profiles = len(rows)
    forced = []
    while remaining:
        once = twice = 0
        for _, mask in rows:
            twice |= once & mask
            once |= mask
        single = once & ~twice
        if not single:
            break
        required = [(index, mask) for index, mask in rows if mask & single]
        forced.extend(index for index, _ in required)
        remaining &= ~union(mask for _, mask in required)
        rows = distinct(rows, remaining)

    # An equal-cost superset can replace a subset in any solution. Bound the
    # comparisons on large matrices. Unpruned rows remain valid search choices.
    kept = []
    for index, mask in sorted(rows, key=lambda row: (-popcount(row[1]), row[0])):
        if not any(mask & other == mask for _, other in islice(kept, 256)):
            kept.append((index, mask))
    rows = kept
    incumbent = []
    left = remaining
    while left:
        index, mask = max(rows, key=lambda row: popcount(row[1] & left))
        incumbent.append(index)
        left &= ~mask

    supports = {}

    def options(bit):
        if bit not in supports:
            packed = bytearray((len(rows)+7)//8)
            for i, (_, mask) in enumerate(rows):
                if mask & bit:
                    packed[i//8] |= 1 << (i%8)
            supports[bit] = int.from_bytes(packed, 'little')
        return supports[bit]

    def branch(left):
        # Inspect a bounded number of obligations at each node. Choosing any
        # uncovered bin is exhaustive. Rarer bins reduce the branching factor.
        candidates = sorted({options(bit) for bit in islice(bits(left), 20)}, key=popcount)
        disjoint, packing = 0, 0
        for support in candidates:
            if not support & disjoint:
                disjoint |= support
                packing += 1
        return candidates[0], packing

    nodes = 0
    lower = 0
    if remaining:
        width = popcount(remaining)
        largest = popcount(rows[0][1])
        lower = (width+largest-1)//largest
        if lower < len(incumbent):
            lower = max(lower, branch(remaining)[1])
    stack = [(remaining, ())] if lower < len(incumbent) else []
    visited = {}
    last_update = start
    while stack and lower < len(incumbent):
        now = monotonic()
        if seconds and now-start >= seconds:
            break
        if progress and now-last_update >= 1:
            progress(nodes, len(forced)+lower, len(forced)+len(incumbent))
            last_update = now
        left, selected = stack.pop()
        nodes += 1
        if not left:
            if len(selected) < len(incumbent):
                incumbent = selected
            continue
        depth = len(selected)
        if depth+1 >= len(incumbent) or visited.get(left, depth+1) <= depth:
            continue
        if len(visited) < 200000:
            visited[left] = depth
        largest = max(popcount(mask & left) for _, mask in rows)
        width = popcount(left)
        if depth+(width+largest-1)//largest >= len(incumbent):
            continue
        candidates, packing = branch(left)
        if depth+packing >= len(incumbent):
            continue
        gains = {}
        for bit in bits(candidates):
            index, mask = rows[bit.bit_length()-1]
            gains.setdefault(mask & left, index)
        for gain, index in sorted(gains.items(), key=lambda pair: popcount(pair[0])):
            stack.append((left & ~gain, selected+(index,)))

    selected = sorted(forced+list(incumbent))
    proven = not stack or lower == len(incumbent)
    if union(masks[index] for index in selected) != target:
        raise RuntimeError('Coverage solver returned an incomplete set')
    return dict(selected=selected, optimal=proven, lower_bound=len(selected) if proven else len(forced)+lower,
                upper_bound=len(selected), nodes=nodes, distinct_profiles=profiles,
                seconds=round(monotonic()-start, 3))
