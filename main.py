"""
毎日の定時実行エントリポイント。

  1. 海外/国内主要指数(yfinance)を取得してDBに保存
  2. J-Quantsから銘柄マスタ(プライム市場)を取得してDBに保存
  3. J-Quantsから当日の四本値を取得し、プライム銘柄分だけDBに保存
     (無料プランは12週間遅延のため、実際には過去の日付になる)
  4. yfinanceで直近12週間分の個別銘柄データを取得し、3.で埋まらない
     「最新の穴」を補完する

実行例:
  JQUANTS_API_KEY=xxxx python main.py
  JQUANTS_API_KEY=xxxx python main.py --date 2026-08-01   # 特定日を指定
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as date_cls, timedelta

import db
import fetch_indices
import fetch_stocks
import fetch_stocks_yf
from jquants_client import JQuantsClient

# J-Quants無料プランはデータが約12週間(84日)遅延配信される。
# 有料プラン(Light以上)にアップグレードしたら --delay-days 0 を指定すれば
# 「今日」のデータを取得しにいく動作に戻せる。
DEFAULT_DELAY_DAYS = 84


def main() -> int:
    parser = argparse.ArgumentParser(description="日次マーケットデータ取得バッチ")
    parser.add_argument(
        "--date",
        default=None,
        help="取得対象日を明示的に指定したい場合 (YYYY-MM-DD)。指定時は --delay-days は無視される。",
    )
    parser.add_argument(
        "--delay-days",
        type=int,
        default=DEFAULT_DELAY_DAYS,
        help=(
            "--date を指定しない場合、本日からこの日数だけ遡った日付を取得対象にする。"
            f"無料プランのデフォルトは{DEFAULT_DELAY_DAYS}日(12週間)。有料プランに上げたら0を指定する。"
        ),
    )
    args = parser.parse_args()

    if args.date:
        target_date = args.date
    else:
        target_date = (date_cls.today() - timedelta(days=args.delay_days)).isoformat()
        print(f"(--date未指定のため、本日から{args.delay_days}日前の {target_date} を自動的に対象日とします)")

    conn = db.get_connection()
    db.init_db(conn)

    # --- 1. 主要指数 ---
    print("[1/4] 主要指数(日経平均・NASDAQ・SOX等)を取得中...")
    try:
        index_rows = fetch_indices.fetch_index_rows()
        db.upsert_indices(conn, index_rows)
        print(f"  -> {len(index_rows)} 件を保存しました。")
    except Exception as e:
        print(f"  [ERROR] 指数データの取得に失敗しました: {e}", file=sys.stderr)

    # --- 2. J-Quants: 銘柄マスタ ---
    print("[2/4] J-Quants: プライム銘柄マスタを取得中...")
    try:
        client = JQuantsClient()
    except RuntimeError as e:
        print(f"  [ERROR] {e}", file=sys.stderr)
        print("J-Quants関連の処理をスキップします(指数データのみ保存済み)。")
        conn.close()
        return 1

    try:
        master_rows, prime_codes = fetch_stocks.fetch_prime_master_rows(client)
        db.upsert_stocks_master(conn, master_rows)
        print(f"  -> プライム銘柄 {len(prime_codes)} 件のマスタを保存しました。")
    except Exception as e:
        print(f"  [ERROR] 銘柄マスタの取得に失敗しました: {e}", file=sys.stderr)
        conn.close()
        return 1

    # --- 3. J-Quants: 対象日の四本値 ---
    print(f"[3/4] J-Quants: {target_date} の株価四本値を取得中...")
    try:
        daily_rows = fetch_stocks.fetch_prime_daily_rows(client, target_date, prime_codes)
        if not daily_rows:
            print("  -> 対象日のデータが0件でした(休日、または無料プランの遅延配信の可能性があります)。")
        else:
            db.upsert_stocks_daily(conn, daily_rows)
            print(f"  -> {len(daily_rows)} 件を保存しました。")
    except Exception as e:
        print(f"  [ERROR] 株価データの取得に失敗しました: {e}", file=sys.stderr)
        print("  -> このステップは失敗しましたが、続けてステップ4(yfinance)を実行します。")

    # --- 4. yfinance: 直近12週間分の穴埋め ---
    print("[4/4] yfinance: 直近の個別銘柄データ(J-Quants遅延分の穴埋め)を取得中...")
    try:
        saved, failed = fetch_stocks_yf.fetch_and_store(
            conn, list(prime_codes), *fetch_stocks_yf.default_date_range()
        )
        print(f"  -> {saved} 件を保存しました(取得失敗銘柄数: {failed})。")
    except Exception as e:
        print(f"  [ERROR] yfinanceでの直近データ取得に失敗しました: {e}", file=sys.stderr)

    conn.close()
    print("完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
