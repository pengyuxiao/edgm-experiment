# src/edgm/mutators/insert_nonconst_global_init_adv.py
#
# #31 / I14-adv: file-scope(全局/静态)变量的 initializer 改成“非 const 表达式”
# 但用 typedef + 函数指针 + 宏层层包装来增加迷惑性，促进诊断不一致。
#
# 目标 error: initializer element is not constant
#
# 不在本文件中调用 gcc/clang；只输出变异成功与否。

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import List, Optional, Tuple

from clang import cindex


def _same_file(seed_path: str, loc_file) -> bool:
    if loc_file is None:
        return False
    try:
        return os.path.samefile(seed_path, loc_file.name)
    except Exception:
        return Path(seed_path).name == Path(loc_file.name).name


def _find_project_root(src: Path) -> Path:
    cur = src.parent
    while True:
        runtime_dir = cur / "src" / "csmithedge" / "csmith" / "runtime"
        if runtime_dir.is_dir():
            return cur
        if cur.parent == cur:
            return src.parent
        cur = cur.parent


def _parse_tu(in_path: str) -> cindex.TranslationUnit:
    src = Path(in_path).resolve()
    project_root = _find_project_root(src)
    runtime_dir = project_root / "src" / "csmithedge" / "csmith" / "runtime"

    args = [
        f"-I{runtime_dir}",
        f"-I{runtime_dir.parent}",
        "-std=c99",
    ]
    index = cindex.Index.create()
    tu = index.parse(
        in_path,
        args=args,
        options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )

    for d in tu.diagnostics:
        print(f"[AST diag] {d}")

    return tu


def _walk(cur: cindex.Cursor):
    yield cur
    for c in cur.get_children():
        yield from _walk(c)


def _find_insert_after_includes(src: str) -> int:
    lines = src.splitlines(keepends=True)
    last_inc = -1
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#include"):
            last_inc = i
    if last_inc < 0:
        return 0
    return sum(len(l) for l in lines[: last_inc + 1])


def _is_supported_global_type(t: cindex.Type) -> bool:
    """
    过滤掉不适合用“函数返回值”构造的类型（主要是数组）。
    struct/union 可返回，指针/整数/浮点都可返回。
    """
    ct = t.get_canonical()
    if ct.kind in (
        cindex.TypeKind.CONSTANTARRAY,
        cindex.TypeKind.INCOMPLETEARRAY,
        cindex.TypeKind.VARIABLEARRAY,
        cindex.TypeKind.DEPENDENTSIZEDARRAY,
    ):
        return False
    return True


def _default_value_expr(type_spelling: str, t: cindex.Type) -> str:
    """
    构造一个“类型正确”的返回值表达式：
      - struct/union: (T){0}
      - 其他：       (T)0
    """
    ct = t.get_canonical()
    if ct.kind == cindex.TypeKind.RECORD:
        return f"({type_spelling}){{0}}"
    return f"({type_spelling})0"


def _collect_file_scope_vardecls_with_init(
    tu: cindex.TranslationUnit, seed_path: str
) -> List[Tuple[cindex.Cursor, cindex.Cursor]]:
    out: List[Tuple[cindex.Cursor, cindex.Cursor]] = []
    for c in _walk(tu.cursor):
        if c.kind != cindex.CursorKind.VAR_DECL:
            continue
        if not _same_file(seed_path, c.location.file):
            continue
        if c.semantic_parent is None or c.semantic_parent.kind != cindex.CursorKind.TRANSLATION_UNIT:
            continue

        name = c.spelling or ""
        if not name or name.startswith("__") or name.startswith("edgm_"):
            continue

        if not _is_supported_global_type(c.type):
            continue

        children = list(c.get_children())
        if not children:
            continue

        # 取最后一个 child 作为 initializer 的候选（对 csmith seed 通常靠谱）
        init_cand = children[-1]
        s_off = init_cand.extent.start.offset
        e_off = init_cand.extent.end.offset
        if s_off is None or e_off is None:
            continue

        out.append((c, init_cand))

    print(f"[debug] I14-adv: file-scope VarDecl with init count = {len(out)}")
    return out


def insert_nonconst_global_init_adv(input_path: str, output_path: str) -> bool:
    in_path = Path(input_path).resolve()
    out_path = Path(output_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"源文件不存在: {in_path}")

    src = in_path.read_text(encoding="utf-8")
    tu = _parse_tu(str(in_path))

    pairs = _collect_file_scope_vardecls_with_init(tu, str(in_path))
    if not pairs:
        print("[debug] I14-adv: no suitable file-scope VarDecl with initializer; skip")
        return False

    var_cur, init_cur = random.choice(pairs)
    var_name = var_cur.spelling or "<var>"
    type_sp = var_cur.type.spelling.strip()

    s_off = init_cur.extent.start.offset
    e_off = init_cur.extent.end.offset
    if s_off is None or e_off is None or not (0 <= s_off <= e_off <= len(src)):
        print(f"[debug] I14-adv: bad init extent offsets for {var_name}: {s_off}..{e_off}")
        return False

    old_init = src[s_off:e_off]
    print(f"[debug] I14-adv: choose global '{var_name}', type='{type_sp}', old_init='{old_init.strip()[:80]}'")

    # 更迷惑的插入片段：typedef + helper + 函数指针 + 多层宏
    tag = "31"
    tname = f"edgm_i14_t_{tag}"
    helper = f"edgm_i14_nonconst_{tag}"
    fp = f"edgm_i14_fp_{tag}"

    ret_expr = _default_value_expr(tname, var_cur.type)

    snippet = (
        f"\n/* edgm I14-adv #{tag}: non-constant global initializer via macro+fp */\n"
        f"typedef {type_sp} {tname};\n"
        f"static {tname} {helper}(void) {{ return {ret_expr}; }}\n"
        f"static {tname} (*{fp})(void) = {helper};\n"
        f"#define EDGM_I14_WRAP_{tag}(x) (x)\n"
        f"#define EDGM_I14_STEP1_{tag}(f) EDGM_I14_WRAP_{tag}(f)\n"
        f"#define EDGM_I14_STEP2_{tag}(f) EDGM_I14_STEP1_{tag}(f)()\n"
        f"#define EDGM_I14_INIT_{tag}() EDGM_I14_STEP2_{tag}({fp})\n\n"
    )

    insert_pos = _find_insert_after_includes(src)
    src2 = src[:insert_pos] + snippet + src[insert_pos:]

    # 插入导致 offset 后移：如果 initializer 在插入点之后，需要平移
    delta = len(snippet)
    if s_off >= insert_pos:
        s_off += delta
        e_off += delta

    new_init = f"EDGM_I14_INIT_{tag}()"
    mutated = src2[:s_off] + new_init + src2[e_off:]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(mutated, encoding="utf-8")
    print(f"[结果] 已生成 I14-adv 变异文件: {out_path}")
    return True
