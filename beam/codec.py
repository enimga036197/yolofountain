"""
beam.codec — the carrier-agnostic fountain transfer core.

A *rateless* one-way file codec: the sender emits droplet frames forever
(``frame(0), frame(1), ...``); a receiver catches whatever arrives — frames may be
lost, reordered, duplicated, or joined late — and reconstructs the payload once it
has enough distinct droplets (~1.0-1.4x the data). No handshake, no ACK, no
retransmit request. Ideal for any *write-only* channel: broadcast, data diodes,
optical (screen->camera), acoustic, magnetic side-channels, satellite downlink.

The algorithm (systematic LT fountain, deterministic block selection, self-
describing frames) is the core distilled from qrbeam. Two things are added here so
it stands alone on a bare channel: a **per-frame CRC-32** (qrbeam relied on the QR
layer's Reed-Solomon), and gzip/encryption flags in a versioned header.

Wire format is specified in SPEC.md. Nothing in this module knows about QR, base45,
audio, or any physical medium — carriers are adapters (see ``beam.carriers``).
"""
import struct
import zlib
import gzip as _gzip
import json

MAGIC0, MAGIC1 = 0xB5, 0x1C
VERSION = 1
HDR = 16
CRC = 4
FLAG_GZIP = 1
FLAG_ENC = 2
CMAGIC = b"BEAM"                      # multi-file container magic

_HDR = struct.Struct("<BBBBHIHI")     # magic0,magic1,ver,flags,session,totalLen,K,frameIndex  = 16 bytes


# ---------- deterministic PRNG (mulberry32) ----------
def _imul(a, b):
    return (a * b) & 0xFFFFFFFF


def mulberry32(seed):
    """The exact 32-bit PRNG qrbeam uses. Seeded from the frame index alone, so the
    encoder and decoder agree on block selection with nothing on the wire."""
    state = seed & 0xFFFFFFFF

    def rng():
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = state
        t = _imul(t ^ (t >> 15), 1 | state)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        t &= 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0
    return rng


# ---------- robust soliton degree distribution ----------
def soliton_cdf(N, c=0.05, delta=0.9):
    import math
    rho = [0.0] * (N + 1)
    tau = [0.0] * (N + 1)
    rho[1] = 1.0 / N
    for d in range(2, N + 1):
        rho[d] = 1.0 / (d * (d - 1))
    S = c * math.log(N / delta) * math.sqrt(N)
    if not (S > 0):
        S = 1.0
    kd = max(1, round(N / S))
    for d in range(1, min(kd, N + 1)):
        tau[d] = S / (N * d)
    if kd <= N:
        tau[kd] += S * math.log(S / delta) / N
    Z = sum(rho[d] + tau[d] for d in range(1, N + 1))
    cdf = [0.0] * (N + 1)
    acc = 0.0
    for d in range(1, N + 1):
        acc += (rho[d] + tau[d]) / Z
        cdf[d] = acc
    cdf[N] = 1.0
    return cdf


def _pick_degree(cdf, N, rnd):
    u = rnd()
    lo, hi = 1, N
    while lo < hi:
        mid = (lo + hi) >> 1
        if cdf[mid] < u:
            lo = mid + 1
        else:
            hi = mid
    return lo


def blocks_for_frame(frame_index, N, cdf):
    """Which source blocks XOR into frame ``frame_index``. Deterministic — both
    sides compute it from the index alone.

    * ``frame_index < N``  -> systematic: the raw block, so a clean pass decodes at
      exactly 1.0x overhead (no fountain tax when the channel is clean).
    * ``frame_index >= N`` -> LT repair droplet, degree drawn from the robust soliton.
    * ``N <= 4``           -> plain round-robin (LT is degenerate that small).
    """
    if N <= 4:
        return [frame_index % N]
    if frame_index < N:
        return [frame_index]
    rnd = mulberry32(_imul(frame_index + 1, 2654435761))
    d = min(N, _pick_degree(cdf, N, rnd))
    out, seen, tries = [], set(), 0
    while len(out) < d and tries < d * 40:
        b = int(rnd() * N) % N
        if b not in seen:
            seen.add(b)
            out.append(b)
        tries += 1
    return out


def _xor_into(dst, src, src_off, length):
    for i in range(length):
        dst[i] ^= src[src_off + i]


# ---------- frame (de)serialisation ----------
def pack_frame(session, flags, total_len, K, frame_index, body):
    assert len(body) == K, "body must be exactly K bytes"
    buf = _HDR.pack(MAGIC0, MAGIC1, VERSION, flags & 0xFF,
                    session & 0xFFFF, total_len, K & 0xFFFF, frame_index) + bytes(body)
    return buf + struct.pack("<I", zlib.crc32(buf) & 0xFFFFFFFF)


def unpack_frame(raw):
    """Validate magic + version + CRC-32; return the header fields + body, or None
    if the frame is malformed or corrupt. A corrupt frame is *rejected* here, never
    fed to the decoder — that is what keeps the fountain from being poisoned."""
    if len(raw) < HDR + CRC or raw[0] != MAGIC0 or raw[1] != MAGIC1 or raw[2] != VERSION:
        return None
    m0, m1, ver, flags, session, total_len, K, frame_index = _HDR.unpack_from(raw, 0)
    if len(raw) != HDR + K + CRC:
        return None
    stored = struct.unpack_from("<I", raw, HDR + K)[0]
    if (zlib.crc32(raw[:HDR + K]) & 0xFFFFFFFF) != stored:
        return None
    return {"flags": flags, "session": session, "total_len": total_len,
            "K": K, "frame_index": frame_index, "body": bytes(raw[HDR:HDR + K])}


# ---------- encoder ----------
class Encoder:
    def __init__(self, payload, K, session=0, flags=0):
        self.payload = payload
        self.K = K
        self.N = max(1, (len(payload) + K - 1) // K)
        self.session = session
        self.flags = flags
        self.cdf = soliton_cdf(self.N) if self.N > 4 else None

    def frame(self, frame_index):
        body = bytearray(self.K)
        for bi in blocks_for_frame(frame_index, self.N, self.cdf):
            off = bi * self.K
            n = min(self.K, len(self.payload) - off)
            if n > 0:
                _xor_into(body, self.payload, off, n)
        return pack_frame(self.session, self.flags, len(self.payload), self.K, frame_index, body)


# ---------- decoder (peeling / belief propagation) ----------
class _Edge:
    __slots__ = ("idxs", "data", "dead")

    def __init__(self, idxs, data):
        self.idxs = idxs
        self.data = data
        self.dead = False


class Decoder:
    def __init__(self, session, total_len, K, flags=0):
        self.session = session
        self.total_len = total_len
        self.K = K
        self.flags = flags
        self.N = max(1, (total_len + K - 1) // K)
        self.data = bytearray(self.N * K)
        self.have = bytearray(self.N)
        self.solved = 0
        self.by_idx = {}
        self.cdf = soliton_cdf(self.N) if self.N > 4 else None
        self.frames_used = 0

    def add_frame_bytes(self, raw):
        hdr = unpack_frame(raw)
        if not hdr or hdr["session"] != self.session or hdr["total_len"] != self.total_len:
            return False
        return self.ingest(hdr["frame_index"], bytearray(hdr["body"]))

    def ingest(self, frame_index, data):
        self.frames_used += 1
        before = self.solved
        s = set()
        for bi in blocks_for_frame(frame_index, self.N, self.cdf):
            if self.have[bi]:
                _xor_into(data, self.data, bi * self.K, self.K)
            elif bi in s:
                s.discard(bi)                       # XORed twice -> cancels
            else:
                s.add(bi)
        queue = []
        self._insert(s, data, queue)
        self._peel(queue)
        return self.solved > before

    def _insert(self, s, data, queue):
        if len(s) == 0:
            return
        if len(s) == 1:
            self._solve(next(iter(s)), data, queue)
            return
        e = _Edge(s, data)
        for bi in s:
            self.by_idx.setdefault(bi, set()).add(e)

    def _solve(self, j, data, queue):
        if self.have[j]:
            return
        self.data[j * self.K:(j + 1) * self.K] = data[0:self.K]
        self.have[j] = 1
        self.solved += 1
        queue.append(j)

    def _peel(self, queue):
        while queue:
            bi = queue.pop()
            users = self.by_idx.pop(bi, None)
            if not users:
                continue
            for e in list(users):
                if e.dead or bi not in e.idxs:
                    continue
                e.idxs.discard(bi)
                _xor_into(e.data, self.data, bi * self.K, self.K)
                if len(e.idxs) == 1:
                    e.dead = True
                    self._solve(next(iter(e.idxs)), e.data, queue)
                elif len(e.idxs) == 0:
                    e.dead = True

    def is_complete(self):
        return self.solved >= self.N

    def payload(self):
        return bytes(self.data[0:self.total_len])

    def progress(self):
        return self.solved / self.N if self.N else 0.0


# ---------- multi-file container ----------
def build_container(files):
    """files: iterable of (name, bytes) or (name, bytes, mime) -> one payload blob."""
    manifest, blobs = [], []
    for f in files:
        name, data = f[0], f[1]
        mime = f[2] if len(f) > 2 else ""
        manifest.append({"n": name, "s": len(data), "t": mime})
        blobs.append(bytes(data))
    mjson = json.dumps(manifest, separators=(",", ":")).encode()
    out = bytearray(CMAGIC)
    out += struct.pack("<I", len(mjson))
    out += mjson
    for b in blobs:
        out += b
    return bytes(out)


def parse_container(buf):
    """-> list of {'name','mime','bytes'}. Raises if not a container."""
    if buf[0:4] != CMAGIC:
        raise ValueError("not a beam container")
    mlen = struct.unpack_from("<I", buf, 4)[0]
    manifest = json.loads(buf[8:8 + mlen].decode())
    p, files = 8 + mlen, []
    for m in manifest:
        files.append({"name": m["n"], "mime": m["t"], "bytes": buf[p:p + m["s"]]})
        p += m["s"]
    return files


def gzip_bytes(data):
    return _gzip.compress(data, mtime=0)


def gunzip_bytes(data):
    return _gzip.decompress(data)
