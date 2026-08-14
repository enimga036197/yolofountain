"""
flock — a zero-config shared clipboard + file drop for your machines.

Copy on any machine, paste on any other, instantly. Drop a file and it lands on every
machine's ~/flock-drops. No accounts, no cloud, no pairing, no server — the machines
just find each other on a UDP multicast group (works across a LAN or a ZeroTier
overlay). Optional group password encrypts everything, so only your machines with the
key see the traffic. Built on softstate (the shared clipboard) + YoloFountain (the file
drops, so large files survive the lossy connectionless channel).

    python flock.py                         # run the daemon (share clipboard, receive drops)
    python flock.py send report.pdf         # drop a file to every machine running flock
    FLOCK_KEY=teamsecret python flock.py     # encrypted group

Why it's *better* than the usual shared-clipboard tools: no Microsoft/cloud account, no
relay, works over your own ZeroTier fabric across heterogeneous machines, encrypted,
crash-safe, and a machine that comes online instantly gets the current clipboard
(soft-state late-join). Why it's *faster*: there's no connection to set up — the whole
cost of the incumbents (pair, sign in, negotiate) is deleted.
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import softstate
import yolofountain
from yolofountain.codec import unpack_frame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clip import default_clipboard   # noqa: E402

CLIP_PORT = 54020
DROP_PORT = 54021
GROUP = "239.19.84.7"
_TS = struct.Struct("<d")            # wall-clock recency stamp inside the clipboard payload


class Flock:
    def __init__(self, node_id, clipboard, clip_channel, drop_channel, password=None,
                 drop_dir=None):
        self.node = node_id & 0xFF
        self.clip = clipboard
        self.drop_dir = drop_dir or os.path.join(os.path.expanduser("~"), "flock-drops")
        os.makedirs(self.drop_dir, exist_ok=True)

        self.clip_channel = clip_channel
        self._pub = softstate.Publisher(self.node, password=password)
        self._sub = softstate.Subscriber(password=password, on_update=self._on_remote_clip)
        clip_channel.subscribe(self._sub.feed)
        self._last_seen = None            # last local clipboard text we published/applied
        self._last_ts = 0.0               # newest clipboard timestamp we've accepted

        self.drop_channel = drop_channel
        self._rx = None
        self._rx_session = None
        self._saved = set()
        if drop_channel is not None:
            drop_channel.subscribe(self._on_drop_frame)
        self.received_files = []          # for tests / logging

    # ---- clipboard ----
    def poll_clipboard(self):
        """Publish the local clipboard if the user changed it (not us)."""
        v = self.clip.get()
        if v is None or v == self._last_seen:
            return
        self._last_seen = v
        self._last_ts = time.time()
        payload = _TS.pack(self._last_ts) + v.encode("utf-8")
        for f in self._pub.frames(payload):
            self.clip_channel.send(f)

    def _on_remote_clip(self, pid, state, version):
        if pid == self.node or len(state) < _TS.size:
            return
        ts = _TS.unpack_from(state, 0)[0]
        text = state[_TS.size:].decode("utf-8", "replace")
        if ts <= self._last_ts:               # older than what we already have -> ignore (converges)
            return
        self._last_ts = ts
        if text != self.clip.get():
            self._last_seen = text            # echo-guard: don't re-publish what we just applied
            self.clip.set(text)

    # ---- file drop ----
    def send_file(self, path, password=None, overhead=1.6):
        data = open(path, "rb").read()
        name = os.path.basename(path)
        tx = yolofountain.Sender.from_files([(name, data)], password=password)
        n = max(tx.n_blocks + 4, int(tx.n_blocks * overhead) + 2)
        for i in range(n):
            self.drop_channel.send(tx.frame(i))
        return name, len(data), n

    def _on_drop_frame(self, frame):
        hdr = unpack_frame(frame)
        if hdr is None:
            return
        sess = hdr["session"]
        if self._rx is None or sess != self._rx_session:
            if sess in self._saved:
                return
            self._rx = yolofountain.Receiver()
            self._rx_session = sess
        if self._rx.add(frame):
            self._saved.add(sess)
            try:
                for f in self._rx.files(password=os.environ.get("FLOCK_KEY")):
                    dest = os.path.join(self.drop_dir, os.path.basename(f["name"]))
                    with open(dest, "wb") as out:
                        out.write(f["bytes"])
                    self.received_files.append(dest)
            except Exception:
                pass
            self._rx = None
            self._rx_session = None


def _run_daemon(password):
    clip = default_clipboard()
    clip_ch = softstate.UdpChannel(group=GROUP, port=CLIP_PORT)
    drop_ch = softstate.UdpChannel(group=GROUP, port=DROP_PORT)
    node = int.from_bytes(os.urandom(1), "little")
    fl = Flock(node, clip, clip_ch, drop_ch, password=password)
    print(f"flock running (node {node}); clipboard shared on {GROUP}:{CLIP_PORT}, "
          f"drops -> {fl.drop_dir}{'  [encrypted]' if password else ''}")
    print("copy anything here and it appears on your other machines. Ctrl-C to stop.")
    try:
        while True:
            fl.poll_clipboard()
            time.sleep(0.3)
    except KeyboardInterrupt:
        clip_ch.close(); drop_ch.close()
        print("\nstopped.")


def _send(path, password):
    drop_ch = softstate.UdpChannel(group=GROUP, port=DROP_PORT)
    fl = Flock(0, default_clipboard(), softstate.InMemoryChannel(), drop_ch, password=password)
    name, size, n = fl.send_file(path, password=password)
    time.sleep(0.5)                            # let the last frames flush
    drop_ch.close()
    print(f"beamed {name} ({size} bytes, {n} frames) to the flock.")


def main():
    password = os.environ.get("FLOCK_KEY")
    if len(sys.argv) >= 3 and sys.argv[1] == "send":
        _send(sys.argv[2], password)
    else:
        _run_daemon(password)


if __name__ == "__main__":
    main()
