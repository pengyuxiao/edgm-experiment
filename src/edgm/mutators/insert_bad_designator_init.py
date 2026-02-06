# src/edgm/mutators/insert_bad_designator_init.py
from __future__ import annotations

import os
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class MutationResult:
    ok: bool
    reason: str = ""
    seed: Optional[int] = None


# -----------------------------
# Helpers: small, safe text ops
# -----------------------------

_WS = re.compile(r"[ \t]+")


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _find_all(pattern: str, s: str, flags: int = 0):
    return list(re.finditer(pattern, s, flags))


def _is_c_ident(x: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_]\w*", x))


def _typo_field_name(good: str, rng: random.Random) -> str:
    """
    Make a plausible "human typo" from an existing field name.
    Avoid edgm_bad_* style.
    """
    if not good:
        return "x"

    ops = ["drop", "swap", "flip", "append", "insert"]
    op = rng.choice(ops)

    if op == "drop" and len(good) >= 2:
        i = rng.randrange(len(good))
        return good[:i] + good[i + 1 :]
    if op == "swap" and len(good) >= 2:
        i = rng.randrange(len(good) - 1)
        return good[:i] + good[i + 1] + good[i] + good[i + 2 :]
    if op == "flip":
        # flip one char (0<->O, 1<->l, etc.) if possible
        table = {"0": "O", "O": "0", "1": "l", "l": "1", "f": "F", "F": "f"}
        i = rng.randrange(len(good))
        c = good[i]
        c2 = table.get(c, chr(((ord(c) - 32) % 95) + 32))
        return good[:i] + c2 + good[i + 1 :]
    if op == "append":
        return good + rng.choice(["_", "__", "0", "1", "x"])
    if op == "insert":
        i = rng.randrange(len(good) + 1)
        return good[:i] + rng.choice(["_", "x", "0"]) + good[i:]
    return good + "_"


def _nonice_index_expr(base_ident: str, rng: random.Random) -> str:
    """
    Return a *non-ICE* array designator index expression.
    Must NOT be constant, and must NOT be the same form every time.
    It is OK if it sometimes evaluates to 0 at runtime; what's required is
    that it is NOT a constant expression.
    """
    k1 = rng.randint(1, 7)
    k2 = rng.randint(8, 31)
    k3 = rng.randint(2, 9)
    # Weighted patterns: "constant-ish" but still depends on an identifier.
    patterns = [
        (10, f"({base_ident})"),
        (10, f"(({base_ident}) + {k1})"),
        (10, f"(({base_ident}) - {k1})"),
        (12, f"(({base_ident}) & {k2})"),
        (12, f"(({base_ident}) | {k1})"),
        (10, f"(({base_ident}) ^ {k2})"),
        (12, f"(({base_ident}) % {k3})"),
        (12, f"(({base_ident}) ? {k1} : {k2})"),
        (8,  f"(({base_ident}) < {k2} ? {k1} : {k2})"),
        # Make front-ends disagree about simplification / parentheses / notes.
        (8,  f"(({base_ident}) + ({k1} * ({base_ident})))"),
        (6,  f"(({base_ident}) && {k1})"),
    ]
    bag = []
    for w, p in patterns:
        bag.extend([p] * w)
    return rng.choice(bag)


def _const_oob_index(array_len: int, rng: random.Random) -> int:
    """
    Choose a constant out-of-bounds index.
    """
    # Make it not trivially +1 every time
    bump = rng.choice([1, 2, 3, 4, 8, 16])
    return array_len + bump


# -----------------------------------------
# Core: mutate a local InitListExpr in code
# -----------------------------------------

def insert_bad_designator_init(in_path: str, out_path: str, seed: Optional[int] = None) -> MutationResult:
    """
    Advanced I15:
      - Prefer local array initlists and rewrite one existing element into a designator element
        with either:
          (a) non-ICE index expression (probabilistic, many forms), or
          (b) constant out-of-bounds index (also probabilistic).
      - If only struct initlists are found, generate a plausible typo field name from an existing one.
    This is purely source-to-source, text-based, designed for fuzzing diagnostics.
    """
    ip = Path(in_path)
    op = Path(out_path)
    op.parent.mkdir(parents=True, exist_ok=True)

    if seed is None:
        seed = random.randrange(1, 2**31 - 1)
    rng = random.Random(seed)

    src = _read_text(ip)

    # Heuristic: find local array declarations with brace init:
    #   <type> <name>[<N>] = { ... };
    # We intentionally keep it heuristic to avoid overfitting to one generator.
    # Match N as an integer literal only (so we can do constant OOB option).
    arr_decl_re = r"""
        (?P<lead>^[ \t]*)
        (?P<type>(?:const[ \t]+)?[A-Za-z_]\w*(?:[ \t\*]+[A-Za-z_]\w*)*[ \t\*]*)
        (?P<name>[A-Za-z_]\w*)
        \[
            (?P<n>\d+)
        \]
        [ \t]*=[ \t]*
        \{
            (?P<init>[^;]*?)
        \}
        [ \t]*;
    """
    arr_matches = _find_all(arr_decl_re, src, flags=re.M | re.S | re.X)

    # Filter out suspicious global-scope arrays by requiring we're inside a function:
    # crude: require a '{' earlier on the same or previous lines with some indentation.
    candidates = []
    for m in arr_matches:
        start = m.start()
        prefix = src[max(0, start - 2000):start]
        # naive "inside function" check: seen a ')' then '{' more recently than a ';' that ends globals
        if prefix.rfind("{") > prefix.rfind(";\n") and prefix.rfind(")") > prefix.rfind(";\n"):
            candidates.append(m)

    # If we have array initlists, mutate one.
    if candidates:
        m = rng.choice(candidates)
        name = m.group("name")
        n = int(m.group("n"))
        init = m.group("init")

        # Split initializer items at top-level commas (very simple brace-level tracking).
        items = []
        buf = []
        depth = 0
        for ch in init:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                items.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        tail = "".join(buf).strip()
        if tail:
            items.append(tail)

        # If no items, bail.
        if not items:
            shutil.copy2(ip, op)
            return MutationResult(ok=False, reason="no initializer items", seed=seed)

        # Choose one existing element to rewrite (avoid adding an 11th element).
        pos = rng.randrange(len(items))
        original = items[pos]

        # Pick an in-scope identifier for non-ICE index:
        # Prefer function parameters in that function: search backwards for "(...)" near last "func_".
        # Fallback: use a known local name-like token from nearby text.
        # This is heuristic but works well on csmithedge/csmith style.
        window = src[max(0, m.start() - 1500):m.start()]
        idents = re.findall(r"\b([A-Za-z_]\w*)\b", window)
        # exclude keywords + the array name itself
        kw = {
            "if","for","while","do","switch","case","default","return","goto","break","continue",
            "int","char","short","long","signed","unsigned","float","double","void","struct","union",
            "const","volatile","static","extern","register","auto","typedef","sizeof"
        }
        pool = [x for x in idents if x not in kw and x != name and _is_c_ident(x)]
        base_ident = rng.choice(pool) if pool else "argc"

        # Decide strategy probabilistically:
        # - 65%: non-ICE index (many forms)
        # - 35%: constant out-of-bounds
        if rng.random() < 0.65:
            idx_expr = _nonice_index_expr(base_ident, rng)
            # keep element type stable: use original RHS
            new_item = f"[{idx_expr}] = {original} /* edgm I15-adv */"
        else:
            oob = _const_oob_index(n, rng)
            new_item = f"[{oob}] = {original} /* edgm I15-adv */"

        items[pos] = new_item

        # Rebuild initializer (preserve indentation roughly)
        lead = m.group("lead")
        joined = (",\n" + lead + "    ").join(items)
        new_decl = (
            f"{m.group('lead')}{m.group('type')}{m.group('name')}[{m.group('n')}] = {{\n"
            f"{m.group('lead')}    {joined}\n"
            f"{m.group('lead')}}};"
        )

        mutated = src[:m.start()] + new_decl + src[m.end():]
        _write_text(op, mutated)
        return MutationResult(ok=True, reason="mutated array initlist", seed=seed)

    # Otherwise: try struct compound literal / initlists:
    # find patterns like: (struct S0){ .f0 = ..., ... }
    struct_lit_re = r"""
        \(\s*struct\s+(?P<tag>[A-Za-z_]\w*)\s*\)\s*
        \{\s*(?P<body>[^}]*?)\s*\}
    """
    lits = _find_all(struct_lit_re, src, flags=re.S | re.X)
    if lits:
        m = rng.choice(lits)
        body = m.group("body")

        # Find one existing designator ".field"
        fields = re.findall(r"\.\s*([A-Za-z_]\w*)\s*=", body)
        if fields:
            good = rng.choice(fields)
            bad = _typo_field_name(good, rng)
        else:
            good = "f0"
            bad = _typo_field_name(good, rng)

        # Inject a new bad field assignment near the start (do not rely on edgm_bad).
        insertion = f".{bad} = (0 ? 1 : 2), "
        new_body = insertion + body

        mutated = src[:m.start("body")] + new_body + src[m.end("body"):]
        _write_text(op, mutated)
        return MutationResult(ok=True, reason="mutated struct designator", seed=seed)

    shutil.copy2(ip, op)
    return MutationResult(ok=False, reason="no suitable initlist found", seed=seed)
