"""
softstate transports — the swappable "carrier" seam. A channel just moves frame
bytes; softstate's Publisher/Subscriber don't care which one. Same code runs over an
in-memory bus, UDP multicast on a LAN, or (in principle) a physical beam.
"""
import random
import socket
import struct
import threading


class InMemoryChannel:
    """A shared broadcast medium with controllable loss/corruption — the fountain's
    'collisions are just erasures' modelled directly. Great for tests and fault
    injection; subscribers register a callback that receives each frame."""
    def __init__(self, loss=0.0, corrupt=0.0, rng=None):
        self.subs = []
        self.loss = loss
        self.corrupt = corrupt
        self.rng = rng or random.Random(0)

    def subscribe(self, cb):
        self.subs.append(cb)

    def send(self, frame):
        for cb in self.subs:
            if self.rng.random() < self.loss:
                continue
            f = frame
            if self.corrupt and self.rng.random() < self.corrupt:
                b = bytearray(f)
                bit = self.rng.randrange(len(b) * 8)
                b[bit // 8] ^= 1 << (bit % 8)
                f = bytes(b)
            cb(f)


class UdpChannel:
    """UDP multicast — connectionless by nature, one sender to many receivers with no
    handshake, exactly the shape softstate wants. Best-effort (which is fine: the
    fountain heals loss). `subscribe(cb)` starts a background receive loop."""
    def __init__(self, group="239.19.84.1", port=54019, ttl=1, loopback=True):
        self.addr = (group, port)
        self.group = group
        self.port = port
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        self._tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if loopback else 0)
        self._rx = None
        self._stop = threading.Event()

    def send(self, frame):
        self._tx.sendto(frame, self.addr)

    def subscribe(self, cb):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rx.bind(("", self.port))
        mreq = struct.pack("=4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
        rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        rx.settimeout(0.5)
        self._rx = rx

        def loop():
            while not self._stop.is_set():
                try:
                    data, _ = rx.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                cb(data)
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    def close(self):
        self._stop.set()
        try:
            self._tx.close()
        except OSError:
            pass
        if self._rx:
            try:
                self._rx.close()
            except OSError:
                pass
