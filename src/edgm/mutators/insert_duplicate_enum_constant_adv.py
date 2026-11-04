from __future__ import annotations

import random
from pathlib import Path
from typing import List, Optional, Tuple

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


def _collect_enum_candidates(
    tu: cindex.TranslationUnit, src_path: Path
) -> List[Tuple[cindex.Cursor, List[cindex.Cursor]]]:
    enums: List[Tuple[cindex.Cursor, List[cindex.Cursor]]] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.ENUM_DECL and _is_from_main_file(node, src_path):
            constants = [
                child
                for child in node.get_children()
                if child.kind == cindex.CursorKind.ENUM_CONSTANT_DECL
            ]
            if constants:
                enums.append((node, constants))
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)
    return enums


def _find_enum_close_brace(text: str, enum_node: cindex.Cursor) -> Optional[int]:
    start = enum_node.extent.start.offset
    end = enum_node.extent.end.offset
    if start is None or end is None:
        return None
    close_pos = text.rfind("}", start, end)
    return close_pos if close_pos != -1 else None


def insert_duplicate_enum_constant_adv(input_path: str, output_path: str) -> bool:
    in_path = Path(input_path)
    out_path = Path(output_path)
    source = _read_text(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    enums = _collect_enum_candidates(tu, in_path)
    if not enums:
        return False

    enum_node, constants = random.choice(enums)
    const = random.choice(constants)
    name = const.spelling
    if not name:
        return False

    close_brace = _find_enum_close_brace(source, enum_node)
    if close_brace is None:
        return False

    indent = _find_indent_at_offset(source, const.location.offset)
    insert_text = f",\n{indent}{name}"

    rewriter = SourceRewriter(str(in_path))
    rewriter.replace_range(close_brace, close_brace, insert_text)
    rewriter.apply(str(out_path))
    return True