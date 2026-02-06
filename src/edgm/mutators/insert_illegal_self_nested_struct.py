#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import shutil
import random
from typing import List, Optional, Tuple


def _pick_tag_base(src: str, rng: random.Random) -> str:
    # pick from existing struct tags to look "natural"
    tags = re.findall(r"\bstruct\s+([A-Za-z_]\w*)\s*\{", src)
    if tags:
        return rng.choice(tags)
    return "S0"


def _find_anchor_offsets(src: str) -> List[int]:
    # Prefer after "__undefined" if present, else after csmith include, else start.
    anchors: List[int] = []

    m = re.search(r"^\s*static\s+long\s+__undefined\s*;\s*$", src, flags=re.M)
    if m:
        anchors.append(m.end())

    m2 = re.search(r'^\s*#include\s+"csmith\.h"\s*$', src, flags=re.M)
    if m2:
        anchors.append(m2.end())

    # Also consider just before struct decl banner if present
    m3 = re.search(r"^\s*/\*\s*---\s*Struct/Union Declarations\s*---\s*\*/\s*$", src, flags=re.M)
    if m3:
        anchors.append(m3.start())

    if not anchors:
        anchors.append(0)
    return anchors


def _gen_payload(tag_base: str, uid: int, rng: random.Random) -> str:
    """
    Generate one of several high-confusion variants with the same "incomplete member" core.
    """
    tag = f"{tag_base}_I18_{uid}"

    variants = [
        "TYPEOF_UNION_SELF",      # self-nesting via typeof + union
        "UNION_INCOMPLETE_LATE",  # wrapper refers to incomplete tag; define tag later
        "TYPEDEF_MACRO_SELF",     # classic self nesting but buried in typedef/macro
    ]
    # weighted choice: favor the most "diagnostic-sensitive"
    pick = rng.choices(variants, weights=[0.55, 0.25, 0.20], k=1)[0]

    if pick == "TYPEOF_UNION_SELF":
        return f"""
/* edgm I18-adv: incomplete-type member hidden behind typedef+macro+typeof+union */
struct {tag};                      /* forward decl (incomplete) */

typedef struct {tag} {tag}_t0;
typedef {tag}_t0 {tag}_t1;
typedef {tag}_t1 {tag}_t2;

/* global pointer to incomplete type (never evaluated, only used for typeof) */
static {tag}_t2 *{tag}_p = (void*)0;

/* Macro wrapper: some front-ends anchor diagnostics at macro-expansion sites */
#define {tag}_DEREF_TYPE(P) __typeof__(*(P))
typedef {tag}_DEREF_TYPE({tag}_p) {tag}_deref_t;

#define {tag}_WRAP(T) T

struct {tag} {{
    int f0;
    unsigned f1;

    union {{
        unsigned long pad;
        /* illegal: non-pointer member of incomplete type (still incomplete here) */
        {tag}_WRAP({tag}_deref_t) next;
    }} u;
}};

#undef {tag}_WRAP
#undef {tag}_DEREF_TYPE

"""

    if pick == "UNION_INCOMPLETE_LATE":
        # Forward declare, use as illegal union member in wrapper, then *define* it later
        # to potentially trigger extra notes / different diag ordering.
        wrap = f"{tag}_Wrap"
        return f"""
/* edgm I18-adv: union member uses forward-declared tag; tag defined later (diag-order sensitive) */
struct {tag}; /* forward */

typedef struct {tag} {tag}_t0;
typedef {tag}_t0 {tag}_t1;

#define {tag}_AS(T) T

struct {wrap} {{
    int f0;
    union {{
        long pad;
        /* illegal: incomplete type as non-pointer union member */
        {tag}_AS(struct {tag}) member;
    }} u;
}};

#undef {tag}_AS

/* late definition (cannot "fix" the earlier error, but may change notes/paths) */
struct {tag} {{
    int f0;
    unsigned f1;
}};

"""

    # TYPEDEF_MACRO_SELF: classic self-nesting but heavily wrapped
    return f"""
/* edgm I18-adv: self-nesting via typedef chain + macro indirection */
typedef struct {tag} {tag}_t0;
typedef {tag}_t0 {tag}_t1;
typedef {tag}_t1 {tag}_t2;

#define {tag}_T(T) T

struct {tag} {{
    int f0;
    unsigned f1;
    /* illegal: member has incomplete type (not pointer), hidden via macro+typedef chain */
    {tag}_T({tag}_t2) next;
}};

#undef {tag}_T

"""


def insert_illegal_self_nested_struct(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)

    with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    tag_base = _pick_tag_base(src, rng)
    uid = rng.randint(10000, 99999)

    anchors = _find_anchor_offsets(src)
    insert_at = rng.choice(anchors)

    payload = _gen_payload(tag_base, uid, rng)

    mutated = src[:insert_at] + payload + src[insert_at:]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(mutated)

    return True
