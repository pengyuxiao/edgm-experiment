# -*- coding: utf-8 -*-
"""
Diversify-only mutation:
- MUST keep code compilable (no errors)
- Inject typedef, enum, switch/case, while, do/while, break, continue
- Avoid inserting inside initializers / for-headers etc by inserting only at safe statement boundaries.

New:
- force_all: deterministically inject ALL supported constructs at least once
  to improve downstream mutator insertion-point availability.
"""

from __future__ import annotations

import os
import re
import time
import random
from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class DiversifyConfig:
    # global injections (prob mode)
    p_global_typedef: float = 0.55
    p_global_enum: float = 0.45

    # per-function injection count (prob mode)
    func_inserts_min: int = 0
    func_inserts_max: int = 6

    # weights for different constructs inside functions (prob mode)
    w_local_typedef: int = 2
    w_local_enum: int = 2
    w_switch: int = 3
    w_while: int = 3
    w_do_while: int = 3

    # max candidate insertion points per function (to limit scanning)
    max_points_per_func: int = 60

    # NEW: force to inject all construct kinds at least once
    force_all: bool = False


_CSMITH_ANCHOR_RE = re.compile(r"^\s*static\s+long\s+__undefined\s*;\s*$", re.M)
_INCLUDE_RE = re.compile(r"^\s*#\s*include\b[^\n]*$", re.M)

# match a single global declaration line that contains g_*
_GLOBAL_DECL_LINE_RE = re.compile(r"^\s*static\b[^\n;]*\bg_[A-Za-z0-9_]+\b[^\n;]*;", re.M)

_BASE_TYPES = [
    "uint8_t", "int8_t",
    "uint16_t", "int16_t",
    "uint32_t", "int32_t",
    "uint64_t", "int64_t",
    "unsigned", "int",
]


def _ensure_outdir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _pick_tag(rng: random.Random) -> int:
    return rng.randrange(10000, 99999)


def _get_line_indent(s: str, pos: int) -> str:
    line_start = s.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", s[line_start:])
    return m.group(0) if m else ""


def _insert_many(s: str, inserts: List[Tuple[int, str]]) -> str:
    for off, text in sorted(inserts, key=lambda x: x[0], reverse=True):
        s = s[:off] + text + s[off:]
    return s


def _find_global_anchor(src: str) -> int:
    m = _CSMITH_ANCHOR_RE.search(src)
    if m:
        return m.end()

    incs = list(_INCLUDE_RE.finditer(src))
    if incs:
        return incs[-1].end()

    return 0


def _collect_global_names(src: str) -> Tuple[List[str], List[str]]:
    int_like: List[str] = []
    ptr_like: List[str] = []

    for m in _GLOBAL_DECL_LINE_RE.finditer(src):
        line = m.group(0)

        mm = re.search(r"\b(g_[A-Za-z0-9_]+)\b", line)
        if not mm:
            continue
        name = mm.group(1)

        name_pos = line.find(name)
        if name_pos < 0:
            continue

        after = line[name_pos + len(name):]
        if re.match(r"\s*\[", after):
            continue  # arrays

        type_part = line[:name_pos]

        if re.search(r"\b(struct|union)\b", type_part):
            continue
        if "(" in type_part or ")" in type_part:
            continue

        if "*" in type_part:
            ptr_like.append(name)
        else:
            int_like.append(name)

    int_like = sorted(set(int_like))
    ptr_like = sorted(set(ptr_like))
    return int_like, ptr_like


def _choose_base_type(src: str, rng: random.Random) -> str:
    present = [t for t in _BASE_TYPES if re.search(r"\b" + re.escape(t) + r"\b", src)]
    if not present:
        present = _BASE_TYPES[:]
    return rng.choice(present)


def _safe_expr(rng: random.Random, int_globals: List[str], ptr_globals: List[str]) -> str:
    choices: List[str] = []

    if int_globals:
        g = rng.choice(int_globals)
        choices.append(f"(unsigned)({g})")

    if ptr_globals:
        g = rng.choice(ptr_globals)
        choices.append(f"(unsigned)((unsigned long)({g}))")

    choices += [
        "(unsigned)__LINE__",
        "(unsigned)(sizeof(int) * 13u)",
        "(unsigned)0x9E3779B9u",
        "(unsigned)0u",
        "(unsigned)1u",
    ]

    e = rng.choice(choices)

    mixers = [
        lambda x: f"(({x}) & 0xFFu)",
        lambda x: f"(({x}) ^ 0xA5u)",
        lambda x: f"((({x}) + 17u) & 0xFFu)",
        lambda x: f"((({x}) << 1) & 0xFFu)",
        lambda x: f"((({x}) >> 1) & 0xFFu)",
    ]
    for _ in range(rng.randrange(1, 4)):
        e = rng.choice(mixers)(e)
    return e


def _gen_global_typedef(tag: int, base: str) -> str:
    return (
        "\n"
        f"/* edgm diversify: global typedef-chain (tag={tag}) */\n"
        f"typedef {base} edgm_td_{tag}_t0;\n"
        f"typedef edgm_td_{tag}_t0 edgm_td_{tag}_t1;\n"
        f"typedef edgm_td_{tag}_t1 edgm_td_{tag}_t2;\n"
    )


def _gen_global_enum(tag: int) -> str:
    return (
        "\n"
        f"/* edgm diversify: global enum (tag={tag}) */\n"
        f"enum edgm_e_{tag} {{\n"
        f"    edgm_e_{tag}_A = 0,\n"
        f"    edgm_e_{tag}_B = 1,\n"
        f"    edgm_e_{tag}_C = 0x7Fu,\n"
        f"}};\n"
        f"typedef enum edgm_e_{tag} edgm_e_{tag}_t;\n"
    )


def _gen_stmt_local_typedef(indent: str, tag: int, base: str) -> str:
    return (
        f"\n{indent}/* edgm diversify: local typedef-chain (tag={tag}) */\n"
        f"{indent}typedef {base} edgm_ltd_{tag}_t0;\n"
        f"{indent}typedef edgm_ltd_{tag}_t0 edgm_ltd_{tag}_t1;\n"
        f"{indent}typedef edgm_ltd_{tag}_t1 edgm_ltd_{tag}_t2;\n"
    )


def _gen_stmt_local_enum(indent: str, tag: int, expr: str) -> str:
    return (
        f"\n{indent}/* edgm diversify: local enum + use (tag={tag}) */\n"
        f"{indent}enum edgm_le_{tag} {{ edgm_le_{tag}_X = 0, edgm_le_{tag}_Y = 1, edgm_le_{tag}_Z = 3 }};\n"
        f"{indent}do {{ volatile unsigned edgm_le_use_{tag} = (({expr}) & 3u) + (unsigned)edgm_le_{tag}_Z; (void)edgm_le_use_{tag}; }} while (0);\n"
    )


def _gen_stmt_switch(indent: str, tag: int, expr: str) -> str:
    return (
        f"\n{indent}/* edgm diversify: switch/case (tag={tag}) */\n"
        f"{indent}do {{\n"
        f"{indent}    unsigned edgm_sw_{tag} = ({expr}) & 3u;\n"
        f"{indent}    switch (edgm_sw_{tag}) {{\n"
        f"{indent}    case 0:\n"
        f"{indent}        break;\n"
        f"{indent}    case 1:\n"
        f"{indent}        /* fallthrough */;\n"
        f"{indent}    default:\n"
        f"{indent}        break;\n"
        f"{indent}    }}\n"
        f"{indent}}} while (0);\n"
    )


def _gen_stmt_while(indent: str, tag: int, expr: str) -> str:
    return (
        f"\n{indent}/* edgm diversify: while + continue/break (tag={tag}) */\n"
        f"{indent}do {{\n"
        f"{indent}    volatile unsigned edgm_w_{tag} = (({expr}) & 7u);\n"
        f"{indent}    while (edgm_w_{tag}--) {{\n"
        f"{indent}        if (edgm_w_{tag} & 1u) continue;\n"
        f"{indent}        break;\n"
        f"{indent}    }}\n"
        f"{indent}}} while (0);\n"
    )


def _gen_stmt_do_while(indent: str, tag: int, expr: str) -> str:
    return (
        f"\n{indent}/* edgm diversify: do/while + nested while (tag={tag}) */\n"
        f"{indent}do {{\n"
        f"{indent}    unsigned edgm_d_{tag} = (({expr}) & 3u) + 1u;\n"
        f"{indent}    while (edgm_d_{tag}--) {{\n"
        f"{indent}        if (edgm_d_{tag} == 1u) continue;\n"
        f"{indent}        break;\n"
        f"{indent}    }}\n"
        f"{indent}}} while (0);\n"
    )


def _find_function_spans(src: str) -> List[Tuple[int, int]]:
    hdr = re.compile(
        r"^[ \t]*(?:static\s+)?[A-Za-z_][\w\s\*\(\)]*\b[A-Za-z_]\w*\s*\([^;{}]*\)\s*\n[ \t]*\{",
        re.M,
    )
    spans = []
    for m in hdr.finditer(src):
        lbrace = m.end() - 1
        rbrace = _match_brace(src, lbrace)
        if rbrace is not None:
            spans.append((lbrace, rbrace))
    return spans


def _match_brace(src: str, lbrace: int) -> Optional[int]:
    assert src[lbrace] == "{"
    i = lbrace + 1
    depth = 1
    paren = 0
    in_str = False
    in_chr = False
    esc = False
    line_c = False
    block_c = False

    while i < len(src):
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""

        if line_c:
            if ch == "\n":
                line_c = False
            i += 1
            continue
        if block_c:
            if ch == "*" and nxt == "/":
                block_c = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if in_chr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_chr = False
            i += 1
            continue

        if ch == "/" and nxt == "/":
            line_c = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_c = True
            i += 2
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "'":
            in_chr = True
            i += 1
            continue

        if ch == "(":
            paren += 1
        elif ch == ")":
            if paren > 0:
                paren -= 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i

        i += 1

    return None


def _find_safe_stmt_points(src: str, lbrace: int, rbrace: int, max_points: int) -> List[int]:
    points: List[int] = []

    j = lbrace + 1
    if j < len(src) and src[j] == "\n":
        points.append(j + 1)
    else:
        while j < rbrace and src[j] in " \t\r\n":
            j += 1
        points.append(j)

    i = lbrace + 1
    depth = 1
    paren = 0
    in_str = in_chr = esc = False
    line_c = block_c = False

    while i < rbrace and len(points) < max_points:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""

        if line_c:
            if ch == "\n":
                line_c = False
            i += 1
            continue
        if block_c:
            if ch == "*" and nxt == "/":
                block_c = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if in_chr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_chr = False
            i += 1
            continue

        if ch == "/" and nxt == "/":
            line_c = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_c = True
            i += 2
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "'":
            in_chr = True
            i += 1
            continue

        if ch == "(":
            paren += 1
        elif ch == ")":
            if paren > 0:
                paren -= 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == ";" and depth == 1 and paren == 0:
            k = i + 1
            if k < rbrace and src[k] == "\n":
                points.append(k + 1)
            else:
                points.append(k)

        i += 1

    pts = sorted(set(p for p in points if lbrace < p < rbrace))
    return pts


def _force_all_inserts(src: str, rng: random.Random, config: DiversifyConfig,
                       int_globals: List[str], ptr_globals: List[str],
                       func_spans: List[Tuple[int, int]]) -> List[Tuple[int, str]]:
    """
    Deterministically ensure every construct kind appears at least once.
    Strategy:
      - Always inject global typedef + global enum at anchor.
      - For function constructs: round-robin pick a function with insertion points,
        then pick a point, inject one kind each.
    """
    inserts: List[Tuple[int, str]] = []

    anchor = _find_global_anchor(src)
    # Ensure stable order at same anchor: enum then typedef or vice versa doesn't matter.
    tag1 = _pick_tag(rng)
    base1 = _choose_base_type(src, rng)
    inserts.append((anchor, _gen_global_typedef(tag1, base1)))

    tag2 = _pick_tag(rng)
    inserts.append((anchor, _gen_global_enum(tag2)))

    # Collect per-function safe points
    func_points: List[Tuple[int, int, List[int]]] = []
    for (lb, rb) in func_spans:
        pts = _find_safe_stmt_points(src, lb, rb, config.max_points_per_func)
        if pts:
            func_points.append((lb, rb, pts))

    # If no function bodies found, global inserts still help; return.
    if not func_points:
        return inserts

    kinds = ["local_typedef", "local_enum", "switch", "while", "do_while"]

    fi = 0
    for kind in kinds:
        lb, rb, pts = func_points[fi % len(func_points)]
        fi += 1
        pos = rng.choice(pts)
        indent = _get_line_indent(src, pos)
        tag = _pick_tag(rng)
        base = _choose_base_type(src, rng)
        expr = _safe_expr(rng, int_globals, ptr_globals)

        if kind == "local_typedef":
            text = _gen_stmt_local_typedef(indent, tag, base)
        elif kind == "local_enum":
            text = _gen_stmt_local_enum(indent, tag, expr)
        elif kind == "switch":
            text = _gen_stmt_switch(indent, tag, expr)
        elif kind == "while":
            text = _gen_stmt_while(indent, tag, expr)
        else:
            text = _gen_stmt_do_while(indent, tag, expr)

        inserts.append((pos, text))

    # Optionally: still add some extra inserts for "more diverse" coverage,
    # but keep it simple & deterministic: add 1 extra per function (if you want, uncomment).
    # for (lb, rb, pts) in func_points:
    #     pos = rng.choice(pts)
    #     indent = _get_line_indent(src, pos)
    #     tag = _pick_tag(rng)
    #     base = _choose_base_type(src, rng)
    #     expr = _safe_expr(rng, int_globals, ptr_globals)
    #     text = _gen_stmt_switch(indent, tag, expr)
    #     inserts.append((pos, text))

    return inserts


def diversify_syntax(in_path: str, out_path: str, seed: Optional[int] = None,
                     config: Optional[DiversifyConfig] = None) -> bool:
    if config is None:
        config = DiversifyConfig()

    with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    if seed is None:
        seed = int(time.time() * 1000) & 0xFFFFFFFF

    rng = random.Random(seed)
    int_globals, ptr_globals = _collect_global_names(src)

    inserts: List[Tuple[int, str]] = []

    func_spans = _find_function_spans(src)

    if config.force_all:
        inserts = _force_all_inserts(src, rng, config, int_globals, ptr_globals, func_spans)
    else:
        # ---- global diversify (prob) ----
        anchor = _find_global_anchor(src)

        if rng.random() < config.p_global_typedef:
            tag = _pick_tag(rng)
            base = _choose_base_type(src, rng)
            inserts.append((anchor, _gen_global_typedef(tag, base)))

        if rng.random() < config.p_global_enum:
            tag = _pick_tag(rng)
            inserts.append((anchor, _gen_global_enum(tag)))

        # ---- per-function diversify (prob) ----
        for (lb, rb) in func_spans:
            n_ins = rng.randint(config.func_inserts_min, config.func_inserts_max)
            if n_ins <= 0:
                continue

            points = _find_safe_stmt_points(src, lb, rb, config.max_points_per_func)
            if not points:
                continue

            choices = (
                ["local_typedef"] * max(0, config.w_local_typedef) +
                ["local_enum"] * max(0, config.w_local_enum) +
                ["switch"] * max(0, config.w_switch) +
                ["while"] * max(0, config.w_while) +
                ["do_while"] * max(0, config.w_do_while)
            )
            if not choices:
                continue

            for _ in range(n_ins):
                pos = rng.choice(points)
                indent = _get_line_indent(src, pos)
                tag = _pick_tag(rng)
                base = _choose_base_type(src, rng)
                expr = _safe_expr(rng, int_globals, ptr_globals)

                kind = rng.choice(choices)
                if kind == "local_typedef":
                    text = _gen_stmt_local_typedef(indent, tag, base)
                elif kind == "local_enum":
                    text = _gen_stmt_local_enum(indent, tag, expr)
                elif kind == "switch":
                    text = _gen_stmt_switch(indent, tag, expr)
                elif kind == "while":
                    text = _gen_stmt_while(indent, tag, expr)
                else:
                    text = _gen_stmt_do_while(indent, tag, expr)

                inserts.append((pos, text))

    mutated = _insert_many(src, inserts)

    _ensure_outdir(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(mutated)

    print(
        f"[debug] diversify: seed={seed}, force_all={config.force_all}, "
        f"global_g_int={len(int_globals)}, global_g_ptr={len(ptr_globals)}, "
        f"funcs={len(func_spans)}, inserts={len(inserts)}"
    )
    return True
