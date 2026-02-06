#!/usr/bin/env bash
set -euo pipefail

ROUNDS="${ROUNDS:-2}"
TARGET_SEEDS_PER_ROUND="${TARGET_SEEDS_PER_ROUND:-20}"   # 每轮处理多少个 parent seeds（不是 mutant 数）
EXPLORE="${EXPLORE:-0.15}"
EMB_K="${EMB_K:-5}"
EMB_DIM="${EMB_DIM:-256}"

mkdir -p data/mutants/queue data/mutants/dropped data/mutants/processed data/bandit

# 确保 TS state 存在
if [ ! -f data/bandit/ts_state.json ]; then
  echo "[INFO] ts_state.json missing, init..."
  python3 scripts/52_ts_state.py --window 200 --k 2 --reward 0 >/dev/null
fi

# 按 0.45/0.45/0.10 抽 m=1/2/3
pick_m_arms() {
  local r=$((RANDOM % 100))
  if [ "$r" -lt 45 ]; then echo 1
  elif [ "$r" -lt 90 ]; then echo 2
  else echo 3
  fi
}

echo "[INFO] rounds=${ROUNDS} target_seeds_per_round=${TARGET_SEEDS_PER_ROUND}"

for round in $(seq 1 "$ROUNDS"); do
  echo "========== ROUND ${round} =========="

  seeds_done=0
  while [ "$seeds_done" -lt "$TARGET_SEEDS_PER_ROUND" ]; do
    parent=$(python3 scripts/65_next_seed.py)
    base=$(basename "$parent" .c)

    m=$(pick_m_arms)
    mapfile -t ARMS < <(python3 scripts/62_pick_arms.py --k "$m" --explore "$EXPLORE")

    echo "[INFO] parent_seed=${base}.c m=${m}"
    for a in "${ARMS[@]}"; do echo "  - arm: $a"; done

    # 对每个 arm 产出 1 个 mutant（成功才进 queue）
    idx=0
    for arm in "${ARMS[@]}"; do
      idx=$((idx+1))
      out="data/mutants/queue/${base}.r${round}.a${idx}.c"
      last=$(python3 scripts/60_run_arm_one.py --arm "$arm" --in "$parent" --out "$out" \
              --mkdir --require-error any 2>&1 | grep -v '^\[AST diag\]' | tail -n 1)
      echo "$last"
    done

    seeds_done=$((seeds_done+1))
  done

  echo "[INFO] queue_c=$(ls -1 data/mutants/queue/*.c 2>/dev/null | wc -l)"
  echo "[INFO] queue_meta=$(ls -1 data/mutants/queue/*.c.meta.json 2>/dev/null | wc -l)"
  diff <(ls data/mutants/queue/*.c | sed 's/$/.meta.json/') <(ls data/mutants/queue/*.c.meta.json) >/dev/null \
    && echo "[OK] all queue have meta" || echo "[BAD] meta missing"

  # 处理队列：embedding→reward→更新 TS
  python3 scripts/61_process_queue_update_ts.py --max 999999 --k "$EMB_K" --dimensions "$EMB_DIM"

  echo "[INFO] after_process:"
  echo "  queue_left=$(ls -1 data/mutants/queue/*.c 2>/dev/null | wc -l)"
  echo "  processed_total=$(ls -1 data/mutants/processed/*.c 2>/dev/null | wc -l)"
  echo "  reward_total=$(ls -1 data/mutants/processed/*.reward.json 2>/dev/null | wc -l)"
done

echo "========== DONE =========="
