/*
 * yolo_core.c — the SIMD + hot-path core for YoloFountain.
 *
 * Ports the per-frame work that capped the Python encoder:
 *   - yolo_xor         : runtime-dispatched AVX2 XOR (SSE2 fallback). Decode peeling.
 *   - yolo_blocks      : deterministic droplet block-selection (mulberry32 + robust
 *                        soliton), so the decoder doesn't run the PRNG in Python.
 *   - yolo_encode_full : one call builds a whole frame — header, block-selection,
 *                        XOR, and CRC-32 (matches zlib.crc32) — no per-frame Python.
 *
 * Everything is bit-for-bit identical to the pure-Python path (tests assert it);
 * the mulberry32 and robust-soliton constants and the frame/CRC layout all match
 * SPEC.md. Build: `python build.py` (MSVC or gcc/clang). Optional accelerator.
 */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <immintrin.h>

#ifdef _MSC_VER
#include <intrin.h>
#define EXPORT __declspec(dllexport)
#define AVX2_TARGET
#else
#include <cpuid.h>
#define EXPORT __attribute__((visibility("default")))
#define AVX2_TARGET __attribute__((target("avx2")))
#endif

/* ---------------- AVX2 detection (OSXSAVE + XCR0 + CPUID.7:EBX.AVX2) --------- */
static int cpu_has_avx2(void)
{
#ifdef _MSC_VER
    int r[4];
    __cpuid(r, 1);
    if (!((r[2] >> 27) & 1)) return 0;
    if ((_xgetbv(0) & 0x6) != 0x6) return 0;
    __cpuidex(r, 7, 0);
    return (r[1] >> 5) & 1;
#else
    unsigned a, b, c, d;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return 0;
    if (!((c >> 27) & 1)) return 0;
    unsigned eax, edx;
    __asm__ volatile ("xgetbv" : "=a"(eax), "=d"(edx) : "c"(0));
    if ((eax & 0x6) != 0x6) return 0;
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return 0;
    return (b >> 5) & 1;
#endif
}

/* ---------------- vectorised XOR -------------------------------------------- */
AVX2_TARGET
static void xor_avx2(uint8_t *dst, const uint8_t *src, size_t n)
{
    size_t i = 0;
    for (; i + 128 <= n; i += 128)
        for (int k = 0; k < 128; k += 32) {
            __m256i a = _mm256_loadu_si256((const __m256i *)(dst + i + k));
            __m256i b = _mm256_loadu_si256((const __m256i *)(src + i + k));
            _mm256_storeu_si256((__m256i *)(dst + i + k), _mm256_xor_si256(a, b));
        }
    for (; i + 32 <= n; i += 32) {
        __m256i a = _mm256_loadu_si256((const __m256i *)(dst + i));
        __m256i b = _mm256_loadu_si256((const __m256i *)(src + i));
        _mm256_storeu_si256((__m256i *)(dst + i), _mm256_xor_si256(a, b));
    }
    for (; i + 8 <= n; i += 8) {
        uint64_t a, b; memcpy(&a, dst + i, 8); memcpy(&b, src + i, 8);
        a ^= b; memcpy(dst + i, &a, 8);
    }
    for (; i < n; i++) dst[i] ^= src[i];
}

static void xor_scalar(uint8_t *dst, const uint8_t *src, size_t n)
{
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t a, b; memcpy(&a, dst + i, 8); memcpy(&b, src + i, 8);
        a ^= b; memcpy(dst + i, &a, 8);
    }
    for (; i < n; i++) dst[i] ^= src[i];
}

typedef void (*xor_fn)(uint8_t *, const uint8_t *, size_t);
static xor_fn g_xor = 0;
static xor_fn resolve(void)
{
    if (!g_xor) g_xor = cpu_has_avx2() ? xor_avx2 : xor_scalar;
    return g_xor;
}

EXPORT void yolo_xor(uint8_t *dst, const uint8_t *src, size_t n) { resolve()(dst, src, n); }

/* ---------------- mulberry32 (must match codec.mulberry32) ------------------ */
static double mul32(uint32_t *s)
{
    *s = *s + 0x6D2B79F5u;
    uint32_t t = *s;
    t = (t ^ (t >> 15)) * (1u | *s);
    t = (t + ((t ^ (t >> 7)) * (61u | t))) ^ t;
    return (double)(t ^ (t >> 14)) / 4294967296.0;
}

/* Which source blocks XOR into a frame — identical to codec.blocks_for_frame.
   `out` must hold up to N indices; returns the count. */
static uint32_t select_blocks(uint32_t frame_index, uint32_t N,
                              const double *cdf, uint32_t *out)
{
    if (N <= 4) { out[0] = frame_index % N; return 1; }
    if (frame_index < N) { out[0] = frame_index; return 1; }
    uint32_t state = (uint32_t)((frame_index + 1u) * 2654435761u);
    double u = mul32(&state);
    uint32_t lo = 1, hi = N;                       /* pick_degree: binary search cdf */
    while (lo < hi) { uint32_t m = (lo + hi) >> 1; if (cdf[m] < u) lo = m + 1; else hi = m; }
    uint32_t d = lo < N ? lo : N;
    uint32_t count = 0, tries = 0, cap = d * 40;
    while (count < d && tries < cap) {
        uint32_t b = (uint32_t)(mul32(&state) * (double)N) % N;
        uint32_t k, dup = 0;
        for (k = 0; k < count; k++) if (out[k] == b) { dup = 1; break; }
        if (!dup) out[count++] = b;
        tries++;
    }
    return count;
}

EXPORT uint32_t yolo_blocks(uint32_t frame_index, uint32_t N, const double *cdf, uint32_t *out)
{
    return select_blocks(frame_index, N, cdf, out);
}

/* ---------------- CRC-32 (IEEE / zlib-compatible) --------------------------- */
static uint32_t crc_tab[256];
static int crc_ready = 0;
static void crc_init(void)
{
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        crc_tab[i] = c;
    }
    crc_ready = 1;
}
static uint32_t crc32_calc(const uint8_t *buf, size_t n)
{
    if (!crc_ready) crc_init();
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = crc_tab[(c ^ buf[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

/* ---------------- whole-frame encode (header + select + XOR + CRC) ---------- */
/* out: 16 + K + 4 bytes. scratch: uint32[>=N] work buffer. cdf: NULL when N<=4. */
EXPORT void yolo_encode_full(uint8_t *out, uint8_t m0, uint8_t m1, uint8_t ver, uint8_t flags,
                             uint16_t session, uint32_t total_len, uint32_t K,
                             uint32_t frame_index, uint32_t N,
                             const uint8_t *payload, uint64_t plen,
                             const double *cdf, uint32_t *scratch)
{
    out[0] = m0; out[1] = m1; out[2] = ver; out[3] = flags;
    out[4] = session & 0xFF; out[5] = (session >> 8) & 0xFF;
    out[6] = total_len & 0xFF; out[7] = (total_len >> 8) & 0xFF;
    out[8] = (total_len >> 16) & 0xFF; out[9] = (total_len >> 24) & 0xFF;
    out[10] = K & 0xFF; out[11] = (K >> 8) & 0xFF;
    out[12] = frame_index & 0xFF; out[13] = (frame_index >> 8) & 0xFF;
    out[14] = (frame_index >> 16) & 0xFF; out[15] = (frame_index >> 24) & 0xFF;

    uint8_t *body = out + 16;
    memset(body, 0, K);
    uint32_t cnt = select_blocks(frame_index, N, cdf, scratch);
    xor_fn xf = resolve();
    for (uint32_t j = 0; j < cnt; j++) {
        uint64_t off = (uint64_t)scratch[j] * K;
        if (off >= plen) continue;
        uint64_t n = (off + K > plen) ? (plen - off) : K;
        xf(body, payload + off, (size_t)n);
    }
    uint32_t crc = crc32_calc(out, 16 + K);
    out[16 + K] = crc & 0xFF; out[16 + K + 1] = (crc >> 8) & 0xFF;
    out[16 + K + 2] = (crc >> 16) & 0xFF; out[16 + K + 3] = (crc >> 24) & 0xFF;
}

EXPORT int yolo_has_avx2(void) { return cpu_has_avx2(); }
