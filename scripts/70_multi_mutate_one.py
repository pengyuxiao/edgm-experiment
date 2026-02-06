#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]

def run_arm(arm, inp, outp, drop_dir):
    cmd = [
        "python3", "scripts/60_run_arm_one.py",
        "--arm", arm,
        "--in", str(inp.resolve()),
        "--out", str(outp.resolve()),
        "--require-error", "none",
        "--drop-dir", str(drop_dir.resolve()),
    ]
    return subprocess.run(cmd).returncode == 0 and outp.exists()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--arms", required=True,
                    help="comma-separated arms")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default="data/tmp_chain")
    args = ap.parse_args()

    seed = Path(args.seed).resolve()
    arms = args.arms.split(",")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    cur = seed
    successful = []

    for i, arm in enumerate(arms):
        nxt = workdir / f"step{i+1}.c"
        ok = run_arm(arm, cur, nxt, workdir / "drop")
        if not ok:
            break
        successful.append(arm)
        cur = nxt

    if not successful:
        print("[FAIL] no mutator applied")
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(cur, out)

    meta = {
        "seed": str(seed),
        "attempted_arms": arms,
        "applied_arms": successful,
        "num_applied": len(successful),
    }
    out.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=2)
    )

    print(f"[OK] final={out} applied={successful}")

if __name__ == "__main__":
    main()
