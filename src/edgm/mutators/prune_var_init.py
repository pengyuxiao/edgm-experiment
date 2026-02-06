# src/edgm/mutators/prune_var_init.py
#
# P10: 删除一个“局部变量”的初始化部分：
#   int x = 1;        -> int x ;
#   int a = 1, b = 2; -> int a, b = 2;
#
# 期望：
#   - 编译时产生使用未初始化变量的 warning（配合 -Werror 可升格为 error）
#   - 或者产生其它相关诊断（取决于后续优化/使用方式）

from __future__ import annotations

import random
from pathlib import Path
from typing import List, Tuple

from clang.cindex import Cursor, CursorKind, SourceLocation

from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列, 1-based) 转成字符串 offset（0-based）。
    """
    lines = text.splitlines(keepends=True)
    if line < 1:
        return 0
    if line > len(lines):
        return len(text)

    offset = 0
    for i in range(line - 1):
        offset += len(lines[i])
    offset += (col - 1)
    return offset


def _location_to_offset(loc: SourceLocation, text: str) -> int:
    """
    将 SourceLocation 映射到 offset；优先用 loc.offset，没有的话退回 (line, col)。
    """
    if hasattr(loc, "offset") and getattr(loc, "offset") is not None:
        return loc.offset  # type: ignore[attr-defined]
    return _loc_to_offset(text, loc.line, loc.column)


def _is_local_vardecl(node: Cursor, main_path: Path) -> bool:
    """
    判断一个 VarDecl 是否是“主文件里的局部变量”：
      - kind 是 VAR_DECL
      - 来自主文件
      - 祖先链上有 FunctionDecl（即在函数体内部）
    """
    if node.kind != CursorKind.VAR_DECL:
        return False

    if not _is_from_main_file(node, main_path):
        return False

    # 向上爬，看看是不是函数体里的局部变量
    parent = node.semantic_parent or node.lexical_parent
    while parent is not None and parent.kind != CursorKind.TRANSLATION_UNIT:
        if parent.kind == CursorKind.FUNCTION_DECL:
            return True
        parent = parent.semantic_parent or parent.lexical_parent

    return False


def _collect_candidate_ranges(
    root: Cursor, main_path: Path, src_text: str
) -> List[Tuple[int, int]]:
    """
    收集所有“可以删初始化”的局部 VarDecl 的 [eq_offset, cut_offset)：

      - 局部 VarDecl（在函数体内）
      - 来自主文件
      - 在其 extent 文本里能找到 '=' 号

    对于一个 VarDecl 覆盖的源码片段 segment：
      - 找到第一个 '=' 的位置 eq_rel
      - 再往后找最近的 ',' 或 ';'，作为 cut_rel
      - 如果没找到 ',' 或 ';'，就用该 VarDecl 的 extent 结束位置 end_off 作为 cut
      - 最终删除源码中 [eq_off, cut_off) 区间
    """
    ranges: List[Tuple[int, int]] = []
    local_total = 0

    def visit(node: Cursor) -> None:
        nonlocal local_total

        if _is_local_vardecl(node, main_path):
            local_total += 1

            start_off = _location_to_offset(node.extent.start, src_text)
            end_off = _location_to_offset(node.extent.end, src_text)

            if start_off < 0 or end_off <= start_off or end_off > len(src_text):
                # 防御性检查
                pass
            else:
                segment = src_text[start_off:end_off]
                eq_rel = segment.find("=")
                if eq_rel != -1:
                    eq_off = start_off + eq_rel

                    # 在 VarDecl 覆盖的这段里，找最近的 ',' 或 ';'
                    cut_rel = None
                    for ch in (",", ";"):
                        idx = segment.find(ch, eq_rel + 1)
                        if idx != -1 and (cut_rel is None or idx < cut_rel):
                            cut_rel = idx

                    if cut_rel is None:
                        # 没有逗号/分号，说明 extent 可能只到初始化表达式末尾；
                        # 那就删到 extent 结束位置。
                        cut_off = end_off
                    else:
                        cut_off = start_off + cut_rel

                    # 避免产生空区间
                    if cut_off > eq_off:
                        ranges.append((eq_off, cut_off))

        for ch in node.get_children():
            visit(ch)

    visit(root)

    print(f"[debug] local VarDecl total: {local_total}")
    print(f"[debug] local VarDecl with init (text) count: {len(ranges)}")

    return ranges


def prune_var_init(input_path: str, output_path: str) -> bool:
    """
    P10 核心接口：
      - 解析 input_path
      - 随机选一个带初始化的“局部 VarDecl”
      - 删除其中一个初始化片段（从 '=' 一直到 ',' 或 ';' 之前，或直到 VarDecl extent 末尾）
      - 写到 output_path
      - 成功变异返回 True，否则返回 False
    """
    in_path = Path(input_path).resolve()
    out_path = Path(output_path).resolve()

    src_text = _read_file(in_path)

    tu = parse_c_file(in_path)
    root = getattr(tu, "cursor", tu)

    # 1. 收集候选初始化区间
    candidates = _collect_candidate_ranges(root, in_path, src_text)
    if not candidates:
        print("[结果] 未找到合适的局部变量进行初始化删除，未变异。")
        return False

    # 2. 随机挑一个区间进行变异
    eq_off, cut_off = random.choice(candidates)

    mutated = src_text[:eq_off] + src_text[cut_off:]

    _write_file(out_path, mutated)
    print(f"[结果] 已生成变异文件: {out_path}")
    return True
