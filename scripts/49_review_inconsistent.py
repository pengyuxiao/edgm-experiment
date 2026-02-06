#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Review GCC/Clang diagnostics concisely.
- Focus on ERROR only (ignore warnings).
- Summarize per-case sigset diff (category+sev+line by default).
- Show only first N raw errors (message + line/col) from GCC JSON and Clang SARIF.
"""

import argparse, glob, json, os, re
from collections import Counter
from json import JSONDecoder

DEC = JSONDecoder()

def load_json_allow_trailing(path: str):
    """Load a JSON object from a file that may have trailing text (e.g., '1 error generated.')."""
    s = open(path, "r", encoding="utf-8", errors="replace").read().lstrip()
    obj, idx = DEC.raw_decode(s)
    return obj

def try_load(path_candidates):
    for p in path_candidates:
        if p and os.path.exists(p):
            return p
    return None

def sig(e):
    # match sig_mode=category+sev+line: ignore col/file/code
    line = e.get("line")
    return (e.get("category"), e.get("sev"), line if line is not None else -1)

def first_norm_err(errs):
    if not errs:
        return None
    # line=None goes last
    es = sorted(errs, key=lambda x: (10**9 if x.get("line") is None else x["line"],
                                    10**9 if x.get("col") is None else (x.get("col") or 10**9)))
    e = es[0]
    return (e.get("category"), e.get("sev"), e.get("line"), e.get("col"), e.get("code"))

def extract_gcc_raw_errors(gcc_obj):
    """
    gcc_obj: list[dict] from -fdiagnostics-format=json (your tool output)
    Return list of (line, col, msg).
    """
    out = []
    if not isinstance(gcc_obj, list):
        return out
    for d in gcc_obj:
        kind = (d.get("kind") or "").lower()
        if kind not in ("error", "fatal error", "fatal"):
            continue
        msg = d.get("message", "")
        line = col = None
        locs = d.get("locations") or []
        if locs:
            caret = (locs[0].get("caret") or {})
            line = caret.get("line")
            col = caret.get("column") or caret.get("display-column") or caret.get("byte-column")
        out.append((line, col, msg))
    # sort
    out.sort(key=lambda t: (10**9 if t[0] is None else t[0], 10**9 if t[1] is None else t[1]))
    return out

def extract_clang_sarif_errors(sarif_obj):
    """
    sarif_obj: dict (SARIF)
    Return list of (line, col, msg).
    """
    out = []
    if not isinstance(sarif_obj, dict):
        return out
    runs = sarif_obj.get("runs") or []
    for r in runs:
        results = r.get("results") or []
        for res in results:
            level = (res.get("level") or "").lower()
            if level != "error":
                continue
            msg = ((res.get("message") or {}).get("text")) or ""
            line = col = None
            locs = res.get("locations") or []
            if locs:
                pl = (locs[0].get("physicalLocation") or {})
                reg = (pl.get("region") or {})
                line = reg.get("startLine")
                col = reg.get("startColumn")
            out.append((line, col, msg))
    out.sort(key=lambda t: (10**9 if t[0] is None else t[0], 10**9 if t[1] is None else t[1]))
    return out

def short_sig_list(counter: Counter, k=6):
    items = list(counter.items())
    items.sort(key=lambda x: (-x[1], x[0]))
    return items[:k]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta-dir", default="data/mutants/meta")
    ap.add_argument("--gcc-dir", default="data/diagnostics/gcc")
    ap.add_argument("--clang-dir", default="data/diagnostics/clang")
    ap.add_argument("--mode", choices=["list", "show"], default="list",
                    help="list: show all inconsistent(sigset) cases; show: show one case in detail")
    ap.add_argument("--id", default=None, help="case id (basename without .json) for --mode show")
    ap.add_argument("--max", type=int, default=200, help="max cases to print in list mode")
    ap.add_argument("--raw-n", type=int, default=5, help="show first N raw errors per compiler")
    ap.add_argument("--norm-n", type=int, default=8, help="show first N normalized errors per compiler")
    args = ap.parse_args()

    meta_paths = sorted(glob.glob(os.path.join(args.meta_dir, "*.json")))
    if not meta_paths:
        print(f"[!] no meta json found in {args.meta_dir}")
        return

    # collect inconsistent(sigset)
    incs = []
    for mp in meta_paths:
        o = json.load(open(mp, "r", encoding="utf-8"))
        g_err = (o["gcc"]["rc"] != 0) or (o["gcc"]["errcount"] > 0)
        c_err = (o["clang"]["rc"] != 0) or (o["clang"]["errcount"] > 0)
        if not (g_err and c_err):
            continue
        g = o["gcc"]["errors"]
        c = o["clang"]["errors"]
        cg = Counter(map(sig, g))
        cc = Counter(map(sig, c))
        if cg != cc:
            incs.append((mp, o, cg, cc))

    if args.mode == "list":
        print(f"inconsistent(sigset): {len(incs)}")
        for (mp, o, cg, cc) in incs[:args.max]:
            id_ = os.path.splitext(os.path.basename(mp))[0]
            src = o.get("src", "")
            gN = len(o["gcc"]["errors"])
            cN = len(o["clang"]["errors"])
            g_first = first_norm_err(o["gcc"]["errors"])
            c_first = first_norm_err(o["clang"]["errors"])
            unk_g = sum(1 for e in o["gcc"]["errors"] if e.get("category") == "unknown")
            unk_c = sum(1 for e in o["clang"]["errors"] if e.get("category") == "unknown")
            only_g = cg - cc
            only_c = cc - cg

            print("\n==", id_, "==")
            print("src:", src)
            print(f"norm_err_count: gcc={gN} (unknown={unk_g})  clang={cN} (unknown={unk_c})")
            print("first_norm: gcc=", g_first, " clang=", c_first)
            print("gcc_only(sig x count):", short_sig_list(only_g, k=6))
            print("clang_only(sig x count):", short_sig_list(only_c, k=6))

        print("\nTip: to inspect one case in detail:")
        print("  python3 scripts/49_review_inconsistent.py --mode show --id <ID>")
        return

    # show mode
    if not args.id:
        print("[!] --id is required for --mode show")
        return

    mp = os.path.join(args.meta_dir, args.id + ".json")
    if not os.path.exists(mp):
        print(f"[!] meta not found: {mp}")
        return
    o = json.load(open(mp, "r", encoding="utf-8"))
    src = o.get("src", "")

    # locate raw diag files
    gcc_json = try_load([
        os.path.join(args.gcc_dir, args.id + ".json"),
        os.path.join(args.gcc_dir, args.id + ".stderr.txt"),
    ])
    clang_sarif = try_load([
        os.path.join(args.clang_dir, args.id + ".sarif.json"),
        os.path.join(args.clang_dir, args.id + ".stderr.txt"),
    ])

    print("==", args.id, "==")
    print("src:", src)
    print("gcc rc/errcount:", o["gcc"]["rc"], o["gcc"]["errcount"])
    print("clang rc/errcount:", o["clang"]["rc"], o["clang"]["errcount"])

    # normalized errors (already filtered to errors by your pipeline)
    def print_norm(side):
        errs = o[side]["errors"][:args.norm_n]
        print(f"\n[{side}] normalized errors (first {args.norm_n}):")
        for e in errs:
            print(" ", (e.get("category"), e.get("sev"), e.get("line"), e.get("col"), e.get("code")))

    print_norm("gcc")
    print_norm("clang")

    # sigset diff
    cg = Counter(map(sig, o["gcc"]["errors"]))
    cc = Counter(map(sig, o["clang"]["errors"]))
    print("\n[sigset diff] gcc_only:", short_sig_list(cg - cc, k=10))
    print("[sigset diff] clang_only:", short_sig_list(cc - cg, k=10))

    # raw errors (message+line/col)
    print("\n[raw errors] (ERROR only, ignore warnings)")
    if gcc_json:
        try:
            if gcc_json.endswith(".json"):
                gcc_obj = json.load(open(gcc_json, "r", encoding="utf-8"))
            else:
                gcc_obj = load_json_allow_trailing(gcc_json)
            g_raw = extract_gcc_raw_errors(gcc_obj)[:args.raw_n]
            print(f"\n[gcc] from {gcc_json} (first {args.raw_n} errors):")
            for (ln, col, msg) in g_raw:
                print(f"  L{ln}:C{col}  {msg}")
        except Exception as e:
            print(f"\n[gcc] failed to parse {gcc_json}: {e}")
    else:
        print("\n[gcc] raw diag file not found")

    if clang_sarif:
        try:
            sarif_obj = load_json_allow_trailing(clang_sarif)
            c_raw = extract_clang_sarif_errors(sarif_obj)[:args.raw_n]
            print(f"\n[clang] from {clang_sarif} (first {args.raw_n} errors):")
            for (ln, col, msg) in c_raw:
                print(f"  L{ln}:C{col}  {msg}")
        except Exception as e:
            print(f"\n[clang] failed to parse {clang_sarif}: {e}")
    else:
        print("\n[clang] raw diag file not found")

    # practical “open around line” helper
    # choose first available line from normalized gcc/clang
    lines = []
    for side in ("gcc", "clang"):
        for e in o[side]["errors"]:
            if isinstance(e.get("line"), int):
                lines.append(e["line"])
                break
    if src and lines:
        L = lines[0]
        lo = max(1, L - 3)
        hi = L + 3
        print("\n[quick view source around first error line]")
        print(f"  sed -n '{lo},{hi}p' '{src}'")

if __name__ == "__main__":
    main()
