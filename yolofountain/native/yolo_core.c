/*
 * yolo_core.c — the SIMD hot loop for YoloFountain.
 *
 * The whole codec's bulk work is XOR of aligned blocks (frame assembly on the
 * encoder, peeling on the decoder). That's a memory-bandwidth-bound SoA workload,
 * so a vectorised XOR takes encode/decode from Python's per-byte crawl to RAM
 * speed. Runtime-dispatched: AVX2 when the CPU has it (helps cache-resident decode
 * peeling), an auto-vectorising scalar path (SSE2 baseline) everywhere else.
 *
 * Build:  MSVC  -> build.cmd     (cl /O2 /LD)
 *         gcc   -> build.sh      (cc -O3 -shared -fPIC)
 * The Python package runs fine without it — this is a pure accelerator.
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

/* Full, correct AVX2 detection: OSXSAVE + XCR0(YMM|XMM) + CPUID.7:EBX.AVX2 */
static int cpu_has_avx2(void)
{
#ifdef _MSC_VER
    int r[4];
    __cpuid(r, 1);
    if (!((r[2] >> 27) & 1)) return 0;               /* OSXSAVE */
    if ((_xgetbv(0) & 0x6) != 0x6) return 0;          /* OS saves YMM+XMM */
    __cpuidex(r, 7, 0);
    return (r[1] >> 5) & 1;                            /* AVX2 */
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

AVX2_TARGET
static void xor_avx2(uint8_t *dst, const uint8_t *src, size_t n)
{
    size_t i = 0;
    for (; i + 128 <= n; i += 128) {                  /* 4x unrolled 32B lanes */
        for (int k = 0; k < 128; k += 32) {
            __m256i a = _mm256_loadu_si256((const __m256i *)(dst + i + k));
            __m256i b = _mm256_loadu_si256((const __m256i *)(src + i + k));
            _mm256_storeu_si256((__m256i *)(dst + i + k), _mm256_xor_si256(a, b));
        }
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

/* auto-vectorises to SSE2 (x86-64 baseline) at /O2 -O3 */
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

/* dst[0..n) ^= src[0..n) */
EXPORT void yolo_xor(uint8_t *dst, const uint8_t *src, size_t n)
{
    resolve()(dst, src, n);
}

/* Assemble one droplet body: out = XOR of the source blocks named in idxs.
   Handles the short final block via plen. One call does a whole frame. */
EXPORT void yolo_encode_frame(uint8_t *out, uint32_t K, const uint8_t *payload,
                              uint64_t plen, const uint32_t *idxs, uint32_t nidx)
{
    xor_fn xf = resolve();
    memset(out, 0, K);
    for (uint32_t j = 0; j < nidx; j++) {
        uint64_t off = (uint64_t)idxs[j] * K;
        if (off >= plen) continue;
        uint64_t n = (off + K > plen) ? (plen - off) : K;
        xf(out, payload + off, (size_t)n);
    }
}

EXPORT int yolo_has_avx2(void) { return cpu_has_avx2(); }
