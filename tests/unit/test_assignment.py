import itertools
import random

from joplin_importer.matching.assignment import assign_pairs, hungarian_min_cost


def brute_force_best(scores, baseline):
    """Exhaustive best assignment for tiny instances (baseline/2 per unmatched node)."""
    sources = sorted({s for s, _t in scores})
    targets = sorted({t for _s, t in scores})
    unmatched = baseline / 2
    best_total, best_map = None, {}
    for k in range(len(sources) + 1):
        for chosen_sources in itertools.combinations(sources, k):
            for chosen_targets in itertools.permutations(targets, k):
                pairs = list(zip(chosen_sources, chosen_targets, strict=True))
                if any((s, t) not in scores for s, t in pairs):
                    continue
                total = sum(scores[(s, t)] for s, t in pairs)
                total += unmatched * (len(sources) - k)  # unmatched sources
                total += unmatched * (len(targets) - k)  # unmatched targets
                if best_total is None or total > best_total + 1e-12:
                    best_total = total
                    best_map = dict(pairs)
    return best_total, best_map


def total_of(mapping, scores, baseline, sources, targets):
    unmatched = baseline / 2
    total = sum(scores[(s, t)] for s, t in mapping.items())
    total += unmatched * (len(sources) - len(mapping))
    total += unmatched * (len(targets) - len(mapping))
    return total


def test_hungarian_simple():
    cost = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]
    assignment = hungarian_min_cost(cost)
    total = sum(cost[i][assignment[i]] for i in range(3))
    assert sorted(assignment) == [0, 1, 2]
    assert total == 5  # 1 + 2 + 2


def test_assign_prefers_strong_pairs():
    scores = {
        ("p1", "n1"): 0.95,
        ("p1", "n2"): 0.60,
        ("p2", "n1"): 0.61,
        ("p2", "n2"): 0.90,
    }
    result = assign_pairs(scores, unmatched_baseline=0.45)
    assert result == {"p1": "n1", "p2": "n2"}


def test_assign_leaves_weak_pairs_unmatched():
    # global assignment must not force a weak pair just to raise total weight
    scores = {("p1", "n1"): 0.30}
    result = assign_pairs(scores, unmatched_baseline=0.45)
    assert result == {}


def test_duplicate_untitled_pages_not_forced():
    # two identical-quality candidates, one clearly better target for each
    scores = {
        ("p1", "n1"): 0.50,
        ("p2", "n1"): 0.50,
    }
    result = assign_pairs(scores, unmatched_baseline=0.45)
    # only one page can take n1; the other stays unmatched instead of a bad pair
    assert len(result) == 1


def test_assignment_matches_brute_force_on_random_instances():
    rng = random.Random(42)
    for _trial in range(60):
        n_sources = rng.randint(1, 4)
        n_targets = rng.randint(1, 4)
        sources = [f"p{i}" for i in range(n_sources)]
        targets = [f"n{j}" for j in range(n_targets)]
        scores = {}
        for s in sources:
            for t in targets:
                if rng.random() < 0.7:
                    scores[(s, t)] = round(rng.uniform(0.3, 1.0), 3)
        if not scores:
            continue
        baseline = 0.45
        best_total, _best_map = brute_force_best(scores, baseline)
        got = assign_pairs(scores, unmatched_baseline=baseline)
        # nodes without any candidate pair are not part of the problem
        scored_sources = sorted({s for s, _t in scores})
        scored_targets = sorted({t for _s, t in scores})
        got_total = total_of(got, scores, baseline, scored_sources, scored_targets)
        assert abs(got_total - best_total) < 1e-6, (scores, got)


def test_components_are_isolated():
    scores = {
        ("p1", "n1"): 0.9,
        ("p2", "n2"): 0.8,
    }
    result = assign_pairs(scores, unmatched_baseline=0.45)
    assert result == {"p1": "n1", "p2": "n2"}
