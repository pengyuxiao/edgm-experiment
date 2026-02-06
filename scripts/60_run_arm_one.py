#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import shutil
import argparse
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BLACKLIST_NAMES = {
    "List", "Tuple", "Optional", "Path", "dataclass",
    "Dict", "Set", "Any", "Callable", "Union",
}
PREFIXES = ("insert_", "delete_", "prune_", "swap_", "mutate_")

def guess_module_from_arm(arm: str) -> str:
    a = arm.strip()
    if a.startswith("edgm.mutators."):
        return a
    if a.endswith(".py"):
        a = Path(a).name[:-3]
    parts = a.split("_", 1)
    if len(parts) == 2 and parts[0].isdigit():
        a = parts[1]
    if a.startswith("mutate_"):
        a = a[len("mutate_"):]
    if "_mutate_" in a:
        a = a.split("_mutate_", 1)[1]
    return "edgm.mutators." + a

def list_callables(mod):
    names = []
    for n in dir(mod):
        if n.startswith("_"):
            continue
        if n in BLACKLIST_NAMES:
            continue
        obj = getattr(mod, n)
        if callable(obj):
            names.append(n)
    return names

def pick_entry_function(mod):
    names = list_callables(mod)
    if "mutate" in names:
        return "mutate", names
    cand = [n for n in names if n.startswith(PREFIXES)]
    if len(cand) == 1:
        return cand[0], names
    if len(cand) > 1:
        cand.sort(key=lambda x: (len(x), x))
        return cand[0], names
    if len(names) == 1:
        return names[0], names
    return None, names

def ensure_parent_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

def run_syntax_check(cmd: str, src: Path, include_dir: Path) -> int:
    import subprocess
    p = subprocess.run(
        [cmd, "-std=c11", "-fsyntax-only", "-w", "-I", str(include_dir), str(src)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return p.returncode

def require_error_ok(mode: str, outp: Path, include_dir: Path) -> bool:
    if mode == "none":
        return True
    gcc_rc   = run_syntax_check("gcc", outp, include_dir)
    clang_rc = run_syntax_check("clang", outp, include_dir)
    if mode == "gcc":
        return gcc_rc != 0
    if mode == "clang":
        return clang_rc != 0
    if mode == "any":
        return (gcc_rc != 0) or (clang_rc != 0)
    if mode == "both":
        return (gcc_rc != 0) and (clang_rc != 0)
    raise ValueError(f"unknown require-error mode: {mode}")

def drop_output(outp: Path, meta: Path, drop_dir: Path | None, reason: str):
    if drop_dir is None:
        outp.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        print(f"[DROP] {outp.name} {reason}（已删除）")
        return
    drop_dir.mkdir(parents=True, exist_ok=True)
    if outp.exists():
        shutil.move(str(outp), str(drop_dir / outp.name))
    if meta.exists():
        shutil.move(str(meta), str(drop_dir / meta.name))
    print(f"[DROP] {outp.name} {reason}（已移入 {drop_dir}）")

def resolve_from_wrapper_script(arm: str):
    """
    If arm is like '19_mutate_param_type_conflict.py', try parse scripts/<arm>
    and extract: from edgm.mutators.X import Y
    Return (module, fn) or (None, None)
    """
    name = arm.strip()
    if not name.endswith(".py"):
        return None, None

    p = Path(name)
    if not p.is_file():
        # try ROOT/scripts/<arm>
        p2 = ROOT / "scripts" / name
        if p2.is_file():
            p = p2
        else:
            return None, None

    txt = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'^\s*from\s+(edgm\.mutators\.[A-Za-z0-9_]+)\s+import\s+([A-Za-z0-9_]+)\s*$', txt, re.M)
    if not m:
        return None, None
    return m.group(1), m.group(2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, help="Arm name, e.g. 31_mutate_swap_call_args_mismatch.py")
    ap.add_argument("--in",  dest="inp",  default="", help="Input parent C file")
    ap.add_argument("--out", dest="outp", default="", help="Output mutant C file")
    ap.add_argument("--mkdir", action="store_true", help="Create parent dir for --out")
    ap.add_argument("--require-error", default="none",
                    choices=["none", "gcc", "clang", "any", "both"],
                    help="Only keep mutants that trigger syntax error (based on -fsyntax-only)")
    ap.add_argument("--drop-dir", default="", help="If set, move dropped outputs here; empty means delete")
    ap.add_argument("--print-entry", action="store_true", help="Print resolved module and entry fn, then exit")
    args = ap.parse_args()

    # 1) resolve module+fn
    modname, chosen_name = resolve_from_wrapper_script(args.arm)

    if modname:
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            raise SystemExit(f"[ERR] import failed: {modname}: {e}")
        if not hasattr(mod, chosen_name):
            raise SystemExit(f"[ERR] wrapper says fn={chosen_name} but not found in {modname}")
        # wrapper-resolved; no need to guess
    else:
        modname = guess_module_from_arm(args.arm)
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            raise SystemExit(f"[ERR] import failed: {modname}: {e}")
        chosen_name, callables = pick_entry_function(mod)
        if not chosen_name:
            raise SystemExit(f"AttributeError: no suitable callable in module, callables={callables}")

    if args.print_entry:
        print(modname)
        print(chosen_name)
        return 0

    # 2) now require in/out
    if not args.inp or not args.outp:
        ap.error("--in and --out are required unless --print-entry is used")

    inp = Path(args.inp)
    outp = Path(args.outp)
    if args.mkdir:
        ensure_parent_dir(outp)

    drop_dir = Path(args.drop_dir) if args.drop_dir.strip() else None
    meta_path = Path(str(outp) + ".meta.json")

    fn = getattr(mod, chosen_name)

    ok = False
    try:
        ret = fn(str(inp), str(outp))
        ok = bool(ret)
    except TypeError:
        try:
            fn(str(inp), str(outp))
        except Exception as e:
            raise SystemExit(f"[ERR] mutator call failed: {modname}.{chosen_name}: {e}")
        ok = outp.exists() and outp.stat().st_size > 0
    except Exception as e:
        raise SystemExit(f"[ERR] mutator call failed: {modname}.{chosen_name}: {e}")

    if not ok or (not outp.exists()) or outp.stat().st_size == 0:
        drop_output(outp, meta_path, drop_dir, f"未找到可变异位置/未生成变异文件 (fn={chosen_name})")
        return 0

    include_dir = ROOT / "src" / "csmithedge" / "csmith" / "runtime"
    if not require_error_ok(args.require_error, outp, include_dir):
        drop_output(outp, meta_path, drop_dir, f"无 error（gcc/clang 语法检查未失败） (fn={chosen_name})")
        return 0

    meta = {
        "arm": args.arm,
        "module": modname,
        "fn": chosen_name,
        "parent": str(inp),
        "out": str(outp),
        "ts": int(time.time()),
        "require_error": args.require_error,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[OK] wrote {outp} (fn={chosen_name})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
