"""
yolofountain._native — optional ctypes binding to the SIMD C core.

If ``_yolocore.{dll,so,dylib}`` is present next to this file (built via build.cmd /
build.sh), the codec routes its bulk XOR through it. If not, ``AVAILABLE`` is False
and the codec uses the pure-Python path — same results, just slower. Nothing else
imports this directly; ``codec`` checks ``AVAILABLE``.
"""
import ctypes
import glob
import os

AVAILABLE = False
HAS_AVX2 = False
_lib = None


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
        lib.yolo_xor.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        lib.yolo_encode_frame.restype = None
        lib.yolo_encode_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                          ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32]
        lib.yolo_has_avx2.restype = ctypes.c_int
        _lib = lib
        HAS_AVX2 = bool(lib.yolo_has_avx2())
        AVAILABLE = True
    except OSError:
        pass


_load()


def _addr(buf):
    """Base address of a bytes/bytearray buffer, no copy. Caller keeps `buf` alive."""
    if isinstance(buf, bytes):
        return ctypes.cast(ctypes.c_char_p(buf), ctypes.c_void_p).value
    return ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))


def xor_into(dst, src, src_off, n):
    """dst[0:n] ^= src[src_off:src_off+n]  (dst is a writable bytearray)."""
    _lib.yolo_xor(_addr(dst), _addr(src) + src_off, n)


def encode_frame_body(K, payload, idxs):
    """Return a K-byte droplet body = XOR of payload blocks named in idxs."""
    out = bytearray(K)
    arr = (ctypes.c_uint32 * len(idxs))(*idxs) if idxs else (ctypes.c_uint32 * 0)()
    _lib.yolo_encode_frame(_addr(out), K, _addr(payload), len(payload),
                           ctypes.cast(arr, ctypes.c_void_p), len(idxs))
    return out
