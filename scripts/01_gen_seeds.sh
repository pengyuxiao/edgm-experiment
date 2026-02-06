#!/usr/bin/env bash
set -euo pipefail

# 用法：
#   ./scripts/01_gen_seeds.sh 500
# 生成到:
#   data/seeds/raw/seed_0001.c ...
# 并记录每个文件对应的 csmith_seed 到:
#   data/seeds/meta/seed_0001.meta.json ...

NUM_SEEDS=${1:-10}
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CSMITH_BIN="/home/pyx/edgm-experiment/src/csmithedge/csmith/build/src/csmith"

RAW_DIR="$PROJECT_ROOT/data/seeds/raw"
META_DIR="$PROJECT_ROOT/data/seeds/meta"

mkdir -p "$RAW_DIR" "$META_DIR"

if [ ! -x "$CSMITH_BIN" ]; then
  echo "错误：找不到可执行的 CsmithEdge 二进制文件：$CSMITH_BIN"
  echo "请确认路径是否正确，以及是否有执行权限。"
  exit 1
fi

# 取一个 32-bit 无符号随机数（0..4294967295）
rand_u32() {
  od -An -N4 -tu4 /dev/urandom | tr -d ' '
}

echo "项目根目录: $PROJECT_ROOT"
echo "CsmithEdge 可执行: $CSMITH_BIN"
echo "种子输出目录: $RAW_DIR"
echo "meta 输出目录: $META_DIR"
echo "准备生成 $NUM_SEEDS 个种子（每个样本显式传入随机 --seed）..."

for ((i=1; i<=NUM_SEEDS; i++)); do
  SEED_ID=$(printf "%04d" "$i")
  OUT_FILE="$RAW_DIR/seed_${SEED_ID}.c"
  META_FILE="$META_DIR/seed_${SEED_ID}.meta.json"

  S=$(rand_u32)

  # 关键：显式 seed，避免每次都复现同一批程序
  "$CSMITH_BIN" --seed "$S" > "$OUT_FILE"

  # 记录，保证可复现
  printf '{ "seed_id": "%s", "csmith_seed": %s }\n' "$SEED_ID" "$S" > "$META_FILE"

  echo "生成第 $i 个种子: $OUT_FILE (csmith_seed=$S)"
done

echo "种子生成完成！"
