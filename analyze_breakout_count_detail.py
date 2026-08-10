"""
「直近120日の高値更新回数(recent_breakout_count_120d)」について、
0〜4回それぞれを1回刻みで細かく見る詳細分析スクリプト。

さらに、それぞれの回数ごとに「出来高倍率(volume_ratio_vs_avg)」と
「直前の値上がり率(trailing_return_pct)」で3分位に分けた場合の勝率・
平均リターンも確認する(組み合わせで効果が変わらないかを見るため)。

使い方:
  python analyze_breakout_count_detail.py
  python analyze_breakout_count_detail.py --max-count 6
"""

from __future__ import annotations

import argparse
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

WIN_LABELS = ["take_profit", "timeout_win"]
DEFAULT_MAX_COUNT = 4  # 0〜この回数まで、1回刻みで見る


def exact_count_breakdown(df: pd.DataFrame, max_count: int) -> pd.DataFrame:
    """recent_breakout_count_120d を 0,1,2,...,max_count 回ずつ、1回刻みで集計する。"""
    rows = []
    for count_val in range(0, max_count + 1):
        g = df[df["breakout_count_int"] == count_val]
        if len(g) == 0:
            continue
        rows.append(
            {
                "breakout_count": count_val,
                "n": len(g),
                "win_rate_pct": round(g["win"].mean() * 100, 1),
                "avg_return_pct": round(g["outcome_return_pct"].mean(), 2),
                "avg_volume_ratio": round(g["volume_ratio_vs_avg"].mean(), 2),
                "avg_trailing_return_pct": round(g["trailing_return_pct"].mean(), 2),
            }
        )
    # max_countを超える分もまとめて1行表示する(比較用)
    g_rest = df[df["breakout_count_int"] > max_count]
    if len(g_rest) > 0:
        rows.append(
            {
                "breakout_count": f"{max_count + 1}回以上",
                "n": len(g_rest),
                "win_rate_pct": round(g_rest["win"].mean() * 100, 1),
                "avg_return_pct": round(g_rest["outcome_return_pct"].mean(), 2),
                "avg_volume_ratio": round(g_rest["volume_ratio_vs_avg"].mean(), 2),
                "avg_trailing_return_pct": round(g_rest["trailing_return_pct"].mean(), 2),
            }
        )
    return pd.DataFrame(rows)


def two_way_breakdown(
    df: pd.DataFrame, count_values: list[int], secondary_col: str, n_bins: int = 3, min_group_size: int = 50
) -> pd.DataFrame:
    """高値更新回数(count_values)ごとに、secondary_colを分位で分けた場合の勝率・平均リターンを見る。"""
    rows = []
    for count_val in count_values:
        sub = df[df["breakout_count_int"] == count_val].copy()
        if len(sub) < n_bins * min_group_size:
            continue
        try:
            sub["_bin"] = pd.qcut(sub[secondary_col], n_bins, duplicates="drop")
        except ValueError:
            continue
        for bin_label, g in sub.groupby("_bin", observed=True):
            if len(g) < min_group_size:
                continue
            rows.append(
                {
                    "breakout_count": count_val,
                    f"{secondary_col}_bin": str(bin_label),
                    "n": len(g),
                    "win_rate_pct": round(g["win"].mean() * 100, 1),
                    "avg_return_pct": round(g["outcome_return_pct"].mean(), 2),
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="高値更新回数(0〜N回)を1回刻みで詳細分析する")
    parser.add_argument("--max-count", type=int, default=DEFAULT_MAX_COUNT, help="1回刻みで見る最大回数")
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
    judged["breakout_count_int"] = judged["recent_breakout_count_120d"].round().astype("Int64")

    print(f"\n===== 高値更新回数を1回刻みで見た結果(0〜{args.max_count}回) =====")
    exact_table = exact_count_breakdown(judged, args.max_count)
    print(exact_table.to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    exact_table.to_csv(OUTPUT_DIR / "breakout_count_exact.csv", index=False, encoding="utf-8-sig")

    count_values = list(range(0, args.max_count + 1))

    print("\n===== 高値更新回数 × 出来高倍率(3分位)の組み合わせ =====")
    vol_table = two_way_breakdown(judged, count_values, "volume_ratio_vs_avg")
    if vol_table.empty:
        print("十分なデータがありませんでした。")
    else:
        print(vol_table.to_string(index=False))
        vol_table.to_csv(OUTPUT_DIR / "breakout_count_x_volume.csv", index=False, encoding="utf-8-sig")

    print("\n===== 高値更新回数 × 直前の値上がり率(3分位)の組み合わせ =====")
    momentum_table = two_way_breakdown(judged, count_values, "trailing_return_pct")
    if momentum_table.empty:
        print("十分なデータがありませんでした。")
    else:
        print(momentum_table.to_string(index=False))
        momentum_table.to_csv(OUTPUT_DIR / "breakout_count_x_momentum.csv", index=False, encoding="utf-8-sig")

    print(f"\n全ての集計結果を {OUTPUT_DIR} に保存しました。")
    return 0


if __name__ == "__main__":
    exit(main())
