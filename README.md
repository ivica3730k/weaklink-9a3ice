# weaklink

A streaming digital modem. Bytes in → audio out → audio in → bytes out. No
framing, no packet vocabulary, no length field visible to the caller — just
a byte pipe with an FEC-hardened wire in the middle. Layer whatever
protocol you like on top.

Both sides stream: `tx` encodes stdin block-by-block and pipes audio out as
it's generated (works with `tail -f`), `rx` writes decoded bytes to stdout
as blocks arrive. No wait-for-EOF, no giant buffers.

![alt text](image.png)

Distribution: `weaklink-9a3ice`.

---

## Install

Portable Linux binary (recommended — no Python, no venv):

```bash
sudo apt install libportaudio2 libsndfile1        # runtime shared libs
curl -L -O https://github.com/ivica3730k/weaklink-9a3ice/releases/latest/download/weaklink-9a3ice-linux-x86_64-latest
chmod +x weaklink-9a3ice-linux-x86_64-latest
./weaklink-9a3ice-linux-x86_64-latest --version
```

The `-latest` suffix always points at the newest release. To pin a specific
version, grab `weaklink-9a3ice-linux-x86_64-X.Y.Z` from the release page.

Debian / Ubuntu `.deb` (installs `weaklink-9a3ice` on `$PATH`):

```bash
curl -L -O https://github.com/ivica3730k/weaklink-9a3ice/releases/latest/download/weaklink-9a3ice_amd64-latest.deb
sudo dpkg -i weaklink-9a3ice_amd64-latest.deb
weaklink-9a3ice --version
```

From source (macOS or when you want to hack on it):

```bash
poetry install
poetry run weaklink-9a3ice --version
```

---

## Quickstart

Send a message to a WAV file, read it back:

```bash
echo -n "hello weaklink" | weaklink-9a3ice tx --modem-wav /tmp/hello.wav
weaklink-9a3ice rx --modem-wav /tmp/hello.wav
# → hello weaklink
```

Live speaker → mic:

```bash
weaklink-9a3ice rx > out.txt &      # start listening
echo -n "over the room" | weaklink-9a3ice tx
# Ctrl-C the rx after the tones stop
```

Long-lived stream (arbitrary length, no memory buffering):

```bash
tail -f /var/log/syslog | weaklink-9a3ice tx --modem-baud 300
```

Both sides must run with the same `--modem-baud`. There's no handshake, so
the config has to agree.

---

## Presets

Three hard-coded baud rates. Everything else about the modem (tone stack,
RS block layout, retry count) is tuned per baud — override individual
knobs if you know what you're doing.

| Baud | CLI (both tx / rx) | 4-FSK tones (Hz) | Total spread | Fits 2.7 kHz SSB? | Default repeats | Measured best SNR (3 kHz ref) | Min live tx (13 B payload) |
|---:|---|---|---:|---|---:|---:|---:|
| 45 | `--modem-baud 45` | 1200 / 1400 / 1600 / 1800 | 600 Hz | ✓ | 4× | ≈ −14 dB | 28 s |
| 300 | `--modem-baud 300` | 1050 / 1350 / 1650 / 1950 | 900 Hz | ✓ | 2× | ≈ −5 dB | 2.4 s |
| 1200 | `--modem-baud 1200` | 500 / 1700 / 2900 / 4100 | 3600 Hz | ✗ (wideband) | 2× | ≈ +2 dB | 1.0 s |

Every preset carries a 13-byte payload per RS block (RS(16,8) + CRC-32).
Message sizes below map identically across bauds.

Rule of thumb:

- **1200** — clean local audio, wired or same-room. ~500 bit/s.
- **300** — SSB radio in fair conditions. ~130 bit/s.
- **45** — weak signal, deep noise, willing to wait. ~10 bit/s.

Override `--modem-block-repeats N` on both sides for more (or fewer)
copies. Each doubling buys ~2–3 dB more AWGN margin via soft-LLR
combining, at proportionally longer transmission.

---

## Debugging live audio

Local WAV roundtrip works but mic-and-speaker doesn't decode? Add
`--modem-debug` to the RX side:

```bash
weaklink-9a3ice rx --modem-debug > out.txt
```

Diagnostics go to `log.txt` (stdout stays clean for piping). What to look
for:

- `audio: peak +X dBFS, rms +Y dBFS` — one per second while RX runs.
  Peak below −40 dBFS = mic muted, wrong device, or gain too low.
- `RS corrected ... byte-symbol(s)` — outer code saved a block.
- `N slot(s) failed CRC/RS` — unrecoverable slots (data loss).
- With `--modem-debug`: coarse and per-preamble frequency offsets,
  preamble positions, block-decode counts per group.

Common gotchas the debug log catches:

- **Wrong mic** — peak level at −∞ dBFS or no preambles found.
- **macOS built-in mic AGC / voice isolation** — butchers modem tones.
  Disable in *System Settings → Sound → Input* or pick a different mic.
- **Sample-rate mismatch** — correlator finds peaks at odd positions.
  Force the input device to 48 kHz.
- **Volume too low** — peak below −40 dBFS; turn mic gain or TX volume up.

---

## SNR benchmarks

Auto-generated. `poetry run weaklink-benchmark` re-measures every config
and rewrites the tables between the markers.

<!-- BENCHMARK RESULTS START -->

Streaming modem. Payload: 100 random-ASCII bytes. Sync every 4 data blocks. Reference bandwidth: 3 kHz.

| Baud | CLI (both tx / rx) | Throughput | Info rate | Measured best SNR |
|---:|---|---|---:|---:|
| 45 | `--modem-baud 45`<br/>`--modem-rs-data-bytes 16`<br/>`--modem-rs-parity-bytes 8`<br/>`--modem-block-repeats 4` | *pending refresh* | | |
| 300 | `--modem-baud 300`<br/>`--modem-rs-data-bytes 16`<br/>`--modem-rs-parity-bytes 8`<br/>`--modem-block-repeats 2` | *pending refresh* | | |
| 1200 | `--modem-baud 1200`<br/>`--modem-rs-data-bytes 16`<br/>`--modem-rs-parity-bytes 8`<br/>`--modem-block-repeats 2` | *pending refresh* | | |

*Full sweep across baud × RS layout × repeats gets regenerated by the
benchmark. Preset rows above are the ones the CLI defaults to.*

<!-- BENCHMARK RESULTS END -->

---

## CLI reference

Everything modem-related is prefixed `--modem-*`. Two subcommands: `tx`,
`rx`. Byte data flows over stdin / stdout.

| Flag | Default | Description |
|------|---------|-------------|
| `--modem-baud N` | `300` | Symbol rate. Only `45`, `300`, `1200` supported. |
| `--modem-sample-rate HZ` | `48000` | Audio sample rate; match your soundcard. |
| `--modem-rs-data-bytes N` | preset | Reed-Solomon data bytes per block. |
| `--modem-rs-parity-bytes N` | preset | RS parity bytes. Corrects up to N/2 byte errors per block. |
| `--modem-no-rs-crc` | CRC on | Skip the CRC-32 inside each RS block. |
| `--modem-block-repeats N` | preset | N copies per block, each permuted differently; RX soft-combines LLRs across copies for diversity gain. |
| `--modem-wav PATH` | live | WAV file instead of live audio. |
| `--modem-audio-output NAME` | OS default | tx audio target: sounddevice index, name substring, or Pulse sink name. |
| `--modem-audio-input NAME` | OS default | rx audio source: same syntax; Pulse sources like `virt.monitor` supported. |
| `--modem-debug` | off | Verbose DEBUG chatter in the log file. |
| `--modem-log-file PATH` | `./log.txt` | Diagnostics land here; stdout / stderr stay clean. |

---

## How it works

### Signal chain

```
stdin ──▶ chunk into (rs_data − 3)-byte payloads ──▶ frame per block ──▶
     RS(N,K) + CRC-32 ──▶ conv encode (K=7, r=1/2) ──▶ per-block interleave
     ──▶ 4-FSK CPFSK ──▶ [pre][slot 0][pre][slot 1]...[pre] ──▶ audio
                                                                    │
                                                                    ▼
stdout ◀── emit in block_index order ◀── strip zero-pad via length header
       ◀── RS + CRC per slot ◀── soft Viterbi ◀── deinterleave
       ◀── preamble correlator (per-slot sync) ◀── non-coherent demod ◀──
```

Every slot is bracketed by a preamble, so any single slot decodes
standalone. The correlator finds each preamble independently; spurious
mid-stream peaks get dropped. Message boundaries between separate tx
sessions are inferred from non-block-length spans between preambles —
one rx pipe can watch many tx sessions in a row.

### Wire format

```
One tx session (live audio):

  ┌────────┬─────┬───────┬─────┬───────┬─────┬─────┬───────┬─────┬────────┐
  │ pilot  │ pre │slot 0 │ pre │slot 1 │ pre │ ... │slot N-1│ pre │ pilot  │
  └────────┴─────┴───────┴─────┴───────┴─────┴─────┴───────┴─────┴────────┘
    ~0.2 s   32     ~L      32     ~L                                ~0.2 s
     4-FSK  syms  syms    syms   syms                                4-FSK
     tones                                                           tones

  L (block_length) depends on the preset: RS wire bytes → conv → interleave.
  With block_repeats > 1, each slot is emitted back-to-back R times.

One RS block, data area (before conv + interleave + FSK):

  ┌── 1B ──┬── 2B ────┬──── rs_data − 3 B ────┬── 4B CRC ──┬── rs_parity B ──┐
  │ length │block_idx │ payload (zero-padded) │  CRC-32    │  RS parity      │
  └────────┴──────────┴───────────────────────┴────────────┴─────────────────┘
    strips    dedupes    user bytes; last          integrity   corrects up to
    trailing  copies /   block in a stream         check       parity/2 byte
    NUL pad   picks      is short — length            ↑         errors per
              output     header records how                     block
              slot       many bytes are real
```

Cap: `block_idx` is 2 bytes, so one tx session is bounded at 65 535 slots
(~850 KB at 45 baud). In practice air time is the ceiling, not the header.

### Feature highlights

- **Reed-Solomon RS(N,K)** — corrects up to `(N-K)/2` byte errors per block.
- **CRC-32 inside every RS block** — catches errors past RS correction.
- **Rate-1/2 K=7 convolutional inner code + soft Viterbi** — ~5 dB coding gain.
- **Per-block pseudorandom interleaver** — 32-cycle shuffle beats bursts *and*
  periodic noise (SMPS harmonics, mains hum, alternator whine).
- **Amplitude-normalised preamble correlator** — fade-invariant, still locks
  below −20 dB SNR against a strong-enough signal.
- **Per-preamble fine frequency tracking** — follows LO drift slot-by-slot.
- **Soft-LLR combining across `--modem-block-repeats` copies** — each copy
  uses a distinct permutation, RX sums LLRs across them for real diversity.
- **Cross-call block-dedup** — copies straddling two RX polls don't emit twice.
- **Head/tail chop recovery** — virtual preamble projection + zero-pad.
- **Live audio via PulseAudio / PipeWire (`paplay`/`parec` subprocess)** or
  sounddevice / PortAudio. Explicit `--modem-audio-input` / `--modem-audio-output`.

---

## Testing

```bash
poetry install
poetry run pytest -q            # unit + e2e streaming, ~2 min
poetry run pytest -m slow -v -s # optional SNR sweeps
```

Every batch-decode test has a companion e2e-streaming variant that drives
the same audio through `_StreamingRxPump` (the class the CLI uses for both
live audio and WAV rx). CI runs the full suite on every push.

---

## Glossary

Reference for terms used above.

- **Baud** — Symbols per second. Higher baud = more bits/s but needs more SNR.
- **4-FSK** — Modulation using four distinct tones; each tone carries 2 bits.
- **CPFSK** — Continuous-Phase FSK. Phase doesn't jump between tones.
- **Preamble** — Known 32-symbol pattern bracketing every slot. RX uses it to lock timing, frequency, amplitude.
- **PN sequence** — Pseudo-random pattern that looks like noise but is deterministic. Our preamble is a 32-symbol PN.
- **Slot** — One wire unit: preamble + one RS-encoded block. Every slot decodes standalone.
- **Block** — Fixed-size RS-encoded chunk inside a slot.
- **Reed-Solomon (RS)** — Outer error-correcting code. RS(N,K): K data + parity → N wire bytes; corrects up to (N-K)/2 byte errors.
- **CRC-32** — 32-bit checksum inside every block. Catches errors past RS.
- **Convolutional code (K=7, rate-1/2)** — Inner code, doubles the bit rate but sharply improves error resilience.
- **Viterbi decoder** — Standard decoder for the convolutional code. Soft Viterbi uses per-bit confidence.
- **Soft bits / LLR** — Log-Likelihood Ratio. Per-bit confidence fed to the decoder.
- **Interleaver** — Deterministically shuffles bits so bursts become isolated errors.
- **Per-block interleaver** — Shuffle changes every block (32-permutation cycle).
- **Non-coherent demod** — Detects tones by energy alone. ~3 dB behind coherent.
- **LO offset** — Local-oscillator frequency error. We estimate and correct up to ±500 Hz.
- **SNR (dB)** — Signal-to-noise ratio, measured in a 3 kHz reference bandwidth.
- **Shannon limit** — Theoretical lowest SNR at which a given data rate is possible.
- **Pilot** — Short random 4-FSK burst before / after every live TX. Wakes idle sinks, gives the RX FFT tone energy.

---

## Roadmap / known limits

- **Non-coherent detection only.** Coherent Costas-loop demod would buy
  another ~3 dB. Big DSP lift.
- **No LDPC.** Would close ~2–4 dB of the Shannon gap. Was drafted then
  removed as experimental; needs a proper girth-optimising construction.

---

## License

MIT. See `LICENSE`.

Contributions welcome — open an issue first for anything non-trivial so we
can agree on shape before code lands.

## Acknowledgments

Reed-Solomon via [`reedsolo`](https://github.com/tomerfiliba-org/reedsolomon).
Convolutional code uses the standard NASA/CCSDS (171, 133) generator
polynomials. Audio via [`sounddevice`](https://github.com/spatialaudio/python-sounddevice)
and [`soundfile`](https://github.com/bastibe/python-soundfile).
