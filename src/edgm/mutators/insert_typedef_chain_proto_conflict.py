# -*- coding: utf-8 -*-
"""
I19: typedef-chain + prototype/decl kind conflict (advanced)
- Randomly pick an existing function prototype: `static ... name(...);`
- Insert a confusing typedef/macro/typeof block and a conflicting re-declaration.
- Strategies:
  * RETURNS_FUNCTION: declare `name` as returning a function type (illegal in C)
  * KIND_MISMATCH_VAR: redeclare `name` as a variable of function-pointer type
  * PARAM_MISMATCH: redeclare `name` with a different parameter list, hidden by typedef/typeof/macro
"""

from __future__ import annotations

import os
import re
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple


_PROTO_RE = re.compile(
    r"""^(?P<indent>\s*)static\s+(?P<rtype>[^;{()]+?)\s+  # return type-ish
        (?P<name>[A-Za-z_]\w*)\s*                         # function name
        \((?P<params>[^;{}]*)\)\s*;                       # params
    """,
    re.VERBOSE,
)


@dataclass
class Proto:
    line_idx: int
    indent: str
    rtype: str
    name: str
    params: str


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _write_text(path: str, s: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(s)


def _find_prototypes(lines: List[str]) -> List[Proto]:
    protos: List[Proto] = []
    for i, line in enumerate(lines):
        m = _PROTO_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        # 跳过一些明显不是我们想要的（你也可按需加黑名单）
        if name.startswith("edgm_"):
            continue
        protos.append(
            Proto(
                line_idx=i,
                indent=m.group("indent") or "",
                rtype=(m.group("rtype") or "").strip(),
                name=name,
                params=(m.group("params") or "").strip(),
            )
        )
    return protos


def _rand_tag(rng: random.Random) -> int:
    return rng.randint(10000, 99999)


def _choose_strategy(rng: random.Random) -> str:
    # 权重：更“高级”的优先
    strategies = [
        ("RETURNS_FUNCTION", 0.45),
        ("KIND_MISMATCH_VAR", 0.35),
        ("PARAM_MISMATCH", 0.20),
    ]
    x = rng.random()
    acc = 0.0
    for s, w in strategies:
        acc += w
        if x <= acc:
            return s
    return strategies[-1][0]


def _emit_injection(target: Proto, tag: int, strategy: str, rng: random.Random) -> str:
    # 让 typedef 链看起来“像是有用的”
    # 注：__typeof__ 是 GCC/Clang 扩展；C99 下可用（只要编译器支持扩展）
    # 注：把冲突声明用宏包一下，尽量让报错位置更分散/更不稳定
    base_u = f"edgm_i19_u32_{tag}"
    idx_t = f"edgm_i19_idx_{tag}"
    cb_t = f"edgm_i19_cb_{tag}"
    fp_t = f"edgm_i19_fp_{tag}"
    fnret_t = f"edgm_i19_fnret_{tag}"

    # 生成几种“参数扰动”的候选，让冲突更不直接
    # 目标：不要直接写 int8_t，而是让类型经 typedef/typeof 绕一圈
    param_forms = [
        f"{idx_t} n",
        f"{cb_t} cb",
        f"{idx_t} n, {idx_t} m",
        f"{idx_t} n, ...",
    ]
    chosen_param = rng.choice(param_forms)

    # 让“返回类型”也绕一圈（很多 Csmith 里都是 uint32_t；这里不强行改返回，主要靠非法返回函数/符号种类冲突/参数冲突）
    # 但可以用 __typeof__(0u) 进一步“像真的”
    ret_expr = "__typeof__(0u)"
    if target.rtype:
        # 尽量沿用原型返回类型的“表面”
        # 但加一层 typeof/macro 会让诊断 anchor 更飘
        ret_expr = f"__typeof__(({target.rtype.strip()})0)"

    # 统一宏名
    fn_macro = f"EDGM_I19_FN_{tag}"
    wrap_macro = f"EDGM_I19_WRAP_{tag}"

    block = []
    block.append(f"/* edgm I19-adv: typedef-chain + {strategy} (tag={tag}) */")
    block.append(f"typedef unsigned int {base_u}_t0;")
    block.append(f"typedef {base_u}_t0 {base_u}_t1;")
    block.append(f"typedef {base_u}_t1 {base_u}_t2;")
    block.append(f"typedef {base_u}_t2 (*{cb_t})({base_u}_t1, {base_u}_t2);")
    block.append("")
    block.append("#if defined(__GNUC__) || defined(__clang__)")
    block.append(f"typedef __typeof__(0u) {idx_t};")
    block.append("#else")
    block.append(f"typedef unsigned {idx_t};")
    block.append("#endif")
    block.append("")
    block.append(f"#define {wrap_macro}(T) T")
    block.append(f"#define {fn_macro}(X) X")
    block.append("")

    if strategy == "RETURNS_FUNCTION":
        # 关键：typedef 一个“函数类型”，然后让目标函数返回它 -> function returning function（非法）
        # 用宏/typeof 把它绕一下：typedef __typeof__(...) base; typedef base fnret(void);
        block.append(f"typedef {ret_expr} {base_u}_ret;")
        block.append(f"typedef {wrap_macro}({base_u}_ret) {fnret_t}(void);")
        # 让 name 看起来像“重声明”，但本质是非法返回类型
        # 原型：static <rtype> name(<params>);
        # 注入：static fnret_t name(idx_t n);
        block.append("#ifdef __cplusplus")
        block.append('extern "C" {')
        block.append("#endif")
        block.append(f"static {fnret_t} {fn_macro}({target.name})({chosen_param});")
        block.append("#ifdef __cplusplus")
        block.append("} /* extern \"C\" */")
        block.append("#endif")

    elif strategy == "KIND_MISMATCH_VAR":
        # 把函数名重声明成“变量”（函数指针变量），会触发 different kind of symbol / redeclared as different kind
        # 更容易造成诊断措辞差异
        block.append(f"typedef {ret_expr} (*{fp_t})({chosen_param});")
        block.append(f"static {fp_t} {fn_macro}({target.name});")

    else:  # PARAM_MISMATCH
        # 参数不匹配，但用 typedef/typeof/宏链掩盖
        # 返回类型尽量保持“像”一致
        block.append(f"static {wrap_macro}({ret_expr}) {fn_macro}({target.name})({chosen_param});")

    block.append("")
    block.append(f"#undef {fn_macro}")
    block.append(f"#undef {wrap_macro}")
    block.append("/* edgm I19-adv end */")

    return "\n".join(block) + "\n"


def insert_typedef_chain_proto_conflict(
    in_path: str,
    out_path: str,
    seed: Optional[int] = None,
) -> bool:
    """
    Returns True if mutated, False if skipped (no suitable prototype found).
    """
    src = _read_text(in_path)
    lines = src.splitlines(True)  # keep newline

    rng = random.Random(seed) if seed is not None else random.Random()

    protos = _find_prototypes(lines)
    if not protos:
        _write_text(out_path, src)
        return False

    target = rng.choice(protos)
    tag = _rand_tag(rng)
    strategy = _choose_strategy(rng)

    inj = _emit_injection(target, tag, strategy, rng)

    # Insert AFTER the chosen prototype line
    insert_at = target.line_idx + 1
    lines.insert(insert_at, inj)

    mutated = "".join(lines)
    _write_text(out_path, mutated)
    return True
