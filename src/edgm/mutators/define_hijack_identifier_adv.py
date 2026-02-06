# -*- coding: utf-8 -*-
"""
ADV+++: Macro hijack an existing global variable name via #define after its definition,
but LOCALIZED to a single statement (or a single control-condition line) to avoid
"hundreds of errors" explosions.

Goal:
- Introduce a controlled compile-time error and maximize chance of diagnostic divergence
  across compilers by targeting "write-like" sites first:
    ++g / g++ / --g / g-- / g = ... / g += ... / g |= ...
  fallback:
    &g

Strategy:
1) Find file-scope `static` global variable definitions (not prototypes; no '(' before ';').
2) Score globals by how often they are used in functions.
3) For the picked global, scan function bodies for "sensitive uses" and pick ONE statement.
4) Insert:
     #define g <payload>
   just before that statement, and:
     #undef g
   right after the statement (or after the condition line).
   This keeps blast radius small.

This is heuristic and Csmith-like friendly.
"""

from __future__ import annotations

import re
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict


C_KEYWORDS = {
    "auto","break","case","char","const","continue","default","do","double","else","enum",
    "extern","float","for","goto","if","inline","int","long","register","restrict","return",
    "short","signed","sizeof","static","struct","switch","typedef","union","unsigned","void",
    "volatile","while","_Alignas","_Alignof","_Atomic","_Bool","_Complex","_Generic",
    "_Imaginary","_Noreturn","_Static_assert","_Thread_local",
}

TYPEDEFS_LIKE = {
    "int8_t","uint8_t","int16_t","uint16_t","int32_t","uint32_t","int64_t","uint64_t",
    "size_t","intptr_t","uintptr_t","ptrdiff_t","ssize_t",
}
_RE_STDINT_T = re.compile(r"^(u?int(8|16|32|64)_t)$")


@dataclass
class GlobalVar:
    name: str
    line_idx: int
    raw_type: str  # best-effort extracted type string


@dataclass
class UseSite:
    name: str
    kind: str            # "inc" | "assign" | "addr"
    line_idx: int        # where the token appears
    stmt_start: int      # statement start line (heuristic)
    stmt_end: int        # statement end line (heuristic)
    score: int           # higher is better


# ---------------- IO ----------------

def _read_text(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def _write_text(p: str, s: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(s, encoding="utf-8")


# ---------------- parsing helpers ----------------

def _is_probably_global_var_def(line: str) -> Optional[Tuple[str, str]]:
    """
    Heuristic for Csmith-like globals:
        static <type...> <name> ... ;
    Must NOT look like a function prototype/decl:
        - reject if '(' appears before ';'
    Return (name, raw_type) if ok.
    """
    s = line.strip()
    if not s.startswith("static "):
        return None
    if ";" not in s:
        return None

    semi = s.find(";")
    head = s[:semi]
    if "(" in head:
        return None

    # Find name: last identifier before [ or = or end-of-head
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b\s*(?:\[|=|$)", head)
    if not m:
        return None
    name = m.group(1)

    if name in C_KEYWORDS:
        return None
    if name.startswith("edgm_"):
        return None
    if name in TYPEDEFS_LIKE or _RE_STDINT_T.match(name) or name.endswith("_t"):
        # avoid accidentally picking a typedef token
        return None

    # crude raw type extraction: remove leading "static", keep before name
    # e.g. "static int32_t * volatile g_720 = &g_721" -> "int32_t * volatile"
    before_name = head[:m.start(1)].strip()
    if before_name.startswith("static "):
        before_name = before_name[len("static "):].strip()

    raw_type = before_name.strip()
    if not raw_type:
        raw_type = "int"

    return (name, raw_type)


def _first_function_def_line(lines: List[str]) -> Optional[int]:
    """
    Find likely start of the first function definition.
    Csmith patterns: "static <ret> func_X(" or "int main(".
    """
    for i, ln in enumerate(lines):
        if re.match(r"^\s*(static\s+)?[A-Za-z_].*\b(func_\d+|main)\s*\(", ln):
            return i
    return None


def _count_token_uses(lines: List[str], name: str, start: int, end: int) -> int:
    pat = re.compile(rf"\b{re.escape(name)}\b")
    cnt = 0
    for ln in lines[start:end]:
        if ln.lstrip().startswith("#"):
            continue
        cnt += len(pat.findall(ln))
    return cnt


def _pick_global_target(lines: List[str], rng: random.Random) -> Optional[GlobalVar]:
    func_start = _first_function_def_line(lines)
    scan_end = func_start if func_start is not None else len(lines)

    globals_found: List[GlobalVar] = []
    for i in range(scan_end):
        res = _is_probably_global_var_def(lines[i])
        if res:
            nm, ty = res
            globals_found.append(GlobalVar(nm, i, ty))

    if not globals_found or func_start is None:
        return None

    scored: List[Tuple[int, GlobalVar]] = []
    for gv in globals_found:
        uses = _count_token_uses(lines, gv.name, func_start, len(lines))
        scored.append((uses, gv))

    scored.sort(key=lambda x: x[0], reverse=True)
    # pick among topk, but must have at least a few uses
    top = [(u, g) for (u, g) in scored if u > 0]
    if not top:
        return None

    topk = [g for (_, g) in top[: max(3, min(10, len(top)))]]
    return rng.choice(topk)


# ---------------- statement boundary heuristics ----------------

def _scan_stmt_end(lines: List[str], start_line: int, max_lookahead: int = 40) -> int:
    """
    Heuristic: find the end line of the statement or condition starting at start_line.
    Track parentheses depth; choose first ';' when depth==0.
    Special-case for control lines like "if (...)" or "for (...)" without ';' at depth==0:
    if we close parens to depth==0 and line contains ')' and not ';' at depth==0, we can
    treat that line as end (so we don't cover the entire body).
    """
    depth = 0
    end_line = start_line
    for j in range(start_line, min(len(lines), start_line + max_lookahead)):
        ln = lines[j]
        if ln.lstrip().startswith("#"):
            # don't cross preprocessor lines; stop early
            break

        # scan chars, update depth and detect ';' at depth==0
        for ch in ln:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0:
                return j

        # if this looks like a control header line and we've closed parens
        stripped = ln.strip()
        if depth == 0 and ")" in ln:
            # if line begins with control keyword (common in csmith)
            if re.match(r"^\s*(if|for|while|switch)\b", ln) and ";" not in stripped:
                return j

        end_line = j
    return end_line


# ---------------- use-site detection ----------------

def _classify_line_use(line: str, name: str) -> Optional[str]:
    """
    Return kind if line contains a "sensitive" use of name.
    Priority: inc > assign > addr.
    """
    if line.lstrip().startswith("#"):
        return None

    # quick filter
    if not re.search(rf"\b{re.escape(name)}\b", line):
        return None

    # increment/decrement
    # ++g, --g, g++, g--
    if re.search(rf"(\+\+|--)\s*\b{re.escape(name)}\b", line) or re.search(rf"\b{re.escape(name)}\b\s*(\+\+|--)", line):
        return "inc"

    # assignment-like (exclude ==, !=, <=, >=)
    # g =, g +=, g -=, g |=, g &=, g ^=, g <<=, g >>=
    if re.search(rf"\b{re.escape(name)}\b\s*(?:<<=|>>=|[+\-*/%&|^]=|=)(?!=)", line):
        return "assign"

    # address-of
    if re.search(rf"&\s*\b{re.escape(name)}\b", line):
        return "addr"

    return None


def _collect_use_sites(lines: List[str], gv: GlobalVar, func_start: int) -> List[UseSite]:
    sites: List[UseSite] = []
    name = gv.name
    # kind weights: push "write-like" much higher than addr
    kind_weight = {"inc": 120, "assign": 100, "addr": 40}

    for i in range(func_start, len(lines)):
        ln = lines[i]
        kind = _classify_line_use(ln, name)
        if not kind:
            continue

        stmt_start = i
        stmt_end = _scan_stmt_end(lines, stmt_start)

        # extra scoring: prefer single-line statements (tighter localization)
        tight_bonus = 20 if stmt_end == stmt_start else 0

        # prefer later (inside deeper logic) a bit? optional: small bonus for non-early
        pos_bonus = min(20, (i - func_start) // 200)

        score = kind_weight[kind] + tight_bonus + pos_bonus
        sites.append(UseSite(name=name, kind=kind, line_idx=i, stmt_start=stmt_start, stmt_end=stmt_end, score=score))

    return sites


def _pick_best_site(sites: List[UseSite], rng: random.Random) -> Optional[UseSite]:
    if not sites:
        return None

    # Hard preference: inc/assign first. Only if none, allow addr.
    strong = [s for s in sites if s.kind in ("inc", "assign")]
    if strong:
        sites = strong

    sites.sort(key=lambda s: s.score, reverse=True)
    # choose among top few to keep variability
    topn = sites[: max(3, min(12, len(sites)))]
    return rng.choice(topn)


# ---------------- payload selection ----------------

def _strip_type_qualifiers(raw_type: str) -> str:
    # remove some qualifiers that can break casts in macro contexts
    t = raw_type
    # normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()
    # remove common qualifiers
    t = re.sub(r"\b(const|volatile|restrict|_Atomic)\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return "int"
    return t


def _choose_payload(gv: GlobalVar, rng: random.Random) -> Tuple[str, str]:
    """
    Return (cast_type, payload) where payload is expression-like and non-lvalue.
    For pointers we deliberately use void* variants to provoke different primary diags.
    """
    cast_type = _strip_type_qualifiers(gv.raw_type)
    is_ptr = "*" in cast_type

    if is_ptr:
        # Use void* payloads (sometimes with invalid arithmetic) to diversify diagnostics.
        payloads = [
            "((void*)0)",                      # rvalue => lvalue errors for ++/&/=
            "((void)0, (void*)0)",             # comma expr
            "((void*)0 + 0)",                  # may trigger "invalid operands to binary +" first
            "((char*)0 + 0)",                  # pointer arithmetic ok, but still rvalue
        ]
        payload = rng.choice(payloads)
        return ("void*", payload)

    # scalar
    # Keep a cast close to original type, but still rvalue / not modifiable
    payloads = [
        f"(({cast_type})0)",
        f"((void)0, ({cast_type})0)",
        f"(({cast_type})0 + 0)",
        f"(({cast_type})0 ^ 0)",
    ]
    payload = rng.choice(payloads)
    return (cast_type, payload)


# ---------------- main mutation ----------------

def insert_define_hijack_identifier_adv(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    func_start = _first_function_def_line(lines)
    if func_start is None:
        _write_text(out_path, text)
        print("[debug] C-macro-adv+++: no function region found; skip")
        return False

    gv = _pick_global_target(lines, rng)
    if not gv:
        _write_text(out_path, text)
        print("[debug] C-macro-adv+++: no suitable global var found; skip")
        return False

    sites = _collect_use_sites(lines, gv, func_start)
    picked = _pick_best_site(sites, rng)
    if not picked:
        _write_text(out_path, text)
        print(f"[debug] C-macro-adv+++: no sensitive use site for '{gv.name}'; skip")
        return False

    tag = rng.randint(10000, 99999)
    cast_type, payload = _choose_payload(gv, rng)

    # Insert define before stmt_start, and undef after stmt_end.
    define_lines = []
    define_lines.append(f"/* edgm C-macro-adv+++: single-statement hijack (tag={tag}) */\n")
    define_lines.append(f"#ifndef EDGM_CMACRO_DONE_{tag}\n")
    define_lines.append(f"#define EDGM_CMACRO_DONE_{tag} 1\n")
    define_lines.append(f"/* hijack `{gv.name}` kind={picked.kind}; prefer ++/= to diversify diagnostics */\n")
    define_lines.append(f"#define {gv.name} {payload}\n")
    define_lines.append(f"#endif\n")

    undef_lines = []
    undef_lines.append(f"/* edgm C-macro-adv+++: end hijack (tag={tag}) */\n")
    undef_lines.append(f"#undef {gv.name}\n")

    new_lines: List[str] = []
    for i, ln in enumerate(lines):
        # insert define right before statement start
        if i == picked.stmt_start:
            new_lines.extend(define_lines)
        new_lines.append(ln)
        # insert undef right after statement end line
        if i == picked.stmt_end:
            new_lines.extend(undef_lines)

    _write_text(out_path, "".join(new_lines))

    print(f"[debug] C-macro-adv+++: seed={seed}")
    print(f"[debug] C-macro-adv+++: picked global '{gv.name}' at line {gv.line_idx+1}")
    print(f"[debug] C-macro-adv+++: statement [{picked.stmt_start+1}..{picked.stmt_end+1}] kind={picked.kind} score={picked.score}")
    print(f"[debug] C-macro-adv+++: cast_type='{cast_type}', payload={payload}")
    return True
