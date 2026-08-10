"""softstate: publish/subscribe, loss + late-join, corruption rejection, crypto, aging."""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import softstate


def _spray(pub, sub, state, loss=0.0, corrupt=0.0, rng=None):
    rng = rng or random.Random(0)
    for f in pub.frames(state):
        if rng.random() < loss:
            continue
        if corrupt and rng.random() < corrupt:
            b = bytearray(f); bit = rng.randrange(len(b) * 8); b[bit // 8] ^= 1 << (bit % 8)
            f = bytes(b)
        sub.feed(f)


def test_basic_roundtrip():
    pub = softstate.Publisher(pid=3)
    sub = softstate.Subscriber()
    _spray(pub, sub, b"hello state")
    st, ver, ts = sub.get(3)
    assert st == b"hello state" and ver == 1


def test_latest_wins_and_callback():
    seen = []
    sub = softstate.Subscriber(on_update=lambda pid, s, v: seen.append((pid, v)))
    pub = softstate.Publisher(pid=1)
    for i in range(1, 6):
        _spray(pub, sub, f"v{i}".encode())
    st, ver, _ = sub.get(1)
    assert ver == 5 and st == b"v5"
    assert seen[-1] == (1, 5)


def test_survives_loss_and_late_join():
    pub = softstate.Publisher(pid=2, redundancy=5)
    rng = random.Random(7)
    # subscriber joins only after several epochs already went by (late join)
    for i in range(1, 4):
        for f in pub.frames(f"early{i}".encode()):
            if rng.random() > 0.5:
                pass  # discarded before the subscriber exists
    sub = softstate.Subscriber()
    for i in range(4, 12):
        _spray(pub, sub, f"state{i}".encode(), loss=0.6, rng=rng)
    st, ver, _ = sub.get(2)
    assert st == b"state11" and ver == 11        # caught up despite 60% loss + late join


def test_corruption_rejected():
    pub = softstate.Publisher(pid=1, redundancy=6)
    sub = softstate.Subscriber()
    _spray(pub, sub, b"good", corrupt=0.4, rng=random.Random(3))
    assert sub.rejected > 0                       # corrupt frames dropped
    st, ver, _ = sub.get(1)
    assert st == b"good"                          # and the state is still correct


def test_encryption_and_wrong_key():
    pub = softstate.Publisher(pid=1, password="grp-secret")
    good = softstate.Subscriber(password="grp-secret")
    bad = softstate.Subscriber(password="nope")
    frames = pub.frames(b"classified")
    # the plaintext never appears on the wire
    assert b"classified" not in b"".join(frames)
    for f in frames:
        good.feed(f); bad.feed(f)
    assert good.get(1)[0] == b"classified"
    assert bad.get(1) is None and bad.rejected > 0


def test_ttl_ageout():
    sub = softstate.Subscriber(ttl=0.05)
    pub = softstate.Publisher(pid=9)
    _spray(pub, sub, b"here")
    assert 9 in sub.snapshot()
    time.sleep(0.08)
    assert 9 not in sub.snapshot()                # quiet publisher aged out


def test_channel_wiring():
    ch = softstate.InMemoryChannel(loss=0.3, rng=random.Random(1))
    sub = softstate.Subscriber()
    ch.subscribe(sub.feed)
    pub = softstate.Publisher(pid=5, redundancy=5)
    for i in range(1, 6):
        for f in pub.frames(f"c{i}".encode()):
            ch.send(f)
    assert sub.get(5)[0] == b"c5"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("softstate: ALL PASS")
