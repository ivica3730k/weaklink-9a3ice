"""Sync detector: clean-channel alignment + tolerance to bit errors."""

from __future__ import annotations

import random

import pytest

from weaklink import pn
from weaklink.sync import StreamingDetector, detect


@pytest.mark.parametrize("length", pn.supported_lengths())
def test_detect_finds_exact_position_in_clean_stream(length: int) -> None:
    sequence = pn.generate(length)
    padding_before = bytes([0] * 40)
    padding_after = bytes([0] * 40)
    stream = padding_before + sequence + padding_after
    result = detect(stream, sequence)
    assert result is not None
    assert result.position == len(padding_before)
    assert result.score == length


def test_detect_tolerates_bit_errors_up_to_a_point() -> None:
    length = 127
    sequence = pn.generate(length)
    stream = bytearray(bytes([0] * 30) + sequence + bytes([0] * 30))
    rng = random.Random(42)
    # Flip 20% of the PN bits within the received copy.
    flips = rng.sample(range(30, 30 + length), k=length // 5)
    for idx in flips:
        stream[idx] ^= 1
    result = detect(bytes(stream), sequence)
    assert result is not None
    assert result.position == 30
    assert result.score >= length - len(flips) - 5  # allow correlation to spill a couple


def test_streaming_detector_reports_first_hit() -> None:
    length = 63
    sequence = pn.generate(length)
    stream = bytes([0] * 10) + sequence + bytes([1] * 10)
    detector = StreamingDetector(pn=sequence, min_score=length, max_search=length * 3)
    hit = None
    for bit in stream:
        hit = detector.push(bit)
        if hit is not None:
            break
    assert hit is not None
    assert hit.position == 10
    assert hit.score == length
