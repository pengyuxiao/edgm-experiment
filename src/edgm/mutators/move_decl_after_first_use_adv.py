# src/edgm/mutators/move_decl_after_first_use_adv.py
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict


@dataclass
class Candidate:
    func: str
    name: str
    vtype: str
    init: str
    decl_line: int  # 1-based
    first_use_line: int  # 1-based
    block_start: int  # 1-based
    block_end: int  # 1-based (line containing closing brace)
    indent: str
    score: int


_BLOCK_COMMENT_START = re.compile(r"/\*")
_BLOCK_COMMENT_END = re.compile(r"\*/")


def _strip_comments_keep_len(src: str) -> str:
    """
    Remove // and /* */ comments; keep newlines.
    This is best-effort; good enough for Csmith-style code.
    """
    out = []
    i = 0
    n = len(src)
    in_block = False
    while i < n:
        if not in_block and i + 1 < n and src[i] == "/" and src[i + 1] == "/":
            # line comment
            while i < n and src[i] != "\n":
                i += 1
            continue
        if not in_block and i + 1 < n and src[i] == "/" and src[i + 1] == "*":
            in_block = True
            i += 2
            continue
        if in_block and i + 1 < n and src[i] == "*" and src[i + 1] == "/":
            in_block = False
            i += 2
            continue
        if in_block:
            # preserve newlines to keep line numbers stable
            out.append("\n" if src[i] == "\n" else " ")
            i += 1
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def _compute_brace_blocks(lines: List[str]) -> Dict[int, Tuple[int, int]]:
    """
    Map each line index (1-based) to the innermost { ... } block's (start_line, end_line).
    Very lightweight brace matcher.
    """
    stack: List[int] = []
    blocks: List[Tuple[int, int]] = []
    # First pass: find all block ranges.
    for i, line in enumerate(lines, start=1):
        for ch in line:
            if ch == "{":
                stack.append(i)
            elif ch == "}":
                if stack:
                    start = stack.pop()
                    end = i
                    blocks.append((start, end))

    # Second pass: for each line, find innermost block (smallest span) containing it.
    per_line: Dict[int, Tuple[int, int]] = {}
    for i in range(1, len(lines) + 1):
        containing = [b for b in blocks if b[0] <= i <= b[1]]
        if not containing:
            continue
        # choose smallest span
        start, end = min(containing, key=lambda b: (b[1] - b[0], b[0]))
        per_line[i] = (start, end)
    return per_line


# Matches: "uint16_t *l_321 = &g_179;"
# Avoids commas (multi-decl), arrays, function pointers, etc.
_DECL_INIT_RE = re.compile(
    r"""
    ^(?P<indent>\s*)
    (?P<type>
        (?:const\s+|volatile\s+|static\s+|register\s+|extern\s+|signed\s+|unsigned\s+|struct\s+\w+\s+|union\s+\w+\s+|enum\s+\w+\s+|\w+\s+)*
        (?:\w+|struct\s+\w+|union\s+\w+|enum\s+\w+)
        (?:\s+|\s*\*+\s*)*
    )
    (?P<name>[A-Za-z_]\w*)
    \s*=\s*
    (?P<init>[^;]+)
    \s*;\s*$
    """,
    re.VERBOSE,
)


_FUNC_HEADER_RE = re.compile(r"^\s*(?:static\s+)?[A-Za-z_]\w*.*\b(func_\d+)\s*\(")


def _find_function_ranges(lines: List[str]) -> List[Tuple[str, int, int]]:
    """
    Best-effort: find 'func_xxx' and its outermost body braces.
    """
    funcs = []
    i = 1
    n = len(lines)
    while i <= n:
        m = _FUNC_HEADER_RE.search(lines[i - 1])
        if not m:
            i += 1
            continue
        fname = m.group(1)
        # find first '{' after header
        j = i
        found = False
        while j <= n:
            if "{" in lines[j - 1]:
                found = True
                break
            j += 1
        if not found:
            i += 1
            continue
        # match braces from that '{'
        brace = 0
        start = j
        k = j
        while k <= n:
            for ch in lines[k - 1]:
                if ch == "{":
                    brace += 1
                elif ch == "}":
                    brace -= 1
            if brace == 0:
                end = k
                funcs.append((fname, start, end))
                i = end + 1
                break
            k += 1
        else:
            i += 1
    return funcs


def _find_first_use(clean_lines: List[str], name: str, start_line: int, end_line: int) -> Optional[int]:
    pat = re.compile(rf"\b{re.escape(name)}\b")
    for ln in range(start_line, end_line + 1):
        if pat.search(clean_lines[ln - 1]):
            return ln
    return None


def _score(decl_line: int, use_line: int, block_end: int, vtype: str, init: str) -> int:
    # Prefer: use is near decl, and block has enough space to hide decl at end.
    d = abs(use_line - decl_line)
    tail_space = max(0, block_end - use_line)
    complexity = min(30, len(vtype) + len(init))  # mildly prefer “复杂”声明
    return (200 - d) + min(80, tail_space) + complexity


def _insert_before_closing_brace(lines: List[str], block_end_line: int, text: str) -> None:
    # Insert before the line that contains the closing brace of this innermost block.
    # If that line has only "}", we insert just above it.
    idx = block_end_line - 1
    lines.insert(idx, text)


def _choose_anchor_if_block(lines: List[str], block_start: int, block_end: int, rng: random.Random) -> Optional[int]:
    """
    Pick an existing 'if (...) {' line inside the block to hijack into deeper scope.
    Return line number of that 'if' line, else None.
    """
    candidates = []
    for ln in range(block_start, block_end + 1):
        s = lines[ln - 1]
        if re.search(r"^\s*if\s*\(.*\)\s*{", s):
            candidates.append(ln)
    if not candidates:
        return None
    return rng.choice(candidates)


def mutate(src_path: str, dst_path: str, seed: Optional[int] = None, keep_k: int = 1) -> bool:
    """
    ADV+++ C-move-decl:
      Mode A: Initializer Splitting (x=init before declaration; declaration hidden at end)
      Mode B: Scope Hijacking (declaration moved into unrelated deeper scope)
      Mode C: Move-to-block-end (full decl moved to end; all earlier uses break)

    keep_k: how many candidates to consider (pick best-k then random among them).
    """
    rng = random.Random(seed)

    with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    clean = _strip_comments_keep_len(src)
    lines = src.splitlines(True)        # keep \n
    clean_lines = clean.splitlines(True)

    per_line_block = _compute_brace_blocks(lines)
    funcs = _find_function_ranges(lines)

    cands: List[Candidate] = []

    for fname, f_start, f_end in funcs:
        # only consider decls inside function body
        for ln in range(f_start, f_end + 1):
            line = lines[ln - 1]
            if "for" in line and line.lstrip().startswith("for"):
                continue
            if "," in line:
                continue
            if "[" in line or "]" in line:
                continue
            if "(" in line and ")" in line and "*" in line:
                # very rough skip of func-pointer style
                continue

            m = _DECL_INIT_RE.match(line)
            if not m:
                continue

            name = m.group("name")
            vtype = (m.group("type") or "").strip()
            init = (m.group("init") or "").strip()
            indent = m.group("indent") or ""

            if ln not in per_line_block:
                continue
            bstart, bend = per_line_block[ln]

            # Ensure decl is inside function body block
            if not (f_start <= bstart <= bend <= f_end):
                continue

            first_use = _find_first_use(
                clean_lines=clean_lines,
                name=name,
                start_line=ln + 1,
                end_line=bend,
            )
            if first_use is None:
                continue

            sc = _score(ln, first_use, bend, vtype, init)
            cands.append(
                Candidate(
                    func=fname,
                    name=name,
                    vtype=vtype,
                    init=init,
                    decl_line=ln,
                    first_use_line=first_use,
                    block_start=bstart,
                    block_end=bend,
                    indent=indent,
                    score=sc,
                )
            )

    if not cands:
        # nothing mutated
        with open(dst_path, "w", encoding="utf-8") as f:
            f.write(src)
        return False

    cands.sort(key=lambda c: c.score, reverse=True)
    top = cands[: max(1, keep_k)]
    picked = rng.choice(top)

    tag = rng.randint(10000, 99999)

    # Decide mode (biased toward A)
    mode = rng.choices(
        population=["A_split_init", "B_scope_hijack", "C_move_to_block_end"],
        weights=[0.55, 0.30, 0.15],
        k=1,
    )[0]

    # Prepare replacement / insertion snippets
    decl_line_text = lines[picked.decl_line - 1]
    decl_no_nl = decl_line_text.rstrip("\n")
    nl = "\n" if decl_line_text.endswith("\n") else ""

    # Normalize declaration forms
    decl_without_init = f"{picked.indent}{picked.vtype} {picked.name};{nl}"
    assign_stmt = f"{picked.indent}{picked.name} = {picked.init};{nl}"

    if mode == "A_split_init":
        # 1) Replace decl with assignment (use-before-decl)
        lines[picked.decl_line - 1] = (
            f"{picked.indent}/* edgm C-move-decl-adv+++: Initializer-Splitting: "
            f"replace decl with assignment (func={picked.func}, decl_line={picked.decl_line}, "
            f"use_line={picked.first_use_line}, tag={tag}) */{nl}"
            + assign_stmt
        )
        # 2) Hide declaration at block end
        _insert_before_closing_brace(
            lines,
            picked.block_end,
            f"{picked.indent}/* edgm C-move-decl-adv+++: moved decl to block end (tag={tag}) */{nl}"
            + decl_without_init,
        )

    elif mode == "B_scope_hijack":
        # Replace decl with assignment (still breaks), then hide decl inside deeper scope.
        lines[picked.decl_line - 1] = (
            f"{picked.indent}/* edgm C-move-decl-adv+++: Scope-Hijack: "
            f"replace decl with assignment; redeclare in unrelated inner scope "
            f"(func={picked.func}, decl_line={picked.decl_line}, use_line={picked.first_use_line}, tag={tag}) */{nl}"
            + assign_stmt
        )

        anchor_if = _choose_anchor_if_block(lines, picked.block_start, picked.block_end, rng)
        if anchor_if is not None:
            # Put inside an existing if-body by injecting a new inner block right after the if-line:
            # if (...) {  -> if (...) { { T x; } ...
            # (This creates "decl exists but in child scope".)
            if_line = lines[anchor_if - 1]
            # Insert immediately after the if line
            insert_at = anchor_if  # 1-based line number after which we insert => list.insert(index)
            lines.insert(
                insert_at,
                f"{picked.indent}    /* edgm C-move-decl-adv+++: hijacked decl into child scope (tag={tag}) */{nl}"
                f"{picked.indent}    {picked.vtype} {picked.name};{nl}",
            )
        else:
            # Synthesize a fake inner-scope after first use to confuse scope resolver.
            _insert_before_closing_brace(
                lines,
                picked.block_end,
                f"{picked.indent}/* edgm C-move-decl-adv+++: synthesized scope hijack (tag={tag}) */{nl}"
                f"{picked.indent}if (0) {{{nl}"
                f"{picked.indent}    {picked.vtype} {picked.name};{nl}"
                f"{picked.indent}}}{nl}",
            )

    else:  # C_move_to_block_end
        # Move the *whole* declaration (with initializer) to block end.
        # This maximizes “all references broken”, and avoids adding new statements.
        lines[picked.decl_line - 1] = (
            f"{picked.indent}/* edgm C-move-decl-adv+++: Move-to-block-end: remove decl here "
            f"(func={picked.func}, decl_line={picked.decl_line}, use_line={picked.first_use_line}, tag={tag}) */{nl}"
        )
        _insert_before_closing_brace(
            lines,
            picked.block_end,
            f"{picked.indent}/* edgm C-move-decl-adv+++: moved original decl to block end (tag={tag}) */{nl}"
            f"{decl_no_nl}{nl}",
        )

    mutated = "".join(lines)

    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(mutated)

    print(f"[debug] C-move-decl-adv+++: seed={seed}")
    print(
        f"[debug] C-move-decl-adv+++: picked mode={mode} func={picked.func} name={picked.name} "
        f"decl_line={picked.decl_line} use_line={picked.first_use_line} block=({picked.block_start},{picked.block_end}) "
        f"score={picked.score} tag={tag}"
    )
    print(f"[结果] 已生成 C-move-decl-adv+++ 变异文件: {dst_path}")
    return True
