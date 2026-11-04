#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, glob, hashlib
from pathlib import Path
from datetime import datetime,UTC

ROOT = Path(__file__).resolve().parents[1]

def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def main():
    proc = ROOT / "data/mutants/processed_chain"
    arch_dir = ROOT / "data/archive"
    arch_dir.mkdir(parents=True, exist_ok=True)

    store = arch_dir / "archive_store.jsonl"
    lst   = arch_dir / "archive_list.json"

    # window: keep last N files in archive_list.json (for bookkeeping)
    window = 200

    reward_files = sorted(glob.glob(str(proc / "*.reward.json")))
    kept_files = []

    lines = []
    for rf in reward_files:
        try:
            r = json.load(open(rf, "r", encoding="utf-8"))
        except Exception:
            continue

        vec = r.get("vec")
        if not isinstance(vec, list) or not vec:
            # 没 vec 就无法入库
            continue

        model = r.get("model", "")
        dims = int(r.get("dims", len(vec)))
        file_path = r.get("file") or r.get("orig_file") or ""
        content_id = r.get("content_id") or sha1_text((r.get("view_file","") + file_path))

        lines.append({
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "sha1": content_id,            # 用 view 的 content_id 作为稳定去重键
            "file": file_path,
            "model": model,
            "dims": dims,
            "vec": vec,
            "reward01": float(r.get("reward01", 0.0)),
        })
        kept_files.append(file_path)

    # overwrite store with unique-by-sha1 (keep last occurrence)
    mp = {}
    if store.exists():
        for line in store.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if "sha1" in o:
                mp[o["sha1"]] = o

    for o in lines:
        mp[o["sha1"]] = o

    # write back
    with store.open("w", encoding="utf-8") as f:
        for o in mp.values():
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    # archive_list.json: keep last window file paths
    kept_files = [x for x in kept_files if x]
    kept_files = kept_files[-window:]
    lst.write_text(json.dumps({"window": window, "files": kept_files}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] archive_store={store} entries={len(mp)} archive_list={lst} kept={len(kept_files)} window={window}")

if __name__ == "__main__":
    main() 
