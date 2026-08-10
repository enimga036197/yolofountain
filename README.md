# YoloFountain

**A tiny one-way fountain codec — spray a file over any *write-only* channel, and
catch it with no back-channel.** You only send once; there's no asking again.

The sender emits an endless stream of droplet frames; a receiver reconstructs the
file once it has caught enough of them. Frames can be **lost, reordered, duplicated,
or joined late** and the transfer still completes. There is **no handshake, no ACK,
no retransmit request** — the receiver never has to talk back.

```python
import yolofountain

# sender — just keep spraying; the receiver takes it from here
tx = yolofountain.Sender(open("photo.jpg", "rb").read(), block_size=1024, password="s3cret")
for i in range(tx.n_blocks * 2):
    channel.send(tx.frame(i))            # tx.frame(i) -> bytes

# receiver — somewhere else, with no way to reply
rx = yolofountain.Receiver()
for frame in channel:                    # whatever arrives, any order
    if rx.add(frame):                    # True once complete
        break
data = rx.result(password="s3cret")      # -> the original bytes
```

## Why this exists

Almost every file-transfer protocol assumes a **two-way** link — TCP, HTTP,
Bluetooth, QUIC, most UDP schemes all need ACKs or NAKs. But a whole class of real
channels is **write-only**: you can transmit, and the far side simply cannot answer.

- broadcast / multicast to many receivers at once
- **data diodes** and air-gapped one-way links (security)
- **optical** — a screen animating codes into a phone camera
- **acoustic** / ultrasonic, LED, LoRa beacons
- **magnetic / EM side-channels**
- satellite and deep-space downlink

Over a one-way lossy channel you can't retransmit, so you use a **rateless erasure
code**: the sender keeps emitting fresh *droplets*, each an XOR of a random subset of
the file's blocks, and the receiver solves the file as soon as it has enough distinct
ones — typically **1.0–1.4×** the file's worth. YoloFountain is a small, dependency-free,
carrier-agnostic implementation of exactly that, distilled from the
[qrbeam](https://github.com/enimga036197/qrbeam) optical file-transfer app and proven
on a second, wildly different carrier (a magnetic side-channel).

## What makes it a good *basic* codec

- **Nothing on the wire but the frame index.** Which blocks a droplet contains is
  derived deterministically from its index — no seeds, no coding tables transmitted.
  The sender is stateless; any receiver reproduces the selection.
- **Systematic prefix.** The first `N` frames are the raw blocks, so a clean channel
  decodes at *exactly 1.0×* — no fountain tax when you don't need it — and repair
  droplets only kick in past that. Efficient *and* robust, without choosing upfront.
- **Self-describing frames.** Any frame's header carries everything a receiver needs
  (`total_len`, `K`) to bootstrap and start solving — **late-join is free.**
- **Carrier-agnostic.** The core speaks bytes and knows nothing about any medium.
  A "carrier" is a two-function seam; swap it to target QR, audio, a magnetic field…
- **Integrity built in.** A per-frame CRC-32 rejects corrupt droplets before they can
  poison the reconstruction (qrbeam leaned on QR's Reed–Solomon; yolofountain stands alone).
- **Optional password encryption.** Authenticated ChaCha20-Poly1305 with an scrypt
  KDF, applied *before* the fountain — so merely seeing or holding the stream gets an
  eavesdropper nothing.

## Text channels (base45 / base64)

Many channels only ever return **text, never raw bytes** — QR via the native camera
decoder, clipboards, terminals, chat, text APIs. A *text carrier* lets yolofountain ride all
of them:

```python
from yolofountain.carriers import base45_encode, base45_decode
text  = base45_encode(tx.frame(i))    # send this string over a text-only channel
frame = base45_decode(text)           # rebuild the exact frame on the far side
```

**base45** is the sharp one: QR's *alphanumeric* mode encodes exactly a 45-character
set, and it's the densest QR mode that survives a text-only decoder — so a base45
frame round-trips through any QR reader that returns a string, at ~3% cost vs raw
bytes. **base64** is included for non-QR text channels (denser, universal).

## Install

```bash
pip install yolofountain           # core, zero dependencies
pip install yolofountain[crypto]   # + password encryption (needs 'cryptography')
```

Or just vendor the `yolofountain/` folder — the core has no third-party dependencies.

## API

| | |
|---|---|
| `yolofountain.Sender(data, block_size=1024, password=None, compress=True)` | wrap bytes as an endless frame stream |
| `yolofountain.Sender.from_files([(name, bytes, mime?), …], …)` | multi-file transfer |
| `sender.frame(i)` / `sender.spray(count=None)` | the i-th droplet / a generator of droplets |
| `yolofountain.Receiver()` | collects frames |
| `receiver.add(frame) -> bool` | feed a frame; True once complete |
| `receiver.result(password=None) -> bytes` | the reconstructed payload |
| `receiver.files(password=None) -> [ {name, mime, bytes}, … ]` | as a container |

Low-level `Encoder` / `Decoder`, the container, and the carriers are exported too.

## Wire format

Specified in **[SPEC.md](SPEC.md)** — frame layout, the deterministic droplet
selection, the robust-soliton parameters, session semantics, and the container /
encryption envelopes. Independent implementations interoperate.

## Tests

```bash
python tests/test_codec.py       # fountain: loss, late-join, corruption, container, gzip
python tests/test_crypto.py      # password round-trip, wrong-password fails, stream reveals nothing
python tests/test_carriers.py    # base45 / base64 round-trip; a frame through a text hop
```

## Real-world carriers

- **[qrbeam](https://github.com/enimga036197/qrbeam)** — animated QR on a screen →
  phone camera. The optical carrier this core was distilled from.
- **magbeam** — modulating a CPU's magnetic field to a phone magnetometer. The proof
  that the core is genuinely carrier-agnostic.

## Optional SIMD C core (~70–85× faster)

The codec's bulk work is XOR of aligned blocks — frame assembly on encode, peeling
on decode — a memory-bandwidth-bound SoA workload. A tiny AVX2 C core (runtime-
dispatched, SSE2 fallback, `yolofountain/native/yolo_core.c`) handles it; the
pure-Python path stays as a bit-for-bit-identical fallback (a test asserts they
agree, and nothing on the wire changes).

```bash
python build.py                 # -> yolofountain/_yolocore.{dll,so,dylib}  (MSVC or gcc/clang)
python examples/benchmark.py
```

Measured — 4 MB payload, K=4096, 40 % loss (repair-heavy), i3-12100F / AVX2:

| path | pure-Python | SIMD C core | speedup |
|---|---|---|---|
| encode | 3.1 MB/s | **263 MB/s** | 85× |
| decode (peeling) | 2.2 MB/s | **154 MB/s** | 70× |

It lands at a few hundred MB/s rather than the XOR's raw GB/s because the per-frame
*Python* scaffolding (block-selection PRNG, CRC-32, struct packing) is now the
ceiling, not the XOR — porting block selection to C is the next lever. The core is a
pure accelerator: build it or don't, results are identical.

## License

MIT.
