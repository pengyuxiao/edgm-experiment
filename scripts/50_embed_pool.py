#!/usr/bin/env python3
import argparse, hashlib, json, random, re
from pathlib import Path
from typing import List, Any

C_COMMENT = re.compile(r"/\*.*?\*/", re.S)
CPP_COMMENT = re.compile(r"//.*?$", re.M)

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

def compress_code(code: str, max_chars: int) -> str:
    # remove comments + normalize whitespace (deterministic)
    code = C_COMMENT.sub("", code)
    code = CPP_COMMENT.sub("", code)
    code = re.sub(r"[ \t]+", " ", code)
    code = re.sub(r"\n{3,}", "\n\n", code).strip()
    if len(code) <= max_chars:
        return code
    head = code[: max_chars // 2]
    tail = code[-max_chars // 2 :]
    return head + "\n/*...TRUNCATED...*/\n" + tail

def embed_hash(text: str, dim: int, salt: str="edgm") -> List[float]:
    h = hashlib.sha256((salt + "\n" + text).encode("utf-8", errors="ignore")).digest()
    rnd = random.Random(h)  # deterministic
    return [rnd.uniform(-1.0, 1.0) for _ in range(dim)]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-root", default="data/corpus/pool")
    ap.add_argument("--emb-root", default="data/corpus/emb")
    ap.add_argument("--backend", choices=["hash"], default="hash")
    ap.add_argument("--dimensions", type=int, default=256)
    ap.add_argument("--max-files", type=int, default=0)
    ap.add_argument("--max-chars", type=int, default=24000)
    args = ap.parse_args()

    pool = Path(args.pool_root)
    emb = Path(args.emb_root)
    emb.mkdir(parents=True, exist_ok=True)

    files = sorted(pool.glob("*.c"))
    if args.max_files and args.max_files > 0:
        files = files[:args.max_files]

    hits = 0
    new = 0
    for f in files:
        raw = read_text(f)
        text = compress_code(raw, args.max_chars)
        # content_id based on actual embedding input (compression included)
        cid = sha256_hex(f"{args.backend}|{args.dimensions}|{text}")
        out = emb / f"{cid}.json"
        if out.exists():
            hits += 1
            continue
        vec = embed_hash(text, args.dimensions)
        write_json(out, {"id": cid, "dim": args.dimensions, "backend": "hash", "embedding": vec})
        new += 1

    print(f"[OK] scanned={len(files)} new={new} cache_hits={hits} emb_dir={emb}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())