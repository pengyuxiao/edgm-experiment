#!/usr/bin/env python3
import argparse, json, os, re, shlex, subprocess, hashlib
from pathlib import Path
from datetime import datetime
from json import JSONDecoder

def run_cmd(cmd, timeout, env):
    p = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, env=env
    )
    return p.returncode, p.stdout, p.stderr

def extract_first_json(text):
    s = (text or "")
    m = re.search(r'[\{\[]', s)
    if not m:
        return None
    s = s[m.start():].lstrip()
    try:
        obj, _idx = JSONDecoder().raw_decode(s)
        return obj
    except Exception:
        return None

def norm_sev(x):
    x = ("" if x is None else str(x)).lower()
    if "fatal" in x:
        return "fatal"
    if "error" in x:
        return "error"
    if "warning" in x:
        return "warning"
    if "note" in x:
        return "note"
    return x

def safe_int(x):
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        xs = x.strip()
        if xs.isdigit():
            return int(xs)
    return None

def strip_file_uri(p):
    if not p:
        return ""
    p = str(p)
    if p.startswith("file://"):
        return p[7:] if p.startswith("file:///") else p.replace("file://", "", 1)
    return p

def stable_id(path: Path):
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return f"{path.stem}__{h}"

def load_diag_norm(path: Path):
    if not path.exists():
        return "unknown", []

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    default_category = "unknown"
    rules = []
    cur = None
    in_rules = False

    def strip_comment(line):
        return line.split("#", 1)[0].rstrip()

    for raw in lines:
        line = strip_comment(raw).rstrip()
        if not line.strip():
            continue

        if re.match(r'^\s*default_category\s*:\s*', line):
            default_category = line.split(":", 1)[1].strip() or default_category
            continue

        if re.match(r'^\s*rules\s*:\s*$', line):
            in_rules = True
            continue

        if not in_rules:
            continue

        m = re.match(r'^\s*-\s*category\s*:\s*(.+)\s*$', line)
        if m:
            if cur:
                rules.append(cur)
            cur = {"category": m.group(1).strip(), "compiler": "", "field": "", "regex": ""}
            continue

        m = re.match(r'^\s*compiler\s*:\s*(.+)\s*$', line)
        if m and cur is not None:
            cur["compiler"] = m.group(1).strip()
            continue

        m = re.match(r'^\s*field\s*:\s*(.+)\s*$', line)
        if m and cur is not None:
            cur["field"] = m.group(1).strip()
            continue

        m = re.match(r'^\s*regex\s*:\s*(.+)\s*$', line)
        if m and cur is not None:
            r = m.group(1).strip()
            if (r.startswith("'") and r.endswith("'")) or (r.startswith('"') and r.endswith('"')):
                r = r[1:-1]
            r = r.strip().lstrip("'\"").rstrip("'\"").strip()
            cur["regex"] = r
            continue

    if cur:
        rules.append(cur)

    compiled = []
    for r in rules:
        try:
            compiled.append({
                "category": r["category"],
                "compiler": r["compiler"],
                "field": r["field"],
                "re": re.compile(r["regex"])
            })
        except re.error:
            continue
    return default_category, compiled

def get_field(obj, field_path):
    if not isinstance(obj, dict) or not field_path:
        return ""
    cur = obj
    for k in field_path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(k)
        if cur is None:
            return ""
    return "" if cur is None else str(cur)

def map_category(compiler, diag, default_cat, rules):
    for r in rules:
        if r["compiler"] != compiler:
            continue
        val = get_field(diag, r["field"])
        if not val:
            continue
        if r["re"].search(val):
            return r["category"]
    return default_cat

def pick_loc_gcc(diag):
    if not isinstance(diag, dict):
        return "", None, None
    locs = diag.get("locations")
    if isinstance(locs, list) and locs:
        first = locs[0]
        if isinstance(first, dict):
            caret = first.get("caret")
            if isinstance(caret, dict):
                return caret.get("file",""), caret.get("line"), caret.get("column") or caret.get("col")
            return first.get("file",""), first.get("line"), first.get("column") or first.get("col")
    return "", None, None

def pick_loc_clang_json(diag):
    if not isinstance(diag, dict):
        return "", None, None
    loc = diag.get("location")
    if isinstance(loc, dict):
        return loc.get("file",""), loc.get("line"), loc.get("col")
    return "", None, None

def parse_gcc_json(obj, default_cat, rules):
    diags = []
    if obj is None:
        return diags

    def add_one(d):
        if not isinstance(d, dict):
            return
        sev = norm_sev(d.get("kind") or d.get("level") or d.get("severity") or d.get("type"))
        if sev not in ("error", "fatal"):
            return
        file, line, col = pick_loc_gcc(d)
        cat = map_category("gcc", d, default_cat, rules)
        code = d.get("diag_name") or d.get("option") or d.get("flag") or ""
        diags.append({
            "category": cat,
            "sev": sev,
            "file": strip_file_uri(file),
            "line": safe_int(line),
            "col": safe_int(col),
            "code": str(code) if code is not None else ""
        })

    if isinstance(obj, list):
        for d in obj:
            add_one(d)
    elif isinstance(obj, dict):
        if isinstance(obj.get("diagnostics"), list):
            for d in obj["diagnostics"]:
                add_one(d)
        else:
            for v in obj.values():
                if isinstance(v, list):
                    for d in v:
                        add_one(d)
    return diags

def parse_clang_sarif(obj, default_cat, rules):
    diags = []
    if obj is None or not isinstance(obj, dict):
        return diags
    if "runs" not in obj:
        return diags

    runs = obj.get("runs")
    if not isinstance(runs, list):
        return diags

    for run in runs:
        if not isinstance(run, dict):
            continue
        results = run.get("results")
        if not isinstance(results, list):
            continue
        for r in results:
            if not isinstance(r, dict):
                continue
            sev = norm_sev(r.get("level"))
            if sev not in ("error", "fatal"):
                continue

            file = ""
            line = None
            col = None
            locs = r.get("locations")
            if isinstance(locs, list) and locs:
                pl = locs[0].get("physicalLocation") if isinstance(locs[0], dict) else None
                if isinstance(pl, dict):
                    al = pl.get("artifactLocation")
                    reg = pl.get("region")
                    if isinstance(al, dict):
                        file = al.get("uri", "") or ""
                    if isinstance(reg, dict):
                        line = reg.get("startLine")
                        col = reg.get("startColumn")

            msg = ""
            m = r.get("message")
            if isinstance(m, dict):
                msg = m.get("text", "") or ""
            diag_for_map = {
                "message": msg,
                "ruleId": str(r.get("ruleId", "") or ""),
                "level": str(r.get("level", "") or ""),
            }
            cat = map_category("clang", diag_for_map, default_cat, rules)

            diags.append({
                "category": cat,
                "sev": sev,
                "file": strip_file_uri(file),
                "line": safe_int(line),
                "col": safe_int(col),
                "code": str(r.get("ruleId") or "")
            })
    return diags

def parse_clang_json_like(obj, default_cat, rules):
    diags = []
    if obj is None:
        return diags

    def add_one(d):
        if not isinstance(d, dict):
            return
        sev = norm_sev(d.get("kind") or d.get("level") or d.get("severity") or d.get("type"))
        if sev not in ("error", "fatal"):
            return
        file, line, col = pick_loc_clang_json(d)
        cat = map_category("clang", d, default_cat, rules)
        code = d.get("diag_name") or d.get("option") or d.get("flag") or d.get("category") or ""
        diags.append({
            "category": cat,
            "sev": sev,
            "file": strip_file_uri(file),
            "line": safe_int(line),
            "col": safe_int(col),
            "code": str(code) if code is not None else ""
        })

    if isinstance(obj, dict) and isinstance(obj.get("diagnostics"), list):
        for d in obj["diagnostics"]:
            add_one(d)
    elif isinstance(obj, list):
        for d in obj:
            add_one(d)
    return diags

def _line_to_cats(diags):
    mp = {}
    for d in diags:
        ln = d.get("line")
        if ln is None:
            continue
        mp.setdefault(int(ln), set()).add(d.get("category") or "unknown")
    return mp

def _min_line(mp):
    return min(mp.keys()) if mp else None

def same_errors_doctor(diags_a, diags_b):
    ma = _line_to_cats(diags_a)
    mb = _line_to_cats(diags_b)

    la = _min_line(ma)
    lb = _min_line(mb)

    if la is None and lb is None:
        return True
    if (la is None) != (lb is None):
        return False
    if la != lb:
        return False

    small, big = (ma, mb) if len(ma) <= len(mb) else (mb, ma)
    for ln, cats_small in small.items():
        if ln not in big:
            return False
        cats_big = big[ln]
        if ("unknown" in cats_small) or ("unknown" in cats_big):
            continue
        if len(cats_small & cats_big) == 0:
            return False
    return True

def main():
    ap = argparse.ArgumentParser(description="Same compiler, different -O levels: compare normalized error categories/lines")
    ap.add_argument("--input", default="data/mutants/final", help="dir of .c files or a single .c")
    ap.add_argument("--out-root", default="data/opt_diff", help="output root")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--compiler", choices=["gcc","clang"], required=True)
    ap.add_argument("--gcc", default="gcc")
    ap.add_argument("--clang", default="clang")
    ap.add_argument("--opt-a", default="O0")
    ap.add_argument("--opt-b", default="O2")
    ap.add_argument("--std", default="c11")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--csmith-include", default="")
    ap.add_argument("--extra-cflags", default="")
    ap.add_argument("--diag-norm", default="config/diag_norm.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    inp = (root / args.input).resolve()

    if inp.exists() and inp.is_file():
        files = [inp]
    elif inp.exists() and inp.is_dir():
        files = sorted([p for p in inp.glob("*.c") if p.is_file()])
    else:
        files = sorted([p for p in root.glob(args.input) if p.is_file() and p.suffix == ".c"])

    if args.limit and args.limit > 0:
        files = files[:args.limit]

    out_base = (root / args.out_root).resolve()
    tag = f"{args.compiler}_{args.opt_a}_vs_{args.opt_b}"
    out_base = out_base / tag

    out_incon = (out_base / "inconsistent_raw").resolve()
    out_comp  = (out_base / "compiled").resolve()
    out_meta  = (out_base / "meta").resolve()

    out_a_stderr = (out_base / "diagnostics" / args.compiler / args.opt_a / "stderr").resolve()
    out_b_stderr = (out_base / "diagnostics" / args.compiler / args.opt_b / "stderr").resolve()
    out_a_json   = (out_base / "diagnostics" / args.compiler / args.opt_a / "json").resolve()
    out_b_json   = (out_base / "diagnostics" / args.compiler / args.opt_b / "json").resolve()

    for d in (out_incon, out_comp, out_meta, out_a_stderr, out_b_stderr, out_a_json, out_b_json):
        d.mkdir(parents=True, exist_ok=True)

    diag_norm_path = (root / args.diag_norm).resolve()
    default_cat, rules = load_diag_norm(diag_norm_path)

    common = ["-std=" + args.std, "-w", "-c"]
    extra = shlex.split(args.extra_cflags) if args.extra_cflags else []

    if args.csmith_include:
        inc = str((root / args.csmith_include).resolve()) if not os.path.isabs(args.csmith_include) else args.csmith_include
        extra = ["-I", inc] + extra
    else:
        runtime = root / "src" / "csmithedge" / "csmith" / "runtime"
        if runtime.exists():
            extra = ["-I", str(runtime.resolve())] + extra

    if args.compiler == "gcc":
        cc = args.gcc
        fmt_flags = ["-fdiagnostics-format=json", "-fmax-errors=1000", "-fno-diagnostics-show-caret"]
    else:
        cc = args.clang
        fmt_flags = ["-ferror-limit=0", "-fno-caret-diagnostics", "-fno-color-diagnostics", "-fdiagnostics-format=sarif"]

    env = os.environ.copy()
    env["LC_ALL"] = "C"

    stats = {
        "total": 0,
        "both_ok": 0,
        "both_err_consistent": 0,
        "inconsistent": 0,
        "a_err_b_ok": 0,
        "a_ok_b_err": 0,
        "json_fail": 0,
        "skipped_existing": 0,
    }

    optA = "-" + args.opt_a if not args.opt_a.startswith("-") else args.opt_a
    optB = "-" + args.opt_b if not args.opt_b.startswith("-") else args.opt_b

    for src in files:
        sid = stable_id(src)
        meta_path = out_meta / f"{sid}.json"
        if args.skip_existing and meta_path.exists():
            stats["skipped_existing"] += 1
            continue

        stats["total"] += 1

        cmdA = [cc] + common + [optA] + fmt_flags + extra + [str(src)]
        cmdB = [cc] + common + [optB] + fmt_flags + extra + [str(src)]

        if args.dry_run:
            print("A:", " ".join(map(shlex.quote, cmdA)))
            print("B:", " ".join(map(shlex.quote, cmdB)))
            print("diag_norm:", str(diag_norm_path))
            return 0

        try:
            rcA, _outA, errA = run_cmd(cmdA, args.timeout, env)
        except subprocess.TimeoutExpired:
            rcA, errA = 124, "TIMEOUT"
        try:
            rcB, _outB, errB = run_cmd(cmdB, args.timeout, env)
        except subprocess.TimeoutExpired:
            rcB, errB = 124, "TIMEOUT"

        objA = extract_first_json(errA)
        objB = extract_first_json(errB)

        (out_a_stderr / f"{sid}.stderr.txt").write_text(errA, encoding="utf-8", errors="replace")
        (out_b_stderr / f"{sid}.stderr.txt").write_text(errB, encoding="utf-8", errors="replace")

        if objA is not None:
            suf = "json" if args.compiler == "gcc" else "sarif.json"
            (out_a_json / f"{sid}.{suf}").write_text(json.dumps(objA, indent=2), encoding="utf-8")
        if objB is not None:
            suf = "json" if args.compiler == "gcc" else "sarif.json"
            (out_b_json / f"{sid}.{suf}").write_text(json.dumps(objB, indent=2), encoding="utf-8")

        if args.compiler == "gcc":
            diA = parse_gcc_json(objA, default_cat, rules) if objA is not None else []
            diB = parse_gcc_json(objB, default_cat, rules) if objB is not None else []
        else:
            diA = parse_clang_sarif(objA, default_cat, rules) if (isinstance(objA, dict) and "runs" in objA) else parse_clang_json_like(objA, default_cat, rules)
            diB = parse_clang_sarif(objB, default_cat, rules) if (isinstance(objB, dict) and "runs" in objB) else parse_clang_json_like(objB, default_cat, rules)

        hasA = (rcA != 0) or (len(diA) > 0)
        hasB = (rcB != 0) or (len(diB) > 0)

        if (hasA and objA is None) or (hasB and objB is None):
            stats["json_fail"] += 1

        record = {
            "src": str(src),
            "id": sid,
            "time": datetime.utcnow().isoformat() + "Z",
            "diag_norm": str(diag_norm_path),
            "compiler": args.compiler,
            "opt_a": args.opt_a,
            "opt_b": args.opt_b,
            "sig_mode": "doctor_line_align(category∩≠∅)+earliest_line_equal",
            "A": {"rc": rcA, "errcount": len(diA), "errors": diA},
            "B": {"rc": rcB, "errcount": len(diB), "errors": diB},
        }

        if (not hasA) and (not hasB):
            stats["both_ok"] += 1
            (out_comp / f"{sid}.c").write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            meta_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            continue

        if hasA and hasB and same_errors_doctor(diA, diB):
            stats["both_err_consistent"] += 1
            (out_comp / f"{sid}.c").write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            meta_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            continue

        stats["inconsistent"] += 1
        if hasA and (not hasB):
            stats["a_err_b_ok"] += 1
        if hasB and (not hasA):
            stats["a_ok_b_err"] += 1

        (out_incon / f"{sid}.c").write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        meta_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print("=== opt-level diff summary (errors only; category-mapped) ===")
    for k in ["total","skipped_existing","both_ok","both_err_consistent","inconsistent","a_err_b_ok","a_ok_b_err","json_fail"]:
        print(f"{k}: {stats[k]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
