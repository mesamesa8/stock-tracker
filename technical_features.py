"""
複数の分析スクリプトで共通して使う、エントリー時点の特徴量(テクニカル
指標)を計算するモジュール。

すべて「その日の終値時点で分かる情報」だけを使って計算しており、未来の
情報は使っていない(実際のトレード判断に使える形にするため)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRAILING_WINDOW_DAYS = 20
MA_WINDOWS = [25, 75, 200]  # 日本株分析でよく使われる代表的な移動平均線期間
RSI_WINDOW = 14  # 標準的なRSI期間

FEATURE_COLS = [
    "trailing_return_pct",
    "volume_ratio_vs_avg",
    "pct_below_52w_high",
    "pct_vs_ma25",
    "pct_vs_ma75",
    "pct_vs_ma200",
    "ma25_above_ma75",
    "rsi14",
    "volatility_contraction_pct",
    "gap_pct",
    "recent_breakout_count_120d",
]


def add_chart_pattern_features(df: pd.DataFrame, trailing_window: int = TRAILING_WINDOW_DAYS) -> pd.DataFrame:
    """
    より「チャートの形」に近い特徴量を追加する。
      - volatility_contraction_pct: 直近trailing_window日間の1日の値幅(高値-安値)が
        終値に対して平均何%あったか。小さいほど「タイトなもみ合い」からのブレイク。
        当日を含まない(shift(1)で除外)。
      - gap_pct: 当日の始値が前日終値に対してどれだけ窓を開けたか(%)。
      - recent_breakout_count_120d: 直近120日以内に52週高値を更新した日数
        (連続して更新し続けた日もそれぞれ1日としてカウントする)。多いほど
        「何度も高値を更新している最中」、0や1なら「久しぶり/初めての高値更新」。
    事前に is_52w_high_break 列が計算済みであることを前提とする。
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    grp = df.groupby("code")

    daily_range_pct = (df["high"] - df["low"]) / df["close"] * 100
    df["_daily_range_pct"] = daily_range_pct
    vol_contraction = df.groupby("code")["_daily_range_pct"].transform(
        lambda s: s.shift(1).rolling(trailing_window, min_periods=max(5, trailing_window // 2)).mean()
    )
    df["volatility_contraction_pct"] = vol_contraction
    df = df.drop(columns=["_daily_range_pct"])

    prev_close = grp["close"].shift(1)
    df["gap_pct"] = (df["open"] - prev_close) / prev_close * 100

    if "is_52w_high_break" in df.columns:
        window_days = 120
        df["recent_breakout_count_120d"] = df.groupby("code")["is_52w_high_break"].transform(
            lambda s: s.shift(1).rolling(window_days, min_periods=1).sum()
        )

    return df


def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """移動平均線との位置関係・RSIを追加する。全て当日終値までの情報のみ使用。"""
    grp = df.groupby("code")["close"]

    for w in MA_WINDOWS:
        ma = grp.transform(lambda s, w=w: s.rolling(w, min_periods=max(5, w // 2)).mean())
        df[f"ma{w}"] = ma
        df[f"pct_vs_ma{w}"] = (df["close"] - ma) / ma * 100

    # ゴールデンクロス状態(短期線が長期線より上にあるか): 1=上, 0=下
    df["ma25_above_ma75"] = (df["ma25"] > df["ma75"]).astype(int)

    # RSI(14日) - 一般的な単純移動平均ベースの簡易版
    delta = grp.transform(lambda s: s.diff())
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.groupby(df["code"]).transform(
        lambda s: s.rolling(RSI_WINDOW, min_periods=RSI_WINDOW).mean()
    )
    avg_loss = loss.groupby(df["code"]).transform(
        lambda s: s.rolling(RSI_WINDOW, min_periods=RSI_WINDOW).mean()
    )
    avg_loss_safe = avg_loss.replace(0, np.nan)
    rs = avg_gain / avg_loss_safe
    rsi = 100 - 100 / (1 + rs)
    # avg_loss=0(下落が無い)の場合: 上昇もゼロなら横ばいとしてRSI=50、上昇があればRSI=100
    rsi = rsi.where(avg_loss != 0, np.where(avg_gain > 0, 100.0, 50.0))
    df["rsi14"] = rsi

    return df


def add_precursor_features(df: pd.DataFrame, trailing_window: int = TRAILING_WINDOW_DAYS) -> pd.DataFrame:
    """
    エントリー時点の特徴量を追加する。事前に detect_new_highs() で
    prior_52w_high 列が計算済みであることを前提とする。
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    grp = df.groupby("code")

    # --- 直前trailing_window日間の値動き(当日を含まない) ---
    close_prior = grp["close"].shift(1)
    close_n_before = grp["close"].shift(trailing_window + 1)
    df["trailing_return_pct"] = (close_prior - close_n_before) / close_n_before * 100

    # --- 出来高倍率(直近trailing_window日平均に対する当日出来高、当日は分母に含まない) ---
    vol_avg = grp["volume"].transform(
        lambda s: s.shift(1).rolling(trailing_window, min_periods=max(5, trailing_window // 2)).mean()
    )
    df["volume_ratio_vs_avg"] = df["volume"] / vol_avg

    # --- 52週高値からの乖離率 ---
    if "prior_52w_high" in df.columns:
        df["pct_below_52w_high"] = (df["prior_52w_high"] - df["close"]) / df["prior_52w_high"] * 100

    df = add_technical_features(df)
    df = add_chart_pattern_features(df)

    return df
