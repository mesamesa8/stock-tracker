"""
東証プライム全銘柄の1分足を yfinance から取得し、日付ごとのファイルに保存するスクリプト。

GitHub Actions(.github/workflows/minute_fetch.yml)から平日16時過ぎに実行される想定。

- 対象銘柄: DB(market_data.sqlite3)の stocks_master にあるプライム銘柄(fetch_stocks_yf.py と同じ)
- 取得範囲: Yahoo Finance の1分足は「直近7日分」しか取れないため、毎回7日分を取得し、
  まだ保存していない日だけをファイルに書き出す。
  → Actionsの起動が遅れたり1日失敗したりしても、7日以内なら次回の実行で自動的に埋まる。
- 出力: <out-dir>/minute_YYYY-MM-DD.csv.gz (1日1ファイル、gzip圧縮CSV)
  列: code, datetime(日本時間 'YYYY-MM-DD HH:MM'), open, high, low, close, volume
- 保存済みの日付は --existing で渡すファイル(ファイル名を1行ずつ並べたもの)から判定する。

使い方:
  python fetch_minute_yf.py --out-dir minute_out --existing existing.txt
  python fetch_minute_yf.py --out-dir minute_out --codes 72030 67580   # 銘柄を指定して試す
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
CHUNK_SIZE = 100
SLEEP_BETWEEN_CHUNKS_SEC = 3.0
RETRY_CHUNK_SIZE = 20
RETRY_WAIT_SEC = 60.0
PERIOD = "7d"  # yfinanceの1分足は1回のリクエストで最大8日分まで

# 大引け(15:30)直後は最後の数分が未反映のことがあるため、当日分はこの時刻以降のみ保存する
TODAY_READY_AFTER = (15, 45)

FILE_RE = re.compile(r"minute_(\d{4}-\d{2}-\d{2})\.csv\.gz")
COLUMNS = ["code", "datetime", "open", "high", "low", "close", "volume"]


def file_name_for(date_str: str) -> str:
    return f"minute_{date_str}.csv.gz"


def parse_existing_dates(lines: list[str]) -> set[str]:
    """ファイル名の一覧から、保存済みの日付('YYYY-MM-DD')の集合を返す。"""
    dates = set()
    for line in lines:
        m = FILE_RE.search(line.strip())
        if m:
            dates.add(m.group(1))
    return dates


def dates_to_save(available: list[str], existing: set[str], now_jst: datetime) -> list[str]:
    """取得できた日付のうち、まだ保存しておらず、データが確定している日付を返す。"""
    today = now_jst.strftime("%Y-%m-%d")
    today_ready = (now_jst.hour, now_jst.minute) >= TODAY_READY_AFTER
    result = []
    for d in sorted(set(available)):
        if d in existing:
            continue
        if d > today or (d == today and not today_ready):
            continue
        result.append(d)
    return result


def frame_to_rows(sub: pd.DataFrame, code: str) -> pd.DataFrame:
    """yfinanceの1銘柄分のDataFrameを、保存用の縦長形式に変換する。"""
    sub = sub.dropna(subset=["Close"])
    if sub.empty:
        return pd.DataFrame(columns=COLUMNS)
    idx = sub.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    idx = idx.tz_convert(JST)
    out = pd.DataFrame(
        {
            "code": code,
            "datetime": idx.strftime("%Y-%m-%d %H:%M"),
            "open": sub["Open"].to_numpy(),
            "high": sub["High"].to_numpy(),
            "low": sub["Low"].to_numpy(),
            "close": sub["Close"].to_numpy(),
            "volume": sub["Volume"].fillna(0).astype("int64").to_numpy(),
        }
    )
    return out


def download_chunk(yf, tickers: list[str]) -> pd.DataFrame | None:
    try:
        return yf.download(
            tickers=tickers,
            period=PERIOD,
            interval="1m",
            group_by="ticker",
            auto_adjust=False,
            prepost=False,
            threads=True,
            progress=False,
        )
    except Exception as e:  # ネットワークエラー等
        print(f"  [WARN] チャンク取得に失敗しました({len(tickers)}銘柄): {e}")
        return None


def extract(data: pd.DataFrame | None, tickers: list[str], ticker_to_code: dict[str, str]):
    """ダウンロード結果を銘柄ごとに分解する。戻り値: (DataFrameのリスト, 取れなかった銘柄)"""
    frames, missing = [], []
    for ticker in tickers:
        sub = None
        if data is not None and not data.empty:
            if isinstance(data.columns, pd.MultiIndex):
                if ticker in data.columns.get_level_values(0):
                    sub = data[ticker]
            elif len(tickers) == 1:
                sub = data
        rows = frame_to_rows(sub, ticker_to_code[ticker]) if sub is not None else None
        if rows is None or rows.empty:
            missing.append(ticker)
        else:
            frames.append(rows)
    return frames, missing


def fetch_all(codes: list[str]) -> tuple[pd.DataFrame, list[str]]:
    import yfinance as yf

    from fetch_stocks_yf import chunked, jquants_code_to_yahoo_ticker

    ticker_to_code = {jquants_code_to_yahoo_ticker(c): c for c in codes}
    tickers = list(ticker_to_code)
    frames, missing = [], []

    for i, chunk in enumerate(chunked(tickers, CHUNK_SIZE), start=1):
        got, miss = extract(download_chunk(yf, chunk), chunk, ticker_to_code)
        frames += got
        missing += miss
        print(f"  チャンク{i}: {len(chunk)}銘柄中 {len(got)}銘柄取得")
        time.sleep(SLEEP_BETWEEN_CHUNKS_SEC)

    # 取れなかった銘柄は、少し待ってから小分けにしてもう一度だけ試す(一時的な負荷制限対策)
    if missing:
        print(f"  {len(missing)}銘柄を{RETRY_WAIT_SEC:.0f}秒後に再取得します")
        time.sleep(RETRY_WAIT_SEC)
        still_missing = []
        for chunk in chunked(missing, RETRY_CHUNK_SIZE):
            got, miss = extract(download_chunk(yf, chunk), chunk, ticker_to_code)
            frames += got
            still_missing += miss
            time.sleep(SLEEP_BETWEEN_CHUNKS_SEC)
        missing = still_missing

    all_rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return all_rows, missing


def load_codes_from_db() -> list[str]:
    import db
    from fetch_stocks_yf import load_prime_codes

    conn = db.get_connection()
    try:
        return load_prime_codes(conn)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="東証プライム銘柄の1分足をyfinanceで取得し日付ごとに保存")
    parser.add_argument("--out-dir", default="minute_out", help="出力先フォルダ")
    parser.add_argument("--existing", help="保存済みファイル名の一覧(1行1ファイル)")
    parser.add_argument("--codes", nargs="*", help="銘柄コードを直接指定(動作確認用)")
    args = parser.parse_args()

    codes = args.codes or load_codes_from_db()
    if not codes:
        print("[ERROR] 対象銘柄がありません(stocks_master が空)", file=sys.stderr)
        return 1

    existing: set[str] = set()
    if args.existing and Path(args.existing).exists():
        existing = parse_existing_dates(Path(args.existing).read_text(encoding="utf-8").splitlines())

    now_jst = datetime.now(JST)
    print(f"対象銘柄数: {len(codes)} / 保存済みの日数(今月・先月): {len(existing)}")

    rows, missing = fetch_all(codes)
    if rows.empty:
        print("[ERROR] 1銘柄もデータを取得できませんでした(Yahooの負荷制限の可能性)", file=sys.stderr)
        return 1

    rows["date"] = rows["datetime"].str.slice(0, 10)
    targets = dates_to_save(rows["date"].unique().tolist(), existing, now_jst)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for d in targets:
        day = rows[rows["date"] == d].drop(columns="date").sort_values(["code", "datetime"])
        path = out_dir / file_name_for(d)
        day.to_csv(path, index=False, compression="gzip")
        print(f"  保存: {path.name}  {day['code'].nunique()}銘柄 / {len(day)}行")

    print("\n===== 完了 =====")
    print(f"新しく保存した日: {', '.join(targets) if targets else 'なし(休場日、または保存済み)'}")
    print(f"取得できなかった銘柄数: {len(missing)}")
    if missing:
        print("  例: " + ", ".join(missing[:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
