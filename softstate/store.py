"""
softstate.Store — a durable, crash-safe key/value store on top of soft-state.

Each node owns a **namespace** (its keys) and holds a **merged view** of every node's
keys. `set()` mutates the local namespace, persists it *atomically*, and (if a channel
is given) broadcasts the whole namespace as absolute soft-state; `get()` reads the
merged view. Give it no channel and it's just a crash-safe local config store; give it
a channel and the same store is shared, connectionless, across a LAN.

The persistence is the piece the incumbents get wrong: a crash mid-save must never
corrupt the store. We write to a temp file, fsync, then **atomically replace** — so on
disk you only ever see the old complete file or the new complete file, never a torn
one — keep the previous version as a `.bak`, and checksum both so bit-rot is caught and
the backup used. This is the crash-safe config/state store the shared-memory and
"write the file in place" incumbents can't be.
"""
import json
import os
import struct
import threading
import zlib

from .core import Publisher, Subscriber

_CRC = struct.Struct("<I")


class Store:
    def __init__(self, path, node_id, channel=None, password=None, refresh_interval=None):
        self.path = path
        self.node = node_id & 0xFF
        self._local = {}                 # this node's authoritative namespace {key: json-value}
        self._remote = {}                # other node_id -> (namespace, version)
        self._lock = threading.RLock()
        self._callbacks = []
        self._channel = channel
        self._load()                     # survive a full restart
        self._pub = Publisher(self.node, password=password)
        self._sub = Subscriber(password=password, on_update=self._on_remote)
        if channel is not None:
            channel.subscribe(self._sub.feed)
        self._publish()                  # announce current state
        self._stop = None
        if refresh_interval and channel is not None:
            self._stop = threading.Event()
            t = threading.Thread(target=self._refresh_loop, args=(refresh_interval,), daemon=True)
            t.start()

    # ---- public API ----
    def set(self, key, value):
        """Set a key this node owns (value must be JSON-serializable). Durable + shared."""
        with self._lock:
            self._local[key] = value
            self._persist()
            self._publish()
        self._notify(key)

    def delete(self, key):
        with self._lock:
            if key in self._local:
                del self._local[key]
                self._persist()
                self._publish()
        self._notify(key)

    def get(self, key, default=None):
        """Merged view: this node's own keys win; otherwise the newest remote value."""
        with self._lock:
            if key in self._local:
                return self._local[key]
            best_v, best = -1, default
            for ns, ver in self._remote.values():
                if key in ns and ver > best_v:
                    best_v, best = ver, ns[key]
            return best

    def keys(self):
        with self._lock:
            ks = set(self._local)
            for ns, _ in self._remote.values():
                ks.update(ns)
            return sorted(ks)

    def items(self):
        return {k: self.get(k) for k in self.keys()}

    def local(self):
        with self._lock:
            return dict(self._local)

    def on_change(self, cb):
        """Register cb(key) — fired on any local or remote change (hot-reload hook)."""
        self._callbacks.append(cb)

    def close(self):
        if self._stop:
            self._stop.set()
        if hasattr(self._channel, "close"):
            self._channel.close()

    # ---- internals ----
    def _notify(self, key):
        for cb in self._callbacks:
            try:
                cb(key)
            except Exception:
                pass

    def _publish(self):
        if self._channel is None:
            return
        for f in self._pub.frames(json.dumps(self._local, separators=(",", ":")).encode()):
            self._channel.send(f)

    def _on_remote(self, pid, state, version):
        try:
            ns = json.loads(state.decode())
        except Exception:
            return
        with self._lock:
            self._remote[pid] = (ns, version)
        for k in ns:
            self._notify(k)

    def _refresh_loop(self, interval):
        # soft-state: periodically re-announce so late joiners catch up and loss heals
        while not self._stop.wait(interval):
            with self._lock:
                self._publish()

    # ---- crash-safe atomic persistence ----
    def _persist(self):
        data = json.dumps({"node": self.node, "ns": self._local},
                          separators=(",", ":")).encode()
        blob = _CRC.pack(zlib.crc32(data) & 0xFFFFFFFF) + data
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(self.path):
            try:
                import shutil
                shutil.copy2(self.path, self.path + ".bak")   # previous good version
            except OSError:
                pass
        os.replace(tmp, self.path)                            # atomic: old-or-new, never torn

    def _load(self):
        for p in (self.path, self.path + ".bak"):
            if not os.path.exists(p):
                continue
            try:
                blob = open(p, "rb").read()
                crc = _CRC.unpack_from(blob, 0)[0]
                data = blob[_CRC.size:]
                if (zlib.crc32(data) & 0xFFFFFFFF) == crc:     # reject a corrupt file, try .bak
                    self._local = json.loads(data.decode())["ns"]
                    return
            except Exception:
                continue
        self._local = {}
