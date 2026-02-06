#!/usr/bin/env python3
import argparse, json, subprocess, shutil, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def preprocess_for_embedding(cfile: Path, max_tokens: int = 7800) -> Path:
    """
    Produce a preprocessed 'program-level view' file under a temp dir.
    """
    tmpdir = ROOT / "data" / "tmp_embed_view"
    tmpdir.mkdir(parents=True, exist_ok=True)
    outp = tmpdir / (cfile.name + ".view.c")
    cmd = ["python3", "scripts/72_preprocess_for_embedding.py", str(cfile),
           "--out", str(outp), "--max-tokens", str(max_tokens)]
    p = subprocess.run(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"preprocess failed rc={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return outp

def run_reward(cfile: Path, k: int, dims: int) -> dict:
    """
    Call reward script. We pass the preprocessed view file to avoid token overflow.
    """
    view = preprocess_for_embedding(cfile, max_tokens=7800)
    cmd = ["python3", "scripts/53_reward_one_c.py", str(view), "--k", str(k), "--dimensions", str(dims)]
    p = subprocess.run(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"reward failed rc={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return json.loads(p.stdout.strip())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", default="data/mutants/queue")
    ap.add_argument("--processed", default="data/mutants/processed")
    ap.add_argument("--dropped", default="data/mutants/dropped")
    ap.add_argument("--state", default="data/bandit/ts_state.json")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--dimensions", type=int, default=256)
    ap.add_argument("--max", type=int, default=0, help="0 means no limit (counting only processed items)")
    ap.add_argument("--drop-orphans", action="store_true", help="move files without meta to dropped")
    args = ap.parse_args()

    qdir = ROOT / args.queue
    pdir = ROOT / args.processed
    ddir = ROOT / args.dropped
    pdir.mkdir(parents=True, exist_ok=True)
    ddir.mkdir(parents=True, exist_ok=True)

    st_path = ROOT / args.state
    st = json.loads(st_path.read_text(encoding="utf-8"))
    window = int(st.get("window", 200))
    arms = st.setdefault("arms", {})

    files = sorted(qdir.glob("*.c"))
    n_ok = n_drop = n_skip = 0

    for cfile in files:
        if args.max and n_ok >= args.max:
            break

        meta = Path(str(cfile) + ".meta.json")
        if not meta.exists():
            if args.drop_orphans:
                shutil.move(str(cfile), str(ddir / cfile.name))
                n_drop += 1
            else:
                n_skip += 1
            continue

        m = json.loads(meta.read_text(encoding="utf-8"))
        arm = m.get("arm")
        if not arm:
            if args.drop_orphans:
                shutil.move(str(cfile), str(ddir / cfile.name))
                shutil.move(str(meta), str(ddir / meta.name))
                n_drop += 1
            else:
                n_skip += 1
            continue

        r = run_reward(cfile, args.k, args.dimensions)
        reward01 = float(r.get("reward01", 0.0))

        arms.setdefault(arm, {}).setdefault("history", []).append(reward01)
        if len(arms[arm]["history"]) > window:
            arms[arm]["history"] = arms[arm]["history"][-window:]

        out_reward = Path(str(cfile) + ".reward.json")
        out_reward.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

        shutil.move(str(cfile), str(pdir / cfile.name))
        shutil.move(str(meta), str(pdir / meta.name))
        shutil.move(str(out_reward), str(pdir / out_reward.name))

        n_ok += 1

    st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] processed={n_ok} dropped={n_drop} skipped={n_skip} state_updated={st_path}")

if __name__ == "__main__":
    main()
