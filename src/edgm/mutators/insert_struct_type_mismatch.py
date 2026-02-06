# src/edgm/mutators/insert_struct_type_mismatch_adv.py
#
# I8-adv: struct 变量/成员访问类型错配（高级版）
# 核心思路：
#   - 不改原变量的声明类型；
#   - 找到一次成员访问 (x.f 或 x->f)；
#   - 在那一行前后插入：
#        #define x  <一个类型不对的表达式>
#        ...原行不改...
#        #undef x
#   - 再在 #include 后注入一段“干扰类型链”（struct/typedef），让报错更像宏污染。
#
# 目标诊断（不同编译器可能主报错不同）：
#   - error: ‘struct edgm_I8_XXX’ has no member named ‘f0’
#   - error: request for member ‘f0’ in something not a structure or union
#   - error: invalid use of incomplete type ‘struct edgm_I8_XXX’

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import List, Optional, Tuple

from clang import cindex


def _find_project_root(src: Path) -> Path:
    cur = src.parent
    while True:
        runtime_dir = cur / "src" / "csmithedge" / "csmith" / "runtime"
        if runtime_dir.is_dir():
            return cur
        if cur.parent == cur:
            return src.parent
        cur = cur.parent


def _parse_tu(seed_file: str) -> cindex.TranslationUnit:
    seed = Path(seed_file).resolve()
    root = _find_project_root(seed)
    runtime = root / "src" / "csmithedge" / "csmith" / "runtime"

    index = cindex.Index.create()
    tu = index.parse(
        str(seed),
        args=["-I", str(runtime), "-I", str(runtime.parent), "-std=c99"],
        options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )

    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    return tu


def _same_file(seed_path: str, loc_file) -> bool:
    if loc_file is None:
        return False
    try:
        return os.path.samefile(seed_path, loc_file.name)
    except Exception:
        return os.path.basename(seed_path) == os.path.basename(loc_file.name)


def _walk(cur: cindex.Cursor):
    yield cur
    for ch in cur.get_children():
        yield from _walk(ch)


def _get_tokens_text(tu: cindex.TranslationUnit, cur: cindex.Cursor) -> str:
    try:
        toks = list(tu.get_tokens(extent=cur.extent))
        return "".join(t.spelling for t in toks)
    except Exception:
        return ""


def _collect_member_refs(
    tu: cindex.TranslationUnit, seed_path: str
) -> List[Tuple[cindex.Cursor, str, str, str]]:
    """
    返回若干候选 (cursor, base_ident, member_name, access_kind)
      access_kind: "arrow" or "dot"（尽力推断）
    只选主文件内的 MemberRefExpr，并且 base 是一个 DeclRefExpr（便于 #define base）。
    """
    cands: List[Tuple[cindex.Cursor, str, str, str]] = []

    for c in _walk(tu.cursor):
        if c.kind != cindex.CursorKind.MEMBER_REF_EXPR:
            continue
        if not _same_file(seed_path, c.location.file):
            continue

        member_name = c.spelling or ""
        if not member_name:
            continue

        # 尝试找 base（通常第一个 child 是 base 表达式）
        children = list(c.get_children())
        if not children:
            continue
        base = children[0]
        if base.kind != cindex.CursorKind.DECL_REF_EXPR:
            continue
        base_ident = base.spelling or ""
        if not base_ident:
            continue
        if base_ident.startswith("__") or base_ident.startswith("edgm_"):
            continue

        # 推断 . / -> ：看 token 串里有没有 "->"
        tok_text = _get_tokens_text(tu, c)
        access_kind = "arrow" if "->" in tok_text else "dot"

        cands.append((c, base_ident, member_name, access_kind))

    print(f"[debug] I8-adv: candidate MEMBER_REF_EXPR count = {len(cands)}")
    return cands


def _find_insert_line_after_includes(src: str) -> int:
    lines = src.splitlines(keepends=True)
    last_inc = -1
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#include"):
            last_inc = i
    return 1 if last_inc < 0 else (last_inc + 2)


def _inject_noise_types(tag: str) -> str:
    """
    注入“干扰类型链”：多层 typedef + 一个缺字段的 struct。
    tag 是唯一后缀，避免重名。
    """
    sname = f"edgm_I8_{tag}"
    t1 = f"edgm_i8_t1_{tag}"
    t2 = f"edgm_i8_t2_{tag}"
    t3 = f"edgm_i8_t3_{tag}"

    return (
        "\n/* edgm I8-adv: noisy type chain (intentionally confusing) */\n"
        f"struct {sname} {{\n"
        "    long edgm_a;\n"
        "    unsigned edgm_b;\n"
        "    void *edgm_p;\n"
        "};\n"
        f"typedef struct {sname} {t1};\n"
        f"typedef {t1} {t2};\n"
        f"typedef {t2} {t3};\n"
        "\n"
    )


def _choose_shadow_macro(base_ident: str, tag: str, member_name: str, access_kind: str) -> Tuple[str, str]:
    """
    选择一种“局部 shadow base_ident”的宏展开方式。
    返回 (define_line, strategy_name)。
    """
    sname = f"edgm_I8_{tag}"

    # 三种策略：更容易让 gcc/clang 的主报错点不一致
    # 1) base 直接变 0：x.f / x->f 会变成 “not a struct/union”
    # 2) base 变成 (struct edgm*)0 或 (*(struct edgm*)0)：更倾向 “no member named …”
    # 3) base 变成 “不完全类型”指针再解引用：更倾向 “incomplete type” 相关报错
    strat = random.choice(["NOT_STRUCT", "NO_MEMBER", "INCOMPLETE"])

    if strat == "NOT_STRUCT":
        # x -> 0; 让 x.f / x->f 直接炸 “not a struct/union”
        define = f"#define {base_ident} (0)\n"
        return define, strat

    if strat == "INCOMPLETE":
        # 先声明一个不完全类型 tag（不定义），再让 base 解引用它
        # 注意：我们会把这个 forward decl 注入到 noise types 里（见下方插入点）
        # 这里让 base 变成 *(struct edgm_I8_TAG*)0（dot） 或 (struct ...*)0（arrow）
        if access_kind == "arrow":
            define = f"#define {base_ident} ((struct {sname} *)0)\n"
        else:
            define = f"#define {base_ident} (*((struct {sname} *)0))\n"
        return define, strat

    # NO_MEMBER：让成员名在 struct 里“不存在”，更像“类型对但字段不对”
    if access_kind == "arrow":
        define = f"#define {base_ident} ((struct edgm_i8_t3_{tag} *)0)\n"
    else:
        define = f"#define {base_ident} (*((struct edgm_i8_t3_{tag} *)0))\n"
    return define, "NO_MEMBER"


def insert_struct_type_mismatch_adv(in_path: str, out_path: str) -> bool:
    seed_path = str(Path(in_path).resolve())
    src_path = Path(seed_path)
    if not src_path.exists():
        raise FileNotFoundError(seed_path)

    code = src_path.read_text(encoding="utf-8")
    tu = _parse_tu(seed_path)

    cands = _collect_member_refs(tu, seed_path)
    if not cands:
        print("[debug] I8-adv: no suitable MEMBER_REF_EXPR; skip")
        return False

    cur, base_ident, member_name, access_kind = random.choice(cands)
    line_no = cur.location.line

    tag = f"{random.randint(10000, 99999)}"
    print(
        f"[debug] I8-adv: choose member access at {cur.location.file}:{line_no}:{cur.location.column}, "
        f"base='{base_ident}', member='{member_name}', kind={access_kind}, tag={tag}"
    )

    # 准备注入噪音类型链
    noise = _inject_noise_types(tag)

    # 如果走 INCOMPLETE 策略，我们把 struct 只 forward-declare，不给定义（更狠）
    define_line, strat = _choose_shadow_macro(base_ident, tag, member_name, access_kind)
    if strat == "INCOMPLETE":
        # 覆盖 noise：只 forward declare，且不给字段
        sname = f"edgm_I8_{tag}"
        noise = (
            "\n/* edgm I8-adv: forward-declare only (incomplete type) */\n"
            f"struct {sname};\n\n"
        )

        # 在目标行前后插入 #define/#undef（只影响那一行）
    lines = code.splitlines(keepends=True)
    if not (1 <= line_no <= len(lines)):
        print("[debug] I8-adv: invalid target line; skip")
        return False

    # 1) 噪音类型插到 include 后 —— 必须按“行”插入
    insert_line = _find_insert_line_after_includes(code)
    idx_noise = max(0, min(len(lines), insert_line - 1))

    noise_ls = noise.splitlines(keepends=True)
    lines[idx_noise:idx_noise] = noise_ls
    shift = len(noise_ls)

    # 2) 重新计算目标行号（噪音插入导致行号整体后移）
    new_line_no = line_no + shift
    idx_target = new_line_no - 1

    # 3) 生成 before/after（用 // 注释，避免嵌套 /* */）
    before = (
        f"\n// edgm I8-adv: shadow '{base_ident}' to trigger struct mismatch\n"
        f"{define_line}"
    )
    after = f"#undef {base_ident}\n"

    before_ls = before.splitlines(keepends=True)
    after_ls = after.splitlines(keepends=True)

    # 4) 插入：before 在目标行前，after 在目标行后
    lines[idx_target:idx_target] = before_ls
    idx_after = idx_target + len(before_ls) + 1  # +1 跳过原目标行
    lines[idx_after:idx_after] = after_ls

    mutated = "".join(lines)


    outp = Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(mutated, encoding="utf-8")

    print(f"[debug] I8-adv: strategy = {strat}")
    print(f"[结果] 已生成 I8-adv 变异文件: {outp}")
    return True
