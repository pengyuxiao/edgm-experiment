# -*- coding: utf-8 -*-
"""
I20-C4-adv: Prefer inserting into an existing switch/case; otherwise create a new switch.
Goal: create diagnostic-sensitive control-flow errors (C4-ish):
  - goto into VLA scope  (C99 constraint)
  - continue not in loop (constraint)
Plus nested switch/macro/attribute noise to diversify diagnostics.

This version avoids:
  - picking typedef/type tokens as expressions
  - using out-of-scope locals (e.g., main's 'k' before declaration)
"""

from __future__ import annotations

import re
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


C_KEYWORDS = {
    "auto","break","case","char","const","continue","default","do","double","else","enum",
    "extern","float","for","goto","if","inline","int","long","register","restrict","return",
    "short","signed","sizeof","static","struct","switch","typedef","union","unsigned","void",
    "volatile","while","_Alignas","_Alignof","_Atomic","_Bool","_Complex","_Generic",
    "_Imaginary","_Noreturn","_Static_assert","_Thread_local",
}

@dataclass
class CaseSite:
    line_idx: int
    indent: str


def _read_text(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def _write_text(p: str, s: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(s, encoding="utf-8")


def _next_code_line(lines: List[str], start: int) -> Optional[int]:
    """Next line index >= start that contains real code, skipping blank/comment-only lines."""
    i = start
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith("//"):
            i += 1
            continue
        if s.startswith("/*") and s.endswith("*/"):
            i += 1
            continue
        return i
    return None


def _looks_like_func_sig(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^(if|for|while|switch|do)\b", s):
        return False
    if s.endswith(";"):  # prototype/decl
        return False
    if "(" not in s or ")" not in s:
        return False
    if re.search(r"=\s*\{", s):
        return False
    return True


def _find_function_body_insertion_sites(text: str, rng: random.Random) -> List[Tuple[int, str]]:
    """
    Detect:
      1) '...){' on same line
      2) '...)' on one line, '{' on the next code line (Csmith style)
    Return list of (open_brace_line_idx, indent_inside_body)
    """
    lines = text.splitlines(True)
    sites: List[Tuple[int, str]] = []

    for i, ln in enumerate(lines):
        if not _looks_like_func_sig(ln):
            continue

        # Case A: same-line '{'
        if re.search(r"\)\s*\{", ln):
            brace_line = i
        else:
            # Case B: '{' on next code line
            j = _next_code_line(lines, i + 1)
            if j is None:
                continue
            if not lines[j].lstrip().startswith("{"):
                continue
            brace_line = j

        body_line = _next_code_line(lines, brace_line + 1)
        if body_line is not None:
            indent = re.match(r"^(\s*)", lines[body_line]).group(1)
        else:
            indent = re.match(r"^(\s*)", lines[brace_line]).group(1) + "    "

        sites.append((brace_line, indent))

    return sites


def _find_switch_case_sites(text: str) -> List[CaseSite]:
    """
    Find lines that are pure 'case ...:' or 'default:' (optionally with trailing comment).
    We keep it simple and robust (no AST).
    """
    lines = text.splitlines(True)
    joined = "".join(lines)

    switch_iter = list(re.finditer(r"\bswitch\s*\(", joined))
    sites: List[CaseSite] = []

    # naive brace matching for each switch body, then scan case/default lines within range
    for m in switch_iter:
        i = m.end() - 1
        par = 0
        brace_pos = None

        while i < len(joined):
            ch = joined[i]
            if ch == "(":
                par += 1
            elif ch == ")":
                par = max(0, par - 1)
                if par == 0:
                    j = i + 1
                    while j < len(joined) and joined[j].isspace():
                        j += 1
                    if j < len(joined) and joined[j] == "{":
                        brace_pos = j
                    break
            i += 1
        if brace_pos is None:
            continue

        # find matching closing brace
        k = brace_pos
        depth = 0
        end_pos = None
        while k < len(joined):
            ch = joined[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_pos = k
                    break
            k += 1
        if end_pos is None:
            continue

        # map char positions to line indices
        acc = 0
        start_line = 0
        end_line = 0
        for idx, ln in enumerate(lines):
            nxt = acc + len(ln)
            if acc <= brace_pos < nxt:
                start_line = idx
            if acc <= end_pos < nxt:
                end_line = idx
                break
            acc = nxt

        for li in range(start_line, end_line + 1):
            s = lines[li]
            if re.match(r"^\s*(case\b[^:]*:|default\s*:)\s*(/\*.*\*/\s*)?$", s):
                indent = re.match(r"^(\s*)", s).group(1)
                sites.append(CaseSite(line_idx=li, indent=indent))

    return sites


def _safe_cond_expr(rng: random.Random) -> str:
    """
    Always-valid expression, independent of scope (no identifiers).
    Keep some variety for different front-ends/diagnostic anchors.
    """
    choices = [
        "(((unsigned)__LINE__ * 1103515245u + 12345u) & 3u)",
        "(((unsigned)__LINE__ ^ 0x9E3779B9u) & 3u)",
        "((((unsigned)__LINE__ + 0x7u) ^ (unsigned)(0xA5u)) & 3u)",
        "(((unsigned)(0xDEu) + (unsigned)__LINE__) & 3u)",
    ]
    return rng.choice(choices)


def _safe_vla_n_expr(tag: int, rng: random.Random) -> str:
    """
    VLA length expression: always >= 1, small range.
    """
    choices = [
        f"((unsigned)(((__LINE__ ^ {tag}u) & 7u) + 1u))",
        f"((unsigned)(((__LINE__ + {tag}u) & 7u) + 1u))",
        f"((unsigned)(((__LINE__ * 3u + {tag}u) & 7u) + 1u))",
    ]
    return rng.choice(choices)


def _make_injection(indent: str, tag: int, rng: random.Random) -> str:
    """
    Produce a C4-ish control-flow mix block.
    Randomly include an illegal 'continue' (not in loop) to diversify diagnostics.
    Always include goto->label into VLA scope (core I20).
    """
    cond_outer = _safe_cond_expr(rng)
    cond_inner = _safe_cond_expr(rng)
    vla_n = _safe_vla_n_expr(tag, rng)

    # probabilities
    use_illegal_continue = (rng.random() < 0.55)   # ~55% programs also get "continue not in loop"
    add_macro_noise = (rng.random() < 0.70)        # ~70% add macro wrapper
    add_align_attr = (rng.random() < 0.60)         # ~60% add aligned attribute (gcc/clang)

    out: List[str] = []
    out.append(f"{indent}/* edgm I20-C4-adv: ctrlflow mix (goto/VLA + optional continue), tag={tag} */\n")

    if add_macro_noise:
        out.append(f"{indent}#define EDGM_I20C4_CASE_{tag}(X) X\n")

    # Optional illegal continue (still syntactically valid; constraint violation)
    if use_illegal_continue:
        out.append(f"{indent}/* C4: illegal continue (not in a loop) */\n")
        if add_macro_noise:
            out.append(f"{indent}EDGM_I20C4_CASE_{tag}(continue);\n")
        else:
            out.append(f"{indent}continue;\n")

    # Nested switch to complicate control-flow / diagnostics
    out.append(f"{indent}switch ({cond_outer}) {{\n")
    out.append(f"{indent}case 0:\n")
    out.append(f"{indent}    /* C4/I20: goto into VLA scope (diagnostic-sensitive) */\n")
    out.append(f"{indent}    goto edgm_i20_L_{tag};\n")
    out.append(f"{indent}case 1:\n")
    out.append(f"{indent}    /* nested switch (noise) */\n")
    out.append(f"{indent}    switch ({cond_inner}) {{\n")
    out.append(f"{indent}    case 0:\n")
    out.append(f"{indent}        goto edgm_i20_L_{tag};\n")
    out.append(f"{indent}    default:\n")
    out.append(f"{indent}        break;\n")
    out.append(f"{indent}    }}\n")
    out.append(f"{indent}    break;\n")
    out.append(f"{indent}default:\n")
    out.append(f"{indent}    break;\n")
    out.append(f"{indent}}}\n")

    # VLA scope block: label after VLA decl => goto enters VLA scope (error)
    out.append(f"{indent}{{\n")
    out.append(f"{indent}    unsigned edgm_i20_n_{tag} = {vla_n};\n")

    if add_align_attr:
        out.append(f"{indent}#if defined(__GNUC__) || defined(__clang__)\n")
        out.append(f"{indent}#  define EDGM_I20C4_ALIGN_{tag} __attribute__((aligned(16)))\n")
        out.append(f"{indent}#else\n")
        out.append(f"{indent}#  define EDGM_I20C4_ALIGN_{tag} /*noalign*/\n")
        out.append(f"{indent}#endif\n")
        out.append(f"{indent}    int edgm_i20_vla_{tag}[edgm_i20_n_{tag}] EDGM_I20C4_ALIGN_{tag};\n")
        out.append(f"{indent}#undef EDGM_I20C4_ALIGN_{tag}\n")
    else:
        out.append(f"{indent}    int edgm_i20_vla_{tag}[edgm_i20_n_{tag}];\n")

    out.append(f"{indent}edgm_i20_L_{tag}:\n")
    out.append(f"{indent}    edgm_i20_vla_{tag}[0] = 0;\n")
    out.append(f"{indent}}}\n")

    if add_macro_noise:
        out.append(f"{indent}#undef EDGM_I20C4_CASE_{tag}\n")

    return "".join(out)


def insert_switch_goto_vla_adv(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    tag = rng.randint(10000, 99999)

    # 1) Prefer existing switch/case
    case_sites = _find_switch_case_sites(text)
    if case_sites:
        site = rng.choice(case_sites)
        # insert after the case/default line; indent inside case body is +4 spaces
        indent = site.indent + "    "
        inj = _make_injection(indent, tag, rng)

        new_lines: List[str] = []
        for i, ln in enumerate(lines):
            new_lines.append(ln)
            if i == site.line_idx:
                new_lines.append(inj)

        _write_text(out_path, "".join(new_lines))
        print(f"[debug] I20-C4-adv: seed={seed}")
        print(f"[debug] I20-C4-adv: inserted into existing switch-case at line {site.line_idx+1}, tag={tag}")
        return True

    # 2) Fallback: construct a new switch in a function body
    func_sites = _find_function_body_insertion_sites(text, rng)
    if not func_sites:
        _write_text(out_path, text)
        print("[debug] I20-C4-adv: no function bodies found (heuristic); skip")
        return False

    brace_line, indent = rng.choice(func_sites)

    wrapper: List[str] = []
    wrapper.append(f"{indent}/* edgm I20-C4-adv: synthesized switch wrapper, tag={tag} */\n")
    wrapper.append(f"{indent}switch ({_safe_cond_expr(rng)}) {{\n")
    wrapper.append(f"{indent}case 0:\n")
    wrapper.append(_make_injection(indent + "    ", tag, rng))
    wrapper.append(f"{indent}    break;\n")
    wrapper.append(f"{indent}default:\n")
    wrapper.append(f"{indent}    break;\n")
    wrapper.append(f"{indent}}}\n")
    inj = "".join(wrapper)

    new_lines: List[str] = []
    for i, ln in enumerate(lines):
        new_lines.append(ln)
        if i == brace_line:
            new_lines.append(inj)

    _write_text(out_path, "".join(new_lines))
    print(f"[debug] I20-C4-adv: seed={seed}")
    print(f"[debug] I20-C4-adv: inserted synthesized switch after '{{' line {brace_line+1}, tag={tag}")
    return True
