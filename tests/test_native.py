"""The SIMD C core must be bit-identical to the pure-Python path. Skips if unbuilt."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yolofountain
from yolofountain import codec
from yolofountain._native import AVAILABLE


def _encode(data, K, native, n):
    codec._NATIVE = native and AVAILABLE
    enc = codec.Encoder(data, K, session=5)
    out = [enc.frame(i) for i in range(n)]
    codec._NATIVE = AVAILABLE
    return out


def test_native_encode_identical():
    if not AVAILABLE:
        print("native not built — skipping"); return
    for K in (8, 63, 64, 100, 512, 4096):          # around the size threshold too
        data = os.urandom(K * 9 + 3)
        py = _encode(data, K, False, 40)
        na = _encode(data, K, True, 40)
        assert py == na, f"native != python encode at K={K}"


def _decode(data, frames, native):
    codec._NATIVE = native and AVAILABLE
    dec = codec.Decoder(1, len(data), frames_K(frames))
    for f in frames:
        h = codec.unpack_frame(f)
        dec.ingest(h["frame_index"], bytearray(h["body"]))
        if dec.is_complete():
            break
    codec._NATIVE = AVAILABLE
    return dec.payload() if dec.is_complete() else None


def frames_K(frames):
    return codec.unpack_frame(frames[0])["K"]


def test_native_decode_identical():
    if not AVAILABLE:
        print("native not built — skipping"); return
    data = os.urandom(4000)
    K = 128
    codec._NATIVE = AVAILABLE
    enc = codec.Encoder(data, K, session=1)
    rng = random.Random(11)
    # spray a lossy stream until a probe decoder actually completes -> guaranteed decodable
    probe = codec.Decoder(1, len(data), K)
    frames = []
    i = 0
    while not probe.is_complete() and i < enc.N * 8:
        f = enc.frame(i); i += 1
        if rng.random() < 0.35:
            continue
        frames.append(f)
        h = codec.unpack_frame(f)
        probe.ingest(h["frame_index"], bytearray(h["body"]))
    py = _decode(data, frames, False)
    na = _decode(data, frames, True)
    assert py == data and na == data, "decode mismatch native vs python"


if __name__ == "__main__":
    test_native_encode_identical()
    test_native_decode_identical()
    print("native: ALL PASS" if AVAILABLE else "native: not built (skipped)")
