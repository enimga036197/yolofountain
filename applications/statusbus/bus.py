"""
statusbus — connectionless status/presence, now a thin app over the **softstate**
library (the extracted shared core). A producer publishes its current state; a
consumer reconstructs the latest per producer and ages quiet ones out. softstate does
the transport, versioning, demux, integrity and crypto; statusbus only adds the
app-level record (a value plus a redundant check field, so the benchmark can *detect*
a torn read — which, on this bus, never happens by construction).

Same primitive as applications/trajectory — the only difference is what the update
callback does (there: reconcile a follower; here: keep a table).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import softstate   # noqa: E402

CHECK_MASK = (1 << 64) - 1
_REC = struct.Struct("<QQ")          # value, check(=~value)  -> torn-read detector


def make_record(value, pad=0):
    body = _REC.pack(value, value ^ CHECK_MASK)
    return body + b"\x00" * pad if pad else body


def parse_record(state):
    value, check = _REC.unpack_from(state, 0)
    return {"value": value, "consistent": check == (value ^ CHECK_MASK)}


class Producer:
    """Publishes a producer's current value as a softstate stream. `version` is kept in
    the signature for the benchmark's readability; softstate assigns the real version."""
    def __init__(self, pid, K=64, password=None, spray=None, pad=0):
        self.pid = pid & 0xFF
        self.pad = pad
        self.pub = softstate.Publisher(pid, K=K, redundancy=4, password=password)

    def frames(self, version, value):
        return self.pub.frames(make_record(value, self.pad))


class Consumer:
    """Latest state per producer, torn-free by construction."""
    def __init__(self, password=None, ttl=2.0):
        self.torn_delivered = 0
        self.sub = softstate.Subscriber(password=password, ttl=ttl, on_update=self._on_update)

    def _on_update(self, pid, state, version):
        if not parse_record(state)["consistent"]:
            self.torn_delivered += 1          # must never happen: CRC + atomic epoch

    def feed(self, raw):
        return self.sub.feed(raw)

    @property
    def rejected(self):
        return self.sub.rejected

    def snapshot(self):
        out = {}
        for pid, (state, version, ts) in self.sub.snapshot().items():
            rec = parse_record(state)
            out[pid] = {"pid": pid, "version": version, "value": rec["value"],
                        "consistent": rec["consistent"], "ts": ts}
        return out


class InMemoryBus:
    """A shared broadcast medium with controllable loss/corruption (fault injection)."""
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
