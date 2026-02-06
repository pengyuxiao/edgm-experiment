#!/usr/bin/env python3
import argparse
import re
from pathlib import Path
from typing import List, Tuple

# ----------------------------
# 0) extract applied arms header (from raw src, even in comments)
# ----------------------------
_APPLIED_RE = re.compile(r"EDGM_APPLIED_ARMS:\s*(.*)")

def extract_applied_arms(raw_src: str) -> str:
    """
    Extract 'EDGM_APPLIED_ARMS: ...' from raw source text.
    Return CSV string or "" if not found.
    """
    for line in raw_src.splitlines():
        m = _APPLIED_RE.search(line)
        if m:
            return m.group(1).strip()
    return ""

# ----------------------------
# 1) comment stripper (C/C++)
# ----------------------------
def strip_c_comments(src: str) -> str:
    OUT = []
    i, n = 0, len(src)
    in_sl_comment = False
    in_ml_comment = False
    in_str = False
    in_char = False
    esc = False
    while i < n:
        c = src[i]
        nxt = src[i+1] if i+1 < n else ""

        if in_sl_comment:
            if c == "\n":
                in_sl_comment = False
                OUT.append(c)
            i += 1
            continue

        if in_ml_comment:
            if c == "*" and nxt == "/":
                in_ml_comment = False
                i += 2
            else:
                i += 1
            continue

        if in_str:
            OUT.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue

        if in_char:
            OUT.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == "'":
                in_char = False
            i += 1
            continue

        # not in any string/comment
        if c == "/" and nxt == "/":
            in_sl_comment = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_ml_comment = True
            i += 2
            continue
        if c == '"':
            in_str = True
            OUT.append(c)
            i += 1
            continue
        if c == "'":
            in_char = True
            OUT.append(c)
            i += 1
            continue

        OUT.append(c)
        i += 1

    return "".join(OUT)

# ----------------------------
# 2) approx token counter
# ----------------------------
_tok_re = re.compile(
    r"""
    (\"([^\"\\]|\\.)*\")           # string literal
  | (\'([^\'\\]|\\.)*\')           # char literal
  | ([A-Za-z_][A-Za-z0-9_]*)        # identifier
  | ([0-9]+(\.[0-9]+)?)             # number
  | (\S)                            # any non-space char as token
    """,
    re.VERBOSE | re.DOTALL,
)

def approx_tokens(s: str) -> int:
    return sum(1 for _ in _tok_re.finditer(s))

# ----------------------------
# 3) function boundary heuristic
# ----------------------------
_ctrl_kw = ("if", "for", "while", "switch", "return", "do")
def looks_like_func_start(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    for kw in _ctrl_kw:
        if s.startswith(kw + " ") or s.startswith(kw + "("):
            return False
    if "(" in s and ")" in s and "{" in s:
        if s.startswith("struct ") or s.startswith("union ") or s.startswith("enum "):
            return False
        return True
    return False

def split_functions(lines: List[str]) -> Tuple[List[int], List[Tuple[int,int]]]:
    # keep edgm marker lines, and also keep our injected macro marker if present
    edgm_idxs = [i for i,l in enumerate(lines) if ("edgm" in l or "EDGM_APPLIED_ARMS" in l)]
    funcs = []
    depth = 0
    i = 0
    n = len(lines)
    while i < n:
        l = lines[i]
        if depth == 0 and looks_like_func_start(l):
            start = i
            d = 0
            j = i
            while j < n:
                d += lines[j].count("{")
                d -= lines[j].count("}")
                if d == 0 and j > i:
                    end = j
                    funcs.append((start, end))
                    i = j + 1
                    break
                j += 1
            else:
                i += 1
            continue

        depth += l.count("{")
        depth -= l.count("}")
        i += 1

    return edgm_idxs, funcs

def sample_function(lines: List[str], start: int, end: int,
                    head_n: int, mid_n: int, tail_n: int) -> List[str]:
    out = []
    brace_line = start
    for k in range(start, min(end+1, start+8)):
        out.append(lines[k])
        if "{" in lines[k]:
            brace_line = k
            break

    body_start = brace_line + 1
    body_end = end - 1
    if body_start > body_end:
        out.append(lines[end])
        return out

    body = lines[body_start:body_end+1]
    out.extend(body[:head_n])

    if mid_n > 0 and len(body) > (head_n + tail_n + mid_n):
        step = max(1, len(body) // (mid_n + 1))
        mid_idxs = [min(len(body)-1, (t+1)*step) for t in range(mid_n)]
        for mi in mid_idxs:
            out.append(body[mi])

    if tail_n > 0:
        out.extend(body[-tail_n:])

    out.append(lines[end])
    return out

def build_program_view(raw_src: str, max_tokens: int) -> str:
    # 0) extract applied arms from raw source before stripping comments
    arms_csv = extract_applied_arms(raw_src)

    # 1) strip comments
    stripped = strip_c_comments(raw_src)
    lines = stripped.splitlines(True)

    includes = [l for l in lines if l.lstrip().startswith("#include")]

    edgm_idxs, funcs = split_functions(lines)

    first_func = funcs[0][0] if funcs else len(lines)
    global_part = []
    for i in range(min(first_func, 250)):
        l = lines[i]
        if l.lstrip().startswith("#include"):
            continue
        if l.strip() == "":
            continue
        global_part.append(l)

    head_n, mid_n, tail_n = 8, 6, 8

    def assemble(head_n_: int, mid_n_: int, tail_n_: int) -> str:
        out_lines = []
        out_lines.append("/* === EDGM_PROGRAM_VIEW === */\n")

        # IMPORTANT: inject applied arms in NON-comment form so it survives stripping
        if arms_csv:
            out_lines.append(f"#define EDGM_APPLIED_ARMS \"{arms_csv}\"\n")
        else:
            out_lines.append("#define EDGM_APPLIED_ARMS \"\"\n")

        out_lines.append("/* === INCLUDES === */\n")
        out_lines.extend(includes[:80])
        out_lines.append("\n/* === GLOBAL_PREFIX === */\n")
        out_lines.extend(global_part[:800])
        out_lines.append("\n/* === FUNCTION_SAMPLES === */\n")

        max_funcs = 40
        for idx,(s,e) in enumerate(funcs[:max_funcs]):
            out_lines.append(f"\n/* --- func#{idx+1} lines {s+1}-{e+1} --- */\n")
            out_lines.extend(sample_function(lines, s, e, head_n_, mid_n_, tail_n_))

        out_lines.append("\n/* === FORCED_LINES === */\n")
        forced = set()
        for i in edgm_idxs:
            for j in (i-1, i, i+1):
                if 0 <= j < len(lines):
                    forced.add(j)
        for j in sorted(forced):
            out_lines.append(lines[j])

        return "".join(out_lines)

    view = assemble(head_n, mid_n, tail_n)
    t = approx_tokens(view)

    while t > max_tokens and (mid_n > 0 or head_n > 2 or tail_n > 2):
        if mid_n > 0:
            mid_n = max(0, mid_n - 2)
        elif head_n > 2:
            head_n = max(2, head_n - 2)
        elif tail_n > 2:
            tail_n = max(2, tail_n - 2)
        view = assemble(head_n, mid_n, tail_n)
        t = approx_tokens(view)

    if t > max_tokens:
        parts = view.splitlines(True)
        keep = []
        cur = 0
        for l in parts:
            lt = approx_tokens(l)
            if cur + lt > max_tokens - 200:
                break
            keep.append(l)
            cur += lt

        keep.append("\n/* === EDGM_FORCED_TAIL === */\n")
        forced_tail = []
        forced = []
        for l in view.splitlines(True):
            if ("edgm" in l or "EDGM_APPLIED_ARMS" in l):
                forced.append(l)

        seen = set()
        for l in forced:
            if l in seen:
                continue
            seen.add(l)
            forced_tail.append(l)
            if approx_tokens("".join(forced_tail)) > 180:
                break
        keep.extend(forced_tail)
        view = "".join(keep)

    return view

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cfile", help="input C file")
    ap.add_argument("--out", default="", help="output file path (default: stdout)")
    ap.add_argument("--max-tokens", type=int, default=7800, help="token budget (approx)")
    args = ap.parse_args()

    cpath = Path(args.cfile)
    raw = cpath.read_text(encoding="utf-8", errors="ignore")
    view = build_program_view(raw, args.max_tokens)

    if args.out:
        Path(args.out).write_text(view, encoding="utf-8")
    else:
        print(view, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
