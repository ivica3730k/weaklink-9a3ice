"""Weak-signal ham use case: 15 chars in ~30 s at very low SNR.

Configuration under test:
- 30 baud, 4-FSK, tone spacing = baud
- 64-symbol preamble with frequency-offset compensation
- Reed-Solomon(24,16) outer + CRC
- 3x payload repetition with soft magnitude combining
- Rate-1/2 K=7 convolutional inner code + soft Viterbi

Design targets:
- Wall time <= 30 s per packet
- Clean decode at SNR = -15 dB in 3 kHz
- Recovery at SNR = -17 dB in 3 kHz for most trials
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from weaklink.modem.cli import main as modem_main
from weaklink.modem.codec import ModemConfig, decode, encode
from weaklink.modem.waveform import WaveformConfig

WEAK_SIGNAL_CONFIG = ModemConfig(
    waveform=WaveformConfig(baud=30.0, tone_spacing_hz=30.0),
    preamble_length=64,
    payload_repeats=3,
    rs_data_bytes=16,
    rs_parity_bytes=8,
    rs_crc_enabled=True,
)


def _add_awgn(samples: np.ndarray, snr_db: float, sample_rate: float, *, bandwidth_hz: float, seed: int) -> np.ndarray:
    signal_power = float(np.mean(np.asarray(samples, dtype=np.float64) ** 2))
    noise_variance = signal_power * sample_rate / (2.0 * bandwidth_hz) / (10 ** (snr_db / 10.0))
    rng = np.random.default_rng(seed)
    return samples + rng.normal(0.0, np.sqrt(noise_variance), size=samples.shape).astype(np.float32)


def _random_message(length: int, seed: int) -> bytes:
    alphabet = (string.ascii_letters + string.digits + " ").encode("ascii")
    return bytes(random.Random(seed).choices(alphabet, k=length))


def test_15_chars_fit_within_30_seconds() -> None:
    payload = _random_message(15, seed=0)
    samples = encode(payload, WEAK_SIGNAL_CONFIG)
    duration = len(samples) / WEAK_SIGNAL_CONFIG.waveform.sample_rate
    assert duration <= 30.0, f"packet is {duration:.2f}s, over the 30s budget"


def test_clean_decode_at_high_snr() -> None:
    payload = _random_message(15, seed=1)
    samples = encode(payload, WEAK_SIGNAL_CONFIG)
    assert decode(samples, WEAK_SIGNAL_CONFIG, payload_length_bytes=15) == payload


def test_decode_survives_minus_15_db_snr() -> None:
    payload = _random_message(15, seed=2)
    samples = encode(payload, WEAK_SIGNAL_CONFIG)
    noisy = _add_awgn(
        samples,
        snr_db=-15.0,
        sample_rate=WEAK_SIGNAL_CONFIG.waveform.sample_rate,
        bandwidth_hz=3_000.0,
        seed=42,
    )
    assert decode(noisy, WEAK_SIGNAL_CONFIG, payload_length_bytes=15) == payload


def test_frequency_offset_estimator_recovers_small_shift() -> None:
    """When TX is transmitted at f0 + delta_f, estimate_frequency_offset should return delta_f."""
    from weaklink.modem.waveform import WaveformConfig, estimate_frequency_offset, modulate
    from weaklink.modem.codec import preamble_symbols

    config = WEAK_SIGNAL_CONFIG
    preamble = preamble_symbols(config)
    shifted_waveform = WaveformConfig(
        baud=config.waveform.baud,
        sample_rate=config.waveform.sample_rate,
        center_hz=config.waveform.center_hz + 12.0,
        tone_spacing_hz=config.waveform.tone_spacing_hz,
    )
    shifted_samples = modulate(preamble, shifted_waveform)
    estimated = estimate_frequency_offset(
        shifted_samples,
        config.waveform,
        preamble,
        search_range_hz=20.0,
        resolution_hz=1.0,
    )
    assert abs(estimated - 12.0) <= 1.0


def test_coarse_frequency_offset_recovers_1khz_shift() -> None:
    """Big-offset case: SSB dial off by 1 kHz. FFT-based coarse search should find it."""
    from weaklink.modem.waveform import WaveformConfig, estimate_coarse_frequency_offset, modulate
    from weaklink.modem.codec import preamble_symbols

    config = WEAK_SIGNAL_CONFIG
    preamble = preamble_symbols(config)
    shifted_waveform = WaveformConfig(
        baud=config.waveform.baud,
        sample_rate=config.waveform.sample_rate,
        center_hz=config.waveform.center_hz + 1000.0,
        tone_spacing_hz=config.waveform.tone_spacing_hz,
    )
    shifted_samples = modulate(preamble, shifted_waveform)
    coarse = estimate_coarse_frequency_offset(
        shifted_samples,
        config.waveform,
        search_range_hz=1500.0,
    )
    assert abs(coarse - 1000.0) <= 5.0


def test_end_to_end_survives_1khz_offset() -> None:
    """Full packet decode when TX is 1 kHz off frequency."""
    from weaklink.modem.waveform import WaveformConfig
    from dataclasses import replace

    tx_waveform = replace(
        WEAK_SIGNAL_CONFIG.waveform,
        center_hz=WEAK_SIGNAL_CONFIG.waveform.center_hz + 1000.0,
    )
    tx_config = replace(WEAK_SIGNAL_CONFIG, waveform=tx_waveform)
    rx_config = replace(WEAK_SIGNAL_CONFIG, coarse_frequency_search_hz=1500.0)

    payload = _random_message(15, seed=77)
    samples = encode(payload, tx_config)
    # Add a light AWGN so the coarse search isn't just picking up a pristine signal.
    noisy = _add_awgn(
        samples, snr_db=0.0, sample_rate=tx_config.waveform.sample_rate, bandwidth_hz=3_000.0, seed=5
    )
    assert decode(noisy, rx_config, payload_length_bytes=15) == payload


def test_e2e_wav_roundtrip_via_cli(tmp_path: Path) -> None:
    """Full CLI TX -> WAV -> CLI RX -> bytes match, at the target config."""
    payload = _random_message(15, seed=4)
    input_file = tmp_path / "msg.bin"
    wav_file = tmp_path / "signal.wav"
    output_file = tmp_path / "out.bin"
    input_file.write_bytes(payload)

    common = [
        "--baud", "30",
        "--tone-spacing", "30",
        "--preamble-length", "64",
        "--payload-repeats", "3",
        "--rs-data-bytes", "16",
        "--rs-parity-bytes", "8",
    ]
    tx_exit = modem_main(["tx", *common, "--input", str(input_file), "--wav", str(wav_file)])
    assert tx_exit == 0

    rx_exit = modem_main(
        ["rx", *common, "--output", str(output_file), "--wav", str(wav_file), "--length", "15"]
    )
    assert rx_exit == 0
    assert output_file.read_bytes() == payload


# --- SNR sweep for the low-baud config -----------------------------------


@dataclass
class SweepPoint:
    snr_db: float
    trials: int
    successful_decodes: int

    @property
    def success_rate(self) -> float:
        return self.successful_decodes / self.trials


@pytest.mark.slow
def test_weak_signal_snr_sweep() -> None:
    trials = 8
    payload = _random_message(15, seed=99)
    samples = encode(payload, WEAK_SIGNAL_CONFIG)

    sample_rate = WEAK_SIGNAL_CONFIG.waveform.sample_rate
    print()
    print(f"{'SNR (dB in 3 kHz)':>18} {'success rate':>18}")
    for snr_db in (-10, -13, -15, -16, -17, -18, -20):
        successes = 0
        for trial_index in range(trials):
            noisy = _add_awgn(samples, snr_db=snr_db, sample_rate=sample_rate, bandwidth_hz=3_000.0, seed=trial_index)
            if decode(noisy, WEAK_SIGNAL_CONFIG, payload_length_bytes=15) == payload:
                successes += 1
        point = SweepPoint(snr_db=snr_db, trials=trials, successful_decodes=successes)
        print(f"{point.snr_db:>18.1f} {point.success_rate:>18.2%}")
