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


def _collect_switches(tu: cindex.TranslationUnit, src_path: Path) -> List[cindex.Cursor]:
    switches: List[cindex.Cursor] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.SWITCH_STMT and _is_from_main_file(node, src_path):
            switches.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)
    return switches


def _collect_cases_and_defaults(
    switch_node: cindex.Cursor,
) -> Tuple[List[cindex.Cursor], List[cindex.Cursor]]:
    cases: List[cindex.Cursor] = []
    defaults: List[cindex.Cursor] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.CASE_STMT:
            cases.append(node)
        elif node.kind == cindex.CursorKind.DEFAULT_STMT:
            defaults.append(node)
        for child in node.get_children():
            visit(child)

    visit(switch_node)
    return cases, defaults


def _extract_case_label(case_node: cindex.Cursor) -> str:
    tokens = list(case_node.get_tokens())
    if not tokens:
        return "0"
    start_idx = None
    for idx, tok in enumerate(tokens):
        if tok.spelling == "case":
            start_idx = idx + 1
            break
    if start_idx is None:
        return "0"
    parts: List[str] = []
    for tok in tokens[start_idx:]:
        if tok.spelling == ":":
            break
        parts.append(tok.spelling)
    label = " ".join(parts).strip()
    return label or "0"


def _pick_insertion(
    cases: List[cindex.Cursor],
    defaults: List[cindex.Cursor],
    source: str,
) -> Optional[Tuple[int, str]]:
    if defaults:
        target = random.choice(defaults)
        insert_off = target.extent.start.offset
        indent = _find_indent_at_offset(source, insert_off)
        return insert_off, f"{indent}default:\n"
    if cases:
        target = random.choice(cases)
        label = _extract_case_label(target)
        insert_off = target.extent.start.offset
        indent = _find_indent_at_offset(source, insert_off)
        return insert_off, f"{indent}case {label}:\n"
    return None


def insert_duplicate_switch_case(input_path: str, output_path: str) -> bool:
    in_path = Path(input_path)
    out_path = Path(output_path)
    source = _read_text(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    switches = _collect_switches(tu, in_path)
    random.shuffle(switches)

    for switch in switches:
        cases, defaults = _collect_cases_and_defaults(switch)
        insertion = _pick_insertion(cases, defaults, source)
        if not insertion:
            continue
        insert_off, insert_text = insertion
        rewriter = SourceRewriter(str(in_path))
        rewriter.replace_range(insert_off, insert_off, insert_text)
        rewriter.apply(str(out_path))
        return True

    return False