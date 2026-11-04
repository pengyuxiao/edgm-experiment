#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, os, re, subprocess, hashlib, math
import sys
import bisect
import time
import uuid
from pathlib import Path
from datetime import datetime,UTC
from urllib import request as ureq
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.corrected_reward import score as corrected_score, score_v2
from edgm import fixed_scale_reward
from edgm import parameter_free_reward

API_LOG = None
API_REQUEST_LIMIT = 0
API_MAX_RETRIES = 0

def _api_event(event):
    if API_LOG is None:
        return
    with Path(API_LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

def _api_request_count():
    if API_LOG is None or not Path(API_LOG).exists():
        return 0
    return sum(1 for line in Path(API_LOG).read_text(encoding="utf-8").splitlines()
               if json.loads(line).get("phase") == "start")

def _api_stats_since(before):
    if API_LOG is None or not Path(API_LOG).exists():
        return {"requests": 0, "elapsed_seconds": 0.0, "usage": None}
    events = [json.loads(line) for line in Path(API_LOG).read_text(encoding="utf-8").splitlines()]
    starts = [e["request_id"] for e in events if e.get("phase") == "start"]
    ids = set(starts[before:])
    finishes = [e for e in events if e.get("phase") == "finish" and e.get("request_id") in ids]
    usage = {}
    for event in finishes:
        for key, value in (event.get("usage") or {}).items():
            usage[key] = usage.get(key, 0) + value
    return {"requests": len(ids), "elapsed_seconds": sum(e["elapsed_seconds"] for e in finishes),
            "usage": usage or None}

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

def run_preprocess(cfile: Path, out_dir: Path | None = None,
                   view_mode: str = "legacy-operator-v1") -> Path:
    """
    Always produce a view file using 72_preprocess_for_embedding.py
    """
    out_dir = out_dir or ROOT / "data" / "embedding_view"
    if view_mode != "legacy-operator-v1":
        out_dir = out_dir / view_mode
    ensure_dir(out_dir)
    out = out_dir / (cfile.name + ".view.c")

    cmd = ["python3", "scripts/72_preprocess_for_embedding.py", str(cfile),
           "--out", str(out), "--view-mode", view_mode]
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
        if API_LOG is not None:
            raise RuntimeError(f"[ERR] HTTPError {e.code}") from None
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"[ERR] HTTPError {e.code} on {url}\n{body}")
    except URLError as e:
        if API_LOG is not None:
            raise RuntimeError("[ERR] URLError") from None
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

def _embed_dashscope_one(text: str, model: str, chunk_index=None, split_depth=0) -> list:
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
    for retry in range(API_MAX_RETRIES + 1):
        if API_REQUEST_LIMIT and _api_request_count() >= API_REQUEST_LIMIT:
            raise RuntimeError("API request limit reached")
        request_id = uuid.uuid4().hex
        details = {"request_id": request_id, "chunk_index": chunk_index,
                   "split_depth": split_depth, "retry_index": retry,
                   "input_chars": len(text), "model": model}
        _api_event({**details, "phase": "start", "time_utc": datetime.now(UTC).isoformat()})
        started = time.monotonic()
        try:
            obj = http_post_json(url, headers, payload, timeout=60)
            data = obj.get("data")
            if not isinstance(data, list) or not data or not isinstance(data[0].get("embedding"), list) or not data[0]["embedding"]:
                raise RuntimeError("invalid embedding response")
            emb = data[0]["embedding"]
            usage = obj.get("usage")
            usage = {k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(usage, dict) else None
            _api_event({**details, "phase": "finish", "status": "success",
                        "elapsed_seconds": time.monotonic() - started, "usage": usage,
                        "dimensions": len(emb)})
            return emb
        except Exception as exc:
            retryable = isinstance(exc, (TimeoutError, URLError)) or (
                isinstance(exc, RuntimeError) and ("URLError" in str(exc) or
                any(f"HTTPError {code}" in str(exc) for code in (429, 500, 502, 503, 504))))
            _api_event({**details, "phase": "finish", "status": "failure",
                        "elapsed_seconds": time.monotonic() - started,
                        "error_type": type(exc).__name__, "retryable": retryable})
            if not retryable or retry == API_MAX_RETRIES:
                raise
            time.sleep(0.5 * (retry + 1))

    raise RuntimeError("embedding request did not complete")

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

def split_text_complete(s: str, max_tokens: int, overlap_tokens: int,
                        max_chars: int, overlap_chars: int):
    """Return ordered [start,end) spans covering every character at least once.

    The next chunk begins at the earlier of (end - overlap_chars) and the
    start of the last overlap_tokens tokens, but always advances past the
    previous start. A long token may cross the hard character boundary.
    Empty input has no embeddable spans.
    """
    if max_tokens < 1 or max_chars < 1 or not 0 <= overlap_tokens < max_tokens or not 0 <= overlap_chars < max_chars:
        raise ValueError("invalid chunk size or overlap")
    starts = [m.start() for m in TOKEN_RE.finditer(s)]
    spans = []
    start = 0
    while start < len(s):
        first = bisect.bisect_left(starts, start)
        token_end = starts[first + max_tokens] if first + max_tokens < len(starts) else len(s)
        end = min(len(s), start + max_chars, token_end)
        if end <= start:
            end = min(len(s), start + max_chars)
        spans.append((start, end))
        if end == len(s):
            break
        next_start = max(start + 1, end - overlap_chars)
        if overlap_tokens:
            count = bisect.bisect_left(starts, end)
            token_start = starts[max(first, count - overlap_tokens)] if count > first else start
            if token_start > start:
                next_start = min(next_start, token_start)
        start = next_start
    return spans

def _weighted_vec(vecs_and_weights):
    if not vecs_and_weights:
        raise ValueError("no nonempty embedding chunks")
    dims = len(vecs_and_weights[0][0])
    total = sum(w for _, w in vecs_and_weights)
    if dims < 1 or total <= 0:
        raise ValueError("invalid embedding chunks")
    out = [0.0] * dims
    for vec, weight in vecs_and_weights:
        if len(vec) != dims or weight <= 0:
            raise ValueError("embedding dimensions or weights differ")
        for i, value in enumerate(vec):
            out[i] += float(value) * weight / total
    return out

def _complete_one(text: str, provider: str, model: str, dimensions: int | None,
                  chunk_index=None, split_depth=0):
    if provider == "mock":
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [([(digest[i] / 127.5) - 1.0 for i in range(8)], len(text))]
    try:
        if provider == "dashscope":
            return [(_embed_dashscope_one(text, model, chunk_index, split_depth), len(text))]
        if provider == "openai":
            return [(embed_openai(text, model, dimensions)["vec"], len(text))]
        raise ValueError(f"unknown embedding provider: {provider}")
    except Exception as exc:
        if provider != "dashscope" or not _is_len_400(exc) or len(text) < 200:
            raise
        mid = len(text) // 2
        _api_event({"phase": "split", "chunk_index": chunk_index,
                    "split_depth": split_depth, "input_chars": len(text)})
        return (_complete_one(text[:mid], provider, model, dimensions, chunk_index, split_depth + 1)
                + _complete_one(text[mid:], provider, model, dimensions, chunk_index, split_depth + 1))

def embed_complete(text: str, provider: str, model: str, dimensions: int | None,
                   max_tokens=2000, overlap_tokens=80, max_chars=7000, overlap_chars=200):
    spans = split_text_complete(text, max_tokens, overlap_tokens, max_chars, overlap_chars)
    parts = []
    weights = []
    previous_end = 0
    for chunk_index, (start, end) in enumerate(spans):
        # Overlap informs the chunk embedding, but is counted only once in aggregation.
        weight = end - max(start, previous_end)
        if weight > 0 and text[start:end].strip():
            children = _complete_one(text[start:end], provider, model, dimensions, chunk_index)
            parts.append((_weighted_vec(children), weight))
            weights.append(weight)
        previous_end = max(previous_end, end)
    return {"provider": provider, "model": model, "vec": _weighted_vec(parts),
            "chunks": len(spans), "chunk_ranges": spans, "chunk_weights": weights,
            "embedding_pipeline_version": "complete-chunk-v2"}

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
    ap.add_argument("--view-dir", default="", help="isolated embedding view directory for corrected runs")
    ap.add_argument("--reward-version", choices=["legacy-eq6", "corrected-loo-v1", "corrected-loo-v2", *fixed_scale_reward.VERSIONS, *parameter_free_reward.VERSIONS], default="legacy-eq6")
    ap.add_argument("--reward-scale-file", default="", help="immutable calibration JSON for fixed-scale reward")
    ap.add_argument("--calibration-cache-dir", default="", help="per-run LOO reference cache")
    ap.add_argument("--calibration-cap", type=int, default=128)
    ap.add_argument("--calibration-seed", type=int, default=0)
    ap.add_argument("--view-mode", choices=["auto", "legacy-operator-v1", "program-content-v2"], default="auto")
    ap.add_argument("--embedding-pipeline", choices=["auto", "legacy-chunk-v1", "complete-chunk-v2"], default="auto")
    ap.add_argument("--allow-mock", action="store_true", help="explicitly permit deterministic test-only embedding")
    ap.add_argument("--api-log", default="", help="append-only request ledger for corrected runs")
    ap.add_argument("--api-request-limit", type=int, default=0)
    ap.add_argument("--api-max-retries", type=int, default=0)

    # robust chunk knobs
    ap.add_argument("--chunk-max-tokens", type=int, default=int(os.environ.get("EMB_CHUNK_MAX_TOKENS", "2000")))
    ap.add_argument("--chunk-overlap-tokens", type=int, default=int(os.environ.get("EMB_CHUNK_OVERLAP_TOKENS", "80")))
    ap.add_argument("--chunk-max-chars", type=int, default=int(os.environ.get("EMB_CHUNK_MAX_CHARS", "7000")))
    ap.add_argument("--chunk-overlap-chars", type=int, default=int(os.environ.get("EMB_CHUNK_OVERLAP_CHARS", "200")))

    args = ap.parse_args()
    global API_LOG, API_REQUEST_LIMIT, API_MAX_RETRIES
    if args.api_request_limit < 0 or args.api_max_retries < 0 or (args.api_request_limit and not args.api_log):
        ap.error("API request limit requires an API log; limits and retries must be nonnegative")
    API_LOG = Path(args.api_log) if args.api_log else None
    API_REQUEST_LIMIT = args.api_request_limit
    API_MAX_RETRIES = args.api_max_retries
    out = {}  # PATCH: always init to avoid UnboundLocalError
    fixed_version = args.reward_version in fixed_scale_reward.VERSIONS
    parameter_free_version = args.reward_version in parameter_free_reward.VERSIONS
    modern = args.reward_version == "corrected-loo-v2" or fixed_version or parameter_free_version
    if parameter_free_version and (args.k != 5 or args.model != "text-embedding-v4" or
            args.dimensions != 1024 or args.provider not in ("dashscope", "mock")):
        ap.error("parameter-free reward requires k=5 and text-embedding-v4/1024")
    fixed_config = fixed_scale = fixed_config_id = None
    if fixed_version:
        if not args.reward_scale_file:
            ap.error("fixed-scale reward requires --reward-scale-file")
        fixed_config, fixed_scale, fixed_config_id = fixed_scale_reward.load_config(
            args.reward_scale_file, args.reward_version)
        if args.k != 5 or args.model != fixed_config["model"] or args.dimensions != fixed_config["dimensions"]:
            ap.error("fixed-scale reward model, dimension and k must match calibration config")
    elif args.reward_scale_file:
        ap.error("--reward-scale-file is only valid for fixed-scale rewards")
    view_mode = ("program-content-v2" if modern else "legacy-operator-v1") if args.view_mode == "auto" else args.view_mode
    pipeline = ("complete-chunk-v2" if modern else "legacy-chunk-v1") if args.embedding_pipeline == "auto" else args.embedding_pipeline
    if fixed_version and (view_mode != fixed_config["view_mode"] or pipeline != fixed_config["embedding_pipeline_version"]):
        ap.error("fixed-scale reward view/pipeline must match calibration config")
    if not modern and pipeline != "legacy-chunk-v1":
        ap.error("Legacy/v1 embedding pipeline must remain legacy-chunk-v1")
    if not modern and view_mode != "legacy-operator-v1":
        ap.error("Legacy/v1 view must remain legacy-operator-v1")
    if (args.reward_version.startswith("corrected-") or fixed_version or parameter_free_version) and (
        args.archive_store == "data/archive/archive_store.jsonl"
        or args.cache_dir == "data/cache/embeddings"
        or not args.view_dir or not args.calibration_cache_dir
    ):
        ap.error("corrected reward requires isolated --archive-store, --cache-dir, --view-dir and --calibration-cache-dir")

    cpath = Path(args.cfile)
    if not cpath.exists():
        raise SystemExit(f"[ERR] file not found: {cpath}")

    view_path = run_preprocess(cpath, Path(args.view_dir) if args.view_dir else None, view_mode)
    view = view_path.read_text(encoding="utf-8", errors="ignore")

    view_sha1 = sha1_text(view)
    provider = args.provider.strip().lower()
    model = args.model.strip()
    dims_req = int(args.dimensions) if int(args.dimensions) > 0 else None

    cache_dir = ROOT / args.cache_dir / f"{provider}__{model}"
    ensure_dir(cache_dir)
    if provider == "mock" and not args.allow_mock:
        raise SystemExit("[ERR] mock embedding requires --allow-mock")
    if modern:
        embedding_identity = [view_sha1, provider, model, dims_req, view_mode,
            pipeline, args.chunk_max_tokens, args.chunk_overlap_tokens, args.chunk_max_chars,
            args.chunk_overlap_chars]
        if fixed_version or parameter_free_version:
            embedding_identity.extend([args.reward_version,
                fixed_config_id if fixed_version else parameter_free_reward.config_id(args.reward_version, args.k)])
        config_key = sha1_text(json.dumps(embedding_identity))
        cache_file = cache_dir / f"{config_key}.json"
    elif args.reward_version == "corrected-loo-v1":
        config_key = sha1_text(json.dumps([view_sha1, provider, model, dims_req,
            args.chunk_max_tokens, args.chunk_overlap_tokens, args.chunk_max_chars,
            args.chunk_overlap_chars]))
        cache_file = cache_dir / f"{config_key}.json"
    else:
        cache_file = cache_dir / f"{view_sha1}.json"

    emb = None
    api_before = _api_request_count()
    if cache_file.exists():
        try:
            emb = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            emb = None

    if not emb or "vec" not in emb:
        if pipeline == "complete-chunk-v2":
            emb = embed_complete(view, provider, model, dims_req,
                max_tokens=int(args.chunk_max_tokens),
                overlap_tokens=int(args.chunk_overlap_tokens),
                max_chars=int(args.chunk_max_chars),
                overlap_chars=int(args.chunk_overlap_chars))
        elif provider == "dashscope":
            emb = embed_dashscope_openai_compatible(
                view, model,
                max_tokens=int(args.chunk_max_tokens),
                overlap_tokens=int(args.chunk_overlap_tokens),
                max_chars=int(args.chunk_max_chars),
                overlap_chars=int(args.chunk_overlap_chars),
            )
        elif provider == "openai":
            emb = embed_openai(view, model, dims_req)
        elif provider == "mock":
            digest = hashlib.sha256(view.encode("utf-8")).digest()
            emb = {"provider": "mock", "model": model,
                   "vec": [(digest[i] / 127.5) - 1.0 for i in range(8)]}
        else:
            raise SystemExit(f"[ERR] unknown provider: {provider} (use dashscope|openai)")
        cache_file.write_text(json.dumps(emb, ensure_ascii=False), encoding="utf-8")

    vec = emb["vec"]
    dims = len(vec)
    if provider == "dashscope" and dims_req is not None and dims != dims_req:
        raise RuntimeError(f"embedding dimension mismatch: expected {dims_req}, got {dims}")

    store = ROOT / args.archive_store
    corrected = None
    if args.reward_version.startswith("corrected-") or fixed_version or parameter_free_version:
        entries = []
        if store.exists():
            for line in store.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                if entry.get("model") == model and entry.get("provider", provider) == provider and entry.get("dims") == dims and (
                    args.reward_version == "corrected-loo-v1" or
                    (entry.get("view_mode") == view_mode and entry.get("embedding_pipeline_version") == pipeline)):
                    if entry.get("sha1") == view_sha1:
                        raise SystemExit("[ERR] duplicate embedding view in archive")
                    entries.append(entry)
        if args.reward_version == "corrected-loo-v2":
            corrected = score_v2(vec, entries, int(args.k),
                cache_dir=args.calibration_cache_dir, calibration_cap=args.calibration_cap,
                sample_seed=args.calibration_seed)
        elif fixed_version:
            corrected = fixed_scale_reward.score(vec, entries, version=args.reward_version,
                scale=fixed_scale, config_hash=fixed_config_id, k=int(args.k))
        elif parameter_free_version:
            corrected = parameter_free_reward.score(vec, entries, version=args.reward_version,
                k=int(args.k), cache_dir=args.calibration_cache_dir)
        else:
            corrected = corrected_score(vec, entries, int(args.k),
                cache_dir=args.calibration_cache_dir or None, calibration_cap=args.calibration_cap)
        archive_size = len(entries)
        knn_mean = corrected["knn_mean_dist"]
        p10 = corrected["reference_p10"]
        p90 = corrected["reference_p90"]
        reward01 = corrected["reward01"]
    else:
        ref_vecs = load_archive_vecs(store, model=model, dims=dims, exclude_sha1=view_sha1)
        archive_size = len(ref_vecs)
        dists = [cosine_dist(vec, rv) for rv in ref_vecs]
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
        "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "file": str(cpath.resolve()),
        "view_file": str(view_path.resolve()),

        # 兼容字段：有些脚本叫 content_id，有些脚本叫 sha1
        "content_id": view_sha1,
        "sha1": view_sha1,

        "orig_file": str(cpath.resolve()),
        "provider": emb.get("provider", provider),
        "model": model,
        "dims": dims,
        "api_usage": _api_stats_since(api_before),
        "api_requests_total": _api_request_count(),
        "vec": vec,  # MUST exist for archive builder

        "k": int(args.k),
        "knn_mean_dist": float(knn_mean),
        "pool_p10": float(p10) if p10 is not None else None,
        "pool_p90": float(p90) if p90 is not None else None,
        "reward01": float(reward01),
        "archive_size": int(archive_size),
        "view_approx_tokens": int(approx_tokens(view)),
        "view_chars": int(len(view)),
        "reward_version": args.reward_version,
        "view_mode": view_mode,
        "embedding_pipeline_version": pipeline,
        "chunk_max_tokens": int(args.chunk_max_tokens),
        "chunk_overlap_tokens": int(args.chunk_overlap_tokens),
        "chunk_max_chars": int(args.chunk_max_chars),
        "chunk_overlap_chars": int(args.chunk_overlap_chars),
    }

    if corrected is not None:
        out.update(corrected)
    if pipeline == "complete-chunk-v2":
        out["chunks"] = int(emb["chunks"])
        out["chunk_ranges"] = emb["chunk_ranges"]
        out["chunk_weights"] = emb["chunk_weights"]

    if provider == "dashscope":
        out["chunks"] = int(emb.get("chunks", 1))
        out["chunk_max_tokens"] = int(args.chunk_max_tokens)
        out["chunk_overlap_tokens"] = int(args.chunk_overlap_tokens)
        out["chunk_max_chars"] = int(args.chunk_max_chars)
        out["chunk_overlap_chars"] = int(args.chunk_overlap_chars)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
