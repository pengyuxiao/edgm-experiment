# -*- coding: utf-8 -*-
"""
binary_op_incompatible_adv.py

C5 goal: advanced binary-operator semantic poison (type-check conflicts),
while avoiding dumb syntax breakage by NEVER touching unary '*' in casts or '(*p)'.

Robust across libclang versions:
- TypeKind enums are collected via getattr() so missing names won't crash.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

from clang import cindex


# -----------------------------
# Helpers: robust TypeKind collection
# -----------------------------

def _tk(name: str):
    return getattr(cindex.TypeKind, name, None)

def _collect_typekinds(*names: str):
    out = set()
    for n in names:
        v = _tk(n)
        if v is not None:
            out.add(v)
    return out


# -----------------------------
# Operator sets
# -----------------------------

SUPPORTED_OP_TOKENS = {
    "+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
    "==", "!=", "<", "<=", ">", ">=",
    "=", "+=", "-=", "*=", "/=", "%=",
    "&=", "|=", "^=", "<<=", ">>=",
    "&&", "||",
}

BINOP_KINDS = {
    cindex.CursorKind.BINARY_OPERATOR,
    cindex.CursorKind.COMPOUND_ASSIGNMENT_OPERATOR,
}

# Prefer semantic-poison modes
MODE_PRI = {
    "float_bitwise_illegal": 900,
    "ptr_plus_ptr": 850,
    "ptr_mul_div_mod_bitwise": 800,
    "struct_clash_fallback": 200,
}

POISON_MAP = {
    "INTEGER": ["&", "|", "^", "<<", ">>"],
    "FLOAT": ["&", "|", "^", "<<", ">>", "%"],  # float bitwise/mod are illegal
    "POINTER": ["*", "/", "%", "&", "|", "^", "<<", ">>", "+"],  # ptr-ptr with these is illegal
}

FLOAT_KINDS = _collect_typekinds("FLOAT", "DOUBLE", "LONGDOUBLE")

INTEGER_KINDS = _collect_typekinds(
    "BOOL",
    "CHAR_U", "UCHAR", "SCHAR",
    "CHAR16", "CHAR32",
    "USHORT", "UINT", "ULONG", "ULONGLONG",
    "SHORT", "INT", "LONG", "LONGLONG",
    "WCHAR",
    "ENUM",
)

POINTER_LIKE_KINDS = set([cindex.TypeKind.POINTER])
POINTER_LIKE_KINDS |= _collect_typekinds(
    # Different libclang/python bindings use different spellings
    "OBJCOBJECTPOINTER",   # common in clang.cindex
    "OBJC_OBJECT_POINTER", # sometimes seen elsewhere
    "MEMBERPOINTER",
)

ARRAY_KINDS = _collect_typekinds(
    "CONSTANTARRAY",
    "INCOMPLETEARRAY",
    "VARIABLEARRAY",
    "DEPENDENTSIZEDARRAY",
)


# -----------------------------
# Data structures
# -----------------------------

@dataclass
class Candidate:
    cursor: cindex.Cursor
    mode: str
    old_op: str
    new_op: str
    score: int
    tag: int
    op_token: Optional[cindex.Token] = None
    replace_span: Optional[Tuple[int, int]] = None   # [start, end) in main file offsets
    replace_text: Optional[str] = None               # replacement text


# -----------------------------
# IO
# -----------------------------

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def _write_text(path: str, s: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)

def _abs(p: str) -> str:
    return os.path.abspath(p)

def _safe_replace(src: str, start: int, end: int, repl: str) -> str:
    if not (0 <= start <= end <= len(src)):
        raise ValueError(f"bad replace span [{start},{end}) for len={len(src)}")
    return src[:start] + repl + src[end:]


# -----------------------------
# AST / token helpers
# -----------------------------

def _tok_is_from_main_file(tok: cindex.Token, main_abs: str) -> bool:
    try:
        f = tok.location.file
        return bool(f) and _abs(f.name) == main_abs
    except Exception:
        return False

def _cursor_in_main_file(cur: cindex.Cursor, main_abs: str) -> bool:
    try:
        f = cur.location.file
        return bool(f) and _abs(f.name) == main_abs
    except Exception:
        return False

def _canonical(t: cindex.Type) -> cindex.Type:
    try:
        return t.get_canonical()
    except Exception:
        return t

def _classify_type(t: cindex.Type) -> str:
    ct = _canonical(t)
    k = ct.kind

    if k in FLOAT_KINDS:
        return "FLOAT"
    if k in INTEGER_KINDS:
        return "INTEGER"
    if k in POINTER_LIKE_KINDS:
        # detect function pointer if possible
        try:
            pt = ct.get_pointee()
            if _canonical(pt).kind == _tk("FUNCTIONPROTO"):
                return "FUNCPTR"
        except Exception:
            pass
        return "POINTER"
    if k in ARRAY_KINDS:
        return "ARRAY"
    if k == _tk("RECORD"):
        return "RECORD"
    return "OTHER"

def _get_binop_children(cur: cindex.Cursor):
    kids = list(cur.get_children())
    if len(kids) < 2:
        return None
    return kids[0], kids[1]

def _extract_operator_token_between(tu: cindex.TranslationUnit, cur: cindex.Cursor, main_abs: str) -> Optional[cindex.Token]:
    """
    Critical: select operator token strictly BETWEEN lhs end and rhs start.
    This avoids touching unary '*' in (void*)0 or (*p).
    """
    lr = _get_binop_children(cur)
    if not lr:
        return None
    lhs, rhs = lr

    try:
        lhs_end = lhs.extent.end.offset
        rhs_start = rhs.extent.start.offset
    except Exception:
        lhs_end, rhs_start = None, None

    toks = list(tu.get_tokens(extent=cur.extent))
    between_ops: List[cindex.Token] = []
    any_ops: List[cindex.Token] = []

    for t in toks:
        if not _tok_is_from_main_file(t, main_abs):
            continue
        if t.kind != cindex.TokenKind.PUNCTUATION:
            continue
        if t.spelling not in SUPPORTED_OP_TOKENS:
            continue

        any_ops.append(t)
        if lhs_end is not None and rhs_start is not None:
            if t.extent.start.offset >= lhs_end and t.extent.end.offset <= rhs_start:
                between_ops.append(t)

    if between_ops:
        return between_ops[0]
    # fallback: take the last operator token in extent (still less likely unary than first)
    if any_ops:
        return any_ops[-1]
    return None

def _find_exact_op_token_between(tu: cindex.TranslationUnit, cur: cindex.Cursor, old_op: str, main_abs: str) -> Optional[cindex.Token]:
    lr = _get_binop_children(cur)
    if not lr:
        return None
    lhs, rhs = lr
    try:
        lhs_end = lhs.extent.end.offset
        rhs_start = rhs.extent.start.offset
    except Exception:
        lhs_end, rhs_start = None, None

    toks = list(tu.get_tokens(extent=cur.extent))
    between_match = None
    last_match = None
    for t in toks:
        if not _tok_is_from_main_file(t, main_abs):
            continue
        if t.kind != cindex.TokenKind.PUNCTUATION:
            continue
        if t.spelling != old_op:
            continue
        last_match = t
        if lhs_end is not None and rhs_start is not None:
            if t.extent.start.offset >= lhs_end and t.extent.end.offset <= rhs_start:
                between_match = t
                break
    return between_match or last_match

def _score(cur: cindex.Cursor) -> int:
    try:
        span = cur.extent.end.offset - cur.extent.start.offset
    except Exception:
        span = 0
    return min(600, max(0, span // 8))

def _make_tag(rnd: random.Random) -> int:
    return rnd.randint(10000, 99999)

def _pick_new_op(rnd: random.Random, pool: List[str], old_op: str) -> str:
    opts = [x for x in pool if x != old_op]
    return rnd.choice(opts) if opts else old_op

def _walk(cur: cindex.Cursor, fn) -> None:
    fn(cur)
    for c in cur.get_children():
        _walk(c, fn)


# -----------------------------
# Candidate generation
# -----------------------------

def _gen_candidates_for_cursor(tu: cindex.TranslationUnit, cur: cindex.Cursor, main_abs: str, rnd: random.Random) -> List[Candidate]:
    if cur.kind not in BINOP_KINDS:
        return []
    if not _cursor_in_main_file(cur, main_abs):
        return []

    lr = _get_binop_children(cur)
    if not lr:
        return []

    lhs_cur, rhs_cur = lr
    lhs_cls = _classify_type(lhs_cur.type)
    rhs_cls = _classify_type(rhs_cur.type)

    op_tok = _extract_operator_token_between(tu, cur, main_abs)
    if not op_tok:
        return []
    old_op = op_tok.spelling
    if old_op not in SUPPORTED_OP_TOKENS:
        return []

    base = _score(cur)
    tag = _make_tag(rnd)
    cands: List[Candidate] = []

    # Mode: float_bitwise_illegal
    if (lhs_cls == "FLOAT" or rhs_cls == "FLOAT") and old_op in {"+", "-", "*", "/"}:
        new_op = _pick_new_op(rnd, POISON_MAP["FLOAT"], old_op)
        cands.append(Candidate(
            cursor=cur,
            mode="float_bitwise_illegal",
            old_op=old_op,
            new_op=new_op,
            score=base + MODE_PRI["float_bitwise_illegal"],
            tag=tag,
            op_token=op_tok,
        ))

    # Mode: ptr_plus_ptr (turn ptrdiff into illegal sum)
    if lhs_cls in {"POINTER", "FUNCPTR"} and rhs_cls in {"POINTER", "FUNCPTR"} and old_op == "-":
        cands.append(Candidate(
            cursor=cur,
            mode="ptr_plus_ptr",
            old_op=old_op,
            new_op="+",
            score=base + MODE_PRI["ptr_plus_ptr"],
            tag=tag,
            op_token=op_tok,
        ))

    # Mode: ptr_mul_div_mod_bitwise (pointer-pointer illegal ops)
    if lhs_cls in {"POINTER", "FUNCPTR"} and rhs_cls in {"POINTER", "FUNCPTR"} and old_op in {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"}:
        new_op = _pick_new_op(rnd, POISON_MAP["POINTER"], old_op)
        cands.append(Candidate(
            cursor=cur,
            mode="ptr_mul_div_mod_bitwise",
            old_op=old_op,
            new_op=new_op,
            score=base + MODE_PRI["ptr_mul_div_mod_bitwise"],
            tag=tag,
            op_token=op_tok,
        ))

    # Fallback: struct_clash_fallback (replace whole expr)
    if old_op in {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", "==", "!=", "<", "<=", ">", ">="}:
        new_op = rnd.choice(["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"])
        repl = f"(((struct {{ int a; }}){{0}}) {new_op} ((struct {{ int b; }}){{0}}))"
        cands.append(Candidate(
            cursor=cur,
            mode="struct_clash_fallback",
            old_op=old_op,
            new_op=new_op,
            score=base + MODE_PRI["struct_clash_fallback"],
            tag=tag,
            op_token=op_tok,
            replace_span=(cur.extent.start.offset, cur.extent.end.offset),
            replace_text=repl,
        ))

    return cands


# -----------------------------
# Apply mutation
# -----------------------------

def _apply_candidate(tu: cindex.TranslationUnit, src: str, cand: Candidate, main_abs: str) -> str:
    # Whole-expression replacement
    if cand.replace_span and cand.replace_text is not None:
        start, end = cand.replace_span
        comment = f"/* edgm C-binary-op-adv+++: mode={cand.mode} old_op='{cand.old_op}' new_op='{cand.new_op}' tag={cand.tag} */ "
        return _safe_replace(src, start, end, comment + cand.replace_text)

    # Operator-token replacement
    tok = cand.op_token
    if not tok:
        tok = _find_exact_op_token_between(tu, cand.cursor, cand.old_op, main_abs)
        if not tok:
            raise RuntimeError("C-binary-op-adv+++: failed to locate operator token")

    start = tok.extent.start.offset
    end = tok.extent.end.offset
    comment = f"/* edgm C-binary-op-adv+++: mode={cand.mode} {cand.old_op}->{cand.new_op} tag={cand.tag} */ "
    return _safe_replace(src, start, end, comment + cand.new_op)


# -----------------------------
# Public entry
# -----------------------------

def mutate(in_path: str, out_path: str, seed: Optional[int] = None, clang_args: Optional[List[str]] = None) -> None:
    in_abs = _abs(in_path)
    src = _read_text(in_abs)
    rnd = random.Random(seed)

    if clang_args is None:
        clang_args = ["-std=c99", "-I", "src/csmithedge/csmith/runtime"]

    idx = cindex.Index.create()
    tu = idx.parse(
        in_abs,
        args=clang_args,
        options=(cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD |
                 cindex.TranslationUnit.PARSE_INCOMPLETE),
    )

    main_abs = in_abs
    cands: List[Candidate] = []

    def visit(cur: cindex.Cursor) -> None:
        if cur.kind in BINOP_KINDS and _cursor_in_main_file(cur, main_abs):
            cands.extend(_gen_candidates_for_cursor(tu, cur, main_abs, rnd))

    _walk(tu.cursor, visit)

    if not cands:
        raise RuntimeError("C-binary-op-adv+++: no suitable BinaryOperator candidates found")

    # Pick best (score desc), tie-break randomly
    cands.sort(key=lambda x: (x.score, rnd.random()), reverse=True)
    chosen = cands[0]

    # lock operator token (between lhs/rhs) for op-replace modes
    if not chosen.replace_span:
        chosen.op_token = _find_exact_op_token_between(tu, chosen.cursor, chosen.old_op, main_abs) or chosen.op_token
        if not chosen.op_token:
            raise RuntimeError("C-binary-op-adv+++: cannot lock operator token (between lhs/rhs)")

    out_src = _apply_candidate(tu, src, chosen, main_abs)
    _write_text(out_path, out_src)


# Optional CLI
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("in_path")
    ap.add_argument("out_path")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--clang-arg", action="append", default=None)
    args = ap.parse_args()
    mutate(args.in_path, args.out_path, seed=args.seed, clang_args=args.clang_arg)


if __name__ == "__main__":
    main()
