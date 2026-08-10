"""
Optical carrier demo — YoloFountain over animated QR (the qrbeam path, in Python).

Turns any data into a stream of QR codes (frame -> base45 -> QR), saves it as an
animated GIF you can play on any screen, then reads it back with a camera-grade QR
decoder and reconstructs the data byte-exact — even with a chunk of the frames
dropped, because the fountain doesn't care *which* ones a camera happens to miss.

This is the same base45->QR carrier the browser app **qrbeam** uses
(https://github.com/enimga036197/qrbeam) — qrbeam is the full, polished, camera-in-
the-browser version people actually use; this is the minimal self-contained proof
that the codec drives the optical channel.

    pip install segno opencv-python pillow
    python examples/qr_optical.py                 # demo text -> beam.gif -> decode
    python examples/qr_optical.py somefile.pdf     # any file
"""
import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yolofountain
from yolofountain.carriers import base45_encode, base45_decode

try:
    import segno
    import numpy as np
    import cv2
    from PIL import Image
except ImportError as e:
    sys.exit(f"needs QR tooling: pip install segno opencv-python pillow  ({e})")

SCALE = 6
BORDER = 4
GIF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "beam.gif")


def frame_to_qr(frame):
    """One YoloFountain frame -> a QR image (via base45, QR's native alphanumeric set)."""
    qr = segno.make(base45_encode(frame), error="l")   # low EC; the fountain handles losses
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=SCALE, border=BORDER)
    return Image.open(buf).convert("RGB")


def build_beam(data, K=180, overhead=1.6):
    tx = yolofountain.Sender(data, block_size=K, compress=True)
    n = max(tx.n_blocks + 2, int(tx.n_blocks * overhead) + 1)
    imgs = [frame_to_qr(tx.frame(i)) for i in range(n)]
    imgs[0].save(GIF, save_all=True, append_images=imgs[1:], duration=120, loop=0)
    return tx.n_blocks, n


def receive_beam(drop=0.0, seed=1):
    """Read frames back out of the GIF with an actual QR decoder, dropping `drop` of
    them to mimic a camera missing frames. Returns the reconstructed bytes."""
    rng = random.Random(seed)
    det = cv2.QRCodeDetector()
    rx = yolofountain.Receiver()
    gif = Image.open(GIF)
    seen = fed = 0
    i = 0
    while True:
        try:
            gif.seek(i)
        except EOFError:
            i = 0
            continue                       # loop the GIF, like a camera watching a repeating stream
        i += 1
        seen += 1
        if rng.random() < drop:
            continue                       # camera missed this frame
        text, _, _ = det.detectAndDecode(np.array(gif.convert("RGB")))
        if not text:
            continue
        frame = base45_decode(text)
        if frame:
            fed += 1                        # a QR frame the "camera" caught and decoded
            rx.add(frame)
        if rx.done:
            break
        if seen > 500:                     # safety bound
            break
    return rx.result(), fed


def main():
    if len(sys.argv) > 1:
        data = open(sys.argv[1], "rb").read()
        label = os.path.basename(sys.argv[1])
    else:
        # ~1.4 KB of distinct text so it spans several QR frames (fountain earns its keep)
        data = bytes(
            "YoloFountain over QR, self-contained. A sender animates a stream of QR "
            "codes on any screen; a phone camera watches them; the file reassembles "
            "byte-exact with no pairing, no network, no app install, and no shared "
            "anything. Because the transport is a rateless fountain, the camera can "
            "miss codes, blur them, or catch duplicates and the transfer still "
            "completes -- it just needs enough distinct droplets, not any particular "
            "one. That is why an optical link this lossy is even viable: loss stops "
            "being a failure and becomes ordinary. The full browser version, qrbeam, "
            "does the camera decode live and is the thing people actually reach for; "
            "this script is the minimal proof that the codec drives the channel. "
            "Point a QR-aware receiver at beam.gif and watch it fill in. " * 2, "utf-8")
        label = "demo text"

    n_blocks, n_frames = build_beam(data, K=120)
    print(f"sent {label}: {len(data)} bytes -> {n_blocks} blocks -> {n_frames} QR frames")
    print(f"wrote {GIF} (play it on a screen; qrbeam's rx-style camera reads it)")

    for drop in (0.0, 0.3):
        result, fed = receive_beam(drop=drop)
        ok = result == data
        print(f"  decoded through {int(drop*100):2d}% frame loss: caught {fed} QR frames "
              f"-> reconstructed {len(result) if ok else 0} bytes, byte-exact: {ok}")
        if not ok:
            return 1
    print("optical round-trip OK -- the same base45->QR path qrbeam uses in the browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
