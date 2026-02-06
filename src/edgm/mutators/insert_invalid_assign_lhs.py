# src/edgm/mutators/insert_invalid_assign_lhs.py
#
# I12-adv: BinaryOperator（赋值）——将赋值左侧替换为更“花”的非左值表达式，
#          制造各种 "lvalue required as left operand of assignment"
#          / "expression is not assignable" 之类的错误。
#
# 做法：
#   - 用 libclang 遍历 AST，收集主文件中的所有简单赋值表达式的 LHS；
#   - 对 LHS 的类型进行一个粗分类（标量整数 / 指针 / 其他）；
#   - 从一个“非左值形态库”里随机选一个变换：
#       标量整数:
#         (lhs + 1)
#         (lhs ? lhs : lhs)
#         ((lhs), 0)
#         (double)(lhs)
#       指针:
#         (lhs + 1)
#         (lhs == 0)
#         (lhs && lhs)
#         ((lhs), (void*)0)
#       其他:
#         (lhs + 0)
#         ((lhs), 0)
#   - 替换源码中对应片段，输出到新文件。
#
# 特点：
#   - 一个 mutator，多种非左值形态，容易让不同编译器在诊断文本、
#     标注位置、是否联动其他 warning 上出现差异。
#
# 注意：
#   - 不在本文件或脚本里调用 gcc/clang，由外部脚本自己编译验证。

from __future__ import annotations

import random
from pathlib import Path
from typing import List

from clang import cindex


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSMITH_RUNTIME_DIR = PROJECT_ROOT / "src" / "csmithedge" / "csmith" / "runtime"


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _location_to_offset(loc: cindex.SourceLocation, text: str) -> int:
    """
    将 (line, column) 转成源码里的 offset。
    line/column 从 1 开始，offset 从 0 开始。
    """
    line = loc.line - 1
    col = loc.column - 1
    if line < 0:
        return 0

    lines = text.splitlines(keepends=True)
    if line >= len(lines):
        return len(text)

    return sum(len(l) for l in lines[:line]) + min(col, len(lines[line]))


def _is_from_main_file(cur: cindex.Cursor, main_path: Path) -> bool:
    loc = cur.location
    if loc is None or loc.file is None:
        return False

    try:
        return Path(loc.file.name).resolve() == main_path
    except Exception:
        return False


def _walk(node: cindex.Cursor):
    """简单 DFS 遍历。"""
    yield node
    for c in node.get_children():
        yield from _walk(c)


def _is_simple_assign(binop: cindex.Cursor) -> bool:
    """
    判断一个 BinaryOperator 是否是简单赋值表达式：
      - kind == BINARY_OPERATOR
      - token 序列中存在 **单独的** '=' token
      - 排除 '==', '+=', '-=' 等运算。
    """
    if binop.kind != cindex.CursorKind.BINARY_OPERATOR:
        return False

    has_eq = False
    for tok in binop.get_tokens():
        spell = tok.spelling
        if spell == "=":
            has_eq = True
            break
        # 遇到 '==', '+=', '*=' 等复合形式就直接判定不是简单赋值
        if "=" in spell and spell != "=":
            return False

    return has_eq


def _classify_type_for_lhs(lhs: cindex.Cursor) -> str:
    """
    粗略类型分类：
      - scalar_int: 各种整型/枚举
      - pointer   : 指针
      - other     : 其他（float、struct、union、array、函数等）
    """
    t = lhs.type
    if t is None:
        return "other"

    canonical = t.get_canonical()
    k = canonical.kind
    TK = cindex.TypeKind

    if k in (
        TK.BOOL,
        TK.CHAR_U,
        TK.UCHAR,
        TK.CHAR_S,
        TK.SCHAR,
        TK.SHORT,
        TK.USHORT,
        TK.INT,
        TK.UINT,
        TK.LONG,
        TK.ULONG,
        TK.LONGLONG,
        TK.ULONGLONG,
        TK.ENUM,
    ):
        return "scalar_int"

    if k == TK.POINTER:
        return "pointer"

    return "other"


def _make_nonlvalue_expr(lhs_src: str, kind: str) -> str:
    """
    根据类型类别，构造一个“明显是非左值”的表达式。
    所有返回的表达式都加一层括号，避免优先级干扰原语境。
    """
    # 去掉左右空白，避免变成 "(   x   + 1)" 这种太丑
    core = lhs_src.strip() or lhs_src

    candidates: List[str] = []

    if kind == "scalar_int":
        candidates = [
            f"({core} + 1)",
            f"({core} ? {core} : {core})",
            f"(({core}), 0)",
            f"(double)({core})",
        ]
    elif kind == "pointer":
        candidates = [
            f"({core} + 1)",          # 指针加法，结果是 rvalue
            f"({core} == 0)",         # 比较结果是 int rvalue
            f"({core} && {core})",    # 逻辑与，int rvalue
            f"(({core}), (void*)0)",  # 逗号表达式，结果是 (void*)0 rvalue
        ]
    else:
        # 其他类型就保守一点，但依旧非左值
        candidates = [
            f"({core} + 0)",
            f"(({core}), 0)",
        ]

    choice = random.choice(candidates)
    print(f"[debug] I12: choose non-lvalue form (kind={kind}): {choice}")
    return choice


def _collect_assign_lhs_candidates(
    tu: cindex.TranslationUnit, main_path: Path
) -> List[cindex.Cursor]:
    """
    收集所有“简单赋值表达式”的左侧表达式 Cursor。
    """
    cands: List[cindex.Cursor] = []

    for cur in _walk(tu.cursor):
        if cur.kind != cindex.CursorKind.BINARY_OPERATOR:
            continue

        if not _is_from_main_file(cur, main_path):
            continue

        if not _is_simple_assign(cur):
            continue

        # BinaryOperator 的第一个 child 通常是 LHS
        children = list(cur.get_children())
        if len(children) < 2:
            continue

        lhs = children[0]

        # 粗滤一些完全不想动的节点也可以，但这里我们宽松一点；
        # 真正“变成非左值”的逻辑放在 _make_nonlvalue_expr 里。
        cands.append(lhs)

    print(f"[debug] I12: candidate assign LHS count = {len(cands)}")
    return cands


def insert_invalid_assign_lhs(input_path: str, output_path: str) -> bool:
    """
    I12-adv 主入口：对 input_path 进行变异，输出到 output_path。

    变异方式：
      - 从若干赋值语句中随机挑一个 LHS；
      - 按其静态类型分类，从“非左值形态库”中选一个表达式替换；
      - 使该赋值语句在语义分析阶段必然报错，而不是 runtime UB。

    返回：
      True  - 成功在某处进行变异
      False - 未找到合适位置，未变异
    """
    in_path = Path(input_path).resolve()
    out_path = Path(output_path).resolve()

    if not in_path.exists():
        raise FileNotFoundError(f"源文件不存在: {in_path}")

    src = _read_file(in_path)

    index = cindex.Index.create()
    args = [
        f"-I{CSMITH_RUNTIME_DIR}",
        f"-I{CSMITH_RUNTIME_DIR.parent}",
        "-std=c99",
    ]
    tu = index.parse(str(in_path), args=args)

    # AST 诊断（仅调试用）
    for diag in tu.diagnostics:
        print(f"[AST diag] {diag}")

    cands = _collect_assign_lhs_candidates(tu, in_path)
    if not cands:
        print("[debug] I12: no suitable assign LHS, skip mutation")
        return False

    target = random.choice(cands)
    kind = _classify_type_for_lhs(target)

    start = target.extent.start
    end = target.extent.end

    off_start = _location_to_offset(start, src)
    off_end = _location_to_offset(end, src)

    if not (0 <= off_start <= off_end <= len(src)):
        print(
            f"[debug] I12: bad offset range for LHS at "
            f"{start.file}:{start.line}:{start.column} -> {off_start}..{off_end}"
        )
        return False

    old_text = src[off_start:off_end]
    print(
        f"[debug] I12: choose LHS expr at "
        f"{start.file}:{start.line}:{start.column}, text='{old_text.strip()}', kind={kind}"
    )

    new_text = _make_nonlvalue_expr(old_text, kind)
    mutated = src[:off_start] + new_text + src[off_end:]

    _write_file(out_path, mutated)
    print(f"[结果] 已生成 I12 变异文件: {out_path}")
    return True


if __name__ == "__main__":
    # 支持直接命令行调用：python3 insert_invalid_assign_lhs.py in.c out.c
    import argparse

    ap = argparse.ArgumentParser(
        description="I12-adv: 将赋值左侧替换为更复杂的非左值表达式"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_invalid_assign_lhs(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 I12 变异（可能未找到合适的赋值语句）")
