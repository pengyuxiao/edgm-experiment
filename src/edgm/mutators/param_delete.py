from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import random

from clang.cindex import Cursor, CursorKind

from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file


def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列) 转成字符串 offset。
    libclang 给的是 1-based line/column，我们要转成 Python 字符串的 0-based index。
    """
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        raise ValueError(f"line 超出范围: {line}, 文件总行数: {len(lines)}")
    offset = 0
    for i in range(line - 1):
        offset += len(lines[i])
    offset += (col - 1)
    return offset


def _collect_funcs_and_calls(root: Cursor, main_path: Path) -> Tuple[List[Cursor], Dict[str, Cursor], Dict[str, Set[str]]]:
    """
    遍历整棵 AST：
      - 收集来自主文件的函数定义（FUNCTION_DECL 且 is_definition）
      - 收集调用关系：caller_usr -> {callee_usr, ...}
    """
    functions: List[Cursor] = []
    func_by_usr: Dict[str, Cursor] = {}
    callees_by_usr: Dict[str, Set[str]] = {}

    current_func_usr: Optional[str] = None

    def visit(node: Cursor):
        nonlocal current_func_usr

        # 只展开主文件的节点，但还是要递归子节点
        if not _is_from_main_file(node, main_path):
            for c in node.get_children():
                visit(c)
            return

        old_func_usr = current_func_usr

        if node.kind == CursorKind.FUNCTION_DECL and node.is_definition():
            current_func_usr = node.get_usr()
            functions.append(node)
            func_by_usr[current_func_usr] = node

        if node.kind == CursorKind.CALL_EXPR and current_func_usr is not None:
            ref = node.referenced
            if ref is not None:
                callee_usr = ref.get_usr()
                callees_by_usr.setdefault(current_func_usr, set()).add(callee_usr)

        for c in node.get_children():
            visit(c)

        # 退出当前函数作用域
        current_func_usr = old_func_usr

    visit(root)
    return functions, func_by_usr, callees_by_usr


def _reachable_from_func1(func_by_usr: Dict[str, Cursor],
                          callees_by_usr: Dict[str, Set[str]]) -> Set[str]:
    """
    从 func_1 出发做一个很简单的「可达函数」分析：
      - 找到名字是 func_1 的函数定义
      - 沿着调用图做 BFS，得到所有从 func_1 可达的函数 USR
    """
    start_usr = None
    for usr, func in func_by_usr.items():
        if func.spelling == "func_1":
            start_usr = usr
            break

    if start_usr is None:
        # 理论上 Csmith 程序一定有 func_1，这里兜个底
        return set()

    reachable: Set[str] = set()
    worklist: List[str] = [start_usr]

    while worklist:
        u = worklist.pop()
        if u in reachable:
            continue
        reachable.add(u)
        for v in callees_by_usr.get(u, ()):
            if v not in reachable:
                worklist.append(v)

    # 把 func_1 自身也算进来了，所以 reachable 至少包含 func_1
    return reachable


def _pick_target_function(functions: List[Cursor],
                          reachable_usrs: Set[str]) -> Optional[Cursor]:
    """
    在「从 func_1 可达」的函数里，选一个有至少一个形参的函数，
    作为我们要删形参的目标。
    为了方便复现，这里目前选第一个满足条件的；你要随机可以改成 random.choice。
    """
    for f in functions:
        usr = f.get_usr()
        if usr not in reachable_usrs:
            continue
        params = list(f.get_arguments())
        if not params:
            continue
        if f.spelling == "main":
            continue
        return f
    return None


def _compute_delete_spans_for_func(text: str,
                                   root: Cursor,
                                   main_path: Path,
                                   target_usr: str,
                                   param_index: int) -> List[Tuple[int, int]]:
    """
    对于所有 USR == target_usr 的 FunctionDecl（包括前向声明和定义），
    计算出在源码里应该删除哪一段 [start_off, end_off) 来移除第 param_index 个形参。

    为了避免 offset 失效，后面会按 start_off 从大到小排序后再真正改文本。
    """
    spans: List[Tuple[int, int]] = []

    def visit(node: Cursor):
        if not _is_from_main_file(node, main_path):
            for c in node.get_children():
                visit(c)
            return

        if node.kind == CursorKind.FUNCTION_DECL and node.get_usr() == target_usr:
            params = list(node.get_arguments())
            if param_index >= len(params):
                # 有些声明可能参数数量不同，直接跳过
                pass
            else:
                param = params[param_index]
                start = param.extent.start
                end = param.extent.end

                s = _loc_to_offset(text, start.line, start.column)
                e = _loc_to_offset(text, end.line, end.column)

                # 根据在参数列表中的位置，决定是吃掉前面的逗号还是后面的逗号
                n_params = len(params)
                idx = param_index

                s_adj, e_adj = s, e

                if n_params == 1:
                    # 唯一一个参数，直接删这一段就行（括号还在）
                    pass
                elif idx == 0:
                    # 第一个参数但不是唯一：删掉后面的逗号和空白
                    i = e
                    # 跳过空白
                    while i < len(text) and text[i].isspace():
                        i += 1
                    if i < len(text) and text[i] == ',':
                        i += 1
                        while i < len(text) and text[i].isspace():
                            i += 1
                        e_adj = i
                elif idx == n_params - 1:
                    # 最后一个参数：吃掉前面的逗号和空白
                    i = s - 1
                    # 先往左跳过空白
                    while i >= 0 and text[i].isspace():
                        i -= 1
                    if i >= 0 and text[i] == ',':
                        j = i - 1
                        while j >= 0 and text[j].isspace():
                            j -= 1
                        s_adj = j + 1
                else:
                    # 中间参数：删掉后面的逗号和空白
                    i = e
                    while i < len(text) and text[i].isspace():
                        i += 1
                    if i < len(text) and text[i] == ',':
                        i += 1
                        while i < len(text) and text[i].isspace():
                            i += 1
                        e_adj = i

                spans.append((s_adj, e_adj))

        for c in node.get_children():
            visit(c)

    visit(root)
    return spans


def delete_func_param(src: str, dst: str) -> bool:
    """
    P2：函数声明形参删除
    --------------------------------
    - 只在「从 func_1 可达」的函数里挑一个有形参的函数
    - 删除这个函数声明/定义中的某一个形参
    - 不改任何调用点

    预期诊断：too few / too many arguments, 或函数类型冲突。
    返回值：
      True  => 至少对一个函数声明/定义完成了形参删除
      False => 没有找到合适的候选（比如所有 reachable 函数都没有形参）
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    main_path = src_path

    # 1. 收集函数定义 + 调用关系
    functions, func_by_usr, callees_by_usr = _collect_funcs_and_calls(root, main_path)

    # 2. 计算从 func_1 可达的函数集合
    reachable_usrs = _reachable_from_func1(func_by_usr, callees_by_usr)
    if not reachable_usrs:
        # 理论上不会，但兜个底
        return False

    # 3. 在 reachable 函数里挑一个「有形参」的函数
    target_func = _pick_target_function(functions, reachable_usrs)
    if target_func is None:
        return False

    params = list(target_func.get_arguments())
    n_params = len(params)
    if n_params == 0:
        return False

    # 这里可以换成 random.randint(0, n_params-1) 做随机选择
    # 为了 debug 稳定，目前先固定选第一个
    param_idx = random.randint(0, n_params - 1)
    #param_idx = 0

    
    target_usr = target_func.get_usr()

    # 4. 读取源码，计算所有需要删除的 [start, end) 区间
    text = src_path.read_text(encoding="utf-8")

    spans = _compute_delete_spans_for_func(
        text=text,
        root=root,
        main_path=main_path,
        target_usr=target_usr,
        param_index=param_idx,
    )

    if not spans:
        # 没找到对应的声明/定义，算失败
        return False

    # 5. 按起始 offset 从大到小排序，然后依次删，避免 offset 失效
    spans.sort(key=lambda se: se[0], reverse=True)

    new_text = text
    for s, e in spans:
        if not (0 <= s <= e <= len(new_text)):
            continue
        new_text = new_text[:s] + new_text[e:]

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(new_text, encoding="utf-8")

    return True
