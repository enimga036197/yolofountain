# flock — a zero-config shared clipboard + file drop for your machines

Copy on any machine, paste on any other, instantly. Drop a file and it lands on every
machine. **No accounts, no cloud, no pairing, no server** — the machines just find each
other on a UDP multicast group (a LAN, or a ZeroTier overlay). Optional group password
encrypts everything.

```bash
python flock.py                         # run the daemon on each machine
python flock.py send report.pdf         # drop a file to every machine running flock
FLOCK_KEY=teamsecret python flock.py     # encrypted group (recommended)
```

Run the daemon on each of your machines; copy something on one and it's on the others.
`send` beams a file to everyone's `~/flock-drops`.

## Why it's better

Shared-clipboard tools usually need a Microsoft/Google account, a cloud relay, or the
same OS ecosystem. flock needs **none** of that:

- **Zero setup** — the whole cost of the incumbents (pair, sign in, negotiate a session)
  is deleted. There's no connection; machines just spray and catch.
- **Works over your ZeroTier fabric**, across heterogeneous machines (Windows now; the
  core is portable). Multicast rides the overlay.
- **Encrypted** with a group key — only your machines with `FLOCK_KEY` see the traffic.
- **Crash-safe & late-join** — a machine that comes online instantly picks up the
  current clipboard (soft-state); a crash never corrupts anything.
- **Large files survive the lossy connectionless channel** — drops ride YoloFountain, so
  a dropped file reconstructs from enough frames even with packet loss.

Built on **[softstate](../../softstate)** (the shared clipboard) + **[YoloFountain](../../)**
(the file drops).

## Honest limits

- **Text clipboard** (images aren't synced via the clipboard — use `send` for files).
- **Live delivery, not a queue** — a `send` sprays for a moment; receivers must be
  running to catch it (no store-and-forward yet).
- **Needs multicast** on the path — fine on a LAN and on ZeroTier; some corporate
  networks block it.
- Without `FLOCK_KEY` the clipboard is broadcast in cleartext on the group — set a key
  on any network you don't fully trust.

## Files

```
flock.py   the daemon + `send` CLI (clipboard via softstate, drops via YoloFountain)
clip.py    OS clipboard access (Windows via pywin32; a fake for tests/headless)
```
Tested (`tests/test_flock.py`): clipboard converges across nodes, no echo loop, older
updates ignored, file drop reconstructs on receivers — and it's been run end-to-end
over real UDP multicast.
