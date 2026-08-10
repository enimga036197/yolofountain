"""
Shared-memory status stores — the on-machine incumbents, in two honest columns.

RawStore: the speed ceiling. A slot per producer, written field-by-field with no
synchronisation — exactly what a plain C struct in an MMF gives you. Fast because it
does no safety work: a reader can catch a half-written slot (torn read), a writer
that dies mid-update leaves it torn forever, a flipped byte is read as data.

HardenedStore: what it costs to make shared memory as safe as the rateless bus —
a seqlock (torn-read-free), a per-record CRC (detect corruption), a version counter,
and optional ChaCha encryption. It closes the integrity gap, at a cost — and even
then a writer that dies mid-update wedges its slot (odd seqlock), it can't
authenticate a writer portably, and it never leaves the machine.

Both expose the same tiny interface as the bus:  publish(pid, version, value) /
read(pid) -> record-or-None.
"""
import mmap
import os
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

CHECK_MASK = (1 << 64) - 1

# record fields (both stores): pid(H) version(I) ts(d) value(Q) check(Q=~value)
_RAW = struct.Struct("<HIdQQ")            # 30 bytes
_REC = _RAW


def _now():
    return time.perf_counter()


class RawStore:
    def __init__(self, n_slots, slot_size=64):
        self.n = n_slots
        self.slot = slot_size
        self.buf = mmap.mmap(-1, n_slots * slot_size)   # anonymous shared region

    def publish(self, pid, version, value):
        off = pid * self.slot
        # field-by-field, no atomicity — a concurrent reader can interleave
        self.buf[off:off + 2] = struct.pack("<H", pid)
        self.buf[off + 2:off + 6] = struct.pack("<I", version)
        self.buf[off + 6:off + 14] = struct.pack("<d", _now())
        self.buf[off + 14:off + 22] = struct.pack("<Q", value)
        # deliberately a beat later: the consistency field trails the value write
        self.buf[off + 22:off + 30] = struct.pack("<Q", value ^ CHECK_MASK)

    def read(self, pid):
        off = pid * self.slot
        pid_, version, ts, value, check = _RAW.unpack_from(self.buf, off)
        if version == 0:
            return None
        return {"pid": pid_, "version": version, "ts": ts, "value": value,
                "consistent": check == (value ^ CHECK_MASK)}

    def close(self):
        self.buf.close()


class HardenedStore:
    def __init__(self, n_slots, slot_size=96, password=None):
        self.n = n_slots
        self.slot = slot_size
        self.buf = mmap.mmap(-1, n_slots * slot_size)
        self.password = password
        self._key = None
        if password is not None:
            from yolofountain.crypto import derive_key
            self._salt = b"statusbus-fixed-salt-16"  # demo: fixed salt (real: per-store)
            self._key = derive_key(password, self._salt[:16])

    def _enc(self, plain):
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        nonce = os.urandom(12)
        return nonce + ChaCha20Poly1305(self._key).encrypt(nonce, plain, None)

    def _dec(self, blob):
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        return ChaCha20Poly1305(self._key).decrypt(blob[:12], blob[12:], None)

    # slot layout: seq(I=4) | len(H=2) | record(len bytes) | padding
    def publish(self, pid, version, value):
        off = pid * self.slot
        seq = struct.unpack_from("<I", self.buf, off)[0]
        seq = (seq + 1) | 1                       # odd -> "write in progress"
        self.buf[off:off + 4] = struct.pack("<I", seq)
        fields = _REC.pack(pid, version, _now(), value, value ^ CHECK_MASK)
        rec = fields + struct.pack("<I", zlib.crc32(fields) & 0xFFFFFFFF)   # +crc32
        if self._key is not None:
            rec = self._enc(rec)
        self.buf[off + 4:off + 6] = struct.pack("<H", len(rec))
        self.buf[off + 6:off + 6 + len(rec)] = rec
        self.buf[off:off + 4] = struct.pack("<I", seq + 1)   # even -> done

    def read(self, pid, retries=8):
        off = pid * self.slot
        for _ in range(retries):
            s1 = struct.unpack_from("<I", self.buf, off)[0]
            if s1 & 1:
                continue                          # writer mid-update -> retry
            ln = struct.unpack_from("<H", self.buf, off + 4)[0]
            if ln == 0 or ln > self.slot - 6:
                return None
            rec = bytes(self.buf[off + 6:off + 6 + ln])
            s2 = struct.unpack_from("<I", self.buf, off)[0]
            if s1 != s2:
                continue                          # changed under us -> retry (torn-free)
            if self._key is not None:
                try:
                    rec = self._dec(rec)
                except Exception:
                    return {"corrupt": True}      # tampered / wrong key
            fields, crc_stored = rec[:_REC.size], struct.unpack_from("<I", rec, _REC.size)[0]
            if (zlib.crc32(fields) & 0xFFFFFFFF) != crc_stored:
                return {"corrupt": True}          # detected, but the slot stays wrong
            pid_, version, ts, value, check = _REC.unpack(fields)
            return {"pid": pid_, "version": version, "ts": ts, "value": value,
                    "consistent": check == (value ^ CHECK_MASK)}
        return {"stuck": True}                     # seqlock never settled (writer died mid-write)

    def close(self):
        self.buf.close()
