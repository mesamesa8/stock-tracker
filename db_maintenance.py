"""
DBファイルの保守用スクリプト(GitHub Actionsから実行する)。

実行例:
  python db_maintenance.py compact   # 不要な索引の削除と空き領域の圧縮
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import db


def compact(db_path: Path) -> None:
    before = db_path.stat().st_size
    conn = db.get_connection(db_path)
    try:
        db.init_db(conn)  # 重複索引の DROP もここで行われる
        db.compact_db(conn)
    finally:
        conn.close()
    after = db_path.stat().st_size
    print(f"DBを圧縮しました: {before / 1e6:.1f}MB -> {after / 1e6:.1f}MB")


def main() -> int:
    parser = argparse.ArgumentParser(description="DBファイルの保守")
    parser.add_argument("--db", default=str(db.DB_PATH), help="DBファイルのパス")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compact", help="不要な索引の削除と空き領域の圧縮")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] DBファイルが見つかりません: {db_path}", file=sys.stderr)
        return 1

    if args.command == "compact":
        compact(db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
