#!/usr/bin/env bash
set -euo pipefail

K="${1:-2}"          # 每轮选几个 arm
TARGET="${2:-50}"    # 每轮目标生成多少个“合格 mutant”
MAX_TRIES="${3:-800}"
KNN_K="${4:-5}"
DIM="${5:-256}"

mkdir -p data/mutants/queue data/mutants/dropped data/mutants/processed

# 选臂
mapfile -t ARMS < <(python3 scripts/62_pick_arms.py --k "$K")
echo "[round] chosen arms:"
printf '  - %s\n' "${ARMS[@]}"

# 清空本轮 queue
rm -f data/mutants/queue/*.c data/mutants/queue/*.c.meta.json

made=0
tries=0
while [ "$made" -lt "$TARGET" ] && [ "$tries" -lt "$MAX_TRIES" ]; do
  tries=$((tries+1))
  arm=${ARMS[$((RANDOM % ${#ARMS[@]}))]}
  parent=$(ls data/corpus/pool/*.c | shuf -n 1)
  out=data/mutants/queue/$(basename "$parent" .c).r${tries}.c

  last=$(python3 scripts/60_run_arm_one.py --arm "$arm" --in "$parent" --out "$out" --mkdir --require-error any \
          2>&1 | grep -v '^\[AST diag\]' | tail -n 1)
  echo "$last"

  if echo "$last" | grep -q '^\[OK\] wrote'; then
    made=$((made+1))
  else
    rm -f "$out" "$out.meta.json" 2>/dev/null || true
  fi
done

echo "[round] made=$made tries=$tries"
echo "[round] queue_c=$(ls -1 data/mutants/queue/*.c 2>/dev/null | wc -l)"
echo "[round] queue_meta=$(ls -1 data/mutants/queue/*.c.meta.json 2>/dev/null | wc -l)"

# meta 完整性校验
diff <(ls data/mutants/queue/*.c | sed 's/$/.meta.json/') <(ls data/mutants/queue/*.c.meta.json) >/dev/null \
  && echo "[OK] all queue have meta" || { echo "[BAD] meta missing"; exit 1; }

# 处理 + 更新 TS
python3 scripts/61_process_queue_update_ts.py --max "$TARGET" --k "$KNN_K" --dimensions "$DIM"

# 展示当前 nonempty arms（快速 sanity）
python3 - <<'PY'
import json
from pathlib import Path
s=json.loads(Path("data/bandit/ts_state.json").read_text())
arms=s["arms"]
def rs(v):
    h=(v or {}).get("history", [])
    out=[]
    for x in h:
        if isinstance(x,(int,float)): out.append(float(x))
        elif isinstance(x,dict):
            r=x.get("reward01", x.get("reward"))
            if r is not None: out.append(float(r))
    return out
non=[]
for a,v in arms.items():
    r=rs(v)
    if r:
        non.append((sum(r)/len(r), len(r), a))
non.sort(reverse=True)
print("nonempty_arms=", len(non))
for mean,n,a in non[:10]:
    print(f"{a:45s} n={n:4d} mean={mean:.4f}")
PY
