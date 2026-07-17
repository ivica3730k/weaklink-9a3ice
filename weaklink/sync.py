"""PN sync detector.

Given a stream of demodulated hard-decision bits (0/1), find the offset that
maximises the correlation against a reference PN sequence, and report that
offset with its score. The score is the number of matching bits in the window,
which for an m-sequence with an on-peak of ``length`` bits and off-peak of ``-1``
gives an unambiguous acquisition signal in the absence of noise.

The correlator is deliberately simple: no timing recovery, no early/late gate,
no soft decisions. Establish a working baseline before optimising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SyncResult:
    position: int
    """Index into the input stream where the PN sequence starts."""
    score: int
    """Number of matching bits in the best window (max = len(pn))."""


def detect(bits: Iterable[int], pn: bytes, *, min_score: int | None = None) -> SyncResult | None:
    """Slide ``pn`` across ``bits`` and return the best-scoring alignment.

    Returns ``None`` if the best score is below ``min_score``. When
    ``min_score`` is ``None`` the highest-scoring position is always returned
    (useful for offline analysis / plotting).
    """
    stream = bytes(bits) if not isinstance(bits, (bytes, bytearray)) else bytes(bits)
    pn_length = len(pn)
    if len(stream) < pn_length:
        return None

    best_position = 0
    best_score = -1
    for offset in range(len(stream) - pn_length + 1):
        score = _match_count(stream, offset, pn)
        if score > best_score:
            best_score = score
            best_position = offset

    if min_score is not None and best_score < min_score:
        return None
    return SyncResult(position=best_position, score=best_score)


def _match_count(stream: bytes, offset: int, pn: bytes) -> int:
    count = 0
    for i, expected in enumerate(pn):
        if stream[offset + i] == expected:
            count += 1
    return count


def detect_streaming(pn: bytes, *, min_score: int, max_search: int) -> "StreamingDetector":
    """Convenience factory for the streaming detector."""
    return StreamingDetector(pn=pn, min_score=min_score, max_search=max_search)


class StreamingDetector:
    """Feed bits one at a time; report the first offset that clears ``min_score``.

    Keeps a rolling window of the last ``max_search`` bits and correlates the
    PN sequence at every offset in the window after each new bit. Once a match
    at or above ``min_score`` is found, ``result`` is populated and further
    bits are ignored.
    """

    def __init__(self, pn: bytes, *, min_score: int, max_search: int):
        if min_score < 1 or min_score > len(pn):
            raise ValueError(f"min_score {min_score} out of range 1..{len(pn)}")
        if max_search < len(pn):
            raise ValueError(f"max_search must be >= len(pn)={len(pn)}, got {max_search}")
        self._pn = pn
        self._min_score = min_score
        self._max_search = max_search
        self._buffer = bytearray()
        self._absolute_index = 0
        self.result: SyncResult | None = None

    def push(self, bit: int) -> SyncResult | None:
        if self.result is not None:
            return self.result
        self._buffer.append(1 if bit else 0)
        self._absolute_index += 1
        if len(self._buffer) > self._max_search:
            drop = len(self._buffer) - self._max_search
            del self._buffer[:drop]
        if len(self._buffer) < len(self._pn):
            return None
        pn_length = len(self._pn)
        window_start_absolute = self._absolute_index - len(self._buffer)
        for offset in range(len(self._buffer) - pn_length + 1):
            score = _match_count(bytes(self._buffer), offset, self._pn)
            if score >= self._min_score:
                self.result = SyncResult(position=window_start_absolute + offset, score=score)
                return self.result
        return None
