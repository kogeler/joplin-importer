"""Component-level comparison of two canonical semantic models.

Blocks are compared by canonical hash as multisets, so moved-but-identical
content does not count as loss, while genuinely missing tables, code blocks,
list items, or paragraphs are reported per component kind.
"""

from __future__ import annotations

from collections import Counter

from ..models.hashing import sha256_canonical_json
from ..normalization.model import Node, iter_blocks


def block_hashes(model: Node) -> Counter[tuple[str, str]]:
    """Multiset of (kind, hash) for every top-level block."""
    counter: Counter[tuple[str, str]] = Counter()
    for block in iter_blocks(model):
        counter[(block.get("kind", "?"), sha256_canonical_json(block))] += 1
    return counter


def diff_models(source: Node, target: Node) -> dict:
    """Blocks present in source but absent in target, and vice versa."""
    src = block_hashes(source)
    dst = block_hashes(target)
    missing = src - dst  # in source, not in target
    extra = dst - src

    def by_kind(counter: Counter[tuple[str, str]]) -> dict[str, int]:
        kinds: Counter[str] = Counter()
        for (kind, _digest), count in counter.items():
            kinds[kind] += count
        return dict(sorted(kinds.items()))

    return {
        "missing_blocks": sum(missing.values()),
        "extra_blocks": sum(extra.values()),
        "missing_by_kind": by_kind(missing),
        "extra_by_kind": by_kind(extra),
        "identical_blocks": sum((src & dst).values()),
    }


def describe_diff(diff: dict) -> str:
    parts: list[str] = []
    if diff["missing_blocks"]:
        kinds = ", ".join(f"{k}:{v}" for k, v in diff["missing_by_kind"].items())
        parts.append(f"{diff['missing_blocks']} source block(s) missing in target ({kinds})")
    if diff["extra_blocks"]:
        kinds = ", ".join(f"{k}:{v}" for k, v in diff["extra_by_kind"].items())
        parts.append(f"{diff['extra_blocks']} extra block(s) in target ({kinds})")
    if not parts:
        parts.append("semantic models are equivalent")
    return "; ".join(parts)
