#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path
import json
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]

def run_arm(arm, inp, outp, drop_dir, strict=False, mutation_seed=None):
    cmd = [
        "python3", "scripts/60_run_arm_one.py",
        "--arm", arm,
        "--in", str(inp.resolve()),
        "--out", str(outp.resolve()),
        "--require-error", "none",
        "--drop-dir", str(drop_dir.resolve()),
    ]
    if strict:
        cmd.append("--strict-result")
        if mutation_seed is not None:
            cmd += ["--mutation-seed", str(mutation_seed)]
    return subprocess.run(cmd, cwd=ROOT).returncode == 0 and outp.is_file() and outp.stat().st_size > 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--arms", required=True,
                    help="comma-separated arms")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default="data/tmp_chain")
    ap.add_argument("--require-complete", action="store_true", help="Strict all-or-nothing chain; preserves legacy default")
    ap.add_argument("--mutation-seed", type=int, default=None)
    args = ap.parse_args()

    seed = Path(args.seed).resolve()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    workdir = Path(args.workdir)
    if args.require_complete and (Path(args.out).exists() or Path(str(args.out) + ".attempt.json").exists()):
        print(f"[ERR] strict output already exists: {args.out}", file=sys.stderr)
        return 2
    workdir.mkdir(parents=True, exist_ok=True)

    cur = seed
    successful = []
    attempted = []
    attempt_dir = workdir / uuid.uuid4().hex if args.require_complete else workdir
    attempt_dir.mkdir(parents=True, exist_ok=True)

    for i, arm in enumerate(arms):
        nxt = attempt_dir / f"step{i+1}.c"
        attempted.append(arm)
        step_seed = (args.mutation_seed + i) if args.mutation_seed is not None else None
        ok = run_arm(arm, cur, nxt, attempt_dir / "drop", strict=args.require_complete, mutation_seed=step_seed)
        if not ok:
            break
        successful.append(arm)
        cur = nxt

    out = Path(args.out)
    status = {"seed": str(seed), "selected_arms": arms, "attempted_arms": attempted,
              "applied_arms": successful, "num_applied": len(successful),
              "complete": len(successful) == len(arms) and bool(arms),
              "mutation_seed": args.mutation_seed}
    if args.require_complete:
        status_path = Path(str(out) + ".attempt.json")
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        if not status["complete"]:
            print(f"[FAIL] incomplete chain: {status}", file=sys.stderr)
            return 2
    if not successful:
        print("[FAIL] no mutator applied")
        return

    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(cur, out)

    meta = status if args.require_complete else {
        "seed": str(seed), "attempted_arms": arms,
        "applied_arms": successful, "num_applied": len(successful)}
    out.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=2)
    )

    print(f"[OK] final={out} applied={successful}")

if __name__ == "__main__":
    raise SystemExit(main())
