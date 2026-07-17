"""Frame layout: PN sync -> guard -> RS blocks x repeat.

On the wire:

    [ PN sync bits ][ guard_bits zero-bits ][ block_1 | block_2 | ... ][ repeat_count-1 further copies ]

Everything downstream of the PN is byte-aligned RS-encoded blocks, but we
serialise them as MSB-first bit streams so the sync search stays bit-level
regardless of underlying transport byte alignment.

No length header, no block index — TX and RX must agree on config. That's
per the plan; the trade-off is spelled out in the README.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from weaklink import pn
from weaklink.rs import RSBlockCodec
from weaklink.sync import StreamingDetector


@dataclass(frozen=True)
class FrameConfig:
    pn_length: int
    guard_bits: int
    num_blocks: int
    repeat_count: int
    sync_min_score: int  # minimum PN correlation score to declare acquisition

    def __post_init__(self) -> None:
        if self.pn_length not in pn.supported_lengths():
            raise ValueError(f"pn_length {self.pn_length} not in {pn.supported_lengths()}")
        if self.repeat_count < 1:
            raise ValueError("repeat_count must be >= 1")
        if self.num_blocks < 1:
            raise ValueError("num_blocks must be >= 1")
        if self.guard_bits < 0:
            raise ValueError("guard_bits must be >= 0")
        if self.sync_min_score < 1 or self.sync_min_score > self.pn_length:
            raise ValueError(f"sync_min_score {self.sync_min_score} out of range 1..{self.pn_length}")


def encode_frame(payload_blocks: Sequence[bytes], *, frame: FrameConfig, codec: RSBlockCodec) -> bytes:
    """Return the full transmitted bit stream (bytes of 0/1) for the frame."""
    if len(payload_blocks) != frame.num_blocks:
        raise ValueError(f"expected {frame.num_blocks} payload blocks, got {len(payload_blocks)}")

    pn_bits = pn.generate(frame.pn_length)
    guard_bits = bytes(frame.guard_bits)  # all zeros
    encoded_blocks = [codec.encode(block) for block in payload_blocks]
    data_bits = bytearray()
    for block in encoded_blocks:
        data_bits.extend(_bytes_to_bits_msb(block))

    out = bytearray()
    out.extend(pn_bits)
    out.extend(guard_bits)
    for _ in range(frame.repeat_count):
        out.extend(data_bits)
    return bytes(out)


def decode_frame(bits: Iterable[int], *, frame: FrameConfig, codec: RSBlockCodec) -> list[bytes | None]:
    """Search for PN sync, then decode ``num_blocks`` payloads.

    Returns a list of length ``num_blocks``; each entry is the decoded payload
    (bytes) or ``None`` if every repetition of that logical block failed to
    decode.
    """
    stream = bytes(bits) if not isinstance(bits, (bytes, bytearray)) else bytes(bits)
    reference = pn.generate(frame.pn_length)

    detector = StreamingDetector(
        pn=reference,
        min_score=frame.sync_min_score,
        max_search=frame.pn_length * 4,
    )
    sync_result = None
    for i, bit in enumerate(stream):
        sync_result = detector.push(bit)
        if sync_result is not None:
            # We consumed bits up to and including index i; the PN starts at
            # ``sync_result.position``. Continue from position + pn_length.
            break

    if sync_result is None:
        return [None] * frame.num_blocks

    data_start = sync_result.position + frame.pn_length + frame.guard_bits
    block_bits = codec.config.block_size * 8
    one_pass_bits = block_bits * frame.num_blocks

    results: list[bytes | None] = [None] * frame.num_blocks
    for repetition in range(frame.repeat_count):
        base = data_start + repetition * one_pass_bits
        if base + one_pass_bits > len(stream):
            break
        for block_index in range(frame.num_blocks):
            if results[block_index] is not None:
                continue
            offset = base + block_index * block_bits
            block_bytes = _bits_to_bytes_msb(stream[offset : offset + block_bits])
            decoded = codec.try_decode(block_bytes)
            if decoded is not None:
                results[block_index] = decoded
    return results


def _bytes_to_bits_msb(data: bytes) -> Iterator[int]:
    for byte in data:
        for shift in range(7, -1, -1):
            yield (byte >> shift) & 1


def _bits_to_bytes_msb(bits: Sequence[int]) -> bytes:
    if len(bits) % 8 != 0:
        raise ValueError(f"bit length {len(bits)} not a multiple of 8")
    out = bytearray(len(bits) // 8)
    for i, bit in enumerate(bits):
        out[i // 8] |= (bit & 1) << (7 - (i % 8))
    return bytes(out)
