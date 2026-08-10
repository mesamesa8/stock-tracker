"""
これまで個別に試してきた特徴量(テクニカル指標・業種)に加えて、新しい切り口
(曜日・月・相場全体の地合い)も含めて、トレード結果(利確/損切り
シミュレーションの結果)との相関の強さを自動的にランキングする分析
スクリプト。

数値系の特徴量(RSI、移動平均乖離率など)は「相関係数」で、
カテゴリ系の特徴量(業種、曜日、月)は「グループ間の平均リターンの
ばらつき(スプレッド)」で、それぞれ影響の大きさを評価する。

使い方:
  python analyze_correlations.py
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_new_highs import DB_PATH, MIN_HISTORY_DAYS_52W, load_data, detect_new_highs
from analyze_breakout_vs_reversal import (
    DEFAULT_COHORT_MAX_BELOW_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_HOLD_DAYS,
    simulate_trade_outcomes,
)
from technical_features import add_precursor_features, FEATURE_COLS

OUTPUT_DIR = Path(__file__).parent / "analysis_output"

WIN_LABELS = ["take_profit", "timeout_win"]

# カテゴリ系特徴量について、結果を表示する最低イベント数(1カテゴリあたり)
MIN_EVENTS_PER_CATEGORY = 100


def load_sector_map(db_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT code, sector33_name FROM stocks_master", conn)
    conn.close()
    return df


def load_market_trend(db_path: Path, trailing_days: int = 20) -> pd.DataFrame:
    """日経平均(^N225)の直近trailing_days日間のリターンを、日付ごとに計算する。"""
    conn = sqlite3.connect(db_path)
    idx = pd.read_sql(
        "SELECT date, close FROM indices_daily WHERE symbol = '^N225' ORDER BY date",
        conn,
        parse_dates=["date"],
    )
    conn.close()
    if idx.empty:
        return pd.DataFrame(columns=["date", "market_trend_pct"])

    idx["market_trend_pct"] = idx["close"].pct_change(trailing_days) * 100
    return idx[["date", "market_trend_pct"]]


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
    df["weekday"] = df["date"].dt.weekday.map(lambda i: weekday_names[i])
    df["month"] = df["date"].dt.month
    return df


def compute_correlations(df: pd.DataFrame, numeric_cols: list[str]) -> pd.DataFrame:
    """数値特徴量それぞれについて、outcome_return_pct・win(勝ち負け)との相関係数を計算する。"""
    rows = []
    n_total = len(df)
    # サンプル数が多い場合、相関係数がどの程度あれば「偶然とは考えにくいか」の目安
    # (標準誤差 ≈ 1/√n という近似を使った簡易的な目安であり、厳密な有意性検定ではない)
    rough_threshold = 2 / np.sqrt(n_total) if n_total > 0 else np.nan

    for col in numeric_cols:
        sub = df[[col, "outcome_return_pct", "win"]].dropna()
        if len(sub) < 30:
            continue
        r_return = sub[col].corr(sub["outcome_return_pct"])
        r_win = sub[col].corr(sub["win"])
        rows.append(
            {
                "feature": col,
                "n": len(sub),
                "corr_with_return": round(r_return, 4),
                "corr_with_win": round(r_win, 4),
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        result["abs_corr_with_return"] = result["corr_with_return"].abs()
        result = result.sort_values("abs_corr_with_return", ascending=False).drop(columns=["abs_corr_with_return"])
    print(f"(参考: サンプル数から見て、相関係数の大きさがおおよそ±{rough_threshold:.3f}を超えると偶然のブレとは考えにくい目安)")
    return result


def compute_categorical_spread(df: pd.DataFrame, cat_cols: list[str], min_events: int = MIN_EVENTS_PER_CATEGORY) -> dict[str, pd.DataFrame]:
    """カテゴリ系特徴量それぞれについて、カテゴリ別の勝率・平均リターンを集計する。"""
    results = {}
    for col in cat_cols:
        rows = []
        for cat, g in df.groupby(col):
            if len(g) < min_events:
                continue
            rows.append(
                {
                    col: cat,
                    "n_events": len(g),
                    "win_rate_pct": round(g["win"].mean() * 100, 1),
                    "avg_return_pct": round(g["outcome_return_pct"].mean(), 2),
                }
            )
        table = pd.DataFrame(rows).sort_values("avg_return_pct", ascending=False)
        results[col] = table
    return results


def compute_quantile_breakdown(df: pd.DataFrame, col: str, n_bins: int = 5) -> pd.DataFrame:
    """数値特徴量を5分位に分けて、勝率・平均リターンがどう変化するかを見る(非線形な効果の確認用)。"""
    valid = df[[col, "outcome_return_pct", "win"]].dropna()
    if len(valid) < n_bins * 20:
        return pd.DataFrame()
    try:
        valid = valid.copy()
        valid["bin"] = pd.qcut(valid[col], n_bins, duplicates="drop")
    except ValueError:
        return pd.DataFrame()

    rows = []
    for bin_label, g in valid.groupby("bin", observed=True):
        rows.append(
            {
                "bin_range": str(bin_label),
                "n": len(g),
                "win_rate_pct": round(g["win"].mean() * 100, 1),
                "avg_return_pct": round(g["outcome_return_pct"].mean(), 2),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="トレード結果と各種特徴量の相関を自動ランキングする")
    parser.add_argument("--cohort-max-below", type=float, default=DEFAULT_COHORT_MAX_BELOW_PCT, help="52週高値から何%%以内を対象にするか")
    parser.add_argument("--take-profit", type=float, default=DEFAULT_TAKE_PROFIT_PCT, help="利確ライン(%%)")
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS_PCT, help="損切りライン(%%)")
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="最大保有営業日数")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db))
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
        return 1

    print(f"読み込んだデータ: {len(df):,} 行 / {df['code'].nunique():,} 銘柄")

    df = detect_new_highs(df)
    df = add_precursor_features(df)
    df = add_calendar_features(df)

    cohort_mask = (
        (df["prior_52w_count"] >= MIN_HISTORY_DAYS_52W)
        & (df["pct_below_52w_high"] <= args.cohort_max_below)
    )
    print(f"コホート該当日数: {int(cohort_mask.sum()):,} 件")

    print("シミュレーション実行中(銘柄数が多いと数分かかることがあります)...")
    df = simulate_trade_outcomes(
        df,
        entry_mask=cohort_mask,
        hold_days=args.hold_days,
        take_profit_pct=args.take_profit,
        stop_loss_pct=args.stop_loss,
    )

    judged = df[df["trade_outcome"].notna()].copy()
    print(f"判定できたトレード数: {len(judged):,} 件")
    if judged.empty:
        print("[WARN] 判定できたトレードがありませんでした。")
        return 0

    judged["win"] = judged["trade_outcome"].isin(WIN_LABELS).astype(int)

    # --- 業種を追加 ---
    sector_map = load_sector_map(Path(args.db))
    judged = judged.merge(sector_map, on="code", how="left")
    judged["sector33_name"] = judged["sector33_name"].fillna("(不明)")

    # --- 相場全体の地合い(日経平均の直近20日リターン)を追加 ---
    market_trend = load_market_trend(Path(args.db))
    judged = judged.merge(market_trend, on="date", how="left")

    # ===== 数値特徴量の相関ランキング =====
    numeric_cols = FEATURE_COLS + ["market_trend_pct"]
    corr_table = compute_correlations(judged, numeric_cols)
    print("\n===== 数値特徴量とトレード結果の相関(|相関係数|の大きい順) =====")
    if corr_table.empty:
        print("計算できる特徴量がありませんでした。")
    else:
        print(corr_table.to_string(index=False))

    # ===== カテゴリ特徴量ごとのばらつき =====
    cat_results = compute_categorical_spread(judged, ["sector33_name", "weekday", "month"])

    print("\n===== 曜日別 =====")
    print(cat_results["weekday"].to_string(index=False))

    print("\n===== 月別 =====")
    print(cat_results["month"].to_string(index=False))

    print("\n===== 業種別(上位10) =====")
    print(cat_results["sector33_name"].head(10).to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    corr_table.to_csv(OUTPUT_DIR / "correlations.csv", index=False, encoding="utf-8-sig")
    cat_results["weekday"].to_csv(OUTPUT_DIR / "by_weekday.csv", index=False, encoding="utf-8-sig")
    cat_results["month"].to_csv(OUTPUT_DIR / "by_month.csv", index=False, encoding="utf-8-sig")
    cat_results["sector33_name"].to_csv(OUTPUT_DIR / "by_sector_full.csv", index=False, encoding="utf-8-sig")

    # ===== 相関上位の特徴量を5分位に分けた内訳(非線形な効果がないか確認) =====
    if not corr_table.empty:
        top_features = corr_table.head(3)["feature"].tolist()
        print("\n===== 相関上位の特徴量を5分位に分けた内訳(境目の目安を確認) =====")
        for feat in top_features:
            qb = compute_quantile_breakdown(judged, feat)
            if qb.empty:
                continue
            print(f"\n--- {feat} ---")
            print(qb.to_string(index=False))
            qb.to_csv(OUTPUT_DIR / f"quantile_{feat}.csv", index=False, encoding="utf-8-sig")

    print(f"\n全ての集計結果を {OUTPUT_DIR} に保存しました。")

    return 0


if __name__ == "__main__":
    exit(main())
