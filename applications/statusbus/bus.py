"""
statusbus — a connectionless status/presence bus built on YoloFountain.

Each producer sprays its current state as an epoch'd rateless transfer; consumers
demux by producer, reconstruct the latest complete state, and age out producers
that go quiet. No host, no connections, no join protocol, no locks. A frame that
fails its CRC is dropped (never delivered as data); the continuous spray heals
transient loss/corruption on the next epoch.

Producer/Consumer are driven directly (feed frames in) so a benchmark can inject
loss and corruption deterministically; `InMemoryBus` and `UdpBus` wire them over a
real medium for the live demo.

Design note: the frame's 16-bit `session` carries (producer_id<<8 | epoch&0xFF) so
consumers can demux and detect a new epoch; the full version + timestamp live in the
record for correct ordering. Small state is one systematic frame (no fountain math);
large state (a join-bootstrap dump) is where the fountain actually earns its keep.
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from yolofountain.codec import Encoder, Decoder, unpack_frame   # noqa: E402

# record: pid(H) version(I) ts(d) value(Q) check(Q[=~value]) + optional pad
_REC = struct.Struct("<HIdQQ")
CHECK_MASK = (1 << 64) - 1


def make_record(pid, version, value, pad=0):
    ts = _now()
    body = _REC.pack(pid, version, ts, value, value ^ CHECK_MASK)
    if pad:
        body += b"\x00" * pad
    return body


def parse_record(body):
    pid, version, ts, value, check = _REC.unpack_from(body, 0)
    consistent = (check == (value ^ CHECK_MASK))
    return {"pid": pid, "version": version, "ts": ts, "value": value,
            "consistent": consistent}


def _now():
    return time.perf_counter()


# group crypto: derive the key ONCE from the shared password; ChaCha per epoch.
# (Deriving per-update would re-run scrypt every message — pathological.)
_GROUP_SALT = b"yolo-sbus-salt16"


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


class Producer:
    """Turns a producer's current state into the frames to spray this epoch."""
    def __init__(self, pid, K=64, password=None, spray=1.6, pad=0):
        self.pid = pid & 0xFF
        self.K = K
        self.spray = spray
        self.pad = pad
        self._key = _derive(password) if password is not None else None

    def frames(self, version, value):
        """The droplet frames for state (version, value). Sprays a little extra so a
        single lost/corrupt frame is recovered within the same epoch."""
        body = make_record(self.pid, version, value, self.pad)
        if self._key is not None:
            body = _seal(self._key, body)
        session = (self.pid << 8) | (version & 0xFF)
        enc = Encoder(body, self.K, session=session)
        n = max(enc.N + 3, int(enc.N * self.spray) + 1)   # floor of a few extra droplets
        return [enc.frame(i) for i in range(n)]


class Consumer:
    """Demuxes frames into per-epoch decoders and keeps the latest state per pid."""
    def __init__(self, password=None, ttl=2.0):
        self._key = _derive(password) if password is not None else None
        self.ttl = ttl
        self.table = {}          # pid -> record dict (+ _seen)
        self._decoders = {}      # session -> [Decoder, done]
        self.rejected = 0        # frames dropped by CRC (integrity guard)
        self.torn_delivered = 0  # records handed up that were inconsistent (should stay 0)

    def feed(self, raw):
        hdr = unpack_frame(raw)
        if hdr is None:
            self.rejected += 1               # corrupt/foreign frame — never delivered
            return False
        session = hdr["session"]
        pid = session >> 8
        ent = self._decoders.get(session)
        if ent is None or ent[0].total_len != hdr["total_len"]:
            ent = [Decoder(session, hdr["total_len"], hdr["K"], hdr["flags"]), False]
            self._decoders[session] = ent
        dec, done = ent
        if done:
            return False
        dec.ingest(hdr["frame_index"], bytearray(hdr["body"]))
        if dec.is_complete():
            ent[1] = True
            body = dec.payload()
            if self._key is not None:
                try:
                    body = _open(self._key, body)
                except Exception:
                    return False              # wrong key / tampered — reject, don't deliver
            rec = parse_record(body)
            if not rec["consistent"]:
                self.torn_delivered += 1      # by construction this must never happen
                return False
            cur = self.table.get(rec["pid"])
            if cur is None or rec["version"] > cur["version"]:
                rec["_seen"] = _now()
                self.table[rec["pid"]] = rec
                self._reap(session)
                return True
        return False

    def _reap(self, keep_session):
        # drop decoders for older epochs of this producer, and any long-idle ones
        pid = keep_session >> 8
        for s in list(self._decoders):
            if s != keep_session and (s >> 8) == pid:
                del self._decoders[s]

    def snapshot(self):
        """Current live state per producer, dropping ones past TTL (aged out)."""
        now = _now()
        return {pid: r for pid, r in self.table.items() if now - r["_seen"] <= self.ttl}


# ---- live wiring (optional; the benchmark drives Producer/Consumer directly) ----
class InMemoryBus:
    """A shared broadcast medium with controllable loss/corruption — the fountain's
    'the medium superimposes, collisions are just erasures' modelled directly."""
    def __init__(self, loss=0.0, corrupt=0.0, rng=None):
        import random
        self.subs = []
        self.loss = loss
        self.corrupt = corrupt
        self.rng = rng or random.Random(0)

    def subscribe(self, consumer):
        self.subs.append(consumer)

    def publish(self, frame):
        for c in self.subs:
            if self.rng.random() < self.loss:
                continue
            f = frame
            if self.corrupt and self.rng.random() < self.corrupt:
                b = bytearray(f)
                bit = self.rng.randrange(len(b) * 8)
                b[bit // 8] ^= 1 << (bit % 8)
                f = bytes(b)
            c.feed(f)
