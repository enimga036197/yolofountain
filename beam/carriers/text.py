"""
beam.carriers.text — turn a byte frame into TEXT and back.

The core codec is byte-oriented. But a large class of channels only ever hand you
back *text, never raw bytes* — QR codes read through the native BarcodeDetector,
copy-paste, terminals, chat, text-only APIs. A text carrier lets beam ride all of
them.

**base45** is the one that makes screen->camera QR transfer fast, and it is worth
understanding: QR's "alphanumeric" mode encodes exactly a 45-character set, and it
is *the densest QR mode that survives a text-only decoder*. So a base45 frame
round-trips through any QR reader that returns a string — no raw-byte access
needed — at a cost of only ~3% capacity vs raw bytes. That single alignment
(bytes <-> the exact 45 chars QR already speaks) is what makes the optical link
practical.

**base64** is here too: denser (6 bits/char) and universal for text channels that
are *not* QR-constrained (logs, JSON, clipboards).

A "carrier" is just a pair of pure functions frame_bytes <-> text. Swap this module
for a modem (audio, magnetic, ...) to target a non-text channel; the core does not
change.
"""
import base64 as _b64

# QR alphanumeric set == the base45 alphabet.
_B45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
_B45I = {c: i for i, c in enumerate(_B45)}


def base45_encode(data):
    """bytes -> base45 text (2 bytes -> 3 chars; odd trailing byte -> 2 chars)."""
    out = []
    n = len(data)
    i = 0
    while i + 1 < n:
        v = data[i] * 256 + data[i + 1]
        out.append(_B45[v % 45]); v //= 45
        out.append(_B45[v % 45]); v //= 45
        out.append(_B45[v])
        i += 2
    if i < n:
        v = data[i]
        out.append(_B45[v % 45])
        out.append(_B45[v // 45])
    return "".join(out)


def base45_decode(s):
    """base45 text -> bytes, or None if the text isn't valid base45 (which is also
    how a receiver silently ignores a foreign QR / stray text)."""
    n = len(s)
    rem = n % 3
    if rem == 1:
        return None
    out = bytearray()
    i = 0
    for _ in range((n - rem) // 3):
        try:
            a, b, c = _B45I[s[i]], _B45I[s[i + 1]], _B45I[s[i + 2]]
        except KeyError:
            return None
        v = a + b * 45 + c * 2025
        if v > 65535:
            return None
        out.append(v >> 8)
        out.append(v & 0xFF)
        i += 3
    if rem == 2:
        try:
            a, b = _B45I[s[i]], _B45I[s[i + 1]]
        except KeyError:
            return None
        v = a + b * 45
        if v > 255:
            return None
        out.append(v)
    return bytes(out)


def base64_encode(data):
    """bytes -> URL-safe base64 text (no padding)."""
    return _b64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def base64_decode(s):
    try:
        pad = "=" * (-len(s) % 4)
        return _b64.urlsafe_b64decode(s + pad)
    except Exception:
        return None
