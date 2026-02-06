# -*- coding: utf-8 -*-
"""
C-redeclare-conflict-adv+++ (C3)

Goals:
- Produce *targeted* hard errors (conflicting types / redefinition / linkage conflict),
  but avoid "poisoning" typedef names like uint16_t that cause massive cascades.
- Support:
  1) local variable conflict
  2) global variable conflict (+ storage class games: static/extern)
  3) function prototype conflict (signature mismatch)

Implementation:
- Text-based heuristics (brace depth + regex).
- Strong name filtering to avoid typedef/keyword/macro-style names.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------- I/O ----------------

def _read_text(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def _write_text(p: str, s: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(s, encoding="utf-8")


# ---------------- Regex / Heuristics ----------------

_IDENT = r"[A-Za-z_]\w*"

_PP = re.compile(r"^\s*#")
_TAG_DECL = re.compile(r"^\s*(struct|union|enum)\s+[_A-Za-z]\w*\s*\{")
_TYPEDEF_LINE = re.compile(r"^\s*typedef\b")

# Function definition header (loose): "ret name(args) {"
_FUNC_DEF_HINT = re.compile(
    rf"^\s*(?:static\s+)?(?:inline\s+)?[\w\s\*\(\)]+?\s+({_IDENT})\s*\([^;]*\)\s*\{{\s*$"
)

# Function prototype at global scope: "ret name(args);"
# Excludes "{" and tries to be stable on Csmith formatting.
_FUNC_PROTO = re.compile(
    rf"^\s*(?:extern\s+|static\s+)?[\w\s\*\(\)]+?\s+({_IDENT})\s*\(([^;{{]]*)\)\s*;\s*$"
)

# Variable declaration (supports pointers and arrays) but not function pointers (we skip '(' lines).
# Examples it should match:
#   static uint16_t *g_323 = &g_179;
#   int32_t g_x;
#   volatile uint32_t g_y[3] = {0};
_VAR_DECL = re.compile(
    rf"""
    ^\s*
    (?P<sc>static|extern)?\s*
    (?P<qual>(?:(?:const|volatile)\s+)*)?
    (?P<type>(?:struct|union|enum)\s+)?(?P<base>[\w_]+(?:\s+[\w_]+)*)\s+
    (?P<ptr>(?:\*+\s*(?:const\s+)*)*)           # *, **, * const, etc.
    (?P<name>{_IDENT})                          # identifier
    (?P<after>\s*(?:\[[^\]]*\]\s*)*(?:[^;]*)?)  # optional arrays + initializer
    ;
    \s*$
    """,
    re.VERBOSE,
)

# Ban names that are almost certainly typedefs / keywords / builtins.
_C_KEYWORDS = {
    "auto","break","case","char","const","continue","default","do","double","else","enum","extern",
    "float","for","goto","if","inline","int","long","register","restrict","return","short","signed",
    "sizeof","static","struct","switch","typedef","union","unsigned","void","volatile","while",
    "_Alignas","_Alignof","_Atomic","_Bool","_Complex","_Generic","_Imaginary","_Noreturn","_Static_assert","_Thread_local"
}
# Common typedef-y names that show up via stdint/stdlib headers or Csmith runtime.
_TYPEDEF_LIKE = {
    "size_t","ptrdiff_t","intptr_t","uintptr_t",
    "int8_t","int16_t","int32_t","int64_t",
    "uint8_t","uint16_t","uint32_t","uint64_t",
    "int_least8_t","int_least16_t","int_least32_t","int_least64_t",
    "uint_least8_t","uint_least16_t","uint_least32_t","uint_least64_t",
    "int_fast8_t","int_fast16_t","int_fast32_t","int_fast64_t",
    "uint_fast8_t","uint_fast16_t","uint_fast32_t","uint_fast64_t",
    "intmax_t","uintmax_t",
}

def _is_bad_name(name: str) -> bool:
    if name in _C_KEYWORDS:
        return True
    if name in _TYPEDEF_LIKE:
        return True
    # Heuristic: many typedefs end with _t; redeclaring them nukes parsing.
    if name.endswith("_t"):
        return True
    # Heuristic: avoid "__" internals
    if name.startswith("__"):
        return True
    return False


@dataclass
class Cand:
    kind: str  # "local_var" | "global_var" | "func_proto"
    line: int
    name: str
    func: Optional[str]
    depth: int
    score: int


def _brace_depth_scan(lines: List[str]) -> Tuple[List[int], List[Optional[str]]]:
    depth = 0
    func = None
    depth_at: List[int] = []
    func_at: List[Optional[str]] = []

    for raw in lines:
        depth_at.append(depth)
        func_at.append(func)

        if depth == 0:
            m = _FUNC_DEF_HINT.match(raw)
            if m:
                func = m.group(1)

        opens = raw.count("{")
        closes = raw.count("}")
        depth += opens - closes
        if depth <= 0:
            depth = 0
            func = None

    return depth_at, func_at


def _pick_candidate(lines: List[str], seed: Optional[int]) -> Optional[Cand]:
    rng = random.Random(seed)
    depth_at, func_at = _brace_depth_scan(lines)

    cands: List[Cand] = []

    for i, raw in enumerate(lines):
        if _PP.match(raw) or _TAG_DECL.match(raw) or _TYPEDEF_LINE.match(raw):
            continue

        depth = depth_at[i]
        func = func_at[i]

        # Prefer prototype conflict at global scope (high-value and clean diagnostics).
        if depth == 0 and "{" not in raw:
            mfp = _FUNC_PROTO.match(raw)
            if mfp:
                name = mfp.group(1)
                if _is_bad_name(name):
                    continue
                score = 260
                if name.startswith("func_"):
                    score += 40
                cands.append(Cand("func_proto", i, name, None, depth, score))
                continue

        # Avoid function pointers / calls: skip if '(' appears at all.
        if "(" in raw:
            continue

        mv = _VAR_DECL.match(raw)
        if not mv:
            continue

        name = mv.group("name")
        if _is_bad_name(name):
            continue

        score = 140
        # prefer "g_*" globals/locals common in Csmith; very stable identifiers
        if name.startswith("g_"):
            score += 25
        if name.startswith("l_"):
            score += 10

        if depth == 0 and func is None:
            # global var: allow storage class games but avoid typedef poisoning (already filtered)
            score += 70
            # non-static globals are great for linkage conflicts
            if mv.group("sc") is None:
                score += 10
            cands.append(Cand("global_var", i, name, None, depth, score))
        else:
            score += 20
            if func:
                score += 10
            cands.append(Cand("local_var", i, name, func, depth, score))

    if not cands:
        return None

    cands.sort(key=lambda c: c.score, reverse=True)
    topk = cands[: max(3, min(14, len(cands)))]
    return rng.choice(topk)


# ---------------- Mutation builders ----------------

def _choose_conflicting_type(rng: random.Random) -> str:
    # Types that are "valid looking" but likely conflict with original.
    return rng.choice(["double", "long double", "uint64_t", "int64_t", "void *", "struct S0"])


def _choose_storage_class(rng: random.Random) -> str:
    return rng.choice(["static", "extern"])


def _make_conflicting_var_decl(name: str, rng: random.Random, force_sc: Optional[str], is_block_scope: bool) -> str:
    bad_type = _choose_conflicting_type(rng)
    sc = force_sc if force_sc else _choose_storage_class(rng)

    # extern + initializer in block scope is illegal; keep it syntactically “clean”.
    init = ""
    if sc != "extern":
        if bad_type == "void *":
            init = " = (void*)0"
        elif bad_type.startswith("struct"):
            init = " = (struct S0){0}"
        else:
            init = " = 0"
    if is_block_scope and sc == "extern":
        init = ""

    return f"{sc} {bad_type} {name}{init};"


def _split_proto(line: str) -> Tuple[str, str]:
    m = _FUNC_PROTO.match(line)
    if not m:
        return ("", "")
    return (m.group(1), m.group(2))


def _make_conflicting_proto(name: str, args: str, rng: random.Random) -> str:
    sc = rng.choice(["", "static ", "extern "])
    ret = rng.choice(["int", "uint64_t", "double", "void *"])

    if args.strip() in ("", "void"):
        new_args = rng.choice(["float a", "int a", "uint64_t a"])
    else:
        parts = [p.strip() for p in args.split(",")]
        out = []
        for p in parts:
            toks = p.split()
            pname = toks[-1] if toks else "a"
            if pname == "void":
                pname = "a"
            ptype = rng.choice(["float", "double", "int", "uint64_t"])
            out.append(f"{ptype} {pname}")
        new_args = ", ".join(out)

    return f"{sc}{ret} {name}({new_args});"


# ---------------- Public API ----------------

def mutate(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    cand = _pick_candidate(lines, seed)
    if not cand:
        _write_text(out_path, text)
        print("[debug] C-redeclare-conflict-adv+++: no candidate; skip")
        return False

    tag = rng.randint(10000, 99999)
    raw = lines[cand.line]
    indent = re.match(r"^\s*", raw).group(0)

    if cand.kind in ("local_var", "global_var"):
        # storage-class conflict policy
        force_sc = None
        if cand.kind == "global_var":
            # Flip/linkage-mess: prefer introducing static if original not static,
            # otherwise try extern.
            if "static" in raw:
                force_sc = "extern"
            elif "extern" in raw:
                force_sc = "static"
            else:
                force_sc = "static"

        is_block_scope = (cand.kind == "local_var")

        new_decl = _make_conflicting_var_decl(
            cand.name, rng, force_sc=force_sc, is_block_scope=is_block_scope
        )

        note = (
            f"{indent}/* edgm C-redeclare-conflict-adv+++: "
            f"{cand.kind} redeclare `{cand.name}` with conflicting type/storage "
            f"(func={cand.func}, line={cand.line+1}, tag={tag}) */\n"
        )
        ins = note + f"{indent}{new_decl}\n"
        lines.insert(cand.line + 1, ins)

        _write_text(out_path, "".join(lines))
        print(f"[debug] C-redeclare-conflict-adv+++: seed={seed}")
        print(f"[debug] C-redeclare-conflict-adv+++: picked kind={cand.kind} func={cand.func} name={cand.name} line={cand.line+1} tag={tag}")
        return True

    if cand.kind == "func_proto":
        name, args = _split_proto(raw)
        if not name or _is_bad_name(name):
            _write_text(out_path, text)
            print("[debug] C-redeclare-conflict-adv+++: proto parse failed/bad name; skip")
            return False

        bad_proto = _make_conflicting_proto(name, args, rng)
        note = (
            f"{indent}/* edgm C-redeclare-conflict-adv+++: "
            f"insert conflicting function prototype for `{name}` "
            f"(line={cand.line+1}, tag={tag}) */\n"
        )
        ins = note + f"{indent}{bad_proto}\n"
        lines.insert(cand.line + 1, ins)

        _write_text(out_path, "".join(lines))
        print(f"[debug] C-redeclare-conflict-adv+++: seed={seed}")
        print(f"[debug] C-redeclare-conflict-adv+++: picked kind=func_proto name={name} line={cand.line+1} tag={tag}")
        return True

    _write_text(out_path, text)
    return False
