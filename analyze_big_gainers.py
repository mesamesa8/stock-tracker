"""
「一定期間で大きく上昇した銘柄」に共通する、上昇が始まる”直前”の特徴を探す
分析スクリプト。

デフォルトでは「20営業日(約1ヶ月)で+50%以上上昇」を"サージ(急騰)"と
定義し、そのサージが始まった日(t日目)について、t日目**以前**の情報だけを
使って以下の特徴量を計算する(未来の情報を使わない = 実際のトレードで
使える先行指標かどうかを見るため)。

  - trailing_return_20d_pct: サージ開始前20日間の値動き(既に上昇基調
    だったのか、それとも横ばい/下落からの急反発だったのか)
  - volume_ratio_vs_20d_avg: 当日出来高が直近20日平均の何倍か
    (出来高急増を伴っていたか)
  - pct_below_52w_high: 52週高値からどれだけ離れた位置にいたか
    (高値圏からの続伸なのか、安値圏からの反発なのか)
  - is_52w_high_break: サージ開始日そのものが52週高値更新日だったか

これらを「サージが起きたイベント群」と「それ以外の全期間(ベースライン)」
とで比較し、統計的に差が出ているかを確認する。

使い方:
  python analyze_big_gainers.py                       # 20営業日+50%以上(デフォルト)
  python analyze_big_gainers.py --horizon 60 --threshold 30   # 期間・閾値を変更
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from analyze_new_highs import (
    DB_PATH,
    load_data,
    detect_new_highs,
    add_forward_returns,
    add_anomaly_flags,
)
from technical_features import add_precursor_features, FEATURE_COLS

OUTPUT_DIR = Path(__file__).parent / "analysis_output"

DEFAULT_SURGE_HORIZON_DAYS = 20
DEFAULT_SURGE_THRESHOLD_PCT = 50.0


def detect_surge_events(
    df: pd.DataFrame, horizon: int, threshold: float
) -> pd.DataFrame:
    """指定期間でthreshold%以上上昇した開始日を検出する(連続する開始日はまとめて1件)。"""
    col = f"fwd_return_{horizon}d"
    if col not in df.columns:
        raise ValueError(
            f"{col} が計算されていません。add_forward_returns() の horizons に {horizon} を含めてください。"
        )

    df["is_surge_start"] = df[col] >= threshold

    prev = df.groupby("code")["is_surge_start"].shift(1).fillna(False).astype(bool)
    df["is_surge_start_first"] = df["is_surge_start"] & ~prev

    return df


def compare_features(df: pd.DataFrame, event_mask: pd.Series) -> pd.DataFrame:
    """イベント群とベースライン(全期間)で特徴量の分布を比較する。"""
    rows = []
    for col in FEATURE_COLS:
        event_vals = df.loc[event_mask, col].dropna()
        baseline_vals = df[col].dropna()
        if len(event_vals) == 0:
            continue
        rows.append(
            {
                "feature": col,
                "n_events": int(len(event_vals)),
                "event_mean": round(event_vals.mean(), 2),
                "event_median": round(event_vals.median(), 2),
                "baseline_mean": round(baseline_vals.mean(), 2),
                "baseline_median": round(baseline_vals.median(), 2),
            }
        )

    # 52週高値更新かどうか(比率)も別途比較する
    if "is_52w_high_break" in df.columns:
        event_rate = df.loc[event_mask, "is_52w_high_break"].mean() * 100
        baseline_rate = df["is_52w_high_break"].mean() * 100
        rows.append(
            {
                "feature": "is_52w_high_break_rate_pct",
                "n_events": int(event_mask.sum()),
                "event_mean": round(event_rate, 1),
                "event_median": None,
                "baseline_mean": round(baseline_rate, 1),
                "baseline_median": None,
            }
        )

    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="一定期間で大きく上昇した銘柄の、上昇直前の特徴を分析する")
    parser.add_argument("--horizon", type=int, default=DEFAULT_SURGE_HORIZON_DAYS, help="何営業日での上昇率を見るか")
    parser.add_argument("--threshold", type=float, default=DEFAULT_SURGE_THRESHOLD_PCT, help="サージと判定する上昇率(%%)")
    parser.add_argument("--code", default=None, help="特定の銘柄コードだけに絞る場合")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    df = load_data(Path(args.db), code=args.code)
    if df.empty:
        print("[ERROR] データがありません。main.py や backfill.py を先に実行してください。")
        return 1

    print(f"読み込んだデータ: {len(df):,} 行 / {df['code'].nunique():,} 銘柄")
    print(f"検出条件: {args.horizon}営業日で+{args.threshold:.0f}%以上の上昇")

    horizons = sorted(set([args.horizon]))
    df = detect_new_highs(df)
    df = add_forward_returns(df, horizons=horizons)
    df = add_anomaly_flags(df, horizons=horizons)
    df = add_precursor_features(df)
    df = detect_surge_events(df, horizon=args.horizon, threshold=args.threshold)

    anomaly_col = f"anomaly_in_window_{args.horizon}d"
    clean_mask = df["is_surge_start_first"] & ~df[anomaly_col]

    n_raw = int(df["is_surge_start"].sum())
    n_first = int(df["is_surge_start_first"].sum())
    n_clean = int(clean_mask.sum())
    print(f"サージ検出: 延べ{n_raw:,}件 / 連続分をまとめると{n_first:,}件 / データ異常除外後{n_clean:,}件")

    if n_clean == 0:
        print("[WARN] 条件に合うイベントが見つかりませんでした。閾値や期間を緩めてみてください。")
        return 0

    comparison = compare_features(df, clean_mask)
    print("\n===== サージ開始「直前」の特徴(イベント群 vs 全期間平均) =====")
    print(comparison.to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    comparison_path = OUTPUT_DIR / "big_gainers_precursor_features.csv"
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    print(f"\n比較結果を保存しました: {comparison_path}")

    event_cols = ["code", "date", "close"] + FEATURE_COLS + [
        "is_52w_high_break",
        f"fwd_return_{args.horizon}d",
    ]
    events = df.loc[clean_mask, event_cols].sort_values(f"fwd_return_{args.horizon}d", ascending=False)
    events_path = OUTPUT_DIR / "big_gainers_events.csv"
    events.to_csv(events_path, index=False, encoding="utf-8-sig")
    print(f"個別イベント一覧を保存しました: {events_path}")

    return 0


if __name__ == "__main__":
    exit(main())
