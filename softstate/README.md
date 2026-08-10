# softstate — connectionless soft-state broadcast

Publish **absolute versioned state**; subscribe as either a change-notified **reader**
(a status/config store) or a reconciling **tracker** (trajectory control). It's *one*
primitive — the role is just what you do in the update callback.

Built on [YoloFountain](../), it inherits the whole suite for free:

- **Torn-free by construction** — a subscriber only ever commits a *complete, CRC-valid*
  version. Never a partial, torn, or corrupt state (a corrupt setpoint can destabilise
  a controller; this makes it impossible).
- **Loss is free** — state is *absolute*, not deltas, so a missed update costs nothing;
  the next one is the whole truth. No ACKs, no retransmit.
- **Late-join is free** — a subscriber that starts late reconstructs the current state
  from the ongoing spray. No catch-up protocol.
- **Crash-graceful** — a publisher that dies just goes quiet and ages out. No wedged
  lock, no torn slot (contrast the shared-memory incumbents — see
  [applications/statusbus](../applications/statusbus)).
- **Optional authenticated group encryption** (scrypt + ChaCha20-Poly1305), portable.
- **Carrier-agnostic** — the channel is a swappable seam: in-memory, UDP multicast, or
  a physical beam. Same publisher/subscriber code.

## Quickstart

```python
import softstate

ch  = softstate.UdpChannel()                         # connectionless LAN multicast
pub = softstate.Publisher(pid=1)                     # this node's id
sub = softstate.Subscriber(on_update=lambda pid, state, ver: print("update", pid, ver))
ch.subscribe(sub.feed)                               # background receive

for f in pub.frames(b"my current state"):            # publish an absolute snapshot
    ch.send(f)

sub.get(1)          # -> (b"my current state", version, ts)   READER role
sub.snapshot()      # -> {pid: (state, version, ts)}  (TTL-aged)
```

## `Store` — a durable, crash-safe key/value store

The everyday config/state store, built on the primitive. Each node owns a namespace
and holds a merged view of all nodes' keys. Without a channel it's a **crash-safe local
config store**; with one it's the *same* store, shared connectionlessly across a LAN.

```python
import softstate

cfg = softstate.Store("app.state.json", node_id=1)   # + channel=UdpChannel() to share
cfg.set("theme", "dark")
cfg.set("volume", 42)
cfg.get("theme")            # "dark"        (survives a full restart — it's on disk)
cfg.on_change(lambda key: reload(key))      # hot-reload hook
```

The persistence is the piece the incumbents get wrong. A crash mid-save must never
corrupt the store, so it writes to a temp file, `fsync`s, then **atomically replaces** —
on disk you only ever see the old complete file or the new one, never a torn one — keeps
the previous version as `.bak`, and checksums both so bit-rot is caught and the backup
used. That's the crash-safe store the "write the config file in place" and
shared-memory incumbents can't be (see [applications/statusbus](../applications/statusbus)
for the measured torn-read / crash-wedge failures it avoids).

## One primitive, two roles

The consumer's role is the *only* thing that differs — both sit on the same core:

- **Reader** (status/config): wire `on_update` to refresh a cache/UI, or just poll
  `get(pid)`. → [`applications/statusbus`](../applications/statusbus)
- **Tracker** (control): wire `on_update` to *reconcile* a self-running follower toward
  the latest state. → [`applications/trajectory`](../applications/trajectory)

Both of those apps are thin: they supply only how to serialize their state and what to
do on update. softstate handles transport, versioning, demux, integrity and crypto.

## API

| | |
|---|---|
| `Publisher(pid, K=256, redundancy=4, password=None)` | `.frames(state_bytes) -> [frame, …]` |
| `Subscriber(password=None, ttl=None, on_update=None)` | `.feed(frame) -> pid-or-None`, `.get(pid)`, `.snapshot()`, `.rejected` |
| `InMemoryChannel(loss=0, corrupt=0)` | `.subscribe(cb)`, `.send(frame)` — for tests/fault injection |
| `UdpChannel(group, port)` | `.subscribe(cb)` (bg thread), `.send(frame)`, `.close()` — real LAN |
| `Store(path, node_id, channel=None, password=None, refresh_interval=None)` | `.set(k,v)`, `.get(k)`, `.delete(k)`, `.keys()`, `.items()`, `.on_change(cb)` — durable KV |

State is opaque bytes — you serialize your own (a struct, JSON, msgpack). softstate
adds only a version + timestamp and manages latest-wins per publisher.

## Tests

```bash
python tests/test_softstate.py     # roundtrip, loss + late-join, corruption, crypto, TTL
```
