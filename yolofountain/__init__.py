"""
yolofountain — a tiny one-way fountain codec.

Spray a file over any *write-only* channel; catch it with no back-channel. The
sender emits droplet frames forever; a receiver reconstructs the payload once it
has caught enough of them — frames may be lost, reordered, duplicated, or joined
late. No handshake, no ACK, no retransmit. Optional gzip and password encryption.

Quickstart::

    import yolofountain

    # --- sender ---
    tx = yolofountain.Sender(b"hello world" * 1000, block_size=512, password="s3cret")
    for i in range(tx.n_blocks * 2):        # just keep spraying; more = more robust
        channel.send(tx.frame(i))           # tx.frame(i) -> bytes

    # --- receiver (elsewhere, no reply path needed) ---
    rx = yolofountain.Receiver()
    for frame in channel:                   # whatever arrives, in any order
        if rx.add(frame):                   # True once the payload is complete
            break
    data = rx.result(password="s3cret")     # -> original bytes

Multiple files::

    tx = yolofountain.Sender.from_files([("a.txt", a), ("b.png", b)])
    files = rx.files()                      # -> [{'name','mime','bytes'}, ...]

Text channels (QR, clipboards, terminals) via a carrier::

    from yolofountain.carriers import base45_encode, base45_decode
    text = base45_encode(tx.frame(i))       # send text ...
    frame = base45_decode(text)             # ... rebuild the frame

The wire format is specified in SPEC.md; independent implementations interoperate.
"""
from .codec import (Encoder, Decoder, pack_frame, unpack_frame,
                    build_container, parse_container, gzip_bytes, gunzip_bytes,
                    blocks_for_frame, soliton_cdf, mulberry32,
                    FLAG_GZIP, FLAG_ENC, HDR, VERSION)

__version__ = "0.1.0"
__all__ = ["Sender", "Receiver", "Encoder", "Decoder", "FLAG_GZIP", "FLAG_ENC"]

_DEFAULT_BLOCK = 1024


def _rand_session():
    import os
    return int.from_bytes(os.urandom(2), "little")


class Sender:
    """Wraps a payload as an endless stream of droplet frames.

    ``compress`` gzips the payload first if that shrinks it; ``password`` encrypts
    it (needs the ``cryptography`` extra). Both are recorded in the frame header so
    the receiver reverses them automatically.
    """
    def __init__(self, payload, block_size=_DEFAULT_BLOCK, password=None,
                 compress=True, session=None):
        data = bytes(payload)
        flags = 0
        if compress:
            gz = gzip_bytes(data)
            if len(gz) < len(data):
                data, flags = gz, flags | FLAG_GZIP
        if password is not None:
            from .crypto import encrypt
            data = encrypt(data, password)
            flags |= FLAG_ENC
        self._enc = Encoder(data, block_size,
                            session if session is not None else _rand_session(), flags)

    @classmethod
    def from_files(cls, files, **kw):
        """files: iterable of (name, bytes) or (name, bytes, mime)."""
        return cls(build_container(files), **kw)

    @property
    def n_blocks(self):
        return self._enc.N

    @property
    def session(self):
        return self._enc.session

    def frame(self, i):
        """The i-th droplet frame (bytes). i in 0..N-1 are the raw blocks
        (systematic); i>=N are repair droplets. Spray as many as you like."""
        return self._enc.frame(i)

    def spray(self, count=None, start=0):
        """Generator of frames. Infinite if ``count`` is None."""
        i = start
        while count is None or i < start + count:
            yield self._enc.frame(i)
            i += 1


class Receiver:
    """Collects droplet frames until the payload is complete. Auto-detects the
    transfer from the first valid frame's header (self-describing); a changed
    session or length starts a fresh decode."""
    def __init__(self):
        self._dec = None
        self.done = False

    def add(self, frame):
        """Feed one received frame (bytes). Invalid/corrupt frames are ignored.
        Returns True once the payload is fully reconstructed."""
        hdr = unpack_frame(frame)
        if not hdr:
            return self.done
        if (self._dec is None or hdr["session"] != self._dec.session
                or hdr["total_len"] != self._dec.total_len):
            self._dec = Decoder(hdr["session"], hdr["total_len"], hdr["K"], hdr["flags"])
            self.done = False
        self._dec.ingest(hdr["frame_index"], bytearray(hdr["body"]))
        self.done = self._dec.is_complete()
        return self.done

    @property
    def progress(self):
        return self._dec.progress() if self._dec else 0.0

    def result(self, password=None):
        """The reconstructed payload bytes (decrypted + decompressed as flagged).
        Returns None if not complete yet; raises if encrypted and no/ wrong
        password."""
        if not self._dec or not self._dec.is_complete():
            return None
        data = self._dec.payload()
        if self._dec.flags & FLAG_ENC:
            from .crypto import decrypt
            if password is None:
                raise ValueError("payload is encrypted; a password is required")
            data = decrypt(data, password)
        if self._dec.flags & FLAG_GZIP:
            data = gunzip_bytes(data)
        return data

    def files(self, password=None):
        """result() parsed as a multi-file container."""
        data = self.result(password=password)
        return parse_container(data) if data is not None else None
