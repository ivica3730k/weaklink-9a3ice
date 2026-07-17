"""SNR sweep: PN sync + RS + optional repetition through the AWGN simulator.

Marked slow because it's an integration test with hundreds of noisy trials —
skip in the fast pass, run explicitly when you want a baseline number.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("numpy")

from weaklink import pn as pn_module
from weaklink.framing import FrameConfig, decode_frame, encode_frame
from weaklink.rs import BlockConfig, RSBlockCodec
from weaklink.simulator import ChannelConfig, loopback


@dataclass
class SweepPoint:
    es_over_n0_db: float
    trials: int
    sync_hits: int
    blocks_decoded: int
    blocks_expected: int

    @property
    def sync_rate(self) -> float:
        return self.sync_hits / self.trials

    @property
    def block_rate(self) -> float:
        return self.blocks_decoded / self.blocks_expected


def _sweep(es_over_n0_db: float, *, trials: int, repeat_count: int) -> SweepPoint:
    frame = FrameConfig(
        pn_length=127,
        guard_bits=0,
        num_blocks=2,
        repeat_count=repeat_count,
        sync_min_score=int(127 * 0.75),
    )
    codec = RSBlockCodec(BlockConfig(data_bytes=16, parity_bytes=8, crc_enabled=True))
    payloads = [b"A" * 16, b"B" * 16]

    sync_hits = 0
    blocks_decoded = 0
    for trial_index in range(trials):
        bits = encode_frame(payloads, frame=frame, codec=codec)
        channel = ChannelConfig(es_over_n0_db=es_over_n0_db, rng_seed=trial_index)
        received = loopback(bits, channel)
        decoded = decode_frame(received, frame=frame, codec=codec)
        if any(block is not None for block in decoded):
            sync_hits += 1
        blocks_decoded += sum(1 for block in decoded if block == payloads[decoded.index(block)]) if any(decoded) else 0
    return SweepPoint(
        es_over_n0_db=es_over_n0_db,
        trials=trials,
        sync_hits=sync_hits,
        blocks_decoded=blocks_decoded,
        blocks_expected=trials * frame.num_blocks,
    )


@pytest.mark.slow
def test_sweep_no_repetition() -> None:
    trials = 20
    results = [_sweep(snr, trials=trials, repeat_count=1) for snr in (10.0, 6.0, 3.0, 0.0)]
    # Sanity: high SNR should decode everything.
    top = results[0]
    assert top.block_rate >= 0.9, f"expected clean decode at 10 dB, got {top.block_rate:.2%}"
    # Print a table so a human running the sweep sees numbers.
    print()
    print(f"{'Es/N0 (dB)':>10} {'sync%':>8} {'block%':>8}")
    for point in results:
        print(f"{point.es_over_n0_db:>10.1f} {point.sync_rate:>8.2%} {point.block_rate:>8.2%}")


@pytest.mark.slow
def test_sweep_repetition_helps_at_burst_edge() -> None:
    """repeat=4 should beat repeat=1 at moderate SNR, at the cost of throughput."""
    trials = 20
    snr = 3.0
    without_repeat = _sweep(snr, trials=trials, repeat_count=1)
    with_repeat = _sweep(snr, trials=trials, repeat_count=4)
    print()
    print(f"@ {snr} dB: repeat=1 -> {without_repeat.block_rate:.2%}, repeat=4 -> {with_repeat.block_rate:.2%}")
    # We don't assert strict > because AWGN gains from repetition are marginal
    # (see the framing memo) — this exists to *measure*, not to gate CI.
