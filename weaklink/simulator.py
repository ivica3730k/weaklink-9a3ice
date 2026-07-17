"""Offline test harness: 2-FSK modulator + AWGN channel + hard-decision demod.

This is deliberately *not* minimodem — we want a controlled, reproducible
channel where SNR is a knob we set, not a property we measure. The modem here
is the simplest thing that resembles the minimodem FSK link:

  bit -> ``samples_per_symbol`` samples of a sinusoid at ``mark`` or ``space``
       (mark = bit 1, space = bit 0)
  channel adds AWGN with a given Es/N0
  demodulator does non-coherent energy detection at each tone frequency,
  outputs a hard 0/1 per symbol

That's enough to sweep SNR against sync-detect rate and packet-decode rate.
When we replace minimodem with our own softer modem, this file becomes the
reference implementation of that modem.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - numpy is a hard requirement for the sim
    raise ImportError("weaklink.simulator requires numpy; add it to your dev deps") from exc


@dataclass(frozen=True)
class ChannelConfig:
    sample_rate: float = 48_000.0
    baud: float = 1_200.0
    mark_hz: float = 1_200.0
    space_hz: float = 2_200.0
    es_over_n0_db: float = 10.0
    """Per-symbol Es/N0 in dB. Lower = noisier."""
    rng_seed: int | None = 0


def modulate(bits: Iterable[int], channel: ChannelConfig) -> "np.ndarray":
    """Emit a real-valued baseband 2-FSK waveform, unit symbol energy."""
    samples_per_symbol = int(round(channel.sample_rate / channel.baud))
    if samples_per_symbol < 4:
        raise ValueError("sample_rate / baud too low; increase sample_rate")

    bit_list = [int(b) & 1 for b in bits]
    signal = np.zeros(len(bit_list) * samples_per_symbol, dtype=np.float64)
    time_axis = np.arange(samples_per_symbol) / channel.sample_rate

    mark_wave = np.sin(2 * math.pi * channel.mark_hz * time_axis)
    space_wave = np.sin(2 * math.pi * channel.space_hz * time_axis)
    # Normalise so each symbol carries unit energy (sum-of-squares = 1).
    mark_wave = mark_wave / math.sqrt(np.sum(mark_wave ** 2))
    space_wave = space_wave / math.sqrt(np.sum(space_wave ** 2))

    for index, bit in enumerate(bit_list):
        start = index * samples_per_symbol
        signal[start : start + samples_per_symbol] = mark_wave if bit else space_wave
    return signal


def add_awgn(signal: "np.ndarray", channel: ChannelConfig) -> "np.ndarray":
    """Add complex-equivalent AWGN calibrated by Es/N0 given unit symbol energy."""
    rng = np.random.default_rng(channel.rng_seed)
    # With unit symbol energy: Es = 1. N0 = Es / (Es/N0 linear). Noise variance
    # per real sample = N0/2 * samples_per_symbol, but because Es=1 already
    # accounts for the samples-per-symbol via the normalisation above, the
    # correct real-noise variance is N0/2 spread across the whole symbol; which
    # for a unit-energy waveform reduces to sigma^2 = 1 / (2 * Es/N0_linear).
    es_over_n0_linear = 10 ** (channel.es_over_n0_db / 10.0)
    sigma = math.sqrt(1.0 / (2.0 * es_over_n0_linear))
    noise = rng.normal(0.0, sigma, size=signal.shape)
    return signal + noise


def demodulate(signal: "np.ndarray", channel: ChannelConfig) -> bytes:
    """Non-coherent energy detector: score each symbol against both tones."""
    samples_per_symbol = int(round(channel.sample_rate / channel.baud))
    num_symbols = len(signal) // samples_per_symbol
    time_axis = np.arange(samples_per_symbol) / channel.sample_rate
    cos_mark = np.cos(2 * math.pi * channel.mark_hz * time_axis)
    sin_mark = np.sin(2 * math.pi * channel.mark_hz * time_axis)
    cos_space = np.cos(2 * math.pi * channel.space_hz * time_axis)
    sin_space = np.sin(2 * math.pi * channel.space_hz * time_axis)

    out = bytearray(num_symbols)
    for symbol_index in range(num_symbols):
        start = symbol_index * samples_per_symbol
        chunk = signal[start : start + samples_per_symbol]
        mark_energy = np.dot(chunk, cos_mark) ** 2 + np.dot(chunk, sin_mark) ** 2
        space_energy = np.dot(chunk, cos_space) ** 2 + np.dot(chunk, sin_space) ** 2
        out[symbol_index] = 1 if mark_energy >= space_energy else 0
    return bytes(out)


def loopback(bits: Iterable[int], channel: ChannelConfig) -> bytes:
    """Modulate -> AWGN -> demodulate. Returns hard-decision bits."""
    waveform = modulate(bits, channel)
    noisy = add_awgn(waveform, channel)
    return demodulate(noisy, channel)


# --- SNR sweep harness ------------------------------------------------------


@dataclass
class TrialResult:
    es_over_n0_db: float
    trials: int
    sync_hits: int
    blocks_decoded: int
    blocks_total: int

    @property
    def sync_rate(self) -> float:
        return self.sync_hits / self.trials if self.trials else 0.0

    @property
    def block_decode_rate(self) -> float:
        return self.blocks_decoded / self.blocks_total if self.blocks_total else 0.0
