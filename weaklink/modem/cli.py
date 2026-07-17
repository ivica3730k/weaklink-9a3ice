"""Streaming modem CLI.

Byte-side I/O is always stdin/stdout — use shell redirection for files or
pipes. Sample-side I/O is either a WAV file (``--modem-wav``) or the default
live audio device.

    echo -n "hello over air" | weaklink-modem tx --modem-wav out.wav
    cat message.txt         | weaklink-modem tx                     # live TX
    weaklink-modem rx --modem-wav out.wav > received.bin
    weaklink-modem rx                                                # live RX, Ctrl-C to stop
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from weaklink.modem.codec import ModemConfig, decode, encode
from weaklink.modem.waveform import WaveformConfig


def _add_modem_args(sub: argparse.ArgumentParser) -> None:
    """All modem-side knobs. ``--modem-*``."""
    modem = sub.add_argument_group("modem", "modem-layer configuration + sample-side I/O")
    modem.add_argument("--modem-baud", type=float, default=300.0, dest="modem_baud")
    modem.add_argument("--modem-sample-rate", type=float, default=48_000.0, dest="modem_sample_rate")
    modem.add_argument(
        "--modem-tone-spacing",
        type=float,
        default=None,
        dest="modem_tone_spacing",
        help="Tone spacing in Hz. Defaults to --modem-baud (Nyquist optimum for non-coherent 4-FSK).",
    )
    modem.add_argument("--modem-rs-data-bytes", type=int, default=16, dest="modem_rs_data_bytes")
    modem.add_argument("--modem-rs-parity-bytes", type=int, default=8, dest="modem_rs_parity_bytes")
    modem.add_argument(
        "--modem-no-rs-crc",
        dest="modem_rs_crc_enabled",
        action="store_false",
        default=True,
        help="Skip the CRC-32 inside the RS-protected region.",
    )
    modem.add_argument(
        "--modem-sync-every-blocks",
        type=int,
        default=4,
        dest="modem_sync_every_blocks",
        help="Preamble inserted every N data blocks (default 4).",
    )
    modem.add_argument(
        "--modem-block-repeats",
        type=int,
        default=1,
        dest="modem_block_repeats",
        help="Each block transmitted N times, round-robin (default 1). "
        "~3 dB per doubling in AWGN plus burst-fade diversity.",
    )
    modem.add_argument(
        "--modem-wav",
        type=Path,
        default=None,
        dest="modem_wav",
        help="Read from / write to a WAV file instead of the live audio device.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weaklink-modem", description="Streaming 4-FSK modem.")
    subparsers = parser.add_subparsers(dest="direction", required=True)
    tx_parser = subparsers.add_parser("tx", help="Encode stdin bytes and transmit (or write to WAV).")
    _add_modem_args(tx_parser)
    rx_parser = subparsers.add_parser("rx", help="Receive (or read WAV) and decode to stdout bytes.")
    _add_modem_args(rx_parser)
    return parser


def _make_config(args: argparse.Namespace) -> ModemConfig:
    tone_spacing = args.modem_tone_spacing if args.modem_tone_spacing is not None else args.modem_baud
    return ModemConfig(
        waveform=WaveformConfig(
            baud=args.modem_baud,
            sample_rate=args.modem_sample_rate,
            tone_spacing_hz=tone_spacing,
        ),
        rs_data_bytes=args.modem_rs_data_bytes,
        rs_parity_bytes=args.modem_rs_parity_bytes,
        rs_crc_enabled=args.modem_rs_crc_enabled,
        sync_every_blocks=args.modem_sync_every_blocks,
        block_repeats=args.modem_block_repeats,
    )


def _run_tx(args: argparse.Namespace) -> int:
    config = _make_config(args)
    payload = sys.stdin.buffer.read()
    samples = encode(payload, config)
    if args.modem_wav is not None:
        from weaklink.modem.audio import write_wav

        write_wav(args.modem_wav, samples, config.waveform.sample_rate)
    else:
        from weaklink.modem.audio import play

        play(samples, config.waveform.sample_rate)
    return 0


def _run_rx(args: argparse.Namespace) -> int:
    import numpy as np

    config = _make_config(args)
    if args.modem_wav is not None:
        from weaklink.modem.audio import read_wav

        samples, _ = read_wav(args.modem_wav, expected_sample_rate=config.waveform.sample_rate)
    else:
        from weaklink.modem.audio import record_until_interrupted

        samples = record_until_interrupted(config.waveform.sample_rate)

    decoded = decode(np.asarray(samples), config)
    output = decoded.rstrip(b"\x00")  # strip trailing NUL padding TX added at the RS-block boundary
    sys.stdout.buffer.write(output)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.direction == "tx":
        return _run_tx(args)
    return _run_rx(args)


if __name__ == "__main__":
    sys.exit(main())
