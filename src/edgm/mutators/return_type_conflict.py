# src/edgm/mutators/return_type_conflict.py
#
# 通过修改“函数定义”的返回类型，让它与前面的原型产生冲突。
# 纯文本/正则实现，支持：
#   - 原型: static int32_t func(...);
#   - 定义: static int32_t func(...)\n{ ... }
#
from __future__ import annotations

import os
import re
import random
from dataclasses import dataclass
from typing import List, Dict, Optional


# 函数签名所在行的正则，不强制末尾一定有 { 或 ;
FUNC_PATTERN = re.compile(
    r"""
    ^(?P<indent>\s*)
    (?P<sc>(?:static|extern)\s+)?     # storage class: static/extern（可选）
    (?P<rtype>[A-Za-z_][\w\s\*]*?)    # 返回类型（可能包含 struct S0、指针等）
    \s+
    (?P<name>[A-Za-z_]\w*)            # 函数名
    \s*
    \(
        (?P<params>[^)]*)             # 参数列表（不跨行）
    \)
    \s*
    (?P<tail>[{;])?                   # 同行可能有 { 或 ;
    """,
    re.VERBOSE,
)


@dataclass
class FuncDecl:
    line_idx: int          # 签名所在行号（0-based）
    indent: str
    storage: str
    rtype: str
    name: str
    params: str
    tail: str              # 原始结尾字符（{ 或 ; 或空）
    kind: str              # "proto" or "def"


def _normalize_type(t: str) -> str:
    """粗糙地归一化类型字符串，用于比较“是否相同类型”."""
    t = t.replace("\t", " ")
    # 去掉 const / volatile
    t = re.sub(r"\bconst\b", " ", t)
    t = re.sub(r"\bvolatile\b", " ", t)
    # 折叠空白
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _pick_conflicting_return_type(old_type: str) -> Optional[str]:
    """
    根据旧的返回类型，挑一个“尽量不兼容”的新类型：
    - 整型 -> 各种 pointer / struct / float / double / 多级指针
    - 指针 -> 各种整型 / float / struct / void
    - struct / void 也有对应的骚操作映射
    """
    norm = _normalize_type(old_type)

    int_like = {
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "int",
        "unsigned int",
        "short",
        "unsigned short",
        "long",
        "unsigned long",
        "char",
        "signed char",
        "unsigned char",
    }

    float_like = {"float", "double", "long double"}

    is_void = norm == "void"
    is_struct = norm.startswith("struct ")
    is_pointer = "*" in norm and not is_void

    candidates = set()

    if norm in int_like:
        # 标量整数类型：往指针 / struct / 浮点狂暴跳转
        candidates.update(
            {
                "int32_t *",
                "uint32_t *",
                "void *",
                "struct S0 *",
                "struct S0",
                "double",
                "float",
                "int32_t **",
                "uint8_t *",
                "uint64_t",
            }
        )
    elif norm in float_like:
        # 浮点：换成各种整数 / 指针 / struct
        candidates.update(
            {
                "int32_t",
                "uint32_t",
                "int64_t",
                "uint64_t",
                "int32_t *",
                "void *",
                "struct S0 *",
                "struct S0",
            }
        )
    elif is_pointer:
        # 指针：直接打回去整型/浮点/struct/void，让表达式各种错
        candidates.update(
            {
                "int32_t",
                "uint32_t",
                "int8_t",
                "uint8_t",
                "float",
                "double",
                "struct S0",
                "void",
            }
        )
        # 再加一些“错类型指针”
        candidates.update(
            {
                "void *",
                "struct S0 *",
                "uint8_t **",
            }
        )
    elif is_struct:
        # struct：直接变成标量 / 指针
        candidates.update(
            {
                "int32_t",
                "uint32_t",
                "float",
                "double",
                "void *",
                "int32_t *",
                "struct S0 *",
            }
        )
    elif is_void:
        # void：给它塞上各种有值类型
        candidates.update(
            {
                "int32_t",
                "uint32_t",
                "struct S0 *",
                "void *",
                "double",
            }
        )
    else:
        # 不认识的奇怪类型：给一堆乱七八糟的
        candidates.update(
            {
                "int32_t *",
                "uint32_t *",
                "void *",
                "struct S0",
                "struct S0 *",
                "double",
            }
        )

    # 去掉与原类型归一化后相同的候选
    norm_candidates = [c for c in candidates if _normalize_type(c) != norm]

    if not norm_candidates:
        return None

    new_type = random.choice(norm_candidates)
    print(f"[debug] pick conflicting return type: old='{old_type}' -> new='{new_type}'")
    return new_type


def _collect_func_decls(lines: List[str]) -> List[FuncDecl]:
    """
    用正则在文本里搜所有函数签名，并区分：
      - 原型（proto）：以 ; 结束，或签名后不是 {，也没有下一行 {）
      - 定义（def）：同一行有 {，或者下一行第一个非空行是 {。
    """
    decls: List[FuncDecl] = []

    n = len(lines)
    for idx, line in enumerate(lines):
        m = FUNC_PATTERN.match(line)
        if not m:
            continue

        indent = m.group("indent") or ""
        storage = m.group("sc") or ""
        rtype = m.group("rtype").strip()
        name = m.group("name")
        params = m.group("params")
        tail = (m.group("tail") or "").strip()

        kind = "proto"

        if ";" in line and (tail == ";" or line.strip().endswith(";")):
            kind = "proto"
        else:
            # 看看这一行或下一行是不是函数定义的 {
            same_line_has_brace = "{" in line
            next_line_has_brace = False

            j = idx + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and re.match(r"^\s*{", lines[j]):
                next_line_has_brace = True

            if same_line_has_brace or next_line_has_brace:
                kind = "def"
            else:
                # 保守起见，当作原型
                kind = "proto"

        decls.append(
            FuncDecl(
                line_idx=idx,
                indent=indent,
                storage=storage,
                rtype=rtype,
                name=name,
                params=params,
                tail=tail,
                kind=kind,
            )
        )

    return decls


def mutate_return_type_conflict(in_path: str, out_path: str) -> bool:
    # 读源文件
    with open(in_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    decls = _collect_func_decls(lines)
    print(f"[debug] total FUNCTION_DECL in seed file: {len(decls)}")

    # 按函数名分组，找“有原型 + 有定义”的函数
    by_name: Dict[str, List[FuncDecl]] = {}
    for d in decls:
        by_name.setdefault(d.name, []).append(d)

    candidates: List[FuncDecl] = []
    for name, group in by_name.items():
        has_proto = any(d.kind == "proto" for d in group)
        defs = [d for d in group if d.kind == "def"]
        if not (has_proto and defs):
            continue
        for d in defs:
            if _normalize_type(d.rtype) == "void":
                continue
            candidates.append(d)

    print(
        f"[debug] total candidate functions for return-type conflict in seed file: {len(candidates)}"
    )

    if not candidates:
        print("[debug] no candidate functions for return-type conflict in this seed file")
        return False

    # 随机挑一个候选函数（函数定义）
    target = random.choice(candidates)
    old_type = target.rtype
    new_type = _pick_conflicting_return_type(old_type)
    if not new_type:
        print("[debug] failed to find a conflicting new return type")
        return False

    print(
        f"[debug] mutate return type: func='{target.name}', old_type='{old_type}' -> new_type='{new_type}'"
    )

    # 在定义所在行上替换返回类型，只改定义，不动前面的原型
    line = lines[target.line_idx]

    # 精确替换： [indent][storage][rtype] name(
    pattern = re.compile(
        rf"""
        ^(?P<indent>\s*)
        (?P<sc>(?:static|extern)\s+)?      # storage class
        (?P<rtype>[A-Za-z_][\w\s\*]*?)     # 原返回类型
        (?P<space>\s+)
        (?P<name>{re.escape(target.name)}) # 函数名
        \s*\(
        """,
        re.VERBOSE,
    )

    m = pattern.match(line)
    if m:
        indent = m.group("indent") or ""
        sc = m.group("sc") or ""
        space = m.group("space") or " "
        prefix = indent + sc + new_type + space + target.name + "("
        new_line = prefix + line[m.end(0) :]
    else:
        # 兜底：用原始 rtype 做一次简单替换
        new_line = line.replace(old_type, new_type, 1)

    lines[target.line_idx] = new_line
    print(
        f"[debug] patched return type in definition line {target.line_idx + 1} for '{target.name}'"
    )

    # 写出到目标路径
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return True
