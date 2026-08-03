"""
J-Quants無料プランの「12週間遅延配信」で埋まらない直近期間を、
yfinance(遅延なし)から取得して補うスクリプト。

- 対象銘柄は stocks_master テーブル(J-Quantsのマスタ取得で作られる)から
  東証プライム銘柄コードを読み込む。
- コード変換: J-Quantsの5桁コード(例: '86970')は、末尾に'0'を付けた形式
  になっていることが多いため、Yahoo Finance用には先頭4桁 + '.T' に変換
  する(例: '86970' -> '8697.T')。
- 大量銘柄を1件ずつ取得すると遅い上にYahoo側の負荷制限に引っかかりやすい
  ため、CHUNK_SIZE銘柄ずつまとめてダウンロードする。

使い方:
  python fetch_stocks_yf.py                  # 直近95日分を取得
  python fetch_stocks_yf.py --days 30         # 直近30日分だけ取得
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

import db

CHUNK_SIZE = 100
SLEEP_BETWEEN_CHUNKS_SEC = 2.0

# J-Quants無料プランの遅延日数(84日)より少し長めに取得し、
# データの抜けが出ないようにバッファを持たせる。
DEFAULT_DAYS = 95


def default_date_range(days: int = DEFAULT_DAYS) -> tuple[str, str]:
    """(開始日, 終了日)を 'YYYY-MM-DD' 文字列で返す。他モジュールから呼び出す用。"""
    end_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    return start_date, end_date


def jquants_code_to_yahoo_ticker(code: str) -> str:
    """例: '86970' -> '8697.T' / 4桁のまま(末尾0でない)場合はそのまま+.T"""
    if len(code) == 5 and code.endswith("0"):
        return f"{code[:4]}.T"
    return f"{code}.T"


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def load_prime_codes(conn) -> list[str]:
    cur = conn.execute("SELECT code FROM stocks_master WHERE market_code = '0111'")
    return [row[0] for row in cur.fetchall()]


def fetch_and_store(conn, codes: list[str], start_date: str, end_date: str) -> tuple[int, int]:
    """
    codesに対応するyfinanceデータを取得してDBに保存する。
    戻り値: (保存した行数, 取得に失敗した銘柄数)
    """
    ticker_to_code = {jquants_code_to_yahoo_ticker(c): c for c in codes}
    tickers = list(ticker_to_code.keys())

    total_rows_saved = 0
    failed_count = 0

    for chunk in chunked(tickers, CHUNK_SIZE):
        try:
            data = yf.download(
                tickers=chunk,
                start=start_date,
                end=end_date,
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception as e:
            print(f"  [WARN] チャンク取得に失敗しました({len(chunk)}銘柄): {e}")
            failed_count += len(chunk)
            time.sleep(SLEEP_BETWEEN_CHUNKS_SEC)
            continue

        rows = []
        for ticker in chunk:
            code = ticker_to_code[ticker]
            try:
                # 銘柄が1件だけの場合、yfinanceはgroup_by無しの形式で返すことがあるため分岐
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.get_level_values(0):
                        failed_count += 1
                        continue
                    sub = data[ticker]
                else:
                    sub = data

                sub = sub.dropna(subset=["Close"])
                for idx, row in sub.iterrows():
                    close = row.get("Close")
                    adj_close = row.get("Adj Close", close)
                    if close is None or pd.isna(close):
                        continue
                    adj_factor = float(adj_close) / float(close) if close else 1.0
                    rows.append(
                        {
                            "code": code,
                            "date": idx.strftime("%Y-%m-%d"),
                            "open": float(row["Open"]) if pd.notna(row.get("Open")) else None,
                            "high": float(row["High"]) if pd.notna(row.get("High")) else None,
                            "low": float(row["Low"]) if pd.notna(row.get("Low")) else None,
                            "close": float(close),
                            "volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
                            "turnover": None,  # yfinanceは売買代金を提供しないため空にしておく
                            "adj_factor": round(adj_factor, 6),
                        }
                    )
            except Exception as e:
                print(f"  [WARN] {ticker} の処理に失敗しました: {e}")
                failed_count += 1

        if rows:
            db.upsert_stocks_daily(conn, rows)
            total_rows_saved += len(rows)

        print(f"  -> {len(chunk)}銘柄分のチャンク処理完了(累計保存 {total_rows_saved} 行)")
        time.sleep(SLEEP_BETWEEN_CHUNKS_SEC)

    return total_rows_saved, failed_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="yfinanceで直近の東証プライム銘柄データを取得(J-Quantsの遅延配信の穴埋め用)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"何日前まで遡って取得するか(デフォルト: {DEFAULT_DAYS}日)",
    )
    args = parser.parse_args()

    conn = db.get_connection()
    db.init_db(conn)

    codes = load_prime_codes(conn)
    if not codes:
        print(
            "[ERROR] stocks_master にプライム銘柄がありません。"
            "先に main.py または backfill.py を実行してマスタを取得してください。",
            file=sys.stderr,
        )
        conn.close()
        return 1

    start_date, end_date = default_date_range(args.days)

    print(f"対象銘柄数: {len(codes)}")
    print(f"取得期間: {start_date} 〜 {end_date}")

    saved, failed = fetch_and_store(conn, codes, start_date, end_date)

    conn.close()
    print("\n===== 完了 =====")
    print(f"保存した行数: {saved}")
    print(f"取得に失敗した銘柄数: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
