# beam wire format (v1)

A **beam** transfer is a one-way, rateless stream of self-describing **frames**. The
sender emits `frame(0), frame(1), …` and never stops on its own; a receiver
reconstructs the payload once it has caught enough distinct frames. There is no
handshake, acknowledgement, or retransmit — the receiver need not be able to talk
back at all.

This document specifies the bytes on the wire so independent implementations
interoperate. All multi-byte integers are **little-endian**.

## 1. Frame

```
offset  size  field
  0      1    magic0      = 0xB5
  1      1    magic1      = 0x1C
  2      1    version     = 1
  3      1    flags       bit0 = GZIP, bit1 = ENCRYPTED, others reserved (0)
  4      2    session     u16   — identifies this transfer (see §4)
  6      4    total_len   u32   — length in bytes of the processed payload (§5)
 10      2    K           u16   — block size in bytes
 12      4    frame_index u32   — which droplet this is (§3)
 16      K    body        K bytes — XOR of the selected source blocks
16+K     4    crc32       u32   — CRC-32 (zlib/ISO-HDLC) over bytes[0 .. 16+K)
```

Frame length is `16 + K + 4`. A receiver MUST reject any frame whose magic,
version, length, or CRC-32 does not check out — a rejected frame is never fed to the
decoder, which is what prevents a corrupt droplet from poisoning the reconstruction.
(On carriers with their own error correction, e.g. QR's Reed–Solomon, the CRC is
belt-and-suspenders; on a bare channel it is the only integrity check.)

## 2. Blocks

The processed payload of length `total_len` is divided into `N = ceil(total_len / K)`
source blocks of `K` bytes each (the final block is zero-padded to `K` internally;
only `total_len` bytes are emitted at the end). Both sides derive `N` from the
header, so any frame bootstraps a receiver.

## 3. Droplet selection (`blocks_for_frame(i, N)`)

Deterministic from the frame index alone — **nothing about block selection is on the
wire**. Given `i` and `N`:

- **`N ≤ 4`** → `[ i mod N ]` (round-robin; LT coding is degenerate this small).
- **`i < N`** → `[ i ]` — *systematic*: frame `i` is the raw block `i`, so a clean
  channel decodes at exactly 1.0× overhead.
- **`i ≥ N`** → an LT repair droplet:
  1. `rng = mulberry32((i + 1) * 2654435761 mod 2^32)`
  2. `d = min(N, pick_degree(soliton_cdf(N), rng))`
  3. draw `d` **distinct** block indices by repeating `b = floor(rng() * N) mod N`,
     skipping duplicates, up to `d * 40` attempts.

The body is the XOR of block bytes at those indices.

### mulberry32 (32-bit PRNG)
```
state = seed & 0xFFFFFFFF
next():
    state = (state + 0x6D2B79F5) & 0xFFFFFFFF
    t = state
    t = imul(t ^ (t >> 15), 1 | state)
    t = ((t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
    return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 2^32      # in [0, 1)
```
`imul(a, b) = (a * b) & 0xFFFFFFFF`.

### Robust soliton CDF (`c = 0.05`, `δ = 0.9`)
```
ρ(1) = 1/N ;  ρ(d) = 1 / (d·(d−1))  for d = 2..N
S  = c · ln(N/δ) · sqrt(N)
kd = max(1, round(N / S))
τ(d) = S / (N·d)  for d = 1..kd−1 ;  τ(kd) += S · ln(S/δ) / N
Z    = Σ (ρ(d) + τ(d))
CDF(d) = Σ_{j≤d} (ρ(j) + τ(j)) / Z ,  CDF(N) = 1
pick_degree: smallest d with CDF(d) ≥ rng()
```

## 4. Session

A transfer is identified by the pair `(session, total_len)`. A receiver seeing a
frame whose pair differs from the one it is currently assembling MUST start a fresh
decode. `session` is a random `u16` chosen by the sender; concurrent senders in one
channel SHOULD pick distinct values.

## 5. Payload processing

The `total_len` payload the fountain carries is produced from the user's data by, in
order:

1. **Container** (optional, for multiple files) — §6.
2. **gzip** — if it shrinks the data, apply it and set `flags.GZIP`.
3. **Encryption** — if a password is given, replace the payload with the encryption
   envelope (§7) and set `flags.ENCRYPTED`.

A receiver reverses this after reconstruction: **decrypt** (if `ENCRYPTED`), then
**gunzip** (if `GZIP`), then parse the container if present.

## 6. Container (`BEAM`)

```
'B','E','A','M'          (4 bytes)
manifest_len             u32
manifest                 UTF-8 JSON: [ {"n": name, "s": size, "t": mime}, … ]
file bytes               each file's bytes, concatenated in manifest order
```

## 7. Encryption envelope (`BEC1`)

```
'B','E','C','1'          (4 bytes)
salt                     16 bytes
nonce                    12 bytes
ciphertext               ChaCha20-Poly1305(plaintext), tag appended
```
`key = scrypt(password, salt, N = 2^15, r = 8, p = 1, dkLen = 32)`.
Authenticated: a wrong password or any tampering fails the tag, so decryption errors
rather than returning garbage. The password is never transmitted; possession of the
stream alone yields nothing.

## Limits (v1)

`total_len` ≤ 4 GiB (u32); `K` ≤ 65535 (u16); the whole payload is held in memory on
both sides (no streaming). Designed for small-to-medium files, which is the niche.
