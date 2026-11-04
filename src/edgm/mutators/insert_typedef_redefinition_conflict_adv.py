from __future__ import annotations

import random
from pathlib import Path
from typing import List

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
            if node.spelling:
                typedefs.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)
    return typedefs


def _pick_conflicting_type(original: str) -> str:
    options = ["int", "long", "short", "unsigned", "float", "double", "char"]
    candidates = [t for t in options if t not in original]
    if not candidates:
        return "int"
    return random.choice(candidates)


def insert_typedef_redefinition_conflict_adv(input_path: str, output_path: str) -> bool:
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
        name = td.spelling
        if not name:
            continue
        original = td.underlying_typedef_type.spelling
        new_type = _pick_conflicting_type(original)
        insert_off = td.extent.end.offset
        insert_text = f"\ntypedef {new_type} {name};\n"
        rewriter = SourceRewriter(str(in_path))
        rewriter.replace_range(insert_off, insert_off, insert_text)
        rewriter.apply(str(out_path))
        return True

    return False