# statusbus — connectionless status/presence over YoloFountain

Status/presence is the most **ubiquitous fake-2-way flow** there is: every dashboard,
every "is it online," every heartbeat and presence dot is built on a polling loop or a
persistent socket — a 2-way connection whose *response* (current state) never
correlates to the *request* (the poll). The reply is the other side's own content, not
a function of anything you sent. So it was never a conversation, and the connection is
ceremony.

This is that flow done connectionless. Each producer **sprays** its current state as an
epoch'd rateless transfer; consumers demux by producer, reconstruct the latest complete
state, and age out producers that go quiet. **No host, no connections, no join protocol,
no locks.** A frame that fails its CRC is dropped (never delivered as data); the
continuous spray heals transient loss or corruption on the next epoch. Producers and
consumers appear and vanish freely — churn is just starting and stopping.

The same code runs over LAN multicast, a radio, or a physical carrier (magbeam/qrbeam),
unchanged. That's the agnosticism paying rent.

## The benchmark — measured against the on-machine incumbents

We benchmark it *on the incumbents' home turf* (a single Windows box) against shared
memory in **two honest columns** — **raw** (fast, fragile) and **hardened** (seqlock +
per-record CRC + version + optional ChaCha, i.e. what it *costs* to be as safe as the
bus) — plus **WMI**, the Windows "ask the system for status" standard. Single-machine is
the *floor test*: if connectionless wins here, off-machine (where the incumbents can't
play) only gets more lopsided.

```
python applications/statusbus/bench.py
```

Measured (i3-12100F, Windows):

| axis | raw shared-mem | hardened shared-mem | rateless bus |
|---|---|---|---|
| read cost (one producer's state) | **0.28 µs** ✅ | 1.09 µs | 0.27 µs |
| torn reads under a hot writer | **117k / 954k (~12%)** ❌ | 0 torn (but ~81% reads *stuck*) | **0 (by construction)** ✅ |
| writer dies mid-update | **torn forever** ❌ | **slot wedged** (odd seqlock) ❌ | **last valid epoch stands** ✅ |
| bytes flipped in the medium | **delivered as data** ❌ | caught, but slot stays wrong | **rejected, heals next epoch** ✅ |
| loss on the medium (off-machine) | n/a (on-box only) | n/a (on-box only) | **converges to 85% loss** ✅ |
| crypto / update (auth+encrypt) | none built in | +3.5 µs | +6 µs (ChaCha, KDF amortized) |
| authenticate a writer, portably | ✗ | on-machine only | ✅ built in |
| runs off-machine / over a carrier | ✗ | ✗ | ✅ same code |
| ask-for-status latency (WMI) | — | — | **~sub-µs vs WMI's 95 ms/query** |

## The honest verdict

- **Raw shared memory** wins raw read latency — and loses everything about *safety*:
  torn reads, corruption delivered as data, a crash leaves the slot torn forever, no
  crypto. Fast precisely because it does none of the safety work.
- **Hardened shared memory** closes the integrity gap (seqlock kills torn reads, CRC
  catches corruption) — at a cost, and it *still* wedges its slot when a writer dies
  mid-write, can't authenticate a writer portably, and never leaves the machine.
- **The rateless bus** never delivers torn or corrupt data, self-heals loss *and*
  crashes, has built-in optional authenticated encryption, is connectionless, and the
  same code runs off-box. It costs freshness (update→visible latency is bounded by the
  spray period) and more CPU per update (it's doing framing + fountain + CRC).

**The thesis, in one line:** *speed is cheap when you skip safety; at equal safety the
bus gets self-healing, integrity, atomic versions and portable crypto essentially for
free — and the incumbents bolt each one on and still can't leave the box.*

## Files

```
bus.py         Producer / Consumer + an in-memory lossy medium (fault injection) and the crypto path
sharedmem.py   RawStore (fragile) and HardenedStore (seqlock + CRC + version + ChaCha)
bench.py       freshness, torn-reads, writer-crash, corruption, loss-convergence, crypto cost, WMI
```

Built on [YoloFountain](../../). Small state is one systematic frame (no fountain math);
a large join-bootstrap dump is where the fountain actually earns its keep — one spray,
every late joiner self-catches from the same stream with no per-device catch-up.
