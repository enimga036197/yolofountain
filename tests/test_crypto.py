"""Password encryption: round-trip, wrong password fails, stream reveals nothing."""
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yolofountain
from yolofountain.crypto import YoloCryptoError


def _deliver(tx, rng=None, loss=0.0):
    rx = yolofountain.Receiver()
    i = 0
    while i < 20000:
        f = tx.frame(i); i += 1
        if rng and rng.random() < loss:
            continue
        if rx.add(f):
            return rx
    return rx


def test_password_roundtrip():
    payload = os.urandom(4000)
    tx = yolofountain.Sender(payload, block_size=200, password="correct horse")
    rx = _deliver(tx)
    assert rx.done
    assert rx.result(password="correct horse") == payload


def test_wrong_password_fails_cleanly():
    tx = yolofountain.Sender(b"top secret" * 100, block_size=64, password="right")
    rx = _deliver(tx)
    try:
        rx.result(password="wrong")
        assert False, "wrong password should raise"
    except YoloCryptoError:
        pass


def test_missing_password_raises():
    tx = yolofountain.Sender(b"secret" * 100, block_size=64, password="pw")
    rx = _deliver(tx)
    try:
        rx.result()
        assert False, "encrypted payload without password should raise"
    except ValueError:
        pass


def test_stream_bytes_do_not_contain_plaintext():
    secret = b"THE-EAGLE-LANDS-AT-DAWN"
    payload = secret * 40
    tx = yolofountain.Sender(payload, block_size=64, password="pw", compress=False)
    # concatenate a pile of frame bodies; the plaintext must not appear anywhere
    blob = b"".join(tx.frame(i) for i in range(tx.n_blocks * 3))
    assert secret not in blob


def test_encryption_survives_loss():
    payload = os.urandom(6000)
    tx = yolofountain.Sender(payload, block_size=128, password="pw")
    rx = _deliver(tx, rng=random.Random(3), loss=0.35)
    assert rx.done
    assert rx.result(password="pw") == payload


if __name__ == "__main__":
    test_password_roundtrip(); test_wrong_password_fails_cleanly()
    test_missing_password_raises(); test_stream_bytes_do_not_contain_plaintext()
    test_encryption_survives_loss()
    print("crypto: ALL PASS")
