"""
これまでの分析で見つかった以下のルールを実際にフィルターとして適用し、
フィルターなし(コホート全体)の場合と成績を比較するバックテストスクリプト。

  ルール:
    - 直近120日の52週高値更新回数が max-breakout-count 回以下
    - 直前20日間の値上がり率が min-momentum 〜 max-momentum の範囲内
      (横ばい/下落でもなく、急騰しすぎでもない「適度な上昇」)

使い方:
  python backtest_rule.py
  python backtest_rule.py --max-breakout-count 3 --min-momentum 1 --max-momentum 8
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

DEFAULT_MAX_BREAKOUT_COUNT = 3
DEFAULT_MIN_MOMENTUM = 1.0
DEFAULT_MAX_MOMENTUM = 8.0


def summarize_outcomes(judged: pd.DataFrame, label: str) -> dict:
    n = len(judged)
    if n == 0:
        return {"label": label, "n": 0}
    counts = judged["trade_outcome"].value_counts()
    return {
        "label": label,
        "n": n,
        "take_profit_pct": round(counts.get("take_profit", 0) / n * 100, 1),
        "stop_loss_pct": round(counts.get("stop_loss", 0) / n * 100, 1),
        "timeout_win_pct": round(counts.get("timeout_win", 0) / n * 100, 1),
        "timeout_loss_pct": round(counts.get("timeout_loss", 0) / n * 100, 1),
        "win_rate_pct": round(judged["trade_outcome"].isin(WIN_LABELS).mean() * 100, 1),
        "avg_return_pct": round(judged["outcome_return_pct"].mean(), 2),
        "median_return_pct": round(judged["outcome_return_pct"].median(), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="発見したルールをフィルターとして適用し、バックテストする")
    parser.add_argument("--cohort-max-below", type=float, default=DEFAULT_COHORT_MAX_BELOW_PCT, help="52週高値から何%%以内を対象にするか")
    parser.add_argument("--take-profit", type=float, default=DEFAULT_TAKE_PROFIT_PCT, help="利確ライン(%%)")
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS_PCT, help="損切りライン(%%)")
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="最大保有営業日数")
    parser.add_argument("--max-breakout-count", type=int, default=DEFAULT_MAX_BREAKOUT_COUNT, help="直近120日の高値更新回数の上限")
    parser.add_argument("--min-momentum", type=float, default=DEFAULT_MIN_MOMENTUM, help="直前20日値上がり率の下限(%%)")
    parser.add_argument("--max-momentum", type=float, default=DEFAULT_MAX_MOMENTUM, help="直前20日値上がり率の上限(%%)")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db))
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
        return 1

    print(f"読み込んだデータ: {len(df):,} 行 / {df['code'].nunique():,} 銘柄")
    print(
        f"ルール: 高値更新回数 <= {args.max_breakout_count}回 かつ "
        f"直前20日値上がり率が {args.min_momentum:.1f}% 〜 {args.max_momentum:.1f}%"
    )

    df = detect_new_highs(df)
    df = add_precursor_features(df)

    base_cohort_mask = (
        (df["prior_52w_count"] >= MIN_HISTORY_DAYS_52W)
        & (df["pct_below_52w_high"] <= args.cohort_max_below)
    )

    rule_mask = (
        base_cohort_mask
        & (df["recent_breakout_count_120d"] <= args.max_breakout_count)
        & (df["trailing_return_pct"] >= args.min_momentum)
        & (df["trailing_return_pct"] <= args.max_momentum)
    )

    print(f"コホート該当日数(フィルターなし): {int(base_cohort_mask.sum()):,} 件")
    print(f"ルール適用後の該当日数: {int(rule_mask.sum()):,} 件")

    print("\nシミュレーション実行中(銘柄数が多いと数分かかることがあります)...")
    # 「フィルターなし」と「ルール適用」を同時にシミュレーションすると二重実行になるため、
    # rule_maskを含む形でbase_cohort_maskを1回だけシミュレーションし、後でrule_maskで絞り込む。
    df = simulate_trade_outcomes(
        df,
        entry_mask=base_cohort_mask,
        hold_days=args.hold_days,
        take_profit_pct=args.take_profit,
        stop_loss_pct=args.stop_loss,
    )

    judged_all = df[df["trade_outcome"].notna() & base_cohort_mask].copy()
    judged_rule = df[df["trade_outcome"].notna() & rule_mask].copy()

    print(f"\n判定できたトレード数(フィルターなし): {len(judged_all):,} 件")
    print(f"判定できたトレード数(ルール適用後): {len(judged_rule):,} 件")

    if judged_rule.empty:
        print("[WARN] ルール適用後のトレードがありませんでした。条件を緩めてみてください。")
        return 0

    summary_all = summarize_outcomes(judged_all, "フィルターなし(全コホート)")
    summary_rule = summarize_outcomes(judged_rule, "ルール適用後")
    comparison = pd.DataFrame([summary_all, summary_rule])

    print("\n===== バックテスト結果の比較 =====")
    print(comparison.to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    comparison_path = OUTPUT_DIR / "backtest_rule_comparison.csv"
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    print(f"\n比較結果を保存しました: {comparison_path}")

    events_cols = [
        "code", "date", "close", "recent_breakout_count_120d", "trailing_return_pct",
        "trade_outcome", "days_to_outcome", "outcome_return_pct",
    ]
    events_path = OUTPUT_DIR / "backtest_rule_events.csv"
    judged_rule[events_cols].sort_values("date").to_csv(events_path, index=False, encoding="utf-8-sig")
    print(f"ルール適用後の個別イベント一覧を保存しました: {events_path}")

    return 0


if __name__ == "__main__":
    exit(main())
