"""
Throughput: pure-Python vs the SIMD C core, encode and decode.

    python build.py            # build the core first (optional)
    python examples/benchmark.py
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yolofountain
from yolofountain import codec
from yolofountain._native import AVAILABLE, HAS_AVX2

PAYLOAD = 4 * 1024 * 1024
K = 4096
LOSS = 0.40          # forces repair droplets -> peeling -> the XOR hot path in decode


def build_frames(data):
    """Encode a lossy frame set once (native on, fast) so both decode runs see the
    same repair-heavy input."""
    codec._NATIVE = AVAILABLE
    enc = codec.Encoder(data, K, session=1)
    rng = random.Random(7)
    frames = []
    i = 0
    # spray until a fresh decoder would complete through LOSS, then keep that set
    probe = codec.Decoder(1, len(data), K)
    while not probe.is_complete() and i < enc.N * 6:
        f = enc.frame(i); i += 1
        if rng.random() < LOSS:
            continue
        frames.append(f)
        h = codec.unpack_frame(f)
        probe.ingest(h["frame_index"], bytearray(h["body"]))
    return frames


def enc_speed(data, native):
    codec._NATIVE = native and AVAILABLE
    enc = codec.Encoder(data, K, session=1)
    n = int(enc.N * 1.4)
    t0 = time.perf_counter()
    for i in range(n):
        enc.frame(i)
    return PAYLOAD / 1e6 / (time.perf_counter() - t0)


def dec_speed(data, frames, native):
    codec._NATIVE = native and AVAILABLE
    dec = codec.Decoder(1, len(data), K)
    t0 = time.perf_counter()
    for f in frames:
        h = codec.unpack_frame(f)
        dec.ingest(h["frame_index"], bytearray(h["body"]))
        if dec.is_complete():
            break
    dt = time.perf_counter() - t0
    assert dec.payload() == data, "decode mismatch!"
    return PAYLOAD / 1e6 / dt


def main():
    print(f"payload {PAYLOAD//1024//1024} MB, K={K}, N={PAYLOAD//K} blocks, {int(LOSS*100)}% loss "
          f"(repair-heavy decode)\nnative="
          f"{'yes (AVX2)' if AVAILABLE and HAS_AVX2 else ('yes' if AVAILABLE else 'NOT BUILT')}\n")
    data = os.urandom(PAYLOAD)
    frames = build_frames(data)

    pe = enc_speed(data, False)
    pd = dec_speed(data, frames, False)
    print(f"  {'pure-Python':16s} encode {pe:8.1f} MB/s   decode {pd:8.1f} MB/s")
    if AVAILABLE:
        ne = enc_speed(data, True)
        nd = dec_speed(data, frames, True)
        print(f"  {'SIMD C core':16s} encode {ne:8.1f} MB/s   decode {nd:8.1f} MB/s")
        print(f"\n  speedup: encode {ne/pe:.0f}x   decode {nd/pd:.0f}x")
    else:
        print("\n  (build the core with `python build.py` to see the C path)")
    codec._NATIVE = AVAILABLE


if __name__ == "__main__":
    main()
