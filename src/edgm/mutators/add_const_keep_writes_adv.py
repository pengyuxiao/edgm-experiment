# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict


C_KEYWORDS = {
    "auto","break","case","char","const","continue","default","do","double","else","enum",
    "extern","float","for","goto","if","inline","int","long","register","restrict","return",
    "short","signed","sizeof","static","struct","switch","typedef","union","unsigned","void",
    "volatile","while","_Alignas","_Alignof","_Atomic","_Bool","_Complex","_Generic",
    "_Imaginary","_Noreturn","_Static_assert","_Thread_local",
}

# 你想“三种情况都有概率出现”，这里就是真正控制概率的地方
MODE_PRIORS: Dict[str, float] = {
    "nonptr": 0.34,
    "ptr_const": 0.33,        # T* const p（写 p 会炸）
    "pointee_const": 0.33,    # const T* p（写 *p / p[i] / p->f 会炸）
}


@dataclass
class FuncRange:
    name: str
    start: int
    header_end: int
    end: int


@dataclass
class Candidate:
    func: str
    decl_line: int
    name: str
    is_pointer: bool
    direct_writes: int
    deref_writes: int
    first_write_line: Optional[int]
    score: int
    init_complex: bool


# ---------------- I/O ----------------

def _read_text(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def _write_text(p: str, s: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(s, encoding="utf-8")


# ---------------- helpers ----------------

def _strip_comments_line(s: str) -> str:
    s = re.sub(r"//.*$", "", s)
    s = re.sub(r"/\*.*?\*/", "", s)
    return s


def _is_blank_or_pp(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


def _split_newline(line: str) -> Tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


# ---------------- function discovery ----------------

def _find_functions(lines: List[str]) -> List[FuncRange]:
    funcs: List[FuncRange] = []
    n = len(lines)
    i = 0

    hdr_inline = re.compile(
        r"^\s*(static\s+)?[A-Za-z_][\w\s\*\(\)]*\b(func_\d+|main)\s*\([^;]*\)\s*\{"
    )
    hdr_line = re.compile(
        r"^\s*(static\s+)?[A-Za-z_][\w\s\*\(\)]*\b(func_\d+|main)\s*\([^;]*\)\s*$"
    )

    while i < n:
        ln = _strip_comments_line(lines[i])

        m1 = hdr_inline.match(ln)
        if m1:
            name = m1.group(2)
            start = i
            header_end = i
            brace = 0
            started = False
            j = i
            while j < n:
                cur = _strip_comments_line(lines[j])
                for ch in cur:
                    if ch == "{":
                        brace += 1
                        started = True
                    elif ch == "}":
                        brace -= 1
                if started and brace == 0:
                    funcs.append(FuncRange(name=name, start=start, header_end=header_end, end=j))
                    i = j + 1
                    break
                j += 1
            else:
                i += 1
            continue

        m2 = hdr_line.match(ln)
        if m2:
            name = m2.group(2)
            k = i + 1
            while k < n and _is_blank_or_pp(_strip_comments_line(lines[k])):
                k += 1
            if k < n and _strip_comments_line(lines[k]).lstrip().startswith("{"):
                start = i
                header_end = k
                brace = 0
                started = False
                j = k
                while j < n:
                    cur = _strip_comments_line(lines[j])
                    for ch in cur:
                        if ch == "{":
                            brace += 1
                            started = True
                        elif ch == "}":
                            brace -= 1
                    if started and brace == 0:
                        funcs.append(FuncRange(name=name, start=start, header_end=header_end, end=j))
                        i = j + 1
                        break
                    j += 1
                else:
                    i += 1
                continue

        i += 1

    return funcs


# ---------------- declaration parsing ----------------

def _extract_decl_name(head: str) -> Optional[str]:
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b\s*(?:\[|=|$)", head)
    if not m:
        return None
    nm = m.group(1)
    if nm in C_KEYWORDS:
        return None
    return nm


def _is_simple_decl_line(line: str) -> bool:
    s = _strip_comments_line(line).strip()
    if not s.endswith(";"):
        return False
    if s.startswith("#"):
        return False
    semi = s.find(";")
    head = s[:semi]
    if "(" in head and "=" not in head:
        if not re.search(r"\(\s*\*\s*[A-Za-z_]\w*\s*\)", head):
            return False
    return True


def _decl_contains_const_anywhere(line: str) -> bool:
    # 关键：避免挑到已经 const 的声明，然后 rewrite 失败导致整轮 skip
    return bool(re.search(r"\bconst\b", _strip_comments_line(line)))


def _parse_decl_for_candidate(line: str) -> Optional[Tuple[str, bool, bool]]:
    if not _is_simple_decl_line(line):
        return None

    s = _strip_comments_line(line).strip()
    semi = s.find(";")
    head = s[:semi]

    if head.lstrip().startswith(("return ", "goto ", "break", "continue", "if ", "for ", "while ", "switch ")):
        return None

    if "=" in head:
        left = head.split("=", 1)[0]
        if re.fullmatch(r"\s*[A-Za-z_]\w*\s*", left) is not None:
            return None

    nm = _extract_decl_name(head)
    if not nm:
        return None

    name_pos = head.rfind(nm)
    is_ptr = ("*" in head[:name_pos])

    init_complex = False
    if "=" in head:
        rhs = head.split("=", 1)[1]
        if "(" in rhs:
            init_complex = True

    return nm, is_ptr, init_complex


# ---------------- write detection ----------------

_ASSIGN_OPS = r"(?:=|\+=|-=|\*=|/=|%=|<<=|>>=|&=|\|=|\^=)"


def _count_write_sites(lines: List[str], start: int, end: int, name: str) -> Tuple[int, int, Optional[int]]:
    pat_direct_assign = re.compile(rf"\b{re.escape(name)}\b\s*{_ASSIGN_OPS}")
    pat_direct_inc1 = re.compile(rf"(?:\+\+|--)\s*\b{re.escape(name)}\b")
    pat_direct_inc2 = re.compile(rf"\b{re.escape(name)}\b\s*(?:\+\+|--)")

    pat_deref_assign = re.compile(rf"\*\s*\b{re.escape(name)}\b\s*{_ASSIGN_OPS}")
    pat_deref_inc1 = re.compile(rf"(?:\+\+|--)\s*\*\s*\b{re.escape(name)}\b")
    pat_deref_inc2 = re.compile(rf"\*\s*\b{re.escape(name)}\b\s*(?:\+\+|--)")

    pat_sub_assign = re.compile(rf"\b{re.escape(name)}\b\s*\[[^\]]+\]\s*{_ASSIGN_OPS}")
    pat_sub_inc1 = re.compile(rf"(?:\+\+|--)\s*\b{re.escape(name)}\b\s*\[[^\]]+\]")
    pat_sub_inc2 = re.compile(rf"\b{re.escape(name)}\b\s*\[[^\]]+\]\s*(?:\+\+|--)")

    pat_arrow_assign = re.compile(rf"\b{re.escape(name)}\b\s*->\s*[A-Za-z_]\w*\s*{_ASSIGN_OPS}")
    pat_arrow_inc1 = re.compile(rf"(?:\+\+|--)\s*\b{re.escape(name)}\b\s*->\s*[A-Za-z_]\w*")
    pat_arrow_inc2 = re.compile(rf"\b{re.escape(name)}\b\s*->\s*[A-Za-z_]\w*\s*(?:\+\+|--)")

    direct = 0
    deref = 0
    first: Optional[int] = None

    for i in range(start, end + 1):
        ln = _strip_comments_line(lines[i])
        if ln.lstrip().startswith("#"):
            continue

        d = 0
        d += len(pat_direct_assign.findall(ln))
        d += len(pat_direct_inc1.findall(ln))
        d += len(pat_direct_inc2.findall(ln))
        if d:
            direct += d
            if first is None:
                first = i

        r = 0
        r += len(pat_deref_assign.findall(ln))
        r += len(pat_deref_inc1.findall(ln))
        r += len(pat_deref_inc2.findall(ln))
        r += len(pat_sub_assign.findall(ln))
        r += len(pat_sub_inc1.findall(ln))
        r += len(pat_sub_inc2.findall(ln))
        r += len(pat_arrow_assign.findall(ln))
        r += len(pat_arrow_inc1.findall(ln))
        r += len(pat_arrow_inc2.findall(ln))
        if r:
            deref += r
            if first is None:
                first = i

    return direct, deref, first


# ---------------- const insertion ----------------

def _insert_const_nonptr(line: str) -> str:
    body, nl = _split_newline(line)
    if re.search(r"\bconst\b", body):
        return line

    m = re.match(r"^(\s*)(?:(static|extern|register)\s+)?(.*)$", body)
    if not m:
        return "const " + body + nl

    indent, storage, rest = m.group(1), m.group(2), m.group(3)
    if storage:
        return f"{indent}{storage} const {rest}{nl}"
    return f"{indent}const {rest}{nl}"


def _insert_const_pointee(line: str, name: str) -> str:
    body, nl = _split_newline(line)
    if re.search(r"\bconst\b", body):
        return line

    semi = body.find(";")
    head = body if semi < 0 else body[:semi]

    name_pos = head.rfind(name)
    if name_pos < 0:
        return line

    star_pos = head.find("*")
    if star_pos < 0 or star_pos > name_pos:
        return line

    m = re.match(r"^(\s*)(?:(static|extern|register)\s+)?(.*)$", body)
    if not m:
        return "const " + body + nl

    indent, storage, rest = m.group(1), m.group(2), m.group(3)
    if storage:
        return f"{indent}{storage} const {rest}{nl}"
    return f"{indent}const {rest}{nl}"


def _insert_const_pointer_itself(line: str, name: str) -> str:
    body, nl = _split_newline(line)
    semi = body.find(";")
    head = body if semi < 0 else body[:semi]

    m = re.search(rf"\b{re.escape(name)}\b", head)
    if not m:
        return line

    prefix = body[:m.start()]
    suffix = body[m.start():]

    last_star = prefix.rfind("*")
    if last_star < 0:
        return line

    # 如果 already "* const" 就别再插
    if re.search(r"\*\s*const\b", prefix[last_star:]):
        return line

    out = prefix[:last_star + 1] + " const" + prefix[last_star + 1:] + suffix
    return out + nl


# ---------------- scoring ----------------

def _score_candidate(is_ptr: bool, direct: int, deref: int, init_complex: bool) -> int:
    writes = direct + deref
    if writes <= 0:
        return -10**9

    base = writes * 10 if not is_ptr else writes * 8

    purity_bonus = 0
    if is_ptr:
        if direct > 0 and deref == 0:
            purity_bonus += 25
        elif deref > 0 and direct == 0:
            purity_bonus += 25
        else:
            purity_bonus += 5
    else:
        if direct > 0 and deref == 0:
            purity_bonus += 15

    explosion_penalty = max(0, writes - 6) * 4
    init_penalty = 10 if init_complex else 0

    return base + purity_bonus - explosion_penalty - init_penalty


def _collect_candidates(lines: List[str]) -> List[Candidate]:
    funcs = _find_functions(lines)
    out: List[Candidate] = []

    for fr in funcs:
        for i in range(fr.header_end + 1, fr.end + 1):
            if _decl_contains_const_anywhere(lines[i]):
                continue  # 关键：直接不选 already-const 的 decl

            parsed = _parse_decl_for_candidate(lines[i])
            if not parsed:
                continue
            name, is_ptr, init_complex = parsed
            if name.startswith("edgm_"):
                continue

            direct, deref, first = _count_write_sites(lines, i + 1, fr.end, name)
            if direct == 0 and deref == 0:
                continue

            score = _score_candidate(is_ptr, direct, deref, init_complex)
            if score < 0:
                continue

            out.append(Candidate(
                func=fr.name,
                decl_line=i,
                name=name,
                is_pointer=is_ptr,
                direct_writes=direct,
                deref_writes=deref,
                first_write_line=first,
                score=score,
                init_complex=init_complex,
            ))
    return out


def _cands_for_mode(cands: List[Candidate], mode: str) -> List[Candidate]:
    if mode == "nonptr":
        return [c for c in cands if (not c.is_pointer) and (c.direct_writes + c.deref_writes) > 0]
    if mode == "ptr_const":
        return [c for c in cands if c.is_pointer and c.direct_writes > 0]
    if mode == "pointee_const":
        return [c for c in cands if c.is_pointer and c.deref_writes > 0]
    return cands


def _pick_mode(rng: random.Random) -> str:
    modes = list(MODE_PRIORS.keys())
    weights = [MODE_PRIORS[m] for m in modes]
    return rng.choices(modes, weights=weights, k=1)[0]


def _pick_from_topk_weighted(rng: random.Random, items: List[Candidate], k: int = 12) -> Candidate:
    items = sorted(items, key=lambda x: x.score, reverse=True)
    pool = items[:max(1, min(k, len(items)))]
    min_s = min(c.score for c in pool)
    weights = [(c.score - min_s + 1) for c in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


# ---------------- main entry ----------------

def insert_add_const_keep_writes_adv(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    cands = _collect_candidates(lines)
    if not cands:
        _write_text(out_path, text)
        print("[debug] C-const-adv+++: no suitable candidate; skip")
        return False

    # 关键：失败就自动换下一个候选，不要整轮 skip
    tried = set()
    for _ in range(min(40, len(cands))):
        mode = _pick_mode(rng)
        pool = _cands_for_mode([c for c in cands if c.decl_line not in tried], mode)
        if not pool:
            pool = [c for c in cands if c.decl_line not in tried]
        if not pool:
            break

        picked = _pick_from_topk_weighted(rng, pool, k=12)
        tried.add(picked.decl_line)

        tag = rng.randint(10000, 99999)
        li = picked.decl_line
        old = lines[li]

        if mode == "nonptr":
            new = _insert_const_nonptr(old)
            note = f"/* edgm C-const-adv+++: make `{picked.name}` const but keep later writes (tag={tag}) */\n"
        elif mode == "ptr_const":
            new = _insert_const_pointer_itself(old, picked.name)
            note = f"/* edgm C-const-adv+++: make pointer `{picked.name}` const (T* const p), keep later writes (tag={tag}) */\n"
        else:
            new = _insert_const_pointee(old, picked.name)
            note = f"/* edgm C-const-adv+++: make pointee of `{picked.name}` const (const T* p), keep later writes (tag={tag}) */\n"

        if new == old:
            # 这次候选对该 mode 不能改，继续换下一个
            continue

        lines[li] = note + new
        _write_text(out_path, "".join(lines))

        print(f"[debug] C-const-adv+++: seed={seed}")
        print(f"[debug] C-const-adv+++: picked func={picked.func}, var={picked.name}, decl_line={li+1}, tag={tag}")
        print(f"[debug] C-const-adv+++: mode={mode}, score={picked.score}")
        print(f"[debug] C-const-adv+++: direct_writes={picked.direct_writes}, deref_writes={picked.deref_writes}, init_complex={picked.init_complex}")
        print(f"[debug] C-const-adv+++: first_write_line={(picked.first_write_line+1) if picked.first_write_line is not None else None}")
        return True

    _write_text(out_path, text)
    print("[debug] C-const-adv+++: all candidates failed to rewrite safely; skip")
    return False
