"""flock: shared clipboard converges across nodes; file drop reconstructs on receivers."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "applications", "flock"))
import softstate
from clip import FakeClipboard
import flock as flockmod


def _pair():
    clip_ch = softstate.InMemoryChannel()
    drop_ch = softstate.InMemoryChannel()
    da, db = tempfile.mkdtemp(), tempfile.mkdtemp()
    a = flockmod.Flock(1, FakeClipboard(), clip_ch, drop_ch, drop_dir=da)
    b = flockmod.Flock(2, FakeClipboard(), clip_ch, drop_ch, drop_dir=db)
    return a, b


def test_clipboard_shares():
    a, b = _pair()
    a.clip.set("hello team")
    a.poll_clipboard()
    assert b.clip.get() == "hello team"          # copied on A, appeared on B


def test_no_echo_loop():
    a, b = _pair()
    a.clip.set("x"); a.poll_clipboard()
    assert b.clip.get() == "x"
    b.poll_clipboard()                            # B just applied it -> must not re-publish
    # a newer copy on B still wins and propagates back
    time.sleep(0.001)
    b.clip.set("y from b"); b.poll_clipboard()
    assert a.clip.get() == "y from b"


def test_older_update_ignored():
    a, b = _pair()
    b.clip.set("newer"); b.poll_clipboard()
    assert a.clip.get() == "newer"
    # an update with an older timestamp must not clobber it (convergence)
    import struct
    stale = struct.pack("<d", 1.0) + b"stale"
    a._on_remote_clip(9, stale, 1)
    assert a.clip.get() == "newer"


def test_file_drop():
    a, b = _pair()
    payload = b"quarterly-report " * 500          # ~8.5 KB, multi-block
    p = os.path.join(tempfile.mkdtemp(), "report.txt")
    open(p, "wb").write(payload)
    a.send_file(p)
    got = [f for f in b.received_files if open(f, "rb").read() == payload]
    assert got, "receiver did not reconstruct the dropped file"
    assert os.path.basename(got[0]) == "report.txt"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("flock: ALL PASS")
