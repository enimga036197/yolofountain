"""
yolofountain._native — optional ctypes binding to the SIMD + hot-path C core.

If ``_yolocore.{dll,so,dylib}`` is present next to this file (built via build.py),
the codec routes block-selection, XOR, and whole-frame encode through it. Otherwise
``AVAILABLE`` is False and the pure-Python path is used — identical results, just
slower. Only ``codec`` imports this, and only behind an ``AVAILABLE`` check.
"""
import ctypes
import glob
import os

AVAILABLE = False
HAS_AVX2 = False
_lib = None

_c = ctypes
_DBL = _c.POINTER(_c.c_double)
_U32 = _c.POINTER(_c.c_uint32)


def _find():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("_yolocore.dll", "_yolocore.so", "_yolocore.dylib",
                 "libyolocore.so", "libyolocore.dylib"):
        p = os.path.join(here, name)
        if os.path.exists(p):
            return p
    hits = [x for x in glob.glob(os.path.join(here, "*yolocore*"))
            if x.endswith((".dll", ".so", ".dylib"))]
    return hits[0] if hits else None


def _load():
    global _lib, AVAILABLE, HAS_AVX2
    path = _find()
    if not path:
        return
    try:
        lib = ctypes.CDLL(path)
        lib.yolo_xor.restype = None
        lib.yolo_xor.argtypes = [_c.c_void_p, _c.c_void_p, _c.c_size_t]
        lib.yolo_blocks.restype = _c.c_uint32
        lib.yolo_blocks.argtypes = [_c.c_uint32, _c.c_uint32, _DBL, _U32]
        lib.yolo_encode_full.restype = None
        lib.yolo_encode_full.argtypes = [
            _c.c_void_p, _c.c_uint8, _c.c_uint8, _c.c_uint8, _c.c_uint8,
            _c.c_uint16, _c.c_uint32, _c.c_uint32, _c.c_uint32, _c.c_uint32,
            _c.c_void_p, _c.c_uint64, _DBL, _U32]
        lib.yolo_has_avx2.restype = _c.c_int
        _lib = lib
        HAS_AVX2 = bool(lib.yolo_has_avx2())
        AVAILABLE = True
    except OSError:
        pass


_load()


def _addr(buf):
    """Base address of a bytes/bytearray buffer, no copy. Caller keeps `buf` alive
    (and, for a bytearray, does not resize it — the address is cached across calls)."""
    if isinstance(buf, bytes):
        return ctypes.cast(ctypes.c_char_p(buf), ctypes.c_void_p).value
    return ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))


addr_of = _addr        # public: let Encoder cache payload/frame-buffer addresses once


# ---- reusable per-transfer buffers (built once by Encoder/Decoder) ----
def make_cdf(cdf_list):
    """Pack the robust-soliton CDF into a C double array (None -> NULL for N<=4)."""
    if not cdf_list:
        return None
    return (ctypes.c_double * len(cdf_list))(*cdf_list)


def make_scratch(n):
    """A reusable uint32[n] block-index work buffer."""
    return (ctypes.c_uint32 * max(1, n))()


# ---- hot-path calls ----
def xor_into(dst, src, src_off, n):
    """dst[0:n] ^= src[src_off:src_off+n]  (decode peeling)."""
    _lib.yolo_xor(_addr(dst), _addr(src) + src_off, n)


def blocks(frame_index, N, cdf_c, idx_c):
    """Fill idx_c with the droplet's source-block indices; return the count."""
    return _lib.yolo_blocks(frame_index, N, cdf_c, idx_c)


def encode_full(out_addr, m0, m1, ver, flags, session, total_len, K, frame_index, N,
                payload_addr, plen, cdf_c, scratch):
    """Build a whole frame at `out_addr` (a HDR+K+CRC buffer) in one C call. Uses
    pre-cached buffer addresses to keep per-frame Python overhead minimal."""
    _lib.yolo_encode_full(out_addr, m0, m1, ver, flags, session, total_len, K,
                          frame_index, N, payload_addr, plen, cdf_c, scratch)
