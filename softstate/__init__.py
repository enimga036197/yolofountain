"""
softstate — connectionless soft-state broadcast on YoloFountain.

Publish absolute versioned state; subscribe as a change-notified reader or a
reconciling tracker. Rateless, CRC-gated (never delivers torn/corrupt state),
self-healing (loss & late-join are free), crash-graceful, optionally encrypted,
carrier-agnostic. One primitive; the consumer's role is the only thing that differs.

    import softstate
    ch  = softstate.InMemoryChannel()             # or UdpChannel() for a real LAN
    pub = softstate.Publisher(pid=1)
    sub = softstate.Subscriber(on_update=lambda pid, state, ver: print(pid, ver))
    ch.subscribe(sub.feed)
    for f in pub.frames(b"my current state"):
        ch.send(f)
    print(sub.get(1))                             # (b'my current state', 1, ts)
"""
from .core import Publisher, Subscriber
from .channels import InMemoryChannel, UdpChannel

__version__ = "0.1.0"
__all__ = ["Publisher", "Subscriber", "InMemoryChannel", "UdpChannel"]
