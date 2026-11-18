"""Input checks shared by every indicator: fail loudly rather than compute on bad input."""
import math
from numbers import Real


def period(n, name="n"):
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"{name} must be a positive whole number, got {n!r}")
    return n


def series(*columns):
    """Every column the same length and every value a finite number; returns the length."""
    lengths = {len(c) for c in columns}
    if len(lengths) > 1:
        raise ValueError(f"inputs differ in length: {sorted(lengths)}")
    for c in columns:
        for i, v in enumerate(c):
            if isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v):
                raise ValueError(f"value {i} is not a finite number: {v!r}")
    return lengths.pop() if lengths else 0


def wilder(values, n):
    """Wilder smoothing: None until n inputs, then their mean, then prev + (x - prev) / n.

    `values` may start with None (a warm-up from an earlier step); smoothing starts at the first number.
    """
    out = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    if len(values) - start < n:
        return out
    avg = sum(values[start:start + n]) / n
    out[start + n - 1] = avg
    for i in range(start + n, len(values)):
        avg += (values[i] - avg) / n
        out[i] = avg
    return out
