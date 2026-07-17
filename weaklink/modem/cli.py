"""Modem CLI: byte-level tx/rx over the 4-FSK modem.

Two subcommands mirror the minimodem-rs CLI shape:

    weaklink-modem tx --input msg.bin --wav out.wav
    weaklink-modem rx --output rx.bin --wav out.wav --length 21

Live PulseAudio flows are supported by omitting ``--wav`` on either side, but
the offline WAV path is what the tests exercise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from weaklink.modem.codec import ModemConfig, decode, encode
from weaklink.modem.waveform import WaveformConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weaklink-modem", description="Weaklink 4-FSK modem tx/rx.")
    subparsers = parser.add_subparsers(dest="direction", required=True)

    for name in ("tx", "rx"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--baud", type=float, default=300.0)
        sub.add_argument("--sample-rate", type=float, default=48_000.0)
        sub.add_argument("--tone-spacing", type=float, default=None, help="Tone spacing in Hz. Defaults to baud.")
        sub.add_argument("--preamble-length", type=int, default=64, help="Preamble length in symbols.")
        sub.add_argument("--payload-repeats", type=int, default=1, help="Repeat the payload N times for soft combining gain.")
        sub.add_argument("--rs-data-bytes", type=int, default=None, help="Enable RS outer code with this many data bytes.")
        sub.add_argument("--rs-parity-bytes", type=int, default=8)
        sub.add_argument("--no-rs-crc", dest="rs_crc_enabled", action="store_false", default=True)
        sub.add_argument("--wav", type=Path, help="Read from / write to a WAV file instead of the audio device.")

    tx_parser = subparsers.choices["tx"]
    tx_parser.add_argument("--input", type=Path, help="Input file (default: stdin).")

    rx_parser = subparsers.choices["rx"]
    rx_parser.add_argument("--output", type=Path, help="Output file (default: stdout).")
    rx_parser.add_argument("--length", type=int, required=True, help="Expected payload length in bytes.")
    rx_parser.add_argument(
        "--record-seconds",
        type=float,
        default=None,
        help="Live-record duration when --wav is not set.",
    )
    return parser


def _make_config(args: argparse.Namespace) -> ModemConfig:
    tone_spacing = args.tone_spacing if args.tone_spacing is not None else args.baud
    return ModemConfig(
        waveform=WaveformConfig(baud=args.baud, sample_rate=args.sample_rate, tone_spacing_hz=tone_spacing),
        preamble_length=args.preamble_length,
        payload_repeats=args.payload_repeats,
        rs_data_bytes=args.rs_data_bytes,
        rs_parity_bytes=args.rs_parity_bytes,
        rs_crc_enabled=args.rs_crc_enabled,
    )


def _run_tx(args: argparse.Namespace) -> int:
    config = _make_config(args)
    payload = args.input.read_bytes() if args.input is not None else sys.stdin.buffer.read()
    samples = encode(payload, config)
    if args.wav is not None:
        from weaklink.modem.audio import write_wav

        write_wav(args.wav, samples, config.waveform.sample_rate)
    else:
        from weaklink.modem.audio import play

        play(samples, config.waveform.sample_rate)
    return 0


def _run_rx(args: argparse.Namespace) -> int:
    import numpy as np

    config = _make_config(args)
    if args.wav is not None:
        from weaklink.modem.audio import read_wav

        samples, _ = read_wav(args.wav, expected_sample_rate=config.waveform.sample_rate)
    else:
        if args.record_seconds is None:
            print("error: --record-seconds is required for live rx", file=sys.stderr)
            return 2
        from weaklink.modem.audio import record

        samples = record(args.record_seconds, config.waveform.sample_rate)

    decoded = decode(np.asarray(samples), config, payload_length_bytes=args.length)
    if args.output is not None:
        args.output.write_bytes(decoded)
    else:
        sys.stdout.buffer.write(decoded)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.direction == "tx":
        return _run_tx(args)
    return _run_rx(args)


if __name__ == "__main__":
    sys.exit(main())
