import random

import pytest

from orchestrator.retry import compute_backoff_seconds


def test_backoff_increases_with_attempt():
    rng = random.Random(42)
    b1 = compute_backoff_seconds(1, rng=rng)
    b2 = compute_backoff_seconds(2, rng=rng)
    b3 = compute_backoff_seconds(3, rng=rng)
    assert b1 < b2 < b3


def test_backoff_respects_cap():
    rng = random.Random(1)
    for attempt in range(1, 20):
        b = compute_backoff_seconds(attempt, cap_seconds=60, jitter_ratio=0.0, rng=rng)
        assert b <= 60


def test_backoff_jitter_varies_output():
    rng = random.Random(7)
    values = {compute_backoff_seconds(4, rng=rng) for _ in range(10)}
    assert len(values) > 1


def test_backoff_rejects_invalid_attempt():
    with pytest.raises(ValueError):
        compute_backoff_seconds(0)
