"""
DBファイルの保守用スクリプト(GitHub Actionsから実行する)。

実行例:
  python db_maintenance.py compact               # 不要な索引の削除と空き領域の圧縮
  python db_maintenance.py count                 # stocks_daily の行数を表示
  python db_maintenance.py verify --min-rows N   # 壊れていないか・行数が減っていないか確認
"""

from __future__ import annotations

import argparse
import sqlite3
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


def count_stock_rows(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM stocks_daily").fetchone()[0]
    finally:
        conn.close()


def verify(db_path: Path, min_rows: int) -> list[str]:
    """Releaseへアップロードしてよい状態か確認し、問題点のリストを返す(空ならOK)。

    以前、DBの二重更新で2025年分のデータが消えたことがあるため、
    行数が取得時より減っていたらアップロードを止める。
    """
    problems = []
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        problems.append(f"DBが破損している可能性があります: {result}")

    rows = count_stock_rows(db_path)
    if rows < min_rows:
        problems.append(f"stocks_daily の行数が減っています: {min_rows} -> {rows}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="DBファイルの保守")
    parser.add_argument("--db", default=str(db.DB_PATH), help="DBファイルのパス")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compact", help="不要な索引の削除と空き領域の圧縮")
    sub.add_parser("count", help="stocks_daily の行数を表示")
    verify_parser = sub.add_parser("verify", help="破損・行数減少がないか確認")
    verify_parser.add_argument(
        "--min-rows", type=int, required=True, help="stocks_daily に最低限必要な行数(取得時の行数)"
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] DBファイルが見つかりません: {db_path}", file=sys.stderr)
        return 1

    if args.command == "compact":
        compact(db_path)
    elif args.command == "count":
        print(count_stock_rows(db_path))
    elif args.command == "verify":
        problems = verify(db_path, args.min_rows)
        for p in problems:
            print(f"[ERROR] {p}", file=sys.stderr)
        if problems:
            return 1
        print("DBの確認OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
