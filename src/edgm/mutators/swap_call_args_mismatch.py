# src/edgm/mutators/swap_call_args_mismatch.py
#
# I13: CallExpr —— 交换两个类型明显不同的实参，
#      制造参数类型不匹配的错误。
#
# 做法：
#   - 用 libclang 遍历 AST，收集主文件中的所有 CallExpr；
#   - 对每个调用，获取所有实参 Cursor；
#   - 针对每个调用，找出“静态类型类别明显不同”的两个实参
#       （例如一个是指针，一个是整型 / struct）；
#   - 在源码里将这两个实参的文本片段互换位置；
#   - 写出变异后的文件。
#
# 典型触发诊断：
#   - passing 'int' to parameter of type 'int *'
#   - incompatible integer to pointer conversion
#   - incompatible pointer to integer conversion
#
# 注意：
#   - 只做字符串层面的交换，不改 AST；
#   - 随机选一个调用 + 一对参数，而不是固定选第一个；
#   - 不在本文件里编译，gcc/clang 由外部脚本控制。

from __future__ import annotations

import random
from pathlib import Path
from typing import List, Tuple

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


def _type_category(t: cindex.Type) -> str:
    """
    将类型粗略分成几类：
      - scalar : 各种整型/枚举
      - pointer: 指针
      - record : struct/union
      - other  : 其他（float、数组、函数、…）
    """
    if t is None:
        return "other"

    t = t.get_canonical()
    TK = cindex.TypeKind
    k = t.kind

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
        return "scalar"

    if k == TK.POINTER:
        return "pointer"

    if k == TK.RECORD:
        return "record"

    return "other"


def _collect_call_arg_pairs(
    tu: cindex.TranslationUnit, main_path: Path
) -> List[Tuple[cindex.Cursor, cindex.Cursor, cindex.Cursor]]:
    """
    收集“可以交换的实参对”。

    返回若干三元组 (call_cursor, arg_i, arg_j)，其中：
      - call_cursor.kind == CALL_EXPR 且在主文件中；
      - arg_i、arg_j 都在主文件中；
      - 它们的静态类型类别明显不同（指针 vs 整型 / struct 等）。
    """
    pairs: List[Tuple[cindex.Cursor, cindex.Cursor, cindex.Cursor]] = []

    for call in _walk(tu.cursor):
        if call.kind != cindex.CursorKind.CALL_EXPR:
            continue

        if not _is_from_main_file(call, main_path):
            continue

        # 获取实参列表
        args = list(call.get_arguments())
        if len(args) < 2:
            continue

        # 可选：跳过一些常见的变参函数名（比如 printf 等），
        # 但 csmith 种子一般不会用到，这里就不特判了。

        # 枚举所有 i < j 的参数对
        for i in range(len(args)):
            for j in range(i + 1, len(args)):
                a = args[i]
                b = args[j]

                if not _is_from_main_file(a, main_path):
                    continue
                if not _is_from_main_file(b, main_path):
                    continue

                cat_a = _type_category(a.type)
                cat_b = _type_category(b.type)

                # 要求类别不同，并且都不是 "other"，
                # 这样更有可能触发明显的类型不匹配。
                if cat_a == cat_b:
                    continue
                if "other" in (cat_a, cat_b):
                    continue

                print(
                    f"[debug] I13: call at {call.location.file}:{call.location.line}, "
                    f"arg pair ({i},{j}), cat=({cat_a},{cat_b})"
                )
                pairs.append((call, a, b))

    print(f"[debug] I13: candidate call-arg pairs count = {len(pairs)}")
    return pairs


def swap_call_args_mismatch(input_path: str, output_path: str) -> bool:
    """
    I13 主入口：对 input_path 进行变异，输出到 output_path。

    变异方式：
      - 在主文件中的某个函数调用里，
        找到一对“类型类别明显不同”的实参；
      - 在源码上直接交换它们的文本片段；
      - 使该函数调用在语义分析阶段产生“参数类型不匹配”错误。

    返回：
      True  - 成功在某处交换了两个实参
      False - 未找到合适的调用，未变异
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

    pairs = _collect_call_arg_pairs(tu, in_path)
    if not pairs:
        print("[debug] I13: no suitable call-arg pairs, skip mutation")
        return False

    # 随机选一个调用里的一个参数对
    call, arg_a, arg_b = random.choice(pairs)

    # 计算源码 offset
    start_a = arg_a.extent.start
    end_a = arg_a.extent.end
    start_b = arg_b.extent.start
    end_b = arg_b.extent.end

    off_a_start = _location_to_offset(start_a, src)
    off_a_end = _location_to_offset(end_a, src)
    off_b_start = _location_to_offset(start_b, src)
    off_b_end = _location_to_offset(end_b, src)

    # 确保范围合法
    if not (0 <= off_a_start <= off_a_end <= len(src)):
        print("[debug] I13: bad offset for arg A, skip")
        return False
    if not (0 <= off_b_start <= off_b_end <= len(src)):
        print("[debug] I13: bad offset for arg B, skip")
        return False

    # 保证 a 在前 b 在后，方便字符串重组
    if off_a_start > off_b_start:
        (off_a_start, off_a_end, off_b_start, off_b_end) = (
            off_b_start,
            off_b_end,
            off_a_start,
            off_a_end,
        )

    text_a = src[off_a_start:off_a_end]
    text_b = src[off_b_start:off_b_end]

    print(
        f"[debug] I13: swap args in call at {call.location.file}:"
        f"{call.location.line}, "
        f"A='{text_a.strip()}', B='{text_b.strip()}'"
    )

    # 重建源码： [0, a_start) + B + [a_end, b_start) + A + [b_end, end)
    mutated = (
        src[:off_a_start]
        + text_b
        + src[off_a_end:off_b_start]
        + text_a
        + src[off_b_end:]
    )

    _write_file(out_path, mutated)
    print(f"[结果] 已生成 I13 变异文件: {out_path}")
    return True


if __name__ == "__main__":
    # 支持直接命令行调用：
    #   python3 src/edgm/mutators/swap_call_args_mismatch.py in.c out.c
    import argparse

    ap = argparse.ArgumentParser(
        description="I13: 交换类型明显不同的函数实参，制造参数类型不匹配"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = swap_call_args_mismatch(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 I13 变异（可能未找到合适的调用）")
