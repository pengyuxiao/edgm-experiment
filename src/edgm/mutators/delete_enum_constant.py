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


def _collect_enum_constants(
    tu: cindex.TranslationUnit, src_path: Path
) -> List[cindex.Cursor]:
    constants: List[cindex.Cursor] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.ENUM_CONSTANT_DECL and _is_from_main_file(node, src_path):
            constants.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)
    return constants


def _expand_range_to_comma(source: str, start: int, end: int) -> Optional[tuple[int, int]]:
    if start is None or end is None:
        return None
    right = source.find(",", end, end + 6)
    if right != -1:
        return start, right + 1
    left = source.rfind(",", max(0, start - 6), start)
    if left != -1:
        return left, end
    return start, end


def delete_enum_constant(input_path: str, output_path: str) -> bool:
    in_path = Path(input_path)
    out_path = Path(output_path)
    source = _read_text(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    constants = _collect_enum_constants(tu, in_path)
    if not constants:
        return False

    random.shuffle(constants)
    for const in constants:
        start = const.extent.start.offset
        end = const.extent.end.offset
        span = _expand_range_to_comma(source, start, end)
        if span is None:
            continue
        span_start, span_end = span
        rewriter = SourceRewriter(str(in_path))
        rewriter.replace_range(span_start, span_end, "")
        rewriter.apply(str(out_path))
        return True

    return False