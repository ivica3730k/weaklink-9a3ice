"""Record from the default audio input for a fixed duration and
decode whatever weaklink signal is in there.

Run:
    python examples/rx_live.py

Continuous streaming decode (poll-and-emit) isn't exposed as a public
API yet -- for that use the CLI (`weaklink-modem rx`). This example
shows the "record a window then batch-decode" pattern which is often
enough for a single message.
"""

from __future__ import annotations

import logging
import sys

import numpy as np
import sounddevice as sd

from weaklink.modem import rx


def main() -> None:
    sample_rate = 18_000  # weaklink's internal rate
    record_seconds = 30

    # Route weaklink diagnostics through our own logger so we see peak /
    # rms snapshots, coarse offset, per-slot decode outcomes.
    logger = logging.getLogger("example.rx")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(name)s %(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    print(f"recording {record_seconds} s at {sample_rate} Hz... (Ctrl-C to stop early)")
    try:
        audio = sd.rec(
            int(record_seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
        )
        sd.wait()
    except KeyboardInterrupt:
        sd.stop()
        print("stopped early")
        return

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    payload = rx(audio, baud=300, num_tones=4, logger=logger)
    if payload:
        print(f"decoded {len(payload)} bytes: {payload!r}")
    else:
        print("no signal decoded (silence, wrong baud, or audio path issue)")


if __name__ == "__main__":
    main()
