"""
backtest_rule.py で確立したエントリールール(高値更新回数の上限・適度な
上昇率の範囲)を固定した上で、利確ライン(take-profit)・損切りライン
(stop-loss)の組み合わせを総当たりで試し、平均リターンが最も良い設定を
探す最適化スクリプト。

使い方:
  python optimize_tp_sl.py
  python optimize_tp_sl.py --tp-grid 8,10,12,15,20,25 --sl-grid 4,6,8,10,12
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from analyze_new_highs import DB_PATH, MIN_HISTORY_DAYS_52W, load_data, detect_new_highs
from analyze_breakout_vs_reversal import DEFAULT_COHORT_MAX_BELOW_PCT, DEFAULT_HOLD_DAYS, simulate_trade_outcomes
from backtest_rule import DEFAULT_MAX_BREAKOUT_COUNT, DEFAULT_MIN_MOMENTUM, DEFAULT_MAX_MOMENTUM
from technical_features import add_precursor_features

OUTPUT_DIR = Path(__file__).parent / "analysis_output"

WIN_LABELS = ["take_profit", "timeout_win"]

DEFAULT_TP_GRID = "8,10,12,15,20"
DEFAULT_SL_GRID = "5,6,8,10,12"


def parse_grid(s: str) -> list[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="利確/損切りラインの組み合わせを総当たりで最適化する")
    parser.add_argument("--tp-grid", default=DEFAULT_TP_GRID, help="試す利確ラインのリスト(カンマ区切り、%%)")
    parser.add_argument("--sl-grid", default=DEFAULT_SL_GRID, help="試す損切りラインのリスト(カンマ区切り、%%)")
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="最大保有営業日数")
    parser.add_argument("--cohort-max-below", type=float, default=DEFAULT_COHORT_MAX_BELOW_PCT, help="52週高値から何%%以内を対象にするか")
    parser.add_argument("--max-breakout-count", type=int, default=DEFAULT_MAX_BREAKOUT_COUNT, help="直近120日の高値更新回数の上限")
    parser.add_argument("--min-momentum", type=float, default=DEFAULT_MIN_MOMENTUM, help="直前20日値上がり率の下限(%%)")
    parser.add_argument("--max-momentum", type=float, default=DEFAULT_MAX_MOMENTUM, help="直前20日値上がり率の上限(%%)")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    tp_grid = parse_grid(args.tp_grid)
    sl_grid = parse_grid(args.sl_grid)
    n_combos = len(tp_grid) * len(sl_grid)

    df = load_data(Path(args.db))
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
        return 1

    print(f"読み込んだデータ: {len(df):,} 行 / {df['code'].nunique():,} 銘柄")
    print(f"試す組み合わせ数: 利確{len(tp_grid)}パターン × 損切り{len(sl_grid)}パターン = {n_combos}通り")
    print("(組み合わせ数が多いと時間がかかります。目安として数分〜数十分程度)")

    df = detect_new_highs(df)
    df = add_precursor_features(df)

    rule_mask = (
        (df["prior_52w_count"] >= MIN_HISTORY_DAYS_52W)
        & (df["pct_below_52w_high"] <= args.cohort_max_below)
        & (df["recent_breakout_count_120d"] <= args.max_breakout_count)
        & (df["trailing_return_pct"] >= args.min_momentum)
        & (df["trailing_return_pct"] <= args.max_momentum)
    )
    print(f"エントリールール該当件数: {int(rule_mask.sum()):,} 件")
    if rule_mask.sum() == 0:
        print("[WARN] 条件に合うエントリーがありませんでした。")
        return 0

    # シミュレーションに必要な列だけに絞って軽量化する
    slim_df = df[["code", "date", "high", "low", "close"]].copy()

    results = []
    done = 0
    for tp in tp_grid:
        for sl in sl_grid:
            done += 1
            sim = simulate_trade_outcomes(
                slim_df.copy(),
                entry_mask=rule_mask,
                hold_days=args.hold_days,
                take_profit_pct=tp,
                stop_loss_pct=sl,
            )
            judged = sim[sim["trade_outcome"].notna() & rule_mask]
            n = len(judged)
            if n == 0:
                print(f"  [{done}/{n_combos}] TP={tp}% SL={sl}%: 判定可能なトレードなし")
                continue

            counts = judged["trade_outcome"].value_counts()
            results.append(
                {
                    "take_profit_pct_setting": tp,
                    "stop_loss_pct_setting": sl,
                    "n": n,
                    "win_rate_pct": round(judged["trade_outcome"].isin(WIN_LABELS).mean() * 100, 1),
                    "avg_return_pct": round(judged["outcome_return_pct"].mean(), 2),
                    "median_return_pct": round(judged["outcome_return_pct"].median(), 2),
                    "std_return_pct": round(judged["outcome_return_pct"].std(), 2),
                    "take_profit_hit_pct": round(counts.get("take_profit", 0) / n * 100, 1),
                    "stop_loss_hit_pct": round(counts.get("stop_loss", 0) / n * 100, 1),
                }
            )
            print(
                f"  [{done}/{n_combos}] TP={tp}% SL={sl}%: "
                f"勝率{results[-1]['win_rate_pct']}% 平均{results[-1]['avg_return_pct']}%"
            )

    result_df = pd.DataFrame(results).sort_values("avg_return_pct", ascending=False)

    print("\n===== 結果(平均リターンの高い順、上位15件) =====")
    print(result_df.head(15).to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    result_path = OUTPUT_DIR / "optimize_tp_sl_results.csv"
    result_df.to_csv(result_path, index=False, encoding="utf-8-sig")
    print(f"\n全組み合わせの結果を保存しました: {result_path}")

    return 0


if __name__ == "__main__":
    exit(main())
