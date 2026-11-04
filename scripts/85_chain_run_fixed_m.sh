#!/usr/bin/env bash
set -euo pipefail

# -------- configurable --------
GROUP="${GROUP:-single}"                 # single | multi
M="${M:-1}"                              # 1 for single, 3 for multi (we will set via env)
TARGET="${TARGET:-200}"                  # how many final programs to produce
SEED_DIR="${SEED_DIR:-data/seeds/diverse/clean_diversify_syntax}"
CURSOR="${CURSOR:-data/run_cursor_rq3_${GROUP}.txt}"

EXPLORE="${EXPLORE:-0.30}"               # exploration in arm-picking (keep moderate)
EMB_K="${EMB_K:-5}"
EMB_DIM="${EMB_DIM:-1024}"
PROVIDER="${PROVIDER:-dashscope}"
MODEL="${MODEL:-text-embedding-v4}"

MAX_TRIES_PER_SEED="${MAX_TRIES_PER_SEED:-4}"
MAX_TOTAL_ATTEMPTS="${MAX_TOTAL_ATTEMPTS:-4000}"
# --------------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT_FINAL="data/rq3/${GROUP}/mutants/final"
OUT_PROC="data/rq3/${GROUP}/mutants/processed_chain"

mkdir -p "$OUT_FINAL" "$OUT_PROC" data/bandit data/tmp_chain data/tmp_chain/drop

# ensure TS state exists (arms list -> ts_state, no reward update)
if [ ! -f data/bandit/ts_state.json ]; then
  echo "[INFO] ts_state.json missing, init..."
  python3 scripts/52_ts_state.py --window 200 --k 2 --reward -1 >/dev/null
fi

# seed picker uses cursor file; we pass cursor+seed_dir via args (small wrapper call)
next_seed() {
  python3 - <<PY
from pathlib import Path
import argparse
ap=argparse.ArgumentParser()
ap.add_argument("--seed-dir", default="${SEED_DIR}")
ap.add_argument("--cursor", default="${CURSOR}")
args=ap.parse_args()
seed_dir=Path(args.seed_dir).resolve()
cur_path=Path(args.cursor)
seeds=sorted(seed_dir.glob("*.c"))
if not seeds:
    raise SystemExit(f"[ERR] no seeds under {seed_dir}")
idx=0
if cur_path.exists():
    try: idx=int(cur_path.read_text().strip() or "0")
    except: idx=0
if idx>=len(seeds): idx=0
chosen=seeds[idx]
cur_path.parent.mkdir(parents=True, exist_ok=True)
cur_path.write_text(str(idx+1), encoding="utf-8")
print(str(chosen))
PY
}

prepend_applied_arms_header() {
  local out_file="$1"
  local arms_csv="$2"
  local tmp="${out_file}.tmp"
  { echo "/* EDGM_APPLIED_ARMS: ${arms_csv} */"; cat "$out_file"; } > "$tmp"
  mv "$tmp" "$out_file"
}

echo "[INFO] RQ3 group=${GROUP} M=${M} TARGET=${TARGET}"
echo "[INFO] OUT_FINAL=${OUT_FINAL}"
echo "[INFO] OUT_PROC=${OUT_PROC}"
echo "[INFO] SEED_DIR=${SEED_DIR} CURSOR=${CURSOR}"

# IMPORTANT: keep outputs isolated; do NOT touch your main data/mutants/final
rm -f "${OUT_FINAL}"/*.chain.c 2>/dev/null || true

seeds_ok=0
total_attempts=0

while [ "$seeds_ok" -lt "$TARGET" ]; do
  total_attempts=$((total_attempts+1))
  if [ "$total_attempts" -gt "$MAX_TOTAL_ATTEMPTS" ]; then
    echo "[ERR] reached MAX_TOTAL_ATTEMPTS=${MAX_TOTAL_ATTEMPTS}, only seeds_ok=${seeds_ok}/${TARGET}"
    break
  fi

  seed="$(next_seed)"
  base="$(basename "$seed" .c)"
  out="${OUT_FINAL}/${base}.chain.c"

  ok=0
  for try in $(seq 1 "$MAX_TRIES_PER_SEED"); do
    # pick exactly M arms using current picker (ts-ish + explore)
    mapfile -t ARMS < <(python3 scripts/62_pick_arms.py --k "$M" --explore "$EXPLORE")
    arms_csv=$(IFS=, ; echo "${ARMS[*]}")

    echo "[INFO] seed=${base} try=${try}/${MAX_TRIES_PER_SEED} m=${M}"
    for a in "${ARMS[@]}"; do echo "  - arm: $a"; done

    set +e
    python3 scripts/70_multi_mutate_one.py --seed "$seed" --arms "$arms_csv" --out "$out" \
      2>&1 | grep -v '^\[AST diag\]'
    rc=${PIPESTATUS[0]}
    set -e

    if [ "$rc" -eq 0 ] && [ -s "$out" ]; then
      prepend_applied_arms_header "$out" "$arms_csv"
      seeds_ok=$((seeds_ok+1))
      echo "[OK] final=${out} seeds_ok=${seeds_ok}/${TARGET}"
      ok=1
      break
    else
      echo "[DROP] chain failed seed=${base} try=${try} (rc=${rc})"
      rm -f "$out" 2>/dev/null || true
    fi
  done

  if [ "$ok" -eq 0 ]; then
    echo "[GIVEUP] seed=${base} after ${MAX_TRIES_PER_SEED} tries"
  fi
done

# reward all finals into group-specific processed dir
python3 scripts/81_process_final_update_ts.py \
  --input "$OUT_FINAL" \
  --out-dir "$OUT_PROC" \
  --k "$EMB_K" --dimensions "$EMB_DIM" --max 0 \
  --provider "$PROVIDER" --model "$MODEL" --force

echo "[DONE] group=${GROUP}"
echo "  final_count=$(ls -1 "${OUT_FINAL}"/*.chain.c 2>/dev/null | wc -l)"
echo "  reward_count=$(ls -1 "${OUT_PROC}"/*.reward.json 2>/dev/null | wc -l)"