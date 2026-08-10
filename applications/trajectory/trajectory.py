"""
trajectory — a self-running deterministic process kept on course by an absolute-state
fountain, now built on the **softstate** library (the extracted shared core).

The follower runs its OWN copy of a Lorenz system (deterministic, but with a small
model error), so open-loop it diverges exponentially — it genuinely needs live
guidance. The authority publishes its absolute state via softstate; the follower is a
softstate Subscriber whose update callback *reconciles* toward the latest valid state.
Because the reference is absolute and the follower is self-running, a lost snapshot
costs nothing; because frames are CRC-gated, it never reconciles toward a torn target.

Note the shape: softstate does all the transport, versioning, demux and integrity; the
app supplies only (a) how to serialize its state and (b) the control law in the
callback. Same primitive as applications/statusbus — only the callback differs.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import softstate   # noqa: E402

TRUTH = (10.0, 28.0, 8.0 / 3.0)     # sigma, rho, beta — the classic chaotic regime
_STATE = struct.Struct("<ddd")       # x, y, z  (softstate adds the version/timestamp)


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
    """Runs the truth and publishes its absolute state as softstate snapshots."""
    def __init__(self, state=(1.0, 1.0, 1.0)):
        self.state = state
        self.pub = softstate.Publisher(pid=1, redundancy=5)

    def step(self, dt):
        self.state = rk4(self.state, dt, TRUTH)

    def snapshot_frames(self):
        return self.pub.frames(_STATE.pack(*self.state))


class Follower:
    """Self-running with a slightly WRONG model; a softstate Subscriber reconciles it
    toward each received snapshot (the update callback is the control law)."""
    def __init__(self, state=(5.0, 5.0, 5.0), rho_err=0.03, gain=0.6):
        self.state = state
        self.params = (10.0, 28.0 * (1.0 + rho_err), 8.0 / 3.0)   # model error -> drift
        self.gain = gain
        self.sub = softstate.Subscriber(on_update=self._on_update)
        self.corrupt_delivered = 0

    def step(self, dt):
        self.state = rk4(self.state, dt, self.params)

    def receive(self, frame):
        self.sub.feed(frame)                         # CRC-invalid frames are dropped here

    def _on_update(self, pid, state, version):
        self._reconcile(_STATE.unpack(state))

    def _reconcile(self, target):
        # thin control layer: blend toward the authoritative absolute state
        self.state = tuple((1 - self.gain) * a + self.gain * b
                           for a, b in zip(self.state, target))

    def force_reconcile(self, target):
        """For the 'torn reference' contrast: reconcile toward a value a transport
        WITHOUT integrity could hand up (what raw shared memory does)."""
        self.corrupt_delivered += 1
        self._reconcile(target)
