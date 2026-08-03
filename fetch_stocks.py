"""
J-Quants API から東証プライム全銘柄の日次OHLCVを取得する。

方針:
  1. 銘柄マスタ(/equities/master)を取得し、市場区分が「プライム」の
     銘柄コード一覧を作る。
  2. 株価四本値(/equities/bars/daily)を「日付のみ指定」で呼び出し、
     その日の全上場銘柄分をまとめて取得する(銘柄ごとに呼び出すと
     1600銘柄 x 1回/日 になり無料プランのレート制限に引っかかるため)。
  3. 取得結果のうち、1.で作ったプライム銘柄コードに含まれるものだけ
     DBに保存する。

プライム市場の MarketCode は '0111'。
"""

from __future__ import annotations

from datetime import date as date_cls

from jquants_client import JQuantsClient

PRIME_MARKET_CODE = "0111"


def fetch_prime_master_rows(client: JQuantsClient) -> tuple[list[dict], set[str]]:
    """
    マスタを取得し、
      - DB保存用の行リスト(プライム市場のみ)
      - プライム銘柄コードの集合
    を返す。
    """
    raw = client.get_listed_master()
    today_str = date_cls.today().isoformat()

    master_rows = []
    prime_codes: set[str] = set()
    for item in raw:
        if item.get("Mkt") != PRIME_MARKET_CODE:
            continue
        code = item.get("Code")
        if not code:
            continue
        prime_codes.add(code)
        master_rows.append(
            {
                "code": code,
                "company_name": item.get("CoName"),
                "market_code": item.get("Mkt"),
                "market_name": item.get("MktNm"),
                "sector33_name": item.get("S33Nm"),
                "updated_at": today_str,
            }
        )
    return master_rows, prime_codes


def fetch_prime_daily_rows(client: JQuantsClient, target_date: str, prime_codes: set[str]) -> list[dict]:
    """
    target_date (形式 'YYYYMMDD' または 'YYYY-MM-DD') の全銘柄四本値を取得し、
    プライム市場銘柄のみに絞ってDB保存用の行リストにして返す。
    """
    raw = client.get_daily_bars(target_date)

    rows = []
    for item in raw:
        code = item.get("Code")
        if code not in prime_codes:
            continue
        d = item.get("Date", "")
        # J-Quants側の日付フォーマット差異を吸収 (YYYYMMDD -> YYYY-MM-DD)
        if len(d) == 8 and "-" not in d:
            d = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

        rows.append(
            {
                "code": code,
                "date": d,
                "open": item.get("O"),
                "high": item.get("H"),
                "low": item.get("L"),
                "close": item.get("C"),
                "volume": item.get("Vo"),
                "turnover": item.get("Va"),
                "adj_factor": item.get("AdjFactor"),
            }
        )
    return rows
