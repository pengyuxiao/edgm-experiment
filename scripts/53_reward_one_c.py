#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, os, re, subprocess, hashlib, math
from pathlib import Path
from datetime import datetime
from urllib import request as ureq
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]

TOKEN_RE = re.compile(
    r'(\"([^\"\\]|\\.)*\")|(\'([^\'\\]|\\.)*\')|([A-Za-z_][A-Za-z0-9_]*)|([0-9]+(\.[0-9]+)?)|(\S)',
    re.DOTALL
)

def sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()

def sha1_text(s: str) -> str:
    return sha1_bytes(s.encode("utf-8", errors="ignore"))

def approx_tokens(s: str) -> int:
    return sum(1 for _ in TOKEN_RE.finditer(s or ""))

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def run_preprocess(cfile: Path) -> Path:
    """
    Always produce a view file using 72_preprocess_for_embedding.py
    """
    out_dir = ROOT / "data" / "embedding_view"
    ensure_dir(out_dir)
    out = out_dir / (cfile.name + ".view.c")

    cmd = ["python3", "scripts/72_preprocess_for_embedding.py", str(cfile), "--out", str(out)]
    p = subprocess.run(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"[ERR] preprocess failed rc={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return out

def http_post_json(url: str, headers: dict, payload: dict, timeout=60) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = ureq.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with ureq.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"[ERR] HTTPError {e.code} on {url}\n{body}")
    except URLError as e:
        raise RuntimeError(f"[ERR] URLError on {url}: {e}")

def _avg_vec(vecs):
    if not vecs:
        return None
    d = len(vecs[0])
    out = [0.0] * d
    for v in vecs:
        if not isinstance(v, list) or len(v) != d:
            return None
        for i, x in enumerate(v):
            out[i] += float(x)
    inv = 1.0 / float(len(vecs))
    for i in range(d):
        out[i] *= inv
    return out

def _is_len_400(err: Exception) -> bool:
    s = str(err)
    return ("Range of input length should be [1, 8192]" in s) or ("InvalidParameter" in s and "8192" in s)

def _embed_dashscope_one(text: str, model: str) -> list:
    """
    One-shot DashScope OpenAI compatible embeddings call.
    Returns embedding list[float].
    """
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    base = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    url = base + "/embeddings"

    payload = {
        "model": model,
        "input": text,
        "encoding_format": "float",
    }
    headers = {"Authorization": f"Bearer {key}"}
    obj = http_post_json(url, headers, payload, timeout=60)

    data = obj.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"[ERR] unexpected embeddings response: {str(obj)[:400]}")
    emb = data[0].get("embedding")
    if not isinstance(emb, list) or not emb:
        raise RuntimeError(f"[ERR] missing embedding vector in response: {str(obj)[:400]}")
    return emb

def _split_text_hybrid(s: str, max_tokens: int, overlap_tokens: int, max_chars: int, overlap_chars: int):
    """
    Hybrid splitter:
      - primary: approx token spans (TOKEN_RE)
      - hard cap: per chunk char length <= max_chars
    """
    s = s or ""
    if not s:
        return [""]

    spans = [m.span() for m in TOKEN_RE.finditer(s)]
    if not spans:
        # fallback purely by chars
        if len(s) <= max_chars:
            return [s]
        chunks = []
        step = max(1, max_chars - max(0, overlap_chars))
        i = 0
        while i < len(s):
            chunks.append(s[i:i+max_chars])
            i += step
        return [c for c in chunks if c.strip()]

    n = len(spans)
    if n <= max_tokens and len(s) <= max_chars:
        return [s]

    chunks = []
    step_t = max(1, max_tokens - max(0, overlap_tokens))
    i = 0
    while i < n:
        j = min(n, i + max_tokens)
        start = spans[i][0]
        end = spans[j - 1][1]

        # hard char cap: shrink end if needed
        if end - start > max_chars:
            end = start + max_chars
            # ensure end not cutting in the middle of multibyte? (python slice ok)
        chunk = s[start:end]
        if chunk.strip():
            chunks.append(chunk)
        i += step_t

    # last safety: if still nothing, take first max_chars
    if not chunks:
        chunks = [s[:max_chars]]
    return chunks

def _embed_with_recursive_fallback(text: str, model: str, max_chars: int, depth: int = 0):
    """
    Robust fallback:
      Try embed. If still 400-len, bisect by chars and embed both halves recursively.
      Return list of vectors (to be averaged).
    """
    text = text or ""
    if not text.strip():
        return []

    # hard cap immediately (avoid huge request)
    if len(text) > max_chars:
        text = text[:max_chars]

    try:
        v = _embed_dashscope_one(text, model)
        return [v]
    except Exception as e:
        if _is_len_400(e):
            # cannot split forever
            if len(text) < 200 or depth >= 12:
                raise
            mid = len(text) // 2
            left = text[:mid]
            right = text[mid:]
            vecs = []
            vecs += _embed_with_recursive_fallback(left, model, max_chars=max_chars, depth=depth+1)
            vecs += _embed_with_recursive_fallback(right, model, max_chars=max_chars, depth=depth+1)
            return vecs
        raise

def embed_dashscope_openai_compatible(
    text: str,
    model: str,
    max_tokens: int = 2000,
    overlap_tokens: int = 80,
    max_chars: int = 7000,
    overlap_chars: int = 200,
) -> dict:
    """
    DashScope embedding with robust chunking:
      1) hybrid chunk by approx tokens + HARD char cap
      2) for each chunk, if still 400-length, recursively bisect (char-based) until it passes
      3) average all vectors
    """
    text = text or ""
    chunks = _split_text_hybrid(
        text,
        max_tokens=int(max_tokens),
        overlap_tokens=int(overlap_tokens),
        max_chars=int(max_chars),
        overlap_chars=int(overlap_chars),
    )

    vecs = []
    for ch in chunks:
        if not ch.strip():
            continue
        # first try direct; if 400, fallback will split further
        vecs += _embed_with_recursive_fallback(ch, model, max_chars=int(max_chars), depth=0)

    if not vecs:
        raise RuntimeError("[ERR] all chunks empty -> cannot embed")

    vec = _avg_vec(vecs)
    if vec is None:
        raise RuntimeError("[ERR] failed to average chunk embeddings (dim mismatch?)")

    return {
        "provider": "dashscope",
        "model": model,
        "vec": vec,
        "chunks": len(vecs),
        "chunk_max_tokens": int(max_tokens),
        "chunk_overlap_tokens": int(overlap_tokens),
        "chunk_max_chars": int(max_chars),
        "chunk_overlap_chars": int(overlap_chars),
    }

def embed_openai(text: str, model: str, dimensions: int | None) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/embeddings"
    payload = {"model": model, "input": text}
    if dimensions is not None and dimensions > 0:
        payload["dimensions"] = int(dimensions)
    headers = {"Authorization": f"Bearer {key}"}
    obj = http_post_json(url, headers, payload, timeout=60)

    data = obj.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"[ERR] unexpected embeddings response: {str(obj)[:400]}")
    emb = data[0].get("embedding")
    if not isinstance(emb, list) or not emb:
        raise RuntimeError(f"[ERR] missing embedding vector in response: {str(obj)[:400]}")

    return {"provider": "openai", "model": model, "vec": emb}

def cosine_dist(a, b) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
        na += float(x) * float(x)
        nb += float(y) * float(y)
    if na <= 1e-12 or nb <= 1e-12:
        return 1.0
    return 1.0 - (dot / (math.sqrt(na) * math.sqrt(nb)))

def load_archive_vecs(store_path: Path, model: str, dims: int, exclude_sha1: str):
    vecs = []
    if not store_path.exists():
        return vecs
    for line in store_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("sha1") == exclude_sha1:
            continue
        if o.get("model") != model:
            continue
        if int(o.get("dims", -1)) != int(dims):
            continue
        v = o.get("vec")
        if isinstance(v, list) and len(v) == dims:
            vecs.append(v)
    return vecs

def percentile(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs)-1) * (p/100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(xs[int(k)])
    return float(xs[f] * (c-k) + xs[c] * (k-f))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cfile", help="input C file")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--dimensions", type=int, default=0, help="only meaningful for OpenAI native embeddings; DashScope ignores")
    ap.add_argument("--provider", default=os.environ.get("EMB_PROVIDER", "dashscope"))
    ap.add_argument("--model", default=os.environ.get("EMB_MODEL", "text-embedding-v4"))
    ap.add_argument("--archive-store", default="data/archive/archive_store.jsonl")
    ap.add_argument("--cache-dir", default="data/cache/embeddings")

    # robust chunk knobs
    ap.add_argument("--chunk-max-tokens", type=int, default=int(os.environ.get("EMB_CHUNK_MAX_TOKENS", "2000")))
    ap.add_argument("--chunk-overlap-tokens", type=int, default=int(os.environ.get("EMB_CHUNK_OVERLAP_TOKENS", "80")))
    ap.add_argument("--chunk-max-chars", type=int, default=int(os.environ.get("EMB_CHUNK_MAX_CHARS", "7000")))
    ap.add_argument("--chunk-overlap-chars", type=int, default=int(os.environ.get("EMB_CHUNK_OVERLAP_CHARS", "200")))

    args = ap.parse_args()
    out = {}  # PATCH: always init to avoid UnboundLocalError

    cpath = Path(args.cfile)
    if not cpath.exists():
        raise SystemExit(f"[ERR] file not found: {cpath}")

    view_path = run_preprocess(cpath)
    view = view_path.read_text(encoding="utf-8", errors="ignore")

    view_sha1 = sha1_text(view)
    provider = args.provider.strip().lower()
    model = args.model.strip()
    dims_req = int(args.dimensions) if int(args.dimensions) > 0 else None

    cache_dir = ROOT / args.cache_dir / f"{provider}__{model}"
    ensure_dir(cache_dir)
    cache_file = cache_dir / f"{view_sha1}.json"

    emb = None
    if cache_file.exists():
        try:
            emb = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            emb = None

    if not emb or "vec" not in emb:
        if provider == "dashscope":
            emb = embed_dashscope_openai_compatible(
                view, model,
                max_tokens=int(args.chunk_max_tokens),
                overlap_tokens=int(args.chunk_overlap_tokens),
                max_chars=int(args.chunk_max_chars),
                overlap_chars=int(args.chunk_overlap_chars),
            )
        elif provider == "openai":
            emb = embed_openai(view, model, dims_req)
        else:
            raise SystemExit(f"[ERR] unknown provider: {provider} (use dashscope|openai)")
        cache_file.write_text(json.dumps(emb, ensure_ascii=False), encoding="utf-8")

    vec = emb["vec"]
    dims = len(vec)

    store = ROOT / args.archive_store
    ref_vecs = load_archive_vecs(store, model=model, dims=dims, exclude_sha1=view_sha1)

    dists = []
    if ref_vecs:
        for rv in ref_vecs:
            dists.append(cosine_dist(vec, rv))

        # ---- reward from distances (or neutral if archive empty) ----
    if dists:
        dists_sorted = sorted(dists)
        k = max(1, int(args.k))
        knn = dists_sorted[:min(k, len(dists_sorted))]
        knn_mean = sum(knn) / len(knn)
        p10 = percentile(dists_sorted, 10)
        p90 = percentile(dists_sorted, 90)
        denom = (p90 - p10) if (p90 - p10) > 1e-12 else 1e-12
        reward01 = (knn_mean - p10) / denom
        reward01 = 0.0 if reward01 < 0.0 else (1.0 if reward01 > 1.0 else reward01)
    else:
        knn_mean = 0.0
        p10 = 0.0
        p90 = 1.0
        reward01 = 0.5  # empty archive -> neutral

    # ---- ALWAYS build output (must include vec) ----
    out = {
        "time": datetime.utcnow().isoformat() + "Z",
        "file": str(cpath.resolve()),
        "view_file": str(view_path.resolve()),

        # 兼容字段：有些脚本叫 content_id，有些脚本叫 sha1
        "content_id": view_sha1,
        "sha1": view_sha1,

        "orig_file": str(cpath.resolve()),
        "provider": emb.get("provider", provider),
        "model": model,
        "dims": dims,
        "vec": vec,  # MUST exist for archive builder

        "k": int(args.k),
        "knn_mean_dist": float(knn_mean),
        "pool_p10": float(p10),
        "pool_p90": float(p90),
        "reward01": float(reward01),
        "archive_size": int(len(ref_vecs)),
        "view_approx_tokens": int(approx_tokens(view)),
        "view_chars": int(len(view)),
    }

    if provider == "dashscope":
        out["chunks"] = int(emb.get("chunks", 1))
        out["chunk_max_tokens"] = int(args.chunk_max_tokens)
        out["chunk_overlap_tokens"] = int(args.chunk_overlap_tokens)
        out["chunk_max_chars"] = int(args.chunk_max_chars)
        out["chunk_overlap_chars"] = int(args.chunk_overlap_chars)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
