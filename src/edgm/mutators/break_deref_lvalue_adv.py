# -*- coding: utf-8 -*-
"""
ADV+++ (补充6 + 星号混淆增强):
- 多级指针写语境（**p/*****a）左值破坏：outer/inner/rvalue 三分支随机
- 乘法* 与解引用* 混淆：X * (*****a) -> X ******a
关键修复：
- 绝不在“声明语句”的声明子里做 write-deref 变异（否则会产生语法错误）
"""

from __future__ import annotations

import re
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


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
    t = line.strip()
    return (not t) or t.startswith("#")


# ---------------- function range detection ----------------

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
                    funcs.append(FuncRange(name, start, header_end, j))
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
                        funcs.append(FuncRange(name, start, header_end, j))
                        i = j + 1
                        break
                    j += 1
                else:
                    i += 1
                continue

        i += 1

    return funcs


# ---------------- patterns ----------------

_ASSIGN_OPS = r"(?:=|\+=|-=|\*=|/=|%=|<<=|>>=|&=|\|=|\^=)"
_DEREF = r"(?:\(\s*(?P<stars1>\*+)\s*(?P<id1>[A-Za-z_]\w*)\s*\)|(?P<stars2>\*+)\s*(?P<id2>[A-Za-z_]\w*))"

_RE_ASSIGN = re.compile(rf"(?P<deref>{_DEREF})\s*(?P<op>{_ASSIGN_OPS})")
_RE_PREINC = re.compile(rf"(?P<inc>\+\+|--)\s*(?P<deref>{_DEREF})")
_RE_POSTINC = re.compile(rf"(?P<deref>{_DEREF})\s*(?P<inc>\+\+|--)")

_RE_MUL_DEREF_PAREN = re.compile(
    r"""
    (?P<lhs>
        (?:\b[A-Za-z_]\w*\b|\b0x[0-9A-Fa-f]+\b|\b\d+\b|\([^()]*\))
        \s*
    )
    \*
    \s*
    \(
        \s*(?P<stars>\*{2,})\s*(?P<id>[A-Za-z_]\w*)\s*
    \)
    """,
    re.VERBOSE,
)

# 声明语句启发式：行首像类型/存储类开头，且是“完整语句行”时认为是 DeclStmt
_DECL_START_RE = re.compile(
    r"""^\s*
        (?:static|extern|register|auto)?\s*
        (?:(?:const|volatile|restrict)\s+)*
        (?:
            (?:signed|unsigned|long|short)\s+
        )*
        (?:
            struct|union|enum|
            void|char|int|float|double|_Bool|
            [A-Za-z_]\w*_t
        )\b
    """,
    re.VERBOSE,
)


def _is_decl_stmt_line(stmt: str) -> bool:
    s = stmt.strip()
    if not s.endswith(";"):
        return False
    # 过滤掉 "return T;" 这类（虽然不太会被匹配到）
    if s.startswith(("return ", "goto ", "break", "continue")):
        return False
    return _DECL_START_RE.match(s) is not None


# ---------------- data ----------------

@dataclass
class Occur:
    line_idx: int
    start: int
    end: int
    kind: str   # assign | preinc | postinc | mulconfuse
    ident: str
    stars: int
    score: int
    extra: Optional[dict] = None


def _collect_occurs(lines: List[str], fr: FuncRange) -> List[Occur]:
    occs: List[Occur] = []
    for i in range(fr.header_end + 1, fr.end + 1):
        raw = lines[i]
        s = _strip_comments_line(raw)
        if s.lstrip().startswith("#"):
            continue

        # 避免在 for/if/while 头部搞事（很容易变成语法噪声）
        lead = s.lstrip()
        if lead.startswith(("for ", "for(", "if ", "if(", "while ", "while(")):
            # mulconfuse 仍然允许（它只改表达式，不碰声明子）
            pass

        is_decl_stmt = _is_decl_stmt_line(s)

        # --- A) 写语境多级解引用：必须确保不是声明语句里的声明子 ---
        for m in _RE_ASSIGN.finditer(s):
            ident = m.group("id1") or m.group("id2")
            stars = len(m.group("stars1") or m.group("stars2") or "")
            deref_span = m.span("deref")
            op = m.group("op")

            # ✅ 关键修复：跳过声明语句中的 “T ****x = ...;”
            if is_decl_stmt and op == "=":
                eq_pos = s.find("=")
                if eq_pos != -1 and deref_span[0] < eq_pos:
                    continue

            # 也跳过 for/if/while 行中的 assign（减少语法噪声）
            if lead.startswith(("for ", "for(", "if ", "if(", "while ", "while(")):
                continue

            bonus = 15 if op != "=" else 0
            bonus += 25 if stars >= 2 else 0
            occs.append(Occur(i, deref_span[0], deref_span[1], "assign", ident, stars, 60 + bonus))

        for m in _RE_PREINC.finditer(s):
            ident = m.group("id1") or m.group("id2")
            stars = len(m.group("stars1") or m.group("stars2") or "")
            deref_span = m.span("deref")
            if is_decl_stmt:
                continue
            if lead.startswith(("for ", "for(", "if ", "if(", "while ", "while(")):
                continue
            bonus = 25 if stars >= 2 else 0
            occs.append(Occur(i, deref_span[0], deref_span[1], "preinc", ident, stars, 70 + bonus))

        for m in _RE_POSTINC.finditer(s):
            ident = m.group("id1") or m.group("id2")
            stars = len(m.group("stars1") or m.group("stars2") or "")
            deref_span = m.span("deref")
            if is_decl_stmt:
                continue
            if lead.startswith(("for ", "for(", "if ", "if(", "while ", "while(")):
                continue
            bonus = 25 if stars >= 2 else 0
            occs.append(Occur(i, deref_span[0], deref_span[1], "postinc", ident, stars, 70 + bonus))

        # --- B) 乘法* 与解引用* 混淆：允许出现在初始化表达式里（不碰声明子） ---
        for m in _RE_MUL_DEREF_PAREN.finditer(s):
            span0 = m.span(0)
            stars = len(m.group("stars"))
            ident = m.group("id")
            occs.append(
                Occur(
                    line_idx=i,
                    start=span0[0],
                    end=span0[1],
                    kind="mulconfuse",
                    ident=ident,
                    stars=stars,
                    score=95,
                    extra={"lhs": m.group("lhs")},
                )
            )

    return occs


def _pick_one(lines: List[str], rng: random.Random) -> Optional[Occur]:
    funcs = _find_functions(lines)
    all_occs: List[Occur] = []
    for fr in funcs:
        all_occs.extend(_collect_occurs(lines, fr))

    if not all_occs:
        return None

    # ✅ 高级池：多级解引用(stars>=2) 或 星号混淆(mulconfuse)
    adv = [o for o in all_occs if (o.kind == "mulconfuse" or o.stars >= 2)]
    basic = all_occs

    # ✅ 70% 概率优先抽高级；如果没有高级就退回 basic
    pool = adv if (adv and rng.random() < 0.70) else basic

    pool.sort(key=lambda x: x.score, reverse=True)
    topk = pool[: max(4, min(14, len(pool)))]
    return rng.choice(topk)



# ---------------- payloads ----------------

def _payload_rvalue(ident: str, rng: random.Random) -> str:
    payloads = [
        "(1)",
        "((1) + 0)",
        f"(({ident}) - ({ident}))",
        f"(({ident}) ? ({ident}) : ({ident}))",
    ]
    return rng.choice(payloads)


def _payload_inner_break(ident: str, rng: random.Random) -> str:
    payloads = [
        "(1)",
        "((1) + 0)",
        f"(({ident}) - ({ident}))",
        f"(({ident}) * 1)",
    ]
    return rng.choice(payloads)


def _mutate_write_deref(stars: int, ident: str, rng: random.Random) -> str:
    if stars <= 1:
        return _payload_rvalue(ident, rng)

    mode = rng.choices(
        ["outer", "inner", "rvalue"],
        weights=[45, 45, 10],
        k=1
    )[0]

    if mode == "outer":
        orig = "*" * stars + ident
        return f"(({orig}) * 1)"

    if mode == "inner":
        outer = "*" * (stars - 1)
        inner = _payload_inner_break(ident, rng)
        return f"{outer}{inner}"

    return _payload_rvalue(ident, rng)


def _mutate_line_once(line: str, occ: Occur, rng: random.Random) -> str:
    before = line[:occ.start]
    after = line[occ.end:]

    if occ.kind == "mulconfuse":
        lhs = (occ.extra or {}).get("lhs", "")
        new_rhs = ("*" * (occ.stars + 1)) + occ.ident
        repl = f"{lhs}{new_rhs}"
        return before + repl + after

    new_deref = _mutate_write_deref(occ.stars, occ.ident, rng)
    return before + new_deref + after


# ---------------- public entry ----------------

def insert_break_deref_lvalue_adv(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    occ = _pick_one(lines, rng)
    if not occ:
        _write_text(out_path, text)
        print("[debug] C-deref-lvalue-adv+++: no suitable site; skip")
        return False

    tag = rng.randint(10000, 99999)
    li = occ.line_idx
    old = lines[li]
    new = _mutate_line_once(old, occ, rng)

    if new == old:
        _write_text(out_path, text)
        print(f"[debug] C-deref-lvalue-adv+++: picked line {li+1} but rewrite failed; skip")
        return False

    note = (
        f"/* edgm C-deref-lvalue-adv+++: "
        f"kind={occ.kind}, ident={occ.ident}, stars={occ.stars}, tag={tag} */\n"
    )
    lines[li] = note + new
    _write_text(out_path, "".join(lines))

    print(f"[debug] C-deref-lvalue-adv+++: seed={seed}")
    print(f"[debug] C-deref-lvalue-adv+++: line={li+1}, kind={occ.kind}, ident={occ.ident}, stars={occ.stars}, tag={tag}")
    return True
