"""
analyze_breakout_vs_reversal.py と同じ利確/損切りシミュレーションを行い、
その結果を33業種区分(stocks_masterテーブルのsector33_name)ごとに
集計して比較する分析スクリプト。

業種によって「52週高値圏からの成功率」に差があるかを確認する。

使い方:
  python analyze_by_sector.py
  python analyze_by_sector.py --min-events 100   # 業種ごとの最低イベント数を変更
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from analyze_new_highs import DB_PATH, MIN_HISTORY_DAYS_52W, load_data, detect_new_highs
from analyze_breakout_vs_reversal import (
    DEFAULT_COHORT_MAX_BELOW_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_HOLD_DAYS,
    simulate_trade_outcomes,
)
from technical_features import add_precursor_features

OUTPUT_DIR = Path(__file__).parent / "analysis_output"

# 業種ごとの結果が偶然のブレで大きく見えてしまわないよう、最低限これだけの
# 判定済みトレード数がある業種だけを結果に表示する
DEFAULT_MIN_EVENTS_PER_SECTOR = 200


def load_sector_map(db_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT code, sector33_name FROM stocks_master", conn)
    conn.close()
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="利確/損切りシミュレーション結果を業種別に集計する")
    parser.add_argument("--cohort-max-below", type=float, default=DEFAULT_COHORT_MAX_BELOW_PCT, help="52週高値から何%%以内を対象にするか")
    parser.add_argument("--take-profit", type=float, default=DEFAULT_TAKE_PROFIT_PCT, help="利確ライン(%%)")
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS_PCT, help="損切りライン(%%)")
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="最大保有営業日数")
    parser.add_argument("--min-events", type=int, default=DEFAULT_MIN_EVENTS_PER_SECTOR, help="結果に表示する業種の最低イベント数")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db))
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
        return 1

    sector_map = load_sector_map(Path(args.db))
    if sector_map.empty or sector_map["sector33_name"].isna().all():
        print("[ERROR] 業種情報(stocks_master.sector33_name)がありません。main.py を実行してマスタを取得してください。")
        return 1

    print(f"読み込んだデータ: {len(df):,} 行 / {df['code'].nunique():,} 銘柄")
    print(
        f"対象コホート: 52週高値から{args.cohort_max_below:.0f}%以内(更新済み含む) / "
        f"利確+{args.take_profit:.0f}% / 損切り-{args.stop_loss:.0f}% / 最大保有{args.hold_days}営業日"
    )

    df = detect_new_highs(df)
    df = add_precursor_features(df)

    cohort_mask = (
        (df["prior_52w_count"] >= MIN_HISTORY_DAYS_52W)
        & (df["pct_below_52w_high"] <= args.cohort_max_below)
    )
    n_cohort = int(cohort_mask.sum())
    print(f"コホート該当日数: {n_cohort:,} 件")
    if n_cohort == 0:
        print("[WARN] 条件に合う日がありませんでした。--cohort-max-below を緩めてみてください。")
        return 0

    print("シミュレーション実行中(銘柄数が多いと数分かかることがあります)...")
    df = simulate_trade_outcomes(
        df,
        entry_mask=cohort_mask,
        hold_days=args.hold_days,
        take_profit_pct=args.take_profit,
        stop_loss_pct=args.stop_loss,
    )

    judged = df[df["trade_outcome"].notna()].copy()
    n_judged = len(judged)
    print(f"判定できたトレード数: {n_judged:,} 件")
    if n_judged == 0:
        print("[WARN] 判定できたトレードがありませんでした。")
        return 0

    judged = judged.merge(sector_map, on="code", how="left")
    judged["sector33_name"] = judged["sector33_name"].fillna("(不明)")

    win_labels = ["take_profit", "timeout_win"]

    rows = []
    for sector, g in judged.groupby("sector33_name"):
        n = len(g)
        if n < args.min_events:
            continue
        counts = g["trade_outcome"].value_counts()
        rows.append(
            {
                "sector33_name": sector,
                "n_events": n,
                "win_rate_pct": round(g["trade_outcome"].isin(win_labels).mean() * 100, 1),
                "take_profit_pct": round(counts.get("take_profit", 0) / n * 100, 1),
                "stop_loss_pct": round(counts.get("stop_loss", 0) / n * 100, 1),
                "avg_return_pct": round(g["outcome_return_pct"].mean(), 2),
                "median_return_pct": round(g["outcome_return_pct"].median(), 2),
            }
        )

    result = pd.DataFrame(rows).sort_values("avg_return_pct", ascending=False)

    print(f"\n===== 業種別の結果(最低{args.min_events}件以上のイベントがある業種のみ表示) =====")
    if result.empty:
        print("表示できる業種がありませんでした(--min-events を下げてみてください)。")
    else:
        print(result.to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    result_path = OUTPUT_DIR / "sector_comparison.csv"
    result.to_csv(result_path, index=False, encoding="utf-8-sig")
    print(f"\n業種別の集計結果を保存しました: {result_path}")

    return 0


if __name__ == "__main__":
    exit(main())
