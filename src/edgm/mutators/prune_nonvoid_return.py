# src/edgm/mutators/prune_nonvoid_return.py
#
# P9: 在非 void 函数中删除一个 return 语句
# 典型诊断：control reaches end of non-void function 等

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


def _collect_candidate_returns(
    tu: cindex.TranslationUnit, main_path: Path
) -> List[Tuple[cindex.Cursor, cindex.Cursor]]:
    """
    收集所有 (函数节点, ReturnStmt 节点) 候选对：
    - 函数必须：非 void，且是定义
    - ReturnStmt 必须在主源码文件里
    """
    main_path_str = str(main_path)
    pairs: List[Tuple[cindex.Cursor, cindex.Cursor]] = []

    def visit_func(func: cindex.Cursor) -> None:
        # 只考虑函数定义
        if func.kind != cindex.CursorKind.FUNCTION_DECL:
            return
        if not func.is_definition():
            return

        # 非 void 函数
        try:
            result_type = func.result_type
        except Exception:
            return

        from clang.cindex import TypeKind

        if result_type.kind == TypeKind.VOID:
            return

        # 在该函数体内部找 ReturnStmt
        returns: List[cindex.Cursor] = []

        def walk(node: cindex.Cursor) -> None:
            for child in node.get_children():
                if child.kind == cindex.CursorKind.RETURN_STMT:
                    loc = child.location
                    if loc.file is not None and loc.file.name == main_path_str:
                        returns.append(child)
                walk(child)

        walk(func)

        for r in returns:
            pairs.append((func, r))

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.FUNCTION_DECL:
            visit_func(node)
        for ch in node.get_children():
            visit(ch)

    visit(tu.cursor)
    return pairs


def prune_nonvoid_return(input_path: str, output_path: str) -> bool:
    """
    P9 核心接口：
    - 读取 input_path
    - 随机挑一个非 void 函数中的 ReturnStmt
    - 删掉这一条 return 语句
    - 写入 output_path
    - 返回是否成功进行了变异
    """
    in_path = Path(input_path)
    out_path = Path(output_path)

    src = _read_file(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    candidates = _collect_candidate_returns(tu, in_path)
    if not candidates:
        return False

    # ✅ 随机选择一个 return，而不是默认第一个
    _func, ret_stmt = random.choice(candidates)

    start = ret_stmt.extent.start
    end = ret_stmt.extent.end

    start_off = _location_to_offset(start, src)
    end_off = _location_to_offset(end, src)

    mutated = src[:start_off] + src[end_off:]

    _write_file(out_path, mutated)
    return True
