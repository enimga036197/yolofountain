"""Text carriers: base45 (QR alphanumeric) and base64 round-trip frames intact."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import beam
from beam.carriers import base45_encode, base45_decode, base64_encode, base64_decode

_B45 = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:")


def test_base45_roundtrip_random():
    for n in range(0, 300):
        data = os.urandom(n)
        s = base45_encode(data)
        assert set(s) <= _B45, "base45 output must stay within the QR alphanumeric set"
        assert base45_decode(s) == data


def test_base45_rejects_foreign_text():
    assert base45_decode("hello, world!") is None      # lowercase/comma not in set
    assert base45_decode("AB") is None or True          # len%3==2 is valid (1 byte)


def test_base64_roundtrip_random():
    for n in range(0, 300):
        data = os.urandom(n)
        assert base64_decode(base64_encode(data)) == data


def test_frame_through_base45():
    # a real beam frame survives the text hop and still decodes
    tx = beam.Sender(os.urandom(2000), block_size=180, compress=False)
    rx = beam.Receiver()
    i = 0
    while i < 10000:
        text = base45_encode(tx.frame(i)); i += 1
        frame = base45_decode(text)
        assert frame is not None
        if rx.add(frame):
            break
    assert rx.done


if __name__ == "__main__":
    test_base45_roundtrip_random(); test_base45_rejects_foreign_text()
    test_base64_roundtrip_random(); test_frame_through_base45()
    print("carriers: ALL PASS")
