"""Weighted scoring and confidence thresholds.

Weights and thresholds are configurable and versioned; every score keeps its
full explanation. Numbers were calibrated against the labeled synthetic
fixtures in tests/unit/test_matching.py — an uncalibrated score is never
presented as certainty (the confidence buckets are what the tool reports).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.enums import MatchConfidence

THRESHOLD_VERSION = "joplin-importer-thresholds/1"

DEFAULT_WEIGHTS: dict[str, float] = {
    "title": 2.0,
    "path": 1.5,
    "created_time": 1.0,
    "updated_time": 0.5,
    "text": 3.0,
    "semantic": 2.0,
    "resources": 2.0,
    "counts": 0.5,
    "urls": 1.0,
    "fragments": 1.5,
    "level": 0.25,
}


@dataclass
class MatchingConfig:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    version: str = THRESHOLD_VERSION
    #: minimum weighted score for any pairing at all
    min_score: float = 0.45
    #: score at/above which a pair is 'probable'
    probable_score: float = 0.65
    #: score at/above which a pair is 'high-confidence'
    high_score: float = 0.82
    #: required margin over the runner-up for probable/high buckets
    min_margin: float = 0.05
    #: below this many applicable features a score is too thin to trust
    min_applicable_features: int = 3


def weighted_score(
    features: dict[str, float | None], weights: dict[str, float]
) -> tuple[float, int]:
    """Weighted mean over applicable (non-None) features.

    Returns (score, number of applicable features).
    """
    total = 0.0
    weight_sum = 0.0
    applicable = 0
    for name, value in features.items():
        if value is None:
            continue
        weight = weights.get(name, 0.0)
        if weight <= 0:
            continue
        total += weight * value
        weight_sum += weight
        applicable += 1
    if weight_sum == 0:
        return 0.0, 0
    return total / weight_sum, applicable


def classify(
    score: float,
    margin: float,
    applicable: int,
    config: MatchingConfig,
) -> MatchConfidence:
    """Map a score + runner-up margin to a confidence bucket."""
    if applicable < config.min_applicable_features or score < config.min_score:
        return MatchConfidence.UNMATCHED
    if score >= config.high_score and margin >= config.min_margin:
        return MatchConfidence.HIGH_CONFIDENCE
    if score >= config.probable_score and margin >= config.min_margin:
        return MatchConfidence.PROBABLE
    return MatchConfidence.AMBIGUOUS
