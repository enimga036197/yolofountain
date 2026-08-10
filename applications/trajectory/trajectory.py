"""
trajectory — a self-running deterministic process kept on course by an absolute-state
fountain, not by a reliable connection.

The follower runs its OWN copy of a Lorenz system (deterministic, but with a small
model error), so open-loop it diverges from the truth exponentially — it genuinely
needs live guidance. The authority just blasts its absolute state as epoch'd
YoloFountain snapshots. The follower reconstructs the latest valid one and reconciles
toward it. Because the reference is ABSOLUTE (not a delta) and the follower is
self-running, a lost snapshot costs nothing — the next one is the full truth. And
because frames are CRC-gated, the follower never reconciles toward a torn/corrupt
target (which, for a controller, is worse than a missing one).

This is the soft-state pattern (periodic absolute refresh that self-heals) with a
rateless code as the ideal transport. The fountain delivers the setpoint robustly;
the smoothing toward it is the follower's thin control layer — not the codec's job.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from yolofountain.codec import Encoder, Decoder, unpack_frame   # noqa: E402

TRUTH = (10.0, 28.0, 8.0 / 3.0)     # sigma, rho, beta — the classic chaotic regime
_SNAP = struct.Struct("<Iddd")       # version(u32) + x,y,z(f64)  = 28 bytes


def _deriv(s, sigma, rho, beta):
    x, y, z = s
    return (sigma * (y - x), x * (rho - z) - y, x * y - beta * z)


def rk4(s, dt, p):
    k1 = _deriv(s, *p)
    k2 = _deriv(tuple(a + 0.5 * dt * b for a, b in zip(s, k1)), *p)
    k3 = _deriv(tuple(a + 0.5 * dt * b for a, b in zip(s, k2)), *p)
    k4 = _deriv(tuple(a + dt * b for a, b in zip(s, k3)), *p)
    return tuple(a + dt / 6.0 * (b + 2 * c + 2 * d + e)
                 for a, b, c, d, e in zip(s, k1, k2, k3, k4))


def dist(a, b):
    return sum((ai - bi) ** 2 for ai, bi in zip(a, b)) ** 0.5


class Authority:
    """Runs the truth and emits its absolute state as fountain snapshots."""
    def __init__(self, state=(1.0, 1.0, 1.0)):
        self.state = state
        self.version = 0

    def step(self, dt):
        self.state = rk4(self.state, dt, TRUTH)

    def snapshot_frames(self, K=28, redundancy=5):
        self.version += 1
        body = _SNAP.pack(self.version, *self.state)     # 28 B -> N=1; spray a few copies
        enc = Encoder(body, K, session=self.version & 0xFFFF)
        return [enc.frame(i) for i in range(enc.N + redundancy)]


class Follower:
    """Self-running with a slightly WRONG model, reconciled toward received snapshots."""
    def __init__(self, state=(5.0, 5.0, 5.0), rho_err=0.03, gain=0.6):
        self.state = state
        self.params = (10.0, 28.0 * (1.0 + rho_err), 8.0 / 3.0)   # model error -> drift
        self.gain = gain
        self._dec = None
        self._sess = self._tlen = None
        self.last_version = 0
        self.corrupt_delivered = 0

    def step(self, dt):
        self.state = rk4(self.state, dt, self.params)

    def receive(self, frame):
        hdr = unpack_frame(frame)
        if hdr is None:
            return                                        # CRC-rejected: never reconcile to garbage
        s, tl = hdr["session"], hdr["total_len"]
        if self._dec is None or s != self._sess or tl != self._tlen:
            self._dec = Decoder(s, tl, hdr["K"], hdr["flags"])
            self._sess, self._tlen = s, tl
        self._dec.ingest(hdr["frame_index"], bytearray(hdr["body"]))
        if self._dec.is_complete():
            v, x, y, z = _SNAP.unpack(self._dec.payload())
            if v > self.last_version:
                self.last_version = v
                self._reconcile((x, y, z))

    def _reconcile(self, target):
        # thin control layer: blend toward the authoritative absolute state
        self.state = tuple((1 - self.gain) * a + self.gain * b
                           for a, b in zip(self.state, target))

    def force_reconcile(self, target):
        """For the 'torn reference' contrast: reconcile toward a value the transport
        handed up WITHOUT integrity (what a raw shared-mem reference could do)."""
        self.corrupt_delivered += 1
        self._reconcile(target)
