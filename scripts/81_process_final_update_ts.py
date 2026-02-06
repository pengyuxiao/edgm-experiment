#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, os, subprocess, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _reward_path_for(cfile: Path, out_dir: Path) -> Path:
    # cfile: seed_0064.chain.c -> stem == "seed_0064.chain"
    return out_dir / (cfile.stem + ".reward.json")

def run_reward_and_write(cfile: Path, k: int, dimensions: int, out_dir: Path, force: bool, provider: str | None, model: str | None):
    """
    Call 53_reward_one_c.py, parse stdout JSON, validate vec, then write reward.json.
    This avoids relying on any pre-existing reward file and prevents empty/invalid files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    reward_path = _reward_path_for(cfile, out_dir)

    if reward_path.exists() and not force:
        # quick validate existing
        try:
            o = json.loads(reward_path.read_text(encoding="utf-8", errors="ignore") or "")
            v = o.get("vec")
            if isinstance(v, list) and len(v) > 0:
                return True, "skipped_existing_with_vec"
        except Exception:
            pass
        # if invalid existing, fallthrough to recompute

    cmd = ["python3", "scripts/53_reward_one_c.py", str(cfile), "--k", str(k), "--dimensions", str(dimensions)]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]

    p = subprocess.run(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if p.returncode != 0:
        # save stderr to help diagnose
        errp = reward_path.with_suffix(".stderr.txt")
        errp.write_text(p.stderr or "", encoding="utf-8")
        return False, f"reward_failed_rc={p.returncode}"

    raw = (p.stdout or "").strip()
    if not raw:
        errp = reward_path.with_suffix(".stderr.txt")
        errp.write_text((p.stderr or "") + "\n[ERR] empty stdout\n", encoding="utf-8")
        return False, "empty_stdout"

    # Some tools accidentally print multiple lines; take last non-empty line as JSON
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    raw_last = lines[-1] if lines else raw

    try:
        o = json.loads(raw_last)
    except Exception as e:
        errp = reward_path.with_suffix(".stderr.txt")
        errp.write_text((p.stderr or "") + f"\n[ERR] json_load_failed: {e}\n--- stdout(last 400) ---\n{raw_last[-400:]}\n", encoding="utf-8")
        return False, "json_load_failed"

    v = o.get("vec")
    if not (isinstance(v, list) and len(v) > 0):
        errp = reward_path.with_suffix(".stderr.txt")
        errp.write_text((p.stderr or "") + "\n[ERR] missing/invalid vec in parsed JSON\n", encoding="utf-8")
        # Still write the JSON for inspection
        reward_path.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        return False, "missing_vec"

    # Ensure compatibility fields: some later scripts expect sha1
    if "sha1" not in o and "content_id" in o:
        o["sha1"] = o["content_id"]

    reward_path.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
    return True, "recomputed_ok"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/mutants/final", help="directory containing final .c files")
    ap.add_argument("--out-dir", default="data/mutants/processed_chain", help="where to write *.reward.json")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--dimensions", type=int, default=1024)
    ap.add_argument("--max", type=int, default=0, help="0 means all")
    ap.add_argument("--force", action="store_true", help="recompute even if existing reward has vec")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", default=os.environ.get("EMB_PROVIDER", ""), help="optional override for 53")
    ap.add_argument("--model", default=os.environ.get("EMB_MODEL", ""), help="optional override for 53")
    args = ap.parse_args()

    in_dir = (ROOT / args.input).resolve()
    out_dir = (ROOT / args.out_dir).resolve()

    fs = sorted(in_dir.glob("*.c"))
    if args.max and args.max > 0:
        fs = fs[:args.max]

    if not fs:
        print(f"[ERR] no .c files under {in_dir}")
        return 1

    provider = args.provider.strip() or None
    model = args.model.strip() or None

    if args.dry_run:
        would = len(fs)
        print(f"[OK] would_recompute={would} out_dir={out_dir}")
        return 0

    ok = 0
    dropped = 0
    skipped = 0

    for cfile in fs:
        success, why = run_reward_and_write(
            cfile=cfile,
            k=int(args.k),
            dimensions=int(args.dimensions),
            out_dir=out_dir,
            force=bool(args.force),
            provider=provider,
            model=model,
        )
        if success:
            if why.startswith("skipped"):
                skipped += 1
            else:
                ok += 1
        else:
            dropped += 1
            print(f"[ERR] {cfile.name}: {why} (see reward stderr beside reward.json)")

    print(f"[OK] processed_ok={ok} dropped={dropped} skipped_existing_with_vec={skipped} out_dir={out_dir}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
