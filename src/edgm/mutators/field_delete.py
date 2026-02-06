from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from clang.cindex import Cursor, CursorKind

from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file

from clang.cindex import CursorKind

def _field_key_from_decl(field_cursor):
    """
    把一个 FieldDecl 映射成字符串 key，例如 'S0::f0'
    """
    parent = field_cursor.semantic_parent
    if parent is not None and parent.spelling:
        rec_name = parent.spelling
    else:
        rec_name = ""
    return f"{rec_name}::{field_cursor.spelling}"


def _field_key_from_member(member_cursor):
    """
    把一个 MemberExpr / MemberRefExpr 映射成字符串 key，尽量和 _field_key_from_decl 一致。
    """
    field_name = member_cursor.spelling

    # 尝试拿 base 表达式的类型名，比如 "struct S0 *"
    base_type_name = None
    for child in member_cursor.get_children():
        base_type_name = getattr(child.type, "spelling", None)
        if base_type_name:
            break

    rec_name = ""
    if base_type_name:
        # 例子: "struct S0 *" / "const struct S0 *"
        t = base_type_name.replace("const ", "").strip()
        t = t.replace("struct ", "").replace("union ", "")
        # 去掉指针星号
        t = t.rstrip(" *")
        rec_name = t

    return f"{rec_name}::{field_name}"


def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列) 转成字符串 offset。
    libclang 是 1-based line/column，这里转成 Python 0-based index。
    """
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        raise ValueError(f"line 超出范围: {line}, 文件总行数: {len(lines)}")

    offset = 0
    for i in range(line - 1):
        offset += len(lines[i])
    offset += (col - 1)
    return offset


def _collect_fields_and_uses(root, src_path: str):
    """
    扫一遍 AST：
      - 收集所有 FIELD_DECL（struct/union 字段定义）到 fields 列表
      - 收集所有成员访问 (MemberExpr / MemberRefExpr)，
        按 “记录类型名::字段名” 这种 key 放到 uses_by_field 里
    """
    fields: List[Cursor] = []               # 只存字段声明的 Cursor
    uses_by_field: Dict[str, List[Cursor]] = {}  # key 是字符串，比如 "S0::f0"

    def visit(node: Cursor):
        # 只处理同一个源文件里的节点
        loc = node.location
        if loc.file is not None and loc.file.name != src_path:
            return

        kind = node.kind

        # 1) 收集字段声明
        if kind == CursorKind.FIELD_DECL:
            fields.append(node)

        # 2) 收集字段使用（成员访问表达式）
        elif kind in (
            CursorKind.MEMBER_REF_EXPR,
            CursorKind.MEMBER_REF,  # 有的 clang 版本用这个
        ):
            key = _field_key_from_member(node)
            uses_by_field.setdefault(key, []).append(node)

        # 递归遍历子节点
        for child in node.get_children():
            visit(child)

    visit(root)
    return fields, uses_by_field




def _pick_field_for_deletion(
    fields: List[Cursor],
    uses_by_field: Dict[str, List[Cursor]],
) -> Optional[Cursor]:
    """
    从所有字段声明里选一个“至少被使用过一次”的字段。

    这里通过把字段 Cursor 映射成 key（结构体名::字段名），
    再在 uses_by_field 里查这个 key 是否有使用点。
    """
    for f in fields:
        key = _field_key_from_decl(f)
        if key in uses_by_field and uses_by_field[key]:
            return f
    return None



def delete_struct_field(src: str, dst: str) -> bool:
    """
    P6: 删除一个 struct/union 字段定义，保留所有对该字段的访问。

    步骤：
      1. 解析 AST，收集字段及其使用点
      2. 选一个“有使用”的字段
      3. 根据 FIELD_DECL 的 extent 从源码里删掉这一行
      4. 写入 dst

    返回值：
      - True: 成功删掉一个字段
      - False: 没有找到“有使用”的字段，未进行变异
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    fields, uses_by_field = _collect_fields_and_uses(root, str(src_path))
    field = _pick_field_for_deletion(fields, uses_by_field)
    if field is None:
        return False

    start = field.extent.start
    end = field.extent.end

    # 保险起见：确保这个字段本身确实在主文件里
    if not start.file or not end.file:
        return False
    if Path(start.file.name).resolve() != src_path:
        return False
    if Path(end.file.name).resolve() != src_path:
        return False

    text = src_path.read_text(encoding="utf-8")

    start_off = _loc_to_offset(text, start.line, start.column)
    end_off = _loc_to_offset(text, end.line, end.column)

    if not (0 <= start_off <= end_off <= len(text)):
        return False

    # 直接删掉这段源码（FIELD_DECL 通常是独立一行，删掉不会破坏 struct 语法）
    new_text = text[:start_off] + text[end_off:]

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(new_text, encoding="utf-8")

    return True
