# src/edgm/mutators/insert_deref_nonptr_expr.py
#
# I11: 将非指针表达式 x 变成 *x，制造
#      "indirection requires pointer operand" 之类的编译错误。
#
# 做法：
#   - 用 libclang 遍历 AST，收集主文件中的所有 DeclRefExpr；
#   - 过滤出“静态类型不是指针/数组/函数”的那些（即真正的非指针表达式）；
#   - 随机挑一个，把源码中对应的那一小段从 `x` 改成 `(*x)`；
#   - 写出到输出文件，并返回 True/False 表示是否变异成功。
#

from __future__ import annotations

import random
from pathlib import Path
from typing import List

from clang.cindex import (
    Index,
    Cursor,
    CursorKind,
    TranslationUnit,
    SourceLocation,
    TypeKind,
)

# ---------- 公共解析工具：从 seed 文件出发找 runtime 并 parse TU ----------


def _find_project_root(src: Path) -> Path:
    """从 seed.c 一路往上找，直到看到 runtime 目录为止。"""
    cur = src.parent
    while True:
        runtime_dir = cur / "src" / "csmithedge" / "csmith" / "runtime"
        if runtime_dir.is_dir():
            return cur
        if cur.parent == cur:
            # 兜底：没找到就用 seed 所在目录
            return src.parent
        cur = cur.parent


def _parse_tu(in_path: str) -> TranslationUnit:
    """统一的 TU 解析函数，负责设置好 -I 等参数。"""
    src = Path(in_path).resolve()
    project_root = _find_project_root(src)
    runtime_dir = project_root / "src" / "csmithedge" / "csmith" / "runtime"

    args = [
        f"-I{runtime_dir}",
        "-std=c99",
    ]

    index = Index.create()
    tu = index.parse(in_path, args=args)

    # 打印诊断信息（方便你确认有没有 fatal error）
    for d in tu.diagnostics:
        print(f"[AST diag] {d}")

    return tu


# ---------- 基础小工具 ----------


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _location_to_offset(loc: SourceLocation, text: str) -> int:
    """
    将 (line, column) 转成源码里的 offset。
    line/column 从 1 开始，offset 从 0 开始。
    """
    line = loc.line - 1
    col = loc.column - 1
    if line < 0:
        return 0

    lines = text.splitlines(keepends=True)
    if line >= len(lines):
        return len(text)

    return sum(len(l) for l in lines[:line]) + min(col, len(lines[line]))


def _is_from_main_file(cur: Cursor, main_path: Path) -> bool:
    loc = cur.location
    if loc is None or loc.file is None:
        return False

    try:
        return Path(loc.file.name).resolve() == main_path
    except Exception:
        return False


def _walk(node: Cursor):
    """简单 DFS 遍历。"""
    yield node
    for c in node.get_children():
        yield from _walk(c)


# 一些“看起来像指针/数组/函数”的类型 kind，排除掉
POINTER_LIKE_KINDS = {
    TypeKind.POINTER,
    TypeKind.LVALUEREFERENCE,
    TypeKind.RVALUEREFERENCE,
    TypeKind.CONSTANTARRAY,
    TypeKind.INCOMPLETEARRAY,
    TypeKind.VARIABLEARRAY,
    TypeKind.DEPENDENTSIZEDARRAY,
    TypeKind.FUNCTIONPROTO,
    TypeKind.FUNCTIONNOPROTO,
}


def _is_good_nonptr_declref(cur: cindex.Cursor, main_path: Path) -> bool:
    """
    判断一个 Cursor 是否是“适合做 I11 的 DeclRefExpr”：
      - 在主文件中；
      - kind == DECL_REF_EXPR；
      - 类型不是指针/数组/函数；
      - 名字不是 __builtin_ 开头。
    """
    from clang.cindex import CursorKind

    if cur.kind != CursorKind.DECL_REF_EXPR:
        return False

    if not _is_from_main_file(cur, main_path):
        return False

    t = cur.type
    if t is None:
        return False

    t = t.get_canonical()
    if t.kind in POINTER_LIKE_KINDS:
        return False

    spelling = cur.spelling or ""
    if spelling.startswith("__builtin_"):
        return False

    return True



def _collect_candidate_exprs(tu: TranslationUnit, main_path: Path) -> List[Cursor]:
    """收集所有可以做 *x 变异的 DeclRefExpr。"""
    cands: List[Cursor] = []
    for cur in _walk(tu.cursor):
        if _is_good_nonptr_declref(cur, main_path):
            cands.append(cur)

    print(f"[debug] I11: candidate non-pointer DeclRefExpr count = {len(cands)}")
    return cands


# ---------- 主变异逻辑 ----------


def insert_deref_nonptr_expr(input_path: str, output_path: str) -> bool:
    """
    I11 主入口：对 input_path 进行变异，输出到 output_path。

    返回：
      True  - 成功在某处把 x 改成 (*x)
      False - 未找到合适位置，未变异
    """
    in_path = Path(input_path).resolve()
    out_path = Path(output_path).resolve()

    if not in_path.exists():
        raise FileNotFoundError(f"源文件不存在: {in_path}")

    src = _read_file(in_path)

    # 这里改成统一用 _parse_tu（带 -I runtime）
    tu = _parse_tu(str(in_path))

    cands = _collect_candidate_exprs(tu, in_path)
    if not cands:
        print("[debug] I11: no suitable non-pointer expressions, skip mutation")
        return False

    # 随机选一个，而不是总是第一个
    target = random.choice(cands)
    name = target.spelling or "<expr>"

    start = target.extent.start
    end = target.extent.end

    off_start = _location_to_offset(start, src)
    off_end = _location_to_offset(end, src)

    if not (0 <= off_start <= off_end <= len(src)):
        print(
            f"[debug] I11: bad offset range for '{name}': "
            f"{off_start}..{off_end}, len={len(src)}"
        )
        return False

    old_text = src[off_start:off_end]
    print(
        f"[debug] I11: choose expr '{name}' at "
        f"{start.file}:{start.line}:{start.column}, text='{old_text}'"
    )

    # 真正的替换：x  ->  (*x)
    new_text = f"(*{old_text})"
    mutated = src[:off_start] + new_text + src[off_end:]

    _write_file(out_path, mutated)
    print(f"[结果] 已生成 I11 变异文件: {out_path}")
    return True


if __name__ == "__main__":
    # 简单本地测试用：保持和其它 mutator 一致的 CLI 风格
    import argparse

    ap = argparse.ArgumentParser(
        description="I11: 将非指针表达式 x 变成 *x（insert_deref_nonptr_expr）"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_deref_nonptr_expr(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 I11 变异（可能未找到合适的非指针表达式）")
