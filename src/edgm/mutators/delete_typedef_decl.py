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


def _collect_typedefs(tu: cindex.TranslationUnit, src_path: Path) -> List[cindex.Cursor]:
    typedefs: List[cindex.Cursor] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.TYPEDEF_DECL and _is_from_main_file(node, src_path):
            typedefs.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)
    return typedefs


def _find_statement_end(text: str, start: int, end: int) -> Optional[int]:
    if start is None or end is None:
        return None
    semi = text.find(";", start, end + 1)
    if semi == -1:
        return None
    return semi


def delete_typedef_decl(input_path: str, output_path: str) -> bool:
    in_path = Path(input_path)
    out_path = Path(output_path)
    source = _read_text(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    typedefs = _collect_typedefs(tu, in_path)
    if not typedefs:
        return False

    random.shuffle(typedefs)
    for td in typedefs:
        start = td.extent.start.offset
        end = td.extent.end.offset
        semi = _find_statement_end(source, start, end)
        if semi is None:
            continue
        rewriter = SourceRewriter(str(in_path))
        rewriter.replace_range(start, semi + 1, "")
        rewriter.apply(str(out_path))
        return True

    return False