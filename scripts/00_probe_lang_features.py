#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from collections import Counter

from clang.cindex import CursorKind

from edgm.paths import PROJECT_ROOT
from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file


# 想探测的语言特性，对应的 AST 节点类型
FEATURE_KINDS = {
    "union": {CursorKind.UNION_DECL},
    "typedef": {CursorKind.TYPEDEF_DECL},
    "goto": {CursorKind.GOTO_STMT},
    "enum": {CursorKind.ENUM_DECL},
    "switch": {CursorKind.SWITCH_STMT},
    "case": {CursorKind.CASE_STMT},
}


def collect_features(src_path: Path) -> Counter:
    """
    对单个 C 文件，统计 union / typedef / goto / enum / switch / case 的出现次数。
    只统计来自主源文件本身的节点（过滤掉头文件）。
    """
    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    counter: Counter[str] = Counter()

    def visit(node):
        # 只处理主文件里的节点
        if not _is_from_main_file(node, str(src_path)):
            return

        kind = node.kind

        # 命中我们关心的几类节点就计数
        for name, kinds in FEATURE_KINDS.items():
            if kind in kinds:
                counter[name] += 1

        for child in node.get_children():
            visit(child)

    visit(root)
    return counter


def main() -> None:
    seed_dir = PROJECT_ROOT / "data/seeds/clean"
    seeds = sorted(seed_dir.glob("*.c"))

    if not seeds:
        print(f"[警告] 没在 {seed_dir} 找到任何种子文件")
        return

    for seed in seeds:
        counts = collect_features(seed)
        # 保证每个字段都有个数字（即使是 0）
        union_n = counts.get("union", 0)
        typedef_n = counts.get("typedef", 0)
        goto_n = counts.get("goto", 0)
        enum_n = counts.get("enum", 0)
        switch_n = counts.get("switch", 0)
        case_n = counts.get("case", 0)

        print(
            f"[探测] {seed.name}: "
            f"union={union_n}, typedef={typedef_n}, "
            f"goto={goto_n}, enum={enum_n}, "
            f"switch={switch_n}, case={case_n}"
        )


if __name__ == "__main__":
    main()
