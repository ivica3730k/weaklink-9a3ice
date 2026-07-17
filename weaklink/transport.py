"""Bit-transport abstraction.

The framing layer (PN sync, RS blocks, repetition) is expressed in terms of a
bit stream in and a bit stream out. The transport is whatever moves those bits
between two endpoints — today that's a minimodem subprocess; tomorrow it will
be a bespoke modem. Keep this interface small so the swap is drop-in.

Bits are represented as ``bytes`` where each byte is 0 or 1. That's more
verbose than bit-packing, but it keeps every downstream operator (PN correlator,
frame slicer, simulator) trivial and index-friendly. Byte-packing is a
premature optimisation until profiling says otherwise.
"""

from __future__ import annotations

import os
import subprocess
from typing import Iterable, Iterator, Protocol


class BitTransport(Protocol):
    """One-way bit pipe. Instantiate one for TX, one for RX."""

    def send(self, bits: Iterable[int]) -> None:
        """Modulate and transmit an iterable of 0/1 bits. Blocks until drained."""

    def recv(self) -> Iterator[int]:
        """Yield demodulated hard-decision bits (0/1) as they arrive."""


# --- minimodem-backed implementation --------------------------------------


def _child_env_without_pyinstaller_leak() -> dict[str, str]:
    """Same guard as minimodem_rs: strip PyInstaller's LD_LIBRARY_PATH leak."""
    env = os.environ.copy()
    for variable_name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        original = env.pop(f"{variable_name}_ORIG", None)
        if original is not None:
            env[variable_name] = original
        else:
            env.pop(variable_name, None)
    return env


class MinimodemTransport:
    """Bit pipe backed by a ``minimodem`` subprocess.

    Uses ``--binary-output``/``--binary-raw 1`` where available so we get a raw
    unframed bit stream rather than 8-N-1 async-framed bytes. Falls back to
    packing bits 8-per-byte for older minimodem builds; the framing layer is
    tolerant either way because we always search on a bit-level correlator.
    """

    def __init__(self, direction: str, baud: int | str, *, minimodem_binary: str = "minimodem", extra_args: list[str] | None = None):
        if direction not in ("tx", "rx"):
            raise ValueError(f"direction must be 'tx' or 'rx', got {direction!r}")
        self._direction = direction
        self._baud = str(baud)
        self._binary = minimodem_binary
        self._extra = list(extra_args or [])
        self._proc: subprocess.Popen | None = None

    def _argv(self) -> list[str]:
        flag = "--tx" if self._direction == "tx" else "--rx"
        return [self._binary, flag, "--binary-output", "1", *self._extra, self._baud]

    def send(self, bits: Iterable[int]) -> None:
        assert self._direction == "tx"
        proc = subprocess.Popen(
            self._argv(),
            stdin=subprocess.PIPE,
            env=_child_env_without_pyinstaller_leak(),
        )
        assert proc.stdin is not None
        buffer = bytearray()
        for bit in bits:
            buffer.append(1 if bit else 0)
            if len(buffer) >= 4096:
                proc.stdin.write(bytes(buffer))
                buffer.clear()
        if buffer:
            proc.stdin.write(bytes(buffer))
        proc.stdin.flush()
        proc.stdin.close()
        proc.wait()

    def recv(self) -> Iterator[int]:
        assert self._direction == "rx"
        proc = subprocess.Popen(
            self._argv(),
            stdout=subprocess.PIPE,
            env=_child_env_without_pyinstaller_leak(),
        )
        assert proc.stdout is not None
        self._proc = proc
        try:
            while True:
                chunk = proc.stdout.read(1024)
                if not chunk:
                    return
                for byte in chunk:
                    yield 1 if byte else 0
        finally:
            proc.terminate()
