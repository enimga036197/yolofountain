"""
softstate — connectionless soft-state broadcast, built on YoloFountain.

Publish *absolute versioned state*; subscribe as either a change-notified **reader**
(a status/config store) or a reconciling **tracker** (trajectory control). Same
primitive, two roles — the role is just what you do in the update callback.

Guarantees, all from the rateless core: a subscriber only ever commits a *complete,
CRC-valid* version (never torn/partial/corrupt); a lost update costs nothing (the next
is the whole truth — absolute state, not deltas); a late joiner reconstructs current
state from the ongoing spray (no catch-up protocol); a publisher that dies just goes
quiet and ages out. Optional authenticated group encryption. Carrier-agnostic — the
channel is a swappable seam (in-memory, UDP multicast, or a physical beam).

    pub = softstate.Publisher(pid=1)
    for frame in pub.frames(state_bytes):   # spray an epoch
        channel.send(frame)

    sub = softstate.Subscriber(on_update=lambda pid, state, ver: ...)
    sub.feed(frame)                         # feed received frames
    sub.get(1)                              # -> (state, version, ts) or None
"""
import os
import struct
import time

from yolofountain.codec import Encoder, Decoder, unpack_frame

_HDR = struct.Struct("<Id")          # version(u32) + ts(f64) prepended to the state
_GROUP_SALT = b"softstate-grp-16"


def _now():
    return time.perf_counter()


# ---- group crypto: KDF once, ChaCha per epoch ----
def _derive(password):
    from yolofountain.crypto import derive_key
    return derive_key(password, _GROUP_SALT)


def _seal(key, data):
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    nonce = os.urandom(12)
    return nonce + ChaCha20Poly1305(key).encrypt(nonce, data, None)


def _open(key, blob):
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    return ChaCha20Poly1305(key).decrypt(blob[:12], blob[12:], None)


class Publisher:
    """Turns a publisher's current absolute state into the frames to spray this epoch.
    `pid` is 0..255; `redundancy` extra droplets make a single update survive loss."""
    def __init__(self, pid, K=256, redundancy=4, password=None):
        self.pid = pid & 0xFF
        self.K = K
        self.redundancy = redundancy
        self._key = _derive(password) if password is not None else None
        self.version = 0

    def frames(self, state):
        self.version += 1
        body = _HDR.pack(self.version, _now()) + bytes(state)
        if self._key is not None:
            body = _seal(self._key, body)
        session = (self.pid << 8) | (self.version & 0xFF)     # demux id: (pid, epoch)
        enc = Encoder(body, self.K, session=session)
        return [enc.frame(i) for i in range(enc.N + self.redundancy)]


class Subscriber:
    """Reconstructs the latest complete, valid state per publisher. `on_update(pid,
    state, version)` fires when a newer version commits — wire it to update a cache
    (reader role) or to reconcile a follower (tracker role). `ttl` ages out quiet
    publishers from `snapshot()`."""
    def __init__(self, password=None, ttl=None, on_update=None):
        self._key = _derive(password) if password is not None else None
        self.ttl = ttl
        self.on_update = on_update
        self.table = {}          # pid -> (state, version, ts, seen)
        self._dec = {}           # session -> [Decoder, done]
        self.rejected = 0        # frames dropped by CRC / bad auth (never delivered)

    def feed(self, raw):
        """Feed one received frame. Returns the pid if it produced a newer state, else None.
        Invalid/corrupt frames are counted and dropped — never delivered as data."""
        hdr = unpack_frame(raw)
        if hdr is None:
            self.rejected += 1
            return None
        session = hdr["session"]
        pid = session >> 8
        ent = self._dec.get(session)
        if ent is None or ent[0].total_len != hdr["total_len"]:
            ent = [Decoder(session, hdr["total_len"], hdr["K"], hdr["flags"]), False]
            self._dec[session] = ent
        if ent[1]:
            return None
        ent[0].ingest(hdr["frame_index"], bytearray(hdr["body"]))
        if not ent[0].is_complete():
            return None
        ent[1] = True
        body = ent[0].payload()
        if self._key is not None:
            try:
                body = _open(self._key, body)
            except Exception:
                self.rejected += 1                # wrong key / tampered
                return None
        version, ts = _HDR.unpack_from(body, 0)
        state = body[_HDR.size:]
        cur = self.table.get(pid)
        if cur is not None and version <= cur[1]:
            return None                            # stale / duplicate epoch
        self.table[pid] = (state, version, ts, _now())
        self._reap(pid, session)
        if self.on_update:
            self.on_update(pid, state, version)
        return pid

    def get(self, pid):
        """Latest (state, version, ts) for a publisher, or None."""
        v = self.table.get(pid)
        return None if v is None else (v[0], v[1], v[2])

    def snapshot(self):
        """All live publishers -> {pid: (state, version, ts)}, TTL-aged."""
        now = _now()
        return {pid: (v[0], v[1], v[2]) for pid, v in self.table.items()
                if self.ttl is None or now - v[3] <= self.ttl}

    def _reap(self, pid, keep_session):
        for s in list(self._dec):
            if s != keep_session and (s >> 8) == pid:
                del self._dec[s]
