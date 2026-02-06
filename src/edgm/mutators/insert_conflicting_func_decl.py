import random
import re
from pathlib import Path
from typing import List, Tuple

from clang.cindex import Index, CursorKind, TranslationUnit, Cursor


# ---------- 基础工具 ----------

def _find_project_root(src: Path) -> Path:
    """从 seed.c 一路往上找，直到看到 runtime 目录为止。"""
    cur = src.parent
    while True:
        runtime_dir = cur / "src" / "csmithedge" / "csmith" / "runtime"
        if runtime_dir.is_dir():
            return cur
        if cur.parent == cur:
            return src.parent
        cur = cur.parent


def _parse_tu(in_path: str) -> TranslationUnit:
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


# ---------- AST 收集 ----------

def _walk(node: Cursor, depth: int = 0):
    yield node, depth
    for c in node.get_children():
        yield from _walk(c, depth + 1)


def _collect_functions(tu: TranslationUnit, in_path: str) -> List[Cursor]:
    """
    收集 FUNCTION_DECL：
      1）先尽量只选属于 seed_0001.c 的函数（通过“文件名相等”判断）；
      2）如果找不到，就退回到“TU 里所有 FUNCTION_DECL 都要”，确保不会空。
    """
    seed_name = Path(in_path).name

    all_funcs: List[Cursor] = []
    main_file_funcs: List[Cursor] = []

    for node, depth in _walk(tu.cursor, 0):
        if node.kind != CursorKind.FUNCTION_DECL:
            continue
        if not node.spelling:
            continue

        loc_file = node.location.file
        file_name = loc_file.name if loc_file is not None else "<none>"

        all_funcs.append(node)

        print(
            f"[debug] FUNCTION_DECL '{node.spelling}' "
            f"at {file_name}:{node.location.line}"
        )

        # 用文件“名字”来判断是不是 seed 文件
        if loc_file is not None and Path(file_name).name == seed_name:
            main_file_funcs.append(node)

    print(f"[debug] total FUNCTION_DECL in TU: {len(all_funcs)}")
    print(
        f"[debug] FUNCTION_DECL with file name == '{seed_name}': "
        f"{len(main_file_funcs)}"
    )

    if main_file_funcs:
        return main_file_funcs

    # 如果按文件名也一个都找不到，就退回到“所有 FUNCTION_DECL”
    if all_funcs:
        print(
            "[debug] no FUNCTION_DECL whose file name matches seed; "
            "fallback to using ALL FUNCTION_DECL in TU"
        )
        return all_funcs

    # 真正完全没有函数
    return []


def _collect_deep_compounds(tu: TranslationUnit, in_path: str) -> List[Cursor]:
    """
    收集嵌套较深的 COMPOUND_STMT，用于“深埋”嵌套声明。
    这里也用文件名匹配，匹配不到就退回：所有 COMPOUND_STMT 都算。
    """
    seed_name = Path(in_path).name

    all_blocks: List[Cursor] = []
    main_blocks: List[Cursor] = []

    for node, depth in _walk(tu.cursor, 0):
        if node.kind != CursorKind.COMPOUND_STMT:
            continue
        if depth < 3:
            continue

        loc_file = node.location.file
        file_name = loc_file.name if loc_file is not None else "<none>"

        all_blocks.append(node)

        if loc_file is not None and Path(file_name).name == seed_name:
            main_blocks.append(node)

    print(f"[debug] deep COMPOUND_STMT (depth>=3) total: {len(all_blocks)}")
    print(
        f"[debug] deep COMPOUND_STMT in seed file '{seed_name}': "
        f"{len(main_blocks)}"
    )

    if main_blocks:
        return main_blocks
    return all_blocks


# ---------- 源码操作 ----------

def _find_decl_end(code: str, start_off: int) -> int:
    """
    从函数声明/定义的起始 offset 出发，找到它的“头部”结束位置：
      - 定义：找到第一个 '{'，返回其位置+1
      - 纯声明：找到第一个 ';'，返回其位置+1
    优先取更靠前的那个。
    """
    idx_semi = code.find(";", start_off)
    idx_lbrace = code.find("{", start_off)

    candidates = [i for i in (idx_semi, idx_lbrace) if i != -1]
    if not candidates:
        return start_off
    return min(candidates) + 1


def _build_proto_from_head(head_src: str) -> str:
    """
    给定函数头部源码（可能以 ';' 结尾，也可能以 '{' 结尾），
    统一变成一个以 ';' 结尾的“原型文本”。
    """
    s = head_src.strip()
    if s.endswith("{"):
        s = s[:-1].rstrip()
    if not s.endswith(";"):
        s = s + ";"
    return s


def _build_variants(proto_src: str) -> dict:
    """
    对一个“单行函数原型”构造 3 种 storage-class 变体：
      none   : 不加 static/extern
      static : static
      extern : extern
    保留原来的缩进和其它部分。
    """
    m = re.match(r"(\s*)(.*)", proto_src)
    if not m:
        indent = ""
        body = proto_src.strip()
    else:
        indent = m.group(1)
        body = m.group(2).strip()

    # 去掉已有 static/extern 以免叠加
    body = re.sub(r"^(?:static|extern)\s+", "", body)

    core = body
    none = f"{indent}{core}"
    static = f"{indent}static {core}"
    extern = f"{indent}extern {core}"

    return {"none": none, "static": static, "extern": extern}


# ---------- 变异主逻辑 ----------

def insert_conflicting_func_decl(in_path: str, out_path: str) -> bool:
    """
    高级变异：针对函数的 storage-class 做“乱变 + 深埋冲突”。

    策略（针对 1~3 个随机函数）：
      1. 利用函数的头部源码构造一个“原型文本”；
      2. 在函数前插入两行互相 storage-class 冲突的声明：
           uint32_t func_1(void);
           static uint32_t func_1(void);
         或者其他组合（none/static/extern 随机选）；
      3. 再在一个深层 COMPOUND_STMT 里插入一条同名原型（storage-class 随机），
         形成“深度嵌套 + 多点冲突”。

    全程只修改源码字符串，不依赖 AST 重写。
    """
    src_path = Path(in_path)
    code = src_path.read_text(encoding="utf-8")

    tu = _parse_tu(in_path)

    funcs = _collect_functions(tu, in_path)
    if not funcs:
        print("[debug] no FUNCTION_DECL at all; skip conflicting decl mutation")
        return False

    deep_blocks = _collect_deep_compounds(tu, in_path)
    # deep_blocks 可以为空，只是少了嵌套部分，不影响顶层冲突

    # 从所有函数里随机挑 1~3 个，避免只动第一个
    max_targets = 3
    target_count = min(max_targets, max(1, len(funcs) // 4))  # 大约 25%，上限 3
    targets = random.sample(funcs, k=target_count)

    print(
        "[debug] selected target functions: "
        + ", ".join(
            f"{c.spelling}@{c.location.file.name if c.location.file else '<none>'}:{c.location.line}"
            for c in targets
        )
    )

    modifications: List[Tuple[int, int, str]] = []

    for func in targets:
        start_off = func.extent.start.offset
        if start_off is None or start_off < 0 or start_off >= len(code):
            print(f"[debug] skip {func.spelling}: invalid start offset {start_off}")
            continue

        head_end = _find_decl_end(code, start_off)
        if head_end <= start_off or head_end > len(code):
            print(f"[debug] skip {func.spelling}: failed to locate head end")
            continue

        head_src = code[start_off:head_end]
        proto = _build_proto_from_head(head_src)
        variants = _build_variants(proto)

        # 几种 storage-class 冲突组合，保证不一致
        patterns = [
            ("none", "static"),
            ("static", "none"),
            ("extern", "static"),
            ("static", "extern"),
        ]
        first_key, second_key = random.choice(patterns)
        first_decl = variants[first_key]
        second_decl = variants[second_key]

        conflict_block = (
            f"\n/* edgm: conflicting decls for {func.spelling} */\n"
            f"{first_decl}\n"
            f"{second_decl}\n"
        )

        print(
            f"[debug] conflict at file-scope for '{func.spelling}': "
            f"{first_key} vs {second_key} (line {func.location.line})"
        )

        # 在函数头部之前插入冲突声明（纯插入，不替换）
        modifications.append((start_off, start_off, conflict_block))

        # 再搞一个深度嵌套的 block 声明（如果有的话）
        if deep_blocks:
            block = random.choice(deep_blocks)
            block_start = block.extent.start.offset or 0
            insert_pos = min(block_start + 1, len(code))

            nested_key = random.choice(["none", "static", "extern"])
            nested_decl = variants[nested_key]

            nested_text = (
                "\n    /* edgm: nested conflicting decl */\n"
                "    " + nested_decl.strip() + "\n"
            )

            print(
                f"[debug]   nested {nested_key} decl for '{func.spelling}' "
                f"inside block at line {block.location.line}"
            )

            modifications.append((insert_pos, insert_pos, nested_text))

    if not modifications:
        print("[debug] no effective modifications generated; skip mutation")
        return False

    # 从后往前应用修改，避免 offset 失效
    modifications.sort(key=lambda x: x[0], reverse=True)
    new_code = code
    for start, end, text in modifications:
        new_code = new_code[:start] + text + new_code[end:]

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(new_code, encoding="utf-8")

    print(f"[结果] 已生成变异文件: {out_p}")
    return True
