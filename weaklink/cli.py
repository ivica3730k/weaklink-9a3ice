"""weaklink CLI: framed tx/rx over minimodem.

Frame layout, PN length, guard, and repetition are set via flags — but there
are no on-wire headers, so both ends must be launched with matching values.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from weaklink import pn
from weaklink.framing import FrameConfig, decode_frame, encode_frame
from weaklink.rs import BlockConfig, RSBlockCodec
from weaklink.transport import MinimodemTransport

DEFAULT_PN_LENGTH = 127
DEFAULT_GUARD_BITS = 0
DEFAULT_NUM_BLOCKS = 4
DEFAULT_REPEAT_COUNT = 1
DEFAULT_DATA_BYTES = 16
DEFAULT_PARITY_BYTES = 8
DEFAULT_SYNC_MIN_SCORE_RATIO = 0.75


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weaklink", description="Weak-signal framing over minimodem (or later, our own modem).")
    subparsers = parser.add_subparsers(dest="direction", required=True)

    for name in ("tx", "rx"):
        sub = subparsers.add_parser(name)
        sub.add_argument("baud", type=int, help="Baud rate for the underlying modem (minimodem for now).")
        sub.add_argument("--pn-length", type=int, default=DEFAULT_PN_LENGTH, choices=pn.supported_lengths())
        sub.add_argument("--guard-bits", type=int, default=DEFAULT_GUARD_BITS)
        sub.add_argument("--num-blocks", type=int, default=DEFAULT_NUM_BLOCKS)
        sub.add_argument("--repeat-count", type=int, default=DEFAULT_REPEAT_COUNT)
        sub.add_argument("--data-bytes", type=int, default=DEFAULT_DATA_BYTES)
        sub.add_argument("--parity-bytes", type=int, default=DEFAULT_PARITY_BYTES)
        sub.add_argument("--no-crc", dest="crc_enabled", action="store_false", default=True)
        sub.add_argument(
            "--sync-min-score",
            type=int,
            default=None,
            help=f"Absolute PN score threshold. Default: {int(DEFAULT_SYNC_MIN_SCORE_RATIO * 100)}%% of PN length.",
        )

    return parser


def _make_configs(args: argparse.Namespace) -> tuple[FrameConfig, RSBlockCodec]:
    min_score = args.sync_min_score
    if min_score is None:
        min_score = max(1, int(round(args.pn_length * DEFAULT_SYNC_MIN_SCORE_RATIO)))
    frame = FrameConfig(
        pn_length=args.pn_length,
        guard_bits=args.guard_bits,
        num_blocks=args.num_blocks,
        repeat_count=args.repeat_count,
        sync_min_score=min_score,
    )
    codec = RSBlockCodec(
        BlockConfig(
            data_bytes=args.data_bytes,
            parity_bytes=args.parity_bytes,
            crc_enabled=args.crc_enabled,
        )
    )
    return frame, codec


def _run_tx(args: argparse.Namespace) -> int:
    frame, codec = _make_configs(args)
    input_bytes = sys.stdin.buffer.read()
    block_size = codec.config.data_bytes
    frame_payload_bytes = block_size * frame.num_blocks
    remainder = len(input_bytes) % frame_payload_bytes
    if remainder:
        input_bytes = input_bytes + b"\x00" * (frame_payload_bytes - remainder)

    transport = MinimodemTransport("tx", args.baud)
    for offset in range(0, len(input_bytes), frame_payload_bytes):
        chunk = input_bytes[offset : offset + frame_payload_bytes]
        blocks = [chunk[i : i + block_size] for i in range(0, frame_payload_bytes, block_size)]
        bit_stream = encode_frame(blocks, frame=frame, codec=codec)
        transport.send(bit_stream)
    return 0


def _run_rx(args: argparse.Namespace) -> int:
    frame, codec = _make_configs(args)
    transport = MinimodemTransport("rx", args.baud)
    bit_iter = transport.recv()
    # Simple offline-style flow: collect enough bits for one frame, decode, repeat.
    frame_length_bits = (
        args.pn_length
        + args.guard_bits
        + codec.config.block_size * 8 * frame.num_blocks * frame.repeat_count
    )
    buffer = bytearray()
    for bit in bit_iter:
        buffer.append(bit)
        if len(buffer) >= frame_length_bits:
            blocks = decode_frame(bytes(buffer), frame=frame, codec=codec)
            for block in blocks:
                if block is not None:
                    sys.stdout.buffer.write(block.rstrip(b"\x00"))
                    sys.stdout.buffer.flush()
            buffer.clear()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.direction == "tx":
        return _run_tx(args)
    return _run_rx(args)


if __name__ == "__main__":
    sys.exit(main())
