"""
yfinance を使って日経平均・NASDAQ・SOX等の主要指数の日次OHLCVを取得する。

J-Quants は東証上場銘柄のみが対象のため、海外指数や日経平均そのものは
yfinance (無料) で別途取得する。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import yfinance as yf

# 監視したい指数。ここに追加すれば対象を増やせる。
INDEX_SYMBOLS = {
    "^N225": "日経平均株価",
    "NIY=F": "日経225先物(CME・円建、夜間取引含む)",
    "USDJPY=X": "ドル円",
    "^IXIC": "NASDAQ総合指数",
    "^SOX": "フィラデルフィア半導体指数(SOX)",
    "^GSPC": "S&P500",
    "^VIX": "VIX指数(恐怖指数)",
    "^DJI": "NYダウ",
    "^KS11": "韓国総合株価指数(KOSPI)",
    # TOPIXそのものはyfinanceでの取得が不安定なため、東証上場の
    # TOPIX連動ETF(1306)を代替として使う。値動きはTOPIXとほぼ完全に連動する。
    "1306.T": "TOPIX(連動ETF 1306で代替)",
}


def fetch_index_rows(days_back: int = 5) -> list[dict]:
    """
    直近 days_back 日分の指数データを取得し、db.upsert_indices() に渡せる
    dict のリストにして返す。土日祝日など未取引日は yfinance 側で自動的に
    スキップされる。少し多めに(デフォルト5日)取得し、DB側は
    ON CONFLICT UPDATE なので重複取得しても問題ない。
    """
    end = datetime.utcnow() + timedelta(days=1)
    start = end - timedelta(days=days_back + 5)  # 土日祝を考慮して余裕を持たせる

    rows: list[dict] = []
    for symbol, name in INDEX_SYMBOLS.items():
        try:
            hist = yf.Ticker(symbol).history(start=start.date(), end=end.date())
        except Exception as e:  # ネットワーク不調等でも他の指数取得は続行する
            print(f"[WARN] {symbol} の取得に失敗しました: {e}")
            continue

        for idx, row in hist.iterrows():
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "date": idx.strftime("%Y-%m-%d"),
                    "open": float(row["Open"]) if row["Open"] == row["Open"] else None,
                    "high": float(row["High"]) if row["High"] == row["High"] else None,
                    "low": float(row["Low"]) if row["Low"] == row["Low"] else None,
                    "close": float(row["Close"]) if row["Close"] == row["Close"] else None,
                    "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else None,
                }
            )
    return rows
