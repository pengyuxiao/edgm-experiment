# src/edgm/mutators/for_init_inject_bad_expr_advppp.py
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


_C_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


@dataclass
class ForSite:
    start: int
    end: int
    header: str
    init: str
    cond: str
    inc: str
    name: Optional[str]
    line: int
    score: int


def _split_for_header(h: str) -> Optional[Tuple[str, str, str]]:
    """
    Split 'init; cond; inc' at top level (paren depth == 0) inside for(...).
    Return (init, cond, inc) stripped.
    """
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    i = 0
    while i < len(h):
        ch = h[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _find_matching_paren(s: str, open_idx: int) -> Optional[int]:
    depth = 0
    i = open_idx
    while i < len(s):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _extract_loop_var(init: str) -> Optional[str]:
    """
    Best-effort extraction of loop variable name from init part.
    Supports:
      - i = 0
      - ++i, i++, i += 1  (treated as i)
      - int i = 0  / uint32_t i = ...
      - for-init declarations with qualifiers/pointers are heuristically handled
    """
    # Common assignment-like
    m = re.match(rf"^\s*({_C_IDENT})\s*=", init)
    if m:
        return m.group(1)

    # Declaration-like: (type stuff) name (= ...)
    # We look for the last identifier before an optional '='
    # e.g. "int32_t *i = 0" -> i
    #      "unsigned long i" -> i
    #      "struct S i = {0}" -> i
    m = re.match(rf"^\s*(?:.+?)\s+({_C_IDENT})\s*(?:=|$)", init)
    if m and m.group(1) not in ("for",):
        return m.group(1)

    # Fallback: ++i / i++
    m = re.match(rf"^\s*(?:\+\+)?\s*({_C_IDENT})\s*(?:\+\+)?\s*$", init)
    if m:
        return m.group(1)

    return None


def _count_token(s: str, name: str) -> int:
    return len(re.findall(rf"\b{re.escape(name)}\b", s))


def _calc_line(src: str, pos: int) -> int:
    # 1-based
    return src.count("\n", 0, pos) + 1


def _collect_for_sites(src: str) -> List[ForSite]:
    sites: List[ForSite] = []
    idx = 0
    while True:
        k = src.find("for", idx)
        if k < 0:
            break
        # Ensure "for" is a token
        if k > 0 and re.match(r"[A-Za-z0-9_]", src[k - 1]):
            idx = k + 3
            continue

        # Skip whitespace to '('
        j = k + 3
        while j < len(src) and src[j].isspace():
            j += 1
        if j >= len(src) or src[j] != "(":
            idx = k + 3
            continue

        close = _find_matching_paren(src, j)
        if close is None:
            idx = k + 3
            continue

        header = src[j + 1 : close]
        split = _split_for_header(header)
        if not split:
            idx = close + 1
            continue
        init, cond, inc = split
        name = _extract_loop_var(init)

        score = 0
        if name:
            # “更恶毒”：优先挑 cond/inc 里名字出现得多、且 inc 常带 ++ 的
            score += 10 * _count_token(cond, name)
            score += 12 * _count_token(inc, name)
            if "++" in inc or "--" in inc:
                score += 25
            if any(op in cond for op in ("<", ">", "<=", ">=", "==", "!=")):
                score += 10

        sites.append(
            ForSite(
                start=j + 1,
                end=close,
                header=header,
                init=init,
                cond=cond,
                inc=inc,
                name=name,
                line=_calc_line(src, k),
                score=score,
            )
        )
        idx = close + 1
    return sites


def _make_poison_init(name: str, tag: int, mode: str, old_init: str) -> str:
    # 只改 init 文本；cond/inc 不动，但语义会被“同名遮蔽”污染
    if mode == "A_scope_poison_struct":
        # struct 类型最稳：后续 i < 3 / i++ 基本必炸，而且诊断串很容易在 GCC/Clang 出现差异
        return (
            f"/* edgm C-for-init-adv+++: mode=A_scope_poison_struct shadow `{name}` (tag={tag}) "
            f"old_init={old_init!r} */ "
            f"struct {{ int a; }} {name} = {{0}}"
        )

    if mode == "A_scope_poison_voidptr":
        return (
            f"/* edgm C-for-init-adv+++: mode=A_scope_poison_voidptr shadow `{name}` (tag={tag}) "
            f"old_init={old_init!r} */ "
            f"void *{name} = (void*)0"
        )

    if mode == "B_illegal_storage":
        # 一些编译器/诊断配置对 for-init 里的 static/attribute 报错点不同（解析期 vs 语义期）
        return (
            f"/* edgm C-for-init-adv+++: mode=B_illegal_storage poison `{name}` (tag={tag}) "
            f"old_init={old_init!r} */ "
            f"static __attribute__((unused)) int {name} = 0"
        )

    if mode == "C_sizeof_incomplete":
        # 你当前跑出来的就是这类
        return (
            f"/* edgm C-for-init-adv+++: mode=C_sizeof_incomplete poison `{name}` (tag={tag}) "
            f"old_init={old_init!r} */ "
            f"int {name} = (int)sizeof(struct edgm_undef_{tag})"
        )

    # fallback
    return (
        f"/* edgm C-for-init-adv+++: mode=A_scope_poison_struct shadow `{name}` (tag={tag}) "
        f"old_init={old_init!r} */ "
        f"struct {{ int a; }} {name} = {{0}}"
    )


def mutate(src_path: str, out_path: str, seed: Optional[int] = None) -> None:
    src = open(src_path, "r", encoding="utf-8", errors="ignore").read()

    rnd = random.Random(seed)
    tag = rnd.randint(10000, 99999)

    sites = _collect_for_sites(src)
    # 只选有 loop var 的；优先高分（cond/inc 强依赖该变量）
    sites = [s for s in sites if s.name and s.score > 0]
    if not sites:
        # 没找到就原样写出（保持管线稳定）
        open(out_path, "w", encoding="utf-8").write(src)
        return

    sites.sort(key=lambda x: x.score, reverse=True)

    # 从 top-K 里抽一个，避免每次都打同一处
    k = min(8, len(sites))
    pick = sites[rnd.randrange(k)]

    # 更恶毒：优先 mode A（只插入声明，cond/inc 不改文本，但会因遮蔽而“类型突变”）
    modes = [
        "A_scope_poison_struct",
        "A_scope_poison_voidptr",
        "B_illegal_storage",
        "C_sizeof_incomplete",
    ]
    weights = [60, 20, 10, 10]
    mode = rnd.choices(modes, weights=weights, k=1)[0]

    new_init = _make_poison_init(pick.name or "i", tag, mode, pick.init)

    new_header = f"{new_init}; {pick.cond}; {pick.inc}"
    mutated = src[: pick.start] + new_header + src[pick.end :]

    # Debug marker（方便你 diff / grep）
    # 注意：不改变 cond/inc 文本
    open(out_path, "w", encoding="utf-8").write(mutated)
