# -*- coding: utf-8 -*-
"""
ADV+++ (Array/Pointer shape confusion):

Modes:
  1) a2p_cap_err:
      T a[N][M]  ->  T *a = (T[1]){0};
      keep only K occurrences of a[i][j] unchanged to trigger a few diagnostics;
      repair the rest a[i][j] -> a[i] to avoid explosion.

  2) a2pa_wrong_dim:
      T a[N][M]  ->  T (*a)[M±1] = (T[1][M±1]){{0}};
      (compiles, but pointer arithmetic dimension is wrong -> very confusing)

  3) p2a_keep_writes:
      T *p (or T **p)  ->  T p[2][2] = {{0}};
      keep later direct writes to p unchanged => assignment to expression with array type / lvalue required.

Usage from script:
  mutate(in_path, out_path, seed=None, keep_k=2, mode="auto")
"""

from __future__ import annotations

import re
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict


# ---------------- I/O ----------------

def _read_text(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def _write_text(p: str, s: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(s, encoding="utf-8")


# ---------------- helpers ----------------

def _strip_comments_line(s: str) -> str:
    s = re.sub(r"//.*$", "", s)
    s = re.sub(r"/\*.*?\*/", "", s)
    return s


def _is_blank_or_pp(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


# ---------------- function discovery ----------------

@dataclass
class FuncRange:
    name: str
    start: int
    header_end: int
    end: int


def _find_functions(lines: List[str]) -> List[FuncRange]:
    funcs: List[FuncRange] = []
    n = len(lines)
    i = 0

    hdr_inline = re.compile(
        r"^\s*(static\s+)?[A-Za-z_][\w\s\*\(\)]*\b(func_\d+|main)\s*\([^;]*\)\s*\{"
    )
    hdr_line = re.compile(
        r"^\s*(static\s+)?[A-Za-z_][\w\s\*\(\)]*\b(func_\d+|main)\s*\([^;]*\)\s*$"
    )

    while i < n:
        ln = _strip_comments_line(lines[i])

        m1 = hdr_inline.match(ln)
        if m1:
            name = m1.group(2)
            start = i
            header_end = i
            brace = 0
            started = False
            j = i
            while j < n:
                cur = _strip_comments_line(lines[j])
                for ch in cur:
                    if ch == "{":
                        brace += 1
                        started = True
                    elif ch == "}":
                        brace -= 1
                if started and brace == 0:
                    funcs.append(FuncRange(name=name, start=start, header_end=header_end, end=j))
                    i = j + 1
                    break
                j += 1
            else:
                i += 1
            continue

        m2 = hdr_line.match(ln)
        if m2:
            name = m2.group(2)
            k = i + 1
            while k < n and _is_blank_or_pp(_strip_comments_line(lines[k])):
                k += 1
            if k < n and _strip_comments_line(lines[k]).lstrip().startswith("{"):
                start = i
                header_end = k
                brace = 0
                started = False
                j = k
                while j < n:
                    cur = _strip_comments_line(lines[j])
                    for ch in cur:
                        if ch == "{":
                            brace += 1
                            started = True
                        elif ch == "}":
                            brace -= 1
                    if started and brace == 0:
                        funcs.append(FuncRange(name=name, start=start, header_end=header_end, end=j))
                        i = j + 1
                        break
                    j += 1
                else:
                    i += 1
                continue

        i += 1

    return funcs


def _build_func_map(lines: List[str]) -> Dict[int, Optional[str]]:
    funcs = _find_functions(lines)
    mp: Dict[int, Optional[str]] = {}
    for i in range(len(lines)):
        mp[i] = None
    for fr in funcs:
        for i in range(fr.start, fr.end + 1):
            mp[i] = fr.name
    return mp


# ---------------- parsing candidates ----------------

@dataclass
class Array2DCand:
    scope: str          # "local"|"global"
    func: Optional[str]
    line: int
    indent: str
    type_str: str
    name: str
    n: int
    m: int
    uses2d: int
    score: int


@dataclass
class PtrCand:
    scope: str          # local only (for now)
    func: str
    line: int
    indent: str
    base_type: str
    stars: str
    name: str
    direct_writes: int
    score: int


_RE_2D_DECL = re.compile(
    r"""^(?P<indent>\s*)
        (?P<type>.+?)                 # non-greedy type
        \s+
        (?P<name>[A-Za-z_]\w*)
        \s*\[\s*(?P<n>\d+)\s*\]
        \s*\[\s*(?P<m>\d+)\s*\]
        \s*(?P<tail>[^;]*);
        \s*$""",
    re.X
)

_RE_PTR_DECL = re.compile(
    r"""^(?P<indent>\s*)
        (?P<type>.+?)\s+
        (?P<stars>\*{1,4})\s*
        (?P<name>[A-Za-z_]\w*)
        \s*(?P<tail>[^;]*);
        \s*$""",
    re.X
)

_RE_DIRECT_WRITE_TMPL = r"(?:\b{nm}\b\s*(?:=|\+=|-=|\*=|/=|%=|<<=|>>=|&=|\|=|\^=)|(?:\+\+|--)\s*\b{nm}\b|\b{nm}\b\s*(?:\+\+|--))"


def _count_2d_uses(lines: List[str], start: int, end: int, name: str) -> int:
    pat = re.compile(rf"\b{re.escape(name)}\b\s*\[[^\]]+\]\s*\[[^\]]+\]")
    c = 0
    for i in range(start, end + 1):
        ln = _strip_comments_line(lines[i])
        if ln.lstrip().startswith("#"):
            continue
        c += len(pat.findall(ln))
    return c


def _count_direct_writes(lines: List[str], start: int, end: int, name: str) -> int:
    pat = re.compile(_RE_DIRECT_WRITE_TMPL.format(nm=re.escape(name)))
    c = 0
    for i in range(start, end + 1):
        ln = _strip_comments_line(lines[i])
        if ln.lstrip().startswith("#"):
            continue
        c += len(pat.findall(ln))
    return c


def _is_probably_decl_line(line: str) -> bool:
    s = _strip_comments_line(line).strip()
    if not s.endswith(";"):
        return False
    if s.startswith("#"):
        return False
    # avoid multi-declarators & weird stuff
    head = s[:-1]
    if "," in head:
        return False
    # avoid prototypes/calls (very rough)
    if "(" in head and not re.search(r"\(\s*\*\s*[A-Za-z_]\w*\s*\)", head) and "=" not in head:
        return False
    return True


def _collect_candidates(lines: List[str]) -> Tuple[List[Array2DCand], List[PtrCand], List[FuncRange]]:
    funcs = _find_functions(lines)
    func_by_line = _build_func_map(lines)

    # helper: get function end line
    func_end: Dict[str, int] = {fr.name: fr.end for fr in funcs}
    func_body_start: Dict[str, int] = {fr.name: fr.header_end + 1 for fr in funcs}

    arrays: List[Array2DCand] = []
    ptrs: List[PtrCand] = []

    nlines = len(lines)
    for i in range(nlines):
        raw = lines[i]
        if not _is_probably_decl_line(raw):
            continue

        s = _strip_comments_line(raw)
        # 2D array candidates
        m2 = _RE_2D_DECL.match(s)
        if m2:
            name = m2.group("name")
            type_str = m2.group("type").strip()
            indent = m2.group("indent")
            n = int(m2.group("n"))
            m = int(m2.group("m"))

            fn = func_by_line[i]
            if fn is None:
                # global scope
                scope = "global"
                start = i + 1
                end = nlines - 1
            else:
                scope = "local"
                start = i + 1
                end = func_end[fn]

            uses2d = _count_2d_uses(lines, start, end, name)
            if uses2d <= 0:
                continue

            # score: prefer more 2D uses, but keep stable (avoid crazy huge)
            score = min(uses2d, 80) * 10
            arrays.append(Array2DCand(
                scope=scope, func=fn, line=i, indent=indent,
                type_str=type_str, name=name, n=n, m=m,
                uses2d=uses2d, score=score
            ))
            continue

        # pointer candidates (for p2a_keep_writes)
        mp = _RE_PTR_DECL.match(s)
        if mp:
            fn = func_by_line[i]
            if fn is None:
                continue  # keep local only for this mode
            # avoid function pointers and pointer-to-array declarators
            head = s[:-1]
            if "(" in head or ")" in head:
                continue
            base_type = mp.group("type").strip()
            stars = mp.group("stars")
            name = mp.group("name")
            indent = mp.group("indent")

            start = i + 1
            end = func_end[fn]
            direct_writes = _count_direct_writes(lines, start, end, name)
            if direct_writes <= 0:
                continue

            # prefer stable: a few writes, not hundreds
            score = min(direct_writes, 10) * 25
            ptrs.append(PtrCand(
                scope="local", func=fn, line=i, indent=indent,
                base_type=base_type, stars=stars, name=name,
                direct_writes=direct_writes, score=score
            ))
            continue

    return arrays, ptrs, funcs


# ---------------- rewrite helpers ----------------

def _rewrite_2d_decl_to_ptr(type_str: str, name: str) -> str:
    # disguise init: T *a = (T[1]){0};
    return f"{type_str} *{name} = ({type_str}[1]){{0}};"


def _rewrite_2d_decl_to_ptr_to_array_wrong_dim(type_str: str, name: str, m: int) -> Tuple[str, int]:
    # choose wrong second dimension (m±1), keep >0
    if m <= 1:
        m2 = 2
    else:
        m2 = m + 1 if m < 32 else (m - 1)
        if m2 <= 0:
            m2 = 2

    # disguise init: T (*a)[m2] = (T[1][m2]){{0}};
    return f"{type_str} (*{name})[{m2}] = ({type_str}[1][{m2}]){{{{0}}}};", m2


def _rewrite_ptr_decl_to_2d_array(base_type: str, name: str) -> str:
    # T p[2][2] = {{0}};
    return f"{base_type} {name}[2][2] = {{{{0}}}};"


def _cap_rewrite_2d_uses(lines: List[str], start: int, end: int, name: str, keep_k: int) -> Tuple[int, int]:
    """
    Keep first keep_k occurrences of a[i][j] unchanged.
    Repair the rest: a[i][j] -> (a[i])  (drop second subscript)
    Returns: (total_seen, kept)
    """
    pat = re.compile(rf"\b{re.escape(name)}\b\s*\[(?P<i>[^\]]+)\]\s*\[(?P<j>[^\]]+)\]")

    seen = 0
    kept = 0

    for li in range(start, end + 1):
        raw = lines[li]
        if raw.lstrip().startswith("#"):
            continue
        s = raw
        out = []
        last = 0
        changed = False

        for m in pat.finditer(s):
            seen += 1
            if kept < keep_k:
                # keep unchanged
                kept += 1
                continue

            # repair: replace "a[i][j]" with "(a[i])"
            i_expr = m.group("i").strip()
            repl = f"({name}[{i_expr}])"

            out.append(s[last:m.start()])
            out.append(repl)
            last = m.end()
            changed = True

        if changed:
            out.append(s[last:])
            lines[li] = "".join(out)

    return seen, kept


# ---------------- main mutate ----------------

def mutate(in_path: str, out_path: str, seed: Optional[int] = None, keep_k: int = 2, mode: str = "auto") -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    arrays, ptrs, funcs = _collect_candidates(lines)
    if not arrays and not ptrs:
        _write_text(out_path, text)
        print("[debug] C-shape-adv+++: no suitable candidate; skip")
        return False

    # choose mode
    mode = (mode or "auto").strip().lower()
    if mode not in ("auto", "a2p_cap_err", "a2pa_wrong_dim", "p2a_keep_writes"):
        mode = "auto"

    if mode == "auto":
        # weights: prefer error-producing, sometimes semantic-confusing
        choices = []
        if arrays:
            choices += ["a2p_cap_err"] * 10
            choices += ["a2pa_wrong_dim"] * 3
        if ptrs:
            choices += ["p2a_keep_writes"] * 7
        mode = rng.choice(choices) if choices else "a2p_cap_err"

    tag = rng.randint(10000, 99999)

    # --- Mode 1/2: array based ---
    if mode in ("a2p_cap_err", "a2pa_wrong_dim"):
        arrays.sort(key=lambda x: x.score, reverse=True)
        topk = arrays[: max(3, min(12, len(arrays)))]
        cand = rng.choice(topk)

        # determine rewrite scope range
        if cand.scope == "global":
            scan_start = cand.line + 1
            scan_end = len(lines) - 1
            func_name = None
        else:
            func_name = cand.func
            fr = next(fr for fr in funcs if fr.name == func_name)
            scan_start = cand.line + 1
            scan_end = fr.end

        old_decl = lines[cand.line]

        if mode == "a2p_cap_err":
            new_decl = cand.indent + _rewrite_2d_decl_to_ptr(cand.type_str, cand.name) + "\n"
            note = (f"{cand.indent}/* edgm C-shape-adv+++: mode=a2p_cap_err 2D-array->T* cap-keep_k={keep_k} "
                    f"scope={cand.scope} func={cand.func} name={cand.name} uses2d={cand.uses2d} tag={tag} */\n")
            lines[cand.line] = note + new_decl

            total_seen, kept = _cap_rewrite_2d_uses(lines, scan_start, scan_end, cand.name, keep_k)
            _write_text(out_path, "".join(lines))

            print(f"[debug] C-shape-adv+++: seed={seed}")
            print(f"[debug] C-shape-adv+++: mode=a2p_cap_err picked scope={cand.scope} func={cand.func} name={cand.name} "
                  f"line={cand.line+1} uses2d={cand.uses2d} keep_k={keep_k} total_seen={total_seen} kept={kept} tag={tag}")
            return True

        else:
            new_decl_core, m2 = _rewrite_2d_decl_to_ptr_to_array_wrong_dim(cand.type_str, cand.name, cand.m)
            new_decl = cand.indent + new_decl_core + "\n"
            note = (f"{cand.indent}/* edgm C-shape-adv+++: mode=a2pa_wrong_dim 2D-array->ptr-to-array WRONG dim "
                    f"M:{cand.m}->{m2} scope={cand.scope} func={cand.func} name={cand.name} uses2d={cand.uses2d} tag={tag} */\n")
            lines[cand.line] = note + new_decl
            _write_text(out_path, "".join(lines))

            print(f"[debug] C-shape-adv+++: seed={seed}")
            print(f"[debug] C-shape-adv+++: mode=a2pa_wrong_dim picked scope={cand.scope} func={cand.func} name={cand.name} "
                  f"line={cand.line+1} N={cand.n} M={cand.m}->{m2} uses2d={cand.uses2d} tag={tag}")
            return True

    # --- Mode 3: pointer -> array keep writes ---
    ptrs.sort(key=lambda x: x.score, reverse=True)
    topk = ptrs[: max(3, min(12, len(ptrs)))]
    candp = rng.choice(topk)

    old_decl = lines[candp.line]
    new_decl = candp.indent + _rewrite_ptr_decl_to_2d_array(candp.base_type, candp.name) + "\n"
    note = (f"{candp.indent}/* edgm C-shape-adv+++: mode=p2a_keep_writes T*->T[2][2] keep later direct writes "
            f"scope=local func={candp.func} name={candp.name} writes={candp.direct_writes} tag={tag} */\n")
    lines[candp.line] = note + new_decl
    _write_text(out_path, "".join(lines))

    print(f"[debug] C-shape-adv+++: seed={seed}")
    print(f"[debug] C-shape-adv+++: mode=p2a_keep_writes picked func={candp.func} name={candp.name} "
          f"line={candp.line+1} writes={candp.direct_writes} tag={tag}")
    return True
