"""
TRADING_RULE.md で定めたエントリールールに合致する銘柄を、その日の終値
時点でスクリーニングし、「期待値(過去の同じ状況での平均リターン)」の
高い順にリストアップするスクリプト。

「期待値」の計算方法:
  1. まず、ルールに合致する過去の全トレードをシミュレーションする
     (backtest_rule.py と同じ仕組み)。
  2. それを「高値更新回数(0/1/2/3回)」×「直前の値上がり率(3段階)」の
     12パターンに分類し、それぞれのパターンの過去の平均リターン・勝率を
     計算する(=期待値テーブル)。
  3. 本日時点でルールに合致する銘柄を、それぞれが該当するパターンの
     期待値テーブルと照らし合わせて、期待値の高い順に並べる。

このスクリプトは「その日のデータが取得された後」に実行することを想定
している(main.py の後に実行する)。

使い方:
  python daily_screener.py
  python daily_screener.py --top 30   # 上位30件だけ表示
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
from backtest_rule import DEFAULT_MAX_BREAKOUT_COUNT, DEFAULT_MIN_MOMENTUM, DEFAULT_MAX_MOMENTUM
from technical_features import add_precursor_features

OUTPUT_DIR = Path(__file__).parent / "analysis_output"
WIN_LABELS = ["take_profit", "timeout_win"]

# 直前の値上がり率を3段階に分けるための境目(TRADING_RULE.mdのエントリー条件 1〜8% の範囲内をさらに細分化)
MOMENTUM_BUCKET_EDGES = [-999, 3.0, 6.0, 999]
MOMENTUM_BUCKET_LABELS = ["1-3%(弱め)", "3-6%(中間)", "6-8%(強め)"]

# --- 総合スコアの調整用パラメータ ---
# 期待値テーブルの各マスは、件数(n)が少ないと偶然のブレの影響を受けやすいため、
# 全体平均へ寄せる補正(縮小推定)をかける。SHRINK_K_BUCKET が大きいほど、
# 少ない件数のマスは全体平均寄りになる。
SHRINK_K_BUCKET = 50

# 業種の効果は「相場環境に依存する可能性がある」という不確実性があるため、
# より強めに全体平均へ寄せた上で、さらに重みを半分にして使う。
SHRINK_K_SECTOR = 200
SECTOR_WEIGHT = 0.5

# チャートの締まり具合(値幅の狭さ)による補正の最大振れ幅(%ポイント)
TIGHTNESS_SCORE_SCALE = 1.0


def load_company_names(db_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT code, company_name, sector33_name FROM stocks_master", conn)
    conn.close()
    return df


def run_historical_simulation(
    df: pd.DataFrame,
    rule_mask: pd.Series,
    hold_days: int,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> pd.DataFrame:
    """ルールに合致した過去の全トレードをシミュレーションし、判定済みの結果だけを返す。"""
    slim = df[["code", "date", "high", "low", "close"]].copy()
    sim = simulate_trade_outcomes(
        slim, entry_mask=rule_mask, hold_days=hold_days, take_profit_pct=take_profit_pct, stop_loss_pct=stop_loss_pct
    )

    judged_idx = sim.index[sim["trade_outcome"].notna() & rule_mask]
    judged = df.loc[
        judged_idx, ["code", "recent_breakout_count_120d", "trailing_return_pct", "volatility_contraction_pct"]
    ].copy()
    judged["trade_outcome"] = sim.loc[judged_idx, "trade_outcome"]
    judged["outcome_return_pct"] = sim.loc[judged_idx, "outcome_return_pct"]
    return judged


def build_expected_value_table(judged: pd.DataFrame, overall_mean_return: float) -> pd.DataFrame:
    """「高値更新回数 × 値上がり率バケット」ごとの期待値テーブルを作る(件数が少ないマスは全体平均へ縮小補正)。"""
    judged = judged.copy()
    judged["breakout_count_bucket"] = judged["recent_breakout_count_120d"].round().clip(upper=3).astype(int)
    judged["momentum_bucket"] = pd.cut(
        judged["trailing_return_pct"], bins=MOMENTUM_BUCKET_EDGES, labels=MOMENTUM_BUCKET_LABELS
    )

    table = (
        judged.groupby(["breakout_count_bucket", "momentum_bucket"], observed=True)
        .agg(
            n=("outcome_return_pct", "size"),
            expected_win_rate_pct=("trade_outcome", lambda s: round(s.isin(WIN_LABELS).mean() * 100, 1)),
            raw_expected_return_pct=("outcome_return_pct", "mean"),
        )
        .reset_index()
    )
    # 縮小推定: (n*マスの平均 + k*全体平均) / (n+k)
    table["expected_return_pct"] = (
        (table["n"] * table["raw_expected_return_pct"] + SHRINK_K_BUCKET * overall_mean_return)
        / (table["n"] + SHRINK_K_BUCKET)
    ).round(2)
    table["raw_expected_return_pct"] = table["raw_expected_return_pct"].round(2)
    return table


def build_sector_table(judged: pd.DataFrame, company_map: pd.DataFrame, overall_mean_return: float) -> pd.DataFrame:
    """業種ごとの過去の平均リターン(全体平均との差)を計算する(件数が少ない業種は0へ縮小補正)。"""
    judged = judged.merge(company_map[["code", "sector33_name"]], on="code", how="left")
    judged["sector33_name"] = judged["sector33_name"].fillna("(不明)")

    table = (
        judged.groupby("sector33_name")
        .agg(n_sector=("outcome_return_pct", "size"), sector_mean_return=("outcome_return_pct", "mean"))
        .reset_index()
    )
    table["raw_sector_excess_pct"] = (table["sector_mean_return"] - overall_mean_return).round(2)
    # 縮小推定: 件数が少ない業種は 0(補正なし) に近づける
    table["sector_adjustment_pct"] = (
        table["n_sector"] * table["raw_sector_excess_pct"] / (table["n_sector"] + SHRINK_K_SECTOR)
    ).round(3)
    return table[["sector33_name", "n_sector", "raw_sector_excess_pct", "sector_adjustment_pct"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="ルールに合致する銘柄を期待値順にスクリーニングする")
    parser.add_argument("--cohort-max-below", type=float, default=DEFAULT_COHORT_MAX_BELOW_PCT, help="52週高値から何%%以内を対象にするか")
    parser.add_argument("--max-breakout-count", type=int, default=DEFAULT_MAX_BREAKOUT_COUNT, help="直近120日の高値更新回数の上限")
    parser.add_argument("--min-momentum", type=float, default=DEFAULT_MIN_MOMENTUM, help="直前20日値上がり率の下限(%%)")
    parser.add_argument("--max-momentum", type=float, default=DEFAULT_MAX_MOMENTUM, help="直前20日値上がり率の上限(%%)")
    parser.add_argument("--take-profit", type=float, default=DEFAULT_TAKE_PROFIT_PCT, help="利確ライン(%%)")
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS_PCT, help="損切りライン(%%)")
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="最大保有営業日数")
    parser.add_argument("--top", type=int, default=50, help="表示する上位件数")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db))
    if df.empty:
        print("[ERROR] データがありません。main.py を先に実行してください。")
        return 1

    latest_date = df["date"].max()
    print(f"最新データの日付: {latest_date.date()}")

    df = detect_new_highs(df)
    df = add_precursor_features(df)

    full_rule_mask = (
        (df["prior_52w_count"] >= MIN_HISTORY_DAYS_52W)
        & (df["pct_below_52w_high"] <= args.cohort_max_below)
        & (df["recent_breakout_count_120d"] <= args.max_breakout_count)
        & (df["trailing_return_pct"] >= args.min_momentum)
        & (df["trailing_return_pct"] <= args.max_momentum)
    )

    # --- 期待値テーブルは「本日より前」の確定した過去データだけから作る ---
    historical_mask = full_rule_mask & (df["date"] < latest_date)
    print(f"期待値テーブル作成用の過去データ件数: {int(historical_mask.sum()):,} 件")
    print("過去データからシミュレーション中(数分かかることがあります)...")
    judged_hist = run_historical_simulation(df, historical_mask, args.hold_days, args.take_profit, args.stop_loss)
    overall_mean_return = judged_hist["outcome_return_pct"].mean()
    print(f"過去データ全体の平均リターン: {overall_mean_return:.2f}%")

    ev_table = build_expected_value_table(judged_hist, overall_mean_return)
    print("\n===== 期待値テーブル(過去データより、件数の少ないマスは全体平均へ補正済み) =====")
    print(ev_table.to_string(index=False))

    company_map = load_company_names(Path(args.db))
    sector_table = build_sector_table(judged_hist, company_map, overall_mean_return)
    print("\n===== 業種調整テーブル(件数の少ない業種は0へ補正済み、さらに重み0.5倍) =====")
    print(sector_table.sort_values("sector_adjustment_pct", ascending=False).to_string(index=False))

    # --- 本日時点の候補銘柄を抽出 ---
    today_mask = full_rule_mask & (df["date"] == latest_date)
    candidates = df.loc[today_mask].copy()
    print(f"\n本日({latest_date.date()})の候補銘柄数: {len(candidates):,} 件")

    if candidates.empty:
        print("本日はルールに合致する銘柄がありませんでした。")
        return 0

    candidates["breakout_count_bucket"] = candidates["recent_breakout_count_120d"].round().clip(upper=3).astype(int)
    candidates["momentum_bucket"] = pd.cut(
        candidates["trailing_return_pct"], bins=MOMENTUM_BUCKET_EDGES, labels=MOMENTUM_BUCKET_LABELS
    )

    candidates = candidates.merge(ev_table, on=["breakout_count_bucket", "momentum_bucket"], how="left")
    candidates = candidates.merge(company_map, on="code", how="left")
    candidates = candidates.merge(sector_table[["sector33_name", "sector_adjustment_pct"]], on="sector33_name", how="left")
    candidates["sector_adjustment_pct"] = candidates["sector_adjustment_pct"].fillna(0.0)

    # --- チャートの締まり具合による補正: 本日の候補内での順位(タイトなほど高スコア) ---
    candidates["tightness_percentile"] = candidates["volatility_contraction_pct"].rank(pct=True) * 100
    candidates["tightness_bonus_pct"] = (
        (50 - candidates["tightness_percentile"]) / 50 * TIGHTNESS_SCORE_SCALE
    ).round(3)

    candidates["sector_adjustment_pct"] = (candidates["sector_adjustment_pct"] * SECTOR_WEIGHT).round(3)
    candidates["composite_score"] = (
        candidates["expected_return_pct"] + candidates["sector_adjustment_pct"] + candidates["tightness_bonus_pct"]
    ).round(2)

    candidates["stop_loss_price"] = round(candidates["close"] * (1 - args.stop_loss / 100), 1)
    candidates["take_profit_price"] = round(candidates["close"] * (1 + args.take_profit / 100), 1)

    result_cols = [
        "code", "company_name", "sector33_name", "close",
        "pct_below_52w_high", "recent_breakout_count_120d", "trailing_return_pct",
        "expected_win_rate_pct", "expected_return_pct", "sector_adjustment_pct", "tightness_bonus_pct",
        "composite_score", "n",
        "stop_loss_price", "take_profit_price",
    ]
    result = candidates[result_cols].sort_values("composite_score", ascending=False)
    result = result.rename(columns={"n": "expected_value_sample_size"})

    print(f"\n===== 本日の候補銘柄(総合スコアの高い順、上位{args.top}件) =====")
    print(result.head(args.top).to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    result_path = OUTPUT_DIR / f"screening_{latest_date.date()}.csv"
    result.to_csv(result_path, index=False, encoding="utf-8-sig")
    # 常に最新のスクリーニング結果を同じファイル名でも保存しておく(GitHub Actionsで見やすくするため)
    latest_path = OUTPUT_DIR / "screening_latest.csv"
    result.to_csv(latest_path, index=False, encoding="utf-8-sig")
    print(f"\n結果を保存しました: {result_path}")
    print(f"最新版としても保存しました: {latest_path}")

    return 0


if __name__ == "__main__":
    exit(main())
