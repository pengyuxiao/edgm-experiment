# src/edgm/mutators/insert_goto_vla.py

import os
import sys
import random
import shutil

from clang.cindex import Index, CursorKind, TypeKind


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _compute_line_offsets(text: str):
    offsets = []
    acc = 0
    for line in text.splitlines(keepends=True):
        offsets.append(acc)
        acc += len(line)
    return offsets


def _offset_from_line_col(text: str, line: int, col: int) -> int:
    offsets = _compute_line_offsets(text)
    if line < 1 or line > len(offsets):
        raise ValueError(f"line {line} out of range")
    return offsets[line - 1] + (col - 1)


def _infer_indent(text: str, offset: int) -> str:
    if offset > len(text):
        offset = len(text)
    line_start = text.rfind("\n", 0, offset)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    i = line_start
    while i < len(text) and text[i] in (" ", "\t"):
        i += 1
    return text[line_start:i]


def _same_file(path1: str, path2: str) -> bool:
    """更鲁棒的文件路径比较：realpath + basename."""
    if not path1 or not path2:
        return False
    r1 = os.path.realpath(path1)
    r2 = os.path.realpath(path2)
    if r1 == r2:
        return True
    # 再退一步，至少保证同名
    return os.path.basename(r1) == os.path.basename(r2)


def _collect_globals_and_funcs(tu, main_path: str):
    int_globals = []
    func_defs = []

    for c in tu.cursor.get_children():
        loc = c.location
        if not loc.file:
            continue
        if not _same_file(loc.file.name, main_path):
            continue

        if c.kind == CursorKind.VAR_DECL:
            ty = c.type
            if ty.kind in (
                TypeKind.INT,
                TypeKind.UINT,
                TypeKind.SHORT,
                TypeKind.USHORT,
                TypeKind.LONG,
                TypeKind.ULONG,
                TypeKind.LONGLONG,
                TypeKind.ULONGLONG,
            ):
                int_globals.append(c.spelling)

        elif c.kind == CursorKind.FUNCTION_DECL:
            # 不再强制 is_definition()，后面再检查有没有 COMPOUND_STMT
            func_defs.append(c)

    return int_globals, func_defs


def _pick_vla_length_expr(func_cursor, int_globals):
    int_param_names = []
    for p in func_cursor.get_arguments():
        ty = p.type
        if ty.kind in (
            TypeKind.INT,
            TypeKind.UINT,
            TypeKind.SHORT,
            TypeKind.USHORT,
            TypeKind.LONG,
            TypeKind.ULONG,
            TypeKind.LONGLONG,
            TypeKind.ULONGLONG,
        ):
            int_param_names.append(p.spelling)

    if int_param_names:
        base = random.choice(int_param_names)
    elif int_globals:
        base = random.choice(int_globals)
    else:
        return "8"  # 实在找不到，退化为常量，保证能插

    return f"(({base}) <= 0 ? 1 : ({base}))"


def _find_func_body_compound(func_cursor, main_path: str):
    for c in func_cursor.get_children():
        if c.kind == CursorKind.COMPOUND_STMT:
            loc = c.location
            if loc.file and _same_file(loc.file.name, main_path):
                return c
    return None


def _pick_insert_location(body_cursor, main_path: str):
    first_stmt = None
    for c in body_cursor.get_children():
        if c.location.file and _same_file(c.location.file.name, main_path):
            first_stmt = c
            break

    if first_stmt is not None:
        loc = first_stmt.extent.start
        return loc.line, loc.column

    loc = body_cursor.extent.start
    return loc.line, loc.column + 1


def insert_goto_vla(in_path: str, out_path: str) -> bool:
    """
    I16: 在某个函数体中插入：
        goto L;
        do {
            int len = (expr <= 0 ? 1 : expr);
            int vla[len];
        L:
            vla[0] = 0;
        } while (0);

    使得 goto 从 VLA 作用域外跳入内部，违反 C99 6.8.6.1。
    """
    # 确保输出目录存在（修复 FileNotFoundError）
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    src = _read_file(in_path)

    # 解析 TU
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    runtime_inc = os.path.join(root_dir, "src", "csmithedge", "csmith", "runtime")

    index = Index.create()
    args = [
        "-std=c99",
        f"-I{runtime_inc}",
    ]
    tu = index.parse(in_path, args=args)

    main_path = os.path.abspath(in_path)

    int_globals, func_defs = _collect_globals_and_funcs(tu, main_path)
    if not func_defs:
        print("[debug] I16: no function candidates in TU; skip")
        shutil.copy2(in_path, out_path)
        return False

    # 过滤掉没有函数体的（没有 COMPOUND_STMT 的）
    func_with_body = []
    for f in func_defs:
        body = _find_func_body_compound(f, main_path)
        if body is not None:
            func_with_body.append((f, body))

    if not func_with_body:
        print("[debug] I16: no function with body in main file; skip")
        shutil.copy2(in_path, out_path)
        return False

    print(f"[debug] I16: candidate functions with body = {len(func_with_body)}")

    func, body = random.choice(func_with_body)
    func_name = func.spelling or "<anon>"
    print(f"[debug] I16: choose function '{func_name}' at line {func.location.line}")

    vla_len_expr = _pick_vla_length_expr(func, int_globals)

    insert_line, insert_col = _pick_insert_location(body, main_path)
    print(f"[debug] I16: insert at {in_path}:{insert_line}:{insert_col}, len_expr='{vla_len_expr}'")

    offset = _offset_from_line_col(src, insert_line, insert_col)
    indent = _infer_indent(src, offset)

    mut_id = random.randint(10000, 99999)
    label = f"edgm_i16_label_{mut_id}"
    len_var = f"edgm_i16_len_{mut_id}"
    vla_var = f"edgm_i16_vla_{mut_id}"

    injected = (
        f"{indent}/* edgm I16: goto into VLA scope (diagnostic-sensitive) */\n"
        f"{indent}goto {label};\n"
        f"{indent}do {{\n"
        f"{indent}    int {len_var} = {vla_len_expr};\n"
        f"{indent}    int {vla_var}[{len_var}];\n"
        f"{indent}{label}:\n"
        f"{indent}    {vla_var}[0] = 0;\n"
        f"{indent}}} while (0);\n\n"
    )

    mutated = src[:offset] + injected + src[offset:]
    _write_file(out_path, mutated)

    print(f"[结果] 已生成 I16 变异文件: {out_path}")
    return True
