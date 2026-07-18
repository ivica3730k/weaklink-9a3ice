"""Audio I/O: WAV files (soundfile) and live PortAudio via sounddevice.

Device selection accepts four permutations, in order of precedence:

1. **Integer index**: a numeric string is used as a raw
   ``sounddevice.query_devices()`` index.
2. **Substring against a sounddevice device name**: e.g. ``USB``, ``Scarlett``,
   ``pulse``. First device whose name contains the hint (or vice versa) wins.
3. **Pulse sink / source name that only exists inside PulseAudio / PipeWire**:
   e.g. a name from ``pactl list short sinks`` that isn't enumerated by
   PortAudio. We open the generic ``pulse`` PortAudio device and set
   ``PULSE_SINK`` / ``PULSE_SOURCE`` so libpulse routes to the named endpoint.
   Also set the equivalent ``PIPEWIRE_NODE`` for PipeWire-based systems that
   don't honour ``PULSE_*`` cleanly.
4. **Nothing given**: use PortAudio's own default (respecting ``PULSE_*`` env
   vars the user has set outside the process).

Both dependencies are imported lazily so pure-DSP tests can run without an
audio server.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

_log = logging.getLogger("weaklink.audio")


def write_wav(path: Path | str, samples: np.ndarray, sample_rate: float) -> None:
    """Write float32 mono samples to a WAV file."""
    import soundfile

    soundfile.write(str(path), np.asarray(samples, dtype=np.float32), int(round(sample_rate)))


def read_wav(path: Path | str, *, expected_sample_rate: float | None = None) -> tuple[np.ndarray, int]:
    """Read a WAV file, downmixing to mono if needed.

    Returns ``(samples_float32, sample_rate)``. Raises if
    ``expected_sample_rate`` is given and doesn't match.
    """
    import soundfile

    data, sample_rate = soundfile.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.float32)
    if expected_sample_rate is not None and int(round(expected_sample_rate)) != int(sample_rate):
        raise ValueError(
            f"WAV sample rate {sample_rate} Hz does not match expected {expected_sample_rate} Hz"
        )
    return data, int(sample_rate)


def play(samples: np.ndarray, sample_rate: float, *, device: str | None = None, blocking: bool = True) -> None:
    """Play ``samples`` through ``device`` (name / index / Pulse-sink) or the
    OS default."""
    sd = _import_sounddevice()
    hint = device if device else os.environ.get("PULSE_SINK")
    resolved = _resolve_device(sd, hint, kind="output")
    sd.play(
        np.asarray(samples, dtype=np.float32),
        int(round(sample_rate)),
        device=resolved,
        blocking=blocking,
    )
    if blocking:
        sd.wait()


def _import_sounddevice() -> Any:
    try:
        import sounddevice  # noqa: WPS433 - deferred import is intentional
    except ImportError as exc:
        raise ImportError(
            "sounddevice is required for live audio I/O. Install with `pip install sounddevice` "
            "or (on Debian/Ubuntu) `sudo apt install libportaudio2` first."
        ) from exc
    return sounddevice


def _resolve_device(sd: Any, name_hint: str | None, *, kind: str) -> int | None:
    """Resolve a user-supplied device hint to a sounddevice index.

    Handles the four permutations documented at the top of this module.
    ``kind`` is ``"input"`` or ``"output"`` -- we only match devices that
    have channels in the requested direction.

    For the Pulse-only fallback we set ``PULSE_SINK`` / ``PULSE_SOURCE`` (and
    ``PIPEWIRE_NODE`` for good measure) so libpulse / pipewire-pulse routes
    the stream once PortAudio opens the generic ``pulse`` device. Env-var
    mutation is scoped to this process only.
    """
    if not name_hint:
        return None
    channel_attr = "max_input_channels" if kind == "input" else "max_output_channels"

    # Permutation 1: bare integer -> raw sounddevice index.
    if name_hint.lstrip("-").isdigit():
        return int(name_hint)

    try:
        devices = sd.query_devices()
    except Exception:
        _log.debug("sounddevice.query_devices() failed while resolving %r", name_hint)
        return None
    hint_lower = name_hint.lower()

    # Permutation 2: substring match against a sounddevice name.
    for index, info in enumerate(devices):
        if info.get(channel_attr, 0) <= 0:
            continue
        name = str(info.get("name", "")).lower()
        if hint_lower in name or name in hint_lower:
            _log.debug("device hint %r -> sounddevice %d %r", name_hint, index, info["name"])
            return index

    # Permutation 3: Pulse-only name. Route via the pulse/pipewire compat
    # device, and set env vars so the library honours the requested endpoint.
    pulse_env_var = "PULSE_SOURCE" if kind == "input" else "PULSE_SINK"
    for index, info in enumerate(devices):
        if info.get(channel_attr, 0) <= 0:
            continue
        n = str(info.get("name", "")).lower()
        if n in ("pulse", "pipewire"):
            os.environ[pulse_env_var] = name_hint
            os.environ.setdefault("PIPEWIRE_NODE", name_hint)
            _log.debug("device hint %r -> pulse/pipewire compat device %d (%s=%s)",
                       name_hint, index, pulse_env_var, name_hint)
            return index

    _log.warning("device hint %r did not match any %s device; using default", name_hint, kind)
    return None
