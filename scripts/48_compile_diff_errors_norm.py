#!/usr/bin/env python3
import argparse, json, os, re, shlex, subprocess, hashlib
from pathlib import Path
from datetime import datetime
from json import JSONDecoder

# ---------------- utils ----------------

def run_cmd(cmd, timeout, env):
    p = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, env=env
    )
    return p.returncode, p.stdout, p.stderr

def extract_first_json(text):
    """
    Extract and parse the first JSON value from a string.
    Works for:
      - GCC JSON diagnostics + trailing text ("compilation terminated.")
      - Clang SARIF JSON + trailing "N errors generated."
    """
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
    # sarif uses file:///abs/path
    if not p:
        return ""
    p = str(p)
    if p.startswith("file://"):
        return p[7:] if p.startswith("file:///") else p.replace("file://", "", 1)
    return p

def stable_id(path: Path):
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return f"{path.stem}__{h}"

# ---------------- diag_norm loader (simple YAML subset) ----------------
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

# ---------------- parsing diagnostics ----------------

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

# ---------------- NEW compare strategy (Doctor rule) ----------------
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

def same_errors_doctor(gcc_diags, clang_diags):
    mg = _line_to_cats(gcc_diags)
    mc = _line_to_cats(clang_diags)

    lg = _min_line(mg)
    lc = _min_line(mc)

    if lg is None and lc is None:
        return True
    if (lg is None) != (lc is None):
        return False
    if lg != lc:
        return False

    small, big = (mg, mc) if len(mg) <= len(mc) else (mc, mg)
    for ln, cats_small in small.items():
        if ln not in big:
            return False
        cats_big = big[ln]
        if ("unknown" in cats_small) or ("unknown" in cats_big):
            continue
        if len(cats_small & cats_big) == 0:
            return False
    return True

# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/mutants/queue", help="directory containing .c files (or a single .c file)")
    ap.add_argument("--out-root", default="", help="if set, write outputs under this directory (to avoid mixing pipelines)")
    ap.add_argument("--skip-existing", action="store_true", help="skip if meta/<sid>.json already exists under out-root")
    ap.add_argument("--gcc", default="gcc")
    ap.add_argument("--clang", default="clang")
    ap.add_argument("--std", default="c11")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--match-col", action="store_true")  # legacy
    ap.add_argument("--csmith-include", default="", help="optional include dir (legacy). if empty, auto use csmith runtime")
    ap.add_argument("--extra-cflags", default="", help="extra flags appended")
    ap.add_argument("--diag-norm", default="config/diag_norm.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]

    # choose output layout
    if args.out_root.strip():
        out_base = (root / args.out_root).resolve()
        out_incon = (out_base / "inconsistent_raw").resolve()
        out_comp  = (out_base / "compiled").resolve()
        out_meta  = (out_base / "meta").resolve()
        out_gcc   = (out_base / "diagnostics" / "gcc").resolve()
        out_clang = (out_base / "diagnostics" / "clang").resolve()
    else:
        out_incon = (root / "data/mutants/inconsistent_raw").resolve()
        out_comp  = (root / "data/mutants/compiled").resolve()
        out_meta  = (root / "data/mutants/meta").resolve()
        out_gcc   = (root / "data/diagnostics/gcc").resolve()
        out_clang = (root / "data/diagnostics/clang").resolve()

    for d in (out_incon, out_comp, out_meta, out_gcc, out_clang):
        d.mkdir(parents=True, exist_ok=True)

    inp = (root / args.input).resolve()
    if inp.exists() and inp.is_file():
        files = [inp]
    else:
        if inp.exists() and inp.is_dir():
            files = sorted([p for p in inp.glob("*.c") if p.is_file()])
        else:
            # allow glob pattern
            files = sorted([p for p in root.glob(args.input) if p.is_file() and p.suffix == ".c"])

    if args.limit and args.limit > 0:
        files = files[:args.limit]

    diag_norm_path = (root / args.diag_norm).resolve()
    default_cat, rules = load_diag_norm(diag_norm_path)

    # Only errors, not warnings.
    common = ["-std=" + args.std, "-fsyntax-only", "-w"]
    gcc_flags = common + ["-fdiagnostics-format=json", "-fmax-errors=1000", "-fno-diagnostics-show-caret"]
    clang_flags = common + ["-ferror-limit=0", "-fno-caret-diagnostics", "-fno-color-diagnostics", "-fdiagnostics-format=sarif"]

    extra = shlex.split(args.extra_cflags) if args.extra_cflags else []

    # auto include csmith runtime unless user explicitly set --csmith-include
    if args.csmith_include:
        inc = str((root / args.csmith_include).resolve()) if not os.path.isabs(args.csmith_include) else args.csmith_include
        extra = ["-I", inc] + extra
    else:
        runtime = root / "src" / "csmithedge" / "csmith" / "runtime"
        if runtime.exists():
            extra = ["-I", str(runtime.resolve())] + extra

    env = os.environ.copy()
    env["LC_ALL"] = "C"

    stats = {
        "total": 0,
        "both_ok": 0,
        "both_err_consistent": 0,
        "inconsistent": 0,
        "gcc_err_clang_ok": 0,
        "gcc_ok_clang_err": 0,
        "json_fail": 0,
        "skipped_existing": 0,
    }

    for src in files:
        sid = stable_id(src)

        meta_path = out_meta / f"{sid}.json"
        if args.skip_existing and meta_path.exists():
            stats["skipped_existing"] += 1
            continue

        stats["total"] += 1

        gcc_cmd = [args.gcc] + gcc_flags + extra + [str(src)]
        clang_cmd = [args.clang] + clang_flags + extra + [str(src)]

        if args.dry_run:
            print("GCC :", " ".join(map(shlex.quote, gcc_cmd)))
            print("CLANG:", " ".join(map(shlex.quote, clang_cmd)))
            print("diag_norm:", str(diag_norm_path))
            return 0

        try:
            gcc_rc, _gcc_out, gcc_err = run_cmd(gcc_cmd, args.timeout, env)
        except subprocess.TimeoutExpired:
            gcc_rc, gcc_err = 124, "TIMEOUT"
        try:
            clang_rc, _clang_out, clang_err = run_cmd(clang_cmd, args.timeout, env)
        except subprocess.TimeoutExpired:
            clang_rc, clang_err = 124, "TIMEOUT"

        gcc_json = extract_first_json(gcc_err)
        clang_json = extract_first_json(clang_err)

        (out_gcc / f"{sid}.stderr.txt").write_text(gcc_err, encoding="utf-8", errors="replace")
        (out_clang / f"{sid}.stderr.txt").write_text(clang_err, encoding="utf-8", errors="replace")

        if gcc_json is not None:
            (out_gcc / f"{sid}.json").write_text(json.dumps(gcc_json, indent=2), encoding="utf-8")
        if clang_json is not None:
            (out_clang / f"{sid}.sarif.json").write_text(json.dumps(clang_json, indent=2), encoding="utf-8")

        gcc_diags = parse_gcc_json(gcc_json, default_cat, rules) if gcc_json is not None else []
        if isinstance(clang_json, dict) and "runs" in clang_json:
            clang_diags = parse_clang_sarif(clang_json, default_cat, rules)
        else:
            clang_diags = parse_clang_json_like(clang_json, default_cat, rules) if clang_json is not None else []

        gcc_has_err = (gcc_rc != 0) or (len(gcc_diags) > 0)
        clang_has_err = (clang_rc != 0) or (len(clang_diags) > 0)

        if (gcc_has_err and gcc_json is None) or (clang_has_err and clang_json is None):
            stats["json_fail"] += 1

        record = {
            "src": str(src),
            "id": sid,
            "time": datetime.utcnow().isoformat() + "Z",
            "diag_norm": str(diag_norm_path),
            "sig_mode": "doctor_line_align(category∩≠∅)+earliest_line_equal",
            "gcc": {"rc": gcc_rc, "errcount": len(gcc_diags), "errors": gcc_diags},
            "clang": {"rc": clang_rc, "errcount": len(clang_diags), "errors": clang_diags},
        }

        if (not gcc_has_err) and (not clang_has_err):
            stats["both_ok"] += 1
            (out_comp / f"{sid}.c").write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            meta_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            continue

        if gcc_has_err and clang_has_err and same_errors_doctor(gcc_diags, clang_diags):
            stats["both_err_consistent"] += 1
            (out_comp / f"{sid}.c").write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            meta_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            continue

        stats["inconsistent"] += 1
        if gcc_has_err and (not clang_has_err):
            stats["gcc_err_clang_ok"] += 1
        if clang_has_err and (not gcc_has_err):
            stats["gcc_ok_clang_err"] += 1

        (out_incon / f"{sid}.c").write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        meta_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print("=== diff summary (errors only; category-mapped) ===")
    for k in ["total","skipped_existing","both_ok","both_err_consistent","inconsistent","gcc_err_clang_ok","gcc_ok_clang_err","json_fail"]:
        print(f"{k}: {stats[k]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
