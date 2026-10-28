"""
J-Quants から決算短信のサマリー(売上高・営業利益など)を取得してDBに保存するスクリプト。

日付指定で「その日に開示された全銘柄分」をまとめて取得する(1日1リクエスト)。
無料プランは12週間より前の日付しか取得できないため、取得できる最新日 = 今日 - 84日。
一度取得した日付は fins_fetched_dates に記録して取り直さない。初回はさかのぼって
取得するが、レート制限(15秒に1回)があるため、1回の実行で取得する日数に上限を設け、
数日かけて少しずつ埋める(新しい日付から順に取得する)。

GitHub Actions で毎日 main.py の後に実行する。J-Quants の障害で失敗しても、
後続のスクリーニング等を止めないよう、エラー時も終了コード0で終わる。

使い方:
  JQUANTS_API_KEY=xxxx python fetch_financials.py
  JQUANTS_API_KEY=xxxx python fetch_financials.py --max-dates 20
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date as date_cls, timedelta

import requests

import db
from jquants_client import JQuantsClient

DELAY_DAYS = 84            # 無料プランの遅延(main.py の DEFAULT_DELAY_DAYS と同じ)
# 前年同期と、その1つ前の四半期までそろうようにさかのぼる日数。最も遠いのは通期(4Q)の場合で、
# 5月発表の通期決算に対し、前年の3Q決算(前年2月発表)が必要になる。取得できる最新日が
# 通期発表から最大3ヶ月後になることも考え、約20ヶ月とする(無料プランの範囲は2年)
LOOKBACK_DAYS = 600
DEFAULT_MAX_DATES = 150    # 1回の実行で取得する日数の上限(15秒間隔なので約40分)


def to_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def to_row(item: dict) -> dict | None:
    """決算(実績)の書類だけを、DB保存用の行に変換する。業績予想の修正などは除く。"""
    if "FinancialStatements" not in (item.get("DocType") or ""):
        return None
    return {
        "disc_no": item.get("DiscNo"),
        "code": item.get("Code"),
        "disc_date": item.get("DiscDate"),
        "doc_type": item.get("DocType"),
        "cur_per_type": item.get("CurPerType"),
        "cur_per_st": item.get("CurPerSt"),
        "cur_per_en": item.get("CurPerEn"),
        "cur_fy_st": item.get("CurFYSt"),
        "cur_fy_en": item.get("CurFYEn"),
        "sales": to_float(item.get("Sales")),
        "op": to_float(item.get("OP")),
    }


def dates_to_fetch(conn, today: date_cls, max_dates: int) -> list[str]:
    end = today - timedelta(days=DELAY_DAYS)
    start = end - timedelta(days=LOOKBACK_DAYS)
    fetched = {r[0] for r in conn.execute("SELECT date FROM fins_fetched_dates")}
    dates = []
    d = end
    while d >= start and len(dates) < max_dates:
        # 土日は開示がほぼないため取得しない(祝日は0件として記録される)
        if d.weekday() < 5 and d.isoformat() not in fetched:
            dates.append(d.isoformat())
        d -= timedelta(days=1)
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(description="決算短信サマリーを取得してDBに保存する")
    parser.add_argument("--max-dates", type=int, default=DEFAULT_MAX_DATES, help="1回で取得する日数の上限")
    parser.add_argument("--db", default=str(db.DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    conn = db.get_connection(args.db)
    db.init_db(conn)
    prime_codes = {r[0] for r in conn.execute("SELECT code FROM stocks_master")}
    targets = dates_to_fetch(conn, date_cls.today(), args.max_dates)
    print(f"決算データを取得する日: {len(targets)}日分")

    try:
        client = JQuantsClient()
    except RuntimeError as e:
        print(f"  [ERROR] {e}(決算データの取得をスキップします)", file=sys.stderr)
        conn.close()
        return 0

    saved = 0
    for i, d in enumerate(targets):
        if i:
            time.sleep(client.sleep_sec)
        try:
            items = client.get_fins_summary(d)
        except requests.RequestException as e:
            print(f"  [ERROR] {d} の取得に失敗したため中断します: {e}", file=sys.stderr)
            break
        rows = [r for r in map(to_row, items) if r and r["code"] in prime_codes]
        db.upsert_fins_summary(conn, rows)
        conn.execute("INSERT OR REPLACE INTO fins_fetched_dates (date, n_rows) VALUES (?, ?)", (d, len(rows)))
        conn.commit()
        saved += len(rows)
    conn.close()
    print(f"  -> 決算 {saved} 件を保存しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
