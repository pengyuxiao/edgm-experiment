#!/usr/bin/env python3
"""Isolated, per-mutant online corrected EIDETIC baseline."""

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm import selection_policies
from edgm import fixed_scale_reward
from edgm import parameter_free_reward
from edgm.diversity_monitor import projected_metrics

def ts_module():
    spec = importlib.util.spec_from_file_location("eidetic_ts", ROOT / "scripts/52_ts_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def atomic_json(path, obj):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

def run_command(cmd):
    p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode, p.stdout, p.stderr

def compiler_identity(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"compiler not found: {name}")
    resolved = str(Path(path).resolve())
    p = subprocess.run([resolved, "--version"], capture_output=True, text=True,
                       errors="replace", timeout=10)
    if p.returncode != 0:
        raise RuntimeError(f"compiler version failed: {name}")
    return {"path": resolved, "version": p.stdout.splitlines()[0]}

def compiler_check(name, identity, src, include, diag_dir, label, timeout=60):
    cmd = [identity["path"], "-std=c11", "-fsyntax-only", "-w", "-I", str(include), str(src)]
    diag_dir.mkdir(parents=True, exist_ok=True)
    prefix = diag_dir / f"{label}.{name}"
    result = {"path": identity["path"], "version": identity["version"],
              "stderr_path": str(prefix) + ".stderr.txt",
              "stdout_path": str(prefix) + ".stdout.txt",
              "exit_code": None, "reports_error": None, "timed_out": False,
              "exception": None, "diagnostic_parse_failed": False,
              "diagnostic_parser": "raw-error-marker-v1"}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
        stdout, stderr = p.stdout, p.stderr
        result["exit_code"] = p.returncode
        result["reports_error"] = bool(re.search(r"(?m)^.*:\s*(?:fatal\s+)?error:\s*", stderr))
        result["diagnostic_parse_failed"] = p.returncode != 0 and not result["reports_error"]
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        stdout = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    except OSError as exc:
        result["exception"] = type(exc).__name__
        stdout, stderr = "", ""
    Path(result["stdout_path"]).write_text(stdout, encoding="utf-8")
    Path(result["stderr_path"]).write_text(stderr, encoding="utf-8")
    return result

def compiler_pair(identities, src, include, diag_dir, label):
    return {name: compiler_check(name, identity, src, include, diag_dir, label)
            for name, identity in identities.items()}

def clean_stderr(stderr):
    for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(key)
        if secret:
            stderr = stderr.replace(secret, "[REDACTED]")
    return stderr[-2000:]

def reward_then_commit(run_dir, state, chosen, reward, mutant, event, ts, policy="beta-ts"):
    """Write-ahead marker makes an interrupted two-file commit visible and terminal."""
    pending = run_dir / "PENDING_COMMIT.json"
    atomic_json(pending, {"mutant": str(mutant), "content_id": reward["content_id"],
                          "reward01": reward["reward01"], "selected_arms": chosen})
    selection_policies.update(policy, state, chosen, float(reward["reward01"]), ts)
    atomic_json(run_dir / "bandit_state.json", state)
    entry = {"sha1": reward["content_id"], "file": str(mutant),
             "provider": reward["provider"], "model": reward["model"],
             "dims": reward["dims"], "vec": reward["vec"],
             "reward01": reward["reward01"], "reward_version": reward["reward_version"],
             "reward_config_id": reward.get("reward_config_id"),
             "view_mode": reward["view_mode"],
             "embedding_pipeline_version": reward["embedding_pipeline_version"]}
    append_jsonl(run_dir / "archive_store.jsonl", entry)
    event.update({"status": "accepted", "reward01": reward["reward01"],
                  "reward_version": reward["reward_version"], "content_id": reward["content_id"],
                  "reward_config_id": reward.get("reward_config_id"),
                  "reward_scale": reward.get("reward_scale"),
                  "effective_neighbors": reward.get("effective_neighbors"),
                  "rank_less": reward.get("rank_less"),
                  "rank_equal": reward.get("rank_equal"),
                  "knn_mean_dist": reward["knn_mean_dist"],
                  "reference_p10": reward["reference_p10"], "reference_p90": reward["reference_p90"],
                  "normalization": reward["normalization"], "policy_updates_after": state["updates"],
                  "archive_size_before": reward["archive_size"],
                  "calibration_size": reward.get("calibration_size", reward.get("reference_size")),
                  "small_archive_fallback": reward["normalization"] in
                  ("empty-neutral", "empty-archive-neutral", "single-reference-neutral",
                   "small-archive-cosine-range"),
                  "view_mode": reward["view_mode"],
                  "embedding_pipeline_version": reward["embedding_pipeline_version"]})
    if policy == "beta-ts":
        event["ts_updates_after"] = state["updates"]
    append_jsonl(run_dir / "events.jsonl", event)
    pending.unlink()

def run(args):
    ts = ts_module()
    run_started = time.perf_counter()
    run_dir = Path(args.run_dir).resolve()
    if run_dir.exists():
        raise ValueError(f"run directory already exists: {run_dir}")
    if args.provider == "mock" and not args.allow_mock:
        raise ValueError("mock embedding requires --allow-mock")
    if args.api_request_limit < 0 or args.api_max_retries < 0:
        raise ValueError("API request limit and retries must be nonnegative")
    if args.provider == "dashscope" and args.api_request_limit < 1:
        raise ValueError("DashScope corrected runs require a positive --api-request-limit")
    fixed_version = args.reward_version in fixed_scale_reward.VERSIONS
    parameter_free_version = args.reward_version in parameter_free_reward.VERSIONS
    modern = args.reward_version == "corrected-loo-v2" or fixed_version or parameter_free_version
    if parameter_free_version and (args.k != 5 or args.model != "text-embedding-v4" or
            args.dimensions != 1024 or args.provider not in ("dashscope", "mock")):
        raise ValueError("parameter-free reward requires k=5 and text-embedding-v4/1024")
    scale_config = scale_value = scale_id = None
    scale_bytes = scale_sha256 = None
    if fixed_version:
        if not args.reward_scale_file:
            raise ValueError("fixed-scale reward requires --reward-scale-file")
        scale_bytes = Path(args.reward_scale_file).read_bytes()
        scale_sha256 = hashlib.sha256(scale_bytes).hexdigest()
        scale_config, scale_value, scale_id = fixed_scale_reward.load_config(
            args.reward_scale_file, args.reward_version)
        if hashlib.sha256(Path(args.reward_scale_file).read_bytes()).hexdigest() != scale_sha256:
            raise ValueError("reward calibration changed during startup")
    elif args.reward_scale_file:
        raise ValueError("--reward-scale-file only applies to fixed-scale rewards")
    view_mode = ("program-content-v2" if modern else "legacy-operator-v1") if args.view_mode == "auto" else args.view_mode
    pipeline = ("complete-chunk-v2" if modern else "legacy-chunk-v1") if args.embedding_pipeline == "auto" else args.embedding_pipeline
    if fixed_version and (args.model != scale_config["model"] or
            args.dimensions != scale_config["dimensions"] or args.k != scale_config["k"] or
            view_mode != scale_config["view_mode"] or
            pipeline != scale_config["embedding_pipeline_version"]):
        raise ValueError("fixed-scale reward settings do not match calibration config")
    calibration_seed = args.random_seed if args.calibration_seed is None else args.calibration_seed
    seeds = [Path(args.seed).resolve()] if args.seed else sorted(Path(args.seed_dir).resolve().glob("*.c"))
    if not seeds or any(not s.is_file() for s in seeds):
        raise ValueError("no readable C seeds")
    arms = ts.load_arms(args.arms_file)
    arms_digest = hashlib.sha256(Path(args.arms_file).read_bytes()).hexdigest()
    canonical_arms = hashlib.sha256((ROOT / "config/arms_list.txt").read_bytes()).hexdigest()
    if not 1 <= args.m <= len(arms) or args.iterations < 1 or args.max_attempts < args.iterations:
        raise ValueError("invalid m, iteration count or attempt budget")
    if args.policy != "beta-ts" and args.rng_mode != "split-v1":
        raise ValueError("random and discounted-ts require split-v1 random streams")
    if args.comparison_mode and (args.rng_mode != "split-v1" or len(arms) != 55 or
            not modern or view_mode != "program-content-v2" or
            pipeline != "complete-chunk-v2" or args.k != 5 or arms_digest != canonical_arms):
        raise ValueError("comparison mode requires 55 arms, k=5, v2 reward/view/pipeline and split-v1 RNG")
    if args.comparison_mode and (args.seed or Path(args.seed_dir).resolve() !=
            (ROOT / "data/seeds/diverse/clean_diversify_syntax").resolve()):
        raise ValueError("comparison mode requires the canonical clean_diversify_syntax seed pool")
    if args.comparison_mode and args.provider != "mock" and (args.provider != "dashscope" or
            args.model != "text-embedding-v4" or args.dimensions != 1024):
        raise ValueError("real comparison requires DashScope text-embedding-v4 at 1024 dimensions")
    comparison_config = {"arms_sha256": arms_digest,
        "seed_sha256": [hashlib.sha256(seed.read_bytes()).hexdigest() for seed in seeds],
        "reward_version": args.reward_version, "view_mode": view_mode,
        "embedding_pipeline_version": pipeline, "k": args.k, "m": args.m,
        "random_seed": args.random_seed, "calibration_seed": calibration_seed,
        "calibration_cap": args.calibration_cap, "provider": args.provider,
        "model": args.model, "dimensions": args.dimensions,
        "chunk_max_tokens": args.chunk_max_tokens,
        "chunk_overlap_tokens": args.chunk_overlap_tokens,
        "chunk_max_chars": args.chunk_max_chars,
        "chunk_overlap_chars": args.chunk_overlap_chars}
    if fixed_version:
        comparison_config.update({"reward_config_id": scale_id,
            "reward_scale_file_sha256": scale_sha256})
    if parameter_free_version:
        comparison_config["reward_config_id"] = parameter_free_reward.config_id(args.reward_version, args.k)
    comparison_digest = hashlib.sha256(json.dumps(comparison_config, sort_keys=True).encode()).hexdigest()
    if args.rng_mode == "legacy":
        rng = random.Random(args.random_seed)
        seed_rng = policy_rng = mutation_rng = rng
    else:
        def stream(label):
            digest = hashlib.sha256(f"edgm-split-v1:{args.random_seed}:{label}".encode()).digest()
            return random.Random(int.from_bytes(digest[:16], "big"))
        seed_rng, policy_rng, mutation_rng = stream("seed"), stream("policy:" + args.policy), stream("mutation")
    identities = ({name: compiler_identity(name) for name in ("gcc", "clang")}
                  if not args.skip_invalidity_check else {})
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "mutants").mkdir()
    (run_dir / "attempts").mkdir()
    if fixed_version:
        (run_dir / "reward_scale.json").write_bytes(scale_bytes)
    state = selection_policies.new_state(args.policy, arms, ts, args.discount_gamma)
    atomic_json(run_dir / "bandit_state.json", state)
    (run_dir / "archive_store.jsonl").touch(exist_ok=False)
    atomic_json(run_dir / "manifest.json", {
        "policy_id": state["policy_id"], "policy": args.policy,
        "discount_gamma": args.discount_gamma if args.policy == "discounted-ts" else None,
        "rng_mode": args.rng_mode, "comparison_mode": args.comparison_mode,
        "comparison_config_sha256": comparison_digest,
        "reward_version": args.reward_version,
        "reward_scale_file": str(run_dir / "reward_scale.json") if fixed_version else None,
        "reward_scale_source": str(Path(args.reward_scale_file).resolve()) if fixed_version else None,
        "reward_scale_file_sha256": scale_sha256,
        "reward_config_id": (parameter_free_reward.config_id(args.reward_version, args.k)
                             if parameter_free_version else scale_id),
        "reward_scale": scale_value,
        "view_mode": view_mode, "embedding_pipeline_version": pipeline,
        "provider": args.provider, "model": args.model, "k": args.k, "m": args.m,
        "dimensions_requested": args.dimensions, "calibration_cap": args.calibration_cap,
        "calibration_seed": calibration_seed,
        "view_max_tokens": 7800,
        "chunk_max_tokens": args.chunk_max_tokens,
        "chunk_overlap_tokens": args.chunk_overlap_tokens,
        "chunk_max_chars": args.chunk_max_chars,
        "chunk_overlap_chars": args.chunk_overlap_chars,
        "arms_file": str(Path(args.arms_file).resolve()),
        "arms_sha256": arms_digest,
        "seeds": [{"file": str(s), "sha256": hashlib.sha256(s.read_bytes()).hexdigest()} for s in seeds],
        "random_seed": args.random_seed,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "invalidity_check": not args.skip_invalidity_check,
        "api_request_limit": args.api_request_limit,
        "api_max_retries": args.api_max_retries,
        "compiler_identities": identities,
        "code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in ["scripts/52_ts_state.py", "scripts/53_reward_one_c.py",
                                     "scripts/60_run_arm_one.py", "scripts/62_pick_arms.py",
                                     "scripts/70_multi_mutate_one.py", "scripts/72_preprocess_for_embedding.py",
                                     "scripts/92_corrected_eidetic.py", "src/edgm/corrected_reward.py",
                                     "src/edgm/selection_policies.py", "src/edgm/diversity_monitor.py",
                                     "src/edgm/fixed_scale_reward.py",
                                     "src/edgm/parameter_free_reward.py"]}})
    accepted = 0
    stop_reason = "attempt_limit"
    validated_seeds = {}
    if not args.skip_invalidity_check:
        include = ROOT / "src/csmithedge/csmith/runtime"
    for attempt in range(1, args.max_attempts + 1):
        attempt_started = time.perf_counter()
        if accepted >= args.iterations:
            stop_reason = "accepted_target"
            break
        if (run_dir / "PENDING_COMMIT.json").exists():
            raise RuntimeError("incomplete prior state/archive commit")
        if fixed_version and hashlib.sha256((run_dir / "reward_scale.json").read_bytes()).hexdigest() != scale_sha256:
            raise RuntimeError("reward calibration changed during run")
        if args.provider == "dashscope" and args.api_request_limit:
            ledger = run_dir / "api_requests.jsonl"
            used = sum(json.loads(line).get("phase") == "start"
                       for line in ledger.read_text(encoding="utf-8").splitlines()) if ledger.exists() else 0
            if used >= args.api_request_limit:
                stop_reason = "api_request_limit"
                break
        # The disk state is reloaded before EVERY decision.
        state = ts.load_state(run_dir / "bandit_state.json")
        seed = seed_rng.choice(seeds)
        attempt_id = f"{attempt:06d}_{uuid.uuid4().hex[:8]}"
        out = run_dir / "mutants" / (attempt_id + ".chain.c")
        if not args.skip_invalidity_check:
            if seed not in validated_seeds:
                validated_seeds[seed] = compiler_pair(identities, seed, include,
                    run_dir / "diagnostics", "seed_" + hashlib.sha256(seed.read_bytes()).hexdigest()[:16])
            seed_checks = validated_seeds[seed]
            if any(v["timed_out"] or v["exception"] for v in seed_checks.values()):
                raise RuntimeError(f"seed compiler check failed: {seed}")
            if not all(v["exit_code"] == 0 for v in seed_checks.values()):
                append_jsonl(run_dir / "events.jsonl", {"attempt": attempt, "id": attempt_id,
                    "seed": str(seed), "status": "invalid_seed", "selected_arms": [],
                    "attempted_arms": [], "applied_arms": [], "policy_id": state["policy_id"],
                    "policy_updates_before": state["updates"], "seed_checks": seed_checks,
                    "attempt_elapsed_seconds": time.perf_counter() - attempt_started})
                continue
        chosen = selection_policies.pick(args.policy, state, args.m, policy_rng, ts)
        mutation_seed = mutation_rng.randrange(2**31)
        event = {"attempt": attempt, "id": attempt_id, "policy_id": state["policy_id"],
                 "seed": str(seed), "selected_arms": chosen, "policy_updates_before": state["updates"]}
        if args.policy == "beta-ts":
            event["ts_updates_before"] = state["updates"]
        if not args.skip_invalidity_check:
            event["seed_checks"] = seed_checks
        event["mutation_seed"] = mutation_seed
        cmd = [sys.executable, str(ROOT / "scripts/70_multi_mutate_one.py"),
               "--seed", str(seed), "--arms", ",".join(chosen), "--out", str(out),
               "--workdir", str(run_dir / "attempts"), "--require-complete",
               "--mutation-seed", str(mutation_seed)]
        rc, stdout, stderr = run_command(cmd)
        attempt_record = Path(str(out) + ".attempt.json")
        chain = json.loads(attempt_record.read_text()) if attempt_record.exists() else {}
        event.update({"attempted_arms": chain.get("attempted_arms", []),
                      "applied_arms": chain.get("applied_arms", []), "mutation_rc": rc,
                      "mutation_stdout": stdout[-2000:], "mutation_stderr": stderr[-2000:]})
        if rc != 0 or not out.is_file() or out.stat().st_size == 0 or out.read_bytes() == seed.read_bytes():
            event["status"] = "mutation_failed"
            event["attempt_elapsed_seconds"] = time.perf_counter() - attempt_started
            append_jsonl(run_dir / "events.jsonl", event)
            continue
        if not args.skip_invalidity_check:
            checks = compiler_pair(identities, out, include, run_dir / "diagnostics", attempt_id)
            event["compiler_checks"] = checks
            if any(v["timed_out"] or v["exception"] for v in checks.values()):
                event["status"] = "compiler_check_failed"
                event["attempt_elapsed_seconds"] = time.perf_counter() - attempt_started
                append_jsonl(run_dir / "events.jsonl", event)
                raise RuntimeError(f"mutant compiler check failed: {out}")
            event["meets_acceptance_condition"] = any(v["exit_code"] != 0 for v in checks.values())
            if not event["meets_acceptance_condition"]:
                event["status"] = "not_invalid"
                event["attempt_elapsed_seconds"] = time.perf_counter() - attempt_started
                append_jsonl(run_dir / "events.jsonl", event)
                continue
        original = out.read_text(encoding="utf-8", errors="ignore")
        out.write_text("/* EDGM_APPLIED_ARMS: " + ",".join(chain["applied_arms"]) + " */\n" + original,
                       encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "scripts/53_reward_one_c.py"), str(out),
               "--reward-version", args.reward_version, "--archive-store", str(run_dir / "archive_store.jsonl"),
               "--cache-dir", str(run_dir / "embedding_cache"), "--view-dir", str(run_dir / "embedding_view"),
               "--calibration-cache-dir", str(run_dir / "calibration_cache"),
               "--calibration-cap", str(args.calibration_cap), "--calibration-seed", str(calibration_seed),
               "--view-mode", view_mode, "--embedding-pipeline", pipeline,
               "--chunk-max-tokens", str(args.chunk_max_tokens),
               "--chunk-overlap-tokens", str(args.chunk_overlap_tokens),
               "--chunk-max-chars", str(args.chunk_max_chars),
               "--chunk-overlap-chars", str(args.chunk_overlap_chars),
               "--provider", args.provider, "--model", args.model, "--k", str(args.k),
               "--dimensions", str(args.dimensions),
               "--api-log", str(run_dir / "api_requests.jsonl"),
               "--api-request-limit", str(args.api_request_limit),
               "--api-max-retries", str(args.api_max_retries)]
        if fixed_version:
            cmd.extend(["--reward-scale-file", str(run_dir / "reward_scale.json")])
        if args.allow_mock:
            cmd.append("--allow-mock")
        rc, stdout, stderr = run_command(cmd)
        if rc != 0:
            duplicate = "duplicate embedding view" in stderr
            event.update({"status": "duplicate_view" if duplicate else "reward_failed",
                          "reward_stderr": clean_stderr(stderr)})
            event["attempt_elapsed_seconds"] = time.perf_counter() - attempt_started
            append_jsonl(run_dir / "events.jsonl", event)
            if duplicate:
                continue
            raise RuntimeError(f"reward failed for {out}: {clean_stderr(stderr)[-1000:]}")
        reward = json.loads(stdout.strip())
        event["api_usage"] = reward.get("api_usage")
        event["api_requests_total"] = reward.get("api_requests_total")
        event["raw_knn"] = reward.get("raw_knn", reward["knn_mean_dist"])
        existing = [json.loads(line) for line in (run_dir / "archive_store.jsonl").read_text().splitlines()]
        if len(existing) != reward["archive_size"]:
            raise RuntimeError("archive size changed before commit")
        event["diversity"] = projected_metrics(existing, reward["vec"])
        event["attempt_elapsed_seconds"] = time.perf_counter() - attempt_started
        atomic_json(run_dir / "mutants" / (attempt_id + ".reward.json"), reward)
        reward_then_commit(run_dir, state, chosen, reward, out, event, ts, args.policy)
        accepted += 1
        if accepted >= args.iterations:
            stop_reason = "accepted_target"
    atomic_json(run_dir / "run_summary.json", {"policy_id": state["policy_id"],
        "accepted": accepted, "target": args.iterations, "max_attempts": args.max_attempts,
        "stop_reason": stop_reason,
        "elapsed_seconds": time.perf_counter() - run_started})
    return accepted

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="new, nonexistent directory; never shares legacy state")
    seeds = ap.add_mutually_exclusive_group(required=True)
    seeds.add_argument("--seed")
    seeds.add_argument("--seed-dir")
    ap.add_argument("--arms-file", default=str(ROOT / "config/arms_list.txt"))
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--max-attempts", type=int, default=20)
    ap.add_argument("--m", type=int, default=2)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--random-seed", type=int, default=0)
    ap.add_argument("--policy", choices=selection_policies.POLICIES, default="beta-ts")
    ap.add_argument("--discount-gamma", type=float, default=0.97)
    ap.add_argument("--rng-mode", choices=("legacy", "split-v1"), default="legacy")
    ap.add_argument("--comparison-mode", action="store_true")
    ap.add_argument("--provider", default="dashscope")
    ap.add_argument("--model", default="text-embedding-v4")
    ap.add_argument("--dimensions", type=int, default=1024)
    ap.add_argument("--calibration-cap", type=int, default=128)
    ap.add_argument("--calibration-seed", type=int, default=None)
    ap.add_argument("--reward-version", choices=["corrected-loo-v1", "corrected-loo-v2", *fixed_scale_reward.VERSIONS, *parameter_free_reward.VERSIONS], default="corrected-loo-v2")
    ap.add_argument("--reward-scale-file", default="")
    ap.add_argument("--view-mode", choices=["auto", "legacy-operator-v1", "program-content-v2"], default="auto")
    ap.add_argument("--embedding-pipeline", choices=["auto", "legacy-chunk-v1", "complete-chunk-v2"], default="auto")
    ap.add_argument("--chunk-max-tokens", type=int, default=2000)
    ap.add_argument("--chunk-overlap-tokens", type=int, default=80)
    ap.add_argument("--chunk-max-chars", type=int, default=7000)
    ap.add_argument("--chunk-overlap-chars", type=int, default=200)
    ap.add_argument("--allow-mock", action="store_true")
    ap.add_argument("--api-request-limit", type=int, default=0)
    ap.add_argument("--api-max-retries", type=int, default=0)
    ap.add_argument("--skip-invalidity-check", action="store_true", help="test-only: do not validate compiler error")
    args = ap.parse_args()
    try:
        accepted = run(args)
    except Exception as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 2
    print(f"[OK] accepted={accepted}/{args.iterations} run_dir={Path(args.run_dir).resolve()}")
    return 0 if accepted == args.iterations else 1

if __name__ == "__main__":
    raise SystemExit(main())
