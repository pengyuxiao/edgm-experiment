#!/usr/bin/env bash
set -euo pipefail

ROUNDS="${ROUNDS:-1}"
TARGET_SEEDS_PER_ROUND="${TARGET_SEEDS_PER_ROUND:-20}"
EXPLORE="${EXPLORE:-0.15}"
EMB_K="${EMB_K:-5}"
EMB_DIM="${EMB_DIM:-1024}"

# 每个 seed 失败时最多重试次数（换 arms）
MAX_TRIES_PER_SEED="${MAX_TRIES_PER_SEED:-4}"
# 为了避免无限循环：每个 round 最多尝试多少个 seed（含失败）
MAX_TOTAL_ATTEMPTS_PER_ROUND="${MAX_TOTAL_ATTEMPTS_PER_ROUND:-500}"

mkdir -p data/mutants/final data/mutants/processed_chain data/bandit data/tmp_chain data/tmp_chain/drop

# ensure TS state exists
if [ ! -f data/bandit/ts_state.json ]; then
  echo "[INFO] ts_state.json missing, init..."
  python3 scripts/52_ts_state.py --window 200 --k 2 --reward 0 >/dev/null
fi

pick_m_arms() {
  local r=$((RANDOM % 100))
  if [ "$r" -lt 20 ]; then echo 1
  elif [ "$r" -lt 65 ]; then echo 2
  else echo 3
  fi
}

# 写入头注释（不依赖 mutator 是否输出 edgm 标记）
prepend_applied_arms_header() {
  local out_file="$1"
  local arms_csv="$2"
  local tmp="${out_file}.tmp"
  {
    echo "/* EDGM_APPLIED_ARMS: ${arms_csv} */"
    cat "$out_file"
  } > "$tmp"
  mv "$tmp" "$out_file"
}

echo "[INFO] chain-run rounds=${ROUNDS} seeds_per_round=${TARGET_SEEDS_PER_ROUND}"
echo "[INFO] policy_id=bootstrap_legacy reward_version=legacy-eq6"
echo "[INFO] MAX_TRIES_PER_SEED=${MAX_TRIES_PER_SEED} MAX_TOTAL_ATTEMPTS_PER_ROUND=${MAX_TOTAL_ATTEMPTS_PER_ROUND}"

for round in $(seq 1 "$ROUNDS"); do
  echo "========== ROUND ${round} =========="

  # 每个 round 开始前，清理 final 目录（避免混入上一轮）
  rm -f data/mutants/final/*.chain.c 2>/dev/null || true

  seeds_ok=0
  total_attempts=0

  while [ "$seeds_ok" -lt "$TARGET_SEEDS_PER_ROUND" ]; do
    total_attempts=$((total_attempts+1))
    if [ "$total_attempts" -gt "$MAX_TOTAL_ATTEMPTS_PER_ROUND" ]; then
      echo "[ERR] reached MAX_TOTAL_ATTEMPTS_PER_ROUND=${MAX_TOTAL_ATTEMPTS_PER_ROUND}, only seeds_ok=${seeds_ok}/${TARGET_SEEDS_PER_ROUND}"
      break
    fi

    seed=$(python3 scripts/65_next_seed.py)
    base=$(basename "$seed" .c)

    ok=0
    for try in $(seq 1 "$MAX_TRIES_PER_SEED"); do
      m=$(pick_m_arms)
      mapfile -t ARMS < <(python3 scripts/62_pick_arms.py --k "$m" --explore "$EXPLORE")
      arms_csv=$(IFS=, ; echo "${ARMS[*]}")
      out="data/mutants/final/${base}.chain.c"

      echo "[INFO] seed=${base} try=${try}/${MAX_TRIES_PER_SEED} m=${m}"
      for a in "${ARMS[@]}"; do echo "  - arm: $a"; done

      # run chain; suppress AST diag lines
      # 关键：不再 || true 掩盖失败，我们要根据返回码决定是否成功
      set +e
      python3 scripts/70_multi_mutate_one.py --seed "$seed" --arms "$arms_csv" --out "$out" \
        2>&1 | grep -v '^\[AST diag\]'
      rc=${PIPESTATUS[0]}
      set -e

      # 成功条件：命令返回 0 且输出文件存在且非空
      if [ "$rc" -eq 0 ] && [ -s "$out" ]; then
        # 头部写入 arms 信息，供后续统计 / 72 强制保留
        prepend_applied_arms_header "$out" "$arms_csv"
        seeds_ok=$((seeds_ok+1))
        echo "[OK] final=${out} seeds_ok=${seeds_ok}/${TARGET_SEEDS_PER_ROUND}"
        ok=1
        break
      else
        echo "[DROP] chain failed for seed=${base} try=${try} (rc=${rc})"
        rm -f "$out" 2>/dev/null || true
      fi
    done

    if [ "$ok" -eq 0 ]; then
      echo "[GIVEUP] seed=${base} after ${MAX_TRIES_PER_SEED} tries"
    fi
  done

  # process chain finals -> update TS
  # 关键：明确 input / out-dir，避免 reward 写错目录
  python3 scripts/81_process_final_update_ts.py \
    --input data/mutants/final \
    --out-dir data/mutants/processed_chain \
    --k "$EMB_K" --dimensions "$EMB_DIM" --max 0 --force

  echo "[INFO] after_process_chain:"
  echo "  final_left=$(ls -1 data/mutants/final/*.chain.c 2>/dev/null | wc -l)"
  echo "  processed_chain_total=$(ls -1 data/mutants/processed_chain/*.reward.json 2>/dev/null | wc -l)"

done

echo "========== DONE =========="
