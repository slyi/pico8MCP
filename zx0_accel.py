"""Optional Python-authored CPU accelerators for the standard ZX0 v2 parser.

The NumPy parser batches integer costs across offsets and materializes immutable
parse nodes only when needed. The Numba parser uses packed arrays and a reusable
node pool. Both preserve the reference parser's ordering and tie-breaking.

(c) Copyright 2021 by Einar Saukas. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
 * Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
 * The name of its author may not be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""

_NUMBA_KERNEL = None


def optimal(data, offset_limit, progress, backend, fallback):
    """Use optional packages when available; retain the standard-library fallback."""
    if backend == "auto":
        if len(data) < 512:
            return fallback(data, offset_limit, progress)
        try:
            import numpy
        except (ImportError, OSError):
            return fallback(data, offset_limit, progress)
        if len(data) >= 4096:
            try:
                import numba
            except (ImportError, OSError):
                pass
            else:
                return optimal_numba(data, offset_limit, progress)
        return optimal_numpy(data, offset_limit, progress)
    if backend == "numpy":
        return optimal_numpy(data, offset_limit, progress)
    if backend == "numba":
        return optimal_numba(data, offset_limit, progress)
    raise ValueError("Unknown ZX0 backend: " + str(backend))


def optimal_numpy(data, offset_limit, progress=None):
    """Vectorized exact costs, followed by lazy replay of offsets on the final path."""
    from bisect import bisect_right
    import numpy as np

    size = len(data)
    ceiling = max(1, min(size - 1, offset_limit))
    inf = 1 << 60
    raw = np.frombuffer(data, dtype=np.uint8)
    gamma = np.array([0] + [2 * n.bit_length() - 1 for n in range(1, size + 1)], dtype=np.int64)
    offsets = np.arange(1, ceiling + 1, dtype=np.int32)
    offset_cost = np.array([8 + 2 * ((o - 1) // 128 + 1).bit_length() - 1
                            for o in range(1, ceiling + 1)], dtype=np.int64)
    literal_bits = np.full(ceiling, inf, dtype=np.int64)
    literal_end = np.full(ceiling, -1, dtype=np.int32)
    match_bits = np.full(ceiling, inf, dtype=np.int64)
    match_end = np.full(ceiling, -1, dtype=np.int32)
    runs = np.zeros(ceiling, dtype=np.int32)
    costs = np.empty(size, dtype=np.int64)
    winners = np.ones(size, dtype=np.int32)
    winning_match = np.zeros(size, dtype=bool)
    match_bits[0] = -1
    literal_bits[0] = costs[0] = 9
    literal_end[0] = 0
    if progress is not None:
        progress(1, size)
    for index in range(1, size):
        active = min(index, offset_limit)
        eq = raw[index - offsets[:active]] == raw[index]
        run = np.where(eq, runs[:active] + 1, 0)
        repeat = np.where(literal_bits[:active] < inf,
                          literal_bits[:active] + 1 + gamma[index - literal_end[:active]], inf)
        maximum = int(run.max())
        if maximum >= 2:
            lengths = np.arange(2, maximum + 1, dtype=np.int32)
            prefix = np.minimum.accumulate(costs[index - lengths] + gamma[lengths - 1])
            new = np.where(run >= 2, prefix[np.maximum(run - 2, 0)] + offset_cost[:active], inf)
            matched = np.minimum(repeat, new)
        else:
            matched = repeat
        length = index - match_end[:active]
        literal = match_bits[:active] + 1 + gamma[length] + length * 8
        candidates = np.where(eq, matched, literal)
        winner = int(candidates.argmin())
        costs[index] = candidates[winner]
        winners[index] = winner + 1
        winning_match[index] = eq[winner]
        changed = eq & (matched < inf)
        match_bits[:active] = np.where(changed, matched, match_bits[:active])
        match_end[:active] = np.where(changed, index, match_end[:active])
        literal_bits[:active] = np.where(eq, literal_bits[:active], literal)
        literal_end[:active] = np.where(eq, literal_end[:active], index)
        runs[:active] = run
        if progress is not None and (index % 256 == 0 or index == size - 1):
            progress(index + 1, size)

    # Only a small subset of offsets occurs on the chosen path. Replaying those
    # offset histories avoids allocating a node for every candidate in the scan.
    histories = {}
    matches = {(-1, 1): (-1, None)}

    def match_at(end, start, offset, previous_end, previous_bits):
        length = end - start + 1
        repeat_bits = inf
        if previous_bits < inf:
            literal_length = start - 1 - previous_end
            literal_cost = previous_bits + 1 + int(gamma[literal_length]) + literal_length * 8
            repeat_bits = literal_cost + 1 + int(gamma[length])
        new_bits = inf
        chosen = 0
        if length >= 2:
            lengths = np.arange(2, length + 1, dtype=np.int32)
            values = costs[end - lengths] + gamma[lengths - 1]
            # Last minimum wins: the reference prefers longer lengths on ties.
            chosen = length - int(values[::-1].argmin())
            new_bits = int(values[chosen - 2]) + int(offset_cost[offset - 1])
        if new_bits < repeat_bits:
            return new_bits, ("optimal", end - chosen, 0)
        if repeat_bits < inf:
            return repeat_bits, ("literal", start - 1, offset)
        return inf, None

    def history(offset):
        found = histories.get(offset)
        if found is not None:
            return found
        equal = raw[offset:] == raw[:-offset]
        edges = np.flatnonzero(np.diff(np.concatenate(([False], equal, [False]))))
        starts = (edges[::2] + offset).tolist()
        ends = (edges[1::2] + offset - 1).tolist()
        previous_ends = []
        previous_costs = []
        previous_end = -1
        previous_bits = -1 if offset == 1 else inf
        for start, end in zip(starts, ends):
            previous_ends.append(previous_end)
            previous_costs.append(previous_bits)
            state = match_at(end, start, offset, previous_end, previous_bits)
            if state[0] < inf:
                matches[end, offset] = state
                previous_end, previous_bits = end, state[0]
        found = starts, ends, previous_ends, previous_costs
        histories[offset] = found
        return found

    path = []
    descriptor = ("optimal", size - 1, 0)
    while descriptor is not None:
        kind, end, offset = descriptor
        if kind == "optimal":
            offset = int(winners[end])
            descriptor = ("match" if winning_match[end] else "literal", end, offset)
            continue
        if kind == "match":
            state = matches.get((end, offset))
            if state is None:
                starts, ends, previous_ends, previous_costs = history(offset)
                state = matches.get((end, offset))
                if state is None:
                    run = bisect_right(starts, end) - 1
                    state = match_at(end, starts[run], offset, previous_ends[run], previous_costs[run])
                    matches[end, offset] = state
            bits, descriptor = state
            path.append((bits, end, offset))
        else:
            starts, ends, previous_ends, previous_costs = history(offset)
            run = bisect_right(ends, end) - 1
            previous_end = ends[run] if run >= 0 else -1
            previous_bits = matches.get((previous_end, offset), (inf, None))[0]
            length = end - previous_end
            bits = previous_bits + 1 + int(gamma[length]) + length * 8
            path.append((bits, end, 0))
            descriptor = ("match", previous_end, offset)
    block = None
    for bits, index, offset in reversed(path):
        block = (bits, index, offset, block)
    return block


def _numba_kernel():
    global _NUMBA_KERNEL, np
    global _numba_release_jit, _numba_allocate_jit, _numba_freeze_jit
    if _NUMBA_KERNEL is not None:
        return _NUMBA_KERNEL
    import numpy as np
    from numba import njit
    # Module-level functions have stable cache identities across processes.
    _numba_release_jit = njit(inline="always")(_numba_release)
    _numba_allocate_jit = njit(inline="always")(_numba_allocate)
    _numba_freeze_jit = njit(inline="always")(_numba_freeze)
    _NUMBA_KERNEL = njit(nogil=True, cache=True)(_numba_scan)
    return _NUMBA_KERNEL


def _numba_release(pool, state, node):
    while node >= 0:
        pool[node, 4] -= 1
        if pool[node, 4] != 0:
            break
        previous = pool[node, 3]
        pool[node, 5] = state[0]
        state[0] = node
        node = previous


def _numba_allocate(pool, state, bits, index, offset, previous):
    if state[0] >= 0:
        node = state[0]
        state[0] = pool[node, 5]
    else:
        node = state[1]
        if node == len(pool):
            grown = np.empty((len(pool) * 2, 6), dtype=np.int64)
            grown[:len(pool)] = pool
            pool = grown
        state[1] += 1
    pool[node, 0] = bits
    pool[node, 1] = index
    pool[node, 2] = offset
    pool[node, 3] = previous
    pool[node, 4] = 1
    if previous >= 0:
        pool[previous, 4] += 1
    return pool, node


def _numba_freeze(pool, state, bits, end, offset, kind, literal_bits, literal_end, literal_previous,
           previous_index, optimal):
    if kind == 1:
        pool, previous = _numba_allocate_jit(pool, state, literal_bits, literal_end, 0, literal_previous)
        pool, node = _numba_allocate_jit(pool, state, bits, end, offset, previous)
        _numba_release_jit(pool, state, previous)
    else:
        pool, node = _numba_allocate_jit(pool, state, bits, end, offset, optimal[previous_index])
    return pool, node


def _numba_scan(raw, offset_limit, gamma, offset_cost):
    size = len(raw)
    ceiling = max(1, min(size - 1, offset_limit))
    inf = np.int64(1) << 60
    literal_bits = np.full(ceiling + 1, inf, dtype=np.int64)
    literal_end = np.full(ceiling + 1, -1, dtype=np.int64)
    literal_previous = np.full(ceiling + 1, -1, dtype=np.int64)
    match_bits = np.full(ceiling + 1, inf, dtype=np.int64)
    match_end = np.full(ceiling + 1, -1, dtype=np.int64)
    match_kind = np.zeros(ceiling + 1, dtype=np.int64)
    match_previous = np.zeros(ceiling + 1, dtype=np.int64)
    match_nodes = np.full(ceiling + 1, -1, dtype=np.int64)
    costs = np.empty(size, dtype=np.int64)
    optimal = np.full(size, -1, dtype=np.int64)
    lengths = np.zeros(ceiling + 1, dtype=np.int64)
    best = np.zeros(max(3, size), dtype=np.int64)
    best[2] = 2
    pool = np.empty((max(1024, size * 4), 6), dtype=np.int64)
    state = np.array([-1, 0], dtype=np.int64)
    pool, sentinel = _numba_allocate_jit(pool, state, -1, -1, 1, -1)
    match_bits[1] = -1
    match_nodes[1] = sentinel
    literal_previous[1] = sentinel
    pool[sentinel, 4] += 1
    for index in range(size):
        best_size = 2
        winner_bits = inf
        winner_offset = 0
        winner_kind = 0
        for offset in range(1, max(1, min(index, offset_limit)) + 1):
            if index and index >= offset and raw[index] == raw[index - offset]:
                bits = inf
                kind = 0
                previous_index = 0
                if literal_bits[offset] < inf:
                    length = index - literal_end[offset]
                    bits = literal_bits[offset] + 1 + gamma[length]
                    kind = 1
                length = lengths[offset] + 1
                lengths[offset] = length
                if length > 1:
                    if best_size < length:
                        chosen = best[best_size]
                        best_bits = costs[index - chosen] + gamma[chosen - 1]
                        while best_size < length:
                            best_size += 1
                            candidate = costs[index - best_size] + gamma[best_size - 1]
                            if candidate <= best_bits:
                                best[best_size] = best_size
                                best_bits = candidate
                            else:
                                best[best_size] = best[best_size - 1]
                    chosen = best[length]
                    candidate = costs[index - chosen] + offset_cost[offset] + gamma[chosen - 1]
                    if candidate < bits:
                        bits = candidate
                        kind = 2
                        previous_index = index - chosen
                if kind:
                    _numba_release_jit(pool, state, match_nodes[offset])
                    match_nodes[offset] = -1
                    match_bits[offset] = bits
                    match_end[offset] = index
                    match_kind[offset] = kind
                    match_previous[offset] = previous_index
                    if winner_bits > bits:
                        winner_bits = bits
                        winner_offset = offset
                        winner_kind = 1
            else:
                if lengths[offset] > 0 and match_bits[offset] < inf:
                    if match_nodes[offset] < 0:
                        pool, node = _numba_freeze_jit(pool, state, match_bits[offset], match_end[offset], offset,
                                            match_kind[offset], literal_bits[offset], literal_end[offset],
                                            literal_previous[offset], match_previous[offset], optimal)
                        match_nodes[offset] = node
                    node = match_nodes[offset]
                    pool[node, 4] += 1
                    _numba_release_jit(pool, state, literal_previous[offset])
                    literal_previous[offset] = node
                lengths[offset] = 0
                if match_bits[offset] < inf:
                    length = index - match_end[offset]
                    bits = match_bits[offset] + 1 + gamma[length] + length * 8
                    literal_bits[offset] = bits
                    literal_end[offset] = index
                    if winner_bits > bits:
                        winner_bits = bits
                        winner_offset = offset
                        winner_kind = 0
        costs[index] = winner_bits
        if winner_kind:
            offset = winner_offset
            if match_nodes[offset] < 0:
                pool, node = _numba_freeze_jit(pool, state, match_bits[offset], match_end[offset], offset,
                                    match_kind[offset], literal_bits[offset], literal_end[offset],
                                    literal_previous[offset], match_previous[offset], optimal)
                match_nodes[offset] = node
            node = match_nodes[offset]
            pool[node, 4] += 1
        else:
            pool, node = _numba_allocate_jit(pool, state, winner_bits, index, 0, literal_previous[winner_offset])
        optimal[index] = node
    return pool, optimal[-1]



def optimal_numba(data, offset_limit, progress=None):
    import numpy as np
    size = len(data)
    ceiling = max(1, min(size - 1, offset_limit))
    gamma = np.array([0] + [2 * n.bit_length() - 1 for n in range(1, size + 1)], dtype=np.int64)
    offset_cost = np.array([0] + [8 + 2 * ((o - 1) // 128 + 1).bit_length() - 1
                                 for o in range(1, ceiling + 1)], dtype=np.int64)
    if progress is not None:
        progress(0, size)
    pool, node = _numba_kernel()(np.frombuffer(data, dtype=np.uint8), offset_limit, gamma, offset_cost)
    path = []
    while node >= 0:
        bits, index, offset, previous = pool[node, :4]
        path.append((int(bits), int(index), int(offset)))
        node = int(previous)
    block = None
    for bits, index, offset in reversed(path):
        block = (bits, index, offset, block)
    if progress is not None:
        progress(size, size)
    return block
