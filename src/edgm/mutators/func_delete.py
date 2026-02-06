from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set

from clang.cindex import Cursor, CursorKind

from edgm.ast.ast_utils import parse_c_file, _is_from_main_file


def _collect_funcs_and_calls(src_path: Path) -> tuple[Dict[str, Cursor], Set[str]]:
    """
    收集：
      - funcs: 所有来自主文件、且有定义的函数（名字 -> Cursor）
      - called: 所有来自主文件的调用中的函数名集合
    """
    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    funcs: Dict[str, Cursor] = {}
    called: Set[str] = set()

    def visit(node: Cursor):
        # 递归展开所有节点，但只把主文件里的记到我们自己的集合
        if not _is_from_main_file(node, src_path):
            for child in node.get_children():
                visit(child)
            return

        kind = node.kind

        # 函数定义
        if kind == CursorKind.FUNCTION_DECL and node.is_definition():
            name = node.spelling
            if name:  # 排除掉一些奇怪的匿名情况
                funcs[name] = node

        # 函数调用
        elif kind == CursorKind.CALL_EXPR:
            callee = node.spelling
            if not callee:
                # 有时候 displayname 里是 "foo(int, int)" 这种
                disp = node.displayname
                if disp:
                    callee = disp.split("(", 1)[0].strip()
            if callee:
                called.add(callee)

        for child in node.get_children():
            visit(child)

    visit(root)
    return funcs, called


def _pick_function_to_delete(funcs: Dict[str, Cursor], called: Set[str]) -> Optional[Cursor]:
    """
    从函数里挑一个要删的：
      - 优先删“被调用过”的非 main 函数
      - 如果一个都没有，再退一步删一个非 main 函数
      - 再不行就放弃
    """
    # 1) 被调用过的非 main
    for name, cur in funcs.items():
        if name == "main":
            continue
        if name in called:
            return cur

    # 2) 任意一个非 main 函数
    for name, cur in funcs.items():
        if name != "main":
            return cur

    # 3) 实在没得删
    return None


def _delete_node_from_source(src_code: str, node: Cursor) -> str:
    """
    根据 node.extent 把对应源码片段删掉，返回新的源码字符串。
    """
    extent = node.extent
    start = extent.start
    end = extent.end

    if start.file is None or end.file is None:
        raise ValueError("node extent has no file information")

    lines = src_code.splitlines(keepends=True)

    # Clang 的行列从 1 开始，这里转成 Python 下标（0 开始）
    s_line = start.line - 1
    s_col = start.column - 1
    e_line = end.line - 1
    e_col = end.column - 1

    if s_line == e_line:
        lines[s_line] = lines[s_line][:s_col] + lines[e_line][e_col:]
    else:
        lines[s_line] = lines[s_line][:s_col]
        lines[e_line] = lines[e_line][e_col:]
        for i in range(s_line + 1, e_line):
            lines[i] = ""

    return "".join(lines)


def delete_called_function(src: str, dst: str) -> bool:
    """
    在 src 里删掉一个“被调用过的函数定义”，把结果写到 dst。

    返回值：
      - True: 成功删掉了一个函数定义
      - False: 没找到合适的函数
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    funcs, called = _collect_funcs_and_calls(src_path)
    target = _pick_function_to_delete(funcs, called)
    if target is None:
        return False

    with src_path.open("r", encoding="utf-8") as f:
        code = f.read()

    new_code = _delete_node_from_source(code, target)

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with dst_path.open("w", encoding="utf-8") as f:
        f.write(new_code)

    return True
