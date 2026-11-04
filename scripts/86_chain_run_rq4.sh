#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   POLICY=ts   GROUP=ts   TARGET=200 K=2 EXPLORE=0.05 ./scripts/86_chain_run_rq4.sh
#   POLICY=rand GROUP=rand TARGET=200 K=2              ./scripts/86_chain_run_rq4.sh
#   POLICY=rf   GROUP=rf   TARGET=200 K=2              ./scripts/86_chain_run_rq4.sh

POLICY="${POLICY:-ts}"          # ts | rand | rf
GROUP="${GROUP:-ts}"
TARGET="${TARGET:-200}"
K="${K:-2}"
EXPLORE="${EXPLORE:-0.05}"      # ts only
MAX_TRIES="${MAX_TRIES:-4}"

SEED_DIR="${SEED_DIR:-data/seeds/diverse/clean_diversify_syntax}"
CURSOR="${CURSOR:-data/run_cursor_rq4_${GROUP}.txt}"

# fixed pool (must exist)
FIXED_POOL="${FIXED_POOL:-data/rq4/fixed_pool.jsonl}"
if [ ! -s "$FIXED_POOL" ]; then
  echo "[ERR] FIXED_POOL not found or empty: $FIXED_POOL" >&2
  exit 2
fi

OUT_ROOT="data/rq4/${GROUP}"
FINAL_DIR="${OUT_ROOT}/mutants/final"
PROC_DIR="${OUT_ROOT}/mutants/processed_chain"
LOG_DIR="${OUT_ROOT}/logs"
STATE_DIR="${OUT_ROOT}/state"
mkdir -p "$FINAL_DIR" "$PROC_DIR" "$LOG_DIR" "$STATE_DIR"

TS_STATE="${STATE_DIR}/ts_state.json"
RF_STATE="${STATE_DIR}/rf_state.joblib"

# init cursor
if [ ! -f "$CURSOR" ]; then echo 0 > "$CURSOR"; fi

# init TS state: sync only (no reward update)
if [ "$POLICY" = "ts" ]; then
  python3 scripts/52_ts_state.py \
    --arms-file config/arms_list.txt \
    --state "$TS_STATE" \
    --window 200 --k "$K" \
    --reward -1 >/dev/null
fi

# init RF state
if [ "$POLICY" = "rf" ]; then
  python3 scripts/88_rf_init.py \
    --arms-file config/arms_list.txt \
    --state "$RF_STATE" >/dev/null
fi

echo "[RQ4] policy=$POLICY group=$GROUP target=$TARGET k=$K explore=$EXPLORE"
echo "[RQ4] fixed_pool=$FIXED_POOL"
echo "[RQ4] out=$OUT_ROOT"

ok=0
attempt=0

while [ "$ok" -lt "$TARGET" ]; do
  attempt=$((attempt+1))

  seed="$(python3 scripts/65_next_seed.py --seed-dir "$SEED_DIR" --cursor "$CURSOR")"
  base="$(basename "$seed" .c)"

  for t in $(seq 1 "$MAX_TRIES"); do
    arms_csv=""   # ALWAYS initialize per-try
    ARMS=()

    # pick arms
    if [ "$POLICY" = "ts" ]; then
      mapfile -t ARMS < <(python3 scripts/87_pick_arms_policy.py --policy ts --k "$K" --explore "$EXPLORE" --ts-state "$TS_STATE" || true)
    elif [ "$POLICY" = "rand" ]; then
      mapfile -t ARMS < <(python3 scripts/87_pick_arms_policy.py --policy rand --k "$K" || true)
    else
      mapfile -t ARMS < <(python3 scripts/87_pick_arms_policy.py --policy rf --k "$K" --rf-state "$RF_STATE" --seed "$seed" || true)
    fi

    # sanitize ARMS (drop empty lines)
    ARMS=($(printf "%s\n" "${ARMS[@]:-}" | sed '/^\s*$/d' || true))

    if [ "${#ARMS[@]}" -lt "$K" ]; then
      echo "[DROP] arms empty/insufficient (got=${#ARMS[@]} need=$K) seed=${base} try=$t" | tee -a "${LOG_DIR}/run.log"
      continue
    fi

    arms_csv="$(IFS=, ; echo "${ARMS[*]}")"

    out="${FINAL_DIR}/${base}.chain.c"
    rm -f "$out" 2>/dev/null || true

    echo "[TRY] ok=${ok}/${TARGET} seed=${base} try=$t arms=${arms_csv}" | tee -a "${LOG_DIR}/run.log"

    set +e
    python3 scripts/70_multi_mutate_one.py --seed "$seed" --arms "$arms_csv" --out "$out" >> "${LOG_DIR}/mutate.log" 2>&1
    rc=$?
    set -e

    if [ "$rc" -ne 0 ] || [ ! -s "$out" ]; then
      echo "[DROP] mutate failed rc=$rc seed=${base} try=$t" | tee -a "${LOG_DIR}/run.log"
      rm -f "$out" 2>/dev/null || true
      continue
    fi

    # prepend applied arms header
    tmp="${out}.tmp"
    { echo "/* EDGM_APPLIED_ARMS: ${arms_csv} */"; cat "$out"; } > "$tmp"
    mv "$tmp" "$out"

    # reward
    reward_json="${PROC_DIR}/${base}.reward.json"
    python3 scripts/53_reward_one_c_rq4.py "$out" \
      --k 5 \
      --archive-store "$FIXED_POOL" \
      > "$reward_json"

    # read reward01 safely
    r01="$(python3 - <<'PY' "$reward_json"
import json, sys
p=sys.argv[1]
o=json.load(open(p,"r",encoding="utf-8"))
print(o.get("reward01", 0.0))
PY
)"

    # update policies
    if [ "$POLICY" = "ts" ]; then
      python3 scripts/52_ts_state.py \
        --arms-file config/arms_list.txt \
        --state "$TS_STATE" \
        --window 200 --k "$K" \
        --chosen "$arms_csv" \
        --reward "$r01" >/dev/null
    elif [ "$POLICY" = "rf" ]; then
      python3 scripts/90_rf_update.py \
        --state "$RF_STATE" \
        --seed "$seed" --arms "$arms_csv" --reward "$r01" >/dev/null
    fi

    # trace.jsonl
    python3 - <<'PY' "$seed" "$out" "$arms_csv" "$r01" "$POLICY" "$GROUP" >> "${LOG_DIR}/trace.jsonl"
import json, time, sys
seed, out, arms_csv, r01, policy, group = sys.argv[1:]
rec = {
  "time": time.time(),
  "seed": seed,
  "out": out,
  "arms": arms_csv,
  "reward01": float(r01),
  "policy": policy,
  "group": group,
}
print(json.dumps(rec, ensure_ascii=False))
PY

    ok=$((ok+1))
    echo "[OK] ok=${ok}/${TARGET} reward01=${r01}" | tee -a "${LOG_DIR}/run.log"
    break
  done
done

echo "[DONE] ok=$ok attempts=$attempt"