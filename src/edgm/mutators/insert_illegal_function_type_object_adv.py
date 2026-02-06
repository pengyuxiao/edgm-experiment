# -*- coding: utf-8 -*-
"""
I17-adv++: illegal function-type objects (diagnostic-divergence oriented)

Upgrades vs I17-adv:
  1) Extract function type via an *attributed function-pointer typedef* + typeof(*fp)
     (warn_unused_result/pure attributes often shift/alter diagnostics across front-ends).
  2) Hide illegal function-type members inside anonymous struct/union (GNU/C11-ish extension guarded).
  3) Add qualifier-confusion (const/volatile) on function types (illegal/ignored/diagnostic-sensitive).
  4) Keep macro wrappers so some front-ends anchor diagnostics at macro expansion sites.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Optional, List, Tuple


_RE_PROTO = re.compile(
    r"^\s*static\b.*?\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*;\s*$"
)

# Avoid picking helper/static inline etc; in Csmith-style seeds we mainly want func_N
_RE_FUNCNAME_OK = re.compile(r"^func_\d+$")


def _read_text(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def _write_text(p: str, s: str) -> None:
    out = Path(p)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(s, encoding="utf-8")


def _find_prototypes(lines: List[str]) -> List[Tuple[int, str]]:
    """Return list of (line_idx, func_name) for candidate prototypes."""
    cands: List[Tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = _RE_PROTO.match(ln)
        if not m:
            continue
        name = m.group("name")
        if not _RE_FUNCNAME_OK.match(name):
            continue
        # skip if it's already an edgm injected redecl
        if "edgm_i17" in ln:
            continue
        cands.append((i, name))
    return cands


def _make_payload(tag: int, target_fn: str) -> str:
    T = tag
    FN = target_fn

    # Notes:
    # - `typedef __typeof__(&FN) ...` yields pointer-to-function type.
    # - Decorating that typedef with function attributes is diagnostic-sensitive.
    # - Using `__typeof__(*(fp))` extracts the *function type* (not pointer).
    # - Anonymous struct/union members are guarded; fallback uses named members.

    payload = f"""\
/* edgm I17-adv++: illegal function-type objects via attr+typeof+typedef-chain+anon-members+qualifiers (tag={T}) */
enum edgm_i17_enum_{T} {{ edgm_i17_enum_val_{T} = 0 }};

#if defined(__GNUC__) || defined(__clang__)
/* Step 1: get a function-pointer type to {FN} */
typedef __typeof__(&{FN}) edgm_i17_fp_{T}_t0;

/*
 * Step 2: attach function-ish attributes to the POINTER TYPE typedef.
 * Different compilers/front-ends can treat/propagate these differently.
 */
typedef edgm_i17_fp_{T}_t0 edgm_i17_fp_{T}_t1 __attribute__((warn_unused_result)) __attribute__((pure));

/* Step 3: extract the *function type* through typeof(*fp) */
static edgm_i17_fp_{T}_t1 edgm_i17_fp_{T}_var = (edgm_i17_fp_{T}_t1)0;
typedef __typeof__(*(edgm_i17_fp_{T}_var)) edgm_i17_fn_{T}_t0;

/* Optionally add attributes directly on the function type typedef (also diagnostic-sensitive) */
typedef edgm_i17_fn_{T}_t0 edgm_i17_fn_{T}_t0_attr __attribute__((warn_unused_result)) __attribute__((pure));
#else
/* Portable fallback: still a function type; we only need it to be illegal as an object. */
typedef int edgm_i17_fn_{T}_t0(void);
typedef edgm_i17_fn_{T}_t0 edgm_i17_fn_{T}_t0_attr;
#endif

/* Typedef chain to hide the function type */
typedef edgm_i17_fn_{T}_t0_attr edgm_i17_fn_{T}_t1;
typedef edgm_i17_fn_{T}_t1 edgm_i17_fn_{T}_t2;

/* Macro wrappers to move diagnostics to expansion sites on some front-ends */
#define EDGM_I17_T_{T}(X) X
#define EDGM_I17_VQ_{T} volatile

struct edgm_i17_wrap_{T} {{
    int f0;

#if defined(__GNUC__) || defined(__clang__)
    /* Enhancement: hide illegal function-type member inside an *anonymous struct* */
    struct {{
        /* illegal: member declared with function type (not pointer) */
        EDGM_I17_T_{T}(edgm_i17_fn_{T}_t2) anonymous_member;
    }};

    /* Enhancement: anonymous union/struct nesting for more layout/diag complexity */
    union {{
        int pad;
        struct {{
            /* illegal again, but now nested and often diagnosed differently */
            EDGM_I17_T_{T}(edgm_i17_fn_{T}_t2) nested_u_member;
        }};
    }};
#else
    /* Fallback without anonymous members */
    struct edgm_i17_inner_{T} {{
        EDGM_I17_T_{T}(edgm_i17_fn_{T}_t2) anonymous_member;
    }} inner;
    union {{
        int pad;
        EDGM_I17_T_{T}(edgm_i17_fn_{T}_t2) nested_u_member;
    }} u;
#endif
}};

/*
 * Qualifier-confusion:
 * - Function types cannot be const/volatile qualified (only pointers can).
 * - Some compilers warn "qualifiers ignored", others error earlier/later.
 */
typedef EDGM_I17_VQ_{T} EDGM_I17_T_{T}(edgm_i17_fn_{T}_t2) edgm_i17_fn_vq_{T};

/* illegal: object of (attempted) qualified function type */
static edgm_i17_fn_vq_{T} edgm_i17_obj_{T};

/* illegal: array of functions (and we add const to further scramble diagnostics) */
static const EDGM_I17_T_{T}(edgm_i17_fn_{T}_t2) edgm_i17_arr_{T}[2];

#undef EDGM_I17_VQ_{T}
#undef EDGM_I17_T_{T}
/* edgm I17-adv++ end */
"""
    return payload


def insert_illegal_function_type_object_adv(in_path: str, out_path: str, seed: Optional[int] = None) -> bool:
    rng = random.Random(seed)
    text = _read_text(in_path)
    lines = text.splitlines(True)

    cands = _find_prototypes(lines)
    if not cands:
        _write_text(out_path, text)
        print("[debug] I17-adv++: no suitable prototypes found; skip")
        return False

    line_idx, fn = rng.choice(cands)
    tag = rng.randint(10000, 99999)

    payload = _make_payload(tag, fn)

    new_lines: List[str] = []
    for i, ln in enumerate(lines):
        new_lines.append(ln)
        if i == line_idx:
            new_lines.append(payload)

    _write_text(out_path, "".join(new_lines))
    print(f"[debug] I17-adv++: seed={seed}")
    print(f"[debug] I17-adv++: chose prototype '{fn}' at line {line_idx+1}, tag={tag}")
    print(f"[debug] I17-adv++: inserted after prototype line {line_idx+1}")
    return True
