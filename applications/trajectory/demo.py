"""
Trajectory-control demo: a chaotic follower kept on course by a lossy absolute-state
fountain.

    python demo.py            # prints tracking error for each scenario (+ plot if matplotlib)

Scenarios, all on the same chaotic Lorenz system:
  - OPEN-LOOP        no reference -> the follower's model error diverges (chaos)
  - FOUNTAIN 0/40/80% loss   absolute snapshots keep it locked despite heavy loss
  - LATE-JOIN        the follower is kicked to a random state mid-run -> it re-converges
  - TORN vs CRC      what happens if the transport delivers a corrupt target (raw
                     shared-mem style) vs the fountain rejecting it
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trajectory import Authority, Follower, dist   # noqa: E402

DT = 0.005
STEPS = 4000
SNAP_EVERY = 15          # emit a snapshot every 15 sim steps


def simulate(loss=0.0, corrupt=0.0, deliver_torn=False, late_join_at=None, seed=1):
    rng = random.Random(seed)
    auth = Authority()
    foll = Follower()
    # warm the truth off the transient so we're on the attractor
    for _ in range(300):
        auth.step(DT)
    foll.state = (5.0, 5.0, 5.0)
    errs = []
    recovery = None
    for i in range(STEPS):
        auth.step(DT)
        foll.step(DT)
        if late_join_at is not None and i == late_join_at:
            foll.state = tuple(rng.uniform(-25, 25) for _ in range(3))   # glitch / fresh join
        if i % SNAP_EVERY == 0 and loss < 1.0 or i % SNAP_EVERY == 0 and deliver_torn:
            for f in auth.snapshot_frames():
                if rng.random() < loss:
                    continue
                if corrupt and rng.random() < corrupt:
                    b = bytearray(f); bit = rng.randrange(len(b) * 8); b[bit // 8] ^= 1 << (bit % 8)
                    f = bytes(b)
                foll.receive(f)                # fountain path: CRC drops corrupt frames
            if deliver_torn and i % (SNAP_EVERY * 8) == 0:
                foll.force_reconcile(tuple(rng.uniform(-25, 25) for _ in range(3)))  # no-integrity transport
        e = dist(foll.state, auth.state)
        errs.append(e)
        if late_join_at is not None and i > late_join_at and recovery is None and e < 2.0:
            recovery = (i - late_join_at) * DT
    mean = sum(errs) / len(errs)
    tail = errs[STEPS // 2:]                    # steady-state (after any warm-in)
    return {"errs": errs, "mean": mean, "tail_mean": sum(tail) / len(tail),
            "max": max(errs), "recovery": recovery, "corrupt_used": foll.corrupt_delivered}


def main():
    print("Lorenz trajectory control — follower model error 3% (open-loop it diverges)\n")
    rows = []
    print(f"  {'scenario':22} {'mean err':>9} {'steady err':>11} {'max err':>9}")

    def show(label, r):
        print(f"  {label:22} {r['mean']:9.2f} {r['tail_mean']:11.2f} {r['max']:9.2f}")
        rows.append((label, r))

    show("open-loop (no ref)", simulate(loss=1.0))               # loss=1 -> no snapshots land
    show("fountain, 0% loss", simulate(loss=0.0))
    show("fountain, 40% loss", simulate(loss=0.4))
    show("fountain, 80% loss", simulate(loss=0.8))
    lj = simulate(loss=0.3, late_join_at=STEPS // 2)
    show("late-join @ mid", lj)
    print(f"     -> re-converged {lj['recovery']:.2f} sim-time after being kicked to a random state")

    print("\n  integrity matters for CONTROL (a corrupt setpoint is worse than a missing one):")
    fnt = simulate(loss=0.3, corrupt=0.25)      # 25% of frames corrupted, but CRC drops them
    torn = simulate(loss=0.3, corrupt=0.0, deliver_torn=True)  # transport delivers torn targets
    print(f"    fountain (25% frames corrupted, CRC-dropped): steady err {fnt['tail_mean']:6.2f}  "
          f"-> stays locked")
    print(f"    raw transport delivers torn targets ({torn['corrupt_used']} of them): "
          f"steady err {torn['tail_mean']:6.2f}  -> destabilised")

    print("\n  verdict: a self-running deterministic process needs only an ABSOLUTE reference,")
    print("  robustly, whenever — exactly what a rateless code gives. Loss is free (the next")
    print("  snapshot is the whole truth); late-join is free; and CRC-gating means the")
    print("  controller never chases a corrupt setpoint. The fountain carries the target;")
    print("  the follower does the smoothing.")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        t = [i * DT for i in range(STEPS)]
        plt.figure(figsize=(9, 4.5))
        for label, r in rows:
            plt.plot(t, r["errs"], lw=1, label=label)
        plt.yscale("log"); plt.xlabel("sim time"); plt.ylabel("tracking error  |follower - truth|")
        plt.title("Trajectory control over a lossy absolute-state fountain (Lorenz)")
        plt.legend(fontsize=8); plt.tight_layout()
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracking.png")
        plt.savefig(out, dpi=110)
        print(f"\n  wrote {out}")
    except ImportError:
        print("\n  (pip install matplotlib for the tracking-error plot)")


if __name__ == "__main__":
    main()
