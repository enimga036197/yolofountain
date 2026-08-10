"""
Runnable demo: move a file through a simulated lossy one-way channel.

    python examples/file_transfer.py                 # random 40 KB payload
    python examples/file_transfer.py some/file.bin    # a real file
    python examples/file_transfer.py some/file.bin hunter2   # + password

The "channel" here just drops 30% of frames and shuffles arrival — stand in for a
camera, a radio, a magnetic field. There is no path from receiver back to sender.
"""
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import beam

path = sys.argv[1] if len(sys.argv) > 1 else None
password = sys.argv[2] if len(sys.argv) > 2 else None

if path:
    data = open(path, "rb").read()
    name = os.path.basename(path)
    tx = beam.Sender.from_files([(name, data)], block_size=1024, password=password)
else:
    data = os.urandom(40 * 1024)
    name = "random.bin"
    tx = beam.Sender.from_files([(name, data)], block_size=1024, password=password)

print(f"sending {name}: {len(data)} bytes  ->  {tx.n_blocks} blocks"
      f"{'  (encrypted)' if password else ''}")

rng = random.Random(1)
LOSS = 0.30
rx = beam.Receiver()
sent = received = 0
i = 0
while not rx.done and i < tx.n_blocks * 20:
    frame = tx.frame(i); i += 1; sent += 1
    if rng.random() < LOSS:                 # one-way channel drops this droplet
        continue
    received += 1
    rx.add(frame)

if rx.done:
    out = rx.files(password=password)[0]
    ok = out["bytes"] == data
    ovhd = received / tx.n_blocks
    print(f"done: {received} frames used ({ovhd:.2f}x overhead) after {sent} sent "
          f"through {int(LOSS*100)}% loss")
    print("byte-exact:", ok)
    sys.exit(0 if ok else 1)
else:
    print("did not complete"); sys.exit(1)
