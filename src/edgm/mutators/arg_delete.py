from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import random
from clang.cindex import Cursor, CursorKind

from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file


def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列) 转成字符串 offset。
    libclang 给的是 1-based 行列，我们要转成 Python 字符串的 0-based index。
    """
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        raise ValueError(f"line 超出范围: {line}, 文件总行数: {len(lines)}")

    offset = 0
    for i in range(line - 1):
        offset += len(lines[i])
    offset += (col - 1)
    return offset


def _collect_call_candidates(src_path: Path) -> List[Tuple[Cursor, List[Cursor]]]:
    """
    收集所有“来自主文件”的 CallExpr，
    并且实参个数 >= 1，作为变异候选。
    返回列表元素：(call_node, [arg0, arg1, ...])。
    """
    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    cands: List[Tuple[Cursor, List[Cursor]]] = []

    def visit(node: Cursor):
        # 展开所有子节点，但只把主文件的节点当候选
        if not _is_from_main_file(node, src_path):
            for child in node.get_children():
                visit(child)
            return

        if node.kind == CursorKind.CALL_EXPR:
            args = list(node.get_arguments())
            if len(args) >= 1:
                # 确保所有实参也来自同一个源文件
                ok = True
                for a in args:
                    loc = a.extent.start
                    if loc.file is None:
                        ok = False
                        break
                    if Path(loc.file.name).name != src_path.name:
                        ok = False
                        break
                if ok:
                    cands.append((node, args))

        for child in node.get_children():
            visit(child)

    visit(root)
    return cands


def delete_call_arg(src: str, dst: str) -> bool:
    """
    P3：删除函数调用里的一个实参（随机挑一个调用点 + 随机挑一个实参）。

    - src: 源文件路径
    - dst: 变异后输出路径

    返回：
      True  = 成功删掉一个实参
      False = 没有找到可删的调用
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    candidates = _collect_call_candidates(src_path)
    if not candidates:
        return False

    # 随机选一个调用
    call_node, args = random.choice(candidates)
    # 在这个调用的实参列表里，再随机选一个实参索引
    idx = random.randrange(len(args))

    # 读取源码文本
    text = src_path.read_text(encoding="utf-8")

    # 计算需要删掉的源码范围（行列 -> offset）
    n = len(args)

    if n == 1:
        # 只有 1 个实参： f(x)  ->  f()
        s = args[0].extent.start
        e = args[0].extent.end
    else:
        if idx == 0:
            # 删第一个： f(a, b, c) -> f(b, c)
            s = args[0].extent.start
            e = args[1].extent.start
        elif idx == n - 1:
            # 删最后一个： f(a, b, c) -> f(a, b)
            s = args[n - 2].extent.end
            e = args[n - 1].extent.end
        else:
            # 删中间某个： f(a, b, c, d) -> 删 b 得 f(a, c, d)
            s = args[idx - 1].extent.end
            e = args[idx].extent.end

    # 文件安全检查
    if s.file is None or e.file is None:
        return False
    if Path(s.file.name).name != src_path.name:
        return False
    if Path(e.file.name).name != src_path.name:
        return False

    start_off = _loc_to_offset(text, s.line, s.column)
    end_off = _loc_to_offset(text, e.line, e.column)

    if not (0 <= start_off <= end_off <= len(text)):
        return False

    new_text = text[:start_off] + text[end_off:]

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(new_text, encoding="utf-8")

    return True
