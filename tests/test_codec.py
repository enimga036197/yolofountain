"""Fountain codec: erasures, late-join, duplicates, bit-corruption, container, gzip."""
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yolofountain
from yolofountain.codec import Encoder, Decoder, unpack_frame


def _run(payload_len, K, loss, late_join, seed, corrupt=0.0, mult=8):
    rng = random.Random(seed)
    payload = bytes(rng.randrange(256) for _ in range(payload_len))
    enc = Encoder(payload, K, session=seed & 0xFFFF)
    dec = None
    N = enc.N
    started = False
    delivered = 0
    for i in range(int(N * mult) + 60):
        raw = bytearray(enc.frame(i))
        if not started and i < late_join * N:
            continue
        started = True
        if rng.random() < loss:
            continue
        if corrupt and rng.random() < corrupt:
            bit = rng.randrange(len(raw) * 8)
            raw[bit // 8] ^= 1 << (bit % 8)
        hdr = unpack_frame(bytes(raw))
        if not hdr:                       # CRC rejected a corrupt frame
            continue
        if dec is None:
            dec = Decoder(hdr["session"], hdr["total_len"], hdr["K"], hdr["flags"])
        dec.ingest(hdr["frame_index"], bytearray(hdr["body"]))
        delivered += 1
        if dec.is_complete():
            break
    ok = dec is not None and dec.is_complete() and dec.payload() == payload
    return ok, N, delivered / N if N else 0


def test_matrix():
    cases = [(1, 8, 0, 0), (7, 8, 0, 0), (200, 16, 0, 0),
             (200, 16, 0.3, 0), (500, 24, 0.2, 0.3), (1024, 32, 0.4, 0.5),
             (4096, 64, 0.3, 0.2), (10000, 64, 0.25, 0.4)]
    for plen, K, loss, join in cases:
        for seed in range(5):
            ok, N, ovhd = _run(plen, K, loss, join, seed)
            assert ok, f"FAIL plen={plen} K={K} loss={loss} join={join} seed={seed}"


def test_bit_corruption_rejected():
    # CRC must reject corrupted frames; transfer still completes from clean ones
    ok, N, ovhd = _run(500, 24, loss=0.1, late_join=0.0, seed=7, corrupt=0.3, mult=15)
    assert ok


def test_container_and_gzip():
    files = [("hello.txt", b"hello yolofountain " * 50, "text/plain"),
             ("data.bin", bytes(range(256)) * 4)]
    tx = yolofountain.Sender.from_files(files, block_size=64)
    rx = yolofountain.Receiver()
    i = 0
    while not rx.add(tx.frame(i)) and i < 10000:
        i += 1
    got = rx.files()
    assert len(got) == 2
    assert got[0]["name"] == "hello.txt" and got[0]["bytes"] == files[0][1]
    assert got[1]["bytes"] == files[1][1]


def test_high_level_roundtrip():
    payload = os.urandom(5000)
    tx = yolofountain.Sender(payload, block_size=200, compress=False)
    rx = yolofountain.Receiver()
    i = 0
    while not rx.add(tx.frame(i)) and i < 10000:
        i += 1
    assert rx.result() == payload


if __name__ == "__main__":
    test_matrix(); test_bit_corruption_rejected()
    test_container_and_gzip(); test_high_level_roundtrip()
    print("codec: ALL PASS")
