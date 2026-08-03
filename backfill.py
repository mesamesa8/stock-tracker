"""
過去の日付範囲をまとめて取得するバックフィル用スクリプト。

main.py は「1日分だけ」取得する設計なので、たとえば2026-01-01から
今日までのように過去分をまとめて溜めたい場合はこちらを使う。

使い方:
  JQUANTS_API_KEY=xxxx python backfill.py --start-date 2026-01-01
  JQUANTS_API_KEY=xxxx python backfill.py --start-date 2026-01-01 --end-date 2026-03-31
  JQUANTS_API_KEY=xxxx python backfill.py --start-date 2026-01-01 --overwrite  # 再取得したい場合

注意:
  - 無料プランはレート制限が厳しいため、1日ごとの取得の間に数秒〜十数秒の
    待機を挟んでいます(jquants_client.py の DEFAULT_SLEEP_SEC)。
    半年分などまとめて取ると数十分かかることがあります。
  - 途中でエラーが出て止まっても、同じコマンドをもう一度実行すれば
    (--overwrite を付けない限り)既に取得済みの日付はスキップされるので
    続きから再開できます。
  - 土日・祝日はJ-Quants側が0件を返すだけなので、自動的にスキップされます
    (エラーにはなりません)。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date as date_cls, timedelta

import requests

import db
import fetch_stocks
from jquants_client import JQuantsClient


def daterange(start: date_cls, end: date_cls):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def already_fetched(conn, date_str: str) -> bool:
    cur = conn.execute("SELECT 1 FROM stocks_daily WHERE date = ? LIMIT 1", (date_str,))
    return cur.fetchone() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="J-Quantsの過去データを日付範囲でまとめて取得")
    parser.add_argument("--start-date", required=True, help="開始日 (YYYY-MM-DD)")
    parser.add_argument(
        "--end-date",
        default=date_cls.today().isoformat(),
        help="終了日 (YYYY-MM-DD)。省略時は本日。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="指定すると、既にDBにある日付も再取得して上書きする(通常は不要)",
    )
    args = parser.parse_args()

    try:
        start = date_cls.fromisoformat(args.start_date)
        end = date_cls.fromisoformat(args.end_date)
    except ValueError:
        print("[ERROR] 日付は YYYY-MM-DD 形式で指定してください(例: 2026-01-01)", file=sys.stderr)
        return 1

    if start > end:
        print("[ERROR] start-date が end-date より後になっています", file=sys.stderr)
        return 1

    conn = db.get_connection()
    db.init_db(conn)

    try:
        client = JQuantsClient()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        conn.close()
        return 1

    # マスタは範囲の最初に1回だけ取得すれば十分(市場区分は日々変わらないため)
    print("プライム銘柄マスタを取得中...")
    master_rows, prime_codes = fetch_stocks.fetch_prime_master_rows(client)
    db.upsert_stocks_master(conn, master_rows)
    print(f"  -> プライム銘柄 {len(prime_codes)} 件を確認しました。")

    total_days = (end - start).days + 1
    fetched_days = 0
    skipped_days = 0
    empty_days = 0

    for i, d in enumerate(daterange(start, end), start=1):
        date_str = d.isoformat()

        if not args.overwrite and already_fetched(conn, date_str):
            print(f"[{i}/{total_days}] {date_str}: 取得済みのためスキップ")
            skipped_days += 1
            continue

        try:
            rows = fetch_stocks.fetch_prime_daily_rows(client, date_str, prime_codes)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None

            if status == 400:
                print(f"[{i}/{total_days}] {date_str}: [ERROR] 400 Bad Request")
                print(
                    "  -> これはおそらく『無料プランではまだ配信されていない日付』を意味します"
                    "(J-Quants無料プランはデータが約12週間遅延配信されるため)。"
                )
                print(
                    f"  -> {date_str}以降も同じ理由で失敗し続ける可能性が高いため、"
                    "ここでバックフィルを打ち切ります。取得済みのデータはそのまま使えます。"
                )
                break

            print(f"[{i}/{total_days}] {date_str}: [ERROR] {e}", file=sys.stderr)
            print("  -> 60秒クールダウンしてから次の日付に進みます。取りこぼした日付は、後でこのスクリプトを再実行すれば自動的に再取得されます。")
            time.sleep(60)
            continue
        except Exception as e:
            print(f"[{i}/{total_days}] {date_str}: [ERROR] {e}", file=sys.stderr)
            print("  -> 60秒クールダウンしてから次の日付に進みます。取りこぼした日付は、後でこのスクリプトを再実行すれば自動的に再取得されます。")
            time.sleep(60)
            continue

        if not rows:
            print(f"[{i}/{total_days}] {date_str}: データ0件(休日等)")
            empty_days += 1
            continue

        db.upsert_stocks_daily(conn, rows)
        print(f"[{i}/{total_days}] {date_str}: {len(rows)} 件を保存")
        fetched_days += 1

        # 呼び出し間隔を空けてレート制限を回避
        time.sleep(client.sleep_sec)

    conn.close()
    print("\n===== 完了 =====")
    print(f"取得済み(新規保存): {fetched_days} 日")
    print(f"スキップ(既取得):   {skipped_days} 日")
    print(f"データなし(休日等): {empty_days} 日")
    return 0


if __name__ == "__main__":
    sys.exit(main())
