"""softstate.Store: durability across restart, crash-safe atomic save, distributed."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import softstate


def _tmp():
    d = tempfile.mkdtemp()
    return os.path.join(d, "store.json")


def test_local_kv():
    p = _tmp()
    s = softstate.Store(p, node_id=1)
    s.set("theme", "dark")
    s.set("volume", 42)
    assert s.get("theme") == "dark" and s.get("volume") == 42
    assert s.get("missing", "def") == "def"
    assert sorted(s.keys()) == ["theme", "volume"]


def test_survives_restart():
    p = _tmp()
    s = softstate.Store(p, node_id=1)
    s.set("token", "abc123")
    s.set("count", 7)
    del s                                    # process "exits"
    s2 = softstate.Store(p, node_id=1)       # fresh process, same file
    assert s2.get("token") == "abc123" and s2.get("count") == 7


def test_atomic_save_never_torn():
    # the file on disk is always a complete, checksum-valid snapshot
    p = _tmp()
    s = softstate.Store(p, node_id=1)
    for i in range(50):
        s.set("k", i)
        blob = open(p, "rb").read()
        import struct, zlib, json
        crc = struct.unpack_from("<I", blob, 0)[0]
        data = blob[4:]
        assert (zlib.crc32(data) & 0xFFFFFFFF) == crc          # never a torn write
        assert json.loads(data.decode())["ns"]["k"] == i


def test_corrupt_file_falls_back_to_backup():
    p = _tmp()
    s = softstate.Store(p, node_id=1)
    s.set("v", "one")            # writes p
    s.set("v", "two")            # writes p, copies previous (with "one") to p.bak
    # simulate bit-rot of the main file
    with open(p, "r+b") as f:
        f.seek(8); f.write(b"\xFF\xFF\xFF\xFF")
    s2 = softstate.Store(p, node_id=1)
    assert s2.get("v") == "one"                                # recovered from .bak


def test_distributed_over_channel():
    ch = softstate.InMemoryChannel()
    a = softstate.Store(_tmp(), node_id=1, channel=ch)
    b = softstate.Store(_tmp(), node_id=2, channel=ch)
    a.set("owner1_key", "A")
    b.set("owner2_key", "B")
    assert b.get("owner1_key") == "A"        # b sees a's key over the channel
    assert a.get("owner2_key") == "B"        # and vice versa
    assert set(a.keys()) == {"owner1_key", "owner2_key"}


def test_on_change_callback():
    ch = softstate.InMemoryChannel()
    a = softstate.Store(_tmp(), node_id=1, channel=ch)
    b = softstate.Store(_tmp(), node_id=2, channel=ch)
    fired = []
    b.on_change(lambda key: fired.append(key))
    a.set("hot", "reload")
    assert "hot" in fired                    # b was notified of a's change


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("store: ALL PASS")
