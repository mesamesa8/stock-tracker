"""
②「52週新高値更新した銘柄のその後の値動きを観測して、法則を見つける」ための分析スクリプト。

検出する2種類の高値更新:
  - 52週高値更新: 過去364日間(当日を除く)の最高値を、当日の高値が上回った日
  - 年初来高値更新: その年の1/1〜前日までの最高値を、当日の高値が上回った日

52週高値については、過去のデータが一定日数(MIN_HISTORY_DAYS_52W)溜まっていない
銘柄・期間は「データ不足による見せかけの高値更新」の可能性があるため、集計対象から
除外する。

出力:
  - 高値更新後 1/5/10/20/60営業日後のリターン分布(勝率・平均・中央値等)を集計
  - 個別の高値更新イベント一覧をCSVに出力(検証・目視確認用)

使い方:
  python analyze_new_highs.py
  python analyze_new_highs.py --code 86970   # 特定銘柄だけに絞って確認したい場合
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = Path(__file__).parent / "market_data.sqlite3"
OUTPUT_DIR = Path(__file__).parent / "analysis_output"

# 52週高値の判定に必要な最低営業日数。364日ローリングウィンドウ内に
# これだけの営業日データが無い場合は「データ不足」として判定対象から外す。
# (日本市場の年間営業日数は約245日。多少の欠損を許容して200日を基準にする)
MIN_HISTORY_DAYS_52W = 200

# 高値更新後、何営業日後のリターンを観測するか
FORWARD_HORIZONS = [1, 5, 10, 20, 60]


def load_data(db_path: Path = DB_PATH, code: str | None = None) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    query = "SELECT code, date, open, high, low, close, volume FROM stocks_daily"
    params: tuple = ()
    if code:
        query += " WHERE code = ?"
        params = (code,)
    df = pd.read_sql(query, conn, params=params, parse_dates=["date"])
    conn.close()

    df = df.dropna(subset=["close", "high"])
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df


def detect_new_highs(df: pd.DataFrame) -> pd.DataFrame:
    """52週高値・年初来高値の更新フラグを付与したDataFrameを返す。"""

    per_code_results = []

    for code, g in df.groupby("code", sort=False):
        g = g.sort_values("date").set_index("date")

        # --- 52週高値(過去364日、当日を含まない) ---
        prior_52w_high = g["high"].rolling("364D", closed="left").max()
        prior_52w_count = g["high"].rolling("364D", closed="left").count()
        g["prior_52w_high"] = prior_52w_high
        g["prior_52w_count"] = prior_52w_count

        # --- 年初来高値(同一年内、当日を含まない) ---
        g["prior_ytd_high"] = (
            g.groupby(g.index.year)["high"].transform(lambda s: s.shift(1).cummax())
        )

        per_code_results.append(g.reset_index().assign(code=code))

    out = pd.concat(per_code_results, ignore_index=True)

    out["is_52w_high_break"] = (out["prior_52w_count"] >= MIN_HISTORY_DAYS_52W) & (
        out["high"] > out["prior_52w_high"]
    )
    out["is_ytd_high_break"] = out["prior_ytd_high"].notna() & (
        out["high"] > out["prior_ytd_high"]
    )

    return out


def add_forward_returns(df: pd.DataFrame, horizons: list[int] = FORWARD_HORIZONS) -> pd.DataFrame:
    """高値更新日の終値から、N営業日後の終値までのリターン(%)を計算して列を追加する。"""
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    for h in horizons:
        fwd_close = df.groupby("code")["close"].shift(-h)
        df[f"fwd_return_{h}d"] = (fwd_close - df["close"]) / df["close"] * 100
    return df


# 1日の変動率がこの値(%)を超える日は、株式分割の未調整やデータ異常の
# 可能性が高いとみなし、その日を含む前方ウィンドウのリターンは集計から除外する。
ANOMALY_DAILY_RETURN_THRESHOLD = 25.0


def add_dedup_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    連続する高値更新日(同じ上昇トレンド中に何日も高値を更新し続けるケース)を
    1つのイベントとして数えるため、「直前の日は高値更新でなかった」日だけを
    is_..._break_first としてフラグ付けする。
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    prev_52w = df.groupby("code")["is_52w_high_break"].shift(1).fillna(False).astype(bool)
    df["is_52w_high_break_first"] = df["is_52w_high_break"] & ~prev_52w

    prev_ytd = df.groupby("code")["is_ytd_high_break"].shift(1).fillna(False).astype(bool)
    df["is_ytd_high_break_first"] = df["is_ytd_high_break"] & ~prev_ytd

    return df


def add_anomaly_flags(df: pd.DataFrame, horizons: list[int] = FORWARD_HORIZONS) -> pd.DataFrame:
    """
    1日の変動率が異常に大きい日(株式分割の未調整等の疑い)を検出し、
    各horizonの前方ウィンドウ内にそうした日が含まれるかをフラグ付けする。
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df["daily_return_pct"] = df.groupby("code")["close"].pct_change() * 100
    df["is_anomaly_day"] = df["daily_return_pct"].abs() > ANOMALY_DAILY_RETURN_THRESHOLD

    for h in horizons:
        shifted = df.groupby("code")["is_anomaly_day"].shift(-1)
        # 逆順にしてrollingすることで「これから先h日分」の集計を実現する
        rev = shifted[::-1]
        rolled = rev.rolling(window=h, min_periods=1).sum()
        df[f"anomaly_in_window_{h}d"] = rolled[::-1].fillna(0) > 0

    return df


def add_excess_returns(df: pd.DataFrame, horizons: list[int] = FORWARD_HORIZONS) -> pd.DataFrame:
    """
    同じ日に全銘柄が平均してどれだけ動いたか(市場全体の地合い)を差し引いた
    「超過リターン」を計算する。地合いだけでプラスになっていないかを見るため。
    """
    for h in horizons:
        market_mean = df.groupby("date")[f"fwd_return_{h}d"].transform("mean")
        df[f"market_mean_return_{h}d"] = market_mean
        df[f"excess_return_{h}d"] = df[f"fwd_return_{h}d"] - market_mean
    return df


def summarize(
    df: pd.DataFrame,
    break_col: str,
    label: str,
    horizons: list[int] = FORWARD_HORIZONS,
    exclude_anomalies: bool = True,
) -> pd.DataFrame:
    """高値更新イベント後のリターン分布(生リターンと市場調整後の超過リターン)を集計する。"""
    events = df[df[break_col]]
    rows = []
    for h in horizons:
        col = f"fwd_return_{h}d"
        excess_col = f"excess_return_{h}d"

        subset = events
        if exclude_anomalies:
            subset = subset[~subset[f"anomaly_in_window_{h}d"]]

        valid = subset[[col, excess_col]].dropna()
        if len(valid) == 0:
            continue

        rows.append(
            {
                "breakout_type": label,
                "horizon_days": h,
                "n_events": int(len(valid)),
                "win_rate_pct": round((valid[col] > 0).mean() * 100, 1),
                "mean_return_pct": round(valid[col].mean(), 2),
                "median_return_pct": round(valid[col].median(), 2),
                "std_return_pct": round(valid[col].std(), 2),
                "mean_excess_vs_market_pct": round(valid[excess_col].mean(), 2),
                "median_excess_vs_market_pct": round(valid[excess_col].median(), 2),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="52週高値・年初来高値更新の分析")
    parser.add_argument("--code", default=None, help="特定の銘柄コードだけに絞る場合(例: 86970)")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db), code=args.code)
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
        return 1

    n_dates = df["date"].nunique()
    print(f"読み込んだデータ: {len(df):,} 行 / {df['code'].nunique():,} 銘柄 / 営業日数 {n_dates:,} 日")
    if n_dates < MIN_HISTORY_DAYS_52W:
        print(
            f"[WARN] 営業日数が {MIN_HISTORY_DAYS_52W} 日未満のため、52週高値の判定対象は"
            "まだ非常に少ない(あるいはゼロの)可能性があります。"
        )

    print("高値更新の検出中...")
    df = detect_new_highs(df)
    df = add_forward_returns(df)
    df = add_dedup_flags(df)
    df = add_anomaly_flags(df)
    df = add_excess_returns(df)

    n_52w_raw = int(df["is_52w_high_break"].sum())
    n_52w_first = int(df["is_52w_high_break_first"].sum())
    n_ytd_raw = int(df["is_ytd_high_break"].sum())
    n_ytd_first = int(df["is_ytd_high_break_first"].sum())
    print(f"52週高値更新イベント: 延べ{n_52w_raw:,}件 / 連続分をまとめると{n_52w_first:,}件")
    print(f"年初来高値更新イベント: 延べ{n_ytd_raw:,}件 / 連続分をまとめると{n_ytd_first:,}件")

    n_anomaly_days = int(df["is_anomaly_day"].sum())
    print(f"1日の変動率が±{ANOMALY_DAILY_RETURN_THRESHOLD:.0f}%を超えた日(データ異常の疑い): {n_anomaly_days:,}件")

    # 「連続分をまとめた初回ブレイクのみ」を基本の集計対象にする
    summary_52w = summarize(df, "is_52w_high_break_first", "52週高値更新(初回のみ)")
    summary_ytd = summarize(df, "is_ytd_high_break_first", "年初来高値更新(初回のみ)")
    summary = pd.concat([summary_52w, summary_ytd], ignore_index=True)

    print("\n===== その後の値動き(集計結果: 連続イベント除外・データ異常除外・市場調整後リターン付き) =====")
    if summary.empty:
        print("集計可能なイベントがまだありません(データが足りない可能性があります)。")
    else:
        print(summary.to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    summary_path = OUTPUT_DIR / "new_highs_summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"\n集計結果を保存しました: {summary_path}")

    events_cols = (
        [
            "code",
            "date",
            "high",
            "close",
            "prior_52w_high",
            "prior_ytd_high",
            "is_52w_high_break",
            "is_52w_high_break_first",
            "is_ytd_high_break",
            "is_ytd_high_break_first",
        ]
        + [f"fwd_return_{h}d" for h in FORWARD_HORIZONS]
        + [f"excess_return_{h}d" for h in FORWARD_HORIZONS]
        + [f"anomaly_in_window_{h}d" for h in FORWARD_HORIZONS]
    )
    events = df[(df["is_52w_high_break_first"]) | (df["is_ytd_high_break_first"])][events_cols]
    events_path = OUTPUT_DIR / "new_highs_events.csv"
    events.to_csv(events_path, index=False, encoding="utf-8-sig")
    print(f"個別イベント一覧を保存しました: {events_path}")

    anomaly_days = df[df["is_anomaly_day"]][["code", "date", "close", "daily_return_pct"]]
    anomaly_path = OUTPUT_DIR / "anomaly_days.csv"
    anomaly_days.to_csv(anomaly_path, index=False, encoding="utf-8-sig")
    print(f"データ異常の疑いがある日の一覧を保存しました: {anomaly_path}")

    return 0


if __name__ == "__main__":
    exit(main())
