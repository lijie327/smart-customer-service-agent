"""CLI：生成合成订单数据

用法（在项目根目录执行）：
    python scripts/seed_data.py            # 仅当库为空时灌入 800 条
    python scripts/seed_data.py --force    # 强制重建 800 条
    python scripts/seed_data.py --count 2000
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.repository import init_database
from backend.db.seed import run_seed


def main():
    parser = argparse.ArgumentParser(description="生成合成订单数据")
    parser.add_argument("--count", type=int, default=800, help="生成订单数量")
    parser.add_argument("--force", action="store_true", help="强制重建（忽略已有数据）")
    args = parser.parse_args()

    db = init_database()
    n = run_seed(db, count=args.count, force=args.force)
    if n:
        print(f"✓ 已灌入 {n} 条合成订单 -> {db.db_path}")
    else:
        print(f"ℹ 订单已存在，跳过 seed（如需重建请加 --force）")


if __name__ == "__main__":
    main()
