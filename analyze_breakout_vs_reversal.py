"""
「52週高値を更新した、または高値圏(-10%以内)にいる銘柄」を対象に、
実際に利確ライン・損切りラインを設定してトレードした場合の結果を
シミュレーションし、「うまくブレイクしたパターン」と「損切りになった
パターン」の違いを比較する分析スクリプト。

シミュレーション内容:
  エントリー(その日の終値で買う)した後、最大 HOLD_DAYS 営業日以内に
    - 利確ライン(+TAKE_PROFIT_PCT%)に高値が先に到達 → "take_profit"
    - 損切りライン(-STOP_LOSS_PCT%)に安値が先に到達 → "stop_loss"
    - どちらにも届かないまま期間終了 → "timeout"(最終日の損益で黒字/赤字を判定)
  同じ日に両方の条件を満たす場合は、保守的に stop_loss を優先する
  (日足データでは日中の値動きの順序が分からないため)。

使い方:
  python analyze_breakout_vs_reversal.py
  python analyze_breakout_vs_reversal.py --cohort-max-below 10 --take-profit 15 --stop-loss 8 --hold-days 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_new_highs import (
    DB_PATH,
    MIN_HISTORY_DAYS_52W,
    load_data,
    detect_new_highs,
)
from technical_features import add_precursor_features, FEATURE_COLS

OUTPUT_DIR = Path(__file__).parent / "analysis_output"

DEFAULT_COHORT_MAX_BELOW_PCT = 10.0  # 52週高値から何%以内を対象にするか
DEFAULT_TAKE_PROFIT_PCT = 15.0
DEFAULT_STOP_LOSS_PCT = 8.0
DEFAULT_HOLD_DAYS = 20


def simulate_trade_outcomes(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    hold_days: int,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> pd.DataFrame:
    """
    entry_mask=Trueの行それぞれについて、その後hold_days日以内に利確/損切り
    ラインに到達するかをシミュレーションし、結果を新しい列として追加した
    DataFrameを返す。

    追加される列:
      - trade_outcome: 'take_profit' / 'stop_loss' / 'timeout_win' / 'timeout_loss' / None(判定不能)
      - days_to_outcome: 判定に要した営業日数(timeoutの場合はhold_days)
      - outcome_return_pct: 判定時点でのリターン(%)
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    outcome = pd.Series([None] * len(df), index=df.index, dtype=object)
    days_to_outcome = pd.Series([np.nan] * len(df), index=df.index)
    outcome_return = pd.Series([np.nan] * len(df), index=df.index)

    take_profit_mult = 1 + take_profit_pct / 100
    stop_loss_mult = 1 - stop_loss_pct / 100

    for code, g in df.groupby("code", sort=False):
        idx = g.index.to_numpy()
        highs = g["high"].to_numpy()
        lows = g["low"].to_numpy()
        closes = g["close"].to_numpy()
        n = len(g)

        entry_positions = np.where(entry_mask.loc[idx].to_numpy())[0]

        for pos in entry_positions:
            if pos + hold_days >= n:
                continue  # 未来のデータが足りない(直近すぎるイベント)ので判定不能のまま

            entry_price = closes[pos]
            tp_level = entry_price * take_profit_mult
            sl_level = entry_price * stop_loss_mult

            result = None
            days = hold_days
            ret = (closes[pos + hold_days] - entry_price) / entry_price * 100

            for offset in range(1, hold_days + 1):
                i = pos + offset
                hit_sl = lows[i] <= sl_level
                hit_tp = highs[i] >= tp_level
                if hit_sl:
                    result = "stop_loss"
                    days = offset
                    ret = (sl_level - entry_price) / entry_price * 100
                    break
                if hit_tp:
                    result = "take_profit"
                    days = offset
                    ret = (tp_level - entry_price) / entry_price * 100
                    break

            if result is None:
                result = "timeout_win" if ret >= 0 else "timeout_loss"

            row_idx = idx[pos]
            outcome.loc[row_idx] = result
            days_to_outcome.loc[row_idx] = days
            outcome_return.loc[row_idx] = ret

    df["trade_outcome"] = outcome
    df["days_to_outcome"] = days_to_outcome
    df["outcome_return_pct"] = outcome_return
    return df


def compare_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """利確/損切り/タイムアウトのそれぞれについて、エントリー時点の特徴量を比較する。"""
    judged = df[df["trade_outcome"].notna()]
    rows = []
    for outcome_label, g in judged.groupby("trade_outcome"):
        row = {"trade_outcome": outcome_label, "n_events": len(g)}
        for col in FEATURE_COLS:
            vals = g[col].dropna()
            row[f"{col}_mean"] = round(vals.mean(), 2) if len(vals) else None
            row[f"{col}_median"] = round(vals.median(), 2) if len(vals) else None
        if "is_52w_high_break" in g.columns:
            row["is_52w_high_break_rate_pct"] = round(g["is_52w_high_break"].mean() * 100, 1)
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="52週高値圏の銘柄について利確/損切りシミュレーションを行う")
    parser.add_argument("--cohort-max-below", type=float, default=DEFAULT_COHORT_MAX_BELOW_PCT, help="52週高値から何%%以内を対象にするか")
    parser.add_argument("--take-profit", type=float, default=DEFAULT_TAKE_PROFIT_PCT, help="利確ライン(%%)")
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS_PCT, help="損切りライン(%%)")
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="最大保有営業日数")
    parser.add_argument("--code", default=None, help="特定の銘柄コードだけに絞る場合")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db), code=args.code)
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
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

    judged = df[df["trade_outcome"].notna()]
    n_judged = len(judged)
    print(f"判定できたトレード数: {n_judged:,} 件(直近すぎて未来データが足りないものは除外)")

    if n_judged == 0:
        print("[WARN] 判定できたトレードがありませんでした。データ期間が足りない可能性があります。")
        return 0

    counts = judged["trade_outcome"].value_counts()
    total = len(judged)
    print("\n===== トレード結果の内訳 =====")
    for label in ["take_profit", "stop_loss", "timeout_win", "timeout_loss"]:
        n = int(counts.get(label, 0))
        print(f"  {label:14s}: {n:6,d} 件 ({n / total * 100:5.1f}%)")

    win_labels = ["take_profit", "timeout_win"]
    win_rate = judged["trade_outcome"].isin(win_labels).mean() * 100
    avg_return = judged["outcome_return_pct"].mean()
    print(f"\n総合勝率(利確 or タイムアウトでプラス): {win_rate:.1f}%")
    print(f"平均リターン(全トレード平均): {avg_return:.2f}%")

    comparison = compare_outcomes(df)
    print("\n===== 利確/損切り別、エントリー時点の特徴 =====")
    print(comparison.to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    comparison_path = OUTPUT_DIR / "breakout_vs_reversal_features.csv"
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    print(f"\n比較結果を保存しました: {comparison_path}")

    events_cols = [
        "code",
        "date",
        "close",
        "trailing_return_pct",
        "volume_ratio_vs_avg",
        "pct_below_52w_high",
        "pct_vs_ma25",
        "pct_vs_ma75",
        "pct_vs_ma200",
        "ma25_above_ma75",
        "rsi14",
        "is_52w_high_break",
        "trade_outcome",
        "days_to_outcome",
        "outcome_return_pct",
    ]
    events = judged[events_cols].sort_values(["trade_outcome", "date"])
    events_path = OUTPUT_DIR / "breakout_vs_reversal_events.csv"
    events.to_csv(events_path, index=False, encoding="utf-8-sig")
    print(f"個別イベント一覧を保存しました: {events_path}")

    return 0


if __name__ == "__main__":
    exit(main())
