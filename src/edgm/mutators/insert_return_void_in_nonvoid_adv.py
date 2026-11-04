from __future__ import annotations

import random
from pathlib import Path
from typing import List, Optional

from clang import cindex

from edgm.ast.rewrite import SourceRewriter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSMITH_RUNTIME_DIR = PROJECT_ROOT / "src" / "csmithedge" / "csmith" / "runtime"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _is_from_main_file(node: cindex.Cursor, main_path: Path) -> bool:
    loc = node.location
    if loc is None or loc.file is None:
        return False
    return Path(loc.file.name).name == main_path.name


def _collect_nonvoid_functions(
    tu: cindex.TranslationUnit, src_path: Path
) -> List[cindex.Cursor]:
    funcs: List[cindex.Cursor] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.FUNCTION_DECL and _is_from_main_file(node, src_path):
            if node.result_type.spelling != "void" and node.is_definition():
                funcs.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)
    return funcs


def _find_body_insert_offset(source: str, func: cindex.Cursor) -> Optional[int]:
    start = func.extent.start.offset
    end = func.extent.end.offset
    if start is None or end is None:
        return None
    brace = source.find("{", start, end)
    if brace == -1:
        return None
    return brace + 1


def _find_indent_at_offset(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, offset)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    indent = ""
    i = line_start
    while i < len(text) and text[i] in (" ", "\t"):
        indent += text[i]
        i += 1
    return indent


def insert_return_void_in_nonvoid_adv(input_path: str, output_path: str) -> bool:
    in_path = Path(input_path)
    out_path = Path(output_path)
    source = _read_text(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    funcs = _collect_nonvoid_functions(tu, in_path)
    if not funcs:
        return False

    random.shuffle(funcs)
    for func in funcs:
        insert_off = _find_body_insert_offset(source, func)
        if insert_off is None:
            continue
        indent = _find_indent_at_offset(source, insert_off) + "    "
        insert_text = (
            f"\n{indent}return (0 ? 1 : (struct {{int __edgm;}}){{0}});"
        )
        rewriter = SourceRewriter(str(in_path))
        rewriter.replace_range(insert_off, insert_off, insert_text)
        rewriter.apply(str(out_path))
        return True

    return False