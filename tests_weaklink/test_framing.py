"""End-to-end framing: encode -> (identity channel) -> decode."""

from __future__ import annotations

import pytest

from weaklink.framing import FrameConfig, decode_frame, encode_frame
from weaklink.rs import BlockConfig, RSBlockCodec


def _codec() -> RSBlockCodec:
    return RSBlockCodec(BlockConfig(data_bytes=16, parity_bytes=8, crc_enabled=True))


def _frame(repeat_count: int = 1) -> FrameConfig:
    return FrameConfig(
        pn_length=127,
        guard_bits=0,
        num_blocks=3,
        repeat_count=repeat_count,
        sync_min_score=100,
    )


def test_encode_decode_roundtrip_clean_channel() -> None:
    frame = _frame()
    codec = _codec()
    payloads = [b"Block one........", b"Block two........", b"Block three....."]
    payloads = [p[:16].ljust(16, b"\x00") for p in payloads]
    bits = encode_frame(payloads, frame=frame, codec=codec)
    decoded = decode_frame(bits, frame=frame, codec=codec)
    assert decoded == payloads


def test_repetition_recovers_when_one_copy_is_corrupted() -> None:
    frame = _frame(repeat_count=2)
    codec = _codec()
    payloads = [b"A" * 16, b"B" * 16, b"C" * 16]
    bits = bytearray(encode_frame(payloads, frame=frame, codec=codec))
    # Wipe the first copy of block 1 by flipping many bits — more errors than RS
    # can correct (parity=8 corrects 4 bytes, i.e. up to 32 bit flips).
    block_bits = codec.config.block_size * 8
    data_start = frame.pn_length + frame.guard_bits
    for i in range(data_start + block_bits, data_start + block_bits * 2, 2):
        bits[i] ^= 1
    decoded = decode_frame(bytes(bits), frame=frame, codec=codec)
    assert decoded == payloads


def test_bad_length_payload_rejected_at_encode() -> None:
    frame = _frame()
    codec = _codec()
    with pytest.raises(ValueError):
        encode_frame([b"too short", b"x" * 16, b"y" * 16], frame=frame, codec=codec)
