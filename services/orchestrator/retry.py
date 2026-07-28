"""Exponential backoff with jitter for task retries (constitution section 6)."""
import random


def compute_backoff_seconds(
    attempt_number: int,
    base_seconds: float = 2.0,
    cap_seconds: float = 300.0,
    jitter_ratio: float = 0.2,
    rng: random.Random | None = None,
) -> float:
    """attempt_number is 1-indexed (first retry = 1)."""
    if attempt_number < 1:
        raise ValueError("attempt_number must be >= 1")
    rng = rng or random
    raw = min(cap_seconds, base_seconds * (2 ** (attempt_number - 1)))
    jitter = raw * jitter_ratio
    return max(0.0, raw + rng.uniform(-jitter, jitter))
