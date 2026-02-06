# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# 让你保持和项目里其他脚本一样的导入风格
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.edgm.mutators.array_to_pointer_keep_subscript_adv import mutate


def main(argv):
    if len(argv) < 3:
        print("usage: python3 scripts/44_mutate_array_pointer_shape_confusion_adv.py <in.c> <out.c> [seed] [keep_k] [mode]")
        print("  mode: auto | a2p_cap_err | a2pa_wrong_dim | p2a_keep_writes")
        return 2

    in_path = argv[1]
    out_path = argv[2]
    seed = None
    keep_k = 2
    mode = "auto"

    if len(argv) >= 4 and argv[3].strip():
        seed = int(argv[3])
    if len(argv) >= 5 and argv[4].strip():
        keep_k = int(argv[4])
    if len(argv) >= 6 and argv[5].strip():
        mode = argv[5].strip()

    ok = mutate(in_path, out_path, seed=seed, keep_k=keep_k, mode=mode)
    if ok:
        print(f"[结果] 已生成 C-shape-adv 变异文件: {out_path}")
        return 0
    print(f"[结果] C-shape-adv 未插入（已原样拷贝或跳过）: {out_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
