# -*- coding: utf-8 -*-
"""
C-implicit-conv-adv+++ : Implicit-Conversion Poison (Advanced)

Goal: generate *invalid C code* to probe compiler diagnostic inconsistencies.

Key idea:
  - Target contexts that normally *permit implicit conversions* (init/assign/return/cond).
  - Replace the RHS/expression with a "semantic poison" that is *guaranteed* to be illegal:
        struct <anonymous>  -> scalar
    This reliably triggers hard errors like:
        error: incompatible types when initializing type 'double' using type 'struct <anonymous>'
        error: invalid operands to binary ...

Important: this mutator NEVER fails. If no suitable candidate is found,
it injects a fallback poison block into the first function body.

Usage (called by your script):
    mutate(in_path, out_path, seed=None)
"""

from __future__ import annotations

import os
import random
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from clang import cindex


# ----------------------------
# Utilities: offsets & slicing
# ----------------------------

def _compute_line_starts(text: str) -> List[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts

def _offset_from_line_col(line_starts: List[int], line: int, col: int) -> int:
    # clang line/col are 1-based
    if line <= 0 or col <= 0:
        return 0
    idx = line - 1
    if idx >= len(line_starts):
        return len(line_starts) and line_starts[-1] or 0
    return line_starts[idx] + (col - 1)

def _slice_replace(text: str, start: int, end: int, repl: str) -> str:
    start = max(0, min(len(text), start))
    end = max(0, min(len(text), end))
    if end < start:
        start, end = end, start
    return text[:start] + repl + text[end:]


# ----------------------------
# Type helpers
# ----------------------------

def _canonical_kind(t: cindex.Type) -> cindex.TypeKind:
    try:
        return t.get_canonical().kind
    except Exception:
        return t.kind

def _is_scalar(t: cindex.Type) -> bool:
    k = _canonical_kind(t)
    # conservative scalar set
    return k in (
        cindex.TypeKind.BOOL,
        cindex.TypeKind.CHAR_U,
        cindex.TypeKind.UCHAR,
        cindex.TypeKind.CHAR16,
        cindex.TypeKind.CHAR32,
        cindex.TypeKind.USHORT,
        cindex.TypeKind.UINT,
        cindex.TypeKind.ULONG,
        cindex.TypeKind.ULONGLONG,
        cindex.TypeKind.CHAR_S,
        cindex.TypeKind.SCHAR,
        cindex.TypeKind.WCHAR,
        cindex.TypeKind.SHORT,
        cindex.TypeKind.INT,
        cindex.TypeKind.LONG,
        cindex.TypeKind.LONGLONG,
        cindex.TypeKind.FLOAT,
        cindex.TypeKind.DOUBLE,
        cindex.TypeKind.LONGDOUBLE,
        cindex.TypeKind.ENUM,
        cindex.TypeKind.POINTER,
        cindex.TypeKind.OBJCID,
        getattr(cindex.TypeKind, "OBJCOBJECTPOINTER", getattr(cindex.TypeKind, "OBJC_OBJECT_POINTER", cindex.TypeKind.INVALID)),
    )

def _is_floating(t: cindex.Type) -> bool:
    k = _canonical_kind(t)
    return k in (cindex.TypeKind.FLOAT, cindex.TypeKind.DOUBLE, cindex.TypeKind.LONGDOUBLE)

def _is_integer(t: cindex.Type) -> bool:
    k = _canonical_kind(t)
    return k in (
        cindex.TypeKind.BOOL,
        cindex.TypeKind.CHAR_U,
        cindex.TypeKind.UCHAR,
        cindex.TypeKind.CHAR16,
        cindex.TypeKind.CHAR32,
        cindex.TypeKind.USHORT,
        cindex.TypeKind.UINT,
        cindex.TypeKind.ULONG,
        cindex.TypeKind.ULONGLONG,
        cindex.TypeKind.CHAR_S,
        cindex.TypeKind.SCHAR,
        cindex.TypeKind.WCHAR,
        cindex.TypeKind.SHORT,
        cindex.TypeKind.INT,
        cindex.TypeKind.LONG,
        cindex.TypeKind.LONGLONG,
        cindex.TypeKind.ENUM,
    )

def _type_str(t: cindex.Type) -> str:
    try:
        return t.spelling
    except Exception:
        return "<unknown>"


# ----------------------------
# Candidate model
# ----------------------------

@dataclass
class Candidate:
    kind: str                   # "init" | "assign" | "return" | "cond" | ...
    extent: Tuple[int, int]     # byte offsets in source for expression to replace
    lhs_type: str
    rhs_type: str
    score: int
    debug_loc: str


def _clang_args() -> List[str]:
    # Allow override via env; fallback is minimal.
    # If your project needs include paths, pass them via EDGM_CLANG_ARGS, e.g.:
    #   export EDGM_CLANG_ARGS="-std=c99 -I src/csmithedge/csmith/runtime"
    env = os.environ.get("EDGM_CLANG_ARGS", "").strip()
    if env:
        return env.split()
    return ["-std=c99"]


# ----------------------------
# AST scanning
# ----------------------------

def _iter_children(node: cindex.Cursor):
    try:
        for ch in node.get_children():
            yield ch
    except Exception:
        return

def _token_text(cursor: cindex.Cursor) -> str:
    try:
        return " ".join([t.spelling for t in cursor.get_tokens()])
    except Exception:
        return ""

def _is_in_main_file(cur: cindex.Cursor, main_path: str) -> bool:
    try:
        loc = cur.location
        if not loc or not loc.file:
            return False
        return os.path.samefile(loc.file.name, main_path)
    except Exception:
        # fallback string compare
        try:
            return cur.location.file and cur.location.file.name == main_path
        except Exception:
            return False

def _extent_offsets(cur: cindex.Cursor, line_starts: List[int]) -> Optional[Tuple[int, int]]:
    try:
        ex = cur.extent
        if not ex:
            return None
        s = ex.start
        e = ex.end
        start = _offset_from_line_col(line_starts, s.line, s.column)
        end = _offset_from_line_col(line_starts, e.line, e.column)
        if end <= start:
            return None
        return (start, end)
    except Exception:
        return None

def _score_context(kind: str, lhs_t: cindex.Type, rhs_t: cindex.Type) -> int:
    # High priority if it looks like a "normal" conversion site (init/assign/return)
    base = {"init": 300, "assign": 280, "return": 260, "cond": 220}.get(kind, 100)

    # Boost if involves floating/integer boundary (your "int<->float" theme)
    if (_is_integer(lhs_t) and _is_floating(rhs_t)) or (_is_floating(lhs_t) and _is_integer(rhs_t)):
        base += 200

    # Boost if both are scalar (most "natural", so poisoning is more deceptive)
    if _is_scalar(lhs_t) and _is_scalar(rhs_t):
        base += 80

    return base

def _collect_candidates(tu: cindex.TranslationUnit, src: str, main_path: str) -> List[Candidate]:
    line_starts = _compute_line_starts(src)
    cands: List[Candidate] = []

    def visit(cur: cindex.Cursor):
        if not _is_in_main_file(cur, main_path):
            return

        # 1) ReturnStmt: replace return expression (if any)
        if cur.kind == cindex.CursorKind.RETURN_STMT:
            kids = list(_iter_children(cur))
            if kids:
                expr = kids[0]
                ex_off = _extent_offsets(expr, line_starts)
                if ex_off:
                    # lhs type is function result type (best-effort: from semantic parent)
                    try:
                        fn = cur.semantic_parent
                        lhs_t = fn.result_type if fn and fn.kind == cindex.CursorKind.FUNCTION_DECL else cindex.Type()
                    except Exception:
                        lhs_t = cindex.Type()
                    rhs_t = expr.type
                    score = _score_context("return", lhs_t, rhs_t)
                    cands.append(
                        Candidate(
                            kind="return",
                            extent=ex_off,
                            lhs_type=_type_str(lhs_t),
                            rhs_type=_type_str(rhs_t),
                            score=score,
                            debug_loc=f"{cur.location.line}:{cur.location.column}",
                        )
                    )

        # 2) VarDecl with initializer: replace init expr
        if cur.kind == cindex.CursorKind.VAR_DECL:
            kids = list(_iter_children(cur))
            if kids:
                # Heuristic: initializer is often the last child expr
                init_expr = kids[-1]
                if init_expr and init_expr.type.kind != cindex.TypeKind.INVALID:
                    ex_off = _extent_offsets(init_expr, line_starts)
                    if ex_off:
                        lhs_t = cur.type
                        rhs_t = init_expr.type
                        score = _score_context("init", lhs_t, rhs_t)
                        cands.append(
                            Candidate(
                                kind="init",
                                extent=ex_off,
                                lhs_type=_type_str(lhs_t),
                                rhs_type=_type_str(rhs_t),
                                score=score,
                                debug_loc=f"{cur.location.line}:{cur.location.column}",
                            )
                        )

        # 3) BinaryOperator '=' : replace RHS
        if cur.kind == cindex.CursorKind.BINARY_OPERATOR:
            # Determine operator by token scan (simple & robust)
            toks = [t.spelling for t in cur.get_tokens()]
            # try find '=' but not '==' or '!=' or '<=' etc.
            if "=" in toks and "==" not in toks:
                kids = list(_iter_children(cur))
                if len(kids) >= 2:
                    lhs, rhs = kids[0], kids[1]
                    # ensure it's an assignment-ish site
                    ex_off = _extent_offsets(rhs, line_starts)
                    if ex_off:
                        lhs_t = lhs.type
                        rhs_t = rhs.type
                        score = _score_context("assign", lhs_t, rhs_t)
                        cands.append(
                            Candidate(
                                kind="assign",
                                extent=ex_off,
                                lhs_type=_type_str(lhs_t),
                                rhs_type=_type_str(rhs_t),
                                score=score,
                                debug_loc=f"{cur.location.line}:{cur.location.column}",
                            )
                        )

        # 4) IfStmt / WhileStmt / ForStmt condition: replace condition expr
        if cur.kind in (cindex.CursorKind.IF_STMT, cindex.CursorKind.WHILE_STMT, cindex.CursorKind.DO_STMT, cindex.CursorKind.FOR_STMT):
            kids = list(_iter_children(cur))
            if kids:
                cond = kids[0]
                # ForStmt first child is often init; condition may be second.
                if cur.kind == cindex.CursorKind.FOR_STMT and len(kids) >= 2:
                    cond = kids[1]
                ex_off = _extent_offsets(cond, line_starts)
                if ex_off:
                    lhs_t = cindex.Type()  # condition context
                    rhs_t = cond.type
                    score = _score_context("cond", lhs_t, rhs_t)
                    cands.append(
                        Candidate(
                            kind="cond",
                            extent=ex_off,
                            lhs_type="<cond>",
                            rhs_type=_type_str(rhs_t),
                            score=score,
                            debug_loc=f"{cur.location.line}:{cur.location.column}",
                        )
                    )

        for ch in _iter_children(cur):
            visit(ch)

    visit(tu.cursor)
    return cands


# ----------------------------
# Poison builders
# ----------------------------

def _make_struct_poison(tag: int) -> str:
    # Struct compound literal => *never* implicitly convertible to scalar.
    return f"/* edgm C-implicit-conv-adv+++: struct_poison tag={tag} */ ((struct {{ int a; }}){{0}})"

def _make_struct_poison_pair(tag: int) -> str:
    # Two distinct structs for more "semantic" clash (useful in binary contexts if you extend later)
    return f"/* edgm C-implicit-conv-adv+++: struct_poison_pair tag={tag} */ (((struct {{ int a; }}){{0}}))"

def _make_void_poison(tag: int) -> str:
    # Use void expression in value context: also a hard error in many contexts
    return f"/* edgm C-implicit-conv-adv+++: void_poison tag={tag} */ ((void)0)"

def _choose_poison(rnd: random.Random, cand: Candidate) -> str:
    # Prefer struct poison (hard error, not warning)
    # For condition contexts, void_poison can be extra nasty in some compilers.
    if cand.kind == "cond" and rnd.random() < 0.35:
        return _make_void_poison(rnd.randrange(10_000, 99_999))
    return _make_struct_poison(rnd.randrange(10_000, 99_999))


# ----------------------------
# Fallback injection (never fail)
# ----------------------------

def _inject_fallback_into_first_function(tu: cindex.TranslationUnit, src: str, main_path: str, rnd: random.Random) -> Optional[str]:
    """
    Insert poison right after the opening '{' of the first function definition.
    Guaranteed to create a compile error by struct->double initialization.
    """
    tag = rnd.randrange(10_000, 99_999)

    def find_first_fn(cur: cindex.Cursor) -> Optional[cindex.Cursor]:
        if cur.kind == cindex.CursorKind.FUNCTION_DECL and cur.is_definition() and _is_in_main_file(cur, main_path):
            return cur
        for ch in _iter_children(cur):
            r = find_first_fn(ch)
            if r:
                return r
        return None

    fn = find_first_fn(tu.cursor)
    if not fn:
        return None

    # Find '{' token and insert after it
    try:
        tokens = list(fn.get_tokens())
        for i, t in enumerate(tokens):
            if t.spelling == "{":
                # insert after '{' -> use token extent end
                line_starts = _compute_line_starts(src)
                off = _offset_from_line_col(line_starts, t.extent.end.line, t.extent.end.column)
                payload = (
                    f"\n    /* edgm C-implicit-conv-adv+++: FALLBACK_INJECT tag={tag} */\n"
                    f"    struct {{ int a; }} edgm_s_{tag} = {{0}};\n"
                    f"    double edgm_d_{tag} = edgm_s_{tag};\n"
                )
                return _slice_replace(src, off, off, payload)
    except Exception:
        return None

    return None


# ----------------------------
# Main mutate
# ----------------------------

def mutate(in_path: str, out_path: str, seed: Optional[int] = None) -> None:
    rnd = random.Random(seed)
    
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    
    with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    index = cindex.Index.create()
    args = _clang_args()

    try:
        tu = index.parse(in_path, args=args, options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
    except Exception as e:
        # If parsing fails, still do fallback injection by raw text heuristic:
        # insert at first occurrence of "{\n" after "func_" or "main("
        tag = rnd.randrange(10_000, 99_999)
        insert = (
            f"\n    /* edgm C-implicit-conv-adv+++: PARSE_FAIL_FALLBACK tag={tag} */\n"
            f"    struct {{ int a; }} edgm_s_{tag} = {{0}};\n"
            f"    double edgm_d_{tag} = edgm_s_{tag};\n"
        )
        idx = src.find("{\n")
        if idx != -1:
            out = src[:idx + 2] + insert + src[idx + 2:]
        else:
            out = insert + src
        with open(out_path, "w", encoding="utf-8") as g:
            g.write(out)
        print(f"[debug] C-implicit-conv-adv+++: clang parse failed ({e}); wrote fallback mutation: {out_path}")
        return

    cands = _collect_candidates(tu, src, os.path.abspath(in_path))
    # rank
    cands.sort(key=lambda c: c.score, reverse=True)

    if cands:
        # pick among top-K to keep diversity
        k = min(25, len(cands))
        chosen = rnd.choice(cands[:k])
        poison = _choose_poison(rnd, chosen)

        start, end = chosen.extent
        new_src = _slice_replace(src, start, end, poison)

        with open(out_path, "w", encoding="utf-8") as g:
            g.write(new_src)

        print(f"[debug] C-implicit-conv-adv+++: picked kind={chosen.kind} loc={chosen.debug_loc} score={chosen.score}")
        print(f"[debug] C-implicit-conv-adv+++: lhs={chosen.lhs_type} rhs={chosen.rhs_type}")
        print(f"[result] wrote: {out_path}")
        return

    # fallback injection
    injected = _inject_fallback_into_first_function(tu, src, os.path.abspath(in_path), rnd)
    if injected is None:
        # last-resort: just prepend a top-level poison (still should fail)
        tag = rnd.randrange(10_000, 99_999)
        injected = (
            f"/* edgm C-implicit-conv-adv+++: TOPLEVEL_FALLBACK tag={tag} */\n"
            f"struct {{ int a; }} edgm_s_{tag} = {{0}};\n"
            f"double edgm_d_{tag} = edgm_s_{tag};\n\n"
        ) + src

    with open(out_path, "w", encoding="utf-8") as g:
        g.write(injected)

    print("[debug] C-implicit-conv-adv+++: no candidate found -> fallback injected")
    print(f"[result] wrote: {out_path}")
