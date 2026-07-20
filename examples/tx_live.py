"""Encode bytes and play them through the default audio output.

Run:
    python examples/tx_live.py

Point your microphone / SDR / rig audio input at the speaker (or use
a loopback cable) and run examples/rx_live.py to receive.
"""

from __future__ import annotations

import sounddevice as sd

from weaklink.modem import tx


def main() -> None:
    payload = b"hello over the air"

    # Encode to float32 samples. 300 baud at N=4 tones, default preset
    # (block_repeats=2, RS(16,8)). tx_volume=100 = full-scale amplitude.
    audio = tx(payload, baud=300, num_tones=4, tx_volume=100)
    sample_rate = 18_000  # weaklink's internal rate; matches what tx() emits.

    print(f"playing {audio.size / sample_rate:.2f} s of audio")
    sd.play(audio, samplerate=sample_rate, blocking=True)
    print("done")


if __name__ == "__main__":
    main()
