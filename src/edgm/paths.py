# src/edgm/paths.py
from pathlib import Path

# 项目根目录：.../edgm-experiment
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 一些常用路径（后面脚本会很方便用）
DATA_DIR    = PROJECT_ROOT / "data"
SEEDS_DIR   = DATA_DIR / "seeds" / "clean"
RESULTS_DIR = PROJECT_ROOT / "results"

__all__ = [
    "PROJECT_ROOT",
    "DATA_DIR",
    "SEEDS_DIR",
    "RESULTS_DIR",
]
