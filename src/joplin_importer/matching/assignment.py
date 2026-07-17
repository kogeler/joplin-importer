"""Globally consistent assignment for ambiguous match groups.

Maximum-weight bipartite matching with explicit unmatched options, solved with
the Hungarian algorithm (O(n^3)) per connected component. Weak pairs are never
forced: every node has an "unmatched" alternative at the baseline score, so
global optimization cannot pair duplicate ``Untitled Page`` items merely to
maximize total weight.
"""

from __future__ import annotations

from collections import defaultdict

_NEG = -1e9


def hungarian_min_cost(cost: list[list[float]]) -> list[int]:
    """Solve the square assignment problem, minimizing total cost.

    Returns ``assignment`` where ``assignment[row] = col``.
    Classic Kuhn-Munkres with potentials, O(n^3).
    """
    n = len(cost)
    if n == 0:
        return []
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)  # p[col] = row matched to col (1-based)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    assignment = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


def assign_pairs(
    scores: dict[tuple[str, str], float],
    *,
    unmatched_baseline: float,
) -> dict[str, str]:
    """Choose a globally consistent set of (source, target) pairs.

    ``scores`` maps ``(source_id, target_id)`` to a similarity in [0, 1].
    A pair is selected only when its score beats ``unmatched_baseline`` — the
    combined value of leaving both endpoints unmatched (each endpoint collects
    ``unmatched_baseline / 2``). Returns ``{source_id: target_id}``.
    """
    if not scores:
        return {}

    # split into connected components to keep matrices small
    graph: dict[str, set[str]] = defaultdict(set)
    for source_id, target_id in scores:
        graph[f"s:{source_id}"].add(f"t:{target_id}")
        graph[f"t:{target_id}"].add(f"s:{source_id}")

    visited: set[str] = set()
    result: dict[str, str] = {}
    for start in sorted(graph):
        if start in visited:
            continue
        component: list[str] = []
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            stack.extend(graph[node] - visited)
        sources = sorted(n[2:] for n in component if n.startswith("s:"))
        targets = sorted(n[2:] for n in component if n.startswith("t:"))
        result.update(_assign_component(sources, targets, scores, unmatched_baseline))
    return result


def _assign_component(
    sources: list[str],
    targets: list[str],
    scores: dict[tuple[str, str], float],
    baseline: float,
) -> dict[str, str]:
    p, t = len(sources), len(targets)
    size = p + t
    unmatched_value = baseline / 2  # per endpoint; a pair must beat 2x this
    # weight matrix: rows = sources + dummy(target-unmatched),
    # cols = targets + dummy(source-unmatched)
    weight = [[_NEG] * size for _ in range(size)]
    for i, source_id in enumerate(sources):
        for j, target_id in enumerate(targets):
            pair_score = scores.get((source_id, target_id))
            if pair_score is not None:
                weight[i][j] = pair_score
        # source i unmatched: dummy column i
        weight[i][t + i] = unmatched_value
    for j in range(t):
        # target j unmatched: dummy row j
        weight[p + j][j] = unmatched_value
    for i in range(t):
        for j in range(p):
            weight[p + i][t + j] = 0.0  # dummy-dummy pairs are free

    max_weight = max(max(row) for row in weight)
    cost = [[max_weight - w for w in row] for row in weight]
    assignment = hungarian_min_cost(cost)

    result: dict[str, str] = {}
    for i, source_id in enumerate(sources):
        j = assignment[i]
        if j < t and weight[i][j] > _NEG / 2:
            result[source_id] = targets[j]
    return result
