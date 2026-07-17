"""PN generator: reproducibility + m-sequence autocorrelation property."""

from __future__ import annotations

import pytest

from weaklink import pn


@pytest.mark.parametrize("length", pn.supported_lengths())
def test_generate_is_deterministic(length: int) -> None:
    first = pn.generate(length)
    second = pn.generate(length)
    assert first == second
    assert len(first) == length
    assert set(first).issubset({0, 1})


def test_generate_rejects_unsupported_length() -> None:
    with pytest.raises(ValueError):
        pn.generate(100)


def test_generate_rejects_zero_seed() -> None:
    with pytest.raises(ValueError):
        pn.generate(63, seed=0)


@pytest.mark.parametrize("length", pn.supported_lengths())
def test_autocorrelation_peak_and_off_peak(length: int) -> None:
    """m-sequences have a single perfect-match position; every shift is at length/2 ± small."""
    sequence = pn.generate(length)
    # +/-1 domain for autocorrelation
    signed = [1 if bit else -1 for bit in sequence]
    # Zero-lag autocorrelation is length.
    zero_lag = sum(a * a for a in signed)
    assert zero_lag == length
    # Circular autocorrelation at any non-zero lag is -1 for an m-sequence.
    for lag in range(1, length):
        shifted = signed[lag:] + signed[:lag]
        correlation = sum(a * b for a, b in zip(signed, shifted))
        assert correlation == -1, f"m-sequence property violated at lag {lag}: got {correlation}"
