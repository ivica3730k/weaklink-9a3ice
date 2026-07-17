"""Deterministic pseudo-noise sequence generator.

Uses maximal-length linear feedback shift registers so the output for a given
length is deterministic and reproducible between TX and RX. Lengths 63 and 127
correspond to standard m-sequences of degree 6 and 7 respectively.

Taps are chosen from standard primitive polynomials over GF(2) (Peterson & Weldon,
"Error-Correcting Codes", Appendix C):
  n=6:  x^6 + x + 1                        -> taps = (6, 1)
  n=7:  x^7 + x^3 + 1                      -> taps = (7, 3)

Each sequence is length ``2^n - 1``, cycles through every non-zero state, and
has good autocorrelation (single peak of n bits, off-peak of -1/(2^n - 1)).
"""

from __future__ import annotations

from typing import Sequence

# Primitive polynomial taps for m-sequences at supported degrees.
# Convention: with this LFSR (shift right, output LSB, feedback into MSB), tap
# positions ``{n, k}`` implement the recurrence s_{t+n} = s_{t+n-1} XOR s_{t+k-1},
# whose characteristic polynomial is x^n + x^{n-1} + x^{k-1}. Both entries below
# choose k=1 to give the primitive polynomial x^n + x^{n-1} + 1.
_TAPS: dict[int, tuple[int, ...]] = {
    6: (6, 1),
    7: (7, 1),
}

DEFAULT_SEED = 0b1  # any non-zero state works; fix it for reproducibility


def supported_lengths() -> list[int]:
    return sorted((1 << n) - 1 for n in _TAPS)


def generate(length: int, *, seed: int = DEFAULT_SEED) -> bytes:
    """Return a PN bit sequence of exactly ``length`` bits as bytes of 0/1.

    Only standard m-sequence lengths (63, 127) are supported so we get the
    correlation properties for free. Add another entry to ``_TAPS`` to extend.
    """
    if seed == 0:
        raise ValueError("LFSR seed must be non-zero")
    for degree, taps in _TAPS.items():
        if length == (1 << degree) - 1:
            return _lfsr(degree, taps, seed, length)
    raise ValueError(f"unsupported PN length {length}; supported: {supported_lengths()}")


def _lfsr(degree: int, taps: Sequence[int], seed: int, length: int) -> bytes:
    """Fibonacci LFSR. Emits one bit per shift, so ``length`` = number of shifts."""
    mask = (1 << degree) - 1
    state = seed & mask
    if state == 0:
        raise ValueError("LFSR seed masked to zero")
    out = bytearray(length)
    for index in range(length):
        out[index] = state & 1
        feedback = 0
        for tap in taps:
            feedback ^= (state >> (tap - 1)) & 1
        state = ((state >> 1) | (feedback << (degree - 1))) & mask
    return bytes(out)
